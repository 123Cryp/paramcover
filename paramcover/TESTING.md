# Testing

## Offline

```
python3 -m unittest discover -s tests
PC_CONTRACT=$PWD/contract_sandbox.py python3 -m unittest discover -s tests
python3 tools/make_sandbox.py --check
```

The suite uses a stub of the genlayer SDK with mocked web and model calls. It covers pricing, pool shares and caps, purchase, resolution, payments (with a randomized accounting fuzz), views, source format rules and the frontend file.

## Studio

Deploy `contract_sandbox.py`. The sandbox accepts windows that already ended, so real past weather settles a policy in one transaction. Open `index.html?contract=0xYourAddress` and use the Lab tab, or run the same calls by hand in Studio.

Scenario A pays out, scenario B expires, scenario C is voided after the 15 minute timeout. Use dates that ended at least a few days ago.
