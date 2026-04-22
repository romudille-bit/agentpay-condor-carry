# Demo — first-tick snapshot

Placeholder. Run one dry-run tick end-to-end against the production AgentPay gateway and paste the snapshot here so anyone landing on this repo can see what a healthy tick looks like before they install anything.

## What to capture

1. The rendered prompt Condor hands the LLM (market context + routine list).
2. The `pre_trade_check` tool call + its full JSON verdict block.
3. The `funding_carry` tool call + its `action` + reasoning.
4. The LLM's final decision (spawn executor / hold / skip).
5. The journal entry the agent appends at end of tick, including total AgentPay cost.

## How to get the snapshot

After following [SETUP.md](../SETUP.md) through step 6 and running one `dry_run` session:

```bash
cat ~/condor/trading_agents/funding_carry_v1/sessions/session_1/snapshots/snapshot_1.md
```

Paste the output below this line.

---

(snapshot goes here)
