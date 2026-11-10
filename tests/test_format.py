import ast
import io
import os
import tokenize
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADER = (
    "# v0.2.16",
    '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


class TestContractFiles(unittest.TestCase):
    FILES = ("contract.py", "contract_sandbox.py")

    def test_two_header_lines_then_code_only(self):
        for name in self.FILES:
            source = read(name)
            lines = source.splitlines()
            self.assertEqual(tuple(lines[:2]), HEADER, name)
            self.assertTrue(lines[2].startswith("from genlayer import"), name)
            comments = [t for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.COMMENT]
            self.assertEqual([c.start[0] for c in comments], [1, 2], f"{name} must contain no comments beyond the header")

    def test_no_docstrings_or_bare_strings(self):
        for name in self.FILES:
            tree = ast.parse(read(name))
            for node in ast.walk(tree):
                for field in ("body", "orelse", "finalbody"):
                    body = getattr(node, field, None)
                    if isinstance(body, list):
                        for child in body:
                            self.assertFalse(
                                isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant)
                                and isinstance(child.value.value, str), f"{name}: bare string at line {child.lineno}")

    def test_files_are_ascii_and_have_no_trailing_spaces(self):
        for name in self.FILES:
            source = read(name)
            source.encode("ascii")
            for number, line in enumerate(source.splitlines(), 1):
                self.assertEqual(line, line.rstrip(), f"{name}:{number}")
            self.assertTrue(source.endswith("\n"))

    def test_exactly_one_contract_class(self):
        for name in self.FILES:
            tree = ast.parse(read(name))
            contracts = [n.name for n in tree.body if isinstance(n, ast.ClassDef)
                         and any(isinstance(b, ast.Attribute) and b.attr == "Contract" for b in n.bases)]
            self.assertEqual(contracts, ["ParamCover"], name)

    def test_nondet_callbacks_are_module_level_and_never_capture_self(self):
        source = read("contract.py")
        tree = ast.parse(source)
        top = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in ("_resolve_compute", "_resolve_validator", "_quote_compute", "_quote_validator"):
            self.assertIn(name, top)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ParamCover")
        for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Lambda):
                    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                    self.assertNotIn("self", names, f"{fn.name} lambda captures self")

    def test_runner_calls_are_positional(self):
        tree = ast.parse(read("contract.py"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "run_nondet_unsafe"]
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(len(call.args), 2)
            self.assertEqual(call.keywords, [])

    def test_known_genvm_pitfalls_are_avoided(self):
        source = read("contract.py")
        for forbidden in ("gl.message.sender,", "gl.message.timestamp", "inmem_allocate", "datetime.min",
                          "import decimal", "import math"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertNotIn('emit_transfer(value=amount, on=', source)
        self.assertIn("emit_transfer(value=u256(amount))", source)

    def test_init_never_assigns_containers_to_storage_fields(self):
        tree = ast.parse(read("contract.py"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ParamCover")
        init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        for node in ast.walk(init):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        self.assertNotIsInstance(node.value, (ast.Dict, ast.List, ast.Set))

    def test_payable_methods_never_raise_after_taking_funds(self):
        source = read("contract.py")
        for name in ("provide_liquidity", "buy_policy"):
            start = source.index(f"def {name}(")
            block = source[start:start + 400]
            self.assertIn("except gl.vm.UserError as error:", block)
            self.assertIn("self._bounce(error)", block)

    def test_sandbox_differs_only_in_the_documented_constants(self):
        prod = read("contract.py").splitlines()
        sand = read("contract_sandbox.py").splitlines()
        self.assertEqual(len(prod), len(sand))
        diff = {p.strip() for p, s in zip(prod, sand) if p != s}
        self.assertEqual(diff, {
            'MODE = "production"', "ALLOW_BACKDATED = False", "MIN_LEAD_SECONDS = 259200", "DATA_LAG_SECONDS = 129600",
            "RESOLVE_TIMEOUT_SECONDS = 1209600", "WITHDRAW_NOTICE_SECONDS = 259200", "MAX_RISK_BPS = 5000",
        })

    def test_production_constants(self):
        source = read("contract.py")
        self.assertIn('MODE = "production"', source)
        self.assertIn("ALLOW_BACKDATED = False", source)


if __name__ == "__main__":
    unittest.main()
