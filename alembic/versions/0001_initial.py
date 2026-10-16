"""initial schema + default SLA policies

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def ts():
    return [
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
    ]


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        *ts(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        *ts(),
        sa.UniqueConstraint("category_name", name="uq_categories_name"),
    )
    op.create_table(
        "sla_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=False),
        *ts(),
        sa.UniqueConstraint("priority", name="uq_sla_priority"),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("availability", sa.String(20), nullable=False, server_default="Offline"),
        sa.Column("max_open_tickets", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        *ts(),
        sa.UniqueConstraint("user_id", name="uq_agents_user"),
    )
    op.create_table(
        "agent_categories",
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
        *ts(),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_number", sa.String(30), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
        sa.Column("subject", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False, server_default="Medium"),
        sa.Column("status", sa.String(30), nullable=False, server_default="Open"),
        sa.Column("assigned_agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("first_response_due", sa.DateTime(), nullable=True),
        sa.Column("resolution_due", sa.DateTime(), nullable=True),
        sa.Column("first_response_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("sla_breached", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("waiting_since", sa.DateTime(), nullable=True),
        sa.Column("paused_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fr_escalated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("res_escalated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        *ts(),
        sa.UniqueConstraint("ticket_number", name="uq_tickets_number"),
    )
    op.create_index("ix_tickets_customer_id", "tickets", ["customer_id"])
    op.create_index("ix_tickets_category_id", "tickets", ["category_id"])
    op.create_index("ix_tickets_status", "tickets", ["status"])
    op.create_index("ix_tickets_assigned_agent_id", "tickets", ["assigned_agent_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sender_role", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_internal_note", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        *ts(),
    )
    op.create_index("ix_messages_ticket_id", "messages", ["ticket_id"])
    op.create_table(
        "ticket_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_status", sa.String(30), nullable=True),
        sa.Column("new_status", sa.String(30), nullable=False),
        sa.Column("changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        *ts(),
    )
    op.create_index("ix_history_ticket_id", "ticket_status_history", ["ticket_id"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        *ts(),
        sa.UniqueConstraint("ticket_id", name="uq_feedback_ticket"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        *ts(),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])

    # default SLA policies
    sla = sa.table("sla_policies", sa.column("priority", sa.String), sa.column("first_response_minutes", sa.Integer),
                   sa.column("resolution_minutes", sa.Integer))
    op.bulk_insert(sla, [
        {"priority": "Low", "first_response_minutes": 24 * 60, "resolution_minutes": 72 * 60},
        {"priority": "Medium", "first_response_minutes": 8 * 60, "resolution_minutes": 48 * 60},
        {"priority": "High", "first_response_minutes": 2 * 60, "resolution_minutes": 24 * 60},
        {"priority": "Urgent", "first_response_minutes": 30, "resolution_minutes": 8 * 60},
    ])


def downgrade():
    for t in ("notifications", "feedback", "ticket_status_history", "messages", "tickets",
              "agent_categories", "agents", "sla_policies", "categories", "users"):
        op.drop_table(t)
