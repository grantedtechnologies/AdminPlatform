"""Unit tests for pilot Stripe sync and admin timezone. Never hits live Stripe."""
from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import admin_time
import stripe_pilots


class TimezoneTests(unittest.TestCase):
    def setUp(self):
        os.environ["ADMIN_TIMEZONE"] = "America/New_York"
        admin_time.admin_zone.cache_clear()

    def tearDown(self):
        admin_time.admin_zone.cache_clear()

    def test_july_evening_to_utc(self):
        # 11:59 PM Eastern in July is EDT (UTC-4) → next day 03:59 UTC
        utc = admin_time.local_form_to_utc_naive("2026-07-01T23:59")
        self.assertEqual(utc, datetime(2026, 7, 2, 3, 59))
        self.assertEqual(admin_time.utc_naive_to_local_form(utc), "2026-07-01T23:59")

    def test_january_evening_to_utc(self):
        # 11:59 PM Eastern in January is EST (UTC-5) → next day 04:59 UTC
        utc = admin_time.local_form_to_utc_naive("2026-01-15T23:59")
        self.assertEqual(utc, datetime(2026, 1, 16, 4, 59))
        self.assertEqual(admin_time.utc_naive_to_local_form(utc), "2026-01-15T23:59")

    def test_expires_unix_matches_end(self):
        from datetime import timezone
        utc = admin_time.local_form_to_utc_naive("2026-07-01T23:59")
        expected = int(datetime(2026, 7, 2, 3, 59, tzinfo=timezone.utc).timestamp())
        self.assertEqual(admin_time.to_unix(utc), expected)

    def test_spring_forward_rejected(self):
        # 2026-03-08 02:30 does not exist in America/New_York
        with self.assertRaises(admin_time.InvalidLocalTime):
            admin_time.local_form_to_utc_naive("2026-03-08T02:30")

    def test_fall_back_ambiguous_rejected(self):
        # 2026-11-01 01:30 is ambiguous in America/New_York
        with self.assertRaises(admin_time.InvalidLocalTime):
            admin_time.local_form_to_utc_naive("2026-11-01T01:30")

    def test_months_rounded_up(self):
        start = datetime(2026, 1, 15)
        self.assertEqual(admin_time.months_rounded_up(start, datetime(2026, 1, 20)), 1)
        self.assertEqual(admin_time.months_rounded_up(start, datetime(2026, 2, 15)), 1)
        self.assertEqual(admin_time.months_rounded_up(start, datetime(2026, 2, 16)), 2)


class CodeValidationTests(unittest.TestCase):
    def test_rejects_space_and_symbol(self):
        self.assertIsNotNone(stripe_pilots.validate_code_format("BAD CODE"))
        self.assertIsNotNone(stripe_pilots.validate_code_format("BAD!"))
        self.assertIsNone(stripe_pilots.validate_code_format("FALL2026"))
        self.assertIsNone(stripe_pilots.validate_code_format("fall-2026"))


class StripeSyncTests(unittest.TestCase):
    def setUp(self):
        stripe_pilots.reset_client()
        self.pilot = {
            "pilotid": 99,
            "name": "Test Pilot",
            "code": "TESTPILOT99",
            "startTime": datetime(2026, 7, 1, 4, 0),  # naive UTC
            "endTime": datetime(2026, 10, 2, 3, 59),
            "stripeCouponId": None,
            "stripePromotionCodeId": None,
        }

    def tearDown(self):
        stripe_pilots.reset_client()
        if "STRIPE_SECRET_KEY" in os.environ:
            # Leave env as tests found it; clear only if we set a fake.
            pass

    def test_not_configured(self):
        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}, clear=False):
            stripe_pilots.reset_client()
            result = stripe_pilots.ensure_pilot_code(self.pilot)
        self.assertFalse(result.configured)
        self.assertFalse(result.ok)
        self.assertIn("not configured", result.message.lower())

    @patch("stripe_pilots.db.set_pilot_stripe_ids")
    @patch("stripe_pilots.client")
    def test_create_coupon_and_promo(self, client_fn, set_ids):
        coupon = MagicMock(id="cou_1", livemode=False)
        promo = MagicMock(id="promo_1", livemode=False)
        api = MagicMock()
        api.v1.coupons.create.return_value = coupon
        api.v1.promotion_codes.create.return_value = promo
        client_fn.return_value = api

        with patch.dict(os.environ, {"STRIPE_SECRET_KEY": "sk_test_fake"}, clear=False):
            stripe_pilots.reset_client()
            with patch("stripe_pilots.configured", return_value=True):
                result = stripe_pilots.ensure_pilot_code(self.pilot)

        self.assertTrue(result.ok)
        self.assertEqual(result.coupon_id, "cou_1")
        self.assertEqual(result.promotion_code_id, "promo_1")
        self.assertFalse(result.livemode)
        coupon_params = api.v1.coupons.create.call_args.kwargs["params"]
        self.assertEqual(coupon_params["percent_off"], 100)
        self.assertEqual(coupon_params["duration"], "repeating")
        promo_params = api.v1.promotion_codes.create.call_args.kwargs["params"]
        self.assertEqual(promo_params["code"], "TESTPILOT99")
        self.assertEqual(promo_params["promotion"], {"type": "coupon", "coupon": "cou_1"})
        self.assertEqual(
            promo_params["expires_at"],
            admin_time.to_unix(self.pilot["endTime"]),
        )
        # Idempotency keys present
        self.assertIn("idempotency_key", api.v1.coupons.create.call_args.kwargs["options"])
        self.assertIn("idempotency_key", api.v1.promotion_codes.create.call_args.kwargs["options"])

    @patch("stripe_pilots.db.set_pilot_stripe_ids")
    @patch("stripe_pilots.client")
    def test_retry_reuses_coupon(self, client_fn, set_ids):
        self.pilot["stripeCouponId"] = "cou_existing"
        promo = MagicMock(id="promo_2", livemode=False)
        existing_coupon = MagicMock(id="cou_existing", livemode=False, redeem_by=9999999999)
        api = MagicMock()
        api.v1.coupons.retrieve.return_value = existing_coupon
        # _coupon_redeem_by uses retrieve; make redeem_by far future
        existing_coupon.to_dict.return_value = {"redeem_by": 9999999999, "livemode": False}
        api.v1.promotion_codes.create.return_value = promo
        client_fn.return_value = api

        with patch("stripe_pilots.configured", return_value=True):
            with patch("stripe_pilots._coupon_redeem_by", return_value=9999999999):
                result = stripe_pilots.ensure_pilot_code(self.pilot)

        self.assertTrue(result.ok)
        api.v1.coupons.create.assert_not_called()
        api.v1.promotion_codes.create.assert_called_once()

    @patch("stripe_pilots.db.set_pilot_stripe_ids")
    @patch("stripe_pilots.client")
    def test_permission_error_mapped(self, client_fn, set_ids):
        import stripe

        api = MagicMock()
        api.v1.coupons.create.side_effect = stripe.AuthenticationError("nope")
        client_fn.return_value = api
        with patch("stripe_pilots.configured", return_value=True):
            result = stripe_pilots.ensure_pilot_code(self.pilot)
        self.assertFalse(result.ok)
        self.assertIn("API key", result.message)
        self.assertNotIn("sk_", result.message)

    @patch("stripe_pilots.db.set_pilot_stripe_ids")
    @patch("stripe_pilots._deactivate_promo")
    @patch("stripe_pilots.ensure_pilot_code")
    def test_edit_end_retires_old(self, ensure, deactivate, set_ids):
        before = dict(self.pilot, stripeCouponId="cou_1", stripePromotionCodeId="promo_1")
        after = dict(
            before,
            endTime=datetime(2026, 12, 1, 5, 0),
            stripeCouponId="cou_1",
            stripePromotionCodeId="promo_1",
        )
        ensure.return_value = stripe_pilots.StripeSyncResult(
            ok=True, configured=True, message="ok", livemode=False
        )
        with patch("stripe_pilots.configured", return_value=True):
            with patch("stripe_pilots._coupon_redeem_by", return_value=0):
                stripe_pilots.sync_after_update(before, after)
        deactivate.assert_called_once_with("promo_1")
        ensure.assert_called_once()

    def test_start_only_no_stripe(self):
        before = dict(self.pilot, stripeCouponId="cou_1", stripePromotionCodeId="promo_1")
        after = dict(before, startTime=datetime(2026, 6, 1, 4, 0))
        with patch("stripe_pilots.configured", return_value=True):
            result = stripe_pilots.sync_after_update(before, after)
        self.assertTrue(result.ok)
        self.assertIn("No Stripe change", result.message)

    @patch("stripe_pilots._deactivate_promo")
    @patch("stripe_pilots.db.get_pilot")
    @patch("stripe_pilots.find_active_promotion_code")
    def test_code_taken(self, find, get_pilot, deactivate):
        # Orphaned active promo (no live pilot) is deactivated and treated as free.
        find.return_value = {"id": "promo_x", "active": True, "metadata": {}}
        get_pilot.return_value = None
        with patch("stripe_pilots.configured", return_value=True):
            self.assertFalse(stripe_pilots.code_taken_in_stripe("ORPHAN"))
        deactivate.assert_called_with("promo_x")

        # Owned by this pilot → free for that pilot.
        find.return_value = {"id": "promo_y", "active": True, "metadata": {"pilotid": "5"}}
        get_pilot.return_value = {"pilotid": 5, "code": "OURS"}
        with patch("stripe_pilots.configured", return_value=True):
            self.assertFalse(stripe_pilots.code_taken_in_stripe("OURS", pilotid=5))

        # Owned by another live pilot → taken.
        with patch("stripe_pilots.configured", return_value=True):
            self.assertTrue(stripe_pilots.code_taken_in_stripe("OURS", pilotid=9))

    @patch("stripe_pilots._deactivate_promo")
    @patch("stripe_pilots.find_active_promotion_code")
    def test_retire_also_clears_by_code(self, find, deactivate):
        find.return_value = {"id": "promo_leftover", "active": True}
        pilot = {
            "pilotid": 1,
            "code": "REUSEME",
            "stripeCouponId": "cou_1",
            "stripePromotionCodeId": "promo_stored",
        }
        with patch("stripe_pilots.configured", return_value=True):
            result = stripe_pilots.retire_pilot_code(pilot)
        self.assertTrue(result.ok)
        self.assertEqual(
            [c.args[0] for c in deactivate.call_args_list],
            ["promo_stored", "promo_leftover"],
        )


class SecretLeakTests(unittest.TestCase):
    def test_repo_has_no_hardcoded_secret_values(self):
        root = os.path.dirname(__file__)
        banned = []
        allow = {"test_stripe_pilots.py"}
        for dirpath, _, files in os.walk(root):
            if "venv" in dirpath.split(os.sep) or "__pycache__" in dirpath:
                continue
            for name in files:
                if name in allow:
                    continue
                if not name.endswith((".py", ".html", ".md", ".txt", ".example", ".js", ".css", ".sql")):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                if "sk_live_" in text or "rk_live_" in text:
                    banned.append(path)
        self.assertEqual(banned, [])


if __name__ == "__main__":
    unittest.main()
