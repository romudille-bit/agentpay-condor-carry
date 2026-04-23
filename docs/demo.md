# Condor tick — real output

Snapshot from a single run of both routines against ETH on the AgentPay
testnet gateway (2026-04-23 19:16 UTC). All payments are real on-chain
testnet USDC transfers — click any tx hash to verify.

- Network: Stellar testnet
- Gateway: `gateway-testnet-production.up.railway.app`
- Budget cap (per routine): $0.02 USDC
- Total spend: 0.016 USDC across 8 paid calls
- Elapsed: ~40s per routine

## Routine 1 — `pre_trade_check`

Four paid signals, aggregated into a go/caution/block verdict.

```
──────────────────────────────────────────────────────────
  AgentPay Session Summary
──────────────────────────────────────────────────────────
  Calls made:  4
  Spent:       $0.008  (budget: $0.02)
  Remaining:   $0.012

  Breakdown:
    funding_rates                  $0.003  |  b8f00a3e76d63a56...
    open_interest                  $0.002  |  8a09133daadfde7b...
    fear_greed_index               $0.001  |  d47afb494f2221b7...
    whale_activity                 $0.002  |  1f1348508e3cd262...
──────────────────────────────────────────────────────────
    elapsed: 40.0s
```

Routine output (this is the string Condor reads):

```
⚠️ Pre-Trade Check: CAUTION
Asset: ETH | Risk: MEDIUM | Cost: $0.008

Market: Funding -0.0074%/8h (negative, ~-8% APY) | OI -11.6%/24h | L/S 1.95 | F&G 46 (Fear) | Whale $0

Flags:
  • OI declining -11.6%/24h — conviction softening
```

Structured JSON (also returned, for programmatic consumers):

```json
{
  "verdict": "caution",
  "risk_level": "medium",
  "asset": "ETH",
  "signals": {
    "funding_avg_pct": -0.007375,
    "funding_annualized": -8.08,
    "funding_regime": "negative",
    "oi_change_24h_pct": -11.61,
    "oi_change_1h_pct": 0.13,
    "long_short_ratio": 1.946,
    "fear_greed_value": 46,
    "fear_greed_label": "Fear",
    "whale_volume_usd": 0.0,
    "whale_transfer_count": 0,
    "cost": "$0.008"
  },
  "flags": ["OI declining -11.6%/24h — conviction softening"],
  "cost": "$0.008",
  "timestamp": "2026-04-23T19:16:34Z"
}
```

## Routine 2 — `funding_carry`

Same four signals, interpreted into a directional decision (long carry,
short carry, or skip).

```
──────────────────────────────────────────────────────────
  AgentPay Session Summary
──────────────────────────────────────────────────────────
  Calls made:  4
  Spent:       $0.008  (budget: $0.02)
  Remaining:   $0.012

  Breakdown:
    funding_rates                  $0.003  |  cbb8aff69f73cedb...
    open_interest                  $0.002  |  ec244a92359c4806...
    fear_greed_index               $0.001  |  a5782732e5fc7f40...
    whale_activity                 $0.002  |  6ea16181eac6b124...
──────────────────────────────────────────────────────────
    elapsed: 40.1s
```

Routine output:

```
Funding Carry Signal — SKIP
Asset: ETH | Direction: — | Lean: short-biased | Cost: $0.008

Market: Funding -0.0074%/8h (~-8% APY) | OI -11.6%/24h | L/S 1.95 | F&G 46 (Fear) | Whale $0
```

Structured JSON:

```json
{
  "action": "skip",
  "direction": null,
  "lean": "short-biased",
  "asset": "ETH",
  "signals": {
    "funding_avg_pct": -0.007364,
    "funding_annualized": -8.06,
    "oi_change_24h_pct": -11.61,
    "long_short_ratio": 1.946,
    "fear_greed_value": 46,
    "fear_greed_label": "Fear",
    "whale_volume_usd": 0.0,
    "cost": "$0.008"
  },
  "cost": "$0.008"
}
```

## What this tells you

Both routines ran against live Binance + Bybit + OKX funding data,
Binance + Bybit open interest, alternative.me Fear & Greed, and Etherscan
whale transfers — aggregated in under a second after the x402 payments
settle on Stellar (~5s per payment).

`pre_trade_check` flagged **CAUTION** on ETH because open interest is
dropping 11.6% in 24h while funding stays negative — conviction is
softening. `funding_carry` came back **SKIP** with a short-biased lean:
negative funding + oversold Fear & Greed = signal exists but isn't
actionable in isolation. A Condor operator would read these verbatim
strings and gate the next strategy decision accordingly.

## Verify on-chain

Every call above produced a real Stellar testnet transaction. Copy any
`tx_hash` prefix and paste it into [Stellar Expert (testnet)](https://stellar.expert/explorer/testnet/search)
to see the USDC payment land at the gateway wallet
`GBI6GZW2MDSZ6N5BN7JSDCTQQ6NEOC6PSDAVYTMYXWXOPUVWQ3O5E67S`.

## Reproduce it yourself

```bash
pip install agentpay-x402
python3 -c "from agentpay import faucet_wallet, Session; w = faucet_wallet(); s = Session(w, testnet=True); print(s.call('funding_rates', {'asset': 'ETH'})['result'])"
```

That prints live cross-exchange funding rates after paying $0.003 USDC
on testnet. No API keys, no subscriptions, no signup.
