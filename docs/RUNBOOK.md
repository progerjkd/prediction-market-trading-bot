# Paper Trading Runbook

This runbook is for operating the v1 bot in paper mode only. It does not enable live trading, and the helper script forces `LIVE_TRADING=false` for daemon and status commands.

## One-Time Checks

From the repository root:

```bash
source .venv/bin/activate
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python -m bot.daemon --once --paper --mock-ai --max-markets 1
```

Optional live-data scan without predictions or trades:

```bash
.venv/bin/python -m bot.daemon --once --paper --scan-only --max-markets 10
```

## Start Paper Daemon In Tmux

```bash
scripts/paper-daemon start
scripts/paper-daemon attach
```

The tmux session defaults to `pm-bot` and opens three panes:

- daemon pane: runs `.venv/bin/python -m bot.daemon --paper --max-markets 10`
- log pane: tails `data/logs/paper-daemon.log`
- status pane: refreshes `.venv/bin/python -m bot.daemon --paper --status`

Useful environment overrides:

```bash
PM_BOT_TMUX_SESSION=pm-bot-1 MAX_MARKETS=25 scripts/paper-daemon start
BOT_DB_PATH=data/paper-run.sqlite scripts/paper-daemon status
```

## Monitor

Show current tmux and acceptance-gate status:

```bash
scripts/paper-daemon status
```

Tail daemon logs outside tmux:

```bash
scripts/paper-daemon logs
```

Inside tmux, use normal tmux controls:

```text
Ctrl-b d        detach
Ctrl-b arrow    move between panes
```

## Stop

Request graceful daemon shutdown:

```bash
scripts/paper-daemon stop
```

This creates `data/STOP`. The daemon finishes the current pass and exits before starting another pass.

After shutdown, close the tmux panes:

```bash
scripts/paper-daemon close
```

Before the next run, remove the STOP file deliberately:

```bash
scripts/paper-daemon clear-stop
```

`scripts/paper-daemon start` refuses to run while the STOP file exists.

## Paper Acceptance Gate

Check status at any time:

```bash
scripts/paper-daemon status
```

Live trading remains out of scope for v1. Do not change `RuntimeSettings.live_trading_enabled`; it must continue returning `False` even if `LIVE_TRADING=true` appears in the environment.
