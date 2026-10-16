from enum import Enum

from sqlalchemy import (Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Table, Text)
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils import utcnow


class Role(str, Enum):
    admin = "Admin"
    agent = "Agent"
    customer = "Customer"


class Availability(str, Enum):
    online = "Online"
    offline = "Offline"


class Priority(str, Enum):
    low = "Low"
    medium = "Medium"
    high = "High"
    urgent = "Urgent"


PRIORITY_ORDER = [Priority.low, Priority.medium, Priority.high, Priority.urgent]


class TicketStatus(str, Enum):
    open = "Open"
    assigned = "Assigned"
    in_progress = "In Progress"
    waiting = "Waiting on Customer"
    resolved = "Resolved"
    closed = "Closed"


def enum_col(enum_cls, length=30):
    return SAEnum(enum_cls, native_enum=False, length=length, values_callable=lambda e: [i.value for i in e])


class TimestampMixin:
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


agent_categories = Table(
    "agent_categories", Base.metadata,
    Column("agent_id", Integer, ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", Integer, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime, default=utcnow, nullable=False),
    Column("updated_at", DateTime, default=utcnow, onupdate=utcnow, nullable=False),
)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(enum_col(Role, 20), nullable=False, default=Role.customer)
    is_active = Column(Boolean, nullable=False, default=True)

    tickets = relationship("Ticket", back_populates="customer", foreign_keys="Ticket.customer_id")
    agent_profile = relationship("Agent", back_populates="user", uselist=False)


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    availability = Column(enum_col(Availability, 20), nullable=False, default=Availability.offline)
    max_open_tickets = Column(Integer, nullable=False, default=10)
    is_active = Column(Boolean, nullable=False, default=True)

    user = relationship("User", back_populates="agent_profile", lazy="joined")
    categories = relationship("Category", secondary=agent_categories, back_populates="agents", lazy="selectin")
    tickets = relationship("Ticket", back_populates="assigned_agent")

    @property
    def name(self):
        return self.user.name

    @property
    def email(self):
        return self.user.email


class Category(Base, TimestampMixin):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    category_name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    tickets = relationship("Ticket", back_populates="category")
    agents = relationship("Agent", secondary=agent_categories, back_populates="categories")


class SLAPolicy(Base, TimestampMixin):
    __tablename__ = "sla_policies"
    id = Column(Integer, primary_key=True)
    priority = Column(enum_col(Priority, 20), unique=True, nullable=False)
    first_response_minutes = Column(Integer, nullable=False)
    resolution_minutes = Column(Integer, nullable=False)


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    ticket_number = Column(String(30), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    subject = Column(String(150), nullable=False)
    description = Column(Text, nullable=False)
    priority = Column(enum_col(Priority, 20), nullable=False, default=Priority.medium)
    status = Column(enum_col(TicketStatus), nullable=False, default=TicketStatus.open, index=True)
    assigned_agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True, index=True)
    first_response_due = Column(DateTime, nullable=True)
    resolution_due = Column(DateTime, nullable=True)
    first_response_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    sla_breached = Column(Boolean, nullable=False, default=False)
    reopen_count = Column(Integer, nullable=False, default=0)
    # SLA bookkeeping
    waiting_since = Column(DateTime, nullable=True)
    paused_seconds = Column(Integer, nullable=False, default=0)
    fr_escalated = Column(Boolean, nullable=False, default=False)
    res_escalated = Column(Boolean, nullable=False, default=False)

    customer = relationship("User", back_populates="tickets", foreign_keys=[customer_id])
    category = relationship("Category", back_populates="tickets")
    assigned_agent = relationship("Agent", back_populates="tickets")
    messages = relationship("Message", back_populates="ticket", cascade="all, delete-orphan")
    history = relationship("StatusHistory", back_populates="ticket", cascade="all, delete-orphan")
    feedback = relationship("Feedback", back_populates="ticket", uselist=False, cascade="all, delete-orphan")


class Message(Base, TimestampMixin):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    sender_role = Column(enum_col(Role, 20), nullable=False)
    message = Column(Text, nullable=False)
    is_internal_note = Column(Boolean, nullable=False, default=False)
    is_read = Column(Boolean, nullable=False, default=False)

    ticket = relationship("Ticket", back_populates="messages")


class StatusHistory(Base, TimestampMixin):
    __tablename__ = "ticket_status_history"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    old_status = Column(enum_col(TicketStatus), nullable=True)
    new_status = Column(enum_col(TicketStatus), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # NULL = system
    note = Column(String(255), nullable=True)

    ticket = relationship("Ticket", back_populates="history")


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="CASCADE"), unique=True, nullable=False)
    customer_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)

    ticket = relationship("Ticket", back_populates="feedback")


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    type = Column(String(50), nullable=False)
    title = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False)
