#!/usr/bin/env python3
"""
Builds contract_sandbox.py from contract.py.

The sandbox build is the same contract with seven class constants changed so
a whole policy lifecycle can be tried in minutes on GenLayer Studio:

  MODE                     "production" -> "sandbox"
  ALLOW_BACKDATED          False        -> True   (windows that already ended can be insured and resolved)
  MIN_LEAD_SECONDS         259200       -> 0
  DATA_LAG_SECONDS         129600       -> 0
  RESOLVE_TIMEOUT_SECONDS  1209600      -> 900
  WITHDRAW_NOTICE_SECONDS  259200       -> 0
  MAX_RISK_BPS             5000         -> 10000  (near-certain events can be priced, so a payout can be forced)

Nothing else changes. The script refuses to run unless each constant line
exists exactly once, and --check fails when the committed file is stale.

Usage:
    python3 tools/make_sandbox.py            write contract_sandbox.py
    python3 tools/make_sandbox.py --check    exit 1 if contract_sandbox.py is stale
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "contract.py")
TARGET = os.path.join(ROOT, "contract_sandbox.py")

CHANGES = (
    ('    MODE = "production"', '    MODE = "sandbox"'),
    ("    ALLOW_BACKDATED = False", "    ALLOW_BACKDATED = True"),
    ("    MIN_LEAD_SECONDS = 259200", "    MIN_LEAD_SECONDS = 0"),
    ("    DATA_LAG_SECONDS = 129600", "    DATA_LAG_SECONDS = 0"),
    ("    RESOLVE_TIMEOUT_SECONDS = 1209600", "    RESOLVE_TIMEOUT_SECONDS = 900"),
    ("    WITHDRAW_NOTICE_SECONDS = 259200", "    WITHDRAW_NOTICE_SECONDS = 0"),
    ("    MAX_RISK_BPS = 5000", "    MAX_RISK_BPS = 10000"),
)


def build(source):
    lines = source.split("\n")
    for old, new in CHANGES:
        hits = [i for i, line in enumerate(lines) if line == old]
        if len(hits) != 1:
            raise SystemExit(f"expected exactly one line {old!r} in contract.py, found {len(hits)}")
        lines[hits[0]] = new
    return "\n".join(lines)


def main(argv):
    with open(SOURCE, encoding="utf-8") as handle:
        result = build(handle.read())
    if "--check" in argv:
        try:
            with open(TARGET, encoding="utf-8") as handle:
                current = handle.read()
        except FileNotFoundError:
            current = None
        if current != result:
            print("contract_sandbox.py is stale: run python3 tools/make_sandbox.py")
            return 1
        print("contract_sandbox.py is up to date")
        return 0
    with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(result)
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
