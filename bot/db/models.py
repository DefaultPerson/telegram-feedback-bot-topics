from datetime import datetime
from uuid import uuid4

from sqlalchemy import UniqueConstraint, func, select, and_, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID, BIGINT, INTEGER
from sqlalchemy.orm import mapped_column, Mapped
from sqlalchemy import select, desc

from bot.db.base import Base


class MessageConnection(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            'from_chat_id', 'from_message_id', 'to_chat_id', 'to_message_id',
            name='unique_messages_ids_combinations'
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    from_chat_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    from_message_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    to_chat_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    to_message_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    @classmethod
    def find_pair_message(
            cls,
            chat_id: int,
            message_id: int,
            originated_from_user: bool,
    ):
        if originated_from_user:
            chat_search_condition = cls.from_chat_id == chat_id
            message_search_condition = cls.from_message_id == message_id
        else:
            chat_search_condition = cls.to_chat_id == chat_id
            message_search_condition = cls.to_message_id == message_id

        return (
            select(cls)
            .where(
                and_(
                    chat_search_condition,
                    message_search_condition,
                )
            )
        )

    def as_dict(self) -> dict:
        return {
            "from_chat_id": self.from_chat_id,
            "from_message_id": self.from_message_id,
            "to_chat_id": self.to_chat_id,
            "to_message_id": self.to_message_id,
        }


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint(
            'user_id', 'topic_id',
            name='unique_topics_pairs'
        ),
    )

    id: Mapped[UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(BIGINT, nullable=False)
    topic_id: Mapped[int] = mapped_column(INTEGER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    @classmethod
    def find_by_user_id(cls, user_id: int):
        return select(cls).where(Topic.user_id == user_id)


    @classmethod
    def find_by_topic_id(cls, topic_id: int):
        return (
            select(cls)
            .where(cls.topic_id == topic_id)
            .order_by(desc(cls.created_at))
            .limit(1)
        )

    def __repr__(self):
        return f"Topic #{self.topic_id} for user {self.user_id}"
