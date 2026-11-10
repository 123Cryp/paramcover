import datetime as _dt
import json
import unittest

from _bootstrap import *


def lp(c, who):
    return json.loads(c.get_lp(who))


def pool(c):
    return json.loads(c.get_pool())


class TestDeposits(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_first_deposit_mints_one_to_one(self):
        out = add_liquidity(self.c, 100)
        self.assertEqual(out["shares_minted"], str(100 * GEN))
        self.assertEqual(pool(self.c)["capital"], str(100 * GEN))
        self.assertEqual(lp(self.c, LP_A)["shares"], str(100 * GEN))
        assert_invariants(self, self.c)

    def test_deposit_below_minimum_is_refunded_not_kept(self):
        set_caller(LP_A)
        with self.assertRaises(PaymentRejected):
            call_payable(self.c, "provide_liquidity", GEN - 1)
        self.assertEqual(pending(self.c, LP_A), GEN - 1)
        self.assertEqual(int(self.c.pool_capital), 0)
        assert_invariants(self, self.c)

    def test_zero_value_deposit_raises(self):
        set_caller(LP_A)
        expect_user_error(self, call_payable, self.c, "provide_liquidity", 0)

    def test_later_depositor_buys_in_at_the_current_share_price(self):
        add_liquidity(self.c, 100)
        pid = new_policy(self.c, 10)
        self.assertEqual(policy(self.c, pid)["status"], "ACTIVE")
        price = int(pool(self.c)["share_price_e18"])
        self.assertGreater(price, GEN)
        out = add_liquidity(self.c, 50, who=LP_B)
        self.assertLess(int(out["shares_minted"]), 50 * GEN)
        value = int(lp(self.c, LP_B)["value_wei"])
        self.assertAlmostEqual(value / GEN, 50, delta=0.0001)
        assert_invariants(self, self.c)

    def test_pool_shares_sum_to_100_percent(self):
        add_liquidity(self.c, 30, who=LP_A)
        add_liquidity(self.c, 70, who=LP_B)
        self.assertEqual(lp(self.c, LP_A)["pool_share_bps"] + lp(self.c, LP_B)["pool_share_bps"], 10000)

    def test_preview_deposit(self):
        add_liquidity(self.c, 100)
        p = json.loads(self.c.preview_deposit(10 * GEN))
        self.assertTrue(p["valid"])
        self.assertEqual(p["shares_minted"], str(10 * GEN))
        self.assertFalse(json.loads(self.c.preview_deposit(5))["valid"])


class TestWithdrawals(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)

    def request(self, shares=None, who=LP_A):
        set_caller(who)
        if shares is None:
            return json.loads(self.c.request_withdrawal_all())
        return json.loads(self.c.request_withdrawal(shares))

    def complete(self, who=LP_A):
        set_caller(who)
        return json.loads(self.c.complete_withdrawal())

    @unittest.skipIf(SANDBOX, "sandbox has no notice period")
    def test_notice_period_is_enforced(self):
        self.request(40 * GEN)
        expect_user_error(self, self.c.complete_withdrawal)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS - 1)
        expect_user_error(self, self.c.complete_withdrawal)
        CLOCK.advance(1)
        out = self.complete()
        self.assertEqual(out["credited_wei"], str(40 * GEN))

    def test_shares_stay_at_risk_until_completion(self):
        self.request(40 * GEN)
        self.assertEqual(lp(self.c, LP_A)["shares"], str(100 * GEN))
        self.assertEqual(lp(self.c, LP_A)["withdraw_request"]["shares"], str(40 * GEN))

    def test_complete_pays_through_the_pending_balance(self):
        self.request(40 * GEN)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        self.complete()
        self.assertEqual(pending(self.c, LP_A), 40 * GEN)
        self.assertEqual(int(self.c.pool_capital), 60 * GEN)
        self.assertEqual(lp(self.c, LP_A)["withdraw_request"], None)
        assert_invariants(self, self.c)

    def test_request_must_not_exceed_holdings(self):
        expect_user_error(self, self.request, 101 * GEN)
        expect_user_error(self, self.request, 0)
        expect_user_error(self, self.request, 1, LP_B)

    def test_cancel_request(self):
        self.request(10 * GEN)
        set_caller(LP_A)
        self.c.cancel_withdrawal()
        expect_user_error(self, self.c.complete_withdrawal)
        expect_user_error(self, self.c.cancel_withdrawal)

    def test_new_request_replaces_old_one_and_restarts_the_clock(self):
        self.request(10 * GEN)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        self.request(20 * GEN)
        if ParamCover.WITHDRAW_NOTICE_SECONDS:
            expect_user_error(self, self.c.complete_withdrawal)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        self.assertEqual(self.complete()["credited_wei"], str(20 * GEN))

    def test_reserved_capital_cannot_leave(self):
        pid = new_policy(self.c, 10)
        self.request()
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        message = expect_user_error(self, self.c.complete_withdrawal)
        self.assertIn("free of active policy reserves", message)
        reserved = int(self.c.reserved)
        free = int(self.c.pool_capital) - reserved
        shares = free * int(self.c.total_shares) // int(self.c.pool_capital)
        self.request(shares)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        out = self.complete()
        self.assertLessEqual(int(out["credited_wei"]), free)
        self.assertGreaterEqual(int(self.c.pool_capital), reserved)
        assert_invariants(self, self.c)

    def test_exit_after_policies_settle(self):
        pid = new_policy(self.c, 10)
        resolve(self.c, pid, [30, 30, 30, 30, 30])
        self.request()
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        out = self.complete()
        self.assertEqual(int(out["credited_wei"]), 100 * GEN + int(policy(self.c, pid)["premium"]))
        self.assertEqual(int(self.c.total_shares), 0)
        self.assertEqual(int(self.c.pool_capital), 0)
        assert_invariants(self, self.c)

    def test_losses_are_shared_pro_rata(self):
        add_liquidity(self.c, 100, who=LP_B)
        pid = new_policy(self.c, 10)
        resolve(self.c, pid, [30, 50, 30, 30, 30])
        a = int(lp(self.c, LP_A)["value_wei"])
        b = int(lp(self.c, LP_B)["value_wei"])
        self.assertAlmostEqual(a, b, delta=1)
        self.assertAlmostEqual((a + b) / GEN, 200 + int(policy(self.c, pid)["premium"]) / GEN - 10, delta=0.0001)

    def test_preview_withdrawal(self):
        pid = new_policy(self.c, 10)
        before_start = int(policy(self.c, pid)["start_epoch"]) - 1
        p = json.loads(self.c.preview_withdrawal(100 * GEN, before_start))
        self.assertFalse(p["payable_now"])
        self.assertEqual(p["held_back_wei"], "0")
        self.assertTrue(json.loads(self.c.preview_withdrawal(10 * GEN, before_start))["payable_now"])
        started = json.loads(self.c.preview_withdrawal(100 * GEN, before_start + 1))
        self.assertEqual(started["held_back_wei"], str(10 * GEN))
        self.assertEqual(json.loads(self.c.preview_withdrawal(100 * GEN))["held_back_wei"], str(10 * GEN))

    def test_exit_holds_back_collateral_of_started_policies(self):
        add_liquidity(self.c, 100, who=LP_B)
        pid = new_policy(self.c, 10)
        premium = int(policy(self.c, pid)["premium"])
        CLOCK.set(_dt.datetime.fromtimestamp(int(policy(self.c, pid)["start_epoch"]), UTC))
        self.request()
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        out = self.complete()
        self.assertEqual(out["held_back_wei"], str(10 * GEN))
        self.assertEqual(int(out["credited_wei"]), (200 * GEN + premium - 10 * GEN) // 2)
        resolve(self.c, pid, [30, 50, 30, 30, 30])
        self.assertGreater(int(lp(self.c, LP_B)["value_wei"]), 0)
        assert_invariants(self, self.c)

    def test_withdrawal_request_expires(self):
        self.request(10 * GEN)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS + ParamCover.WITHDRAW_WINDOW_SECONDS + 1)
        message = expect_user_error(self, self.c.complete_withdrawal)
        self.assertIn("expired", message)
        self.request(10 * GEN)
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        self.assertEqual(self.complete()["credited_wei"], str(10 * GEN))

    def test_depositor_cannot_withdraw_without_request(self):
        set_caller(LP_B)
        expect_user_error(self, self.c.complete_withdrawal)


if __name__ == "__main__":
    unittest.main()
