from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_roles
from app.database import get_db
from app.models.models import (Agent, Availability, Category, Feedback, Priority, Role, Ticket, TicketStatus, User)

router = APIRouter(prefix="/reports", tags=["Reports"], dependencies=[Depends(require_roles(Role.admin))])


def _range(q, from_date: Optional[date], to_date: Optional[date]):
    if from_date:
        q = q.where(Ticket.created_at >= datetime.combine(from_date, time.min))
    if to_date:
        q = q.where(Ticket.created_at < datetime.combine(to_date + timedelta(days=1), time.min))
    return q


def _avg_seconds(pairs):
    vals = [(b - a).total_seconds() for a, b in pairs if a and b]
    return round(sum(vals) / len(vals), 2) if vals else None


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Ticket.id)))).scalar() or 0
    rows = (await db.execute(select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status))).all()
    by_status = {s.value: 0 for s in TicketStatus}
    by_status.update({r[0].value: r[1] for r in rows})
    unassigned = (await db.execute(select(func.count(Ticket.id)).where(
        Ticket.assigned_agent_id.is_(None), Ticket.status == TicketStatus.open))).scalar() or 0
    breached = (await db.execute(select(func.count(Ticket.id)).where(Ticket.sla_breached == True))).scalar() or 0  # noqa: E712
    online = (await db.execute(select(func.count(Agent.id)).join(User, User.id == Agent.user_id).where(
        Agent.availability == Availability.online, Agent.is_active == True, User.is_active == True))).scalar() or 0  # noqa: E712
    return {"total_tickets": total, "tickets_by_status": by_status, "unassigned_tickets": unassigned,
            "sla_breached_tickets": breached, "agents_online": online}


@router.get("/agent-performance")
async def agent_performance(from_date: Optional[date] = None, to_date: Optional[date] = None,
                            db: AsyncSession = Depends(get_db)):
    agents = (await db.execute(select(Agent).order_by(Agent.id))).scalars().unique().all()
    tickets = (await db.execute(_range(select(Ticket).where(Ticket.assigned_agent_id.isnot(None)),
                                       from_date, to_date))).scalars().all()
    ratings = defaultdict(list)
    for tid, rating in (await db.execute(select(Feedback.ticket_id, Feedback.rating))).all():
        ratings[tid].append(rating)
    by_agent = defaultdict(list)
    for t in tickets:
        by_agent[t.assigned_agent_id].append(t)
    out = []
    for a in agents:
        ts = by_agent.get(a.id, [])
        resolved = [t for t in ts if t.resolved_at or t.closed_at]
        rs = [r for t in ts for r in ratings.get(t.id, [])]
        out.append({
            "agent_id": a.id, "agent_name": a.name, "total_tickets": len(ts),
            "tickets_resolved": len([t for t in ts if t.status in (TicketStatus.resolved, TicketStatus.closed)]),
            "avg_first_response_seconds": _avg_seconds((t.created_at, t.first_response_at) for t in ts),
            "avg_resolution_seconds": _avg_seconds((t.created_at, t.resolved_at) for t in resolved),
            "sla_breach_percentage": round(100 * len([t for t in ts if t.sla_breached]) / len(ts), 2) if ts else 0.0,
            "avg_rating": round(sum(rs) / len(rs), 2) if rs else None,
        })
    return out


@router.get("/category-summary")
async def category_summary(from_date: Optional[date] = None, to_date: Optional[date] = None,
                           db: AsyncSession = Depends(get_db)):
    cats = (await db.execute(select(Category).order_by(Category.id))).scalars().all()
    tickets = (await db.execute(_range(select(Ticket), from_date, to_date))).scalars().all()
    by_cat = defaultdict(list)
    for t in tickets:
        by_cat[t.category_id].append(t)
    return [{"category_id": c.id, "category_name": c.category_name, "ticket_count": len(by_cat.get(c.id, [])),
             "avg_resolution_seconds": _avg_seconds((t.created_at, t.resolved_at) for t in by_cat.get(c.id, []))}
            for c in cats]


@router.get("/sla-summary")
async def sla_summary(db: AsyncSession = Depends(get_db)):
    """met = Resolved/Closed without a breach; breached = any ticket flagged sla_breached."""
    tickets = (await db.execute(select(Ticket))).scalars().all()
    out = {p.value: {"sla_met": 0, "sla_breached": 0} for p in Priority}
    for t in tickets:
        if t.sla_breached:
            out[t.priority.value]["sla_breached"] += 1
        elif t.status in (TicketStatus.resolved, TicketStatus.closed):
            out[t.priority.value]["sla_met"] += 1
    return out


@router.get("/customer-satisfaction")
async def customer_satisfaction(db: AsyncSession = Depends(get_db)):
    avg = (await db.execute(select(func.avg(Feedback.rating)))).scalar()
    rows = (await db.execute(select(Feedback.rating, func.count(Feedback.id)).group_by(Feedback.rating))).all()
    dist = {str(i): 0 for i in range(1, 6)}
    dist.update({str(r[0]): r[1] for r in rows})
    return {"average_rating": round(float(avg), 2) if avg is not None else None,
            "total_ratings": sum(dist.values()), "rating_distribution": dist}
