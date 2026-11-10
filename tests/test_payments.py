import json
import random
import unittest

from _bootstrap import *


class TestPaymentFlow(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)
        self.pid = new_policy(self.c, 10)
        resolve(self.c, self.pid, [30, 50, 30, 30, 30])

    def test_withdraw_sends_the_whole_balance_to_the_caller(self):
        set_caller(HOLDER)
        out = json.loads(self.c.withdraw())
        self.assertEqual(out["paid_wei"], str(10 * GEN))
        self.assertEqual(gl.evm.transfers[-1], {"to": HOLDER, "value": 10 * GEN})
        self.assertEqual(pending(self.c, HOLDER), 0)
        self.assertEqual(int(self.c.owed_total), 0)
        assert_invariants(self, self.c)

    def test_no_double_withdrawal(self):
        set_caller(HOLDER)
        self.c.withdraw()
        expect_user_error(self, self.c.withdraw)
        self.assertEqual(len(gl.evm.transfers), 1)

    def test_nothing_to_withdraw(self):
        set_caller(STRANGER)
        expect_user_error(self, self.c.withdraw)

    def test_push_payment_lets_anyone_deliver_the_payout_to_the_holder(self):
        set_caller(KEEPER)
        self.c.push_payment(HOLDER)
        self.assertEqual(gl.evm.transfers[-1]["to"], HOLDER)
        self.assertEqual(gl.evm.transfers[-1]["value"], 10 * GEN)
        self.assertEqual(pending(self.c, KEEPER), 0)

    def test_push_payment_never_pays_the_caller_instead(self):
        set_caller(KEEPER)
        expect_user_error(self, self.c.push_payment, KEEPER)
        self.assertEqual(pending(self.c, HOLDER), 10 * GEN)

    def test_push_payment_validates_the_address(self):
        set_caller(KEEPER)
        for bad in ("", "0x123", "HOLDER", "0x" + "zz" * 20):
            expect_user_error(self, self.c.push_payment, bad)

    def test_balance_is_credited_case_insensitively(self):
        upper = "0x" + HOLDER[2:].upper()
        self.assertEqual(pending(self.c, upper), 10 * GEN)
        set_caller(KEEPER)
        self.c.push_payment(upper)
        self.assertEqual(pending(self.c, HOLDER), 0)

    def test_funds_conservation_after_everything_is_paid_out(self):
        set_caller(HOLDER)
        self.c.withdraw()
        set_caller(LP_A)
        self.c.request_withdrawal_all()
        CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
        self.c.complete_withdrawal()
        self.c.withdraw()
        assert_invariants(self, self.c)
        self.assertEqual(int(self.c.balance), 0)
        paid = sum(int(t["value"]) for t in gl.evm.transfers)
        premium = int(policy(self.c, self.pid)["premium"])
        self.assertEqual(paid, 100 * GEN + premium)


class TestAccountingFuzz(unittest.TestCase):
    PERILS = (("HEAT_WAVE", "49", [30] * 5, [30, 50, 30, 30, 30]),
              ("FROST", "-10", [5] * 5, [5, -12, 5, 5, 5]),
              ("RAIN_EXCESS", "50", [1] * 5, [20] * 5),
              ("STORM_WIND", "90", [20] * 5, [20, 95, 20, 20, 20]))

    def run_seed(self, seed, steps=140):
        rng = random.Random(seed)
        c = make_contract()
        lps = [LP_A, LP_B]
        holders = [HOLDER, HOLDER_2]
        add_liquidity(c, 200, who=LP_A)
        active = []
        for step in range(steps):
            op = rng.choice(["deposit", "buy", "buy", "buy", "resolve", "resolve", "cancel", "void", "request",
                             "complete", "withdraw", "push"])
            try:
                if op == "deposit":
                    add_liquidity(c, rng.randint(1, 50), who=rng.choice(lps))
                elif op == "buy":
                    peril, threshold, _quiet, _hot = rng.choice(self.PERILS)
                    CLOCK.reset()
                    who = rng.choice(holders)
                    lat = f"{rng.randint(-60, 60)}.5"
                    q = request_quote(c, who=who, peril=peril, threshold=threshold, latitude=lat,
                                      start_date=day_offset(rng.randint(4, 8)), end_date=day_offset(rng.randint(9, 12)))
                    gen = rng.randint(1, 12)
                    set_caller(who)
                    try:
                        out = json.loads(call_payable(c, "buy_policy", rng.randint(1, 15) * GEN, q["id"], gen))
                        active.append(out["policy_id"])
                    except PaymentRejected:
                        pass
                elif op == "resolve" and active:
                    pid = active.pop(rng.randrange(len(active)))
                    p = policy(c, pid)
                    spec = next(x for x in self.PERILS if x[0] == p["peril"])
                    values = spec[3] if rng.random() < 0.5 else spec[2]
                    days = p["days"]
                    values = (values * 4)[:days]
                    try:
                        resolve(c, pid, values)
                    except gl.vm.UserError:
                        active.append(pid)
                elif op == "cancel" and active:
                    pid = active[rng.randrange(len(active))]
                    CLOCK.reset()
                    set_caller(policy(c, pid)["holder"])
                    try:
                        c.cancel_policy(pid)
                        active.remove(pid)
                    except gl.vm.UserError:
                        pass
                elif op == "void" and active:
                    pid = active[rng.randrange(len(active))]
                    pass_window(c, pid, ParamCover.RESOLVE_TIMEOUT_SECONDS)
                    set_caller(KEEPER)
                    try:
                        c.void_policy(pid)
                        active.remove(pid)
                    except gl.vm.UserError:
                        pass
                elif op == "request":
                    who = rng.choice(lps)
                    set_caller(who)
                    held = int(c.lp_shares[who.lower()]) if who.lower() in c.lp_shares else 0
                    if held:
                        c.request_withdrawal(max(1, held // rng.randint(1, 4)))
                elif op == "complete":
                    set_caller(rng.choice(lps))
                    CLOCK.advance(ParamCover.WITHDRAW_NOTICE_SECONDS)
                    try:
                        c.complete_withdrawal()
                    except gl.vm.UserError:
                        pass
                elif op == "withdraw":
                    set_caller(rng.choice(holders + lps))
                    try:
                        c.withdraw()
                    except gl.vm.UserError:
                        pass
                elif op == "push":
                    set_caller(KEEPER)
                    try:
                        c.push_payment(rng.choice(holders + lps))
                    except gl.vm.UserError:
                        pass
            finally:
                CLOCK.advance(0)
            assert_invariants(self, c)
        return c

    def test_invariants_hold_over_random_sequences(self):
        for seed in range(12):
            with self.subTest(seed=seed):
                self.run_seed(seed)

    def test_every_status_is_reachable_in_the_fuzz(self):
        seen = set()
        for seed in range(12):
            c = self.run_seed(seed, 100)
            for raw in c.policies.values():
                seen.add(json.loads(raw)["status"])
        self.assertTrue({"PAID", "EXPIRED"} <= seen, seen)


if __name__ == "__main__":
    unittest.main()
