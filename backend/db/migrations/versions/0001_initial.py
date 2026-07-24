"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "business",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tagline", sa.String(length=300), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=50), server_default="", nullable=False),
        sa.Column("address", sa.String(length=400), server_default="", nullable=False),
        sa.Column(
            "timezone", sa.String(length=64), server_default="America/New_York", nullable=False
        ),
        sa.Column("owner_email", sa.String(length=320), nullable=False),
        sa.Column("twilio_phone_number", sa.String(length=32), nullable=True),
        sa.Column("greeting", sa.Text(), server_default="", nullable=False),
        sa.Column("voice_personality", sa.Text(), server_default="", nullable=False),
        sa.Column("escalation_line", sa.Text(), server_default="", nullable=False),
        sa.Column("recording_disclosure", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("twilio_phone_number", name="uq_business_twilio_number"),
    )

    op.create_table(
        "service",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=100), server_default="", nullable=False),
        sa.Column("price", sa.String(length=100), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "faq",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "policy",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "call",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("external_call_sid", sa.String(length=64), nullable=True),
        sa.Column("caller_number", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="in_progress", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("external_call_sid", name="uq_call_external_sid"),
    )

    op.create_table(
        "transcript_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["call.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("call_id", "seq", name="uq_transcript_call_seq"),
    )

    op.create_table(
        "lead",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), server_default="", nullable=False),
        sa.Column("phone", sa.String(length=50), server_default="", nullable=False),
        sa.Column("service", sa.String(length=200), server_default="", nullable=False),
        sa.Column("intent", sa.Text(), server_default="", nullable=False),
        sa.Column("preferred_time", sa.String(length=200), server_default="", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("priority", sa.String(length=10), server_default="low", nullable=False),
        sa.Column("needs_human", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("delivered", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["call.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["business_id"], ["business.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("call_id", name="uq_lead_call"),
    )

    op.create_table(
        "notification",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("lead_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(length=20), server_default="email", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="sent", nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=300), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["call.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lead_id"], ["lead.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("call_id", "channel", name="uq_notification_call_channel"),
    )


def downgrade() -> None:
    for table in (
        "notification",
        "lead",
        "transcript_message",
        "call",
        "policy",
        "faq",
        "service",
        "business",
    ):
        op.drop_table(table)
