from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user, require_roles
from app.database import get_db
from app.models.models import Feedback, Role, TicketStatus, User
from app.schemas.schemas import FeedbackIn, FeedbackOut
from app.services.access import load_ticket_for_user

router = APIRouter(prefix="/tickets/{ticket_id}/feedback", tags=["Feedback"])


@router.post("", response_model=FeedbackOut, status_code=201)
async def give_feedback(ticket_id: int, body: FeedbackIn, user: User = Depends(require_roles(Role.customer)),
                        db: AsyncSession = Depends(get_db)):
    ticket = await load_ticket_for_user(db, ticket_id, user)  # 403 if not the ticket's customer
    if ticket.status not in (TicketStatus.resolved, TicketStatus.closed):
        raise HTTPException(400, "Feedback is allowed only for Resolved or Closed tickets")
    if (await db.execute(select(Feedback.id).where(Feedback.ticket_id == ticket_id))).first():
        raise HTTPException(409, "Feedback already submitted for this ticket")
    fb = Feedback(ticket_id=ticket_id, customer_id=user.id, rating=body.rating, comment=body.comment)
    db.add(fb)
    await db.commit()
    return fb


@router.get("", response_model=FeedbackOut)
async def get_feedback(ticket_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await load_ticket_for_user(db, ticket_id, user)
    fb = (await db.execute(select(Feedback).where(Feedback.ticket_id == ticket_id))).scalar_one_or_none()
    if not fb:
        raise HTTPException(404, "No feedback for this ticket")
    return fb
