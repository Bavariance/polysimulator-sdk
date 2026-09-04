# Contributing

Issues and pull requests are welcome, including "your docs are wrong here".

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```

The suite runs entirely offline — every HTTP call is mocked, so `pytest` needs no
API key and touches no network.

## Running the examples

The examples DO hit the live paper API and need a key:

```bash
export POLYSIM_API_KEY=ps_live_...
python examples/01_first_trade.py
```

They place real simulated orders against your paper balance. Nothing touches a
chain; paper mode has no private key, no `chain_id` and no EIP-712 signing.

## What we especially want

- **Fidelity bug reports.** If a fill differs from what the same order would have
  done on Polymarket, that is the most valuable issue you can file. Include the
  market, the order, what happened and what you expected.
- **`py-clob-client` porting friction.** The compatibility surface is meant to be
  drop-in; anywhere it is not is a bug.
- **Documentation that misleads.** The README states our fidelity gaps on purpose.
  If it overstates anything, say so.

## What this SDK deliberately does not do

Paper mode has no queue position, no market impact and no sub-second matching.
These are documented in the README's fidelity section rather than hidden, and a
PR adding a *claim* about them will be declined; a PR adding the *behaviour*,
with tests, is a different conversation.

## Style

The code targets Python 3.10+, uses `httpx` and `websockets` and nothing else at
runtime. Keep it that way — the dependency-light footprint is a feature for people
running bots in constrained environments.
