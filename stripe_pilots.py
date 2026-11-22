"""Stripe coupon + promotion code for pilot codes.

DB is written first; Stripe second. A teacher who redeems late may keep the
repeating 100%-off discount past the pilot end — accepted by design.
Promotion codes are immutable for code and expires_at; we retire by
deactivating and create a new code when those fields change.
Stripe has no pilot start: the code is usable from creation (start-only
edits do not touch Stripe).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import stripe

import admin_time
import db

log = logging.getLogger(__name__)

CODE_RE = re.compile(r"^[A-Za-z0-9-]+$")
CODE_MAX_LEN = 50

# One StripeClient per DB target so staging never reuses the default account key.
_clients: dict[str, stripe.StripeClient] = {}


class StripePilotsError(Exception):
    def __init__(self, message: str, reason: str = "other"):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class StripeSyncResult:
    ok: bool
    configured: bool
    message: str
    livemode: bool | None = None
    coupon_id: str | None = None
    promotion_code_id: str | None = None


def _env_key(target: str | None = None) -> str:
    """Secret for the active (or given) DB target.

    Staging uses STAGING_STRIPE_SECRET_KEY only — never falls back to
    STRIPE_SECRET_KEY, so pilot codes cannot land in the wrong Stripe account.
    """
    chosen = (target or db.db_target()).strip().lower()
    if chosen == db.TARGET_STAGING:
        return (os.getenv("STAGING_STRIPE_SECRET_KEY") or "").strip().strip("'\"")
    return (os.getenv("STRIPE_SECRET_KEY") or "").strip().strip("'\"")


def configured(target: str | None = None) -> bool:
    return bool(_env_key(target))


def client() -> stripe.StripeClient:
    target = db.db_target()
    existing = _clients.get(target)
    if existing is not None:
        return existing
    key = _env_key(target)
    if not key:
        if target == db.TARGET_STAGING:
            raise StripePilotsError(
                "Staging Stripe is not configured (set STAGING_STRIPE_SECRET_KEY).",
                "not_configured",
            )
        raise StripePilotsError("Stripe is not configured.", "not_configured")
    created = stripe.StripeClient(key)
    _clients[target] = created
    return created


def reset_client() -> None:
    """Test helper: drop cached clients."""
    _clients.clear()


def validate_code_format(code: str) -> str | None:
    """Return an error message or None if ok. Does not change case."""
    if not code:
        return "Code is required."
    if len(code) > CODE_MAX_LEN:
        return f"Code must be at most {CODE_MAX_LEN} characters."
    if not CODE_RE.fullmatch(code):
        return "Code may only use letters, digits, and dashes (no spaces or symbols)."
    return None


def map_stripe_error(exc: BaseException) -> StripePilotsError:
    if isinstance(exc, StripePilotsError):
        return exc
    if isinstance(exc, stripe.AuthenticationError):
        return StripePilotsError(
            "Stripe rejected the API key (auth or permissions).",
            "auth",
        )
    if isinstance(exc, stripe.PermissionError):
        return StripePilotsError(
            "Stripe key is missing permission for coupons or promotion codes.",
            "auth",
        )
    if isinstance(exc, stripe.RateLimitError):
        return StripePilotsError("Stripe rate limit hit. Try again shortly.", "rate_limit")
    if isinstance(exc, stripe.APIConnectionError):
        return StripePilotsError("Could not reach Stripe (network or timeout).", "network")
    if isinstance(exc, stripe.InvalidRequestError):
        msg = (getattr(exc, "user_message", None) or str(exc) or "").lower()
        if "already" in msg and "code" in msg:
            return StripePilotsError("That code already exists in Stripe.", "code_exists")
        if "promotion code" in msg and "unique" in msg:
            return StripePilotsError("That code already exists in Stripe.", "code_exists")
        if "redeem_by" in msg and "past" in msg:
            return StripePilotsError(
                "Pilot end must be in the future for Stripe to create the code.",
                "other",
            )
        if "expires_at" in msg and "past" in msg:
            return StripePilotsError(
                "Pilot end must be in the future for Stripe to create the code.",
                "other",
            )
        return StripePilotsError("Stripe rejected the request.", "other")
    if isinstance(exc, stripe.StripeError):
        return StripePilotsError("Stripe request failed.", "other")
    return StripePilotsError("Stripe request failed.", "other")


def _mode_label(livemode: bool | None) -> str:
    if livemode is True:
        return "(live)"
    if livemode is False:
        return "(test)"
    return ""


def _pilot_end(pilot: dict) -> datetime:
    end = pilot["endTime"]
    if isinstance(end, str):
        end = datetime.fromisoformat(end)
    return end


def _pilot_start(pilot: dict) -> datetime:
    start = pilot["startTime"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    return start


def find_active_promotion_code(code: str) -> dict | None:
    """Return the first active promotion code with this code string, or None."""
    listed = client().v1.promotion_codes.list(
        params={"code": code, "active": True, "limit": 10}
    )
    data = listed.data if hasattr(listed, "data") else listed.get("data", [])
    for item in data:
        as_dict = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        if as_dict.get("active"):
            return as_dict
    return None


def _deactivate_promo(promo_id: str | None) -> None:
    if not promo_id:
        return
    try:
        client().v1.promotion_codes.update(promo_id, params={"active": False})
    except stripe.InvalidRequestError:
        log.info("Could not deactivate promotion code %s (already gone?)", promo_id)
    except stripe.StripeError as exc:
        raise map_stripe_error(exc) from exc


def release_orphaned_code(code: str, pilotid: int | None = None) -> None:
    """Free a code left active after a pilot was deleted.

    Stripe promotion codes cannot be deleted — only deactivated. An active
    leftover blocks creating the same code string again. If another live pilot
    still owns the code, raise code_exists.
    """
    found = find_active_promotion_code(code)
    if not found:
        return
    meta = found.get("metadata") or {}
    owner = str(meta.get("pilotid") or "")
    if pilotid is not None and owner == str(pilotid):
        return
    if owner.isdigit():
        other = db.get_pilot(int(owner))
        if other is not None and (pilotid is None or int(other["pilotid"]) != int(pilotid)):
            raise StripePilotsError("That code already exists in Stripe.", "code_exists")
    # Orphaned (pilot gone) or no metadata — deactivate so the code can be reused.
    _deactivate_promo(found.get("id"))


def code_taken_in_stripe(code: str, pilotid: int | None = None) -> bool:
    """True if another live pilot owns an active Stripe promo with this code.

    Orphaned active promos from deleted pilots are deactivated and treated as free.
    """
    if not configured():
        return False
    try:
        release_orphaned_code(code, pilotid=pilotid)
    except StripePilotsError as exc:
        if exc.reason == "code_exists":
            return True
        raise
    except stripe.StripeError as exc:
        raise map_stripe_error(exc) from exc
    return False


def _coupon_redeem_by(coupon_id: str) -> int | None:
    try:
        coupon = client().v1.coupons.retrieve(coupon_id)
    except stripe.InvalidRequestError:
        return None
    except stripe.StripeError as exc:
        raise map_stripe_error(exc) from exc
    as_dict = coupon.to_dict() if hasattr(coupon, "to_dict") else dict(coupon)
    value = as_dict.get("redeem_by")
    return int(value) if value is not None else None


def _create_coupon(pilot: dict, end_ts: int) -> Any:
    start = _pilot_start(pilot)
    end = _pilot_end(pilot)
    months = admin_time.months_rounded_up(start, end)
    pilotid = pilot["pilotid"]
    params = {
        "percent_off": 100,
        "duration": "repeating",
        "duration_in_months": months,
        "redeem_by": end_ts,
        "name": f"Pilot: {pilot['name']}"[:40],
        "metadata": {"pilotid": str(pilotid)},
    }
    try:
        return client().v1.coupons.create(
            params=params,
            options={"idempotency_key": f"pilot-{pilotid}-coupon-{end_ts}"},
        )
    except stripe.StripeError as exc:
        raise map_stripe_error(exc) from exc


def _create_promo(pilot: dict, coupon_id: str, end_ts: int, *, attempt: int = 1) -> Any:
    pilotid = pilot["pilotid"]
    code = pilot["code"]
    params = {
        "promotion": {"type": "coupon", "coupon": coupon_id},
        "code": code,
        "expires_at": end_ts,
        "metadata": {"pilotid": str(pilotid)},
    }
    # attempt suffix avoids Stripe replaying a cached failure after we free an orphan.
    try:
        return client().v1.promotion_codes.create(
            params=params,
            options={"idempotency_key": f"pilot-{pilotid}-promo-{code}-{end_ts}-a{attempt}"},
        )
    except stripe.StripeError as exc:
        raise map_stripe_error(exc) from exc


def _account_label() -> str:
    if db.db_target() == db.TARGET_STAGING:
        return "staging Stripe"
    return "Stripe"


def ensure_pilot_code(pilot: dict) -> StripeSyncResult:
    """Create coupon + promotion code if missing; save IDs. Idempotent."""
    if not configured():
        msg = (
            "Staging Stripe is not configured (set STAGING_STRIPE_SECRET_KEY)."
            if db.db_target() == db.TARGET_STAGING
            else "Stripe is not configured."
        )
        return StripeSyncResult(ok=False, configured=False, message=msg)

    end = _pilot_end(pilot)
    end_ts = admin_time.to_unix(end)
    coupon_id = pilot.get("stripeCouponId")
    promo_id = pilot.get("stripePromotionCodeId")
    livemode = None

    try:
        if coupon_id:
            redeem_by = _coupon_redeem_by(coupon_id)
            if redeem_by is None or redeem_by < end_ts:
                # Coupon redeem_by cannot be changed; mint a new coupon.
                coupon = _create_coupon(pilot, end_ts)
                coupon_id = coupon.id
                livemode = bool(coupon.livemode)
                db.set_pilot_stripe_ids(pilot["pilotid"], coupon_id, promo_id)
            else:
                try:
                    existing = client().v1.coupons.retrieve(coupon_id)
                    livemode = bool(existing.livemode)
                except stripe.StripeError:
                    pass
        else:
            coupon = _create_coupon(pilot, end_ts)
            coupon_id = coupon.id
            livemode = bool(coupon.livemode)
            db.set_pilot_stripe_ids(pilot["pilotid"], coupon_id, None)

        account = _account_label()
        if promo_id:
            db.set_pilot_stripe_ids(pilot["pilotid"], coupon_id, promo_id)
            return StripeSyncResult(
                ok=True,
                configured=True,
                message=f"{account} code synced {_mode_label(livemode)}".strip(),
                livemode=livemode,
                coupon_id=coupon_id,
                promotion_code_id=promo_id,
            )

        # Free leftovers from a prior delete so the same code string can be recreated.
        release_orphaned_code(pilot["code"], pilotid=pilot["pilotid"])
        try:
            promo = _create_promo(pilot, coupon_id, end_ts, attempt=1)
        except StripePilotsError as exc:
            if exc.reason != "code_exists":
                raise
            # Race / missed orphan: deactivate again and retry once.
            release_orphaned_code(pilot["code"], pilotid=pilot["pilotid"])
            promo = _create_promo(pilot, coupon_id, end_ts, attempt=2)
        promo_id = promo.id
        livemode = bool(promo.livemode)
        db.set_pilot_stripe_ids(pilot["pilotid"], coupon_id, promo_id)
        return StripeSyncResult(
            ok=True,
            configured=True,
            message=f"{account} code created {_mode_label(livemode)}".strip(),
            livemode=livemode,
            coupon_id=coupon_id,
            promotion_code_id=promo_id,
        )
    except StripePilotsError as exc:
        return StripeSyncResult(ok=False, configured=True, message=exc.message)
    except Exception as exc:  # noqa: BLE001 — map anything else safely
        mapped = map_stripe_error(exc)
        log.exception("Stripe sync failed for pilot %s", pilot.get("pilotid"))
        return StripeSyncResult(ok=False, configured=True, message=mapped.message)


def retire_pilot_code(pilot: dict) -> StripeSyncResult:
    """Deactivate the pilot's Stripe promotion code (Stripe cannot delete them)."""
    if not configured():
        msg = (
            "Staging Stripe is not configured (set STAGING_STRIPE_SECRET_KEY)."
            if db.db_target() == db.TARGET_STAGING
            else "Stripe is not configured."
        )
        return StripeSyncResult(ok=False, configured=False, message=msg)
    promo_id = pilot.get("stripePromotionCodeId")
    code = pilot.get("code") or ""
    try:
        if promo_id:
            _deactivate_promo(promo_id)
        # Also clear any still-active promo with this code string (missing id, etc.).
        if code:
            leftover = find_active_promotion_code(code)
            if leftover:
                _deactivate_promo(leftover.get("id"))
        return StripeSyncResult(
            ok=True,
            configured=True,
            message="Stripe promotion code deactivated (Stripe does not allow deleting codes).",
            coupon_id=pilot.get("stripeCouponId"),
            promotion_code_id=promo_id,
        )
    except StripePilotsError as exc:
        return StripeSyncResult(ok=False, configured=True, message=exc.message)


def retire_and_replace(pilot: dict, *, old_promo_id: str | None, old_coupon_id: str | None) -> StripeSyncResult:
    """Deactivate the old promotion code, then ensure a fresh one.

    Used when the pilot code or end time changes. Coupons are not deleted;
    a new coupon is created only when redeem_by would be too early.
    """
    if not configured():
        return StripeSyncResult(
            ok=False,
            configured=False,
            message="Stripe is not configured.",
        )
    try:
        _deactivate_promo(old_promo_id)
    except StripePilotsError as exc:
        return StripeSyncResult(ok=False, configured=True, message=exc.message)

    # Clear promo id so ensure creates a new one; keep coupon if still usable.
    end_ts = admin_time.to_unix(_pilot_end(pilot))
    coupon_id = old_coupon_id
    if coupon_id:
        redeem_by = _coupon_redeem_by(coupon_id)
        if redeem_by is None or redeem_by < end_ts:
            coupon_id = None
    db.set_pilot_stripe_ids(pilot["pilotid"], coupon_id, None)
    refreshed = dict(pilot)
    refreshed["stripeCouponId"] = coupon_id
    refreshed["stripePromotionCodeId"] = None
    return ensure_pilot_code(refreshed)


def sync_after_create(pilot: dict) -> StripeSyncResult:
    return ensure_pilot_code(pilot)


def sync_after_update(before: dict, after: dict) -> StripeSyncResult:
    """Keep Stripe in step after a successful DB update.

    Start-only changes: no Stripe update (codes have no start).
    Name/country/state-only: no Stripe update.
  """
    if not configured():
        msg = (
            "Staging Stripe is not configured (set STAGING_STRIPE_SECRET_KEY)."
            if db.db_target() == db.TARGET_STAGING
            else "Stripe is not configured."
        )
        return StripeSyncResult(ok=False, configured=False, message=msg)

    code_changed = before.get("code") != after.get("code")
    end_changed = before.get("endTime") != after.get("endTime")
    missing = not after.get("stripePromotionCodeId") or not after.get("stripeCouponId")

    if not code_changed and not end_changed and not missing:
        # Start / name / country / state only — nothing to do in Stripe.
        return StripeSyncResult(
            ok=True,
            configured=True,
            message="No Stripe change needed.",
            coupon_id=after.get("stripeCouponId"),
            promotion_code_id=after.get("stripePromotionCodeId"),
        )

    if code_changed or end_changed:
        return retire_and_replace(
            after,
            old_promo_id=before.get("stripePromotionCodeId"),
            old_coupon_id=before.get("stripeCouponId") or after.get("stripeCouponId"),
        )
    return ensure_pilot_code(after)


def stripe_ui_state(pilot: dict | None) -> dict:
    """Template helper: synced / missing / not_configured."""
    if not configured():
        detail = (
            "STAGING_STRIPE_SECRET_KEY is not set."
            if db.db_target() == db.TARGET_STAGING
            else "STRIPE_SECRET_KEY is not set."
        )
        return {
            "state": "not_configured",
            "label": "Not configured",
            "detail": detail,
            "can_retry": False,
        }
    if not pilot:
        return {
            "state": "not_configured",
            "label": "Not configured",
            "detail": "",
            "can_retry": False,
        }
    promo = pilot.get("stripePromotionCodeId")
    coupon = pilot.get("stripeCouponId")
    if promo and coupon:
        return {
            "state": "synced",
            "label": "Synced",
            "detail": promo,
            "can_retry": False,
        }
    return {
        "state": "missing",
        "label": "Missing",
        "detail": "Stripe code not created yet.",
        "can_retry": True,
    }
