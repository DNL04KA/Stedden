"""Lumera Aesthetics — the fictional demo med spa.

100% synthetic. No real business, patients, or PHI. Prices are made up for the
demo. A few services intentionally omit a price so we can exercise the
"unknown price -> escalate" path.
"""

from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from core.kb import FaqItem, KnowledgeBase, Policy, Service
from providers.base import StaffMember, WorkingHours
from providers.internal import InternalDataset
from providers.registry import STEDDEN_INTERNAL, ProviderConfig

# Stable id so seeded rows and simulator sessions line up across runs.
LUMERA_BUSINESS_ID = "11111111-1111-1111-1111-111111111111"
LUMERA_TZ = ZoneInfo("America/New_York")

_HOURS = {
    "Monday": "9:00 AM – 6:00 PM",
    "Tuesday": "9:00 AM – 6:00 PM",
    "Wednesday": "9:00 AM – 7:00 PM",
    "Thursday": "9:00 AM – 7:00 PM",
    "Friday": "9:00 AM – 5:00 PM",
    "Saturday": "10:00 AM – 3:00 PM",
    "Sunday": "Closed",
}

_SERVICES = [
    Service(
        name="Botox",
        category="Injectables",
        price="$12 per unit",
        description="Wrinkle relaxer for forehead, frown lines, and crow's feet.",
    ),
    Service(
        name="Dysport",
        category="Injectables",
        price="$4 per unit",
        description="Alternative neuromodulator to Botox.",
    ),
    Service(
        name="Juvederm Dermal Filler",
        category="Injectables",
        price="$650 per syringe",
        description="Hyaluronic acid filler for cheeks, folds, and volume.",
    ),
    Service(
        name="Lip Filler",
        category="Injectables",
        price="$600 per syringe",
        description="Hyaluronic acid filler to add volume and shape to lips.",
    ),
    Service(
        name="Kybella",
        category="Injectables",
        price="$1,200 per session",
        description="Injectable that dissolves fat under the chin.",
    ),
    Service(
        name="Sculptra",
        category="Injectables",
        price="$850 per vial",
        description="Collagen-stimulating filler for gradual volume restoration.",
    ),
    Service(
        name="HydraFacial",
        category="Facials",
        price="$199",
        description="Cleanses, exfoliates, and hydrates in one session.",
    ),
    Service(
        name="Dermaplaning",
        category="Facials",
        price="$120",
        description="Physical exfoliation that removes dead skin and fine hair.",
    ),
    Service(
        name="Chemical Peel",
        category="Facials",
        price="$175",
        description="Resurfacing peel for tone and texture; strengths vary.",
    ),
    Service(
        name="Microneedling",
        category="Skin",
        price="$350 per session",
        description="Collagen induction therapy for texture and scars.",
    ),
    Service(
        name="Morpheus8",
        category="Skin",
        price="",
        description="RF microneedling for skin tightening; pricing set at consultation.",
    ),
    Service(
        name="Laser Hair Removal",
        category="Laser",
        price="$99 per small area",
        description="Reduces unwanted hair; package pricing for larger areas.",
    ),
    Service(
        name="IPL Photofacial",
        category="Laser",
        price="$275",
        description="Targets sun damage, redness, and pigmentation.",
    ),
    Service(
        name="PRP Hair Restoration",
        category="Restorative",
        price="",
        description="Platelet-rich plasma for thinning hair; pricing set at consultation.",
    ),
    Service(
        name="B12 / Vitamin Injection",
        category="Wellness",
        price="$35",
        description="Energy and wellness boost injection.",
    ),
]

_FAQS = [
    FaqItem(
        question="What are your hours?",
        answer="We're open Monday and Tuesday 9–6, Wednesday and Thursday 9–7, "
        "Friday 9–5, Saturday 10–3, and closed Sunday.",
    ),
    FaqItem(
        question="Where are you located?",
        answer="We're at 1200 Brickell Avenue, Suite 400, Miami, FL 33131.",
    ),
    FaqItem(
        question="Is there parking?",
        answer="Yes, complimentary validated parking is available in the building garage.",
    ),
    FaqItem(
        question="Are consultations free?", answer="Yes, initial consultations are complimentary."
    ),
    FaqItem(
        question="Do I need an appointment or do you take walk-ins?",
        answer="We're by appointment. We occasionally accommodate walk-ins if a "
        "provider is available, but booking ahead is best.",
    ),
    FaqItem(
        question="What is your cancellation policy?",
        answer="We ask for at least 24 hours' notice. Late cancellations or no-shows "
        "may forfeit the deposit.",
    ),
    FaqItem(
        question="Do you require a deposit?",
        answer="A $50 deposit is required to hold injectable and laser appointments, "
        "and it's applied to your treatment.",
    ),
    FaqItem(
        question="What payment methods do you accept?",
        answer="We accept all major credit cards, debit, and cash.",
    ),
    FaqItem(
        question="Do you offer financing?",
        answer="Yes, we offer financing through Cherry with approval.",
    ),
    FaqItem(
        question="Do you offer gift cards?", answer="Yes, gift cards are available in any amount."
    ),
    FaqItem(
        question="Do you have a membership program?",
        answer="Yes, our Lumera Glow membership includes monthly credits and member "
        "pricing. Ask us for current details.",
    ),
    FaqItem(
        question="Is there a discount for first-time clients?",
        answer="New clients receive 10% off their first single treatment.",
    ),
    FaqItem(
        question="Who performs the injections?",
        answer="Injectables are performed by licensed nurse injectors and a PA under "
        "the supervision of our medical director.",
    ),
    FaqItem(
        question="Are your treatments FDA approved?",
        answer="We use FDA-approved products such as Botox, Dysport, and Juvederm.",
    ),
    FaqItem(
        question="How long does Botox take to work?",
        answer="Most clients start to see results in 3–5 days, with full effect around "
        "two weeks. Our provider will review specifics at your visit.",
    ),
    FaqItem(
        question="Is there downtime after a HydraFacial?",
        answer="There's typically no downtime after a HydraFacial; some redness can occur briefly.",
    ),
    FaqItem(
        question="Do you offer numbing before treatments?",
        answer="Yes, topical numbing is available for treatments where it's appropriate.",
    ),
    FaqItem(
        question="Is there an age requirement?",
        answer="Clients must be 18 or older for aesthetic treatments.",
    ),
    FaqItem(
        question="Can I get treatments while pregnant or breastfeeding?",
        answer="For your safety, our medical provider reviews this individually. Please "
        "let us know and we'll have the team follow up before booking.",
    ),
    FaqItem(
        question="What should I do before and after my appointment?",
        answer="We'll send specific pre- and post-care instructions when you book; in "
        "general, avoid alcohol and blood thinners before injectables.",
    ),
]

_POLICIES = [
    Policy(
        title="Cancellations",
        content="24 hours' notice required; late cancellations may forfeit the deposit.",
    ),
    Policy(
        title="Deposits",
        content="$50 deposit on injectable and laser bookings, applied to treatment.",
    ),
    Policy(title="Late arrivals", content="Arrivals more than 15 minutes late may be rescheduled."),
    Policy(
        title="Medical questions",
        content="Clinical, diagnostic, or medical-suitability questions are answered "
        "only by our medical team, not over the phone by reception.",
    ),
]

_GREETING = (
    "Thank you for calling Lumera Aesthetics, this is your virtual receptionist. "
    "Just so you know, this call may be recorded and you're speaking with an "
    "automated assistant. How can I help you today?"
)

_VOICE_PERSONALITY = (
    "Warm, polished, and concise — like the best front-desk coordinator at a "
    "high-end Miami med spa. Friendly but never pushy. Speaks in short, natural "
    "sentences suitable for being read aloud."
)

_ESCALATION_LINE = (
    "That's a great question — I'll have someone from our team follow up with you on that."
)

_RECORDING_DISCLOSURE = "This call may be recorded and you're speaking with an automated assistant."


# ── Scheduling data (Mode 2, Stedden-internal) ───────────────────────────────
# Structured hours for the availability engine. weekday: Mon=0 … Sun=6.
_BUSINESS_HOURS = [
    WorkingHours(0, time(9, 0), time(18, 0)),  # Mon
    WorkingHours(1, time(9, 0), time(18, 0)),  # Tue
    WorkingHours(2, time(9, 0), time(19, 0)),  # Wed
    WorkingHours(3, time(9, 0), time(19, 0)),  # Thu
    WorkingHours(4, time(9, 0), time(17, 0)),  # Fri
    WorkingHours(5, time(10, 0), time(15, 0)),  # Sat
    # Sunday: closed (absent)
]

_STAFF = [
    StaffMember(
        id="staff-sofia",
        name="Nurse Sofia",
        hours=[WorkingHours(d, time(9, 0), time(17, 0)) for d in range(0, 5)],  # Mon–Fri
    ),
    StaffMember(
        id="staff-daniel",
        name="PA Daniel",
        hours=[
            WorkingHours(2, time(9, 0), time(19, 0)),  # Wed
            WorkingHours(3, time(9, 0), time(19, 0)),  # Thu
            WorkingHours(4, time(9, 0), time(17, 0)),  # Fri
            WorkingHours(5, time(10, 0), time(15, 0)),  # Sat
        ],
    ),
]

_SERVICE_DURATIONS = {
    "botox": 30,
    "dysport": 30,
    "juvederm dermal filler": 45,
    "lip filler": 45,
    "kybella": 45,
    "sculptra": 45,
    "hydrafacial": 60,
    "dermaplaning": 45,
    "chemical peel": 45,
    "microneedling": 60,
    "morpheus8": 60,
    "laser hair removal": 30,
    "ipl photofacial": 45,
    "prp hair restoration": 60,
    "b12 / vitamin injection": 15,
    "consultation": 30,
}


def lumera_internal_dataset() -> InternalDataset:
    """A fresh in-memory front-desk dataset for Lumera (empty appointment book)."""
    return InternalDataset(
        tz=LUMERA_TZ,
        business_hours=list(_BUSINESS_HOURS),
        staff=list(_STAFF),
        service_durations=dict(_SERVICE_DURATIONS),
        default_duration_min=30,
    )


def lumera_provider_config() -> ProviderConfig:
    """Lumera runs fully on Stedden-internal providers (Mode 2)."""
    return ProviderConfig(
        customer_provider=STEDDEN_INTERNAL,
        scheduling_provider=STEDDEN_INTERNAL,
        notification_provider=STEDDEN_INTERNAL,
    )


def lumera_kb(owner_email: str = "owner@lumera-demo.com") -> KnowledgeBase:
    """Return the Lumera Aesthetics knowledge base for the Brain and simulator."""
    return KnowledgeBase(
        business_id=LUMERA_BUSINESS_ID,
        name="Lumera Aesthetics",
        tagline="Miami's glow, perfected.",
        phone="(305) 555-0142",
        address="1200 Brickell Avenue, Suite 400, Miami, FL 33131",
        timezone="America/New_York",
        hours=_HOURS,
        services=_SERVICES,
        faqs=_FAQS,
        policies=_POLICIES,
        greeting=_GREETING,
        voice_personality=_VOICE_PERSONALITY,
        escalation_line=_ESCALATION_LINE,
        recording_disclosure=_RECORDING_DISCLOSURE,
        owner_email=owner_email,
    )
