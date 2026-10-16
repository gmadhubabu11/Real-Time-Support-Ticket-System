from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Agent, Notification, Role, Ticket, TicketStatus, User
from app.websockets.manager import notif_manager

LOAD_STATUSES = [TicketStatus.assigned, TicketStatus.in_progress, TicketStatus.waiting]


async def notify(db: AsyncSession, user_id: int, title: str, message: str,
                 ticket_id: Optional[int] = None, type: str = "info") -> Notification:
    """Persist a notification and push it live if the user is connected."""
    n = Notification(user_id=user_id, ticket_id=ticket_id, type=type, title=title, message=message)
    db.add(n)
    await db.flush()
    await notif_manager.send_to_user(user_id, {
        "type": "notification", "id": n.id, "ticket_id": ticket_id, "notification_type": type,
        "title": title, "message": message, "is_read": False,
    })
    return n


async def notify_admins(db: AsyncSession, title: str, message: str, ticket_id: Optional[int] = None,
                        type: str = "info"):
    admins = (await db.execute(select(User.id).where(User.role == Role.admin, User.is_active == True))).scalars().all()  # noqa: E712
    for uid in admins:
        await notify(db, uid, title, message, ticket_id, type)


async def broadcast_counts(db: AsyncSession, agent_ids: Iterable[Optional[int]] = ()):
    """Push live open-ticket counts to the given agents and to all admins."""
    rows = (await db.execute(
        select(Ticket.assigned_agent_id, func.count(Ticket.id))
        .where(Ticket.status.in_(LOAD_STATUSES), Ticket.assigned_agent_id.isnot(None))
        .group_by(Ticket.assigned_agent_id))).all()
    counts = {r[0]: r[1] for r in rows}
    unassigned = (await db.execute(
        select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.open,
                                            Ticket.assigned_agent_id.is_(None)))).scalar() or 0
    ids = {a for a in agent_ids if a}
    if ids:
        agents = (await db.execute(select(Agent).where(Agent.id.in_(ids)))).scalars().unique().all()
        for a in agents:
            await notif_manager.send_to_user(a.user_id, {"type": "open_ticket_count",
                                                         "open_tickets": counts.get(a.id, 0)})
    admins = (await db.execute(select(User.id).where(User.role == Role.admin))).scalars().all()
    payload = {"type": "dashboard_counts", "open_tickets_total": sum(counts.values()),
               "unassigned_tickets": unassigned, "open_tickets_by_agent": {str(k): v for k, v in counts.items()}}
    for uid in admins:
        await notif_manager.send_to_user(uid, payload)
