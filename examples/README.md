# Runnable examples

Every file here runs end to end against the live paper-trading API. They are
ordered by what you probably want first.

```bash
pip install polysimulator
export POLYSIM_API_KEY=ps_live_...        # https://polysimulator.com/settings/api
python examples/01_first_trade.py
```

| file | what it shows |
|---|---|
| `01_first_trade.py` | Balance, a market, its book, one market order. The 60-second version. |
| `02_limit_orders.py` | Resting a limit, watching it fill, cancelling. Post-only and IOC/FOK. |
| `03_updown_crypto.py` | The BTC/ETH Up/Down markets, which is where most bot volume is. |
| `04_stream_prices.py` | WebSocket price streaming instead of polling. |
| `05_port_a_clob_bot.py` | The same bot written against `py-clob-client`, ported by changing the host. |

**These place real simulated orders** against your paper balance. Nothing touches
a chain, and no key of yours signs anything — paper mode has no private key, no
`chain_id` and no EIP-712. Reset your balance any time from the dashboard.
