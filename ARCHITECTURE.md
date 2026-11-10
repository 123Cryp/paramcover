# Architecture

## Contract

Single class `ParamCover`. State is JSON strings in `TreeMap[str, str]` plus `u256` counters, which keeps storage simple and views cheap.

### Non-deterministic work

Two calls use `gl.vm.run_nondet_unsafe(leader, validator)`, both with module-level functions and a plain context dict:

- **Quote.** Leader fetches ten years of daily history and counts hits and samples over sliding windows. Validators fetch the same history independently and accept only if hits and samples match exactly.
- **Resolve.** Leader fetches the window data, computes the observed value and asks a model to review the page (valid/reason). Validators recompute and must agree exactly on the decision and day count, the observed value must be within a small per-peril tolerance (recent weather data can be revised between fetches), and the decision must follow from the rule.

Network and model errors inside these functions are converted to plain values (an inconclusive decision), never raised across consensus. Money moves only in separate transactions from non-deterministic calls.

### Accounting

- `pool_capital` is LP capital including retained premiums, minus payouts.
- `reserved` is the sum of active coverage; it always stays within capital.
- `pending_withdrawals` holds amounts owed to holders and LPs; `owed_total` is their sum.
- Invariant: contract balance equals capital plus owed amounts. `get_accounting` exposes it and tests assert it after every scenario, including a randomized fuzz.

### Shares

First deposit mints shares 1:1. Later deposits mint `amount * total_shares / capital`. Withdrawals are two steps: request with notice, then complete within a fixed window, otherwise the request expires. Completion is limited to free capital, and the payout is computed on capital minus the coverage of every active policy whose window has started. That part stays with the remaining LPs, so an LP who sees an outcome coming cannot exit at full value and leave the loss to the others. Active policies are tracked as a compact JSON list for this.

### Payable methods

A rejected payable call does not raise after value arrived. It credits the value to the sender's pending balance and returns `{refunded_wei, rejected}`, so no funds are stranded.

## Frontend

One `index.html`, no build step and no framework. `genlayer-js` is imported from esm.sh with `createClient({ chain, account })` only; the only other external resource is the Schibsted Grotesk web font, with a system font fallback.

- **Routes:** home, cover (buy), pool, policies, policy detail, lab, all on the hash.
- **Composer:** a policy is written as one sentence whose underlined words are real form controls. The same sentence is what the contract checks.
- **Transactions:** every write shows four distinct stages: waiting for signature, submitted, accepted by validators, finalized. Wallet rejection, wrong network, insufficient balance, contract refusals and bounced payments each get their own message. Buttons lock while their transaction runs.
- **Confirmation:** actions that send GEN or cannot be undone open a native modal dialog first.
- **Layout:** mobile first with a bottom tab bar; from 1024 px a sidebar. Policy lists are tables on wide screens and row lists on phones.
- **Accessibility:** semantic landmarks, skip link, visible focus, labelled controls, live regions for progress, and reduced motion respected.
- **Safety:** all contract data is rendered through `textContent`. The sandbox or production mode is read from `get_config`, and the Lab runs only on sandbox deployments.
