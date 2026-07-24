"""front desk: customers, staff, hours, appointments, tasks, action log

Revision ID: 0002_front_desk
Revises: 0001_initial
Create Date: 2026-07-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_front_desk"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    # Provider configuration: one authoritative source per capability.
    for column in ("customer_provider", "scheduling_provider", "notification_provider"):
        op.add_column(
            "business",
            sa.Column(
                column, sa.String(length=50), server_default="stedden_internal", nullable=False
            ),
        )
    op.add_column(
        "service",
        sa.Column("duration_min", sa.Integer(), server_default="30", nullable=False),
    )

    op.create_table(
        "customer",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=320), server_default="", nullable=False),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("business_id", "phone", name="uq_customer_business_phone"),
    )

    op.create_table(
        "customer_note",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "staff",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "staff_hours",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("staff_id", "weekday", "start_time", name="uq_staff_hours_window"),
    )

    op.create_table(
        "business_hours",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "business_id", "weekday", "start_time", name="uq_business_hours_window"
        ),
    )

    op.create_table(
        "appointment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("service", sa.String(length=200), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="booked", nullable=False),
        sa.Column("confirmation_code", sa.String(length=20), server_default="", nullable=False),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_appointment_business_start", "appointment", ["business_id", "start_at"])

    # The database-level double-booking guard. Business logic checks first, but
    # this closes the race window: two concurrent inserts for the same staff and
    # overlapping time range cannot both commit. Cancelled rows don't block.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE appointment ADD CONSTRAINT ex_appointment_no_overlap
        EXCLUDE USING gist (
            staff_id WITH =,
            tstzrange(start_at, end_at) WITH &&
        ) WHERE (status != 'cancelled')
        """
    )

    op.create_table(
        "task",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("details", sa.Text(), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=50), server_default="", nullable=False),
        sa.Column("name", sa.String(length=200), server_default="", nullable=False),
        sa.Column("priority", sa.String(length=10), server_default="medium", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "action_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=50), server_default="", nullable=False),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("error", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["call_id"], ["call.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("call_id", "seq", name="uq_action_log_call_seq"),
    )


def downgrade() -> None:
    for table in (
        "action_log",
        "task",
        "appointment",
        "business_hours",
        "staff_hours",
        "staff",
        "customer_note",
        "customer",
    ):
        op.drop_table(table)
    op.drop_column("service", "duration_min")
    for column in ("notification_provider", "scheduling_provider", "customer_provider"):
        op.drop_column("business", column)
