import json
import unittest

from _bootstrap import *


class Base(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)


class TestTermsValidation(Base):
    def bad(self, text, **over):
        before = int(self.c.quote_count)
        message = expect_user_error(self, request_quote, self.c, **over)
        self.assertIn(text, message)
        self.assertEqual(int(self.c.quote_count), before)

    def test_unknown_peril(self):
        self.bad("peril must be one of", peril="TORNADO")

    def test_label_rules(self):
        self.bad("location_label", location_label="")
        self.bad("location_label", location_label="x" * 61)

    def test_coordinates(self):
        self.bad("latitude", latitude="91")
        self.bad("latitude", longitude="181")
        self.bad("latitude", latitude="abc")

    def test_radius_rules(self):
        self.bad("radius_km must be 0", radius_km=50)
        self.bad("between 10 and 500", peril="QUAKE", threshold="6.0", radius_km=0)
        self.bad("between 10 and 500", peril="QUAKE", threshold="6.0", radius_km=501)

    def test_dates(self):
        self.bad("real dates", start_date="2026-02-30")
        self.bad("real dates", end_date="soon")
        self.bad("must not be before", start_date=day_offset(9), end_date=day_offset(5))

    def test_window_length_limits(self):
        self.bad("windows must last", end_date=day_offset(30))
        self.bad("windows must last", peril="RAIN_DEFICIT", threshold="5", end_date=day_offset(6))
        self.bad("windows must last", peril="QUAKE", threshold="6.0", radius_km=100, end_date=day_offset(40))

    @unittest.skipIf(SANDBOX, "sandbox allows backdated windows")
    def test_lead_time_is_a_waiting_period(self):
        self.bad("at least 72 hours", start_date=day_offset(0), end_date=day_offset(3))
        self.bad("at least 72 hours", start_date=day_offset(-3), end_date=day_offset(-1))
        self.bad("at least 72 hours", start_date=day_offset(3), end_date=day_offset(4))
        q = request_quote(self.c, start_date=day_offset(4), end_date=day_offset(5))
        self.assertEqual(q["start_date"], day_offset(4))

    def test_horizon_cap(self):
        self.bad("within 120 days", start_date=day_offset(119), end_date=day_offset(125))

    def test_threshold_bounds_and_format(self):
        self.bad("threshold must be a number", threshold="forty")
        self.bad("between 25.0 and 55.0", threshold="24.9")
        self.bad("between 25.0 and 55.0", threshold="55.1")
        self.bad("threshold must be a number", threshold="40.55")

    def test_negative_thresholds_for_frost(self):
        q = request_quote(self.c, peril="FROST", threshold="-12.5")
        self.assertEqual(q["threshold_t"], -125)
        self.assertEqual(q["cmp"], "LTE")
        self.bad("between -30.0 and 5.0", peril="FROST", threshold="-30.1")

    @unittest.skipUnless(SANDBOX, "sandbox only")
    def test_sandbox_allows_backdated_windows(self):
        q = request_quote(self.c, start_date="2026-01-10", end_date="2026-01-14")
        self.assertEqual(q["start_date"], "2026-01-10")

    def test_dates_before_2000_are_rejected(self):
        self.bad("too far in the past", start_date="1999-12-31", end_date="2000-01-02")


class TestBuying(Base):
    def test_happy_path_state(self):
        q = request_quote(self.c)
        out = buy(self.c, q["id"], 10)
        p = policy(self.c, out["policy_id"])
        self.assertEqual(p["status"], "ACTIVE")
        self.assertEqual(p["holder"], HOLDER)
        self.assertEqual(p["coverage"], str(10 * GEN))
        self.assertEqual(p["premium"], out["premium_wei"])
        self.assertEqual(int(self.c.reserved), 10 * GEN)
        self.assertEqual(int(self.c.pool_capital), 100 * GEN + int(p["premium"]))
        self.assertEqual(int(self.c.peril_reserved["HEAT_WAVE"]), 10 * GEN)
        self.assertEqual(int(self.c.region_reserved["25:55"]), 10 * GEN)
        self.assertEqual(json.loads(self.c.get_holder_policies(HOLDER))["total"], 1)
        assert_invariants(self, self.c)

    def test_policy_copies_the_quote_terms(self):
        q = request_quote(self.c, threshold="42.5", end_date=day_offset(7))
        p = policy(self.c, buy(self.c, q["id"], 3)["policy_id"])
        for key in ("peril", "label", "lat_e4", "lon_e4", "start_date", "end_date", "days", "threshold_t", "risk_bps",
                    "rate_bps", "region", "start_epoch", "end_epoch"):
            self.assertEqual(p[key], q[key], key)

    def test_overpayment_is_credited_back(self):
        q = request_quote(self.c)
        premium = int(json.loads(self.c.preview_premium(q["id"], 10))["premium_wei"])
        out = buy(self.c, q["id"], 10, value=premium + 5 * GEN)
        self.assertEqual(out["refunded_wei"], str(5 * GEN))
        self.assertEqual(pending(self.c, HOLDER), 5 * GEN)
        assert_invariants(self, self.c)

    def test_underpayment_is_bounced_with_a_full_refund(self):
        q = request_quote(self.c)
        premium = int(json.loads(self.c.preview_premium(q["id"], 10))["premium_wei"])
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", premium - 1, q["id"], 10)
        self.assertIn("premium is", str(ctx.exception))
        self.assertEqual(pending(self.c, HOLDER), premium - 1)
        self.assertEqual(int(self.c.policy_count), 0)
        assert_invariants(self, self.c)

    def test_zero_value_raises(self):
        q = request_quote(self.c)
        set_caller(HOLDER)
        expect_user_error(self, call_payable, self.c, "buy_policy", 0, q["id"], 10)

    def test_unknown_quote_is_bounced(self):
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected):
            call_payable(self.c, "buy_policy", GEN, "q_9", 1)
        self.assertEqual(pending(self.c, HOLDER), GEN)

    def test_expired_quote(self):
        q = request_quote(self.c)
        CLOCK.advance(ParamCover.QUOTE_TTL_SECONDS + 1)
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", 3 * GEN, q["id"], 10)
        self.assertIn("expired", str(ctx.exception))

    @unittest.skipIf(SANDBOX, "sandbox has no waiting period")
    def test_lead_time_is_rechecked_at_purchase(self):
        q = request_quote(self.c, start_date=day_offset(4), end_date=day_offset(5))
        CLOCK.advance(ParamCover.QUOTE_TTL_SECONDS - 60)
        CLOCK.set(CLOCK.now.replace(hour=12) + __import__("datetime").timedelta(days=1))
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", 3 * GEN, q["id"], 10)
        self.assertTrue("expired" in str(ctx.exception) or "at least" in str(ctx.exception))

    def test_coverage_bounds(self):
        q = request_quote(self.c)
        set_caller(HOLDER)
        for bad in (0, -1, 1000001):
            with self.assertRaises(PaymentRejected):
                call_payable(self.c, "buy_policy", GEN, q["id"], bad)

    def test_a_quote_can_back_several_policies_but_caps_still_apply(self):
        q = request_quote(self.c)
        for _ in range(3):
            buy(self.c, q["id"], 5)
        self.assertEqual(int(self.c.policy_count), 3)
        self.assertEqual(int(self.c.reserved), 15 * GEN)
        assert_invariants(self, self.c)

    def test_policy_ids_and_holder_index(self):
        q1 = request_quote(self.c, who=HOLDER)
        q2 = request_quote(self.c, who=HOLDER_2)
        a = buy(self.c, q1["id"], 2)["policy_id"]
        b = buy(self.c, q2["id"], 2, who=HOLDER_2)["policy_id"]
        c = buy(self.c, q1["id"], 2)["policy_id"]
        self.assertEqual((a, b, c), ("pol_0", "pol_1", "pol_2"))
        ids = [i["id"] for i in json.loads(self.c.get_holder_policies(HOLDER))["items"]]
        self.assertEqual(ids, ["pol_2", "pol_0"])

    def test_holder_policy_cap(self):
        ParamCover.MAX_POLICIES_PER_HOLDER = 2
        try:
            q = request_quote(self.c)
            buy(self.c, q["id"], 1)
            buy(self.c, q["id"], 1)
            set_caller(HOLDER)
            with self.assertRaises(PaymentRejected):
                call_payable(self.c, "buy_policy", 3 * GEN, q["id"], 1)
        finally:
            ParamCover.MAX_POLICIES_PER_HOLDER = 300


class TestRiskCaps(Base):
    def prepare(self, capital=500):
        add_liquidity(self.c, capital - 100, who=LP_B)

    def fill(self, peril, threshold, lat_start, step=5, chunk=50, stop_binding=None):
        """Buy chunks in distinct regions until the named limit binds."""
        j = 0
        while True:
            lat = f"{lat_start + j * step}.5"
            cap = json.loads(self.c.get_capacity(peril, lat, "10.5"))
            if cap["max_coverage_gen"] == 0 or (stop_binding and cap["binding_limit"] == stop_binding):
                return lat, cap
            q = request_quote(self.c, peril=peril, threshold=threshold, latitude=lat, longitude="10.5")
            buy(self.c, q["id"], min(chunk, cap["max_coverage_gen"]), value=chunk * GEN)
            j += 1

    def test_single_policy_share_cap(self):
        q = request_quote(self.c)
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", 90 * GEN, q["id"], 11)
        self.assertIn("policy_share", str(ctx.exception))
        buy(self.c, q["id"], 10)

    def test_peril_cap(self):
        self.prepare(500)
        lat, cap = self.fill("RAIN_EXCESS", "50", 10, stop_binding="peril_cap")
        self.assertEqual(cap["binding_limit"], "peril_cap")
        used = int(self.c.peril_reserved["RAIN_EXCESS"])
        self.assertLessEqual(used, int(self.c.pool_capital) * 4000 // 10000)
        self.assertGreater(used, 150 * GEN)
        q = request_quote(self.c, peril="RAIN_EXCESS", threshold="50", latitude=lat, longitude="10.5")
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", 20 * GEN, q["id"], cap["max_coverage_gen"] + 1)
        self.assertIn("peril_cap", str(ctx.exception))
        other = json.loads(self.c.get_capacity("FROST", lat, "10.5"))
        self.assertGreater(other["max_coverage_gen"], 0)
        exposure = {e["peril"]: e for e in json.loads(self.c.get_exposure())}
        self.assertEqual(exposure["RAIN_EXCESS"]["reserved"], str(used))
        assert_invariants(self, self.c)

    def test_pool_utilization_cap(self):
        self.prepare(500)
        for peril, threshold, lat in (("RAIN_EXCESS", "50", 10), ("FROST", "-10", 40)):
            self.fill(peril, threshold, lat, stop_binding="pool_utilization")
        fresh = json.loads(self.c.get_capacity("STORM_WIND", "-30.5", "10.5"))
        self.assertEqual(fresh["binding_limit"], "pool_utilization")
        self.assertLessEqual(int(self.c.reserved), int(self.c.pool_capital) * 8000 // 10000)
        q = request_quote(self.c, peril="STORM_WIND", threshold="90", latitude="-30.5", longitude="10.5")
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", 50 * GEN, q["id"], fresh["max_coverage_gen"] + 1)
        self.assertIn("pool_utilization", str(ctx.exception))
        assert_invariants(self, self.c)

    def test_region_cap_binds_across_perils(self):
        self.prepare(500)
        for peril, threshold, gen in (("HEAT_WAVE", "49", 50), ("FROST", "-10", 50), ("STORM_WIND", "90", 25)):
            q = request_quote(self.c, peril=peril, threshold=threshold)
            buy(self.c, q["id"], gen, value=gen * GEN)
        cap = json.loads(self.c.get_capacity("RAIN_EXCESS", LAT, LON))
        self.assertEqual(cap["binding_limit"], "region_cap")
        self.assertEqual(cap["max_coverage_gen"], 0)
        q = request_quote(self.c, peril="RAIN_EXCESS", threshold="50")
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected) as ctx:
            call_payable(self.c, "buy_policy", 20 * GEN, q["id"], 1)
        self.assertIn("region_cap", str(ctx.exception))

    def test_other_regions_are_unaffected(self):
        self.prepare(500)
        q1 = request_quote(self.c, threshold="49")
        buy(self.c, q1["id"], 50, value=50 * GEN)
        cap = json.loads(self.c.get_capacity("HEAT_WAVE", "48.85", "2.35"))
        self.assertEqual(cap["binding_limit"], "policy_share")

    def test_empty_pool_has_no_capacity(self):
        c = make_contract()
        cap = json.loads(c.get_capacity("HEAT_WAVE", LAT, LON))
        self.assertEqual(cap["max_coverage_wei"], "0")


class TestOwnerControls(Base):
    def test_only_the_owner_can_pause(self):
        set_caller(STRANGER)
        expect_user_error(self, self.c.set_sales_paused, True)
        set_caller(OWNER)
        self.c.set_sales_paused(True)
        self.assertTrue(json.loads(self.c.get_config())["sales_paused"])

    def test_paused_sales_bounce_but_everything_else_works(self):
        q = request_quote(self.c)
        pid = buy(self.c, q["id"], 5)["policy_id"]
        set_caller(OWNER)
        self.c.set_sales_paused(True)
        set_caller(HOLDER)
        with self.assertRaises(PaymentRejected):
            call_payable(self.c, "buy_policy", 3 * GEN, q["id"], 5)
        add_liquidity(self.c, 10, who=LP_B)
        p = resolve(self.c, pid, [30, 30, 30, 30, 30])
        self.assertEqual(p["status"], "EXPIRED")
        set_caller(OWNER)
        self.c.set_sales_paused(False)
        self.assertFalse(json.loads(self.c.get_config())["sales_paused"])

    def test_claim_ownership_only_when_unset(self):
        set_caller(STRANGER)
        expect_user_error(self, self.c.claim_ownership)
        self.c.settings["owner"] = ""
        set_caller(STRANGER)
        expect_user_error(self, self.c.set_sales_paused, True)
        self.c.claim_ownership()
        self.c.set_sales_paused(True)
        set_caller(OWNER)
        expect_user_error(self, self.c.claim_ownership)

    def test_owner_cannot_touch_funds(self):
        public = {n for n in dir(ParamCover) if not n.startswith("_")}
        owner_methods = {"claim_ownership", "set_sales_paused"}
        self.assertTrue(owner_methods <= public)
        set_caller(OWNER)
        expect_user_error(self, self.c.withdraw)


class TestCancel(Base):
    def setUp(self):
        super().setUp()
        self.q = request_quote(self.c, start_date=day_offset(6), end_date=day_offset(8))
        self.pid = buy(self.c, self.q["id"], 10)["policy_id"]

    def test_refund_minus_fee_and_release(self):
        premium = int(policy(self.c, self.pid)["premium"])
        capital_before = int(self.c.pool_capital)
        set_caller(HOLDER)
        out = json.loads(self.c.cancel_policy(self.pid))
        fee = premium * ParamCover.CANCEL_FEE_BPS // 10000
        self.assertEqual(out, {"fee_wei": str(fee), "refunded_wei": str(premium - fee)})
        self.assertEqual(pending(self.c, HOLDER), premium - fee)
        self.assertEqual(int(self.c.pool_capital), capital_before - (premium - fee))
        self.assertEqual(int(self.c.reserved), 0)
        self.assertEqual(policy(self.c, self.pid)["status"], "CANCELLED")
        assert_invariants(self, self.c)

    def test_only_holder(self):
        set_caller(STRANGER)
        expect_user_error(self, self.c.cancel_policy, self.pid)

    def test_cannot_cancel_twice_or_resolve_after(self):
        set_caller(HOLDER)
        self.c.cancel_policy(self.pid)
        expect_user_error(self, self.c.cancel_policy, self.pid)
        pass_window(self.c, self.pid, 10)
        expect_user_error(self, self.c.resolve_policy, self.pid)
        expect_user_error(self, self.c.void_policy, self.pid)

    @unittest.skipIf(SANDBOX, "production cutoff")
    def test_cutoff_equals_the_purchase_deadline(self):
        start = int(policy(self.c, self.pid)["start_epoch"])
        import datetime
        CLOCK.set(datetime.datetime.fromtimestamp(start - ParamCover.MIN_LEAD_SECONDS, datetime.timezone.utc))
        set_caller(HOLDER)
        expect_user_error(self, self.c.cancel_policy, self.pid)

    def test_cancel_after_start_is_refused(self):
        pass_window(self.c, self.pid)
        set_caller(HOLDER)
        expect_user_error(self, self.c.cancel_policy, self.pid)


if __name__ == "__main__":
    unittest.main()
