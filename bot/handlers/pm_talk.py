import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    Message, MessageId, ReplyParameters, User,
    InputMediaAnimation, InputMediaAudio, InputMediaDocument,
    InputMediaPhoto, InputMediaVideo,
)
from fluent.runtime import FluentLocalization
from structlog.types import FilteringBoundLogger

from bot.filters import ForwardableTypesFilter, ServiceMessagesFilter
from bot.handlers_feedback import MessageConnectionFeedback

router = Router()
logger: FilteringBoundLogger = structlog.get_logger()

# ────────────────────────────────────────────────────────────────
# Простое in-memory-хранилище соответствий «пользователь → topic_id»
# ────────────────────────────────────────────────────────────────
_topic_store: dict[int, int] = {}


async def save_topic_id(user_id: int, topic_id: int) -> None:
    """Сохраняем topic_id для пользователя."""
    _topic_store[user_id] = topic_id


async def get_topic_id(user_id: int) -> int | None:
    """Получаем сохранённый topic_id или None."""
    return _topic_store.get(user_id)


# ────────────────────────────────────────────────────────────────
# Вспомогательная утилита для форматирования «визитки» пользователя
# ────────────────────────────────────────────────────────────────
def get_user_data(
        l10n: FluentLocalization,
        user: User,
) -> dict:
    premium_key = "yes" if user.is_premium else "no"
    premium = l10n.format_value(premium_key, {"case": "lower"})

    language_key = "unknown" if user.language_code is None else user.language_code
    language = l10n.format_value(language_key, {"case": "lower"})

    username = f"@{user.username}" if user.username else l10n.format_value("no", {"case": "lower"})

    return {
        "full_name": user.full_name,
        "username": username,
        "premium": premium,
        "language": language,
    }


# ────────────────────────────────────────────────────────────────
# Обработчик любых «пересылаемых» сообщений
# ────────────────────────────────────────────────────────────────
@router.message(ForwardableTypesFilter())
async def any_forwardable_message(
        message: Message,
        bot: Bot,
        forum_chat_id: int,
        l10n: FluentLocalization,
        topic_id: int | None = None,
        new_topic_created: bool | None = None,
        error: str | None = None,
        reply_to_message_id: int | None = None,
        caption_length: int | None = None,
):
    # 1) Быстрые выходы
    if error is not None:
        await message.answer(error)
        return

    if caption_length is not None and caption_length > 1023:
        await message.reply(l10n.format_value("error-caption-too-long"))
        return

    # 2) Если dependency не передала topic_id — ищем в хранилище
    if topic_id is None:
        topic_id = await get_topic_id(message.from_user.id)

    # 3) При создании новой ветки (флаг new_topic_created) публикуем визитку
    if new_topic_created is True and topic_id is not None:
        user_info = get_user_data(l10n, message.from_user)
        user_info_text = l10n.format_value(
            "user-info",
            {
                "full_name": user_info["full_name"],
                "username": user_info["username"],
                "premium": user_info["premium"],
                "language": user_info["language"],
            })
        try:
            await bot.send_message(
                chat_id=forum_chat_id,
                message_thread_id=topic_id,
                text=user_info_text,
            )
        except TelegramAPIError:
            await logger.aexception("Failed to send intro info message from forum group to private chat")

    # 4) Формируем параметры ответа (если нужно)
    reply_parameters = None
    if reply_to_message_id is not None:
        reply_parameters = ReplyParameters(
            message_id=reply_to_message_id,
            allow_sending_without_reply=True,
        )

    # 5) Пытаемся переслать сообщение в нужную ветку
    try:
        result: MessageId = await message.copy_to(
            chat_id=forum_chat_id,
            message_thread_id=topic_id,
            reply_parameters=reply_parameters,
        )
        return MessageConnectionFeedback(
            from_chat_id=message.chat.id,
            from_message_id=message.message_id,
            to_chat_id=forum_chat_id,
            to_message_id=result.message_id,
        )

    # ───────────────────────────────────────────────────────
    # Ветка удалена / «архивирована»  → создаём новую
    # ───────────────────────────────────────────────────────
    except TelegramBadRequest as e:
        if "message thread not found" in str(e):
            # 5.1) Создаём новую ветку
            topic_title = f"{message.from_user.full_name} ({message.from_user.id})"
            new_topic = await bot.create_forum_topic(
                chat_id=forum_chat_id,
                name=topic_title,
            )
            topic_id = new_topic.message_thread_id
            await save_topic_id(message.from_user.id, topic_id)

            # 5.2) Публикуем визитку пользователя
            user_info = get_user_data(l10n, message.from_user)
            user_info_text = l10n.format_value(
                "user-info",
                {
                    "full_name": user_info["full_name"],
                    "username": user_info["username"],
                    "premium": user_info["premium"],
                    "language": user_info["language"],
                })
            await bot.send_message(
                chat_id=forum_chat_id,
                message_thread_id=topic_id,
                text=user_info_text,
            )

            # 5.3) Повторяем пересылку
            result: MessageId = await message.copy_to(
                chat_id=forum_chat_id,
                message_thread_id=topic_id,
                reply_parameters=reply_parameters,
            )
            return MessageConnectionFeedback(
                from_chat_id=message.chat.id,
                from_message_id=message.message_id,
                to_chat_id=forum_chat_id,
                to_message_id=result.message_id,
            )

        # Если причина BadRequest другая — пробрасываем выше
        raise

    # ───────────────────────────────────────────────────────
    # Любая иная ошибка Telegram API
    # ───────────────────────────────────────────────────────
    except TelegramAPIError:
        await logger.aexception("Failed to send message from private chat to forum group")
        await message.reply(l10n.format_value("error-from-pm-to-group"))


# ────────────────────────────────────────────────────────────────
# Сервисные сообщения мы игнорируем
# ────────────────────────────────────────────────────────────────
@router.message(ServiceMessagesFilter())
async def any_service_message(
        message: Message,
):
    return


# ────────────────────────────────────────────────────────────────
# Сообщения «непересылаемых» типов
# ────────────────────────────────────────────────────────────────
@router.message()
async def any_non_forwardable_message(
        message: Message,
        l10n: FluentLocalization,
):
    await message.reply(l10n.format_value("error-non-forwardable-type"))


# ────────────────────────────────────────────────────────────────
# Редактирование ТЕКСТОВЫХ сообщений
# ────────────────────────────────────────────────────────────────
@router.edited_message(F.text)
async def edited_text_message(
        message: Message,
        bot: Bot,
        error: str | None = None,
        edit_chat_id: int | None = None,
        edit_message_id: int | None = None,
):
    if error is not None:
        await message.answer(error)
        return
    try:
        await bot.edit_message_text(
            chat_id=edit_chat_id,
            message_id=edit_message_id,
            text=message.text,
            entities=message.entities,
        )
    except TelegramAPIError:
        await logger.aexception("Failed to edit text message on group side")


# ────────────────────────────────────────────────────────────────
# Редактирование медиа-сообщений
# ────────────────────────────────────────────────────────────────
@router.edited_message()  # Все прочие типы редактируемых медиа
async def edited_media_message(
        message: Message,
        bot: Bot,
        error: str | None = None,
        edit_chat_id: int | None = None,
        edit_message_id: int | None = None,
):
    if error is not None:
        await message.answer(error)
        return

    if message.animation:
        new_media = InputMediaAnimation(media=message.animation.file_id)
    elif message.audio:
        new_media = InputMediaAudio(media=message.audio.file_id)
    elif message.document:
        new_media = InputMediaDocument(media=message.document.file_id)
    elif message.photo:
        new_media = InputMediaPhoto(media=message.photo[-1].file_id)
    elif message.video:
        new_media = InputMediaVideo(media=message.video.file_id)
    else:
        return

    if message.caption:
        new_media.caption = message.caption
        new_media.caption_entities = message.caption_entities

    try:
        await bot.edit_message_media(
            chat_id=edit_chat_id,
            message_id=edit_message_id,
            media=new_media,
        )
    except TelegramAPIError:
        await logger.aexception("Failed to edit media message on group side")
