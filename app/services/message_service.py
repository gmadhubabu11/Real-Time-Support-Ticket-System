from typing import List

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Agent, Message, Role, Ticket, TicketStatus, User
from app.services.notification_service import notify
from app.services.ticket_service import apply_status
from app.utils import utcnow
from app.websockets.manager import chat_manager


def message_dict(m: Message, sender_name: str = None) -> dict:
    return {"id": m.id, "ticket_id": m.ticket_id, "sender_id": m.sender_id, "sender_name": sender_name,
            "sender_role": m.sender_role.value, "message": m.message, "is_internal_note": m.is_internal_note,
            "is_read": m.is_read, "created_at": m.created_at.isoformat()}


async def fetch_messages(db: AsyncSession, ticket_id: int, viewer_role: Role, skip: int = 0, limit: int = 50,
                         latest: bool = False) -> List[dict]:
    """latest=True returns the newest `limit` messages (still in chronological order)."""
    q = select(Message, User.name).join(User, User.id == Message.sender_id).where(Message.ticket_id == ticket_id)
    if viewer_role == Role.customer:
        q = q.where(Message.is_internal_note == False)  # noqa: E712
    if latest:
        rows = (await db.execute(q.order_by(Message.id.desc()).limit(limit))).all()[::-1]
    else:
        rows = (await db.execute(q.order_by(Message.id).offset(skip).limit(limit))).all()
    return [message_dict(m, name) for m, name in rows]


async def mark_read(db: AsyncSession, ticket_id: int, user: User) -> int:
    stmt = update(Message).where(Message.ticket_id == ticket_id, Message.sender_id != user.id,
                                 Message.is_read == False)  # noqa: E712
    if user.role == Role.customer:
        stmt = stmt.where(Message.is_internal_note == False)  # noqa: E712
    res = await db.execute(stmt.values(is_read=True))
    return res.rowcount or 0


async def post_message(db: AsyncSession, ticket: Ticket, user: User, text: str, internal: bool = False) -> dict:
    """Validate, save, notify and broadcast a chat message (used by REST and WebSocket)."""
    if user.role == Role.customer:
        if ticket.status == TicketStatus.closed:
            raise HTTPException(400, "Cannot send messages on a closed ticket")
        if internal:
            raise HTTPException(403, "Customers cannot send internal notes")
    msg = Message(ticket_id=ticket.id, sender_id=user.id, sender_role=user.role, message=text,
                  is_internal_note=internal, is_read=False)
    db.add(msg)
    await db.flush()
    staff = user.role in (Role.agent, Role.admin)
    if staff and not internal and ticket.first_response_at is None:
        ticket.first_response_at = utcnow()
    # customer replied while we were waiting on them -> resume work (and the SLA timer)
    if user.role == Role.customer and ticket.status == TicketStatus.waiting:
        apply_status(db, ticket, TicketStatus.in_progress, user.id, "Customer replied")
    if staff and not internal and not chat_manager.is_connected(ticket.id, ticket.customer_id):
        await notify(db, ticket.customer_id, "New reply on your ticket",
                     f"{ticket.ticket_number}: you have a new reply", ticket.id, "reply")
    if user.role == Role.customer and ticket.assigned_agent_id:
        agent = await db.get(Agent, ticket.assigned_agent_id)
        if agent and not chat_manager.is_connected(ticket.id, agent.user_id):
            await notify(db, agent.user_id, "Customer replied",
                         f"{ticket.ticket_number}: new message from customer", ticket.id, "reply")
    await db.commit()
    payload = message_dict(msg, user.name)
    await chat_manager.broadcast(ticket.id, {"type": "message", **payload}, internal=internal)
    return payload
