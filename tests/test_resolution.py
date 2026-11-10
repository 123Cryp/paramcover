import datetime
import json
import unittest
from unittest.mock import patch

from _bootstrap import *


class Base(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        add_liquidity(self.c, 100)


class TestDecisionRules(Base):
    def setUp(self):
        super().setUp()
        add_liquidity(self.c, 400, who=LP_B)

    def run_peril(self, peril, threshold, values, **over):
        pid = new_policy(self.c, 10, peril=peril, threshold=threshold, **over)
        return resolve(self.c, pid, values), pid

    def test_heat_triggers_at_or_above_threshold(self):
        p, _ = self.run_peril("HEAT_WAVE", "40.0", [30, 30, 40.0, 30, 30], end_date=day_offset(9))
        self.assertEqual((p["status"], p["decision"], p["observed_t"]), ("PAID", "TRIGGERED", 400))

    def test_heat_just_below_does_not_trigger(self):
        p, _ = self.run_peril("HEAT_WAVE", "40.0", [30, 30, 39.9, 30, 30])
        self.assertEqual((p["status"], p["decision"], p["observed_t"]), ("EXPIRED", "NOT_TRIGGERED", 399))

    def test_rain_excess_sums_the_window(self):
        p, _ = self.run_peril("RAIN_EXCESS", "50", [10.0, 10.0, 10.0, 10.0, 10.0])
        self.assertEqual((p["status"], p["observed_t"]), ("PAID", 500))
        p, _ = self.run_peril("RAIN_EXCESS", "50", [10.0, 10.0, 10.0, 10.0, 9.9])
        self.assertEqual((p["status"], p["observed_t"]), ("EXPIRED", 499))

    def test_dry_spell_triggers_at_or_below(self):
        kw = dict(end_date=day_offset(11))
        p, _ = self.run_peril("RAIN_DEFICIT", "5", [0.5] * 7 + [0.2] * 0 + [0.0] * 0, start_date=day_offset(5), end_date=day_offset(11))
        self.assertEqual((p["status"], p["observed_t"]), ("PAID", 35))
        p, _ = self.run_peril("RAIN_DEFICIT", "5", [0.8] * 7, start_date=day_offset(5), end_date=day_offset(11))
        self.assertEqual((p["status"], p["observed_t"]), ("EXPIRED", 56))

    def test_frost_uses_the_coldest_day(self):
        p, _ = self.run_peril("FROST", "-5.0", [3, 1, -5.0, 2, 4])
        self.assertEqual((p["status"], p["observed_t"]), ("PAID", -50))
        p, _ = self.run_peril("FROST", "-5.0", [3, 1, -4.9, 2, 4])
        self.assertEqual((p["status"], p["observed_t"]), ("EXPIRED", -49))

    def test_storm_wind_uses_the_strongest_gust(self):
        p, _ = self.run_peril("STORM_WIND", "90", [40, 60, 90.0, 50, 45])
        self.assertEqual((p["status"], p["observed_t"]), ("PAID", 900))

    def test_earthquake_threshold(self):
        kw = dict(peril="QUAKE", radius_km=300, latitude="35.68", longitude="139.69", location_label="Tokyo")
        web = Web().add("fdsnws/event/1/count", quake_count_json(2))
        pid = new_policy(self.c, 10, threshold="6.0", web=web, **kw)
        p = resolve(self.c, pid, 6.0)
        self.assertEqual((p["status"], p["observed_t"]), ("PAID", 60))
        web = Web().add("fdsnws/event/1/count", quake_count_json(2))
        pid = new_policy(self.c, 10, threshold="6.0", web=web, **kw)
        p = resolve(self.c, pid, 5.9)
        self.assertEqual((p["status"], p["observed_t"]), ("EXPIRED", 59))

    def test_earthquake_no_events_means_not_triggered(self):
        web = Web().add("fdsnws/event/1/count", quake_count_json(0))
        pid = new_policy(self.c, 10, peril="QUAKE", threshold="5.0", radius_km=100, web=web)
        p = resolve(self.c, pid, None)
        self.assertEqual((p["status"], p["observed_t"]), ("EXPIRED", 0))


class TestSettlementAccounting(Base):
    def test_paid_policy(self):
        pid = new_policy(self.c, 10)
        premium = int(policy(self.c, pid)["premium"])
        capital = int(self.c.pool_capital)
        p = resolve(self.c, pid, [30, 50, 30, 30, 30])
        self.assertEqual(p["status"], "PAID")
        self.assertEqual(p["payout"], str(10 * GEN))
        self.assertEqual(p["resolver"], KEEPER)
        self.assertEqual(pending(self.c, HOLDER), 10 * GEN)
        self.assertEqual(int(self.c.pool_capital), capital - 10 * GEN)
        self.assertEqual(int(self.c.reserved), 0)
        self.assertEqual(int(self.c.payouts_total), 10 * GEN)
        self.assertEqual(int(self.c.paid_count), 1)
        self.assertEqual(int(self.c.pool_capital), 100 * GEN + premium - 10 * GEN)
        self.assertIn("complete", p["note"])
        self.assertIn("api.open-meteo.com", p["evidence_url"])
        assert_invariants(self, self.c)

    def test_expired_policy_keeps_the_premium_and_frees_the_reserve(self):
        pid = new_policy(self.c, 10)
        premium = int(policy(self.c, pid)["premium"])
        p = resolve(self.c, pid, [30, 30, 30, 30, 30])
        self.assertEqual((p["status"], p["payout"]), ("EXPIRED", "0"))
        self.assertEqual(int(self.c.pool_capital), 100 * GEN + premium)
        self.assertEqual(int(self.c.reserved), 0)
        self.assertEqual(int(self.c.peril_reserved["HEAT_WAVE"]), 0)
        self.assertEqual(int(self.c.region_reserved["25:55"]), 0)
        self.assertEqual(pending(self.c, HOLDER), 0)
        self.assertEqual(int(self.c.expired_count), 1)
        assert_invariants(self, self.c)

    def test_a_policy_can_only_be_resolved_once(self):
        pid = new_policy(self.c, 10)
        resolve(self.c, pid, [30, 50, 30, 30, 30])
        expect_user_error(self, self.c.resolve_policy, pid)
        expect_user_error(self, self.c.void_policy, pid)
        set_caller(HOLDER)
        expect_user_error(self, self.c.cancel_policy, pid)

    def test_anyone_may_resolve_and_the_holder_is_always_the_payee(self):
        pid = new_policy(self.c, 10)
        resolve(self.c, pid, [30, 50, 30, 30, 30], caller=STRANGER)
        self.assertEqual(pending(self.c, HOLDER), 10 * GEN)
        self.assertEqual(pending(self.c, STRANGER), 0)

    def test_unknown_policy(self):
        expect_user_error(self, self.c.resolve_policy, "pol_77")
        expect_user_error(self, self.c.get_policy, "pol_77")


class TestTiming(Base):
    @unittest.skipIf(SANDBOX, "sandbox has no data lag")
    def test_cannot_resolve_before_the_window_ends_plus_lag(self):
        pid = new_policy(self.c, 10)
        p = policy(self.c, pid)
        for moment in (BASE, datetime.datetime.fromtimestamp(p["start_epoch"] + 100, UTC),
                       datetime.datetime.fromtimestamp(p["end_epoch"] - 1, UTC),
                       datetime.datetime.fromtimestamp(p["end_epoch"] + ParamCover.DATA_LAG_SECONDS - 1, UTC)):
            CLOCK.set(moment)
            set_caller(KEEPER)
            message = expect_user_error(self, self.c.resolve_policy, pid)
            self.assertIn("not finished", message)
        self.assertEqual(policy(self.c, pid)["status"], "ACTIVE")
        CLOCK.set(datetime.datetime.fromtimestamp(p["end_epoch"] + ParamCover.DATA_LAG_SECONDS, UTC))
        web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(p), [30] * 5))
        with web_patch(web), model_patch(Model()):
            self.c.resolve_policy(pid)
        self.assertEqual(policy(self.c, pid)["status"], "EXPIRED")

    def test_resolution_never_opens_before_the_window_ends(self):
        pid = new_policy(self.c, 10)
        p = policy(self.c, pid)
        CLOCK.set(datetime.datetime.fromtimestamp(p["end_epoch"] - 1, UTC))
        set_caller(KEEPER)
        expect_user_error(self, self.c.resolve_policy, pid)

    def test_recent_windows_use_the_forecast_api_with_past_days(self):
        pid = new_policy(self.c, 10)
        p = policy(self.c, pid)
        web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(p), [30] * 5))
        resolve(self.c, pid, web=web, extra=ParamCover.DATA_LAG_SECONDS)
        url = web.calls[0]
        self.assertIn("api.open-meteo.com/v1/forecast", url)
        self.assertNotIn("archive", url)
        today = CLOCK.now.date()
        start = datetime.date.fromisoformat(p["start_date"])
        self.assertIn(f"past_days={(today - start).days + 1}", url)
        self.assertIn("forecast_days=1", url)
        self.assertIn("timezone=UTC", url)
        self.assertIn("daily=temperature_2m_max", url)
        self.assertIn("latitude=25.2048", url)

    def test_old_windows_use_the_archive_api(self):
        pid = new_policy(self.c, 10)
        p = policy(self.c, pid)
        web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(p), [30] * 5))
        resolve(self.c, pid, web=web, extra=10 * 86400)
        url = web.calls[0]
        self.assertIn("archive-api.open-meteo.com/v1/archive", url)
        self.assertIn(f"start_date={p['start_date']}", url)
        self.assertIn(f"end_date={p['end_date']}", url)

    def test_earthquake_url_covers_the_whole_last_day(self):
        web = Web().add("fdsnws/event/1/count", quake_count_json(0))
        pid = new_policy(self.c, 10, peril="QUAKE", threshold="5.0", radius_km=100, web=web)
        p = policy(self.c, pid)
        qweb = Web().add("earthquake.usgs.gov", quake_feature_json(None))
        resolve(self.c, pid, web=qweb)
        url = qweb.calls[0]
        end_plus_one = (datetime.date.fromisoformat(p["end_date"]) + datetime.timedelta(days=1)).isoformat()
        self.assertIn(f"starttime={p['start_date']}", url)
        self.assertIn(f"endtime={end_plus_one}", url)
        self.assertIn("maxradiuskm=100", url)
        self.assertIn("eventtype=earthquake", url)
        self.assertIn("orderby=magnitude", url)


class TestInconclusive(Base):
    def setUp(self):
        super().setUp()
        self.pid = new_policy(self.c, 10)
        self.p = policy(self.c, self.pid)
        self.before = (dict(self.c.policies), int(self.c.pool_capital), int(self.c.reserved), int(self.c.owed_total))

    def assert_unchanged(self):
        self.assertEqual(policy(self.c, self.pid)["status"], "ACTIVE")
        self.assertEqual((dict(self.c.policies), int(self.c.pool_capital), int(self.c.reserved), int(self.c.owed_total)),
                         self.before)

    def attempt(self, body, model=None):
        web = Web().add("open-meteo.com", body)
        with self.assertRaises(gl.vm.UserError) as ctx:
            resolve(self.c, self.pid, web=web, model=model)
        self.assert_unchanged()
        return str(ctx.exception)

    def test_missing_day(self):
        dates = dates_of(self.p)
        message = self.attempt(weather_json("HEAT_WAVE", dates[:-1], [30] * 4))
        self.assertIn("is missing", message)

    def test_day_without_a_value_yet(self):
        message = self.attempt(weather_json("HEAT_WAVE", dates_of(self.p), [30, 30, None, 30, 30]))
        self.assertIn("no observed value", message)

    def test_implausible_value(self):
        message = self.attempt(weather_json("HEAT_WAVE", dates_of(self.p), [30, 30, 400, 30, 30]))
        self.assertIn("implausible", message)

    def test_error_payload_and_garbage(self):
        self.assertIn("error", self.attempt({"error": True, "reason": "limit"}))
        self.assertIn("JSON object", self.attempt("<html>Too many requests</html>"))
        self.attempt({"daily": "nope"})
        self.attempt({"daily": {"time": ["x"], "temperature_2m_max": []}})

    def test_wrong_variable_in_the_response(self):
        body = weather_json("FROST", dates_of(self.p), [1] * 5)
        self.attempt(body)

    def test_source_unreachable(self):
        with self.assertRaises(gl.vm.UserError) as ctx:
            resolve(self.c, self.pid, web=Web().add("open-meteo.com", RuntimeError("down")))
        self.assertIn("could not be reached", str(ctx.exception))
        self.assert_unchanged()

    def test_review_model_rejecting_the_evidence_blocks_settlement(self):
        message = self.attempt(weather_json("HEAT_WAVE", dates_of(self.p), [30, 50, 30, 30, 30]), model=Model(MODEL_BAD))
        self.assertIn("review model did not accept", message)

    def test_review_model_failure_blocks_settlement(self):
        self.attempt(weather_json("HEAT_WAVE", dates_of(self.p), [30, 50, 30, 30, 30]),
                     model=Model(RuntimeError("model down")))

    def test_review_model_garbage_answer_blocks_settlement(self):
        for answer in ("not json", {"valid": "maybe"}, {"reason": "fine"}, [], "null", {"valid": False}):
            self.attempt(weather_json("HEAT_WAVE", dates_of(self.p), [30, 50, 30, 30, 30]), model=Model(answer))

    def test_review_model_string_true_is_accepted(self):
        web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 30, 30, 30, 30]))
        resolve(self.c, self.pid, web=web, model=Model({"valid": "true", "reason": "ok"}))
        self.assertEqual(policy(self.c, self.pid)["status"], "EXPIRED")

    def test_earthquake_without_a_usable_magnitude(self):
        c = make_contract()
        add_liquidity(c, 100)
        web = Web().add("fdsnws/event/1/count", quake_count_json(1))
        pid = new_policy(c, 10, peril="QUAKE", threshold="5.0", radius_km=100, web=web)
        for body in ({"type": "FeatureCollection", "features": [{"properties": {"mag": None}}]},
                     {"type": "Nope", "features": []}, {"type": "FeatureCollection", "features": "x"}, "oops"):
            with self.assertRaises(gl.vm.UserError):
                resolve(c, pid, web=Web().add("earthquake.usgs.gov", body))
            self.assertEqual(policy(c, pid)["status"], "ACTIVE")

    def test_a_retry_after_a_failure_succeeds(self):
        self.attempt({"error": True})
        web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 50, 30, 30, 30]))
        resolve(self.c, self.pid, web=web)
        self.assertEqual(policy(self.c, self.pid)["status"], "PAID")


class TestReviewPrompt(Base):
    def test_prompt_contains_the_evidence_and_does_not_ask_for_the_payout_decision(self):
        pid = new_policy(self.c, 10)
        model = Model()
        resolve(self.c, pid, [30, 50, 30, 30, 30], model=model)
        prompt = model.prompts[0]
        self.assertIn("Dubai", prompt)
        self.assertIn("HEAT_WAVE", prompt)
        self.assertIn("2026-03-07: 50.0", prompt)
        self.assertIn("Do not decide whether the policy pays", prompt)
        self.assertNotIn("40.0", prompt.split("Evidence extracted")[0])
        self.assertIn('"valid"', prompt)

    def test_model_is_called_once_per_resolution_and_only_with_clean_data(self):
        pid = new_policy(self.c, 10)
        model = Model()
        web = Web().add("open-meteo.com", {"error": True})
        with self.assertRaises(gl.vm.UserError):
            resolve(self.c, pid, web=web, model=model)
        self.assertEqual(model.prompts, [])


class TestValidatorAgreement(Base):
    def setUp(self):
        super().setUp()
        self.pid = new_policy(self.c, 10)
        self.p = policy(self.c, self.pid)
        pass_window(self.c, self.pid, ParamCover.DATA_LAG_SECONDS)
        self.ctx = self.c._resolve_ctx(self.p, CLOCK.now)
        self.web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 50, 30, 30, 30]))

    def honest(self):
        with web_patch(self.web), model_patch(Model()):
            return m._resolve_compute(self.ctx)

    def check(self, leader):
        with web_patch(self.web), model_patch(Model()):
            return m._resolve_validator(gl.vm.Return(leader), self.ctx)

    def test_honest_leader_is_accepted(self):
        leader = self.honest()
        self.assertEqual(leader["decision"], "TRIGGERED")
        self.assertTrue(self.check(leader))

    def test_flipped_decision_is_rejected(self):
        leader = self.honest()
        self.assertFalse(self.check(dict(leader, decision="NOT_TRIGGERED")))

    def test_forged_observation_is_rejected_even_with_a_consistent_decision(self):
        leader = self.honest()
        self.assertFalse(self.check(dict(leader, observed_t=0, decision="NOT_TRIGGERED")))
        self.assertFalse(self.check(dict(leader, observed_t=999)))

    def test_decision_must_match_the_rule_for_the_leaders_own_observation(self):
        forged = dict(decision="TRIGGERED", observed_t=300, days=5, evidence_url=self.ctx["url"], note="x")
        self.assertFalse(m._resolve_agree(forged, dict(forged), self.ctx))

    def test_wrong_evidence_url_is_rejected(self):
        self.assertFalse(self.check(dict(self.honest(), evidence_url="https://evil.example/data")))

    def test_inconclusive_needs_the_validator_to_agree_it_is_inconclusive(self):
        leader = dict(self.honest(), decision="INCONCLUSIVE")
        self.assertFalse(self.check(leader))
        self.web = Web().add("open-meteo.com", {"error": True})
        self.assertTrue(self.check(dict(leader, observed_t=0)))

    def test_malformed_leader_results(self):
        for junk in ("x", None, [], 7):
            self.assertFalse(self.check(junk))
        with web_patch(self.web), model_patch(Model()):
            self.assertFalse(m._resolve_validator(self.honest(), self.ctx))
            self.assertFalse(m._resolve_validator(gl.vm.UserError("boom"), self.ctx))

    def test_validator_with_a_failing_model_disagrees_instead_of_rubber_stamping(self):
        leader = self.honest()
        with web_patch(self.web), model_patch(Model(RuntimeError("down"))):
            self.assertFalse(m._resolve_validator(gl.vm.Return(leader), self.ctx))

    def test_validator_with_different_data_disagrees(self):
        leader = self.honest()
        self.web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 51, 30, 30, 30]))
        self.assertFalse(self.check(leader))

    def test_small_data_revision_within_tolerance_is_accepted(self):
        leader = self.honest()
        self.web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 50.3, 30, 30, 30]))
        self.assertTrue(self.check(leader))

    def test_revision_that_flips_the_decision_is_rejected(self):
        self.web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 40.1, 30, 30, 30]))
        leader = self.honest()
        self.assertEqual(leader["decision"], "TRIGGERED")
        self.web = Web().add("open-meteo.com", weather_json("HEAT_WAVE", dates_of(self.p), [30, 39.8, 30, 30, 30]))
        self.assertFalse(self.check(leader))

    def test_non_integer_observation_is_rejected(self):
        leader = self.honest()
        self.assertFalse(self.check(dict(leader, observed_t="500")))
        self.assertFalse(self.check(dict(leader, observed_t=True)))

    def test_leader_result_is_plain_json_data(self):
        json.dumps(self.honest(), allow_nan=False)

    def test_contradicting_consensus_result_is_refused_by_the_contract(self):
        forged = dict(decision="TRIGGERED", observed_t=300, days=5, evidence_url=self.ctx["url"], note="x")
        with patch.object(gl.vm, "run_nondet_unsafe", return_value=forged):
            set_caller(KEEPER)
            message = expect_user_error(self, self.c.resolve_policy, self.pid)
        self.assertIn("contradicts", message)
        self.assertEqual(policy(self.c, self.pid)["status"], "ACTIVE")

    def test_unknown_decision_is_refused(self):
        forged = dict(decision="MAYBE", observed_t=300, days=5, evidence_url=self.ctx["url"], note="x")
        with patch.object(gl.vm, "run_nondet_unsafe", return_value=forged):
            set_caller(KEEPER)
            expect_user_error(self, self.c.resolve_policy, self.pid)
        with patch.object(gl.vm, "run_nondet_unsafe", return_value="nonsense"):
            expect_user_error(self, self.c.resolve_policy, self.pid)


class TestVoid(Base):
    def test_void_before_timeout_is_refused(self):
        pid = new_policy(self.c, 10)
        pass_window(self.c, pid, ParamCover.RESOLVE_TIMEOUT_SECONDS - 1)
        set_caller(STRANGER)
        expect_user_error(self, self.c.void_policy, pid)

    def test_void_after_timeout_refunds_the_full_premium(self):
        pid = new_policy(self.c, 10)
        premium = int(policy(self.c, pid)["premium"])
        capital = int(self.c.pool_capital)
        pass_window(self.c, pid, ParamCover.RESOLVE_TIMEOUT_SECONDS)
        set_caller(STRANGER)
        out = json.loads(self.c.void_policy(pid))
        self.assertEqual(out["refunded_wei"], str(premium))
        self.assertEqual(pending(self.c, HOLDER), premium)
        self.assertEqual(int(self.c.pool_capital), capital - premium)
        self.assertEqual(int(self.c.reserved), 0)
        self.assertEqual(policy(self.c, pid)["status"], "VOID")
        self.assertEqual(int(self.c.void_count), 1)
        assert_invariants(self, self.c)
        expect_user_error(self, self.c.resolve_policy, pid)

    def test_resolution_still_possible_right_up_to_the_timeout(self):
        pid = new_policy(self.c, 10)
        resolve(self.c, pid, [30, 50, 30, 30, 30], extra=ParamCover.RESOLVE_TIMEOUT_SECONDS - 1)
        self.assertEqual(policy(self.c, pid)["status"], "PAID")


if __name__ == "__main__":
    unittest.main()
