from datetime import timedelta
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (PRIORITY_ORDER, Agent, Availability, Priority, Role, SLAPolicy, StatusHistory,
                               Ticket, TicketStatus, User, agent_categories)
from app.services.notification_service import LOAD_STATUSES, notify
from app.utils import utcnow

ACTIVE_STATUSES = [TicketStatus.open, TicketStatus.assigned, TicketStatus.in_progress, TicketStatus.waiting]

TRANSITIONS = {
    TicketStatus.open: {TicketStatus.assigned},
    TicketStatus.assigned: {TicketStatus.in_progress},
    TicketStatus.in_progress: {TicketStatus.waiting, TicketStatus.resolved},
    TicketStatus.waiting: {TicketStatus.in_progress, TicketStatus.resolved},
    TicketStatus.resolved: {TicketStatus.closed},
}


async def get_policy(db: AsyncSession, priority: Priority) -> SLAPolicy:
    p = (await db.execute(select(SLAPolicy).where(SLAPolicy.priority == priority))).scalar_one_or_none()
    if not p:
        raise HTTPException(500, f"SLA policy for {priority.value} is not configured")
    return p


def calc_due(ticket: Ticket, policy: SLAPolicy) -> None:
    """Due times are measured from created_at; paused (waiting) time extends the resolution due."""
    ticket.first_response_due = ticket.created_at + timedelta(minutes=policy.first_response_minutes)
    ticket.resolution_due = ticket.created_at + timedelta(minutes=policy.resolution_minutes,
                                                          seconds=ticket.paused_seconds or 0)


async def generate_ticket_number(db: AsyncSession) -> str:
    prefix = f"TKT-{utcnow().strftime('%Y%m%d')}-"
    last = (await db.execute(select(func.max(Ticket.ticket_number))
                             .where(Ticket.ticket_number.like(prefix + "%")))).scalar()
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def add_history(db: AsyncSession, ticket: Ticket, old: Optional[TicketStatus], new: TicketStatus,
                actor_id: Optional[int], note: Optional[str] = None) -> None:
    db.add(StatusHistory(ticket_id=ticket.id, old_status=old, new_status=new, changed_by=actor_id, note=note))


def apply_status(db: AsyncSession, ticket: Ticket, new: TicketStatus, actor_id: Optional[int],
                 note: Optional[str] = None) -> None:
    """Low-level status change (no validation): handles SLA pause + timestamps + history."""
    now, old = utcnow(), ticket.status
    if old == TicketStatus.waiting and ticket.waiting_since:
        delta = int((now - ticket.waiting_since).total_seconds())
        ticket.paused_seconds = (ticket.paused_seconds or 0) + delta
        if ticket.resolution_due:
            ticket.resolution_due += timedelta(seconds=delta)
        ticket.waiting_since = None
    if new == TicketStatus.waiting:
        ticket.waiting_since = now
    if new == TicketStatus.resolved:
        ticket.resolved_at = now
    if new == TicketStatus.closed:
        ticket.closed_at = now
    ticket.status = new
    add_history(db, ticket, old, new, actor_id, note)


async def change_status(db: AsyncSession, ticket: Ticket, new: TicketStatus, actor: User,
                        note: Optional[str] = None) -> None:
    if ticket.status == TicketStatus.closed:
        raise HTTPException(400, "Closed tickets cannot be updated")
    if new not in TRANSITIONS.get(ticket.status, set()):
        raise HTTPException(400, f"Invalid status change: {ticket.status.value} -> {new.value}")
    if new == TicketStatus.assigned and ticket.assigned_agent_id is None:
        raise HTTPException(400, "Ticket has no assigned agent")
    apply_status(db, ticket, new, actor.id, note)
    await db.flush()
    await notify(db, ticket.customer_id, "Ticket status updated",
                 f"{ticket.ticket_number} is now '{new.value}'", ticket.id, "status_change")


async def agent_open_count(db: AsyncSession, agent_id: int) -> int:
    return (await db.execute(select(func.count(Ticket.id)).where(
        Ticket.assigned_agent_id == agent_id, Ticket.status.in_(LOAD_STATUSES)))).scalar() or 0


async def find_agent_for(db: AsyncSession, category_id: int) -> Optional[Agent]:
    """Online + active agent handling the category, with spare capacity, fewest open tickets first."""
    rows = (await db.execute(
        select(Ticket.assigned_agent_id, func.count(Ticket.id))
        .where(Ticket.status.in_(LOAD_STATUSES), Ticket.assigned_agent_id.isnot(None))
        .group_by(Ticket.assigned_agent_id))).all()
    counts = {r[0]: r[1] for r in rows}
    q = (select(Agent).join(User, User.id == Agent.user_id)
         .join(agent_categories, agent_categories.c.agent_id == Agent.id)
         .where(agent_categories.c.category_id == category_id, Agent.is_active == True,  # noqa: E712
                User.is_active == True, Agent.availability == Availability.online))  # noqa: E712
    agents = (await db.execute(q)).scalars().unique().all()
    eligible = [a for a in agents if counts.get(a.id, 0) < a.max_open_tickets]
    if not eligible:
        return None
    return min(eligible, key=lambda a: (counts.get(a.id, 0), a.id))


async def assign_ticket(db: AsyncSession, ticket: Ticket, agent: Agent, actor_id: Optional[int] = None,
                        note: Optional[str] = None) -> None:
    ticket.assigned_agent_id = agent.id
    apply_status(db, ticket, TicketStatus.assigned, actor_id, note or f"Auto-assigned to agent #{agent.id}")
    await db.flush()
    await notify(db, agent.user_id, "New ticket assigned",
                 f"{ticket.ticket_number}: {ticket.subject}", ticket.id, "assignment")


async def assign_pending(db: AsyncSession) -> List[int]:
    """Try to auto-assign every unassigned Open ticket. Returns ids of agents that received tickets."""
    pending = (await db.execute(select(Ticket).where(
        Ticket.status == TicketStatus.open, Ticket.assigned_agent_id.is_(None))
        .order_by(Ticket.created_at))).scalars().all()
    touched: List[int] = []
    for t in pending:
        agent = await find_agent_for(db, t.category_id)
        if agent:
            await assign_ticket(db, t, agent)
            touched.append(agent.id)
    return touched


def next_priority(p: Priority) -> Priority:
    i = PRIORITY_ORDER.index(p)
    return PRIORITY_ORDER[min(i + 1, len(PRIORITY_ORDER) - 1)]
