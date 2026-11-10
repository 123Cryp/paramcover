import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
CONTRACT = open(os.path.join(ROOT, "contract.py"), encoding="utf-8").read()


def contract_methods():
    tree = ast.parse(CONTRACT)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ParamCover")
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}


def params_of(name):
    sig = re.search(r"def %s\((.*?)\) -> str" % name, CONTRACT, re.S).group(1)
    return [p.strip().split(":")[0].split("=")[0].strip() for p in sig.split(",") if p.strip() and p.strip() != "self"]


def script():
    return re.search(r'<script type="module">(.*?)</script>', HTML, re.S).group(1)


class TestFrontend(unittest.TestCase):
    def test_single_file_with_esm_sh_latest(self):
        self.assertIn('from "https://esm.sh/genlayer-js@latest"', HTML)
        self.assertIn('from "https://esm.sh/genlayer-js@latest/chains"', HTML)
        self.assertEqual(len(re.findall(r'<script type="module">', HTML)), 1)
        for inline in re.findall(r"<script>(.*?)</script>", HTML, re.S):
            self.assertLess(len(inline), 300)
            self.assertIn("paramcover.theme", inline)
        for link in re.findall(r"<link\b[^>]*>", HTML):
            self.assertRegex(link, r'href="https://fonts\.(googleapis|gstatic)\.com')

    def test_clients_use_only_chain_and_account(self):
        calls = re.findall(r"createClient\(\{([^}]*)\}\)", HTML)
        self.assertGreaterEqual(len(calls), 3)
        for body in calls:
            self.assertTrue(body.strip().startswith("chain: studionet"), body)
            self.assertNotIn("provider", body)
        for forbidden in ("initializeConsensusSmartContract", ".connect(", "provider:"):
            self.assertNotIn(forbidden, script())

    def test_hash_routes(self):
        for route in ("#/buy", "#/policies", "#/lab", "#/policy/"):
            self.assertIn(route, HTML)
        for tab in ('["buy", "shield"', '["pool", "pool"', '["policies", "list"', '["lab", "flask"'):
            self.assertIn(tab, script())
        self.assertIn('addEventListener("hashchange", route)', HTML)

    def test_uses_list_views(self):
        for name in ("list_policies", "get_holder_policies", "get_stats", "get_pool", "get_exposure"):
            self.assertIn('"%s"' % name, script())

    def test_every_called_method_exists_in_the_contract(self):
        s = script()
        called = set(re.findall(r'(?:read|readJson)\("([a-z_]+)"', s))
        called |= set(re.findall(r'write\([^,]+,\s*"[^"]*",\s*"([a-z_]+)"', s))
        self.assertGreaterEqual(len(called), 18)
        self.assertEqual(called - contract_methods(), set())

    def test_every_write_method_is_reachable_from_the_ui(self):
        used = set(re.findall(r'"([a-z_]+)"', script()))
        for name in ("request_quote", "buy_policy", "cancel_policy", "resolve_policy", "void_policy", "withdraw",
                     "push_payment", "provide_liquidity", "request_withdrawal", "complete_withdrawal",
                     "cancel_withdrawal"):
            self.assertIn(name, used)

    def test_quote_argument_order_matches_the_contract(self):
        self.assertEqual(params_of("request_quote"), ["peril", "location_label", "latitude", "longitude",
                                                      "start_date", "end_date", "threshold", "radius_km"])
        call = re.search(r'"request_quote",\s*\[(.*?)\]\);', script(), re.S).group(1)
        self.assertEqual(call.split(",")[:8].__len__(), 8)
        self.assertTrue(call.strip().startswith("bs.peril, bs.label, bs.lat, bs.lon, bs.start, bs.end"))

    def test_buy_sends_value_and_premium_buffer(self):
        self.assertEqual(params_of("buy_policy"), ["quote_id", "coverage_gen"])
        self.assertIn('"buy_policy", [q.id, Number(bs.coverage)]', script())
        self.assertIn("* 102n / 100n", script())

    def test_fields_read_from_json_exist_in_contract(self):
        for field in ("premium_wei", "coverage_wei", "risk_bps", "surcharge_bps", "fits", "max_coverage_gen",
                      "shares_minted", "pool_share_bps", "value_wei", "withdraw_request", "ready_at",
                      "utilization_bps", "free_capital", "share_price_e18", "premiums_total", "payouts_total",
                      "evidence_url", "observed_t", "resolved_epoch", "data_lag_seconds",
                      "resolve_timeout_seconds", "min_lead_seconds", "allow_backdated", "withdraw_notice_seconds"):
            self.assertIn(field, CONTRACT, field)
            self.assertIn(field, script(), field)

    def test_contract_data_is_never_inserted_as_html(self):
        s = script()
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(forbidden, s)

    def test_private_key_is_not_persisted_outside_session(self):
        s = script()
        self.assertNotIn("localStorage.setItem(\"paramcover.key", s)
        self.assertNotRegex(s, r"localStorage\.setItem\([^)]*pk")

    def test_all_perils_have_ui_entries(self):
        perils = set(re.findall(r'^    "([A-Z_]+)": \{', CONTRACT, re.M)) or set(re.findall(r'"([A-Z_]+)":\s*\{"', CONTRACT))
        ui = set(re.findall(r"^  ([A-Z_]+): \{ icon:", script(), re.M))
        self.assertTrue(ui)
        self.assertEqual({"RAIN_EXCESS", "RAIN_DEFICIT", "HEAT_WAVE", "FROST", "STORM_WIND", "QUAKE"}, ui)
        for key in ui:
            self.assertIn(key, CONTRACT)

    def test_native_append_never_receives_optional_nulls(self):
        for line in script().splitlines():
            if ".append(" in line and "? " in line and ": null" in line:
                self.assertIn("h(", line.split(".append(")[0] + "h(", line)
                self.assertNotRegex(line, r"(clear\([^)]*\)|body|root)\.append\(")

    def test_accessibility_basics(self):
        self.assertIn('<html lang="en">', HTML)
        self.assertIn("prefers-reduced-motion", HTML)
        self.assertIn(":focus-visible", HTML)
        self.assertIn('class="skip"', HTML)
        self.assertIn("showModal()", HTML)
        self.assertIn('aria-live="polite"', HTML)

    def test_transaction_states_are_distinct(self):
        s = script()
        for text in ("Waiting for your signature", "Submitted to the network", "Accepted by validators", "Finalized"):
            self.assertIn(text, s)
        self.assertIn('status: "ACCEPTED"', s)
        self.assertIn('status: "FINALIZED"', s)
        self.assertIn("You declined the request in your wallet", s)
        self.assertIn("Not enough GEN", s)

    def test_value_sending_actions_ask_for_confirmation(self):
        s = script()
        self.assertGreaterEqual(s.count("confirmAction({"), 6)

    def test_default_address_constant_present(self):
        self.assertRegex(HTML, r'DEFAULT_CONTRACT_ADDRESS = "0x[0-9a-fA-F]{40}"')


if __name__ == "__main__":
    unittest.main()
