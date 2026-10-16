from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database import get_db
from app.models.models import Notification, User
from app.schemas.schemas import NotificationOut

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=List[NotificationOut])
async def list_notifications(unread_only: bool = False, skip: int = Query(0, ge=0),
                             limit: int = Query(20, ge=1, le=100),
                             user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.is_read == False)  # noqa: E712
    return (await db.execute(q.order_by(Notification.id.desc()).offset(skip).limit(limit))).scalars().all()


@router.put("/read-all")
async def read_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(update(Notification).where(Notification.user_id == user.id,
                                                      Notification.is_read == False).values(is_read=True))  # noqa: E712
    await db.commit()
    return {"marked_read": res.rowcount or 0}


@router.put("/{notification_id}/read", response_model=NotificationOut)
async def read_one(notification_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    n = await db.get(Notification, notification_id)
    if not n or n.user_id != user.id:
        raise HTTPException(404, "Notification not found")
    n.is_read = True
    await db.commit()
    return n
