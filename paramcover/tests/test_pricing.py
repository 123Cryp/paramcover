import json
import unittest

from _bootstrap import *


class TestParsingHelpers(unittest.TestCase):
    def test_threshold_parsing(self):
        self.assertEqual(m._parse_tenths("50"), 500)
        self.assertEqual(m._parse_tenths("7.5"), 75)
        self.assertEqual(m._parse_tenths("-5.5"), -55)
        self.assertEqual(m._parse_tenths("0"), 0)
        for bad in ("", "abc", "1.25", "1e3", "12345", "--1", "1.", ".5", None):
            self.assertIsNone(m._parse_tenths(bad), bad)

    def test_coordinates(self):
        self.assertEqual(m._parse_coord_e4("25.2048", 90), 252048)
        self.assertEqual(m._parse_coord_e4("-0.5", 90), -5000)
        self.assertEqual(m._parse_coord_e4("90", 90), 900000)
        self.assertIsNone(m._parse_coord_e4("90.0001", 90))
        self.assertIsNone(m._parse_coord_e4("181", 180))
        self.assertIsNone(m._parse_coord_e4("1.23456", 90))
        self.assertEqual(m._fmt_e4(-5000), "-0.5000")
        self.assertEqual(m._fmt_e4(252048), "25.2048")

    def test_rounding_to_tenths(self):
        self.assertEqual(m._to_tenths(12.3), 123)
        self.assertEqual(m._to_tenths(0.05), 1)
        self.assertEqual(m._to_tenths(-5.55), -55)
        self.assertEqual(m._to_tenths(7), 70)
        for bad in (True, None, "3", float("nan"), 1e12):
            self.assertIsNone(m._to_tenths(bad))

    def test_region_uses_floor_degrees(self):
        self.assertEqual(m._region_of(252048, 552708), "25:55")
        self.assertEqual(m._region_of(-5000, -5000), "-1:-1")

    def test_shift_year_handles_leap_day(self):
        import datetime
        self.assertEqual(m._shift_year(datetime.date(2028, 2, 29), 2019), datetime.date(2019, 2, 28))
        self.assertEqual(m._shift_year(datetime.date(2026, 3, 6), 2019), datetime.date(2019, 3, 6))


class TestQuote(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)

    def test_history_counts_are_exact(self):
        q = request_quote(self.c)
        self.assertEqual((q["hits"], q["samples"]), (50, 310))
        self.assertEqual(q["risk_bps"], 51 * 10000 // 312)
        self.assertEqual(q["rate_bps"], q["risk_bps"] * 13000 // 10000)

    def test_rare_event_pays_the_floor_rate(self):
        q = request_quote(self.c, history=history_json(spike_every_year=False))
        self.assertEqual(q["hits"], 0)
        self.assertEqual(q["rate_bps"], 50)

    def test_too_likely_event_is_refused(self):
        web = Web().add("archive-api", history_json(base_value=500))
        if SANDBOX:
            q = request_quote(self.c, web=web)
            self.assertEqual(q["risk_bps"], 311 * 10000 // 312)
            return
        message = expect_user_error(self, request_quote, self.c, web=web)
        self.assertIn("too likely", message)
        self.assertEqual(int(self.c.quote_count), 0)

    def test_quake_uses_catalog_count(self):
        web = Web().add("fdsnws/event/1/count", quake_count_json(3))
        q = request_quote(self.c, web=web, peril="QUAKE", threshold="6.0", radius_km=300, location_label="Tokyo",
                          latitude="35.68", longitude="139.69")
        self.assertEqual((q["hits"], q["samples"]), (3 * 5, 20 * 365))
        self.assertIn("minmagnitude=6.0", web.calls[0])
        self.assertIn("starttime=2006-01-01", web.calls[0])
        self.assertIn("endtime=2026-01-01", web.calls[0])

    def test_history_window_is_ten_complete_years(self):
        web = Web().add("archive-api", history_json())
        request_quote(self.c, web=web)
        self.assertIn("start_date=2016-01-01", web.calls[0])
        self.assertIn("end_date=2025-12-31", web.calls[0])

    def test_unusable_history_is_not_recorded(self):
        for body in ("not json", {"error": True, "reason": "x"}, {"daily": {"time": [], "temperature_2m_max": []}},
                     {"daily": {"time": ["2020-01-01"], "temperature_2m_max": []}}):
            web = Web().add("archive-api", body)
            message = expect_user_error(self, request_quote, self.c, web=web)
            self.assertIn("could not be priced", message)
        self.assertEqual(int(self.c.quote_count), 0)

    def test_unreachable_history_source(self):
        web = Web().add("archive-api", RuntimeError("down"))
        expect_user_error(self, request_quote, self.c, web=web)
        self.assertEqual(int(self.c.quote_count), 0)

    def test_validator_rejects_a_forged_leader_result(self):
        ctx = self.c._quote_ctx(self.c._validate_terms("HEAT_WAVE", "Dubai", LAT, LON, day_offset(5), day_offset(9),
                                                       "40.0", 0, CLOCK.now), CLOCK.now)
        web = Web().add("archive-api", history_json())
        with web_patch(web):
            honest = m._quote_compute(ctx)
            self.assertTrue(m._quote_validator(gl.vm.Return(dict(honest)), ctx))
            forged = dict(honest, hits=0)
            self.assertFalse(m._quote_validator(gl.vm.Return(forged), ctx))
            self.assertFalse(m._quote_validator(gl.vm.Return(dict(honest, samples=honest["samples"] - 1)), ctx))
            self.assertFalse(m._quote_validator(honest, ctx))
            self.assertFalse(m._quote_validator(gl.vm.Return("junk"), ctx))

    def test_quote_ids_are_sequential_and_stored(self):
        q0 = request_quote(self.c)
        q1 = request_quote(self.c, threshold="45")
        self.assertEqual((q0["id"], q1["id"]), ("q_0", "q_1"))
        self.assertEqual(json.loads(self.c.get_quote("q_1"))["threshold_t"], 450)
        expect_user_error(self, self.c.get_quote, "q_9")

    def test_harder_threshold_is_cheaper(self):
        easy = request_quote(self.c, threshold="35.0")
        hard = request_quote(self.c, threshold="49.0")
        self.assertGreater(easy["rate_bps"], hard["rate_bps"])


class TestPremium(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)
        self.q = request_quote(self.c)

    def preview(self, gen):
        return json.loads(self.c.preview_premium(self.q["id"], gen))

    def test_premium_matches_formula(self):
        p = self.preview(5)
        self.assertEqual(int(p["premium_wei"]), 5 * GEN * 2124 // 10000)
        self.assertEqual(p["surcharge_bps"], 10000)

    def test_premium_is_monotonic_in_coverage(self):
        values = [int(self.preview(g)["premium_wei"]) for g in range(1, 11)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))

    def test_utilization_surcharge_kicks_in_past_the_kink(self):
        add_liquidity(self.c, 400, who=LP_B)
        low = self.preview(10)
        self.assertEqual(low["surcharge_bps"], 10000)
        plans = (("RAIN_EXCESS", "50", 0), ("FROST", "-10", 100), ("STORM_WIND", "90", 300))
        for i, (peril, threshold, base) in enumerate(plans):
            for j in range(2):
                q = request_quote(self.c, peril=peril, threshold=threshold, latitude=f"{10 + 20 * i + j}.5",
                                  history=history_json(peril, base_value=base, spike_every_year=False))
                buy(self.c, q["id"], 50, value=50 * GEN)
        high = self.preview(10)
        self.assertGreater(high["surcharge_bps"], 10000)
        self.assertGreater(int(high["premium_wei"]), int(low["premium_wei"]))

    def test_premium_is_never_below_the_floor_or_above_the_cap(self):
        q = request_quote(self.c, history=history_json(spike_every_year=False))
        p = json.loads(self.c.preview_premium(q["id"], 1))
        self.assertEqual(int(p["premium_wei"]), GEN * 50 // 10000)
        hot = ParamCover.MAX_PREMIUM_BPS
        q2 = dict(self.q, rate_bps=20000)
        premium, _ = self.c._premium(20000, 10 * GEN)
        self.assertEqual(premium, 10 * GEN * hot // 10000)

    def test_preview_flags_coverage_over_capacity(self):
        p = self.preview(11)
        self.assertFalse(p["fits"])
        self.assertEqual(p["binding_limit"], "policy_share")
        self.assertTrue(self.preview(10)["fits"])

    def test_preview_bounds(self):
        expect_user_error(self, self.c.preview_premium, self.q["id"], 0)
        expect_user_error(self, self.c.preview_premium, self.q["id"], 1000001)
        expect_user_error(self, self.c.preview_premium, "q_5", 1)


if __name__ == "__main__":
    unittest.main()
