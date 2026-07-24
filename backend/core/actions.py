"""The business-action vocabulary the Brain can request.

The Brain only ever names an action and supplies typed arguments — it never knows
which provider (Stedden-internal, Mangomint, Google Calendar, …) will fulfil it.
Actions split into:

- QUERY actions (reads): executed mid-turn so the Brain sees real data before it
  makes any factual claim (availability, customer records, existing appointments).
- COMMAND actions (writes): also executed mid-turn so the Brain only confirms
  after the provider returns success (e.g. an appointment is truly booked).

``end_call`` is NOT an action here — it is a control flag on ``TurnResult``.

These names + JSON-schema parameter specs are surfaced to the model as tools.
Adding a provider later does not change this vocabulary, the FSM, or the Brain.
"""

from __future__ import annotations

from enum import StrEnum


class ActionName(StrEnum):
    # ── Queries (reads) ──────────────────────────────────────────────
    FIND_CUSTOMER_BY_PHONE = "find_customer_by_phone"
    GET_CUSTOMER_HISTORY = "get_customer_history"
    CHECK_AVAILABILITY = "check_availability"
    GET_APPOINTMENT = "get_appointment"
    # ── Commands (writes) ────────────────────────────────────────────
    CREATE_CUSTOMER = "create_customer"
    UPDATE_CUSTOMER = "update_customer"
    ADD_CUSTOMER_NOTE = "add_customer_note"
    CREATE_APPOINTMENT = "create_appointment"
    RESCHEDULE_APPOINTMENT = "reschedule_appointment"
    CANCEL_APPOINTMENT = "cancel_appointment"
    CREATE_LEAD = "create_lead"
    SEND_CONFIRMATION = "send_confirmation"
    NOTIFY_STAFF = "notify_staff"
    SEND_SMS = "send_sms"
    CREATE_CALLBACK_TASK = "create_callback_task"
    TRANSFER_CALL = "transfer_call"


QUERY_ACTIONS: frozenset[ActionName] = frozenset(
    {
        ActionName.FIND_CUSTOMER_BY_PHONE,
        ActionName.GET_CUSTOMER_HISTORY,
        ActionName.CHECK_AVAILABILITY,
        ActionName.GET_APPOINTMENT,
    }
)


def is_query(action: ActionName) -> bool:
    return action in QUERY_ACTIONS


# JSON-schema parameter specs, one per action. Surfaced to the model as function
# tools. Kept intentionally flat and phone-keyed so the model can fill them from
# a spoken conversation without needing internal database IDs.
def _obj(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_OPT_STR = {"type": "string"}

TOOL_PARAMETERS: dict[ActionName, dict] = {
    ActionName.FIND_CUSTOMER_BY_PHONE: _obj({"phone": _STR}, ["phone"]),
    ActionName.GET_CUSTOMER_HISTORY: _obj({"phone": _STR}, ["phone"]),
    ActionName.CHECK_AVAILABILITY: _obj(
        {
            "service": _STR,
            "date": {"type": "string", "description": "Requested day as YYYY-MM-DD."},
            "part_of_day": {"type": "string", "description": "optional: morning/afternoon/evening"},
        },
        ["service", "date"],
    ),
    ActionName.GET_APPOINTMENT: _obj({"phone": _STR}, ["phone"]),
    ActionName.CREATE_CUSTOMER: _obj(
        {"name": _STR, "phone": _STR, "email": _OPT_STR}, ["name", "phone"]
    ),
    ActionName.UPDATE_CUSTOMER: _obj(
        {"phone": _STR, "name": _OPT_STR, "email": _OPT_STR}, ["phone"]
    ),
    ActionName.ADD_CUSTOMER_NOTE: _obj({"phone": _STR, "note": _STR}, ["phone", "note"]),
    ActionName.CREATE_APPOINTMENT: _obj(
        {
            "name": _STR,
            "phone": _STR,
            "service": _STR,
            "start": {
                "type": "string",
                "description": "Slot start, ISO-8601 local, e.g. 2026-07-24T14:00:00.",
            },
            "staff_id": {"type": "string", "description": "optional: from a returned slot"},
        },
        ["name", "phone", "service", "start"],
    ),
    ActionName.RESCHEDULE_APPOINTMENT: _obj(
        {"appointment_id": _STR, "new_start": _STR}, ["appointment_id", "new_start"]
    ),
    ActionName.CANCEL_APPOINTMENT: _obj({"appointment_id": _STR}, ["appointment_id"]),
    ActionName.CREATE_LEAD: _obj(
        {
            "name": _OPT_STR,
            "phone": _OPT_STR,
            "service": _OPT_STR,
            "intent": _OPT_STR,
            "preferred_time": _OPT_STR,
            "summary": _OPT_STR,
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "needs_human": {"type": "boolean"},
        },
        [],
    ),
    ActionName.SEND_CONFIRMATION: _obj(
        {
            "phone": _STR,
            "appointment_id": _OPT_STR,
            "channel": {"type": "string", "enum": ["email", "sms"]},
        },
        ["phone"],
    ),
    ActionName.NOTIFY_STAFF: _obj(
        {"message": _STR, "priority": {"type": "string", "enum": ["low", "medium", "high"]}},
        ["message"],
    ),
    ActionName.SEND_SMS: _obj({"to": _STR, "body": _STR}, ["to", "body"]),
    ActionName.CREATE_CALLBACK_TASK: _obj(
        {"reason": _STR, "details": _OPT_STR, "phone": _OPT_STR, "name": _OPT_STR}, ["reason"]
    ),
    ActionName.TRANSFER_CALL: _obj({"reason": _OPT_STR}, []),
}

TOOL_DESCRIPTIONS: dict[ActionName, str] = {
    ActionName.FIND_CUSTOMER_BY_PHONE: "Look up an existing customer by phone number.",
    ActionName.GET_CUSTOMER_HISTORY: "Get a customer's past and upcoming appointments and notes.",
    ActionName.CHECK_AVAILABILITY: (
        "Get REAL bookable time slots for a service on a date. Never invent slots."
    ),
    ActionName.GET_APPOINTMENT: "Find the caller's upcoming appointment(s) by phone.",
    ActionName.CREATE_CUSTOMER: "Create a new customer record.",
    ActionName.UPDATE_CUSTOMER: "Update an existing customer's details.",
    ActionName.ADD_CUSTOMER_NOTE: "Attach a note to a customer record.",
    ActionName.CREATE_APPOINTMENT: (
        "Book an appointment in a returned slot. Only confirm to the caller after this succeeds."
    ),
    ActionName.RESCHEDULE_APPOINTMENT: "Move an existing appointment to a new start time.",
    ActionName.CANCEL_APPOINTMENT: "Cancel an existing appointment.",
    ActionName.CREATE_LEAD: (
        "Record a lead for human follow-up (interest, or when no booking is possible)."
    ),
    ActionName.SEND_CONFIRMATION: "Send an appointment confirmation to the customer.",
    ActionName.NOTIFY_STAFF: "Notify the business's staff about something needing attention.",
    ActionName.SEND_SMS: "Send an SMS text message.",
    ActionName.CREATE_CALLBACK_TASK: "Create a task for staff to call the customer back.",
    ActionName.TRANSFER_CALL: "Request a transfer of the live call to a human.",
}


def tool_specs() -> list[dict]:
    """Return OpenAI Responses-API function tool definitions for every action."""
    return [
        {
            "type": "function",
            "name": name.value,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": TOOL_PARAMETERS[name],
        }
        for name in ActionName
    ]
