"""Backtest harness: replay resolved markets through XGBoost, write settled trades."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    import aiosqlite

    from bot.config import RuntimeSettings

# Lazy-import skill scripts already on sys.path via ensure_skill_script_paths()
def xgb_infer(features: dict[str, Any], model_path: Path) -> tuple[float, str, dict]:
    from infer_xgboost import infer_probability
    return infer_probability(features, model_path=model_path)


FEATURE_COLS = [
    "current_mid", "spread", "volume_24h", "days_to_resolution",
    "narrative_score", "momentum_1h", "momentum_24h",
]


async def run_backtest(
    conn: aiosqlite.Connection,
    df: pd.DataFrame,
    *,
    model_path: Path,
    settings: RuntimeSettings,
) -> dict[str, Any]:
    """Replay resolved markets from df, write immediately-settled paper trades.

    Each row becomes one trade if xgb_prob - current_mid > edge_threshold.
    Fill price = current_mid + spread/2 (simplified, no walk-the-book).
    PnL = (final_price - fill_price) * size, where final_price = 1.0 (YES) or 0.0 (NO).
    """
    from bot.storage.models import FlaggedMarket, Prediction, Trade
    from bot.storage.repo import close_trade, insert_flagged_market, insert_prediction, insert_trade

    # Idempotent: clear any previous backtest rows so re-runs start fresh.
    await conn.execute("DELETE FROM trades WHERE condition_id LIKE 'bt_%'")
    await conn.execute("DELETE FROM predictions WHERE condition_id LIKE 'bt_%'")
    await conn.execute("DELETE FROM markets_flagged WHERE condition_id LIKE 'bt_%'")
    await conn.commit()

    trades_written = 0
    win_count = 0
    rows_skipped = 0
    now = int(time.time())

    for idx, row in df.iterrows():
        mid = float(row.get("current_mid", 0.5))
        spread = float(row.get("spread", 0.02))
        label = int(row.get("label", 0))

        features = {col: float(row.get(col, 0.0)) for col in FEATURE_COLS}
        xgb_prob, _, _imp = xgb_infer(features, model_path)
        edge = xgb_prob - mid

        if edge <= settings.edge_threshold:
            rows_skipped += 1
            continue

        cid = f"bt_{idx:07d}"
        token = f"bt_tok_{idx:07d}"

        # Write market record (required FK for trade)
        await insert_flagged_market(
            conn,
            FlaggedMarket(
                condition_id=cid,
                yes_token=token,
                no_token=f"bt_no_{idx:07d}",
                mid_price=mid,
                spread=spread,
                volume_24h=float(row.get("volume_24h", 0.0)),
                flagged_at=now,
            ),
        )

        pred_id = await insert_prediction(
            conn,
            Prediction(
                condition_id=cid,
                token_id=token,
                p_model=xgb_prob,
                p_market=mid,
                edge=edge,
                components={"xgb_prob": xgb_prob, "backtest": True},
                created_at=now,
            ),
        )

        fill_price = mid + spread / 2.0
        size = max(1.0, settings.bankroll_usdc * settings.max_position_pct * settings.kelly_fraction * edge)
        trade_id = await insert_trade(
            conn,
            Trade(
                condition_id=cid,
                token_id=token,
                side="BUY",
                size=size,
                limit_price=fill_price,
                fill_price=fill_price,
                slippage=spread / 2.0,
                intended_size=size,
                is_paper=True,
                prediction_id=pred_id,
                opened_at=now,
                source="backtest",
            ),
        )

        final_price = 1.0 if label == 1 else 0.0
        outcome = "YES" if label == 1 else "NO"
        pnl = (final_price - fill_price) * size
        await close_trade(conn, trade_id, pnl=pnl, outcome=outcome, closed_at=now)

        trades_written += 1
        if outcome == "YES":
            win_count += 1

    win_rate = win_count / trades_written if trades_written > 0 else 0.0
    return {
        "trades_written": trades_written,
        "win_count": win_count,
        "win_rate": win_rate,
        "rows_skipped": rows_skipped,
    }
