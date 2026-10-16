from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models.models import Category, Priority, Role, SLAPolicy, Ticket, User
from app.schemas.schemas import CategoryIn, CategoryOut, CategoryUpdate, SLAIn, SLAOut
from app.services.ticket_service import ACTIVE_STATUSES

router = APIRouter(tags=["Categories & SLA"])
admin_only = require_roles(Role.admin)


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryIn, _: User = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(Category.id).where(Category.category_name == body.category_name))).first():
        raise HTTPException(409, "Category name already exists")
    cat = Category(**body.model_dump())
    db.add(cat)
    await db.commit()
    return cat


@router.get("/categories", response_model=List[CategoryOut])
async def list_categories(_: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Category).order_by(Category.id))).scalars().all()


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def update_category(category_id: int, body: CategoryUpdate, _: User = Depends(admin_only),
                          db: AsyncSession = Depends(get_db)):
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    data = body.model_dump(exclude_unset=True)
    if "category_name" in data and data["category_name"] != cat.category_name:
        if (await db.execute(select(Category.id).where(Category.category_name == data["category_name"]))).first():
            raise HTTPException(409, "Category name already exists")
    for k, v in data.items():
        setattr(cat, k, v)
    await db.commit()
    return cat


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, _: User = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    open_count = (await db.execute(select(func.count(Ticket.id)).where(
        Ticket.category_id == category_id, Ticket.status.in_(ACTIVE_STATUSES)))).scalar() or 0
    if open_count:
        raise HTTPException(409, f"Category has {open_count} open ticket(s) and cannot be deleted")
    total = (await db.execute(select(func.count(Ticket.id)).where(Ticket.category_id == category_id))).scalar() or 0
    if total:  # historical tickets reference it -> keep the row, just deactivate
        cat.is_active = False
        await db.commit()
        return {"detail": "Category has ticket history, so it was deactivated instead of deleted"}
    await db.delete(cat)
    await db.commit()
    return {"detail": "Category deleted"}


@router.get("/sla-policies", response_model=List[SLAOut])
async def list_sla(_: User = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(SLAPolicy).order_by(SLAPolicy.id))).scalars().all()


@router.put("/sla-policies/{priority}", response_model=SLAOut)
async def update_sla(priority: Priority, body: SLAIn, _: User = Depends(admin_only),
                     db: AsyncSession = Depends(get_db)):
    pol = (await db.execute(select(SLAPolicy).where(SLAPolicy.priority == priority))).scalar_one_or_none()
    if not pol:
        raise HTTPException(404, "SLA policy not found")
    pol.first_response_minutes = body.first_response_minutes
    pol.resolution_minutes = body.resolution_minutes
    await db.commit()
    return pol
