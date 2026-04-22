# agentpay-condor-carry

A delta-neutral ETH funding-rate carry agent for [Hummingbot Condor](https://github.com/hummingbot/condor), gated by AgentPay's budget-aware pre-trade check.

Two Condor Routines + one agent strategy. Each tick, the agent pays sub-penny USDC to AgentPay for market signals (funding, open interest, F&G, whale activity), decides whether to enter, hold, or exit a paired spot+perp position, and logs everything to the session journal. Budget-capped per tick — the LLM can't burn your wallet.

## 30-second test (no Condor needed)

Before you commit to the full Condor install below, you can confirm AgentPay is reachable and the routines' data source works with a single Python call on testnet. Free, no real USDC:

```bash
pip install agentpay-x402
python3 -c "from agentpay import faucet_wallet, Session; w = faucet_wallet(); s = Session(w, testnet=True); print(s.call('funding_rates', {'asset': 'ETH'})['result'])"
```

You should see ETH funding rates from Binance, Bybit, and OKX printed as a JSON blob. If that works, the full Condor install below will work too — the routines call the same tool, just wrapped in a scheduler.

## Quickstart

```bash
# 1. Install Condor (https://github.com/hummingbot/condor) and the AgentPay SDK
uv pip install agentpay-x402

# 2. Drop the routines + agent into Condor
cp ./routines/*.py ~/condor/routines/
cp -r ./trading_agents/funding_carry_v1 ~/condor/trading_agents/

# 3. Add AgentPay secrets to ~/condor/.env (replace <paste-your-...> with real values)
echo "AGENTPAY_NETWORK=testnet" >> ~/condor/.env
echo "TEST_AGENT_SECRET_KEY=<paste-your-stellar-testnet-secret>" >> ~/condor/.env
# (Need a testnet secret? See SETUP.md step 4 for a one-liner that generates + funds one.)

# 4. Start Condor and create a session in Telegram
cd ~/condor && make run
# Then DM your Condor bot: /routines → run each once; /agent → funding_carry_v1 → dry_run
```

Full install instructions, including how to generate a testnet key and what the first-tick snapshot should look like, are in [SETUP.md](SETUP.md).

## What each tick does

```
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ pre_trade_check  │ → │  funding_carry   │ → │  Hummingbot      │
│ (gate: abort /   │   │  (signal: enter/ │   │  executor (spot  │
│  caution /       │   │  exit / hold /   │   │  + perp legs)    │
│  proceed)        │   │  skip)           │   │                  │
└──────────────────┘   └──────────────────┘   └──────────────────┘
        ↑                       ↑
        │                       │
      AgentPay               AgentPay
  funding + whale +       funding + OI +
  F&G + OI ($0.008)       F&G ($0.006)
```

Per tick cost: roughly $0.014 USDC. Both routines are individually budget-capped via `max_spend_usd` in their `Config`; if AgentPay would exceed the cap the routine returns a graceful SKIP rather than partial data.

## Routines

- **`pre_trade_check`** — gate every tick. Pulls funding, open interest, Fear & Greed, and whale flow for the asset. Returns `abort` on whale tail risk or F&G extremes, `caution` on mixed signals (agent halves position size), `proceed` otherwise.
- **`funding_carry`** — state-aware signal. Given current `carry_position` + `carry_direction`, returns one of: `enter_long_carry`, `enter_short_carry`, `exit_long_carry`, `exit_short_carry`, `hold`, `skip`.

Both are plain Python files in [`routines/`](routines/). Drop them in `~/condor/routines/`; Condor auto-discovers.

### Why cross-exchange data matters here

The routines lean on AgentPay tools that aggregate across venues in a single call: `funding_rates` returns Binance + Bybit + OKX, `open_interest` and `orderbook_depth` cover Binance + Bybit. Each individual Hummingbot connector only sees its own venue, so this is where AgentPay earns its keep — the agent compares all three venues at once and picks the richest carry *before* routing execution to a single-venue connector. Scraping one exchange's public API gives you one data point; paying AgentPay gives you the whole perp market for sub-penny USDC.

## Strategy

[`trading_agents/funding_carry_v1/agent.md`](trading_agents/funding_carry_v1/agent.md) — a delta-neutral ETH carry on `binance` (spot) + `binance_perpetual`. $1000 per leg default, ticks every 4h (aligned with funding windows), testnet/dry-run only for the first 10 ticks. Every tick is journaled with the verdict, action, executors spawned, and total AgentPay cost.

## Related

- **AgentPay** — the x402 payment gateway powering these routines: [github.com/romudille-bit/agentpay](https://github.com/romudille-bit/agentpay)
- **Hummingbot Condor** — the agent harness this runs on: [github.com/hummingbot/condor](https://github.com/hummingbot/condor)
- **Live gateway** — Stellar mainnet + Base mainnet at `https://gateway-production-2cc2.up.railway.app`
- **PyPI** — `pip install agentpay-x402`

## License

MIT — see [LICENSE](LICENSE).
