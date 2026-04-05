"""
VMA Analyzer v5.504
- SQLite + JSONL dual source
- Equity curve / DD curve / distribution charts
- EV dynamics with regime detection
- Time-of-day / council label / exit reason / slippage breakdown
- Single-file 7-panel PNG report output
- r_multiple/risk_pips は本体(vma_bot.py)の保存値を読むだけ（真実は1か所）
"""

import os
import sys
import json
import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, Any

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import FuncFormatter
from scipy.signal import savgol_filter
from scipy.stats import skew, kurtosis

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================================
# 定数
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADE_RESULTS_FILE = os.path.join(BASE_DIR, "trade_results.jsonl")
DB_FILE = os.path.join(BASE_DIR, "vma.db")
REPORT_IMAGE = os.path.join(BASE_DIR, "vma_analyzer_report.png")
REPORT_TEXT = os.path.join(BASE_DIR, "vma_analyzer_report.txt")

MIN_ANALYSIS_TRADES = 10
EV_WINDOW = 15
BOOTSTRAP_N = 3000
BASE_RISK_PCT = 0.02  # VMA基本リスク2%

# ============================================================================
# データ読み込み（SQLite優先 → JSONL fallback）
# ============================================================================

def load_trades_from_db() -> pd.DataFrame:
    """SQLiteから取得"""
    if not os.path.exists(DB_FILE):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        rows = conn.execute("SELECT data FROM trades ORDER BY id ASC").fetchall()
        conn.close()
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame([json.loads(r[0]) for r in rows])
    except Exception as e:
        print(f"  DB読み込み失敗: {e}")
        return pd.DataFrame()


def load_jsonl(filepath: str) -> pd.DataFrame:
    if not os.path.exists(filepath):
        return pd.DataFrame()
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    return pd.DataFrame(records)


def load_trades() -> pd.DataFrame:
    """SQLite → JSONL の優先順で読み込み、重複排除"""
    db_df = load_trades_from_db()
    jsonl_df = load_jsonl(TRADE_RESULTS_FILE)

    if db_df.empty and jsonl_df.empty:
        return pd.DataFrame()

    if not db_df.empty and not jsonl_df.empty:
        combined = pd.concat([db_df, jsonl_df], ignore_index=True)
        if "ticket" in combined.columns:
            combined = combined.drop_duplicates(subset=["ticket"], keep="first")
        return combined
    return db_df if not db_df.empty else jsonl_df


def load_council_logs() -> pd.DataFrame:
    """
    合議ログ読み込み（VMA: SQLite council_logsテーブルのみ）
    カラム: label, action, data(JSON), created_at
    """
    if not os.path.exists(DB_FILE):
        return pd.DataFrame()

    try:
        conn = sqlite3.connect(DB_FILE, timeout=5)
        rows = conn.execute(
            "SELECT label, action, data, created_at FROM council_logs ORDER BY id ASC"
        ).fetchall()
        conn.close()
        if not rows:
            return pd.DataFrame()
        records = []
        for r in rows:
            entry = {"label": r[0], "action": r[1], "created_at": r[3], "timestamp": r[3]}
            try:
                entry["data"] = json.loads(r[2]) if r[2] else {}
            except Exception:
                entry["data"] = {}
            records.append(entry)
        return pd.DataFrame(records)
    except Exception as e:
        print(f"  Council logs読み込み失敗: {e}")
        return pd.DataFrame()


# ============================================================================
# 前処理
# ============================================================================

def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    # timestamp
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    elif "entry_time" in df.columns:
        df["timestamp"] = pd.to_datetime(df["entry_time"], errors="coerce")

    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    # 必須カラム確認・補完
    for col, default in [
        ("result_pips", 0.0), ("risk_pips", 5.0), ("r_multiple", 0.0),
        ("win", False), ("hold_minutes", 0.0), ("slippage_pips", 0.0),
        ("direction", "UNKNOWN"), ("council_label", ""),
        ("exit_reason", "unknown"), ("max_favorable_pips", 0.0),
        ("max_adverse_pips", 0.0),
    ]:
        if col not in df.columns:
            df[col] = default

    # ★r_multipleは本体(vma_bot.py)が約定時に計算・保存した値をそのまま使う。
    # analyzer側で再計算しない（真実は1か所）。
    # 旧レコード互換: r_multipleが0かつresult_pipsが非0なら、risk_pipsから復元を試みる
    mask = (df["r_multiple"] == 0) & (df["result_pips"] != 0) & (df["risk_pips"] > 0)
    if mask.any():
        df.loc[mask, "r_multiple"] = df.loc[mask, "result_pips"] / df.loc[mask, "risk_pips"]

    # win を bool 化（result_pips基準）
    df["win"] = df["result_pips"] > 0

    # 時間帯カラム
    df["hour"] = df["timestamp"].dt.hour
    df["weekday"] = df["timestamp"].dt.day_name()
    df["date"] = df["timestamp"].dt.date

    return df


# ============================================================================
# 統計エンジン
# ============================================================================

def calc_core_stats(df: pd.DataFrame) -> Dict[str, Any]:
    """主要統計量"""
    r = df["r_multiple"].values
    n = len(r)
    wins = np.sum(r > 0)
    losses = np.sum(r <= 0)

    win_r = r[r > 0]
    loss_r = r[r <= 0]

    def max_streak(arr):
        mx, cur = 0, 0
        for v in arr:
            if v:
                cur += 1
                mx = max(mx, cur)
            else:
                cur = 0
        return mx

    # Equity curve (R-based)
    cum_r = np.cumsum(r)
    peak = np.maximum.accumulate(cum_r)
    dd_r = peak - cum_r
    max_dd_r = np.max(dd_r) if len(dd_r) > 0 else 0.0

    # Capital curve (compound)
    capital = np.ones(n + 1)
    for i, rv in enumerate(r):
        capital[i + 1] = capital[i] * max(0.0, 1.0 + rv * BASE_RISK_PCT)
    cap_peak = np.maximum.accumulate(capital[1:])
    cap_dd = (cap_peak - capital[1:]) / np.where(cap_peak > 0, cap_peak, 1.0)

    stats = {
        "total_trades": n,
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": wins / n if n > 0 else 0.0,
        "total_r": float(np.sum(r)),
        "avg_r": float(np.mean(r)) if n > 0 else 0.0,
        "median_r": float(np.median(r)) if n > 0 else 0.0,
        "std_r": float(np.std(r)) if n > 0 else 0.0,
        "skew_r": float(skew(r)) if n >= 8 else 0.0,
        "kurtosis_r": float(kurtosis(r)) if n >= 8 else 0.0,
        "avg_win_r": float(np.mean(win_r)) if len(win_r) > 0 else 0.0,
        "avg_loss_r": float(np.mean(loss_r)) if len(loss_r) > 0 else 0.0,
        "profit_factor": (
            abs(np.sum(win_r) / np.sum(loss_r))
            if len(loss_r) > 0 and np.sum(loss_r) != 0 else float("inf")
        ),
        "max_win_r": float(np.max(r)) if n > 0 else 0.0,
        "max_loss_r": float(np.min(r)) if n > 0 else 0.0,
        "max_dd_r": float(max_dd_r),
        "max_dd_pct": float(np.max(cap_dd)) if len(cap_dd) > 0 else 0.0,
        "max_win_streak": max_streak(r > 0),
        "max_loss_streak": max_streak(r <= 0),
        "sharpe_r": (
            float(np.mean(r) / np.std(r)) if n > 1 and np.std(r) > 0 else 0.0
        ),
        "avg_hold_min": float(df["hold_minutes"].mean()) if "hold_minutes" in df.columns else 0.0,
        "cum_r": cum_r,
        "capital_curve": capital,
        "capital_dd": cap_dd,
    }
    return stats


def bootstrap_expected_r(r_values: np.ndarray, n_boot: int = BOOTSTRAP_N) -> Dict[str, float]:
    """Bootstrap confidence interval for expected R"""
    if len(r_values) < 20:
        return {"ev_mean": float(np.mean(r_values)), "ev_5pct": 0.0, "ev_95pct": 0.0}

    means = []
    for _ in range(n_boot):
        sample = np.random.choice(r_values, size=len(r_values), replace=True)
        means.append(np.mean(sample))

    means = np.array(means)
    return {
        "ev_mean": float(np.mean(means)),
        "ev_5pct": float(np.percentile(means, 5)),
        "ev_95pct": float(np.percentile(means, 95)),
    }


# ============================================================================
# 分析モジュール
# ============================================================================

def analyze_by_label(df: pd.DataFrame, out: list):
    """合議ラベル別（状態A/B/C）"""
    if "council_label" not in df.columns or df["council_label"].isna().all():
        return

    out.append("\n■ Performance by Council Label")
    out.append(f"{'Label':>10s} {'N':>5s} {'WR':>7s} {'AvgR':>7s} {'TotalR':>8s}")

    for label in sorted(df["council_label"].dropna().unique()):
        sub = df[df["council_label"] == label]
        if len(sub) < 3:
            continue
        wr = (sub["r_multiple"] > 0).mean()
        ar = sub["r_multiple"].mean()
        tr = sub["r_multiple"].sum()
        out.append(f"  {label:>8s} {len(sub):5d} {wr:6.1%} {ar:+6.2f}  {tr:+7.2f}")


def analyze_by_direction(df: pd.DataFrame, out: list):
    """BUY/SELL別"""
    if "direction" not in df.columns:
        return

    out.append("\n■ Performance by Direction")
    out.append(f"{'Dir':>6s} {'N':>5s} {'WR':>7s} {'AvgR':>7s}")

    for d in ["BUY", "SELL"]:
        sub = df[df["direction"] == d]
        if len(sub) == 0:
            continue
        wr = (sub["r_multiple"] > 0).mean()
        ar = sub["r_multiple"].mean()
        out.append(f"  {d:>4s} {len(sub):5d} {wr:6.1%} {ar:+6.2f}")


def analyze_by_hour(df: pd.DataFrame, out: list):
    """時間帯別"""
    if "hour" not in df.columns:
        return

    out.append("\n■ Performance by Hour (JST)")
    out.append(f"{'Hour':>6s} {'N':>5s} {'WR':>7s} {'AvgR':>7s}")

    for h in sorted(df["hour"].unique()):
        sub = df[df["hour"] == h]
        if len(sub) < 3:
            continue
        wr = (sub["r_multiple"] > 0).mean()
        ar = sub["r_multiple"].mean()
        out.append(f"  {h:4d}h {len(sub):5d} {wr:6.1%} {ar:+6.2f}")


def analyze_exit_reasons(df: pd.DataFrame, out: list):
    """決済理由別"""
    if "exit_reason" not in df.columns:
        return

    out.append("\n■ Performance by Exit Reason")
    out.append(f"{'Reason':>16s} {'N':>5s} {'WR':>7s} {'AvgR':>7s}")

    for reason in sorted(df["exit_reason"].dropna().unique()):
        sub = df[df["exit_reason"] == reason]
        if len(sub) == 0:
            continue
        wr = (sub["r_multiple"] > 0).mean()
        ar = sub["r_multiple"].mean()
        out.append(f"  {reason:>14s} {len(sub):5d} {wr:6.1%} {ar:+6.2f}")


def analyze_slippage(df: pd.DataFrame, out: list):
    """スリッページ分析"""
    if "slippage_pips" not in df.columns:
        return

    slips = df["slippage_pips"].dropna()
    if len(slips) < 5:
        return

    out.append("\n■ Slippage Analysis")
    out.append(f"  Mean   : {slips.mean():+.2f} pips")
    out.append(f"  Median : {slips.median():+.2f} pips")
    out.append(f"  Std    : {slips.std():.2f} pips")
    out.append(f"  Max    : {slips.max():+.2f} pips")
    out.append(f"  > 2pips: {(slips.abs() > 2).sum()} / {len(slips)}")


def analyze_ev_dynamics(df: pd.DataFrame, out: list) -> Optional[pd.DataFrame]:
    """EV推移 + レジーム判定"""
    sub = df.dropna(subset=["r_multiple"]).copy()
    if len(sub) < EV_WINDOW + 5:
        return None

    sub = sub.sort_values("timestamp").reset_index(drop=True)

    # Rolling metrics
    sub["rolling_ev"] = sub["r_multiple"].rolling(EV_WINDOW).mean()
    sub["rolling_std"] = sub["r_multiple"].rolling(EV_WINDOW).std()
    sub["rolling_sharpe"] = sub["rolling_ev"] / (sub["rolling_std"] + 1e-9)

    # Gradient (Savitzky-Golay or simple diff)
    ev_vals = sub["rolling_ev"].dropna().values
    if len(ev_vals) > 11:
        wl = min(11, len(ev_vals) - (1 if len(ev_vals) % 2 == 0 else 0))
        if wl < 5:
            wl = 5
        if wl % 2 == 0:
            wl -= 1
        try:
            smooth = savgol_filter(ev_vals, window_length=wl, polyorder=min(2, wl - 1))
            grad = np.gradient(smooth)
            sub.loc[sub["rolling_ev"].dropna().index, "ev_gradient"] = grad
        except Exception:
            sub["ev_gradient"] = sub["rolling_ev"].diff(3) / 3.0
    else:
        sub["ev_gradient"] = sub["rolling_ev"].diff(3) / 3.0

    # Latest values
    latest = sub.dropna(subset=["rolling_ev", "ev_gradient", "rolling_sharpe"]).iloc[-1]
    ev = latest["rolling_ev"]
    grad = latest["ev_gradient"]
    sharpe = latest["rolling_sharpe"]

    out.append("\n■ EV Dynamics")
    out.append(f"  Rolling EV (w={EV_WINDOW}) : {ev:+.4f}")
    out.append(f"  EV Gradient              : {grad:+.5f}")
    out.append(f"  Rolling Sharpe           : {sharpe:.3f}")

    # Regime detection — Hybrid Defense-Attack Logic
    out.append("\n■ Regime Detection")

    attack_score = 0
    if ev > 0:
        attack_score += 1
    if grad > 0:
        attack_score += 1
    if sharpe > 0.7:
        attack_score += 1

    if attack_score >= 2:
        out.append("  🔥 ATTACK-HYBRID — EV条件複合成立")
    elif ev > 0:
        out.append("  ⚠️ HYBRID CAUTION — EV正だが不安定")
    elif grad > 0:
        out.append("  🔄 HYBRID RECOVERING")
    else:
        out.append("  🛑 DEFENSE LOCK — 低エントロピー領域")

    return sub


def analyze_mfe_mae(df: pd.DataFrame, out: list):
    """最大有利幅 / 最大逆行幅"""
    if "max_favorable_pips" not in df.columns or "max_adverse_pips" not in df.columns:
        return

    mfe = df["max_favorable_pips"].dropna()
    mae = df["max_adverse_pips"].dropna()
    if len(mfe) < 5:
        return

    out.append("\n■ MFE / MAE Analysis")
    out.append(f"  MFE Mean : {mfe.mean():.1f} pips | Median : {mfe.median():.1f}")
    out.append(f"  MAE Mean : {mae.mean():.1f} pips | Median : {mae.median():.1f}")

    wins_df = df[df["r_multiple"] > 0]
    if len(wins_df) > 3:
        captured = wins_df["result_pips"] / (wins_df["max_favorable_pips"] + 1e-9)
        out.append(f"  Profit Capture (wins) : {captured.mean():.1%}")

    losses_df = df[df["r_multiple"] <= 0]
    if len(losses_df) > 3 and "risk_pips" in df.columns:
        overrun = losses_df["max_adverse_pips"] / (losses_df["risk_pips"] + 1e-9)
        out.append(f"  MAE / Risk (losses)   : {overrun.mean():.2f}x")


# ============================================================================
# 可視化
# ============================================================================

def generate_report_chart(df: pd.DataFrame, stats: Dict, ev_df: Optional[pd.DataFrame]):
    """7パネルPNGレポート"""

    fig = plt.figure(figsize=(18, 14), facecolor="white")
    fig.suptitle(
        f"VMA Performance Report — {len(df)} trades "
        f"({df['timestamp'].min().strftime('%Y-%m-%d')} → "
        f"{df['timestamp'].max().strftime('%Y-%m-%d')})",
        fontsize=14, fontweight="bold", y=0.98,
    )

    gs = gridspec.GridSpec(3, 3, hspace=0.35, wspace=0.3, top=0.93, bottom=0.05)

    # --- Panel 1: Equity Curve ---
    ax1 = fig.add_subplot(gs[0, 0:2])
    cum_r = stats["cum_r"]
    ax1.plot(range(len(cum_r)), cum_r, color="#2196F3", linewidth=1.5, label="Cumulative R")
    ax1.fill_between(range(len(cum_r)), cum_r, 0, alpha=0.1, color="#2196F3")
    ax1.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax1.set_title("Equity Curve (Cumulative R)", fontsize=11)
    ax1.set_xlabel("Trade #")
    ax1.set_ylabel("R-multiple")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    # --- Panel 2: Stats Box ---
    ax2 = fig.add_subplot(gs[0, 2])
    ax2.axis("off")
    pf_display = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] < 100 else "∞"
    info_lines = [
        f"Total Trades: {stats['total_trades']}",
        f"Win Rate: {stats['win_rate']:.1%}",
        f"Total R: {stats['total_r']:+.2f}",
        f"Avg R: {stats['avg_r']:+.3f}",
        f"Sharpe(R): {stats['sharpe_r']:.3f}",
        f"Profit Factor: {pf_display}",
        f"Max DD (R): {stats['max_dd_r']:.2f}",
        f"Max DD (%): {stats['max_dd_pct']:.1%} (2%固定)",
        f"",
        f"Avg Win R: {stats['avg_win_r']:+.2f}",
        f"Avg Loss R: {stats['avg_loss_r']:+.2f}",
        f"Max Streak W/L: {stats['max_win_streak']}/{stats['max_loss_streak']}",
        f"Skew: {stats['skew_r']:+.2f}",
        f"Kurtosis: {stats['kurtosis_r']:.2f}",
        f"Avg Hold: {stats['avg_hold_min']:.0f}min",
    ]
    ax2.text(
        0.05, 0.95, "\n".join(info_lines),
        transform=ax2.transAxes, fontsize=9, verticalalignment="top",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="#f5f5f5", edgecolor="#cccccc"),
    )
    ax2.set_title("Key Statistics", fontsize=11)

    # --- Panel 3: R-multiple Distribution ---
    ax3 = fig.add_subplot(gs[1, 0])
    r_vals = df["r_multiple"].values
    bins = np.linspace(max(-5, r_vals.min()), min(5, r_vals.max()), 30)
    ax3.hist(r_vals, bins=bins, color="#4CAF50", alpha=0.7, edgecolor="white")
    ax3.axvline(np.mean(r_vals), color="red", linewidth=1.5, linestyle="--", label=f"Mean={np.mean(r_vals):+.2f}")
    ax3.axvline(0, color="black", linewidth=0.8)
    ax3.set_title("R-Multiple Distribution", fontsize=11)
    ax3.set_xlabel("R-multiple")
    ax3.legend(fontsize=8)
    ax3.grid(True, alpha=0.3)

    # --- Panel 4: Capital Drawdown (固定リスク基準) ---
    ax4 = fig.add_subplot(gs[1, 1])
    cap_dd = stats["capital_dd"]
    ax4.fill_between(range(len(cap_dd)), -cap_dd * 100, 0, color="#F44336", alpha=0.4)
    ax4.plot(range(len(cap_dd)), -cap_dd * 100, color="#F44336", linewidth=1.0)
    ax4.set_title("Capital Drawdown (固定2%基準・正規化)", fontsize=11)
    ax4.set_xlabel("Trade #")
    ax4.set_ylabel("DD %")
    ax4.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.1f}%"))
    ax4.grid(True, alpha=0.3)

    # --- Panel 5: Win Rate by Hour ---
    ax5 = fig.add_subplot(gs[1, 2])
    if "hour" in df.columns:
        hourly = df.groupby("hour").agg(
            n=("r_multiple", "count"),
            wr=("win", "mean"),
        )
        hourly = hourly[hourly["n"] >= 3]
        if not hourly.empty:
            colors = ["#4CAF50" if w >= 0.5 else "#F44336" for w in hourly["wr"]]
            ax5.bar(hourly.index, hourly["wr"] * 100, color=colors, alpha=0.7, edgecolor="white")
            ax5.axhline(50, color="gray", linewidth=0.8, linestyle="--")
            ax5.set_xlabel("Hour (JST)")
            ax5.set_ylabel("Win Rate %")
    ax5.set_title("Win Rate by Hour", fontsize=11)
    ax5.grid(True, alpha=0.3)

    # --- Panel 6: EV Dynamics ---
    ax6 = fig.add_subplot(gs[2, 0:2])
    if ev_df is not None and "rolling_ev" in ev_df.columns:
        ev_sub = ev_df.dropna(subset=["rolling_ev"])
        x = range(len(ev_sub))
        ax6.plot(x, ev_sub["rolling_ev"].values, color="#2196F3", linewidth=1.5, label="Rolling EV")
        ax6.fill_between(x, ev_sub["rolling_ev"].values, 0, alpha=0.1, color="#2196F3")
        if "ev_gradient" in ev_sub.columns:
            grad = ev_sub["ev_gradient"].dropna()
            ax6_twin = ax6.twinx()
            ax6_twin.plot(range(len(grad)), grad.values, color="#FF9800", linewidth=1.0, alpha=0.7, label="Gradient")
            ax6_twin.set_ylabel("Gradient", fontsize=9, color="#FF9800")
            ax6_twin.tick_params(axis="y", labelcolor="#FF9800")
        ax6.axhline(0, color="gray", linewidth=0.5, linestyle="--")
        ax6.legend(fontsize=8, loc="upper left")
    ax6.set_title(f"EV Dynamics (window={EV_WINDOW})", fontsize=11)
    ax6.set_xlabel("Trade #")
    ax6.set_ylabel("Rolling EV")
    ax6.grid(True, alpha=0.3)

    # --- Panel 7: Exit Reason Breakdown ---
    ax7 = fig.add_subplot(gs[2, 2])
    if "exit_reason" in df.columns:
        reason_stats = df.groupby("exit_reason").agg(
            n=("r_multiple", "count"),
            avg_r=("r_multiple", "mean"),
        ).sort_values("n", ascending=True)
        if not reason_stats.empty:
            colors = ["#4CAF50" if r >= 0 else "#F44336" for r in reason_stats["avg_r"]]
            ax7.barh(reason_stats.index, reason_stats["avg_r"], color=colors, alpha=0.7, edgecolor="white")
            ax7.axvline(0, color="gray", linewidth=0.8)
            ax7.set_xlabel("Avg R-multiple")
            for i, (idx, row) in enumerate(reason_stats.iterrows()):
                ax7.text(row["avg_r"], i, f" n={int(row['n'])}", va="center", fontsize=8)
    ax7.set_title("Avg R by Exit Reason", fontsize=11)
    ax7.grid(True, alpha=0.3)

    plt.savefig(REPORT_IMAGE, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close("all")
    print(f"\n  📊 Chart saved: {REPORT_IMAGE}")


# ============================================================================
# メイン
# ============================================================================

def analyze_performance():
    print("=" * 64)
    print("  VMA Analyzer v5.504")
    print("=" * 64)

    # --- Load ---
    raw_df = load_trades()
    council_df = load_council_logs()

    if raw_df.empty:
        print("\n  トレード履歴なし。終了。")
        return

    print(f"\n  Data: {len(raw_df)} trades loaded")
    if not council_df.empty:
        print(f"  Council logs: {len(council_df)} entries")

    df = preprocess(raw_df)

    if len(df) < 3:
        print("  有効なトレードが不足。終了。")
        return

    # --- Core Stats ---
    stats = calc_core_stats(df)
    out: List[str] = []

    out.append("\n■ Core Statistics")
    out.append(f"  Trades       : {stats['total_trades']} ({stats['wins']}W / {stats['losses']}L)")
    out.append(f"  Win Rate     : {stats['win_rate']:.1%}")
    out.append(f"  Total R      : {stats['total_r']:+.2f}")
    out.append(f"  Avg R        : {stats['avg_r']:+.4f}")
    out.append(f"  Median R     : {stats['median_r']:+.4f}")
    out.append(f"  Std R        : {stats['std_r']:.4f}")
    out.append(f"  Sharpe (R)   : {stats['sharpe_r']:.3f}")
    pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] < 100 else "∞"
    out.append(f"  Profit Factor: {pf_str}")
    out.append(f"  Max DD (R)   : {stats['max_dd_r']:.2f}")
    out.append(f"  Max DD (%)   : {stats['max_dd_pct']:.1%} (固定2%基準・正規化)")
    out.append(f"  Avg Win R    : {stats['avg_win_r']:+.2f}")
    out.append(f"  Avg Loss R   : {stats['avg_loss_r']:+.2f}")
    out.append(f"  Skew / Kurt  : {stats['skew_r']:+.2f} / {stats['kurtosis_r']:.2f}")
    out.append(f"  Max Streak   : {stats['max_win_streak']}W / {stats['max_loss_streak']}L")
    out.append(f"  Avg Hold     : {stats['avg_hold_min']:.0f} min")

    # --- Bootstrap ---
    if len(df) >= 20:
        boot = bootstrap_expected_r(df["r_multiple"].values)
        out.append(f"\n  Bootstrap EV : {boot['ev_mean']:+.4f} [{boot['ev_5pct']:+.4f}, {boot['ev_95pct']:+.4f}] (90% CI)")

    # --- Breakdowns ---
    analyze_by_label(df, out)
    analyze_by_direction(df, out)
    analyze_by_hour(df, out)
    analyze_exit_reasons(df, out)
    analyze_slippage(df, out)
    analyze_mfe_mae(df, out)

    # --- EV Dynamics ---
    ev_df = analyze_ev_dynamics(df, out)

    # --- Print all ---
    report_text = "\n".join(out)
    print(report_text)

    # --- Save text report ---
    try:
        with open(REPORT_TEXT, "w", encoding="utf-8") as f:
            f.write(f"VMA Analyzer v5.504 — {datetime.now().isoformat()}\n")
            f.write("=" * 64 + "\n")
            f.write(report_text)
            f.write("\n")
        print(f"\n  📄 Text report saved: {REPORT_TEXT}")
    except Exception as e:
        print(f"  テキストレポート保存失敗: {e}")

    # --- Chart ---
    try:
        generate_report_chart(df, stats, ev_df)
    except Exception as e:
        print(f"  チャート生成失敗: {e}")

    print("\n" + "=" * 64)


if __name__ == "__main__":
    analyze_performance()