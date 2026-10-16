from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.models import Availability, Priority, Role, TicketStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth / users ----------
class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)


class UserOut(ORM):
    id: int
    name: str
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ---------- categories / SLA ----------
class CategoryIn(BaseModel):
    category_name: str = Field(min_length=2, max_length=100)
    description: Optional[str] = None
    is_active: bool = True


class CategoryUpdate(BaseModel):
    category_name: Optional[str] = Field(None, min_length=2, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CategoryOut(ORM):
    id: int
    category_name: str
    description: Optional[str]
    is_active: bool


class SLAIn(BaseModel):
    first_response_minutes: int = Field(gt=0)
    resolution_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def _check(self):
        if self.resolution_minutes < self.first_response_minutes:
            raise ValueError("resolution_minutes must be >= first_response_minutes")
        return self


class SLAOut(ORM):
    priority: Priority
    first_response_minutes: int
    resolution_minutes: int


# ---------- agents ----------
class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=72)
    category_ids: List[int] = []
    max_open_tickets: int = Field(10, ge=1)


class AgentUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    category_ids: Optional[List[int]] = None
    max_open_tickets: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None


class AvailabilityIn(BaseModel):
    availability: Availability


class AgentOut(ORM):
    id: int
    user_id: int
    name: str
    email: str
    categories: List[CategoryOut]
    availability: Availability
    max_open_tickets: int
    is_active: bool


# ---------- tickets ----------
class TicketCreate(BaseModel):
    category_id: int
    subject: str = Field(min_length=5, max_length=150)
    description: str = Field(min_length=10)
    priority: Priority = Priority.medium


class StatusIn(BaseModel):
    status: TicketStatus
    note: Optional[str] = Field(None, max_length=255)


class PriorityIn(BaseModel):
    priority: Priority


class AssignIn(BaseModel):
    agent_id: int


class TicketOut(ORM):
    id: int
    ticket_number: str
    customer_id: int
    category_id: int
    subject: str
    description: str
    priority: Priority
    status: TicketStatus
    assigned_agent_id: Optional[int]
    first_response_due: Optional[datetime]
    resolution_due: Optional[datetime]
    first_response_at: Optional[datetime]
    resolved_at: Optional[datetime]
    closed_at: Optional[datetime]
    sla_breached: bool
    reopen_count: int
    created_at: datetime
    updated_at: datetime


class HistoryOut(ORM):
    id: int
    ticket_id: int
    old_status: Optional[TicketStatus]
    new_status: TicketStatus
    changed_by: Optional[int]
    note: Optional[str]
    created_at: datetime


# ---------- messages ----------
class MessageIn(BaseModel):
    message: str = Field(max_length=2000)
    is_internal_note: bool = False

    @field_validator("message")
    @classmethod
    def _not_empty(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty")
        return v


class MessageOut(BaseModel):
    id: int
    ticket_id: int
    sender_id: int
    sender_name: Optional[str] = None
    sender_role: Role
    message: str
    is_internal_note: bool
    is_read: bool
    created_at: datetime


# ---------- feedback / notifications ----------
class FeedbackIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = Field(None, max_length=1000)


class FeedbackOut(ORM):
    id: int
    ticket_id: int
    customer_id: int
    rating: int
    comment: Optional[str]
    created_at: datetime


class NotificationOut(ORM):
    id: int
    ticket_id: Optional[int]
    type: str
    title: str
    message: str
    is_read: bool
    created_at: datetime
