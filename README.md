# ParamCover

Small parametric insurance on GenLayer. The payout trigger is written into the policy up front (for example "the hottest day in Dubai reaches 42 C between two dates"). A holder pays a premium, a shared liquidity pool locks the full payout as collateral, and after the window GenLayer validators read public weather or earthquake data. If the trigger happened, the payout is credited automatically. If not, the pool keeps the premium.

There are no claims, no adjusters and no discretion: the rule is fixed at purchase and decided by validator consensus.

## What is in the box

| File | Purpose |
| --- | --- |
| `contract.py` | Production Intelligent Contract (`ParamCover`) |
| `contract_sandbox.py` | Same contract with seven constants changed so a full lifecycle runs in minutes on Studio (generated, never edited by hand) |
| `index.html` | Single-file frontend: write a policy as a sentence, pool dashboard, policy pages, guided test lab |
| `tests/` | Offline test suite with a GenLayer SDK stub (176 tests) |
| `tools/make_sandbox.py` | Builds and checks `contract_sandbox.py` |
| `ARCHITECTURE.md`, `TESTING.md` | Design notes and the Studio walkthrough |

## Live deployment

| | |
| --- | --- |
| Contract (sandbox build) | `0xb4fCBDB1217a59643f30243c03BbbaBd1577A4EA` |
| Explorer | https://explorer-studio.genlayer.com/address/0xb4fCBDB1217a59643f30243c03BbbaBd1577A4EA |
| Frontend | https://123cryp.github.io/paramcover/ |
| Network | GenLayer Studio |

## Policy types

| Peril | Pays out when | Window | Data source |
| --- | --- | --- | --- |
| Heavy rain | total rain in the window is at least X mm | 1-14 days | Open-Meteo |
| Dry spell | total rain in the window is at most X mm | 7-14 days | Open-Meteo |
| Extreme heat | hottest daily maximum is at least X C | 1-14 days | Open-Meteo |
| Hard frost | coldest daily minimum is at most X C | 1-14 days | Open-Meteo |
| Storm winds | strongest daily gust is at least X km/h | 1-14 days | Open-Meteo |
| Earthquake | a quake of at least magnitude X (4.0-9.0) within R km | 1-30 days | USGS |

Flight delays are deliberately not a peril: there is no free, stable, machine-readable source that validators can all fetch and agree on.

## Money flow

1. **Liquidity providers** deposit GEN and receive pool shares. Share price rises with premiums and falls with payouts.
2. **Quote.** `request_quote` makes validators read about ten years of history for the location and count how often the trigger would have fired in comparable windows. The quote stores that frequency.
3. **Premium** = Laplace-smoothed historical frequency x 130% loading x utilization surcharge, clamped to a floor and ceiling. `preview_premium` shows it for any coverage.
4. **Buy.** `buy_policy` takes the premium; the full coverage is reserved from pool capital. Overpayment is credited back, not lost.
5. **Resolve.** After the window plus a data lag, anyone calls `resolve_policy`. Validators fetch the data, compute the observed value and a second LLM review gates obviously broken pages.
6. **Outcome.** Triggered: coverage is credited to the holder. Not triggered: the pool keeps the premium and the collateral is released. Inconclusive data records nothing; after a timeout anyone can `void_policy` for a full premium refund.
7. **Payments** are pull-based (`withdraw`) with a permissionless `push_payment` that sends a holder's balance to them.

## Pool risk caps

- One policy: at most 10% of pool capital
- All active cover: at most 80% of capital
- One peril: at most 40%
- One map region (1 degree cell): at most 25%
- LP exits need notice (3 days in production), must be completed within 2 days after that, and only free capital can leave
- An exiting LP leaves behind their share of the collateral of policies whose window has already started, so nobody can exit after seeing an outcome and leave the loss to the others

`get_capacity` reports the binding limit for any peril and location.

## Read-only functions

`get_config`, `get_perils`, `get_pool`, `get_stats`, `get_exposure`, `get_capacity`, `get_quote`, `preview_premium`, `get_policy`, `list_policies`, `get_holder_policies`, `get_lp`, `preview_deposit`, `preview_withdrawal`, `get_pending_withdrawal`, `get_accounting`.

## Run the tests

```
python3 -m unittest discover -s tests
PC_CONTRACT=$PWD/contract_sandbox.py python3 -m unittest discover -s tests
python3 tools/make_sandbox.py --check
```

## Deploy and try it

1. Open GenLayer Studio, deploy `contract_sandbox.py` (no constructor arguments) and copy the address.
2. Open `index.html` with `?contract=0xYourAddress`, or paste the address in the wallet menu.
3. Follow the **Lab** tab, or the table in `TESTING.md`.

The frontend is wired to the deployment above through `DEFAULT_CONTRACT_ADDRESS` in `index.html`; `?contract=0x...` overrides it. For a production deployment use `contract.py`.

## Limits and honesty

- Prices are indicative, derived from public history, not an actuarial model.
- Open-Meteo and USGS response shapes were implemented from their documented formats and mocked in tests. They have not been exercised against the live services from the build environment, so the first live Studio run is the real integration test.
- A buyer may know a forecast that the historical price does not; the lead time and risk cap limit but do not remove this.
- This is a demonstration contract, not regulated insurance.

## License

MIT
