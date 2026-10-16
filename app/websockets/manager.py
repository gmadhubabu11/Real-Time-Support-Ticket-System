from typing import Dict, List, Tuple

from fastapi import WebSocket

from app.models.models import Role


class ChatManager:
    """Per-ticket chat rooms."""

    def __init__(self):
        self.rooms: Dict[int, List[Tuple[WebSocket, int, Role]]] = {}

    def connect(self, ticket_id: int, ws: WebSocket, user_id: int, role: Role):
        self.rooms.setdefault(ticket_id, []).append((ws, user_id, role))

    def disconnect(self, ticket_id: int, ws: WebSocket):
        conns = self.rooms.get(ticket_id, [])
        self.rooms[ticket_id] = [c for c in conns if c[0] is not ws]
        if not self.rooms[ticket_id]:
            self.rooms.pop(ticket_id, None)

    def is_connected(self, ticket_id: int, user_id: int) -> bool:
        return any(c[1] == user_id for c in self.rooms.get(ticket_id, []))

    async def broadcast(self, ticket_id: int, payload: dict, internal: bool = False, exclude: WebSocket = None):
        for ws, _uid, role in list(self.rooms.get(ticket_id, [])):
            if ws is exclude:
                continue
            if internal and role == Role.customer:
                continue  # customers never receive internal notes
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(ticket_id, ws)


class NotificationManager:
    """Per-user live notification sockets (a user may have several tabs)."""

    def __init__(self):
        self.users: Dict[int, List[WebSocket]] = {}

    def connect(self, user_id: int, ws: WebSocket):
        self.users.setdefault(user_id, []).append(ws)

    def disconnect(self, user_id: int, ws: WebSocket):
        self.users[user_id] = [w for w in self.users.get(user_id, []) if w is not ws]
        if not self.users[user_id]:
            self.users.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict):
        for ws in list(self.users.get(user_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(user_id, ws)


chat_manager = ChatManager()
notif_manager = NotificationManager()
