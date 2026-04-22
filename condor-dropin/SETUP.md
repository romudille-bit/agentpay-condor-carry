# Funding Carry Agent — Condor Setup

End-to-end guide: install Condor from source, drop in the two AgentPay routines, and create the agent via Condor's agent menu. Written for a fresh machine — skip steps you've already done.

---

## 1. Prerequisites

You need:

- macOS or Linux
- Python 3.12+ (`python3 --version`)
- Git
- `uv` — Astral's Python package manager. Install with:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- A Telegram bot token (create one in @BotFather on Telegram, takes ~30 seconds)
- Your Telegram user ID (DM @userinfobot to get it)
- A Hummingbot Backend API running locally — Condor talks to this for trading calls. If you don't have it yet, clone `hummingbot/hummingbot` and follow its quickstart, or skip for now: the **routines** work without it (they only hit AgentPay), you just can't execute trades from the agent layer until the API is up.
- An LLM CLI for ACP — `claude-code` is Condor's default and what fede recommended. Install it separately so Condor can spawn agent sessions.

## 2. Clone and install Condor

```bash
cd ~
git clone https://github.com/hummingbot/condor.git
cd condor
make install
```

`make install` is interactive — it will ask for:
- Telegram bot token
- Admin user ID (your Telegram ID)
- OpenAI API key (optional — only needed if you want to use OpenAI models instead of Claude Code)

It writes `.env` and `config.yml` and installs dependencies via `uv`. **Do not use Docker** — fede confirmed in the Discord thread that the LLM/ACP connection doesn't work in the container yet.

## 3. Drop the AgentPay routines into Condor

From the root of this repo, copy both routine files into Condor's global `routines/` folder:

```bash
cp ./routines/pre_trade_check.py ~/condor/routines/
cp ./routines/funding_carry.py   ~/condor/routines/
```

Install the AgentPay SDK into Condor's virtualenv so the routines' `from agentpay import ...` resolves:

```bash
cd ~/condor
uv pip install agentpay-x402
```

## 4. Configure AgentPay secrets

Edit `~/condor/.env` and append:

```bash
# AgentPay — start on testnet so nothing costs real USDC
AGENTPAY_NETWORK=testnet
TEST_AGENT_SECRET_KEY=S...your_testnet_secret...   # from setup_wallet.py --testnet

# When you're ready for mainnet, switch to:
# AGENTPAY_NETWORK=mainnet
# STELLAR_SECRET_KEY=S...your_mainnet_secret...
```

If you don't have a testnet secret key, generate one with the AgentPay SDK (after you ran `uv pip install agentpay-x402` above):

```bash
python3 -c "from agentpay import faucet_wallet; w = faucet_wallet(); print('Secret:', w.secret_key); print('Public:', w.public_key)"
```

`faucet_wallet()` creates a fresh testnet keypair and funds it from the AgentPay testnet faucet (~0.05 USDC), enough for thousands of routine ticks. Paste the secret into `~/condor/.env`. If you'd rather fund manually, copy the public address into https://faucet.stellar.org.

## 5. Drop in the agent definition

Copy the agent folder into Condor's `trading_agents/`:

```bash
mkdir -p ~/condor/trading_agents
cp -r ./trading_agents/funding_carry_v1 ~/condor/trading_agents/funding_carry_v1
```

This gives Condor a `trading_agents/funding_carry_v1/agent.md` strategy file it will pick up automatically.

## 6. Start Condor and create the agent session

```bash
cd ~/condor
make run
```

Then in Telegram, message your bot:

1. `/routines` — Condor should list `pre_trade_check` and `funding_carry` among the available routines. Open each one, hit **Run**, confirm you see a verdict/signal printed. If either fails, fix it here before moving on — the agent layer just calls these.
2. `/agent` — opens the agent menu.
3. Select **Strategies** → you should see `funding_carry_v1` in the list (loaded from `agent.md`).
4. Select it → **New Session** → pick run mode:
   - `dry_run` first — one tick, no trading, just to see the LLM read the routines and respond.
   - `loop` later — ticks every `frequency_sec` (default 14400s = 4h, matching a funding window).

The LLM (Claude Code via ACP) will read `agent.md`, see it's supposed to call `pre_trade_check` and `funding_carry` each tick, invoke them via the `condor.manage_routines_run` MCP tool, then decide whether to spawn a Hummingbot executor. Snapshots land in `trading_agents/funding_carry_v1/sessions/session_N/snapshots/`.

## 7. First-tick sanity check

After the dry-run tick finishes, inspect:

```bash
cat ~/condor/trading_agents/funding_carry_v1/sessions/session_1/snapshots/snapshot_1.md
```

You should see:
- The rendered prompt with market context
- A tool call to `pre_trade_check`
- Its verdict/reasoning string
- A tool call to `funding_carry` (if pre-trade verdict wasn't ABORT)
- The LLM's final decision

If you see both routine calls and a coherent decision, you're done. Post a screenshot to the Condor Discord thread.

---

## Troubleshooting

| Issue | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'agentpay'` | `cd ~/condor && uv pip install agentpay-x402` |
| `No secret key found` when routine runs | `.env` missing `TEST_AGENT_SECRET_KEY` (testnet) or `STELLAR_SECRET_KEY` (mainnet) |
| Routine runs but returns `Budget exceeded` | Raise `PRETRADE_BUDGET` / `CARRY_BUDGET_PER_TICK` in `.env` (defaults are conservative) |
| Agent CLI not in PATH warning | Install `claude-code`: `npm install -g @anthropic-ai/claude-code`, or switch to `ollama:*` in `agent.md` frontmatter |
| Hummingbot API connection refused | Routines don't need it — only the agent's execution layer does. Skip for dry-run testing. |
