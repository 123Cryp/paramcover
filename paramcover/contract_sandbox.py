# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json
import re
import datetime

BPS = 10000
WEI_PER_GEN = 10 ** 18
EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
ONE_SECOND = datetime.timedelta(seconds=1)
ONE_DAY = datetime.timedelta(days=1)

ST_ACTIVE = "ACTIVE"
ST_PAID = "PAID"
ST_EXPIRED = "EXPIRED"
ST_VOID = "VOID"
ST_CANCELLED = "CANCELLED"

DEC_TRIGGERED = "TRIGGERED"
DEC_NOT_TRIGGERED = "NOT_TRIGGERED"
DEC_INCONCLUSIVE = "INCONCLUSIVE"

HOST_ARCHIVE = "archive-api.open-meteo.com"
HOST_RECENT = "api.open-meteo.com"
HOST_QUAKE = "earthquake.usgs.gov"

ARCHIVE_AGE_DAYS = 8
MAX_PAST_DAYS = 92
HISTORY_YEARS = 10
QUAKE_HISTORY_YEARS = 20
SAMPLE_OFFSET_DAYS = 15
MIN_SAMPLES = 200
MIN_START_DATE = datetime.date(2000, 1, 1)

PERIL_ORDER = ("RAIN_EXCESS", "RAIN_DEFICIT", "HEAT_WAVE", "FROST", "STORM_WIND", "QUAKE")

PERILS = {
    "RAIN_EXCESS": {
        "title": "Heavy rain", "src": "weather", "var": "precipitation_sum", "agg": "SUM", "cmp": "GTE",
        "unit": "mm", "lo": 200, "hi": 5000, "min_days": 1, "max_days": 14, "bound_lo": 0, "bound_hi": 20000, "tol": 20,
        "rule": "total rainfall over the whole window is at least",
    },
    "RAIN_DEFICIT": {
        "title": "Dry spell", "src": "weather", "var": "precipitation_sum", "agg": "SUM", "cmp": "LTE",
        "unit": "mm", "lo": 0, "hi": 1000, "min_days": 7, "max_days": 14, "bound_lo": 0, "bound_hi": 20000, "tol": 20,
        "rule": "total rainfall over the whole window is at most",
    },
    "HEAT_WAVE": {
        "title": "Extreme heat", "src": "weather", "var": "temperature_2m_max", "agg": "MAX", "cmp": "GTE",
        "unit": "C", "lo": 250, "hi": 550, "min_days": 1, "max_days": 14, "bound_lo": -900, "bound_hi": 700, "tol": 5,
        "rule": "the hottest daily maximum in the window is at least",
    },
    "FROST": {
        "title": "Hard frost", "src": "weather", "var": "temperature_2m_min", "agg": "MIN", "cmp": "LTE",
        "unit": "C", "lo": -300, "hi": 50, "min_days": 1, "max_days": 14, "bound_lo": -900, "bound_hi": 700, "tol": 5,
        "rule": "the coldest daily minimum in the window is at most",
    },
    "STORM_WIND": {
        "title": "Storm winds", "src": "weather", "var": "wind_gusts_10m_max", "agg": "MAX", "cmp": "GTE",
        "unit": "km/h", "lo": 400, "hi": 2000, "min_days": 1, "max_days": 14, "bound_lo": 0, "bound_hi": 5000, "tol": 30,
        "rule": "the strongest daily wind gust in the window is at least",
    },
    "QUAKE": {
        "title": "Earthquake", "src": "quake", "var": "magnitude", "agg": "MAX", "cmp": "GTE",
        "unit": "M", "lo": 40, "hi": 90, "min_days": 1, "max_days": 30, "bound_lo": 0, "bound_hi": 100, "tol": 0,
        "rule": "the largest earthquake within the radius during the window has magnitude at least",
    },
}

DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
THRESHOLD_RE = re.compile(r"(-?)([0-9]{1,4})(?:\.([0-9]))?")
COORD_RE = re.compile(r"(-?)([0-9]{1,3})(?:\.([0-9]{1,4}))?")
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def _epoch_of(moment) -> int:
    return int((moment - EPOCH) // ONE_SECOND)


def _day_start_epoch(day) -> int:
    return _epoch_of(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc))


def _parse_date(text):
    value = str(text or "").strip()
    if not DATE_RE.fullmatch(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_tenths(text):
    match = THRESHOLD_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    sign, whole, frac = match.groups()
    value = int(whole) * 10 + int(frac or "0")
    return -value if sign else value


def _parse_coord_e4(text, limit):
    match = COORD_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    sign, whole, frac = match.groups()
    value = int(whole) * 10000 + int((frac or "").ljust(4, "0") or "0")
    if value > limit * 10000:
        return None
    return -value if sign else value


def _fmt_e4(value: int) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(int(value))
    return f"{sign}{magnitude // 10000}.{str(magnitude % 10000).rjust(4, '0')}"


def _fmt_tenths(value: int) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(int(value))
    return f"{sign}{magnitude // 10}.{magnitude % 10}"


def _to_tenths(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw != raw or abs(raw) > 1e9:
        return None
    return int((raw * 10 + 0.5) // 1)


def _compare(rule: str, observed: int, threshold: int) -> bool:
    if rule == "GTE":
        return observed >= threshold
    return observed <= threshold


def _aggregate(kind: str, values) -> int:
    if kind == "SUM":
        return sum(values)
    if kind == "MAX":
        return max(values)
    return min(values)


def _region_of(lat_e4: int, lon_e4: int) -> str:
    return f"{lat_e4 // 10000}:{lon_e4 // 10000}"


def _safe_json_obj(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _load_json_lenient(content):
    text = str(content or "")[:3000000].strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    lo = text.find("{")
    hi = text.rfind("}")
    if lo >= 0 and hi > lo:
        try:
            return json.loads(text[lo:hi + 1])
        except Exception:
            return None
    return None


def _shift_year(day, year):
    try:
        return day.replace(year=year)
    except ValueError:
        return datetime.date(year, 2, 28)


def _date_range(first, count):
    return [(first + datetime.timedelta(days=i)).isoformat() for i in range(count)]


def _weather_url(host_kind, peril_key, lat_e4, lon_e4, first, last, past_days):
    peril = PERILS[peril_key]
    lat = _fmt_e4(lat_e4)
    lon = _fmt_e4(lon_e4)
    if host_kind == "archive":
        return (f"https://{HOST_ARCHIVE}/v1/archive?latitude={lat}&longitude={lon}&start_date={first}"
                f"&end_date={last}&daily={peril['var']}&timezone=UTC")
    return (f"https://{HOST_RECENT}/v1/forecast?latitude={lat}&longitude={lon}&daily={peril['var']}"
            f"&past_days={past_days}&forecast_days=1&timezone=UTC")


def _quake_url(lat_e4, lon_e4, radius_km, first, end_exclusive):
    return (f"https://{HOST_QUAKE}/fdsnws/event/1/query?format=geojson&starttime={first}&endtime={end_exclusive}"
            f"&latitude={_fmt_e4(lat_e4)}&longitude={_fmt_e4(lon_e4)}&maxradiuskm={int(radius_km)}"
            f"&eventtype=earthquake&orderby=magnitude&limit=1")


def _quake_count_url(lat_e4, lon_e4, radius_km, first, end_exclusive, min_tenths):
    return (f"https://{HOST_QUAKE}/fdsnws/event/1/count?format=geojson&starttime={first}&endtime={end_exclusive}"
            f"&latitude={_fmt_e4(lat_e4)}&longitude={_fmt_e4(lon_e4)}&maxradiuskm={int(radius_km)}"
            f"&minmagnitude={_fmt_tenths(min_tenths)}&eventtype=earthquake")


def _fetch_text(url):
    try:
        return gl.nondet.web.render(url, mode="text")
    except Exception:
        return None


def _parse_weather(text, peril, dates):
    data = _load_json_lenient(text)
    if not isinstance(data, dict):
        return {"ok": False, "error": "the data source did not return a JSON object"}
    if data.get("error"):
        return {"ok": False, "error": "the data source reported an error"}
    daily = data.get("daily")
    if not isinstance(daily, dict):
        return {"ok": False, "error": "the response has no daily block"}
    times = daily.get("time")
    values = daily.get(peril["var"])
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        return {"ok": False, "error": "the daily series is missing or malformed"}
    index = {}
    for stamp, raw in zip(times, values):
        index[str(stamp)] = raw
    series = []
    for day in dates:
        if day not in index:
            return {"ok": False, "error": f"day {day} is missing from the response"}
        tenths = _to_tenths(index[day])
        if tenths is None:
            return {"ok": False, "error": f"day {day} has no observed value yet"}
        if tenths < peril["bound_lo"] or tenths > peril["bound_hi"]:
            return {"ok": False, "error": f"day {day} has an implausible value"}
        series.append(tenths)
    table = "\n".join(f"{day}: {_fmt_tenths(value)}" for day, value in zip(dates, series))
    return {"ok": True, "observed_t": _aggregate(peril["agg"], series), "table": table}


def _parse_quake(text):
    data = _load_json_lenient(text)
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        return {"ok": False, "error": "the data source did not return an event list"}
    features = data.get("features")
    if not isinstance(features, list):
        return {"ok": False, "error": "the event list is malformed"}
    if not features:
        return {"ok": True, "observed_t": 0, "table": "no earthquakes were recorded inside the radius during the window"}
    first = features[0] if isinstance(features[0], dict) else {}
    props = first.get("properties") if isinstance(first.get("properties"), dict) else {}
    tenths = _to_tenths(props.get("mag"))
    if tenths is None or tenths < 0 or tenths > 100:
        return {"ok": False, "error": "the largest event has no usable magnitude"}
    place = str(props.get("place", ""))[:120]
    return {"ok": True, "observed_t": tenths, "table": f"largest event: magnitude {_fmt_tenths(tenths)}, {place}"}


def _review_prompt(ctx, parsed):
    peril = PERILS[ctx["peril"]]
    return (
        "You are an independent reviewer for a parametric insurance contract. Decide ONLY whether the evidence below "
        "is valid, complete and plausible. Do not decide whether the policy pays.\n"
        f"Peril: {peril['title']} ({ctx['peril']}). Place label: {ctx['label']}. "
        f"Coordinates: {_fmt_e4(ctx['lat_e4'])}, {_fmt_e4(ctx['lon_e4'])}. Radius km: {ctx['radius_km']}.\n"
        f"Window (UTC dates): {ctx['dates'][0]} to {ctx['dates'][-1]}. Unit: {peril['unit']}.\n"
        f"Data source host: {ctx['host']}.\n"
        "Evidence extracted by deterministic code from the source response:\n"
        f"{parsed['table']}\n"
        + (
            "Answer valid=true if the evidence reads like a normal earthquake catalog result for the radius and window "
            "(a single largest event, or no events at all, are both normal) and nothing suggests an error page or a unit "
            "mix-up. "
            if peril["src"] == "quake" else
            "Answer valid=true only if every day of the window is present exactly once, the numbers are plausible for the "
            "unit and the place, and nothing suggests an error page, a unit mix-up or a different location. "
        ) +
        'Reply with JSON only: {"valid": true or false, "reason": "one short sentence"}'
    )


def _llm_review(ctx, parsed):
    try:
        raw = gl.nondet.exec_prompt(_review_prompt(ctx, parsed), response_format="json")
    except Exception:
        return {"valid": False, "reason": "the review model was unavailable"}
    obj = _safe_json_obj(raw)
    flag = obj.get("valid")
    valid = flag is True or str(flag).strip().lower() == "true"
    return {"valid": valid, "reason": str(obj.get("reason", ""))[:300]}


def _inconclusive(ctx, reason):
    return {"decision": DEC_INCONCLUSIVE, "observed_t": 0, "days": len(ctx["dates"]),
            "evidence_url": ctx["url"], "note": str(reason)[:300]}


def _resolve_compute(ctx):
    peril = PERILS[ctx["peril"]]
    text = _fetch_text(ctx["url"])
    if text is None:
        return _inconclusive(ctx, "the data source could not be reached")
    parsed = _parse_quake(text) if peril["src"] == "quake" else _parse_weather(text, peril, ctx["dates"])
    if not parsed["ok"]:
        return _inconclusive(ctx, parsed["error"])
    review = _llm_review(ctx, parsed)
    if not review["valid"]:
        return _inconclusive(ctx, "the review model did not accept the evidence: " + review["reason"])
    triggered = _compare(peril["cmp"], parsed["observed_t"], ctx["threshold_t"])
    return {"decision": DEC_TRIGGERED if triggered else DEC_NOT_TRIGGERED, "observed_t": parsed["observed_t"],
            "days": len(ctx["dates"]), "evidence_url": ctx["url"], "note": review["reason"]}


def _resolve_agree(leader, mine, ctx) -> bool:
    if not isinstance(leader, dict) or not isinstance(mine, dict):
        return False
    if leader.get("decision") != mine.get("decision"):
        return False
    if leader.get("evidence_url") != ctx["url"] or not isinstance(leader.get("note"), str):
        return False
    if leader["decision"] == DEC_INCONCLUSIVE:
        return True
    observed = leader.get("observed_t")
    if isinstance(observed, bool) or not isinstance(observed, int) or leader.get("days") != mine.get("days"):
        return False
    if abs(observed - int(mine.get("observed_t", 0))) > PERILS[ctx["peril"]]["tol"]:
        return False
    rule = PERILS[ctx["peril"]]["cmp"]
    expected = DEC_TRIGGERED if _compare(rule, int(leader["observed_t"]), ctx["threshold_t"]) else DEC_NOT_TRIGGERED
    return leader["decision"] == expected


def _resolve_validator(leader_result, ctx) -> bool:
    if not isinstance(leader_result, gl.vm.Return):
        return False
    return _resolve_agree(leader_result.calldata, _resolve_compute(ctx), ctx)


def _history_counts(index, peril, start, days, threshold_t, year_from, year_to):
    hits = 0
    samples = 0
    for year in range(year_from, year_to + 1):
        anchor = _shift_year(start, year)
        for offset in range(-SAMPLE_OFFSET_DAYS, SAMPLE_OFFSET_DAYS + 1):
            first = anchor + datetime.timedelta(days=offset)
            window = []
            for step in range(days):
                value = index.get((first + datetime.timedelta(days=step)).isoformat())
                if value is None:
                    window = None
                    break
                window.append(value)
            if window is None:
                continue
            samples += 1
            if _compare(peril["cmp"], _aggregate(peril["agg"], window), threshold_t):
                hits += 1
    return hits, samples


def _parse_history(text, peril, ctx):
    data = _load_json_lenient(text)
    if not isinstance(data, dict) or data.get("error"):
        return {"ok": False, "error": "the history source did not return usable data"}
    daily = data.get("daily")
    if not isinstance(daily, dict):
        return {"ok": False, "error": "the history response has no daily block"}
    times = daily.get("time")
    values = daily.get(peril["var"])
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        return {"ok": False, "error": "the history series is malformed"}
    index = {}
    for stamp, raw in zip(times, values):
        tenths = _to_tenths(raw)
        if tenths is not None and peril["bound_lo"] <= tenths <= peril["bound_hi"]:
            index[str(stamp)] = tenths
    start = datetime.date.fromisoformat(ctx["start"])
    hits, samples = _history_counts(index, peril, start, ctx["days"], ctx["threshold_t"], ctx["year_from"], ctx["year_to"])
    if samples < MIN_SAMPLES:
        return {"ok": False, "error": f"only {samples} usable historical windows were found, at least {MIN_SAMPLES} are needed"}
    return {"ok": True, "hits": hits, "samples": samples}


def _parse_quake_history(text, ctx):
    data = _load_json_lenient(text)
    if not isinstance(data, dict):
        return {"ok": False, "error": "the earthquake catalog did not return a count"}
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > 1000000:
        return {"ok": False, "error": "the earthquake catalog count is missing or invalid"}
    return {"ok": True, "hits": count * ctx["days"], "samples": QUAKE_HISTORY_YEARS * 365}


def _quote_compute(ctx):
    text = _fetch_text(ctx["url"])
    if text is None:
        return {"ok": False, "error": "the history source could not be reached", "hits": 0, "samples": 0}
    peril = PERILS[ctx["peril"]]
    if peril["src"] == "quake":
        result = _parse_quake_history(text, ctx)
    else:
        result = _parse_history(text, peril, ctx)
    if not result["ok"]:
        return {"ok": False, "error": result["error"], "hits": 0, "samples": 0}
    return {"ok": True, "error": "", "hits": result["hits"], "samples": result["samples"]}


def _quote_validator(leader_result, ctx) -> bool:
    if not isinstance(leader_result, gl.vm.Return):
        return False
    leader = leader_result.calldata
    mine = _quote_compute(ctx)
    if not isinstance(leader, dict):
        return False
    if leader.get("ok") != mine["ok"]:
        return False
    if not mine["ok"]:
        return True
    return leader.get("hits") == mine["hits"] and leader.get("samples") == mine["samples"]


@gl.evm.contract_interface
class _Payee:
    class View:
        pass

    class Write:
        pass


class ParamCover(gl.Contract):

    MODE = "sandbox"
    ALLOW_BACKDATED = True
    MIN_LEAD_SECONDS = 0
    DATA_LAG_SECONDS = 0
    RESOLVE_TIMEOUT_SECONDS = 900
    WITHDRAW_NOTICE_SECONDS = 0
    WITHDRAW_WINDOW_SECONDS = 172800
    QUOTE_TTL_SECONDS = 21600

    MIN_DEPOSIT = 10 ** 18
    MIN_COVERAGE_GEN = 1
    MAX_COVERAGE_GEN = 1000000
    MAX_UTILIZATION_BPS = 8000
    MAX_POLICY_SHARE_BPS = 1000
    MAX_PERIL_SHARE_BPS = 4000
    MAX_REGION_SHARE_BPS = 2500
    MAX_RISK_BPS = 10000
    LOADING_BPS = 13000
    MIN_PREMIUM_BPS = 50
    MAX_PREMIUM_BPS = 9500
    SURCHARGE_KINK_BPS = 5000
    CANCEL_FEE_BPS = 1000
    MAX_HORIZON_DAYS = 120
    MAX_POLICIES_PER_HOLDER = 300
    MAX_LABEL_LENGTH = 60

    settings: TreeMap[str, str]
    policies: TreeMap[str, str]
    quotes: TreeMap[str, str]
    holder_index: TreeMap[str, str]
    lp_shares: TreeMap[str, u256]
    withdraw_requests: TreeMap[str, str]
    pending_withdrawals: TreeMap[str, u256]
    peril_reserved: TreeMap[str, u256]
    region_reserved: TreeMap[str, u256]
    pool_capital: u256
    total_shares: u256
    reserved: u256
    owed_total: u256
    premiums_total: u256
    payouts_total: u256
    policy_count: u256
    quote_count: u256
    paid_count: u256
    expired_count: u256
    void_count: u256
    cancelled_count: u256

    def __init__(self):
        self.pool_capital = u256(0)
        self.total_shares = u256(0)
        self.reserved = u256(0)
        self.owed_total = u256(0)
        self.premiums_total = u256(0)
        self.payouts_total = u256(0)
        self.policy_count = u256(0)
        self.quote_count = u256(0)
        self.paid_count = u256(0)
        self.expired_count = u256(0)
        self.void_count = u256(0)
        self.cancelled_count = u256(0)
        self.settings["sales_paused"] = "0"
        try:
            self.settings["owner"] = str(gl.message.sender_address).lower()
        except Exception:
            self.settings["owner"] = ""

    @staticmethod
    def _now_utc() -> datetime.datetime:
        try:
            moment = datetime.datetime.fromisoformat(str(gl.message_raw["datetime"]).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=datetime.timezone.utc)
            return moment.astimezone(datetime.timezone.utc)
        except Exception:
            return datetime.datetime.now(datetime.timezone.utc)

    @staticmethod
    def _key(address) -> str:
        return str(address).lower()

    def _bounce(self, error) -> str:
        sent = int(gl.message.value)
        if sent <= 0:
            raise error
        self._credit(gl.message.sender_address, sent)
        return json.dumps({"rejected": str(error)[:300], "refunded_wei": str(sent)}, sort_keys=True)

    def _credit(self, address, amount: int) -> None:
        if amount <= 0:
            return
        key = self._key(address)
        current = int(self.pending_withdrawals[key]) if key in self.pending_withdrawals else 0
        self.pending_withdrawals[key] = u256(current + amount)
        self.owed_total = u256(int(self.owed_total) + amount)

    def _get_int(self, mapping, key) -> int:
        return int(mapping[key]) if key in mapping else 0

    def _load_policy(self, policy_id: str) -> dict:
        key = str(policy_id)
        if key not in self.policies:
            raise gl.vm.UserError("No policy found with this id")
        return json.loads(self.policies[key])

    def _save_policy(self, policy: dict) -> None:
        self.policies[policy["id"]] = json.dumps(policy, sort_keys=True)

    def _load_quote(self, quote_id: str) -> dict:
        key = str(quote_id)
        if key not in self.quotes:
            raise gl.vm.UserError("No quote found with this id")
        return json.loads(self.quotes[key])

    def _require_owner(self) -> None:
        owner = self.settings["owner"] if "owner" in self.settings else ""
        if not owner:
            raise gl.vm.UserError("The owner is not set; call claim_ownership first")
        if self._key(gl.message.sender_address) != owner:
            raise gl.vm.UserError("Only the owner may do this")

    def _validate_terms(self, peril, label, latitude, longitude, start_date, end_date, threshold, radius_km, now):
        if peril not in PERILS:
            raise gl.vm.UserError(f"peril must be one of {list(PERIL_ORDER)}")
        spec = PERILS[peril]
        label_text = str(label or "").strip()
        if not label_text or len(label_text) > self.MAX_LABEL_LENGTH:
            raise gl.vm.UserError(f"location_label must be 1 to {self.MAX_LABEL_LENGTH} characters")
        lat_e4 = _parse_coord_e4(latitude, 90)
        lon_e4 = _parse_coord_e4(longitude, 180)
        if lat_e4 is None or lon_e4 is None:
            raise gl.vm.UserError("latitude must be within -90..90 and longitude within -180..180, up to 4 decimals")
        radius = int(radius_km)
        if spec["src"] == "quake":
            if radius < 10 or radius > 500:
                raise gl.vm.UserError("radius_km must be between 10 and 500 for QUAKE")
        elif radius != 0:
            raise gl.vm.UserError("radius_km must be 0 for weather perils")
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        if start is None or end is None:
            raise gl.vm.UserError("start_date and end_date must be real dates formatted YYYY-MM-DD")
        if end < start:
            raise gl.vm.UserError("end_date must not be before start_date")
        days = (end - start).days + 1
        if days < spec["min_days"] or days > spec["max_days"]:
            raise gl.vm.UserError(f"{peril} windows must last {spec['min_days']} to {spec['max_days']} days")
        if start < MIN_START_DATE:
            raise gl.vm.UserError("start_date is too far in the past")
        start_epoch = _day_start_epoch(start)
        end_epoch = _day_start_epoch(end) + 86400
        now_epoch = _epoch_of(now)
        if not self.ALLOW_BACKDATED and start_epoch < now_epoch + self.MIN_LEAD_SECONDS:
            raise gl.vm.UserError(
                f"Cover must be bought at least {self.MIN_LEAD_SECONDS // 3600} hours before the window starts"
            )
        if end_epoch > now_epoch + self.MAX_HORIZON_DAYS * 86400:
            raise gl.vm.UserError(f"The window must end within {self.MAX_HORIZON_DAYS} days from now")
        threshold_t = _parse_tenths(threshold)
        if threshold_t is None:
            raise gl.vm.UserError("threshold must be a number with at most one decimal, for example 50 or 7.5")
        if threshold_t < spec["lo"] or threshold_t > spec["hi"]:
            raise gl.vm.UserError(
                f"threshold for {peril} must be between {_fmt_tenths(spec['lo'])} and {_fmt_tenths(spec['hi'])} {spec['unit']}"
            )
        return {
            "peril": peril, "label": label_text, "lat_e4": lat_e4, "lon_e4": lon_e4, "radius_km": radius,
            "start_date": start.isoformat(), "end_date": end.isoformat(), "days": days,
            "start_epoch": start_epoch, "end_epoch": end_epoch, "threshold_t": threshold_t,
            "cmp": spec["cmp"], "unit": spec["unit"], "region": _region_of(lat_e4, lon_e4),
        }

    def _quote_ctx(self, terms, now):
        spec = PERILS[terms["peril"]]
        year_to = now.year - 1
        if spec["src"] == "quake":
            year_from = year_to - QUAKE_HISTORY_YEARS + 1
            url = _quake_count_url(terms["lat_e4"], terms["lon_e4"], terms["radius_km"],
                                   f"{year_from}-01-01", f"{year_to + 1}-01-01", terms["threshold_t"])
        else:
            year_from = year_to - HISTORY_YEARS + 1
            url = _weather_url("archive", terms["peril"], terms["lat_e4"], terms["lon_e4"],
                               f"{year_from}-01-01", f"{year_to}-12-31", 0)
        return {"peril": terms["peril"], "url": url, "start": terms["start_date"], "days": terms["days"],
                "threshold_t": terms["threshold_t"], "year_from": year_from, "year_to": year_to}

    def _resolve_ctx(self, policy, now):
        spec = PERILS[policy["peril"]]
        start = datetime.date.fromisoformat(policy["start_date"])
        end = datetime.date.fromisoformat(policy["end_date"])
        dates = _date_range(start, policy["days"])
        if spec["src"] == "quake":
            url = _quake_url(policy["lat_e4"], policy["lon_e4"], policy["radius_km"], dates[0],
                             (end + ONE_DAY).isoformat())
            host = HOST_QUAKE
        elif (now.date() - end).days >= ARCHIVE_AGE_DAYS:
            url = _weather_url("archive", policy["peril"], policy["lat_e4"], policy["lon_e4"], dates[0], dates[-1], 0)
            host = HOST_ARCHIVE
        else:
            past_days = max(1, min(MAX_PAST_DAYS, (now.date() - start).days + 1))
            url = _weather_url("recent", policy["peril"], policy["lat_e4"], policy["lon_e4"], dates[0], dates[-1],
                               past_days)
            host = HOST_RECENT
        return {"peril": policy["peril"], "url": url, "host": host, "dates": dates,
                "threshold_t": policy["threshold_t"], "label": policy["label"], "lat_e4": policy["lat_e4"],
                "lon_e4": policy["lon_e4"], "radius_km": policy["radius_km"]}

    def _capacity(self, peril: str, region: str):
        capital = int(self.pool_capital)
        limits = {
            "policy_share": capital * self.MAX_POLICY_SHARE_BPS // BPS,
            "pool_utilization": capital * self.MAX_UTILIZATION_BPS // BPS - int(self.reserved),
            "peril_cap": capital * self.MAX_PERIL_SHARE_BPS // BPS - self._get_int(self.peril_reserved, peril),
            "region_cap": capital * self.MAX_REGION_SHARE_BPS // BPS - self._get_int(self.region_reserved, region),
        }
        binding = min(limits, key=lambda name: limits[name])
        return max(0, limits[binding]), binding, limits

    def _premium(self, rate_bps: int, coverage: int):
        capital = int(self.pool_capital)
        after = int(self.reserved) + coverage
        utilization = after * BPS // capital if capital > 0 else BPS
        surcharge = BPS + max(0, utilization - self.SURCHARGE_KINK_BPS) * 2
        raw = -(-coverage * rate_bps * surcharge // (BPS * BPS))
        floor = -(-coverage * self.MIN_PREMIUM_BPS // BPS)
        ceiling = coverage * self.MAX_PREMIUM_BPS // BPS
        premium = min(max(raw, floor), ceiling)
        return premium, {"rate_bps": rate_bps, "utilization_after_bps": utilization, "surcharge_bps": surcharge}

    def _active_list(self) -> list:
        raw = self.settings["active"] if "active" in self.settings else ""
        return json.loads(raw) if raw else []

    def _started_exposure(self, now_epoch: int) -> int:
        return sum(int(item[2]) for item in self._active_list() if now_epoch <= 0 or int(item[1]) <= now_epoch)

    def _release(self, policy: dict) -> None:
        self.settings["active"] = json.dumps([item for item in self._active_list() if item[0] != policy["id"]])
        coverage = int(policy["coverage"])
        self.reserved = u256(int(self.reserved) - coverage)
        peril = policy["peril"]
        self.peril_reserved[peril] = u256(self._get_int(self.peril_reserved, peril) - coverage)
        region = policy["region"]
        self.region_reserved[region] = u256(self._get_int(self.region_reserved, region) - coverage)

    def _pay_out(self, address_text: str) -> str:
        key = self._key(address_text)
        amount = self._get_int(self.pending_withdrawals, key)
        if amount <= 0:
            raise gl.vm.UserError("There is no withdrawable GEN balance for this address")
        self.pending_withdrawals[key] = u256(0)
        self.owed_total = u256(int(self.owed_total) - amount)
        _Payee(Address(address_text)).emit_transfer(value=u256(amount))
        return json.dumps({"paid_wei": str(amount), "to": address_text}, sort_keys=True)

    @gl.public.write
    def claim_ownership(self) -> str:
        if "owner" in self.settings and self.settings["owner"]:
            raise gl.vm.UserError("The owner is already set")
        self.settings["owner"] = self._key(gl.message.sender_address)
        return "owner set"

    @gl.public.write
    def set_sales_paused(self, paused: bool) -> str:
        self._require_owner()
        self.settings["sales_paused"] = "1" if paused else "0"
        return "sales paused" if paused else "sales open"

    @gl.public.write.payable
    def provide_liquidity(self) -> str:
        try:
            return self._provide()
        except gl.vm.UserError as error:
            return self._bounce(error)

    def _provide(self) -> str:
        amount = int(gl.message.value)
        if amount < self.MIN_DEPOSIT:
            raise gl.vm.UserError(f"Deposit at least {self.MIN_DEPOSIT} wei (1 GEN)")
        capital = int(self.pool_capital)
        shares_total = int(self.total_shares)
        if shares_total == 0:
            if capital != 0:
                raise gl.vm.UserError("The pool is in an inconsistent state")
            minted = amount
        else:
            if capital == 0:
                raise gl.vm.UserError("The pool has no capital left; new deposits are closed")
            minted = amount * shares_total // capital
        if minted <= 0:
            raise gl.vm.UserError("Deposit is too small to mint any shares")
        key = self._key(gl.message.sender_address)
        self.lp_shares[key] = u256(self._get_int(self.lp_shares, key) + minted)
        self.total_shares = u256(shares_total + minted)
        self.pool_capital = u256(capital + amount)
        return json.dumps({"deposited_wei": str(amount), "shares_minted": str(minted)}, sort_keys=True)

    def _set_withdraw_request(self, shares: int) -> str:
        key = self._key(gl.message.sender_address)
        held = self._get_int(self.lp_shares, key)
        if shares <= 0 or shares > held:
            raise gl.vm.UserError("shares must be between 1 and your share balance")
        ready = _epoch_of(self._now_utc()) + self.WITHDRAW_NOTICE_SECONDS
        self.withdraw_requests[key] = json.dumps({"shares": str(shares), "ready_at": ready}, sort_keys=True)
        return json.dumps({"shares": str(shares), "ready_at": ready}, sort_keys=True)

    @gl.public.write
    def request_withdrawal(self, shares: int) -> str:
        return self._set_withdraw_request(int(shares))

    @gl.public.write
    def request_withdrawal_all(self) -> str:
        return self._set_withdraw_request(self._get_int(self.lp_shares, self._key(gl.message.sender_address)))

    @gl.public.write
    def cancel_withdrawal(self) -> str:
        key = self._key(gl.message.sender_address)
        if key not in self.withdraw_requests or not self.withdraw_requests[key]:
            raise gl.vm.UserError("You have no pending withdrawal request")
        self.withdraw_requests[key] = ""
        return "withdrawal request cancelled"

    @gl.public.write
    def complete_withdrawal(self) -> str:
        key = self._key(gl.message.sender_address)
        if key not in self.withdraw_requests or not self.withdraw_requests[key]:
            raise gl.vm.UserError("You have no pending withdrawal request")
        request = json.loads(self.withdraw_requests[key])
        now_epoch = _epoch_of(self._now_utc())
        if now_epoch < int(request["ready_at"]):
            raise gl.vm.UserError("The withdrawal notice period has not elapsed yet")
        if now_epoch > int(request["ready_at"]) + self.WITHDRAW_WINDOW_SECONDS:
            raise gl.vm.UserError("This withdrawal request expired; request the withdrawal again")
        shares = min(int(request["shares"]), self._get_int(self.lp_shares, key))
        if shares <= 0:
            raise gl.vm.UserError("You hold no shares")
        capital = int(self.pool_capital)
        shares_total = int(self.total_shares)
        at_risk = self._started_exposure(now_epoch)
        amount = shares * (capital - at_risk) // shares_total
        free = capital - int(self.reserved)
        if amount > free:
            raise gl.vm.UserError(
                f"Only {free} wei is free of active policy reserves; request fewer shares or retry after policies settle"
            )
        self.lp_shares[key] = u256(self._get_int(self.lp_shares, key) - shares)
        self.total_shares = u256(shares_total - shares)
        self.pool_capital = u256(capital - amount)
        self.withdraw_requests[key] = ""
        self._credit(key, amount)
        return json.dumps({"shares_burned": str(shares), "credited_wei": str(amount), "held_back_wei": str(at_risk)},
                          sort_keys=True)

    @gl.public.write
    def request_quote(
        self,
        peril: str,
        location_label: str,
        latitude: str,
        longitude: str,
        start_date: str,
        end_date: str,
        threshold: str,
        radius_km: int = 0,
    ) -> str:
        now = self._now_utc()
        terms = self._validate_terms(peril, location_label, latitude, longitude, start_date, end_date, threshold,
                                     radius_km, now)
        ctx = self._quote_ctx(terms, now)
        result = gl.vm.run_nondet_unsafe(
            lambda: _quote_compute(ctx),
            lambda leader: _quote_validator(leader, ctx),
        )
        if not isinstance(result, dict) or not result.get("ok"):
            reason = str(result.get("error", "unknown")) if isinstance(result, dict) else "unknown"
            raise gl.vm.UserError("The risk could not be priced: " + reason + ". Nothing was recorded; try again later")
        hits = int(result["hits"])
        samples = int(result["samples"])
        risk_bps = (hits + 1) * BPS // (samples + 2)
        if risk_bps > self.MAX_RISK_BPS:
            raise gl.vm.UserError(
                f"This event happened in {risk_bps // 100}.{str(risk_bps % 100).rjust(2, '0')}% of comparable historical windows; "
                "it is too likely to insure. Pick a harder threshold"
            )
        rate_bps = max(self.MIN_PREMIUM_BPS, risk_bps * self.LOADING_BPS // BPS)
        number = int(self.quote_count)
        quote_id = f"q_{number}"
        self.quote_count = u256(number + 1)
        quote = dict(terms)
        quote.update({
            "id": quote_id, "requester": self._key(gl.message.sender_address), "hits": hits, "samples": samples,
            "risk_bps": risk_bps, "rate_bps": rate_bps, "created_epoch": _epoch_of(now),
            "expires_epoch": _epoch_of(now) + self.QUOTE_TTL_SECONDS, "history_url": ctx["url"],
        })
        self.quotes[quote_id] = json.dumps(quote, sort_keys=True)
        return json.dumps(quote, sort_keys=True)

    @gl.public.write.payable
    def buy_policy(self, quote_id: str, coverage_gen: int) -> str:
        try:
            return self._buy(quote_id, int(coverage_gen))
        except gl.vm.UserError as error:
            return self._bounce(error)

    def _buy(self, quote_id: str, coverage_gen: int) -> str:
        if self.settings["sales_paused"] == "1":
            raise gl.vm.UserError("New policy sales are paused")
        sent = int(gl.message.value)
        quote = self._load_quote(quote_id)
        now = self._now_utc()
        now_epoch = _epoch_of(now)
        if now_epoch > int(quote["expires_epoch"]):
            raise gl.vm.UserError("This quote has expired; request a new one")
        if not self.ALLOW_BACKDATED and int(quote["start_epoch"]) < now_epoch + self.MIN_LEAD_SECONDS:
            raise gl.vm.UserError(
                f"Cover must be bought at least {self.MIN_LEAD_SECONDS // 3600} hours before the window starts"
            )
        if coverage_gen < self.MIN_COVERAGE_GEN or coverage_gen > self.MAX_COVERAGE_GEN:
            raise gl.vm.UserError(f"coverage_gen must be between {self.MIN_COVERAGE_GEN} and {self.MAX_COVERAGE_GEN}")
        coverage = coverage_gen * WEI_PER_GEN
        capacity, binding, _limits = self._capacity(quote["peril"], quote["region"])
        if coverage > capacity:
            raise gl.vm.UserError(
                f"Coverage exceeds the pool capacity for this risk ({binding}); at most {capacity // WEI_PER_GEN} GEN is available"
            )
        premium, _parts = self._premium(int(quote["rate_bps"]), coverage)
        if sent < premium:
            raise gl.vm.UserError(f"The premium is {premium} wei but only {sent} wei was sent")
        holder = self._key(gl.message.sender_address)
        indexed = json.loads(self.holder_index[holder]) if holder in self.holder_index else []
        if len(indexed) >= self.MAX_POLICIES_PER_HOLDER:
            raise gl.vm.UserError("This address holds the maximum number of policies")
        number = int(self.policy_count)
        policy_id = f"pol_{number}"
        self.policy_count = u256(number + 1)
        policy = {
            "id": policy_id, "holder": holder, "quote_id": quote["id"], "peril": quote["peril"],
            "label": quote["label"], "lat_e4": quote["lat_e4"], "lon_e4": quote["lon_e4"],
            "radius_km": quote["radius_km"], "start_date": quote["start_date"], "end_date": quote["end_date"],
            "days": quote["days"], "start_epoch": quote["start_epoch"], "end_epoch": quote["end_epoch"],
            "threshold_t": quote["threshold_t"], "cmp": quote["cmp"], "unit": quote["unit"],
            "region": quote["region"], "risk_bps": quote["risk_bps"], "rate_bps": quote["rate_bps"],
            "coverage": str(coverage), "premium": str(premium), "created_epoch": now_epoch,
            "status": ST_ACTIVE, "decision": "", "observed_t": None, "note": "", "evidence_url": "",
            "resolved_epoch": 0, "resolver": "", "payout": "0",
        }
        self.pool_capital = u256(int(self.pool_capital) + premium)
        self.reserved = u256(int(self.reserved) + coverage)
        peril = quote["peril"]
        self.peril_reserved[peril] = u256(self._get_int(self.peril_reserved, peril) + coverage)
        region = quote["region"]
        self.region_reserved[region] = u256(self._get_int(self.region_reserved, region) + coverage)
        self.premiums_total = u256(int(self.premiums_total) + premium)
        indexed.append(policy_id)
        self.holder_index[holder] = json.dumps(indexed)
        active = self._active_list()
        active.append([policy_id, int(quote["start_epoch"]), str(coverage)])
        self.settings["active"] = json.dumps(active)
        self._save_policy(policy)
        self._credit(holder, sent - premium)
        return json.dumps({"policy_id": policy_id, "premium_wei": str(premium), "refunded_wei": str(sent - premium)},
                          sort_keys=True)

    @gl.public.write
    def cancel_policy(self, policy_id: str) -> str:
        policy = self._load_policy(policy_id)
        if policy["status"] != ST_ACTIVE:
            raise gl.vm.UserError(f"Cannot cancel a policy in status '{policy['status']}'")
        if self._key(gl.message.sender_address) != policy["holder"]:
            raise gl.vm.UserError("Only the policyholder may cancel")
        if _epoch_of(self._now_utc()) >= int(policy["start_epoch"]) - self.MIN_LEAD_SECONDS:
            raise gl.vm.UserError("The cancellation period has closed")
        premium = int(policy["premium"])
        fee = premium * self.CANCEL_FEE_BPS // BPS
        refund = premium - fee
        self.pool_capital = u256(int(self.pool_capital) - refund)
        self._release(policy)
        self._credit(policy["holder"], refund)
        policy["status"] = ST_CANCELLED
        policy["resolved_epoch"] = _epoch_of(self._now_utc())
        self._save_policy(policy)
        self.cancelled_count = u256(int(self.cancelled_count) + 1)
        return json.dumps({"refunded_wei": str(refund), "fee_wei": str(fee)}, sort_keys=True)

    @gl.public.write
    def resolve_policy(self, policy_id: str) -> str:
        policy = self._load_policy(policy_id)
        if policy["status"] != ST_ACTIVE:
            raise gl.vm.UserError(f"Cannot resolve a policy in status '{policy['status']}'")
        now = self._now_utc()
        opens = int(policy["end_epoch"]) + self.DATA_LAG_SECONDS
        if _epoch_of(now) < opens:
            raise gl.vm.UserError(f"The observation window has not finished yet; resolution opens at epoch {opens}")
        ctx = self._resolve_ctx(policy, now)
        result = gl.vm.run_nondet_unsafe(
            lambda: _resolve_compute(ctx),
            lambda leader: _resolve_validator(leader, ctx),
        )
        if not isinstance(result, dict):
            raise gl.vm.UserError("The resolution produced no result; try again later")
        decision = result.get("decision")
        if decision == DEC_INCONCLUSIVE:
            raise gl.vm.UserError(
                "The evidence could not be confirmed (" + str(result.get("note", ""))[:200] +
                "). Nothing was recorded; try again later. If it stays unresolved the policy can be voided for a full premium refund"
            )
        if decision not in (DEC_TRIGGERED, DEC_NOT_TRIGGERED):
            raise gl.vm.UserError("The resolution returned an unknown decision")
        observed = int(result["observed_t"])
        triggered = _compare(policy["cmp"], observed, int(policy["threshold_t"]))
        if triggered != (decision == DEC_TRIGGERED):
            raise gl.vm.UserError("The consensus decision contradicts the rule written into the policy")
        coverage = int(policy["coverage"])
        self._release(policy)
        if triggered:
            self.pool_capital = u256(int(self.pool_capital) - coverage)
            self._credit(policy["holder"], coverage)
            self.payouts_total = u256(int(self.payouts_total) + coverage)
            self.paid_count = u256(int(self.paid_count) + 1)
            policy["status"] = ST_PAID
            policy["payout"] = str(coverage)
        else:
            self.expired_count = u256(int(self.expired_count) + 1)
            policy["status"] = ST_EXPIRED
        policy["decision"] = decision
        policy["observed_t"] = observed
        policy["note"] = str(result.get("note", ""))[:300]
        policy["evidence_url"] = str(result.get("evidence_url", ""))[:400]
        policy["resolved_epoch"] = _epoch_of(now)
        policy["resolver"] = self._key(gl.message.sender_address)
        self._save_policy(policy)
        return json.dumps(policy, sort_keys=True)

    @gl.public.write
    def void_policy(self, policy_id: str) -> str:
        policy = self._load_policy(policy_id)
        if policy["status"] != ST_ACTIVE:
            raise gl.vm.UserError(f"Cannot void a policy in status '{policy['status']}'")
        now_epoch = _epoch_of(self._now_utc())
        opens = max(int(policy["end_epoch"]), int(policy["created_epoch"])) + self.RESOLVE_TIMEOUT_SECONDS
        if now_epoch < opens:
            raise gl.vm.UserError(f"The resolution timeout has not elapsed yet; voiding opens at epoch {opens}")
        premium = int(policy["premium"])
        self.pool_capital = u256(int(self.pool_capital) - premium)
        self._release(policy)
        self._credit(policy["holder"], premium)
        policy["status"] = ST_VOID
        policy["resolved_epoch"] = now_epoch
        policy["resolver"] = self._key(gl.message.sender_address)
        self._save_policy(policy)
        self.void_count = u256(int(self.void_count) + 1)
        return json.dumps({"refunded_wei": str(premium)}, sort_keys=True)

    @gl.public.write
    def withdraw(self) -> str:
        return self._pay_out(str(gl.message.sender_address))

    @gl.public.write
    def push_payment(self, beneficiary: str) -> str:
        if not ADDRESS_RE.fullmatch(str(beneficiary)):
            raise gl.vm.UserError("beneficiary is not a valid address")
        return self._pay_out(str(beneficiary))

    @staticmethod
    def _summary(policy: dict) -> dict:
        return {
            "id": policy["id"], "holder": policy["holder"], "peril": policy["peril"], "label": policy["label"],
            "start_date": policy["start_date"], "end_date": policy["end_date"], "threshold_t": policy["threshold_t"],
            "unit": policy["unit"], "coverage": policy["coverage"], "premium": policy["premium"],
            "status": policy["status"], "observed_t": policy["observed_t"], "payout": policy["payout"],
            "cmp": policy["cmp"], "radius_km": policy["radius_km"], "created_epoch": policy["created_epoch"],
            "resolved_epoch": policy["resolved_epoch"],
        }

    @gl.public.view
    def get_config(self) -> str:
        return json.dumps({
            "mode": self.MODE, "allow_backdated": self.ALLOW_BACKDATED, "min_lead_seconds": self.MIN_LEAD_SECONDS,
            "data_lag_seconds": self.DATA_LAG_SECONDS, "resolve_timeout_seconds": self.RESOLVE_TIMEOUT_SECONDS,
            "withdraw_notice_seconds": self.WITHDRAW_NOTICE_SECONDS,
            "withdraw_window_seconds": self.WITHDRAW_WINDOW_SECONDS, "quote_ttl_seconds": self.QUOTE_TTL_SECONDS,
            "min_deposit_wei": str(self.MIN_DEPOSIT), "min_coverage_gen": self.MIN_COVERAGE_GEN,
            "max_coverage_gen": self.MAX_COVERAGE_GEN, "max_utilization_bps": self.MAX_UTILIZATION_BPS,
            "max_policy_share_bps": self.MAX_POLICY_SHARE_BPS, "max_peril_share_bps": self.MAX_PERIL_SHARE_BPS,
            "max_region_share_bps": self.MAX_REGION_SHARE_BPS, "max_risk_bps": self.MAX_RISK_BPS,
            "loading_bps": self.LOADING_BPS, "min_premium_bps": self.MIN_PREMIUM_BPS,
            "max_premium_bps": self.MAX_PREMIUM_BPS, "surcharge_kink_bps": self.SURCHARGE_KINK_BPS,
            "cancel_fee_bps": self.CANCEL_FEE_BPS, "max_horizon_days": self.MAX_HORIZON_DAYS,
            "history_years": HISTORY_YEARS, "quake_history_years": QUAKE_HISTORY_YEARS,
            "sales_paused": self.settings["sales_paused"] == "1", "owner": self.settings["owner"],
        }, sort_keys=True)

    @gl.public.view
    def get_perils(self) -> str:
        items = []
        for key in PERIL_ORDER:
            spec = PERILS[key]
            items.append({
                "peril": key, "title": spec["title"], "rule": spec["rule"], "cmp": spec["cmp"], "unit": spec["unit"],
                "threshold_min_t": spec["lo"], "threshold_max_t": spec["hi"], "min_days": spec["min_days"],
                "max_days": spec["max_days"], "uses_radius": spec["src"] == "quake",
                "source": HOST_QUAKE if spec["src"] == "quake" else "open-meteo.com",
            })
        return json.dumps(items, sort_keys=True)

    @gl.public.view
    def get_pool(self) -> str:
        capital = int(self.pool_capital)
        shares = int(self.total_shares)
        reserved = int(self.reserved)
        return json.dumps({
            "capital": str(capital), "shares": str(shares), "reserved": str(reserved),
            "free_capital": str(capital - reserved),
            "share_price_e18": str(capital * WEI_PER_GEN // shares) if shares > 0 else str(WEI_PER_GEN),
            "utilization_bps": reserved * BPS // capital if capital > 0 else 0,
            "premiums_total": str(int(self.premiums_total)), "payouts_total": str(int(self.payouts_total)),
            "max_utilization_bps": self.MAX_UTILIZATION_BPS,
        }, sort_keys=True)

    @gl.public.view
    def get_stats(self) -> str:
        return json.dumps({
            "policies": int(self.policy_count), "quotes": int(self.quote_count), "paid": int(self.paid_count),
            "expired": int(self.expired_count), "void": int(self.void_count), "cancelled": int(self.cancelled_count),
            "active": int(self.policy_count) - int(self.paid_count) - int(self.expired_count)
            - int(self.void_count) - int(self.cancelled_count),
        }, sort_keys=True)

    @gl.public.view
    def get_exposure(self) -> str:
        capital = int(self.pool_capital)
        cap = capital * self.MAX_PERIL_SHARE_BPS // BPS
        items = []
        for key in PERIL_ORDER:
            used = self._get_int(self.peril_reserved, key)
            items.append({"peril": key, "reserved": str(used), "cap": str(cap), "remaining": str(max(0, cap - used))})
        return json.dumps(items, sort_keys=True)

    @gl.public.view
    def get_capacity(self, peril: str, latitude: str, longitude: str) -> str:
        if peril not in PERILS:
            raise gl.vm.UserError(f"peril must be one of {list(PERIL_ORDER)}")
        lat_e4 = _parse_coord_e4(latitude, 90)
        lon_e4 = _parse_coord_e4(longitude, 180)
        if lat_e4 is None or lon_e4 is None:
            raise gl.vm.UserError("latitude or longitude is not valid")
        capacity, binding, limits = self._capacity(peril, _region_of(lat_e4, lon_e4))
        return json.dumps({
            "max_coverage_wei": str(capacity), "max_coverage_gen": capacity // WEI_PER_GEN, "binding_limit": binding,
            "limits": {name: str(max(0, value)) for name, value in limits.items()},
        }, sort_keys=True)

    @gl.public.view
    def get_quote(self, quote_id: str) -> str:
        return self.quotes[self._load_quote(quote_id)["id"]]

    @gl.public.view
    def preview_premium(self, quote_id: str, coverage_gen: int) -> str:
        quote = self._load_quote(quote_id)
        coverage_gen = int(coverage_gen)
        if coverage_gen < self.MIN_COVERAGE_GEN or coverage_gen > self.MAX_COVERAGE_GEN:
            raise gl.vm.UserError(f"coverage_gen must be between {self.MIN_COVERAGE_GEN} and {self.MAX_COVERAGE_GEN}")
        coverage = coverage_gen * WEI_PER_GEN
        capacity, binding, _limits = self._capacity(quote["peril"], quote["region"])
        premium, parts = self._premium(int(quote["rate_bps"]), coverage)
        parts.update({
            "premium_wei": str(premium), "coverage_wei": str(coverage), "capacity_wei": str(capacity),
            "binding_limit": binding, "fits": coverage <= capacity,
            "expires_epoch": quote["expires_epoch"], "risk_bps": quote["risk_bps"],
        })
        return json.dumps(parts, sort_keys=True)

    @gl.public.view
    def get_policy(self, policy_id: str) -> str:
        return self.policies[self._load_policy(policy_id)["id"]]

    @gl.public.view
    def list_policies(self, offset: int = 0, limit: int = 20) -> str:
        total = int(self.policy_count)
        offset = max(0, int(offset))
        limit = max(1, min(50, int(limit)))
        items = []
        index = total - 1 - offset
        while index >= 0 and len(items) < limit:
            items.append(self._summary(json.loads(self.policies[f"pol_{index}"])))
            index -= 1
        return json.dumps({"total": total, "items": items}, sort_keys=True)

    @gl.public.view
    def get_holder_policies(self, address: str) -> str:
        key = self._key(address)
        ids = json.loads(self.holder_index[key]) if key in self.holder_index else []
        items = [self._summary(json.loads(self.policies[pid])) for pid in reversed(ids[-50:])]
        return json.dumps({"total": len(ids), "items": items}, sort_keys=True)

    @gl.public.view
    def get_lp(self, address: str) -> str:
        key = self._key(address)
        shares = self._get_int(self.lp_shares, key)
        total = int(self.total_shares)
        capital = int(self.pool_capital)
        request = json.loads(self.withdraw_requests[key]) if key in self.withdraw_requests and self.withdraw_requests[key] else None
        return json.dumps({
            "shares": str(shares), "value_wei": str(shares * capital // total) if total > 0 else "0",
            "pool_share_bps": shares * BPS // total if total > 0 else 0, "withdraw_request": request,
            "pending_withdrawal": str(self._get_int(self.pending_withdrawals, key)),
        }, sort_keys=True)

    @gl.public.view
    def preview_deposit(self, amount_wei: int) -> str:
        amount = int(amount_wei)
        total = int(self.total_shares)
        capital = int(self.pool_capital)
        minted = amount if total == 0 else (amount * total // capital if capital > 0 else 0)
        return json.dumps({"shares_minted": str(minted), "valid": amount >= self.MIN_DEPOSIT and minted > 0},
                          sort_keys=True)

    @gl.public.view
    def preview_withdrawal(self, shares: int, as_of_epoch: int = 0) -> str:
        shares = int(shares)
        total = int(self.total_shares)
        capital = int(self.pool_capital)
        at_risk = self._started_exposure(int(as_of_epoch))
        amount = shares * (capital - at_risk) // total if total > 0 else 0
        free = capital - int(self.reserved)
        return json.dumps({"amount_wei": str(amount), "held_back_wei": str(at_risk), "free_capital_wei": str(free),
                           "payable_now": amount <= free}, sort_keys=True)

    @gl.public.view
    def get_pending_withdrawal(self, address: str) -> str:
        return str(self._get_int(self.pending_withdrawals, self._key(address)))

    @gl.public.view
    def get_accounting(self) -> str:
        balance = int(self.balance)
        capital = int(self.pool_capital)
        owed = int(self.owed_total)
        reserved = int(self.reserved)
        return json.dumps({
            "contract_balance": str(balance), "pool_capital": str(capital), "owed_total": str(owed),
            "reserved": str(reserved), "liabilities": str(capital + owed),
            "balance_covers_liabilities": balance >= capital + owed, "reserved_within_capital": reserved <= capital,
        }, sort_keys=True)
