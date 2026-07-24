"""The knowledge base the Brain is allowed to speak from — and nothing else.

This is the *entire* world the receptionist knows. If a fact isn't here, the
Brain must not invent it; it escalates. For a single business with a few dozen
facts this fits comfortably in the system prompt, so there is no retrieval /
vector search — that would be over-engineering.
"""

from __future__ import annotations

from pydantic import BaseModel


class Service(BaseModel):
    name: str
    category: str = ""
    #: Human-facing price string, e.g. "$12 per unit". Empty means *not published*
    #: — the Brain must treat an empty price as "unknown" and escalate rather than
    #: guess a number.
    price: str = ""
    description: str = ""


class FaqItem(BaseModel):
    question: str
    answer: str


class Policy(BaseModel):
    title: str
    content: str


class KnowledgeBase(BaseModel):
    business_id: str
    name: str
    tagline: str = ""
    phone: str = ""
    address: str = ""
    timezone: str = "America/New_York"
    #: Ordered day -> hours string, e.g. {"Monday": "9:00 AM – 6:00 PM"}.
    hours: dict[str, str]
    services: list[Service]
    faqs: list[FaqItem]
    policies: list[Policy]
    greeting: str
    voice_personality: str
    escalation_line: str
    recording_disclosure: str
    #: Where captured leads are emailed.
    owner_email: str

    def render(self) -> str:
        """Render the KB as compact text for the system prompt."""
        lines: list[str] = []
        lines.append(f"BUSINESS: {self.name}")
        if self.tagline:
            lines.append(f"Tagline: {self.tagline}")
        if self.address:
            lines.append(f"Address: {self.address}")
        if self.phone:
            lines.append(f"Phone: {self.phone}")
        lines.append(f"Timezone: {self.timezone}")

        lines.append("\nHOURS:")
        for day, hrs in self.hours.items():
            lines.append(f"  {day}: {hrs}")

        lines.append("\nSERVICES (only quote a price if one is listed here):")
        for s in self.services:
            price = s.price if s.price else "price not published — must escalate if asked"
            cat = f" [{s.category}]" if s.category else ""
            desc = f" — {s.description}" if s.description else ""
            lines.append(f"  • {s.name}{cat}: {price}{desc}")

        lines.append("\nFAQ:")
        for f in self.faqs:
            lines.append(f"  Q: {f.question}\n  A: {f.answer}")

        lines.append("\nPOLICIES:")
        for p in self.policies:
            lines.append(f"  {p.title}: {p.content}")

        return "\n".join(lines)
