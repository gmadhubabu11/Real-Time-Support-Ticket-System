import asyncio
import logging
from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import SessionLocal
from app.models.models import Agent, Role, Ticket, TicketStatus, User
from app.services.email_service import send_email
from app.services.notification_service import broadcast_counts, notify, notify_admins
from app.services.ticket_service import add_history, apply_status, next_priority
from app.models.models import Priority
from app.utils import utcnow

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def sla_check_job():
    """Every 5 min: flag SLA breaches and escalate (once per breach)."""
    async with SessionLocal() as db:
        now = utcnow()
        tickets = (await db.execute(select(Ticket).where(
            Ticket.status.notin_([TicketStatus.resolved, TicketStatus.closed])))).scalars().all()
        agent_ids, emails = set(), []
        for t in tickets:
            kinds = []
            if (t.first_response_at is None and t.first_response_due and now > t.first_response_due
                    and not t.fr_escalated):
                kinds.append("first response")
                t.fr_escalated = True
            # Waiting on Customer pauses the resolution SLA
            if (t.status != TicketStatus.waiting and t.resolution_due and now > t.resolution_due
                    and not t.res_escalated):
                kinds.append("resolution")
                t.res_escalated = True
            if not kinds:
                continue
            t.sla_breached = True
            old = t.priority
            if old != Priority.urgent:
                t.priority = next_priority(old)
                note = f"SLA breach ({' & '.join(kinds)}): escalated {old.value} -> {t.priority.value}"
            else:
                note = f"SLA breach ({' & '.join(kinds)}): already Urgent, cannot escalate further"
            add_history(db, t, t.status, t.status, None, note)
            await notify_admins(db, "SLA breached" if old == Priority.urgent else "Ticket escalated",
                                f"{t.ticket_number}: {note}", t.id, "sla_breach")
            if t.assigned_agent_id:
                agent_ids.add(t.assigned_agent_id)
            emails.append((t.ticket_number, note))
        if emails:
            admin_emails = (await db.execute(select(User.email).where(
                User.role == Role.admin, User.is_active == True))).scalars().all()  # noqa: E712
        await db.commit()
        if emails:
            await broadcast_counts(db, agent_ids)
            for number, note in emails:
                for addr in admin_emails:
                    await asyncio.to_thread(send_email, addr, f"SLA breached: {number}", note)
        logger.info("SLA check done: %d ticket(s) breached", len(emails))


async def auto_close_job():
    """Daily: close Resolved tickets not reopened within 7 days."""
    async with SessionLocal() as db:
        cutoff = utcnow() - timedelta(days=7)
        tickets = (await db.execute(select(Ticket).where(
            Ticket.status == TicketStatus.resolved, Ticket.resolved_at <= cutoff))).scalars().all()
        for t in tickets:
            apply_status(db, t, TicketStatus.closed, None, "Auto-closed: not reopened within 7 days")
            await db.flush()
            await notify(db, t.customer_id, "Ticket closed", f"{t.ticket_number} was automatically closed",
                         t.id, "status_change")
        await db.commit()
        logger.info("Auto-close done: %d ticket(s) closed", len(tickets))


def start_scheduler():
    scheduler.add_job(sla_check_job, "interval", minutes=5, id="sla_check", max_instances=1, coalesce=True,
                      replace_existing=True)
    scheduler.add_job(auto_close_job, "cron", hour=0, minute=30, id="auto_close", max_instances=1,
                      replace_existing=True)
    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
