from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.message import Message, MessageDirection


class MessageRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        order_id: int,
        sender_id: int,
        text: str,
        direction: MessageDirection,
    ) -> Message:
        msg = Message(
            order_id=order_id,
            sender_id=sender_id,
            text=text,
            direction=direction,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def get_by_order(self, order_id: int) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.order_id == order_id)
            .options(selectinload(Message.sender))
            .order_by(Message.created_at)
        )
        return list(result.scalars().all())
