import json
import unittest

from _bootstrap import *


class TestViews(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)

    def test_config_exposes_every_rule(self):
        cfg = json.loads(self.c.get_config())
        self.assertEqual(cfg["mode"], ParamCover.MODE)
        self.assertEqual(cfg["max_policy_share_bps"], 1000)
        self.assertEqual(cfg["max_utilization_bps"], 8000)
        self.assertEqual(cfg["loading_bps"], 13000)
        self.assertEqual(cfg["owner"], OWNER)
        self.assertFalse(cfg["sales_paused"])
        for key in ("min_lead_seconds", "data_lag_seconds", "resolve_timeout_seconds", "withdraw_notice_seconds",
                    "quote_ttl_seconds", "history_years", "cancel_fee_bps", "max_horizon_days", "max_risk_bps"):
            self.assertIn(key, cfg)

    def test_perils_table(self):
        perils = json.loads(self.c.get_perils())
        self.assertEqual([p["peril"] for p in perils],
                         ["RAIN_EXCESS", "RAIN_DEFICIT", "HEAT_WAVE", "FROST", "STORM_WIND", "QUAKE"])
        quake = perils[-1]
        self.assertTrue(quake["uses_radius"])
        self.assertEqual(quake["source"], "earthquake.usgs.gov")
        self.assertEqual({p["cmp"] for p in perils}, {"GTE", "LTE"})
        for p in perils:
            self.assertLess(p["threshold_min_t"], p["threshold_max_t"])
            self.assertLessEqual(p["min_days"], p["max_days"])

    def test_pool_and_stats_follow_activity(self):
        self.assertEqual(json.loads(self.c.get_pool())["share_price_e18"], str(GEN))
        a = new_policy(self.c, 10)
        b = new_policy(self.c, 5, who=HOLDER_2)
        stats = json.loads(self.c.get_stats())
        self.assertEqual((stats["policies"], stats["active"], stats["paid"]), (2, 2, 0))
        resolve(self.c, a, [30, 50, 30, 30, 30])
        stats = json.loads(self.c.get_stats())
        self.assertEqual((stats["active"], stats["paid"]), (1, 1))
        pool = json.loads(self.c.get_pool())
        self.assertEqual(int(pool["reserved"]), 5 * GEN)
        self.assertEqual(pool["utilization_bps"], int(pool["reserved"]) * 10000 // int(pool["capital"]))
        self.assertEqual(int(pool["free_capital"]), int(pool["capital"]) - int(pool["reserved"]))
        self.assertEqual(int(pool["payouts_total"]), 10 * GEN)

    def test_list_policies_is_newest_first_and_paged(self):
        ids = [new_policy(self.c, 1) for _ in range(5)]
        page = json.loads(self.c.list_policies(0, 2))
        self.assertEqual(page["total"], 5)
        self.assertEqual([i["id"] for i in page["items"]], [ids[4], ids[3]])
        page = json.loads(self.c.list_policies(2, 10))
        self.assertEqual([i["id"] for i in page["items"]], [ids[2], ids[1], ids[0]])
        self.assertEqual(json.loads(self.c.list_policies(9, 10))["items"], [])
        self.assertEqual(len(json.loads(self.c.list_policies(0, 999))["items"]), 5)
        self.assertEqual(len(json.loads(self.c.list_policies(0, 0))["items"]), 1)

    def test_summary_has_what_the_ui_needs(self):
        pid = new_policy(self.c, 4)
        item = json.loads(self.c.list_policies(0, 1))["items"][0]
        for key in ("id", "holder", "peril", "label", "start_date", "end_date", "threshold_t", "unit", "coverage",
                    "premium", "status", "observed_t", "payout"):
            self.assertIn(key, item)

    def test_get_policy_has_the_full_record(self):
        pid = new_policy(self.c, 4)
        p = policy(self.c, pid)
        for key in ("quote_id", "lat_e4", "lon_e4", "risk_bps", "rate_bps", "cmp", "decision", "note", "evidence_url",
                    "resolved_epoch", "resolver", "created_epoch", "start_epoch", "end_epoch"):
            self.assertIn(key, p)

    def test_holder_policies_for_unknown_address(self):
        self.assertEqual(json.loads(self.c.get_holder_policies(STRANGER)), {"items": [], "total": 0})

    def test_lp_view_for_a_stranger(self):
        lp = json.loads(self.c.get_lp(STRANGER))
        self.assertEqual((lp["shares"], lp["value_wei"], lp["withdraw_request"]), ("0", "0", None))

    def test_exposure_view(self):
        new_policy(self.c, 10)
        exposure = {e["peril"]: e for e in json.loads(self.c.get_exposure())}
        self.assertEqual(exposure["HEAT_WAVE"]["reserved"], str(10 * GEN))
        self.assertEqual(exposure["FROST"]["reserved"], "0")
        self.assertEqual(int(exposure["FROST"]["cap"]), int(self.c.pool_capital) * 4000 // 10000)

    def test_capacity_view_rejects_bad_input(self):
        expect_user_error(self, self.c.get_capacity, "NOPE", "1", "1")
        expect_user_error(self, self.c.get_capacity, "FROST", "x", "1")

    def test_accounting_view(self):
        new_policy(self.c, 10)
        sync_balance(self.c)
        acct = json.loads(self.c.get_accounting())
        self.assertTrue(acct["balance_covers_liabilities"])
        self.assertEqual(acct["contract_balance"], acct["liabilities"])

    def test_read_views_do_not_change_state(self):
        pid = new_policy(self.c, 3)
        before = (dict(self.c.policies), int(self.c.pool_capital), int(self.c.quote_count))
        for call in (self.c.get_config, self.c.get_perils, self.c.get_pool, self.c.get_stats, self.c.get_exposure,
                     self.c.get_accounting, lambda: self.c.list_policies(0, 5), lambda: self.c.get_policy(pid),
                     lambda: self.c.get_holder_policies(HOLDER), lambda: self.c.get_lp(LP_A),
                     lambda: self.c.preview_premium("q_0", 3), lambda: self.c.get_quote("q_0"),
                     lambda: self.c.preview_deposit(GEN), lambda: self.c.preview_withdrawal(GEN)):
            call()
        self.assertEqual(before, (dict(self.c.policies), int(self.c.pool_capital), int(self.c.quote_count)))


if __name__ == "__main__":
    unittest.main()


class TestSummaryFields(unittest.TestCase):
    def test_list_items_carry_timestamps_for_the_activity_feed(self):
        c = make_contract()
        add_liquidity(c, 100)
        pid = new_policy(c, 10)
        item = json.loads(c.list_policies(0, 5))["items"][0]
        full = policy(c, pid)
        for key in ("cmp", "radius_km", "created_epoch", "resolved_epoch"):
            self.assertEqual(item[key], full[key])
