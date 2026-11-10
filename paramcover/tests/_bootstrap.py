"""
Shared test bootstrap: wires up the offline genlayer SDK stub, loads the
contract once (contract.py by default, or the file named by the PC_CONTRACT
environment variable, which is how the sandbox build is tested), replaces the
contract's single clock with a controllable one, and provides builders for
liquidity, quotes, policies and mocked weather / earthquake sources.
"""
import datetime as _dt
import importlib.util
import json as _json
import os
import sys
from unittest.mock import patch as _patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

ROOT = os.path.dirname(_THIS_DIR)
CONTRACT_PATH = os.environ.get("PC_CONTRACT") or os.path.join(ROOT, "contract.py")
_spec = importlib.util.spec_from_file_location("paramcover_contract", CONTRACT_PATH)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ParamCover = m.ParamCover
gl = m.gl
Address = m.Address
u256 = m.u256
GEN = 10 ** 18
SANDBOX = ParamCover.MODE == "sandbox"

UTC = _dt.timezone.utc
BASE = _dt.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
DAY = 86400


class Clock:
    def __init__(self):
        self.now = BASE

    def reset(self):
        self.now = BASE

    def set(self, moment):
        self.now = moment

    def advance(self, seconds):
        self.now = self.now + _dt.timedelta(seconds=seconds)


CLOCK = Clock()
ParamCover._now_utc = staticmethod(lambda: CLOCK.now)

OWNER = "0x" + "aa" * 20
LP_A = "0x" + "a1" * 20
LP_B = "0x" + "a2" * 20
HOLDER = "0x" + "b1" * 20
HOLDER_2 = "0x" + "b2" * 20
KEEPER = "0x" + "c1" * 20
STRANGER = "0x" + "d1" * 20

LAT = "25.2048"
LON = "55.2708"
LABEL = "Dubai"

_INFLOW = [0]


def set_caller(address_text):
    gl.message.sender_address = Address(address_text)


def reset_transfers():
    gl.evm.transfers.clear()


def make_contract():
    CLOCK.reset()
    reset_transfers()
    _INFLOW[0] = 0
    set_caller(OWNER)
    return ParamCover()


class PaymentRejected(gl.vm.UserError):
    pass


def _snapshot(contract):
    return (dict(contract.policies), int(contract.pool_capital), int(contract.total_shares), int(contract.reserved),
            dict(contract.lp_shares), int(contract.policy_count))


def call_payable(contract, method_name, value, *args, **kwargs):
    """Invoke a payable method as if `value` wei were attached.

    A returned rejection must have credited the exact value back to the
    sender and changed nothing else; it is then raised as PaymentRejected."""
    gl.message.value = u256(value)
    contract.balance = contract.balance + u256(value)
    _INFLOW[0] += int(value)
    sender = str(gl.message.sender_address).lower()
    before = _snapshot(contract)
    owed_before = int(contract.pending_withdrawals[sender]) if sender in contract.pending_withdrawals else 0
    try:
        result = getattr(contract, method_name)(*args, **kwargs)
    except Exception:
        contract.balance = contract.balance - u256(value)
        _INFLOW[0] -= int(value)
        raise
    finally:
        gl.message.value = u256(0)
    if isinstance(result, str) and result.startswith('{"refunded_wei"') and '"rejected"' in result:
        data = _json.loads(result)
        owed_after = int(contract.pending_withdrawals[sender]) if sender in contract.pending_withdrawals else 0
        assert data["refunded_wei"] == str(int(value)), data
        assert owed_after - owed_before == int(value), (owed_before, owed_after, value)
        assert _snapshot(contract) == before, "a rejected payable call changed state"
        raise PaymentRejected(data["rejected"])
    return result


def sync_balance(contract):
    out = sum(int(t["value"]) for t in gl.evm.transfers)
    contract.balance = u256(_INFLOW[0] - out)
    return int(contract.balance)


def assert_invariants(testcase, contract):
    sync_balance(contract)
    acct = _json.loads(contract.get_accounting())
    testcase.assertTrue(acct["balance_covers_liabilities"], acct)
    testcase.assertEqual(int(acct["contract_balance"]), int(acct["liabilities"]), acct)
    testcase.assertTrue(acct["reserved_within_capital"], acct)
    reserved_by_policy = 0
    for raw in contract.policies.values():
        policy = _json.loads(raw)
        if policy["status"] == "ACTIVE":
            reserved_by_policy += int(policy["coverage"])
    testcase.assertEqual(reserved_by_policy, int(contract.reserved))
    testcase.assertEqual(sum(int(v) for v in contract.peril_reserved.values()), int(contract.reserved))
    testcase.assertEqual(sum(int(v) for v in contract.region_reserved.values()), int(contract.reserved))
    testcase.assertEqual(sum(int(v) for v in contract.lp_shares.values()), int(contract.total_shares))
    testcase.assertEqual(sum(int(v) for v in contract.pending_withdrawals.values()), int(contract.owed_total))


def iso_date(moment):
    return moment.date().isoformat()


def day_offset(days, start=None):
    return ((start or BASE) + _dt.timedelta(days=days)).date().isoformat()


def history_json(peril_key="HEAT_WAVE", base_value=300, spike_value=450, spike_month=3, spike_day=8,
                 year_from=2016, year_to=2025, spike_every_year=True):
    """Ten years of daily data: a constant base value with one spike day each
    year, so the number of historical hits is known exactly."""
    var = m.PERILS[peril_key]["var"]
    times = []
    values = []
    day = _dt.date(year_from, 1, 1)
    last = _dt.date(year_to, 12, 31)
    while day <= last:
        times.append(day.isoformat())
        spike = day.month == spike_month and day.day == spike_day and spike_every_year
        values.append((spike_value if spike else base_value) / 10)
        day += _dt.timedelta(days=1)
    return {"latitude": 25.2, "longitude": 55.3, "daily": {"time": times, var: values}}


_HISTORY_CACHE = {}


def _cached_history(peril_key):
    """HEAT_WAVE: one 45 C spike per year (50 hits at 40 C). Others: no hits, so quotes sit at the floor rate."""
    if peril_key == "HEAT_WAVE" and peril_key not in _HISTORY_CACHE:
        _HISTORY_CACHE[peril_key] = _json.dumps(history_json("HEAT_WAVE"))
    if peril_key not in _HISTORY_CACHE:
        base = {"HEAT_WAVE": 300, "FROST": 100, "RAIN_EXCESS": 0, "RAIN_DEFICIT": 100, "STORM_WIND": 300}.get(peril_key, 0)
        key = peril_key if peril_key in m.PERILS and m.PERILS[peril_key]["src"] == "weather" else "HEAT_WAVE"
        _HISTORY_CACHE[peril_key] = _json.dumps(history_json(key, base_value=base, spike_value=base, spike_every_year=False))
    return _HISTORY_CACHE[peril_key]


def weather_json(peril_key, dates, values):
    var = m.PERILS[peril_key]["var"]
    return {"latitude": 25.2, "longitude": 55.3, "daily_units": {var: "x"},
            "daily": {"time": list(dates), var: list(values)}}


def quake_count_json(count):
    return {"count": count, "maxAllowed": 20000}


def quake_feature_json(mag=None, place="10 km N of Somewhere"):
    if mag is None:
        return {"type": "FeatureCollection", "metadata": {"status": 200, "count": 0}, "features": []}
    return {"type": "FeatureCollection", "metadata": {"status": 200, "count": 1},
            "features": [{"type": "Feature", "properties": {"mag": mag, "place": place}}]}


class Web:
    """Route-based replacement for gl.nondet.web.render: the first route
    whose substring is contained in the URL answers."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, fragment, body):
        self.routes.append((fragment, body))
        return self

    def render(self, url, mode="text"):
        self.calls.append(url)
        for fragment, body in self.routes:
            if fragment in url:
                if isinstance(body, Exception):
                    raise body
                return body if isinstance(body, str) else _json.dumps(body)
        raise RuntimeError("404 " + url)


MODEL_OK = {"valid": True, "reason": "complete and plausible"}
MODEL_BAD = {"valid": False, "reason": "looks like an error page"}


class Model:
    def __init__(self, answer=None):
        self.answer = MODEL_OK if answer is None else answer
        self.prompts = []

    def __call__(self, prompt, response_format="text"):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def web_patch(web):
    return _patch.object(gl.nondet.web, "render", side_effect=web.render)


def model_patch(model):
    return _patch.object(gl.nondet, "exec_prompt", side_effect=model)


def add_liquidity(c, gen=100, who=LP_A):
    set_caller(who)
    return _json.loads(call_payable(c, "provide_liquidity", gen * GEN))


def default_terms(**over):
    terms = {
        "peril": "HEAT_WAVE", "location_label": LABEL, "latitude": LAT, "longitude": LON,
        "start_date": day_offset(5), "end_date": day_offset(9), "threshold": "40.0", "radius_km": 0,
    }
    terms.update(over)
    return terms


def request_quote(c, who=HOLDER, history=None, web=None, **over):
    terms = default_terms(**over)
    set_caller(who)
    if web is None:
        body = history if history is not None else _cached_history(terms["peril"])
        web = Web().add("archive-api.open-meteo.com", body)
    with web_patch(web):
        raw = c.request_quote(terms["peril"], terms["location_label"], terms["latitude"], terms["longitude"],
                              terms["start_date"], terms["end_date"], terms["threshold"], terms["radius_km"])
    return _json.loads(raw)


def buy(c, quote_id, coverage_gen=10, value=None, who=HOLDER):
    set_caller(who)
    if value is None:
        preview = _json.loads(c.preview_premium(quote_id, coverage_gen))
        value = int(preview["premium_wei"])
    return _json.loads(call_payable(c, "buy_policy", value, quote_id, coverage_gen))


def new_policy(c, coverage_gen=10, who=HOLDER, reset_clock=True, **over):
    if reset_clock:
        CLOCK.reset()
    quote = request_quote(c, who=who, **over)
    return buy(c, quote["id"], coverage_gen, who=who)["policy_id"]


def policy(c, policy_id):
    return _json.loads(c.get_policy(policy_id))


def pass_window(c, policy_id, extra=0):
    p = policy(c, policy_id)
    CLOCK.set(_dt.datetime.fromtimestamp(int(p["end_epoch"]) + extra, UTC))


def dates_of(p):
    start = _dt.date.fromisoformat(p["start_date"])
    return [(start + _dt.timedelta(days=i)).isoformat() for i in range(p["days"])]


def resolve(c, policy_id, values=None, web=None, model=None, caller=KEEPER, extra=None):
    """Move the clock to the moment resolution opens and resolve.
    `values` are daily numbers in real units (for example 41.5)."""
    p = policy(c, policy_id)
    lag = int(c.DATA_LAG_SECONDS) if extra is None else extra
    pass_window(c, policy_id, lag)
    if web is None:
        if m.PERILS[p["peril"]]["src"] == "quake":
            web = Web().add("earthquake.usgs.gov", quake_feature_json(values))
        else:
            web = Web().add("open-meteo.com", weather_json(p["peril"], dates_of(p), values))
    model = model or Model()
    set_caller(caller)
    with web_patch(web), model_patch(model):
        c.resolve_policy(policy_id)
    return policy(c, policy_id)


def pending(c, address):
    return int(c.get_pending_withdrawal(address))


def expect_user_error(testcase, fn, *args, **kwargs):
    with testcase.assertRaises(gl.vm.UserError) as ctx:
        fn(*args, **kwargs)
    return str(ctx.exception)
