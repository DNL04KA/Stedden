"""The Business Action Engine — the single place business actions execute.

The Brain requests a typed action by name; the Engine routes it to the business's
configured provider (Stedden-internal or an external CRM), records an audit
entry, and returns a compact JSON-able result the Brain can read before it speaks.

It is explicit dispatch over a fixed action vocabulary — no plugin registry, no
workflow engine. Adding a provider does not change this file; adding a new action
is a deliberate, visible edit here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from core.actions import ActionName
from engine.context import ActionContext, ActionRecord
from providers.base import Appointment, Customer, SchedulingConflict, Slot
from providers.registry import STEDDEN_INTERNAL

log = structlog.get_logger("stedden.engine")

_MAX_SLOTS = 6


class ActionEngine:
    async def dispatch(self, name: str, args: dict[str, Any], ctx: ActionContext) -> dict:
        try:
            action = ActionName(name)
        except ValueError:
            return self._record(ctx, name, args, "skipped", error=f"unknown action '{name}'")

        try:
            return await self._route(action, args, ctx)
        except SchedulingConflict as exc:
            return self._record(
                ctx,
                name,
                args,
                "failed",
                error=str(exc),
                result={"ok": False, "reason": "unavailable"},
            )
        except Exception as exc:  # provider/network failure — Brain should fall back
            log.warning("engine.action_failed", action=name, error=str(exc))
            return self._record(ctx, name, args, "failed", error=str(exc), result={"ok": False})

    # ── Routing ──────────────────────────────────────────────────────────────
    async def _route(self, action: ActionName, args: dict, ctx: ActionContext) -> dict:
        p = ctx.providers
        match action:
            case ActionName.FIND_CUSTOMER_BY_PHONE:
                cust = await p.customer.find_customer_by_phone(args["phone"])
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success",
                    result={"found": cust is not None, "customer": _customer(cust)},
                    capability="customer",
                )
            case ActionName.GET_CUSTOMER_HISTORY:
                appts = await p.customer.get_customer_history(phone=args["phone"])
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success",
                    result={"appointments": [_appt(a) for a in appts]},
                    capability="customer",
                )
            case ActionName.CHECK_AVAILABILITY:
                slots = await p.scheduling.check_availability(
                    service=args["service"], date=args["date"], part_of_day=args.get("part_of_day")
                )
                trimmed = slots[:_MAX_SLOTS]
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success",
                    result={
                        "slots": [_slot(s) for s in trimmed],
                        "count": len(slots),
                        "source": _sched_source(ctx),
                    },
                    capability="scheduling",
                )
            case ActionName.GET_APPOINTMENT:
                appts = await p.scheduling.get_appointment(phone=args["phone"])
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success",
                    result={"appointments": [_appt(a) for a in appts]},
                    capability="scheduling",
                )
            case ActionName.CREATE_CUSTOMER:
                cust = await p.customer.create_customer(
                    name=args["name"], phone=args["phone"], email=args.get("email", "")
                )
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success",
                    result={"ok": True, "customer": _customer(cust)},
                    external_ref=cust.external_ref,
                    capability="customer",
                )
            case ActionName.UPDATE_CUSTOMER:
                cust = await p.customer.update_customer(
                    phone=args["phone"], name=args.get("name"), email=args.get("email")
                )
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if cust else "failed",
                    result={"ok": cust is not None, "customer": _customer(cust)},
                    capability="customer",
                )
            case ActionName.ADD_CUSTOMER_NOTE:
                ok = await p.customer.add_customer_note(phone=args["phone"], note=args["note"])
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if ok else "failed",
                    result={"ok": ok},
                    capability="customer",
                )
            case ActionName.CREATE_APPOINTMENT:
                appt = await p.scheduling.create_appointment(
                    name=args["name"],
                    phone=args["phone"],
                    service=args["service"],
                    start=args["start"],
                    staff_id=args.get("staff_id"),
                )
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success",
                    result={"ok": True, "appointment": _appt(appt)},
                    external_ref=appt.external_ref,
                    capability="scheduling",
                )
            case ActionName.RESCHEDULE_APPOINTMENT:
                appt = await p.scheduling.reschedule_appointment(
                    appointment_id=args["appointment_id"], new_start=args["new_start"]
                )
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if appt else "failed",
                    result={"ok": appt is not None, "appointment": _appt(appt)},
                    capability="scheduling",
                )
            case ActionName.CANCEL_APPOINTMENT:
                ok = await p.scheduling.cancel_appointment(appointment_id=args["appointment_id"])
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if ok else "failed",
                    result={"ok": ok},
                    capability="scheduling",
                )
            case ActionName.SEND_CONFIRMATION:
                key = args.get("appointment_id") or args["phone"]
                if key in ctx._emailed:
                    return self._record(
                        ctx,
                        action.value,
                        args,
                        "skipped",
                        result={"ok": True, "already_sent": True},
                        capability="notification",
                    )
                ok = await p.notification.send_confirmation(
                    phone=args["phone"], appointment=None, channel=args.get("channel", "email")
                )
                if ok:
                    ctx._emailed.add(key)
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if ok else "failed",
                    result={"ok": ok},
                    capability="notification",
                )
            case ActionName.NOTIFY_STAFF:
                ok = await p.notification.notify_staff(
                    message=args["message"], priority=args.get("priority", "medium")
                )
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if ok else "failed",
                    result={"ok": ok},
                    capability="notification",
                )
            case ActionName.SEND_SMS:
                ok = await p.notification.send_sms(to=args["to"], body=args["body"])
                return self._record(
                    ctx,
                    action.value,
                    args,
                    "success" if ok else "failed",
                    result={"ok": ok},
                    capability="notification",
                )
            case ActionName.CREATE_LEAD:
                # Persisted in 4b; for now the audit record IS the lead hand-off.
                return self._record(ctx, action.value, args, "success", result={"ok": True})
            case ActionName.CREATE_CALLBACK_TASK:
                # The universal fallback when a booking can't be completed live.
                who = f"{args.get('name', '')} {args.get('phone', '')}".strip()
                await p.notification.notify_staff(
                    message=f"Callback needed: {args.get('reason', '')} — {who}",
                    priority="high",
                )
                return self._record(ctx, action.value, args, "success", result={"ok": True})
            case ActionName.TRANSFER_CALL:
                # Real warm-transfer arrives with telephony; recorded as intent now.
                return self._record(
                    ctx, action.value, args, "success", result={"ok": True, "transfer": "requested"}
                )
        return self._record(ctx, action.value, args, "skipped", error="unrouted action")

    # ── Audit helper ─────────────────────────────────────────────────────────
    def _record(
        self,
        ctx: ActionContext,
        action: str,
        args: dict,
        status: str,
        *,
        result: dict | None = None,
        error: str = "",
        external_ref: str | None = None,
        capability: str = "",
    ) -> dict:
        payload = dict(result or {})
        # Surface failures to the model so it can recover (e.g. offer a callback).
        if error:
            payload.setdefault("error", error)
        if status != "success":
            payload.setdefault("ok", False)
        provider = _provider_for(ctx, capability)
        ctx.audit.append(
            ActionRecord(
                action=action,
                args=args,
                status=status,
                result=payload,
                provider=provider,
                external_ref=external_ref,
                error=error,
            )
        )
        return payload


# ── Serialization (compact, model-friendly) ──────────────────────────────────
def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _label(dt: datetime) -> str:
    return dt.strftime("%a %b %d, %-I:%M %p")


def _slot(s: Slot) -> dict:
    return {
        "start": _iso(s.start),
        "end": _iso(s.end),
        "staff_id": s.staff_id,
        "staff_name": s.staff_name,
        "label": _label(s.start),
    }


def _appt(a: Appointment | None) -> dict | None:
    if a is None:
        return None
    return {
        "id": a.id,
        "service": a.service,
        "start": _iso(a.start),
        "end": _iso(a.end),
        "staff_name": a.staff_name,
        "status": a.status,
        "label": _label(a.start),
        "confirmation_code": a.confirmation_code,
        "external_ref": a.external_ref,
    }


def _customer(c: Customer | None) -> dict | None:
    if c is None:
        return None
    return {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "external_ref": c.external_ref,
    }


def _provider_for(ctx: ActionContext, capability: str) -> str:
    cfg = ctx.providers.config
    return {
        "customer": cfg.customer_provider,
        "scheduling": cfg.scheduling_provider,
        "notification": cfg.notification_provider,
    }.get(capability, "")


def _sched_source(ctx: ActionContext) -> str:
    return "external" if ctx.providers.scheduling_is_external else STEDDEN_INTERNAL
