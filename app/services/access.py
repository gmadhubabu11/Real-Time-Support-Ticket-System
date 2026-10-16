from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Agent, Role, Ticket, User


async def user_can_access(db: AsyncSession, user: User, ticket: Ticket) -> bool:
    if user.role == Role.admin:
        return True
    if user.role == Role.customer:
        return ticket.customer_id == user.id
    if user.role == Role.agent:
        agent = (await db.execute(select(Agent).where(Agent.user_id == user.id))).scalars().unique().first()
        return bool(agent and ticket.assigned_agent_id == agent.id)
    return False


async def load_ticket_for_user(db: AsyncSession, ticket_id: int, user: User) -> Ticket:
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if not await user_can_access(db, user, ticket):
        raise HTTPException(403, "You do not have access to this ticket")
    return ticket
