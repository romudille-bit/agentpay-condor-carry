"""Pre-trade market context check — gates entries on funding, OI, F&G, whale activity.

Drop-in Condor routine. Matches Condor's routine contract:
  - Pydantic `Config` class (auto-discovered, fields show up in /routines UI)
  - async `run(config, context) -> str` (Telegram-readable result string)

Reads AgentPay SDK (installed via `uv pip install agentpay-x402`) and pays ~$0.008
USDC per tick to gather four live signals. Returns a verdict block the Condor
agent can read verbatim and decide whether to proceed.

Required env vars in ~/condor/.env:
  AGENTPAY_NETWORK         mainnet | testnet (default: testnet)
  TEST_AGENT_SECRET_KEY    Stellar secret for testnet mode
  STELLAR_SECRET_KEY       Stellar secret for mainnet mode
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from agentpay import AgentWallet, Session, BudgetExceeded, PaymentFailed

log = logging.getLogger(__name__)

_GATEWAYS = {
    "mainnet": "https://gateway-production-2cc2.up.railway.app",
    "testnet": "https://gateway-testnet-production.up.railway.app",
}


class Config(BaseModel):
    """Gate trade entries using funding, OI, sentiment, and whale activity."""

    asset: str = Field(default="ETH", description="Asset symbol (e.g. ETH, BTC)")
    max_spend_usd: float = Field(default=0.02, description="Max AgentPay spend per tick (USDC)")
    whale_abort_usd: float = Field(default=5_000_000, description="Abort if whale volume exceeds this (USD)")
    whale_caution_usd: float = Field(default=1_000_000, description="Caution if whale volume exceeds this (USD)")
    fg_extreme_high: int = Field(default=90, description="Abort if Fear & Greed above this")
    fg_extreme_low: int = Field(default=10, description="Abort if Fear & Greed below this")
    oi_drop_abort_pct: float = Field(default=-15.0, description="Abort if OI 24h change below this %")
    oi_drop_caution_pct: float = Field(default=-8.0, description="Caution if OI 24h change below this %")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Run the pre-trade check. Returns a string the Condor agent can read."""
    network = os.environ.get("AGENTPAY_NETWORK", "testnet")
    gateway = os.environ.get("AGENTPAY_GATEWAY_URL", _GATEWAYS[network])
    secret = (
        os.environ.get("STELLAR_SECRET_KEY")
        or (os.environ.get("TEST_AGENT_SECRET_KEY") if network == "testnet" else None)
        or ""
    )
    if not secret:
        key_name = "TEST_AGENT_SECRET_KEY" if network == "testnet" else "STELLAR_SECRET_KEY"
        return f"ABORT\nNo {key_name} in ~/condor/.env — cannot gather signals."

    # The AgentPay SDK is sync (httpx + stellar-sdk). Run it off the event loop
    # so Condor's async tick engine doesn't block.
    return await asyncio.to_thread(_run_sync, config, network, gateway, secret)


def _run_sync(config: Config, network: str, gateway: str, secret: str) -> str:
    wallet = AgentWallet(secret_key=secret, network=network)
    signals: dict = {}
    try:
        with Session(wallet, gateway_url=gateway, max_spend=str(config.max_spend_usd)) as session:
            signals = _gather(session, config.asset)
    except BudgetExceeded as e:
        return f"ABORT\nSignal budget exceeded: {e}"
    except PaymentFailed as e:
        return f"SKIP\nPayment failed: {e}"
    except Exception as e:
        log.exception("pre_trade_check: signal gathering failed")
        return f"ABORT\nSignal error: {e}"

    verdict, risk, flags = _evaluate(signals, config)
    return _format(signals, verdict, risk, flags, config)


def _gather(session: Session, asset: str) -> dict:
    out: dict = {}

    rates = session.call("funding_rates", {"asset": asset}).get("result", {})
    exchanges = rates.get("rates", rates.get("exchanges", []))
    if exchanges:
        avg = sum(e["funding_rate_pct"] for e in exchanges) / len(exchanges)
        out["funding_avg_pct"] = avg
        out["funding_annualized"] = avg * 3 * 365
        out["funding_regime"] = "positive" if avg > 0.005 else "negative" if avg < -0.005 else "neutral"
        out["funding_ok"] = True
    else:
        out["funding_avg_pct"] = 0.0
        out["funding_annualized"] = 0.0
        out["funding_regime"] = "unknown"
        out["funding_ok"] = False

    oi = session.call("open_interest", {"symbol": asset}).get("result", {})
    out["oi_change_24h_pct"] = oi.get("oi_change_24h_pct", 0)
    out["oi_change_1h_pct"] = oi.get("oi_change_1h_pct", 0)
    out["long_short_ratio"] = oi.get("long_short_ratio", 1.0)

    fg = session.call("fear_greed_index", {}).get("result", {})
    out["fear_greed_value"] = fg.get("value", 50)
    out["fear_greed_label"] = fg.get("value_classification", "Neutral")

    whales = session.call("whale_activity", {"token": asset, "min_usd": 500_000}).get("result", {})
    out["whale_volume_usd"] = whales.get("total_volume_usd", 0)
    out["whale_transfer_count"] = len(whales.get("large_transfers", []))

    out["cost"] = session.spent()
    return out


def _evaluate(s: dict, cfg: Config) -> tuple[str, str, list[str]]:
    aborts: list[str] = []
    cautions: list[str] = []

    fg = s["fear_greed_value"]
    whale = s["whale_volume_usd"]
    oi = s["oi_change_24h_pct"]

    if whale >= cfg.whale_abort_usd:
        aborts.append(f"Whale volume ${whale:,.0f} — potential disruption")
    if fg >= cfg.fg_extreme_high:
        aborts.append(f"Extreme greed ({fg}/100) — reversal risk")
    if fg <= cfg.fg_extreme_low:
        aborts.append(f"Extreme fear ({fg}/100) — capitulation risk")
    if oi <= cfg.oi_drop_abort_pct:
        aborts.append(f"OI collapsed {oi:.1f}%/24h — liquidity thinning")

    if cfg.whale_caution_usd <= whale < cfg.whale_abort_usd:
        cautions.append(f"Elevated whale volume: ${whale:,.0f}")
    if cfg.oi_drop_abort_pct < oi <= cfg.oi_drop_caution_pct:
        cautions.append(f"OI declining {oi:.1f}%/24h — conviction softening")
    if (cfg.fg_extreme_high - 10) <= fg < cfg.fg_extreme_high:
        cautions.append(f"Fear/Greed approaching extreme ({fg}/100)")
    if cfg.fg_extreme_low < fg <= (cfg.fg_extreme_low + 10):
        cautions.append(f"Fear/Greed approaching extreme fear ({fg}/100)")
    if not s.get("funding_ok", True):
        cautions.append("Funding rate data unavailable")

    if aborts:
        return "ABORT", "high", aborts + cautions
    if len(cautions) >= 2:
        return "ABORT", "high", cautions
    if len(cautions) == 1:
        return "CAUTION", "medium", cautions
    return "PROCEED", "low", []


def _format(s: dict, verdict: str, risk: str, flags: list[str], cfg: Config) -> str:
    icon = {"PROCEED": "✅", "CAUTION": "⚠️", "ABORT": "🛑"}[verdict]
    flag_block = "\n".join(f"  • {f}" for f in flags) if flags else "  (none)"
    summary = (
        f"Funding {s['funding_avg_pct']:+.4f}%/8h ({s['funding_regime']}, "
        f"~{s['funding_annualized']:.0f}% APY) | OI {s['oi_change_24h_pct']:+.1f}%/24h | "
        f"L/S {s['long_short_ratio']:.2f} | F&G {s['fear_greed_value']} ({s['fear_greed_label']}) | "
        f"Whale ${s['whale_volume_usd']:,.0f}"
    )
    payload = {
        "verdict": verdict.lower(),
        "risk_level": risk,
        "asset": cfg.asset,
        "signals": s,
        "flags": flags,
        "cost": s["cost"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return (
        f"{icon} Pre-Trade Check: {verdict}\n"
        f"Asset: {cfg.asset} | Risk: {risk.upper()} | Cost: {s['cost']}\n\n"
        f"Market: {summary}\n\n"
        f"Flags:\n{flag_block}\n\n"
        f"--- JSON ---\n{json.dumps(payload, default=str)}"
    )
