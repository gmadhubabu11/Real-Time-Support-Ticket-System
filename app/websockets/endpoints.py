import json

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from app.auth.security import decode_token
from app.database import SessionLocal
from app.models.models import Notification, Role, Ticket, User
from app.services.access import user_can_access
from app.services.message_service import fetch_messages, mark_read, post_message
from app.websockets.manager import chat_manager, notif_manager

router = APIRouter()


async def _auth(db, token):
    payload = decode_token(token)
    if not payload or not str(payload.get("sub", "")).isdigit():
        return None
    user = await db.get(User, int(payload["sub"]))
    return user if user and user.is_active else None


@router.websocket("/ws/tickets/{ticket_id}")
async def ticket_chat(ws: WebSocket, ticket_id: int, token: str = Query(None)):
    await ws.accept()
    async with SessionLocal() as db:
        user = await _auth(db, token)
        if not user:
            await ws.close(code=4401, reason="Invalid or expired token")
            return
        ticket = await db.get(Ticket, ticket_id)
        if not ticket:
            await ws.close(code=4404, reason="Ticket not found")
            return
        if not await user_can_access(db, user, ticket):
            await ws.close(code=4403, reason="Forbidden")
            return
        history = await fetch_messages(db, ticket_id, user.role, limit=50, latest=True)  # last 50
        marked = await mark_read(db, ticket_id, user)
        await db.commit()
    chat_manager.connect(ticket_id, ws, user.id, user.role)
    try:
        await ws.send_json({"type": "history", "messages": history})
        if marked:
            await chat_manager.broadcast(ticket_id, {"type": "read", "reader_id": user.id}, exclude=ws)
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
                assert isinstance(data, dict)
            except Exception:
                await ws.send_json({"type": "error", "detail": "Send JSON like {\"message\": \"Hello\"}"})
                continue
            if data.get("type") == "typing":  # transient - never saved
                await chat_manager.broadcast(ticket_id, {
                    "type": "typing", "user_id": user.id, "role": user.role.value,
                    "text": f"{user.role.value} is typing..."}, exclude=ws)
                continue
            text = str(data.get("message") or "").strip()
            if not text or len(text) > 2000:
                await ws.send_json({"type": "error", "detail": "Message must be 1-2000 characters"})
                continue
            async with SessionLocal() as db:
                ticket = await db.get(Ticket, ticket_id)
                if not ticket or not await user_can_access(db, user, ticket):
                    await ws.close(code=4403, reason="Forbidden")
                    return
                try:
                    await post_message(db, ticket, user, text, bool(data.get("is_internal_note", False)))
                except HTTPException as exc:
                    await ws.send_json({"type": "error", "detail": exc.detail})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        chat_manager.disconnect(ticket_id, ws)


@router.websocket("/ws/notifications")
async def notifications_ws(ws: WebSocket, token: str = Query(None)):
    await ws.accept()
    async with SessionLocal() as db:
        user = await _auth(db, token)
        if not user:
            await ws.close(code=4401, reason="Invalid or expired token")
            return
        unread = (await db.execute(select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.is_read == False))).scalar() or 0  # noqa: E712
    notif_manager.connect(user.id, ws)
    try:
        await ws.send_json({"type": "connected", "unread_count": unread})
        while True:
            await ws.receive_text()  # keep-alive; client pings are ignored
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        notif_manager.disconnect(user.id, ws)
