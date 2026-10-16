from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_agent, require_roles
from app.auth.security import hash_password
from app.database import get_db
from app.models.models import Agent, Availability, Category, Role, Ticket, TicketStatus, User
from app.schemas.schemas import AgentCreate, AgentOut, AgentUpdate, AvailabilityIn, TicketOut
from app.services.notification_service import broadcast_counts
from app.services.ticket_service import assign_pending

router = APIRouter(prefix="/agents", tags=["Agents"])
admin_only = require_roles(Role.admin)


async def _categories(db: AsyncSession, ids: List[int]) -> List[Category]:
    ids = list(set(ids))
    cats = (await db.execute(select(Category).where(Category.id.in_(ids)))).scalars().all() if ids else []
    if len(cats) != len(ids):
        raise HTTPException(404, "One or more categories not found")
    return list(cats)


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, _: User = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(User.id).where(User.email == body.email.lower()))).first():
        raise HTTPException(409, "Email already registered")
    cats = await _categories(db, body.category_ids)
    user = User(name=body.name, email=body.email.lower(), hashed_password=hash_password(body.password),
                role=Role.agent)
    agent = Agent(user=user, categories=cats, max_open_tickets=body.max_open_tickets,
                  availability=Availability.offline)
    db.add_all([user, agent])
    await db.commit()
    return agent


@router.get("", response_model=List[AgentOut])
async def list_agents(availability: Optional[Availability] = None, category_id: Optional[int] = None,
                      is_active: Optional[bool] = None, skip: int = Query(0, ge=0),
                      limit: int = Query(10, ge=1, le=100),
                      _: User = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    q = select(Agent)
    if availability:
        q = q.where(Agent.availability == availability)
    if is_active is not None:
        q = q.where(Agent.is_active == is_active)
    if category_id:
        q = q.where(Agent.categories.any(Category.id == category_id))
    rows = await db.execute(q.order_by(Agent.id).offset(skip).limit(limit))
    return rows.scalars().unique().all()


# NOTE: /me routes are declared before /{agent_id} so "me" is not parsed as an id
@router.put("/me/availability", response_model=AgentOut)
async def set_availability(body: AvailabilityIn, agent: Agent = Depends(get_current_agent),
                           db: AsyncSession = Depends(get_db)):
    agent.availability = body.availability
    await db.flush()
    touched = []
    if body.availability == Availability.online:
        touched = await assign_pending(db)  # pick up waiting tickets for this agent's categories
    await db.commit()
    await broadcast_counts(db, [agent.id] + touched)
    return agent


@router.get("/me/tickets", response_model=List[TicketOut])
async def my_tickets(status: Optional[TicketStatus] = None, skip: int = Query(0, ge=0),
                     limit: int = Query(10, ge=1, le=100),
                     agent: Agent = Depends(get_current_agent), db: AsyncSession = Depends(get_db)):
    q = select(Ticket).where(Ticket.assigned_agent_id == agent.id)
    if status:
        q = q.where(Ticket.status == status)
    return (await db.execute(q.order_by(Ticket.created_at.desc()).offset(skip).limit(limit))).scalars().all()


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: int, body: AgentUpdate, _: User = Depends(admin_only),
                       db: AsyncSession = Depends(get_db)):
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")
    if body.name is not None:
        agent.user.name = body.name
    if body.email is not None and body.email.lower() != agent.user.email:
        if (await db.execute(select(User.id).where(User.email == body.email.lower()))).first():
            raise HTTPException(409, "Email already registered")
        agent.user.email = body.email.lower()
    if body.category_ids is not None:
        agent.categories = await _categories(db, body.category_ids)
    if body.max_open_tickets is not None:
        agent.max_open_tickets = body.max_open_tickets
    if body.is_active is not None:
        agent.is_active = body.is_active
        agent.user.is_active = body.is_active
    await db.flush()
    touched = await assign_pending(db)  # new categories / capacity may unblock waiting tickets
    await db.commit()
    await broadcast_counts(db, [agent.id] + touched)
    return agent
