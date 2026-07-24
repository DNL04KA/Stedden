"""ORM models.

Design notes:
- ``Call.external_call_sid`` holds the Twilio CallSid and is the unique external
  identifier. It's nullable so the text simulator (no telephony) can create calls
  with a synthetic ``sim-*`` id.
- Transcript is stored as one row per message (``TranscriptMessage``), never a
  single text blob, so it's queryable and displayable turn-by-turn.
- ``LeadRecord.call_id`` is unique — one lead per call — which is what makes
  ``save_lead`` idempotent (upsert). ``Notification`` has a unique (call_id,
  channel) constraint, which makes ``send_email`` idempotent.
- No audio is stored yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class Business(Base, TimestampMixin):
    __tablename__ = "business"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tagline: Mapped[str] = mapped_column(String(300), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    address: Mapped[str] = mapped_column(String(400), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    owner_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # How an inbound call maps to a business (set when telephony is wired).
    twilio_phone_number: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    greeting: Mapped[str] = mapped_column(Text, default="")
    voice_personality: Mapped[str] = mapped_column(Text, default="")
    escalation_line: Mapped[str] = mapped_column(Text, default="")
    recording_disclosure: Mapped[str] = mapped_column(Text, default="")

    services: Mapped[list[Service]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )
    faqs: Mapped[list[Faq]] = relationship(back_populates="business", cascade="all, delete-orphan")
    policies: Mapped[list[PolicyRow]] = relationship(
        back_populates="business", cascade="all, delete-orphan"
    )


class Service(Base, TimestampMixin):
    __tablename__ = "service"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), default="")
    price: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    business: Mapped[Business] = relationship(back_populates="services")


class Faq(Base, TimestampMixin):
    __tablename__ = "faq"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)

    business: Mapped[Business] = relationship(back_populates="faqs")


class PolicyRow(Base, TimestampMixin):
    __tablename__ = "policy"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    business: Mapped[Business] = relationship(back_populates="policies")


class Call(Base, TimestampMixin):
    __tablename__ = "call"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    # Twilio CallSid — unique external identifier. Null for simulator sessions
    # (which use a synthetic sim-* value instead).
    external_call_sid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    caller_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="in_progress", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list[TranscriptMessage]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="TranscriptMessage.seq"
    )
    lead: Mapped[LeadRecord | None] = relationship(back_populates="call", uselist=False)


class TranscriptMessage(Base):
    __tablename__ = "transcript_message"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)  # FSM state at this turn
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    call: Mapped[Call] = relationship(back_populates="messages")

    __table_args__ = (UniqueConstraint("call_id", "seq", name="uq_transcript_call_seq"),)


class LeadRecord(Base, TimestampMixin):
    __tablename__ = "lead"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # One lead per call -> upsert target that makes save_lead idempotent.
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("call.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    service: Mapped[str] = mapped_column(String(200), default="")
    intent: Mapped[str] = mapped_column(Text, default="")
    preferred_time: Mapped[str] = mapped_column(String(200), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="low", nullable=False)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    call: Mapped[Call] = relationship(back_populates="lead")


class Notification(Base, TimestampMixin):
    __tablename__ = "notification"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("call.id", ondelete="CASCADE"))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead.id", ondelete="SET NULL"), nullable=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="email", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="sent", nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), default="")

    # Unique (call_id, channel) -> makes send_email idempotent across restarts.
    __table_args__ = (UniqueConstraint("call_id", "channel", name="uq_notification_call_channel"),)
