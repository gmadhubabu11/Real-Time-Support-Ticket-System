from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.database import get_db
from app.models.models import User
from app.schemas.schemas import MessageIn, MessageOut
from app.services.access import load_ticket_for_user
from app.services.message_service import fetch_messages, mark_read, post_message
from app.websockets.manager import chat_manager

router = APIRouter(prefix="/tickets/{ticket_id}/messages", tags=["Messages"])


@router.get("", response_model=List[MessageOut])
async def list_messages(ticket_id: int, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                        user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await load_ticket_for_user(db, ticket_id, user)
    msgs = await fetch_messages(db, ticket_id, user.role, skip, limit)
    if await mark_read(db, ticket_id, user):
        await db.commit()
        await chat_manager.broadcast(ticket_id, {"type": "read", "reader_id": user.id})
    return msgs


@router.post("", response_model=MessageOut, status_code=201)
async def send_message(ticket_id: int, body: MessageIn, user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    ticket = await load_ticket_for_user(db, ticket_id, user)
    return await post_message(db, ticket, user, body.message, body.is_internal_note)
