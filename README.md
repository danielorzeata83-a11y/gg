# Polymarket Copy-Trading Bot

A four-phase pipeline that discovers alpha wallets on Polymarket, watches their on-chain trades in real time, and optionally copies them with configurable risk controls.

## Architecture

```
discover_alpha.py  -->  alpha_wallets.json
                              |
                       watch_onchain.py  (Polygon logs, block time ~2s)
                              |
                        decision.py  (slippage, cooldown, risk gates)
                              |
                    executor_paper.py  OR  executor_live.py
                              |
                          ledger.jsonl
                              |
                     api_server.py + dashboard.html
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your RPC URL
```

### Environment variables (`.env`)

```
POLYGON_RPC_URL=wss://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
BANKROLL_USDC=500

# Live mode only:
PRIVATE_KEY=0x...
DEPOSIT_WALLET_ADDRESS=0x...
CLOB_API_KEY=...
CLOB_API_SECRET=...
CLOB_API_PASSPHRASE=...
I_UNDERSTAND_REAL_MONEY=yes
```

## Phase 1 — Discover Alpha Wallets

```bash
python discover_alpha.py --pool 100 --top 20 --out alpha_wallets.json
```

This pulls the Polymarket leaderboard, profiles each wallet from their real closed positions, and scores them on a composite metric (realized PnL, win rate, breadth, concentration). Output: `alpha_wallets.json`.

Options:
- `--category` — OVERALL, POLITICS, SPORTS, CRYPTO, etc.
- `--period` — DAY, WEEK, MONTH, ALL
- `--min-markets` — minimum resolved markets to qualify (default 10)

## Phase 2 — Watch On-Chain

```bash
python watch_onchain.py --rpc $POLYGON_RPC_URL --watchlist alpha_wallets.json
```

Subscribes to `OrderFilled` logs from Polymarket's CTF Exchange contracts on Polygon. Prints signals as they arrive (block time ~2s). No money involved.

## Phase 3 — Paper Trading

```bash
python bot.py --mode paper --bankroll 1000 --rpc $POLYGON_RPC_URL
```

Simulates trades against the real live order book (no real orders placed). Every decision and simulated fill is recorded in `ledger.jsonl`. A summary prints every 5 minutes.

Run the dashboard to watch in real time:
```bash
python api_server.py --ledger ledger.jsonl --port 8080
# open http://localhost:8080
```

**Run at least 30 resolved paper trades before going live.**

Check your paper results:
```bash
python -c "from ledger import Ledger; Ledger('ledger.jsonl').print_summary('paper')"
```

## Phase 4 — Live Trading (Real Money)

The live gate requires ALL of the following:
1. `I_UNDERSTAND_REAL_MONEY=yes` in environment
2. At least 30 resolved paper trades in `ledger.jsonl`
3. `BANKROLL_USDC` > 0
4. Geoblock check passes (Polymarket is not available in all regions)
5. Interactive confirmation prompt

```bash
python bot.py --mode live --bankroll 500 --rpc $POLYGON_RPC_URL --really-send
```

Omit `--really-send` to run in dry-run mode (logs orders but does not post them).

## Configuration

All tunables are in `config.py` and can be overridden via environment variables:

| Env Var | Default | Description |
|---------|---------|-------------|
| `BANKROLL_USDC` | 0 | Your total USDC bankroll |
| `POSITION_FRACTION` | 0.02 | Fraction of bankroll per trade (2%) |
| `MAX_POSITION_USDC` | 50 | Hard cap per position |
| `MAX_SLIPPAGE` | 0.02 | Reject if fill price > alpha + this |
| `MAX_OPEN_POSITIONS` | 5 | Max concurrent positions |
| `MAX_TOTAL_EXPOSURE_USDC` | 200 | Max total open exposure |
| `MAX_DAILY_LOSS_USDC` | 50 | Daily loss circuit breaker |
| `COOLDOWN_SECONDS` | 300 | Per (wallet, market) cooldown |
| `MIN_PAPER_TRADES` | 30 | Paper trades required before live |

## Risk Warnings

- **This bot copies other traders. Past performance does not guarantee future results.**
- Prediction market prices are highly volatile and can go to 0 or 1 instantly on resolution.
- Copy-trading introduces additional latency vs. the alpha wallet. Slippage is real.
- Start with the smallest bankroll you are comfortable losing entirely.
- The paper-trading gate exists for a reason. Do not bypass it.
- Review `ledger.jsonl` and the dashboard before enabling live mode.
- Kill switch: the bot will cancel all open orders on SIGINT/SIGTERM in live mode.

## Tests

```bash
python -m pytest tests/ -v
```

## File Reference

| File | Purpose |
|------|---------|
| `discover_alpha.py` | Stage 1: wallet discovery & scoring |
| `watch_onchain.py` | Stage 2: real-time on-chain signal source |
| `config.py` | All configuration tunables |
| `ledger.py` | Append-only trade ledger (JSONL) |
| `decision.py` | Risk gates & trade sizing logic |
| `executor_paper.py` | Paper trade simulation |
| `executor_live.py` | Live CLOB order placement |
| `bot.py` | Main entrypoint / orchestrator |
| `api_server.py` | Flask API for the dashboard |
| `dashboard.html` | Web dashboard (dark theme, Chart.js) |
| `requirements.txt` | Python dependencies |
