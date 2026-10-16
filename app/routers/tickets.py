from datetime import date, datetime, time, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models.models import (Agent, Category, Priority, Role, StatusHistory, Ticket, TicketStatus, User)
from app.schemas.schemas import (AssignIn, HistoryOut, PriorityIn, StatusIn, TicketCreate, TicketOut)
from app.services.access import load_ticket_for_user
from app.services.email_service import send_email
from app.services.notification_service import broadcast_counts, notify
from app.services.ticket_service import (ACTIVE_STATUSES, add_history, agent_open_count, apply_status, assign_pending,
                                         assign_ticket, calc_due, change_status, find_agent_for,
                                         generate_ticket_number, get_policy)
from app.utils import utcnow

router = APIRouter(prefix="/tickets", tags=["Tickets"])


@router.post("", response_model=TicketOut, status_code=201)
async def create_ticket(body: TicketCreate, bg: BackgroundTasks, user: User = Depends(require_roles(Role.customer)),
                        db: AsyncSession = Depends(get_db)):
    cat = await db.get(Category, body.category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    if not cat.is_active:
        raise HTTPException(400, "Category is inactive")
    policy = await get_policy(db, body.priority)
    ticket = Ticket(ticket_number=await generate_ticket_number(db), customer_id=user.id,
                    category_id=cat.id, subject=body.subject, description=body.description,
                    priority=body.priority, status=TicketStatus.open, created_at=utcnow())
    calc_due(ticket, policy)
    db.add(ticket)
    await db.flush()
    add_history(db, ticket, None, TicketStatus.open, user.id, "Ticket created")
    agent = await find_agent_for(db, cat.id)
    if agent:
        await assign_ticket(db, ticket, agent)
    await db.commit()
    await broadcast_counts(db, [ticket.assigned_agent_id])
    bg.add_task(send_email, user.email, f"Ticket {ticket.ticket_number} created",
                f"Hi {user.name}, your ticket '{ticket.subject}' has been created.")
    return ticket


@router.get("", response_model=List[TicketOut])
async def list_tickets(
        ticket_number: Optional[str] = None, subject: Optional[str] = None,
        status: Optional[TicketStatus] = None, priority: Optional[Priority] = None,
        category_id: Optional[int] = None, assigned_agent_id: Optional[int] = None,
        sla_breached: Optional[bool] = None, from_date: Optional[date] = None, to_date: Optional[date] = None,
        sort_by: Literal["created_at", "priority", "resolution_due", "first_response_due", "status", "id"] = "created_at",
        order: Literal["asc", "desc"] = "desc",
        skip: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=100),
        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(Ticket)
    if user.role == Role.customer:
        q = q.where(Ticket.customer_id == user.id)
    elif user.role == Role.agent:
        agent = (await db.execute(select(Agent).where(Agent.user_id == user.id))).scalars().unique().first()
        if not agent:
            return []
        q = q.where(Ticket.assigned_agent_id == agent.id)
    if ticket_number:
        q = q.where(Ticket.ticket_number == ticket_number)
    if subject:
        q = q.where(Ticket.subject.like(f"%{subject}%"))
    if status:
        q = q.where(Ticket.status == status)
    if priority:
        q = q.where(Ticket.priority == priority)
    if category_id:
        q = q.where(Ticket.category_id == category_id)
    if assigned_agent_id:
        q = q.where(Ticket.assigned_agent_id == assigned_agent_id)
    if sla_breached is not None:
        q = q.where(Ticket.sla_breached == sla_breached)
    if from_date:
        q = q.where(Ticket.created_at >= datetime.combine(from_date, time.min))
    if to_date:
        q = q.where(Ticket.created_at < datetime.combine(to_date + timedelta(days=1), time.min))
    if sort_by == "priority":
        col = case({Priority.low: 1, Priority.medium: 2, Priority.high: 3, Priority.urgent: 4}, value=Ticket.priority)
    else:
        col = getattr(Ticket, sort_by)
    q = q.order_by(col.asc() if order == "asc" else col.desc(), Ticket.id.desc())
    return (await db.execute(q.offset(skip).limit(limit))).scalars().all()


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(ticket_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await load_ticket_for_user(db, ticket_id, user)


@router.put("/{ticket_id}/status", response_model=TicketOut)
async def update_status(ticket_id: int, body: StatusIn, bg: BackgroundTasks,
                        user: User = Depends(require_roles(Role.admin, Role.agent)),
                        db: AsyncSession = Depends(get_db)):
    ticket = await load_ticket_for_user(db, ticket_id, user)  # agent must be the assigned one
    await change_status(db, ticket, body.status, user, body.note)
    touched = []
    if body.status == TicketStatus.resolved:  # capacity freed -> pending tickets may be assigned
        touched = await assign_pending(db)
    await db.commit()
    await broadcast_counts(db, [ticket.assigned_agent_id] + touched)
    if body.status == TicketStatus.resolved:
        customer = await db.get(User, ticket.customer_id)
        bg.add_task(send_email, customer.email, f"Ticket {ticket.ticket_number} resolved",
                    f"Hi {customer.name}, your ticket '{ticket.subject}' was resolved. You can reopen it within 7 days.")
    return ticket


@router.put("/{ticket_id}/priority", response_model=TicketOut)
async def update_priority(ticket_id: int, body: PriorityIn,
                          user: User = Depends(require_roles(Role.admin, Role.agent)),
                          db: AsyncSession = Depends(get_db)):
    ticket = await load_ticket_for_user(db, ticket_id, user)
    if ticket.status == TicketStatus.closed:
        raise HTTPException(400, "Closed tickets cannot be updated")
    old = ticket.priority
    if old != body.priority:
        ticket.priority = body.priority
        calc_due(ticket, await get_policy(db, body.priority))
        add_history(db, ticket, ticket.status, ticket.status, user.id,
                    f"Priority changed {old.value} -> {body.priority.value}; due times recalculated")
        await db.commit()
    return ticket


@router.put("/{ticket_id}/reopen", response_model=TicketOut)
async def reopen_ticket(ticket_id: int, user: User = Depends(require_roles(Role.customer)),
                        db: AsyncSession = Depends(get_db)):
    ticket = await load_ticket_for_user(db, ticket_id, user)
    if ticket.status == TicketStatus.closed:
        raise HTTPException(400, "Closed tickets cannot be reopened")
    if ticket.status != TicketStatus.resolved:
        raise HTTPException(400, "Only Resolved tickets can be reopened")
    if not ticket.resolved_at or utcnow() - ticket.resolved_at > timedelta(days=7):
        raise HTTPException(400, "Reopen window (7 days after resolution) has expired")
    if ticket.reopen_count >= 2:
        raise HTTPException(400, "Ticket can be reopened at most 2 times")
    ticket.reopen_count += 1
    apply_status(db, ticket, TicketStatus.in_progress, user.id, f"Reopened by customer (#{ticket.reopen_count})")
    ticket.resolved_at = None
    policy = await get_policy(db, ticket.priority)  # resolution SLA restarts on reopen
    ticket.resolution_due = utcnow() + timedelta(minutes=policy.resolution_minutes)
    ticket.res_escalated = False
    await db.flush()
    if ticket.assigned_agent_id:
        agent = await db.get(Agent, ticket.assigned_agent_id)
        await notify(db, agent.user_id, "Ticket reopened", f"{ticket.ticket_number} was reopened by the customer",
                     ticket.id, "reopen")
    await db.commit()
    await broadcast_counts(db, [ticket.assigned_agent_id])
    return ticket


@router.get("/{ticket_id}/history", response_model=List[HistoryOut])
async def ticket_history(ticket_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await load_ticket_for_user(db, ticket_id, user)
    return (await db.execute(select(StatusHistory).where(StatusHistory.ticket_id == ticket_id)
                             .order_by(StatusHistory.id))).scalars().all()


@router.put("/{ticket_id}/assign", response_model=TicketOut)
async def reassign_ticket(ticket_id: int, body: AssignIn, user: User = Depends(require_roles(Role.admin)),
                          db: AsyncSession = Depends(get_db)):
    ticket = await db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket.status in (TicketStatus.resolved, TicketStatus.closed):
        raise HTTPException(400, f"Cannot assign a {ticket.status.value} ticket")
    agent = await db.get(Agent, body.agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if not agent.is_active or not agent.user.is_active:
        raise HTTPException(400, "Agent is inactive")
    if ticket.category_id not in [c.id for c in agent.categories]:
        raise HTTPException(400, "Agent does not handle this ticket's category")
    if ticket.assigned_agent_id == agent.id:
        raise HTTPException(400, "Ticket is already assigned to this agent")
    if await agent_open_count(db, agent.id) >= agent.max_open_tickets:
        raise HTTPException(409, "Agent has reached max_open_tickets")
    old_agent = await db.get(Agent, ticket.assigned_agent_id) if ticket.assigned_agent_id else None
    ticket.assigned_agent_id = agent.id
    note = f"Reassigned from agent #{old_agent.id} to agent #{agent.id}" if old_agent else f"Manually assigned to agent #{agent.id}"
    if ticket.status == TicketStatus.open:
        apply_status(db, ticket, TicketStatus.assigned, user.id, note)
    else:
        add_history(db, ticket, ticket.status, ticket.status, user.id, note)
    await db.flush()
    await notify(db, agent.user_id, "Ticket assigned to you", f"{ticket.ticket_number}: {ticket.subject}",
                 ticket.id, "assignment")
    if old_agent:
        await notify(db, old_agent.user_id, "Ticket reassigned",
                     f"{ticket.ticket_number} was reassigned to another agent", ticket.id, "reassignment")
    await db.commit()
    await broadcast_counts(db, [agent.id, old_agent.id if old_agent else None])
    return ticket
