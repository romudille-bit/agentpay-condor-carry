"""Funding rate carry signal — long/short carry enter/exit decisions for Condor.

Drop-in Condor routine. Contract:
  - Pydantic `Config` class (auto-discovered)
  - async `run(config, context) -> str`

Pays ~$0.008 USDC per tick via AgentPay to gather funding, OI, F&G, whale activity.
Returns a directional decision the Condor agent reads to decide whether to spawn
a two-legged delta-neutral executor (spot + perp).

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

from pydantic import BaseModel, Field
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from agentpay import AgentWallet, Session, BudgetExceeded

log = logging.getLogger(__name__)

_GATEWAYS = {
    "mainnet": "https://gateway-production-2cc2.up.railway.app",
    "testnet": "https://gateway-testnet-production.up.railway.app",
}


class Config(BaseModel):
    """Funding rate carry trade signal — returns enter/exit/hold with direction."""

    asset: str = Field(default="ETH", description="Asset symbol (e.g. ETH, BTC)")
    max_spend_usd: float = Field(default=0.02, description="Max AgentPay spend per tick (USDC)")
    funding_enter_threshold_pct: float = Field(default=0.01, description="Min |funding| %/8h to enter")
    funding_exit_threshold_pct: float = Field(default=0.002, description="Exit when |funding| falls below")
    max_whale_vol_usd: float = Field(default=5_000_000, description="Abort/exit if whale volume exceeds")
    oi_min_change_pct: float = Field(default=-5.0, description="Skip if OI 24h change below this %")
    fg_long_min: int = Field(default=50, description="Min F&G to enter long carry")
    fg_long_max: int = Field(default=79, description="Max F&G to enter long carry (above = crowded)")
    fg_short_min: int = Field(default=21, description="Min F&G to enter short carry (below = crowded)")
    fg_short_max: int = Field(default=49, description="Max F&G to enter short carry")
    carry_position: bool = Field(default=False, description="Are we currently holding a carry position?")
    carry_direction: str = Field(default="", description="Current carry direction: long, short, or empty")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Run the funding carry signal. Returns a string the Condor agent can read."""
    network = os.environ.get("AGENTPAY_NETWORK", "testnet")
    gateway = os.environ.get("AGENTPAY_GATEWAY_URL", _GATEWAYS[network])
    secret = (
        os.environ.get("STELLAR_SECRET_KEY")
        or (os.environ.get("TEST_AGENT_SECRET_KEY") if network == "testnet" else None)
        or ""
    )
    if not secret:
        key_name = "TEST_AGENT_SECRET_KEY" if network == "testnet" else "STELLAR_SECRET_KEY"
        return f"ERROR\nNo {key_name} in ~/condor/.env — cannot gather signals."

    return await asyncio.to_thread(_run_sync, config, network, gateway, secret)


def _run_sync(config: Config, network: str, gateway: str, secret: str) -> str:
    wallet = AgentWallet(secret_key=secret, network=network)
    signals: dict = {}
    try:
        with Session(wallet, gateway_url=gateway, max_spend=str(config.max_spend_usd)) as session:
            signals = _gather(session, config.asset)
    except BudgetExceeded as e:
        return f"SKIP\nSignal budget exceeded: {e}"
    except Exception as e:
        log.exception("funding_carry: signal gathering failed")
        return f"ERROR\nSignal error: {e}"

    action, direction = _decide(signals, config)
    return _format(action, direction, signals, config)


def _gather(session: Session, asset: str) -> dict:
    out: dict = {}

    rates = session.call("funding_rates", {"asset": asset}).get("result", {})
    exchanges = rates.get("rates", rates.get("exchanges", []))
    if exchanges:
        avg = sum(e["funding_rate_pct"] for e in exchanges) / len(exchanges)
        out["funding_avg_pct"] = avg
        out["funding_annualized"] = avg * 3 * 365
    else:
        out["funding_avg_pct"] = 0.0
        out["funding_annualized"] = 0.0

    oi = session.call("open_interest", {"symbol": asset}).get("result", {})
    out["oi_change_24h_pct"] = oi.get("oi_change_24h_pct", 0)
    out["long_short_ratio"] = oi.get("long_short_ratio", 1.0)

    fg = session.call("fear_greed_index", {}).get("result", {})
    out["fear_greed_value"] = fg.get("value", 50)
    out["fear_greed_label"] = fg.get("value_classification", "Neutral")

    whales = session.call("whale_activity", {"token": asset, "min_usd": 500_000}).get("result", {})
    out["whale_volume_usd"] = whales.get("total_volume_usd", 0)

    out["cost"] = session.spent()
    return out


def _decide(s: dict, cfg: Config) -> tuple[str, str | None]:
    funding = s["funding_avg_pct"]
    oi = s["oi_change_24h_pct"]
    fg = s["fear_greed_value"]
    whale = s["whale_volume_usd"]

    # Hard abort — whale tail risk
    if whale >= cfg.max_whale_vol_usd:
        if cfg.carry_position:
            return f"exit_{cfg.carry_direction}_carry", cfg.carry_direction
        return "skip", None

    # Exit if funding collapses back near zero
    if cfg.carry_position and abs(funding) < cfg.funding_exit_threshold_pct:
        return f"exit_{cfg.carry_direction}_carry", cfg.carry_direction

    # Already positioned — hold
    if cfg.carry_position:
        return "hold", cfg.carry_direction

    # OI must not be collapsing
    if oi < cfg.oi_min_change_pct:
        return "skip", None

    # Long carry — positive funding + greed band
    if funding >= cfg.funding_enter_threshold_pct and cfg.fg_long_min <= fg <= cfg.fg_long_max:
        return "enter_long_carry", "long"

    # Short carry — negative funding + fear band
    if funding <= -cfg.funding_enter_threshold_pct and cfg.fg_short_min <= fg <= cfg.fg_short_max:
        return "enter_short_carry", "short"

    return "skip", None


def _format(action: str, direction: str | None, s: dict, cfg: Config) -> str:
    lean = (
        "long-biased" if s["funding_avg_pct"] > 0 else
        "short-biased" if s["funding_avg_pct"] < 0 else
        "neutral"
    )
    summary = (
        f"Funding {s['funding_avg_pct']:+.4f}%/8h (~{s['funding_annualized']:.0f}% APY) | "
        f"OI {s['oi_change_24h_pct']:+.1f}%/24h | L/S {s['long_short_ratio']:.2f} | "
        f"F&G {s['fear_greed_value']} ({s['fear_greed_label']}) | Whale ${s['whale_volume_usd']:,.0f}"
    )
    payload = {
        "action": action,
        "direction": direction,
        "lean": lean,
        "asset": cfg.asset,
        "signals": s,
        "cost": s["cost"],
    }
    return (
        f"Funding Carry Signal — {action.upper()}\n"
        f"Asset: {cfg.asset} | Direction: {direction or '—'} | Lean: {lean} | Cost: {s['cost']}\n\n"
        f"Market: {summary}\n\n"
        f"--- JSON ---\n{json.dumps(payload, default=str)}"
    )
