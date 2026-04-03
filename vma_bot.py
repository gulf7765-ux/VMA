"""
VMA - VM Advance Trading System v5.300
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BB背景分析最優先 × Gemini合議 × Python側異常ガード

Architecture:
  - BotPhase状態マシン（遷移表ベース）
  - AnomalyGuard: スプレッド急拡大・ティック凍結・価格ジャンプをPython側で即時検知
  - Gemini 4ペルソナ合議（分析官/監査官/金庫番/議長）
  - 非線形R倍数トレーリングストップ
  - タイムストップ（撤退の美学）
  - SQLite + JSONL二重永続化
  - LINE通知
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import time
import datetime
import os
import sys
import platform
import threading
import math
import gc
import json
import logging
import logging.handlers
import re
import sqlite3
import signal
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple, Any

import MetaTrader5 as mt5
import requests
import PIL.Image
import pandas as pd
import numpy as np
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from google import genai
from dotenv import load_dotenv


# ============================================================================
# §1. 定数定義
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = "5.300"  # DDバイパスspread限定 + B'上書き + PostSignalGate

# --- 通貨ペア ---
SYMBOL = "USDJPY"
MAGIC_NUMBER = 20260403

# --- リスク管理（PhantomOS DD4層準拠）---
BASE_RISK_PERCENT = 0.02           # 標準リスク (2%)
MIN_SL_PIPS = 5.0                  # SL下限 (5pips)
SL_MAX_DISTANCE_PIPS = 80.0        # SL許容最大乖離 (pips)
DD_WARNING_PCT = 8.0               # DD4層: 警告 (8%)
DD_REDUCTION_PCT = 10.0            # DD4層: リスク半減 (10%)
DD_HALT_PCT = 15.0                 # DD4層: 新規停止 (15%)
DD_DISQUALIFY_PCT = 20.0           # DD4層: 全決済+停止 (20%)
DD_RECOVERY_PCT = 5.0              # 半減→復帰 (5%)
DAILY_LOSS_LIMIT_PCT = 3.0         # 日次損失上限 (3%)

# --- トレーリングストップ ---
TRAIL_START_PIPS = 20.0            # フォールバック開始利益 (pips)
TRAIL_STEP_PIPS = 1.0              # 最小更新幅 (pips)

# --- タイムストップ（撤退の美学）---
TIME_STOP_MINUTES = 180            # 3時間（M30で6本）
TIME_STOP_MIN_R = 1.0              # 要求最低含み益 (1.0R未満は切る)
TIME_STOP_EXCEPTION_ANGLE = 30.0   # パターンD免除角度

# --- トリガー閾値 ---
STATE_C_PROXIMITY_PIPS = 5.0       # 状態C接近判定 (pips)
STATE_B_MIN_THRESHOLD_PIPS = 25.0  # 状態B最低閾値 (pips)
STATE_B_ATR_MULTIPLIER = 3.0       # 状態B ATR倍率
STATE_B_VOLUME_RATIO = 1.5         # 状態B出来高倍率
STATE_B_BB_EXPANSION_PIPS = 5.0    # 状態B BB拡大判定 (pips)
STATE_B_FLAG_TTL_SECONDS = 600     # B'急変動フラグ寿命 (10分。初動の熱が冷めない範囲)

# --- エントリー ---
ENTRY_WINDOW_MINUTES = 5           # 確定後許可時間 (分)
MAX_TRADE_RETRIES = 3              # 注文リトライ回数
ORDER_DEVIATION = 50               # スリッページ許容 (ポイント)

# --- Python側異常ガード（APIを使わない即時判定）---
SPREAD_WARN_PIPS = 3.0             # スプレッド警告閾値 (pips)
SPREAD_BLOCK_PIPS = 5.0            # スプレッド エントリー禁止閾値 (pips)
TICK_FREEZE_SECONDS = 30           # ティック凍結検知 (秒)
PRICE_JUMP_PIPS = 30.0             # 瞬間価格ジャンプ検知 (pips)
ANOMALY_COOLDOWN_SECONDS = 60      # 異常検知後のクールダウン (秒)
ANOMALY_RECOVERY_TICKS = 10        # 復帰に必要な正常ティック連続回数
ANOMALY_ESCALATION_COUNT = 3       # 手動解除に格上げする再発回数
ANOMALY_ESCALATION_WINDOW = 1800   # 再発カウントのローリング窓 (30分)

# --- エントリー前関門（PostSignalGate）---
GATE_MAX_RISK_PCT = 0.03           # 1トレード最大リスク (3%)
GATE_MIN_RR_RATIO = 1.5            # 最低リスクリワード比

# --- タイマー ---
CACHE_INTERVAL_SECONDS = 1800      # D1/H4キャッシュ更新間隔 (秒)
STATE_B_COOLDOWN = 600             # 状態B合議クールダウン (秒)
STATE_C_COOLDOWN = 300             # 状態C合議クールダウン (秒)
MAIN_LOOP_INTERVAL = 10            # メインループ間隔 (秒)
ERROR_COOLDOWN = 10                # エラー後待機 (秒)
WEEKEND_SLEEP = 3600               # 週末スリープ (秒)
CHART_REFRESH_INTERVAL = 120       # チャート更新間隔 (秒)

# --- ファイルパス ---
STATE_FILE = os.path.join(BASE_DIR, "bot_state.json")
COUNCIL_LOG_FILE = os.path.join(BASE_DIR, "council_log.jsonl")
TRADE_RESULTS_FILE = os.path.join(BASE_DIR, "trade_results.jsonl")
DB_FILE = os.path.join(BASE_DIR, "vma.db")
CHARTER_FILE = os.path.join(BASE_DIR, "charter.md")
CHART_NUM_CANDLES = 20
REFERENCE_IMAGE_PATHS = [
    os.path.join(BASE_DIR, "image_head_fake.png"),
    os.path.join(BASE_DIR, "image_chop.png"),
    os.path.join(BASE_DIR, "image_expansion_convergence.png"),
]


# ============================================================================
# §2. 環境初期化
# ============================================================================
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))


class JsonFormatter(logging.Formatter):
    """構造化JSONログフォーマッタ"""
    def format(self, record):
        entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "msg": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_json_file = logging.handlers.RotatingFileHandler(
    os.path.join(BASE_DIR, "vma.jsonl"), maxBytes=10 * 1024 * 1024,
    backupCount=5, encoding="utf-8",
)
_json_file.setFormatter(JsonFormatter())
_plain_file = logging.FileHandler(os.path.join(BASE_DIR, "vma.log"), encoding="utf-8")
_plain_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_console, _json_file, _plain_file])

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

if not all([GEMINI_API_KEY, LINE_ACCESS_TOKEN, LINE_USER_ID]):
    logging.critical("環境変数未設定 (GEMINI_API_KEY, LINE_ACCESS_TOKEN, LINE_USER_ID)")
    raise EnvironmentError("必須環境変数が不足しています。")

try:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    logging.critical(f"Gemini初期化エラー: {e}")
    raise


# ============================================================================
# §3. BotPhase状態マシン
# ============================================================================
class BotPhase(Enum):
    """BOTの動作状態。遷移表に基づいてのみ遷移する。"""
    INITIALIZING = auto()
    MONITORING = auto()          # 通常監視（トレード可能）
    COUNCIL_PENDING = auto()     # Gemini合議中
    WEEKEND = auto()             # 週末休止
    DD_LOCKED = auto()           # DD超過ロック
    ANOMALY_HALTED = auto()      # Python側異常検知で一時停止
    SHUTTING_DOWN = auto()       # シャットダウン中

    def can_trade(self) -> bool:
        return self == BotPhase.MONITORING

    def can_detect(self) -> bool:
        return self in (BotPhase.MONITORING, BotPhase.COUNCIL_PENDING)


PHASE_TRANSITIONS: Dict[Tuple[BotPhase, str], BotPhase] = {
    (BotPhase.INITIALIZING, "init_done"):       BotPhase.MONITORING,
    (BotPhase.MONITORING, "weekend"):            BotPhase.WEEKEND,
    (BotPhase.MONITORING, "dd_lock"):            BotPhase.DD_LOCKED,
    (BotPhase.MONITORING, "anomaly_halt"):       BotPhase.ANOMALY_HALTED,
    (BotPhase.MONITORING, "council_start"):      BotPhase.COUNCIL_PENDING,
    (BotPhase.MONITORING, "shutdown"):           BotPhase.SHUTTING_DOWN,
    (BotPhase.COUNCIL_PENDING, "council_done"):  BotPhase.MONITORING,
    (BotPhase.COUNCIL_PENDING, "weekend"):       BotPhase.WEEKEND,
    (BotPhase.COUNCIL_PENDING, "dd_lock"):       BotPhase.DD_LOCKED,
    (BotPhase.COUNCIL_PENDING, "anomaly_halt"):  BotPhase.ANOMALY_HALTED,
    (BotPhase.COUNCIL_PENDING, "shutdown"):      BotPhase.SHUTTING_DOWN,
    (BotPhase.WEEKEND, "weekday_resume"):        BotPhase.MONITORING,
    (BotPhase.WEEKEND, "shutdown"):              BotPhase.SHUTTING_DOWN,
    (BotPhase.DD_LOCKED, "unlock"):              BotPhase.MONITORING,
    (BotPhase.DD_LOCKED, "shutdown"):            BotPhase.SHUTTING_DOWN,
    (BotPhase.ANOMALY_HALTED, "anomaly_clear"):  BotPhase.MONITORING,
    (BotPhase.ANOMALY_HALTED, "shutdown"):       BotPhase.SHUTTING_DOWN,
}


class PhaseManager:
    """スレッドセーフな状態遷移管理"""
    def __init__(self):
        self._lock = threading.Lock()
        self._phase = BotPhase.INITIALIZING

    @property
    def phase(self) -> BotPhase:
        with self._lock:
            return self._phase

    def transition(self, event: str) -> bool:
        with self._lock:
            key = (self._phase, event)
            next_phase = PHASE_TRANSITIONS.get(key)
            if next_phase is None:
                logging.debug(f"【遷移却下】{self._phase.name} + '{event}' → 遷移先なし")
                return False
            prev = self._phase
            self._phase = next_phase
            logging.info(f"【状態遷移】{prev.name} → {next_phase.name} (event='{event}')")
            return True


# ============================================================================
# §4. Python側異常ガード（AnomalyGuard）— Blocker 1/2対応
# ============================================================================
class AnomalyGuard:
    """
    Gemini APIを使わず、Python側で即時に異常を検知・遮断する。
    
    設計原則（PhantomOS実装憲章準拠）:
      「安全装置が致命傷を引き起こしてはならない」
      → スプレッド異常時の成行決済は行わない。既存ポジはSLに任せる。
      → 凍結するのは「成行注文を伴う操作」のみ。トレーリング更新は維持。
    
    監視対象:
      1. スプレッド急拡大 → エントリー禁止 + 成行操作凍結
      2. ティック凍結 → エントリー禁止
      3. 瞬間価格ジャンプ → エントリー禁止
    
    復帰条件（Blocker 2対応 — 時間だけでは復帰しない）:
      段階1: 直近N回のティックでスプレッドがBLOCK閾値未満
      段階2: ティック更新が正常（凍結なし）連続N回
      段階3: 上記を満たした上でクールダウン秒数経過
      再発3回(30分窓) → 手動解除に格上げ
    """

    def __init__(self):
        self._last_tick_time: Optional[float] = None
        self._last_bid: Optional[float] = None
        self._last_anomaly_time: Optional[float] = None
        self._spread_history: List[float] = []
        self._normal_tick_streak: int = 0      # 正常ティック連続回数
        self._halted: bool = False              # 現在ANOMALY_HALTED中か
        self._manual_lock: bool = False         # 手動解除ロック
        self._halt_timestamps: List[float] = [] # 再発カウント用タイムスタンプ
        self._lock = threading.Lock()

    def check(self, tick) -> Dict[str, Any]:
        """
        Returns:
            {
                "block_entry": bool,          # 新規エントリー禁止
                "freeze_market_orders": bool,  # 成行決済操作凍結
                "spread_anomaly": bool,        # ★スプレッド起因の異常（DD判定バイパス用）
                "tick_frozen": bool,
                "manual_locked": bool,
                "alerts": [str, ...],
            }
        """
        result = {
            "block_entry": False,
            "freeze_market_orders": False,
            "spread_anomaly": False,
            "tick_frozen": False,
            "manual_locked": False,
            "alerts": [],
        }

        if self._manual_lock:
            result["block_entry"] = True
            result["freeze_market_orders"] = True
            result["manual_locked"] = True
            result["alerts"].append("🔴 手動解除ロック中: 異常が短時間に繰り返し発生")
            return result

        if tick is None:
            result["block_entry"] = True
            result["tick_frozen"] = True
            result["alerts"].append("ティックデータ取得不能")
            return result

        now = time.time()
        spread_pips = price_to_pips(tick.ask - tick.bid)
        is_anomaly = False

        with self._lock:
            self._spread_history.append(spread_pips)
            if len(self._spread_history) > 30:
                self._spread_history = self._spread_history[-30:]

            # --- 1. スプレッド監視 ---
            if spread_pips >= SPREAD_BLOCK_PIPS:
                result["block_entry"] = True
                result["freeze_market_orders"] = True
                result["spread_anomaly"] = True  # ★spread起因を明示
                is_anomaly = True
                self._normal_tick_streak = 0
                result["alerts"].append(
                    f"🟡 スプレッド異常: {spread_pips:.1f}pips → エントリー禁止+成行凍結"
                )
            elif spread_pips >= SPREAD_WARN_PIPS:
                result["alerts"].append(f"⚠️ スプレッド拡大中: {spread_pips:.1f}pips")
                self._normal_tick_streak += 1
            else:
                self._normal_tick_streak += 1

            # --- 2. ティック凍結検知 ---
            if self._last_tick_time is not None:
                elapsed = now - self._last_tick_time
                if elapsed >= TICK_FREEZE_SECONDS:
                    result["tick_frozen"] = True
                    result["block_entry"] = True
                    is_anomaly = True
                    self._normal_tick_streak = 0
                    result["alerts"].append(
                        f"🔴 ティック凍結: {elapsed:.0f}秒更新なし"
                    )
            self._last_tick_time = now

            # --- 3. 瞬間価格ジャンプ検知 ---
            if self._last_bid is not None:
                jump_pips = abs(price_to_pips(tick.bid - self._last_bid))
                if jump_pips >= PRICE_JUMP_PIPS:
                    result["block_entry"] = True
                    is_anomaly = True
                    self._normal_tick_streak = 0
                    result["alerts"].append(
                        f"🔴 価格ジャンプ: {jump_pips:.1f}pips"
                    )
            self._last_bid = tick.bid

            # --- HALTED中の段階復帰判定 ---
            if self._halted and not is_anomaly:
                can_recover = (
                    self._normal_tick_streak >= ANOMALY_RECOVERY_TICKS
                    and self._last_anomaly_time is not None
                    and (now - self._last_anomaly_time) >= ANOMALY_COOLDOWN_SECONDS
                )
                if can_recover:
                    self._halted = False
                    logging.info("【異常ガード】段階復帰条件クリア → MONITORING復帰可能")
                else:
                    result["block_entry"] = True
                    result["freeze_market_orders"] = True

            # --- 異常発生時: HALT + 再発カウント ---
            if is_anomaly and not self._halted:
                self._halted = True
                self._last_anomaly_time = now
                self._normal_tick_streak = 0
                # 再発カウント（ローリング窓）
                self._halt_timestamps.append(now)
                self._halt_timestamps = [
                    t for t in self._halt_timestamps
                    if now - t <= ANOMALY_ESCALATION_WINDOW
                ]
                if len(self._halt_timestamps) >= ANOMALY_ESCALATION_COUNT:
                    self._manual_lock = True
                    result["manual_locked"] = True
                    result["alerts"].append(
                        f"🔴 {ANOMALY_ESCALATION_COUNT}回再発 → 手動解除ロック"
                    )

            # --- クールダウン中は禁止維持 ---
            if self._last_anomaly_time is not None:
                if now - self._last_anomaly_time < ANOMALY_COOLDOWN_SECONDS:
                    result["block_entry"] = True

        return result

    def get_avg_spread(self) -> float:
        with self._lock:
            if not self._spread_history:
                return 0.0
            return sum(self._spread_history) / len(self._spread_history)

    def is_halted(self) -> bool:
        with self._lock:
            return self._halted

    def is_manual_locked(self) -> bool:
        with self._lock:
            return self._manual_lock

    def unlock_manual(self) -> None:
        """手動解除（外部から呼ぶ）"""
        with self._lock:
            self._manual_lock = False
            self._halted = False
            self._halt_timestamps.clear()
            self._normal_tick_streak = 0
            logging.info("【異常ガード】手動解除実行")

    def restore_halt_history(self, timestamps: List[float]) -> None:
        """SQLiteから再発タイムスタンプを復元（起動時に呼ぶ）"""
        now = time.time()
        with self._lock:
            self._halt_timestamps = [
                t for t in timestamps if now - t <= ANOMALY_ESCALATION_WINDOW
            ]
            if len(self._halt_timestamps) >= ANOMALY_ESCALATION_COUNT:
                self._manual_lock = True
                logging.warning("【異常ガード】復元時に再発回数超過 → 手動解除ロック")

    def get_halt_timestamps(self) -> List[float]:
        """再発タイムスタンプを返す（SQLite保存用）"""
        with self._lock:
            return list(self._halt_timestamps)


# ============================================================================
# §5. SQLite永続化層
# ============================================================================
class PersistenceDB:
    """スレッドローカル接続のSQLite永続化"""
    def __init__(self, db_path: str = DB_FILE):
        self._db_path = db_path
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER UNIQUE,
                slippage_pips REAL DEFAULT 0.0,
                data TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS council_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT, action TEXT, sl REAL,
                data TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS anomaly_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                spread_pips REAL,
                details TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_trades_ticket ON trades(ticket);
        """)
        conn.commit()

    def insert_trade(self, record: Dict[str, Any]) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO trades (ticket, slippage_pips, data) VALUES (?, ?, ?)",
                (record.get("ticket", 0), record.get("slippage_pips", 0.0),
                 json.dumps(record, ensure_ascii=False, default=str)),
            )
            conn.commit()
        except Exception as e:
            logging.warning(f"DB trade挿入失敗: {e}")

    def insert_council_log(self, label: str, action: str, sl: float, data: Dict[str, Any]) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO council_logs (label, action, sl, data) VALUES (?, ?, ?, ?)",
                (label, action, sl, json.dumps(data, ensure_ascii=False, default=str)),
            )
            conn.commit()
        except Exception as e:
            logging.warning(f"DB council_log挿入失敗: {e}")

    def insert_anomaly_event(self, event_type: str, spread_pips: float, details: str) -> None:
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO anomaly_events (event_type, spread_pips, details) VALUES (?, ?, ?)",
                (event_type, spread_pips, details),
            )
            conn.commit()
        except Exception as e:
            logging.warning(f"DB anomaly_event挿入失敗: {e}")

    def get_trade_count(self) -> int:
        try:
            return self._get_conn().execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        except Exception:
            return 0

    def get_recent_trades(self, n: int = 20) -> List[Dict[str, Any]]:
        try:
            rows = self._get_conn().execute(
                "SELECT data FROM trades ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
            return [json.loads(r[0]) for r in rows]
        except Exception:
            return []

    def get_recent_anomaly_halt_times(self, window_seconds: int = 1800) -> List[float]:
        """直近window_seconds以内のanomaly_halt発生時刻をUNIXタイムスタンプで返す"""
        try:
            rows = self._get_conn().execute(
                "SELECT created_at FROM anomaly_events "
                "WHERE event_type = 'anomaly_halt' "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
            now = time.time()
            result = []
            for r in rows:
                try:
                    dt = datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
                    ts = dt.timestamp()
                    if now - ts <= window_seconds:
                        result.append(ts)
                except Exception:
                    pass
            return result
        except Exception:
            return []


persistence_db = PersistenceDB()


# ============================================================================
# §6. ユーティリティ
# ============================================================================
def prevent_sleep() -> None:
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        logging.info("【覚醒維持】Windowsスリープ阻止を有効化。")
    except Exception as e:
        logging.warning(f"【覚醒維持】失敗: {e}")


def allow_sleep() -> None:
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    except Exception:
        pass


def atomic_write_json(filepath: str, data: Any) -> None:
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_path, filepath)
    except Exception as e:
        logging.warning(f"アトミック書き込み失敗 ({filepath}): {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def atomic_append_jsonl(filepath: str, record: Dict[str, Any]) -> None:
    try:
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        logging.warning(f"JSONL追記失敗 ({filepath}): {e}")


# --- pip_sizeキャッシュ ---
_pip_size_cache: Dict[str, float] = {}


def pip_size(symbol: str = SYMBOL) -> float:
    if symbol in _pip_size_cache:
        return _pip_size_cache[symbol]
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.01
    ps = info.point * 10 if info.digits in (3, 5) else info.point
    _pip_size_cache[symbol] = ps
    return ps


def price_to_pips(price_diff: float, symbol: str = SYMBOL) -> float:
    ps = pip_size(symbol)
    return price_diff / ps if ps != 0 else 0.0


def pips_to_price(pips_val: float, symbol: str = SYMBOL) -> float:
    return pips_val * pip_size(symbol)


def get_filling_mode(symbol: str = SYMBOL) -> int:
    info = mt5.symbol_info(symbol)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    if info.filling_mode & mt5.SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    elif info.filling_mode & mt5.SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


# ============================================================================
# §7. キャッシュ
# ============================================================================
class ThreadSafeCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.last_update: Optional[datetime.datetime] = None
        self.data: Dict[str, Any] = {}

    def get(self) -> Tuple[Optional[datetime.datetime], Dict[str, Any]]:
        with self._lock:
            return self.last_update, dict(self.data)

    def set(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self.last_update = datetime.datetime.now()
            self.data = data


cached_d1_h4 = ThreadSafeCache()


class EventCalendarCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.event_times: List[datetime.datetime] = []
        self.last_update: Optional[datetime.datetime] = None

    def get(self) -> Tuple[List[datetime.datetime], Optional[datetime.datetime]]:
        with self._lock:
            return list(self.event_times), self.last_update

    def set(self, events: List[datetime.datetime], update_time: datetime.datetime) -> None:
        with self._lock:
            self.event_times = events
            self.last_update = update_time


calendar_cache = EventCalendarCache()


# ============================================================================
# §8. データ構造 (TradeTracker & BotState)
# ============================================================================
@dataclass
class TradeTracker:
    """個別トレードの追跡データ"""
    ticket: int
    direction: str                          # "BUY" or "SELL"
    entry_price: float
    sl_initial: float
    entry_time: str                         # ISO format
    council_label: str                      # "状態A" / "状態B" / "状態C"
    market_state: Dict[str, Any] = field(default_factory=dict)
    max_favorable_pips: float = 0.0
    max_adverse_pips: float = 0.0
    sl_current: float = 0.0

    def __post_init__(self):
        if self.sl_current == 0.0:
            self.sl_current = self.sl_initial

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticket": self.ticket, "direction": self.direction,
            "entry_price": self.entry_price, "sl_initial": self.sl_initial,
            "sl_current": self.sl_current, "entry_time": self.entry_time,
            "council_label": self.council_label, "market_state": self.market_state,
            "max_favorable_pips": round(self.max_favorable_pips, 1),
            "max_adverse_pips": round(self.max_adverse_pips, 1),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TradeTracker":
        return cls(
            ticket=d["ticket"], direction=d["direction"],
            entry_price=d["entry_price"], sl_initial=d["sl_initial"],
            entry_time=d["entry_time"],
            council_label=d.get("council_label", ""),
            market_state=d.get("market_state", {}),
            max_favorable_pips=d.get("max_favorable_pips", 0.0),
            max_adverse_pips=d.get("max_adverse_pips", 0.0),
            sl_current=d.get("sl_current", d.get("sl_initial", 0.0)),
        )


@dataclass
class BotState:
    last_normal_entry_check: Optional[datetime.datetime] = None
    last_state_b_check: Optional[datetime.datetime] = None
    last_state_c_check: Optional[datetime.datetime] = None
    open_trades: Dict[int, TradeTracker] = field(default_factory=dict)
    consecutive_losses: int = 0
    peak_balance: float = 0.0
    start_time: datetime.datetime = field(default_factory=datetime.datetime.now)

    def save(self) -> None:
        try:
            data = {
                "last_normal_entry_check": self.last_normal_entry_check.isoformat() if self.last_normal_entry_check else None,
                "last_state_b_check": self.last_state_b_check.isoformat() if self.last_state_b_check else None,
                "last_state_c_check": self.last_state_c_check.isoformat() if self.last_state_c_check else None,
                "open_trades": {str(k): v.to_dict() for k, v in self.open_trades.items()},
                "consecutive_losses": self.consecutive_losses,
                "peak_balance": self.peak_balance,
            }
            atomic_write_json(STATE_FILE, data)
        except Exception as e:
            logging.warning(f"BotState保存失敗: {e}")

    @classmethod
    def load(cls) -> "BotState":
        state = cls()
        if not os.path.exists(STATE_FILE):
            return state
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key in ("last_normal_entry_check", "last_state_b_check", "last_state_c_check"):
                val = data.get(key)
                if val:
                    setattr(state, key, datetime.datetime.fromisoformat(val))
            for k, v in data.get("open_trades", {}).items():
                try:
                    state.open_trades[int(k)] = TradeTracker.from_dict(v)
                except Exception:
                    pass
            state.consecutive_losses = data.get("consecutive_losses", 0)
            state.peak_balance = data.get("peak_balance", 0.0)
            logging.info("【復元】BotStateをファイルから復元しました。")
        except Exception as e:
            logging.warning(f"BotState復元失敗 (初期状態で開始): {e}")
        return state


# ============================================================================
# §9. テクニカル計算
# ============================================================================
def analyze_momentum(series: pd.Series) -> Tuple[str, str]:
    if series is None or len(series) < 2:
        return "FLAT", "Weak"
    diff = series.iloc[-1] - series.iloc[-2]
    direction = "UP" if diff > 0 else ("DOWN" if diff < 0 else "FLAT")
    strength = "Strong" if abs(diff) >= pips_to_price(1.0) else "Weak"
    return direction, strength


def calculate_sma_angle(sma_series: pd.Series, period: int = 5) -> float:
    if sma_series is None or len(sma_series) < period:
        return 0.0
    y = sma_series.iloc[-period:].values
    x = np.arange(period)
    slope, _ = np.polyfit(x, y, 1)
    return round(math.degrees(math.atan(price_to_pips(slope))), 1)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_adx(df: pd.DataFrame, period: int = 9) -> pd.DataFrame:
    """ADX(期間9)を計算。VMA詳細版第1章準拠。"""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=1/period, min_periods=period).mean()
    return pd.DataFrame({"ADX": adx, "PLUS_DI": plus_di, "MINUS_DI": minus_di})


def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD(12,26,9)を計算。VMA詳細版第1章準拠。"""
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"MACD": macd_line, "SIGNAL": signal_line, "HIST": macd_line - signal_line})


def calculate_stoch(df: pd.DataFrame, k_period: int = 12, k_smooth: int = 3, d_smooth: int = 3) -> pd.DataFrame:
    """ストキャスティクス(スロー, 12,3,3)。VMA詳細版第1章準拠。SLOW%D重視。"""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    fast_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    slow_k = fast_k.rolling(k_smooth).mean()  # %K
    slow_d = slow_k.rolling(d_smooth).mean()   # SLOW%D
    return pd.DataFrame({"SLOW_K": slow_k, "SLOW_D": slow_d})


def classify_slope(series: pd.Series, n: int = 3) -> str:
    """直近n本の傾きをUP/DOWN/FLATに分類"""
    if series is None or len(series) < n:
        return "FLAT"
    vals = series.iloc[-n:].values
    if np.any(np.isnan(vals)):
        return "FLAT"
    diff = vals[-1] - vals[0]
    if diff > 0:
        return "UP"
    elif diff < 0:
        return "DOWN"
    return "FLAT"


def get_last_n(series: pd.Series, n: int = 3) -> List[float]:
    """直近n本の値をリストで返す（Geminiに時系列で渡すため）"""
    if series is None or len(series) < n:
        return []
    vals = series.iloc[-n:].values
    return [round(float(v), 4) if not np.isnan(v) else 0.0 for v in vals]


def classify_sigma_position(price: float, sma: float, std: float) -> str:
    """価格のσ帯位置を分類"""
    if std == 0:
        return "AT_SMA"
    z = (price - sma) / std
    if z > 3:
        return "ABOVE_+3S"
    elif z > 2:
        return "+2S_to_+3S"
    elif z > 1:
        return "+1S_to_+2S"
    elif z > 0:
        return "SMA_to_+1S"
    elif z > -1:
        return "-1S_to_SMA"
    elif z > -2:
        return "-2S_to_-1S"
    elif z > -3:
        return "-3S_to_-2S"
    return "BELOW_-3S"


def fetch_and_calc_labels(timeframe: int) -> Optional[Dict[str, Any]]:
    """指定時間足のテクニカルラベルを計算する（Blocker3対応: ADX/MACD/Stoch追加）"""
    rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, 100)
    if rates is None or len(rates) < 30:
        return None
    df = pd.DataFrame(rates)
    df["sma21"] = df["close"].rolling(21).mean()
    df["std21"] = df["close"].rolling(21).std(ddof=1)  # Minor6: ddof統一

    sma_drop = df["sma21"].dropna()
    m_dir, m_str = analyze_momentum(sma_drop)
    sma_angle = calculate_sma_angle(sma_drop, 5)

    current_close = float(df["close"].iloc[-1])
    current_sma = float(df["sma21"].iloc[-1])
    current_std = float(df["std21"].iloc[-1])
    price_position = "ABOVE_SMA" if current_close >= current_sma else "BELOW_SMA"

    bb_u3 = current_sma + 3 * current_std
    bb_l3 = current_sma - 3 * current_std
    bb_u2 = current_sma + 2 * current_std
    bb_l2 = current_sma - 2 * current_std
    bb_width = price_to_pips(bb_u3 - bb_l3)

    # BB幅の変化（直近3本）
    std_series = df["std21"].dropna()
    if len(std_series) >= 3:
        w_now = float(std_series.iloc[-1])
        w_prev = float(std_series.iloc[-3])
        bb_change = "EXPANDING" if w_now > w_prev * 1.02 else ("CONTRACTING" if w_now < w_prev * 0.98 else "STABLE")
    else:
        bb_change = "STABLE"

    # σ帯位置
    sigma_pos = classify_sigma_position(current_close, current_sma, current_std)

    # レジサポ距離（直近50本の高安値）
    recent_high = float(df["high"].iloc[-50:].max())
    recent_low = float(df["low"].iloc[-50:].min())
    nearest_resistance_pips = round(price_to_pips(recent_high - current_close), 1)
    nearest_support_pips = round(price_to_pips(current_close - recent_low), 1)

    result = {
        "PRICE_POSITION": price_position,
        "CURRENT_CLOSE": round(current_close, 3),
        "SMA21_VALUE": round(current_sma, 3),
        "SMA21_DIR": f"{m_dir} ({m_str})",
        "SMA21_ANGLE": sma_angle,
        "SMA21_ANGLE_LAST3": get_last_n(pd.Series([
            calculate_sma_angle(sma_drop.iloc[:-(2-i)] if i < 2 else sma_drop, 5)
            for i in range(3)
        ]), 3) if len(sma_drop) > 7 else [],
        "BB_WIDTH_PIPS": round(bb_width, 1),
        "BB_WIDTH_CHANGE": bb_change,
        "BB_UPPER_2S": round(bb_u2, 3),
        "BB_LOWER_2S": round(bb_l2, 3),
        "BB_UPPER_3S": round(bb_u3, 3),
        "BB_LOWER_3S": round(bb_l3, 3),
        "PRICE_SIGMA_POSITION": sigma_pos,
        "NEAREST_RESISTANCE_PIPS": nearest_resistance_pips,
        "NEAREST_SUPPORT_PIPS": nearest_support_pips,
        "RECENT_50_HIGH": recent_high,
        "RECENT_50_LOW": recent_low,
    }

    # --- ADX (期間9) ---
    try:
        adx_df = calculate_adx(df, period=9)
        adx_vals = adx_df["ADX"].dropna()
        if len(adx_vals) >= 3:
            result["ADX_VALUE"] = round(float(adx_vals.iloc[-1]), 1)
            result["ADX_SLOPE"] = classify_slope(adx_vals, 3)
            result["ADX_LAST3"] = get_last_n(adx_vals, 3)
            result["PLUS_DI"] = round(float(adx_df["PLUS_DI"].iloc[-1]), 1)
            result["MINUS_DI"] = round(float(adx_df["MINUS_DI"].iloc[-1]), 1)
    except Exception:
        pass

    # --- MACD (12,26,9) ---
    try:
        macd_df = calculate_macd(df)
        macd_vals = macd_df["MACD"].dropna()
        if len(macd_vals) >= 3:
            result["MACD_LINE"] = round(float(macd_vals.iloc[-1]), 5)
            result["MACD_SIGNAL"] = round(float(macd_df["SIGNAL"].iloc[-1]), 5)
            result["MACD_SLOPE"] = classify_slope(macd_vals, 3)
            result["MACD_LINE_LAST3"] = get_last_n(macd_vals, 3)
    except Exception:
        pass

    # --- ストキャスティクス (12,3,3) ---
    try:
        stoch_df = calculate_stoch(df)
        slow_d = stoch_df["SLOW_D"].dropna()
        if len(slow_d) >= 3:
            result["STOCH_SLOW_D"] = round(float(slow_d.iloc[-1]), 1)
            result["STOCH_SLOPE"] = classify_slope(slow_d, 3)
            result["STOCH_SLOW_D_LAST3"] = get_last_n(slow_d, 3)
    except Exception:
        pass

    return result


def get_market_data_optimized() -> str:
    """Geminiに渡す市場データJSON（M15/D1追加）"""
    now = datetime.datetime.now()
    last_upd, cached_data = cached_d1_h4.get()

    if last_upd is None or (now - last_upd).total_seconds() >= CACHE_INTERVAL_SECONDS:
        d1 = fetch_and_calc_labels(mt5.TIMEFRAME_D1)
        h4 = fetch_and_calc_labels(mt5.TIMEFRAME_H4)
        new_data = {
            "D1": d1 if d1 else {"STATUS": "UNAVAILABLE"},
            "H4": h4 if h4 else {"STATUS": "UNAVAILABLE"},
        }
        cached_d1_h4.set(new_data)
        cached_data = new_data

    tick = mt5.symbol_info_tick(SYMBOL)
    m30 = fetch_and_calc_labels(mt5.TIMEFRAME_M30)
    m15 = fetch_and_calc_labels(mt5.TIMEFRAME_M15)
    m5 = fetch_and_calc_labels(mt5.TIMEFRAME_M5)

    # 時間足方向整合 (TF_ALIGNMENT)
    tf_dirs = {}
    for name, labels in [("M5", m5), ("M15", m15), ("M30", m30), ("H4", cached_data.get("H4")), ("D1", cached_data.get("D1"))]:
        if labels and isinstance(labels, dict) and "SMA21_ANGLE" in labels:
            angle = labels["SMA21_ANGLE"]
            tf_dirs[name] = "UP" if angle > 5 else ("DOWN" if angle < -5 else "FLAT")
        else:
            tf_dirs[name] = "N/A"

    return json.dumps({
        "symbol": SYMBOL,
        "time": now.isoformat(),
        "bid": tick.bid if tick else 0.0,
        "ask": tick.ask if tick else 0.0,
        "SPREAD_PIPS": round(price_to_pips(tick.ask - tick.bid), 1) if tick else 0.0,
        "ENTRY_WINDOW_OPEN": is_entry_window(now),
        "RESTRICTED_TIME_ACTIVE": is_restricted_time(now),
        "TF_ALIGNMENT": tf_dirs,
        "D1_Labels": cached_data.get("D1", {"STATUS": "UNAVAILABLE"}),
        "H4_Labels": cached_data.get("H4", {"STATUS": "UNAVAILABLE"}),
        "M30_Labels": m30,
        "M15_Labels": m15,
        "M5_Labels": m5,
    }, ensure_ascii=False)


# ============================================================================
# §10. チャート生成（バックグラウンドスレッド）
# ============================================================================
class ChartCache:
    """バックグラウンドでチャートをメモリ内に生成・保持"""
    def __init__(self, shutdown_event: threading.Event):
        self._shutdown = shutdown_event
        self._lock = threading.Lock()
        self._charts: Dict[str, bytes] = {}
        self._last_gen: Optional[datetime.datetime] = None

    def start(self) -> None:
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def _worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                self._generate_all()
            except Exception as e:
                logging.warning(f"チャート生成エラー: {e}")
            self._shutdown.wait(CHART_REFRESH_INTERVAL)

    def _generate_all(self) -> None:
        for tf_name, tf_const in [("H4", mt5.TIMEFRAME_H4), ("M30", mt5.TIMEFRAME_M30), ("M5", mt5.TIMEFRAME_M5)]:
            img_bytes = self._generate_single(tf_const)
            if img_bytes:
                with self._lock:
                    self._charts[tf_name] = img_bytes
        with self._lock:
            self._last_gen = datetime.datetime.now()

    @staticmethod
    def _generate_single(timeframe: int) -> Optional[bytes]:
        try:
            rates = mt5.copy_rates_from_pos(SYMBOL, timeframe, 0, 100)
            if rates is None or len(rates) < 30:
                return None
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df.set_index("time", inplace=True)
            df["sma21"] = df["close"].rolling(21).mean()
            df["std21"] = df["close"].rolling(21).std()
            df["bb_u2"] = df["sma21"] + 2 * df["std21"]
            df["bb_l2"] = df["sma21"] - 2 * df["std21"]
            df["bb_u3"] = df["sma21"] + 3 * df["std21"]
            df["bb_l3"] = df["sma21"] - 3 * df["std21"]

            df_p = df.iloc[-CHART_NUM_CANDLES:].copy()
            apds = [
                mpf.make_addplot(df_p["sma21"], color="blue", width=1.5),
                mpf.make_addplot(df_p[["bb_u2", "bb_l2"]], color="green", linestyle="--", width=1.0),
                mpf.make_addplot(df_p[["bb_u3", "bb_l3"]], color="red", linestyle=":", width=1.0),
            ]
            import io
            buf = io.BytesIO()
            mpf.plot(df_p, type="candle", addplot=apds, style="charles",
                     figsize=(6, 4), savefig=dict(fname=buf, dpi=100, bbox_inches="tight"))
            plt.close("all")
            gc.collect()
            buf.seek(0)
            return buf.read()
        except Exception as e:
            logging.warning(f"チャート生成失敗: {e}")
            return None

    def get_images(self) -> List[PIL.Image.Image]:
        """Geminiに渡すPIL.Imageリストを返す"""
        images = []
        with self._lock:
            for name in ("H4", "M30", "M5"):
                data = self._charts.get(name)
                if data:
                    try:
                        import io
                        images.append(PIL.Image.open(io.BytesIO(data)).copy())
                    except Exception:
                        pass
        return images

    def force_refresh(self) -> None:
        threading.Thread(target=self._generate_all, daemon=True).start()


# ============================================================================
# §11. 資金管理（エクイティカーブ・コントロール）
# ============================================================================
def get_dd_percent(state: BotState) -> float:
    """現在のDD%を計算"""
    acc = mt5.account_info()
    if not acc:
        return 0.0
    equity = float(acc.equity)
    if state.peak_balance <= 0:
        return 0.0
    return (state.peak_balance - equity) / state.peak_balance * 100.0


def get_dd_stage(dd_pct: float) -> str:
    """DD%からステージを判定（PhantomOS DD4層準拠）"""
    if dd_pct >= DD_DISQUALIFY_PCT:
        return "DISQUALIFY"
    elif dd_pct >= DD_HALT_PCT:
        return "HALT"
    elif dd_pct >= DD_REDUCTION_PCT:
        return "REDUCTION"
    elif dd_pct >= DD_WARNING_PCT:
        return "WARNING"
    return "NORMAL"


def get_dynamic_risk(state: BotState) -> float:
    """DD4層+連敗から動的リスクを計算（PhantomOS DDMonitor準拠）"""
    acc = mt5.account_info()
    if not acc:
        return BASE_RISK_PERCENT

    current_balance = float(acc.balance)
    if state.peak_balance == 0.0 or current_balance > state.peak_balance:
        state.peak_balance = current_balance
        state.save()

    dd_pct = get_dd_percent(state)
    stage = get_dd_stage(dd_pct)

    if stage == "DISQUALIFY":
        logging.critical(f"【DD4層】DD {dd_pct:.1f}% ≧ {DD_DISQUALIFY_PCT}% → 全決済+停止")
        return 0.0  # 呼び出し元で全決済処理
    elif stage == "HALT":
        logging.warning(f"【DD4層】DD {dd_pct:.1f}% ≧ {DD_HALT_PCT}% → 新規停止")
        return 0.0  # 新規エントリー不可
    elif stage == "REDUCTION":
        logging.info(f"【DD4層】DD {dd_pct:.1f}% ≧ {DD_REDUCTION_PCT}% → リスク半減")
        return BASE_RISK_PERCENT / 2.0
    elif stage == "WARNING":
        logging.info(f"【DD4層】DD {dd_pct:.1f}% ≧ {DD_WARNING_PCT}% → 警告")

    if state.consecutive_losses >= 5:
        logging.info(f"【資金防衛】{state.consecutive_losses}連敗 → リスク1/3")
        return BASE_RISK_PERCENT / 3.0
    elif state.consecutive_losses >= 3:
        logging.info(f"【資金防衛】{state.consecutive_losses}連敗 → リスク半減")
        return BASE_RISK_PERCENT / 2.0

    return BASE_RISK_PERCENT


def calculate_dynamic_lot(sl_price: float, entry_price: float, state: BotState) -> Tuple[float, int]:
    """SL幅からロットを逆算"""
    acc = mt5.account_info()
    info = mt5.symbol_info(SYMBOL)
    if not acc or not info:
        return 0.01, 0

    min_sl_points = MIN_SL_PIPS * (10 if info.digits in (3, 5) else 1)
    diff = max(abs(entry_price - sl_price), info.point * min_sl_points)
    loss_per_lot = (diff / info.trade_tick_size) * info.trade_tick_value
    if loss_per_lot <= 0:
        return info.volume_min, 0

    active_risk = get_dynamic_risk(state)
    raw_lot = (acc.balance * active_risk) / loss_per_lot
    step = info.volume_step
    final_lot = max(info.volume_min, min(math.floor(raw_lot / step) * step, info.volume_max))
    return float(round(final_lot, 2)), int(round(final_lot * loss_per_lot))


# ============================================================================
# §12. 注文執行
# ============================================================================
def execute_trade(order_type: int, sl_price: float, state: BotState) -> Tuple[bool, float, float, int]:
    """成行注文を送信"""
    for attempt in range(1, MAX_TRADE_RETRIES + 1):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            time.sleep(1)
            continue
        entry = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
        vol, risk = calculate_dynamic_lot(sl_price, entry, state)
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": vol,
            "type": order_type,
            "price": entry,
            "sl": sl_price,
            "deviation": ORDER_DEVIATION,
            "magic": MAGIC_NUMBER,
            "comment": f"VMA {VERSION}",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": get_filling_mode(SYMBOL),
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return True, vol, res.price, risk
        err = f"拒否: {res.retcode if res else '通信エラー'}"
        logging.warning(f"注文試行 {attempt}/{MAX_TRADE_RETRIES}: {err}")
        if attempt == MAX_TRADE_RETRIES:
            send_line_notify(f"【執行失敗】{err}")
        time.sleep(1)
    return False, 0.0, 0.0, 0


def close_position(ticket: int, order_type: int, volume: float) -> bool:
    """ポジション決済"""
    for attempt in range(1, MAX_TRADE_RETRIES + 1):
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick is None:
            time.sleep(1)
            continue
        close_type = mt5.ORDER_TYPE_SELL if order_type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        close_price = tick.bid if order_type == mt5.ORDER_TYPE_BUY else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": volume,
            "type": close_type,
            "position": ticket,
            "price": close_price,
            "deviation": ORDER_DEVIATION,
            "magic": MAGIC_NUMBER,
            "type_filling": get_filling_mode(SYMBOL),
        }
        res = mt5.order_send(req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return True
        logging.warning(f"決済試行 {attempt}/{MAX_TRADE_RETRIES}: {res.retcode if res else '通信エラー'}")
        time.sleep(1)
    return False


def close_all_positions_safely(state: BotState, reason: str) -> None:
    """全ポジションを安全に決済する（緊急時・週末用）"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return
    for p in positions:
        if p.magic != MAGIC_NUMBER:
            continue
        if close_position(p.ticket, p.type, p.volume):
            tracker = state.open_trades.pop(p.ticket, None)
            if tracker:
                log_trade_result(tracker, p.price_current, reason, state)
    state.save()
    send_line_notify(f"【全決済】理由: {reason}")


# ============================================================================
# §13. ログ記録
# ============================================================================
def send_line_notify(msg: str) -> None:
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        return
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"},
            json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg[:4900]}]},
            timeout=10,
        )
    except Exception:
        pass


def log_trade_result(tracker: TradeTracker, exit_price: float, exit_reason: str,
                     state: BotState, slippage_pips: float = 0.0,
                     commission: float = 0.0, swap: float = 0.0,
                     mt5_profit: Optional[float] = None) -> None:
    """決済結果の記録"""
    try:
        entry_time = datetime.datetime.fromisoformat(tracker.entry_time)
        hold_seconds = (datetime.datetime.now() - entry_time).total_seconds()
        if tracker.direction == "BUY":
            result_pips = price_to_pips(exit_price - tracker.entry_price)
        else:
            result_pips = price_to_pips(tracker.entry_price - exit_price)

        if result_pips > 0:
            state.consecutive_losses = 0
        else:
            state.consecutive_losses += 1

        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "ticket": tracker.ticket,
            "direction": tracker.direction,
            "council_label": tracker.council_label,
            "entry_price": tracker.entry_price,
            "exit_price": exit_price,
            "sl_initial": tracker.sl_initial,
            "sl_final": tracker.sl_current,
            "result_pips": round(result_pips, 1),
            "hold_minutes": round(hold_seconds / 60, 1),
            "max_favorable_pips": round(tracker.max_favorable_pips, 1),
            "max_adverse_pips": round(tracker.max_adverse_pips, 1),
            "exit_reason": exit_reason,
            "slippage_pips": round(slippage_pips, 1),
            "commission": commission, "swap": swap,
            "mt5_profit": mt5_profit,
            "win": result_pips > 0,
        }
        atomic_append_jsonl(TRADE_RESULTS_FILE, record)
        persistence_db.insert_trade(record)
        logging.info(
            f"【学習ログ】Ticket:{tracker.ticket} {tracker.direction} "
            f"結果:{result_pips:+.1f}pips 理由:{exit_reason} "
            f"保有:{hold_seconds/60:.0f}分 連敗数:{state.consecutive_losses}"
        )
    except Exception as e:
        logging.warning(f"トレード結果ログ記録失敗: {e}")


def resolve_closed_trade(ticket: int) -> Optional[Dict[str, Any]]:
    """MT5約定履歴から決済データを取得"""
    try:
        now = datetime.datetime.now()
        deals = mt5.history_deals_get(now - datetime.timedelta(days=30), now, position=ticket)
        if not deals:
            return None
        close_deal = None
        for d in deals:
            if d.entry == mt5.DEAL_ENTRY_OUT:
                close_deal = d
                break
        if close_deal is None:
            for d in deals:
                if d.entry == mt5.DEAL_ENTRY_INOUT:
                    close_deal = d
                    break
        if close_deal is None:
            return None
        reason_map = {
            mt5.DEAL_REASON_SL: "sl", mt5.DEAL_REASON_TP: "tp",
            mt5.DEAL_REASON_SO: "stop_out", mt5.DEAL_REASON_CLIENT: "manual",
            mt5.DEAL_REASON_EXPERT: "ea",
        }
        return {
            "exit_price": close_deal.price,
            "exit_reason": reason_map.get(close_deal.reason, f"other_{close_deal.reason}"),
            "commission": close_deal.commission,
            "swap": close_deal.swap,
            "profit": close_deal.profit,
        }
    except Exception as e:
        logging.warning(f"履歴取得失敗 Ticket:{ticket}: {e}")
        return None


# ============================================================================
# §14. 経済カレンダー・時間判定
# ============================================================================
def update_economic_calendar() -> None:
    now = datetime.datetime.now()
    _, last_upd = calendar_cache.get()
    if last_upd and last_upd.date() == now.date():
        return
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if response.status_code != 200:
            return
        events = []
        for item in response.json():
            if item.get("impact") == "High" and item.get("country") in ("USD", "JPY") and item.get("date"):
                dt = pd.to_datetime(item["date"])
                if dt.tzinfo is not None:
                    dt_jst = dt.tz_convert("Asia/Tokyo").tz_localize(None).to_pydatetime()
                else:
                    dt_jst = dt.tz_localize("UTC").tz_convert("Asia/Tokyo").tz_localize(None).to_pydatetime()
                if -3600 <= (dt_jst - now).total_seconds() <= 172800:
                    events.append(dt_jst)
        calendar_cache.set(events, now)
    except Exception:
        pass


def is_restricted_time(now: datetime.datetime) -> bool:
    events, last_upd = calendar_cache.get()
    if last_upd and events:
        return any(abs((now - ev).total_seconds()) <= 3600 for ev in events)
    if now.weekday() >= 5:
        return False
    for time_str in ["22:30", "03:00", "04:00"]:
        h, m = map(int, time_str.split(":"))
        ev_base = now.replace(hour=h, minute=m, second=0, microsecond=0)
        for d in (-1, 0, 1):
            if abs((now - (ev_base + datetime.timedelta(days=d))).total_seconds()) <= 3600:
                return True
    return False


def check_and_reconnect_mt5() -> bool:
    info = mt5.terminal_info()
    if info is None or not info.connected:
        return mt5.initialize()
    return True


def is_weekend_close_time(now: datetime.datetime) -> bool:
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now_jst = now.astimezone(jst) if now.tzinfo else now.replace(tzinfo=jst)
    return now_jst.weekday() == 5 and 4 <= now_jst.hour <= 6


def is_entry_window(now: datetime.datetime) -> bool:
    return (0 <= now.minute < ENTRY_WINDOW_MINUTES) or (30 <= now.minute < 30 + ENTRY_WINDOW_MINUTES)


# ============================================================================
# §15. トレーリングストップ（非線形R倍数段階制）
# ============================================================================
def process_trailing_stop(state: BotState) -> None:
    """R倍数に基づき段階的にトレール幅を圧縮"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return

    rates_m30 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M30, 0, 30)
    rates_m5 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, 5)
    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick or rates_m30 is None or rates_m5 is None:
        return

    df_m30 = pd.DataFrame(rates_m30)
    df_m5 = pd.DataFrame(rates_m5)
    atr_val = calculate_atr(df_m30).iloc[-1]
    if np.isnan(atr_val) or atr_val == 0:
        return

    for p in positions:
        if p.magic != MAGIC_NUMBER:
            continue
        tracker = state.open_trades.get(p.ticket)
        if not tracker:
            continue

        curr = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
        current_pips = price_to_pips(curr - tracker.entry_price) if p.type == mt5.ORDER_TYPE_BUY else price_to_pips(tracker.entry_price - curr)

        one_r_pips = price_to_pips(abs(tracker.entry_price - tracker.sl_initial))
        if one_r_pips <= 0:
            one_r_pips = MIN_SL_PIPS
        r_multiple = current_pips / one_r_pips

        new_sl = p.sl
        buffer = pips_to_price(1.0)

        if r_multiple >= 4.0:
            # 4R到達: M5直近3本の高安トレール
            if p.type == mt5.ORDER_TYPE_BUY:
                new_sl = round(float(df_m5["low"].iloc[-3:].min()) - buffer, 3)
            else:
                new_sl = round(float(df_m5["high"].iloc[-3:].max()) + buffer, 3)
        elif r_multiple >= 3.0:
            trail_w = pips_to_price(price_to_pips(atr_val) * 1.0)
            new_sl = round(curr - trail_w if p.type == mt5.ORDER_TYPE_BUY else curr + trail_w, 3)
        elif r_multiple >= 2.0:
            trail_w = pips_to_price(price_to_pips(atr_val) * 1.2)
            new_sl = round(curr - trail_w if p.type == mt5.ORDER_TYPE_BUY else curr + trail_w, 3)
        elif r_multiple >= 1.0 or current_pips >= TRAIL_START_PIPS:
            trail_w = pips_to_price(price_to_pips(atr_val) * 1.5)
            new_sl = round(curr - trail_w if p.type == mt5.ORDER_TYPE_BUY else curr + trail_w, 3)
        else:
            continue

        should_update = False
        if p.sl == 0.0:
            should_update = True
        else:
            if price_to_pips(abs(new_sl - p.sl)) >= TRAIL_STEP_PIPS:
                if p.type == mt5.ORDER_TYPE_BUY and new_sl > p.sl:
                    should_update = True
                elif p.type == mt5.ORDER_TYPE_SELL and new_sl < p.sl:
                    should_update = True

        if should_update:
            res = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": SYMBOL,
                "position": p.ticket,
                "sl": new_sl,
                "tp": p.tp,
            })
            if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                logging.info(f"【トレール】Ticket:{p.ticket} {r_multiple:.1f}R SL:{p.sl}→{new_sl}")


# ============================================================================
# §16. タイムストップ（撤退の美学）
# ============================================================================
def process_time_stop(state: BotState) -> None:
    """エントリーからTIME_STOP_MINUTES経過 & 含み益TIME_STOP_MIN_R未満 → 撤退"""
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return
    tick = mt5.symbol_info_tick(SYMBOL)
    if not tick:
        return
    now = datetime.datetime.now()

    for p in positions:
        if p.magic != MAGIC_NUMBER:
            continue
        tracker = state.open_trades.get(p.ticket)
        if not tracker:
            continue

        entry_time = datetime.datetime.fromisoformat(tracker.entry_time)
        elapsed_min = (now - entry_time).total_seconds() / 60
        if elapsed_min < TIME_STOP_MINUTES:
            continue

        one_r_pips = price_to_pips(abs(tracker.entry_price - tracker.sl_initial))
        if one_r_pips <= 0:
            one_r_pips = MIN_SL_PIPS

        curr = tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask
        current_pips = price_to_pips(curr - tracker.entry_price) if p.type == mt5.ORDER_TYPE_BUY else price_to_pips(tracker.entry_price - curr)
        r_now = current_pips / one_r_pips

        if r_now >= TIME_STOP_MIN_R:
            continue

        # パターンD免除チェック
        m30_labels = fetch_and_calc_labels(mt5.TIMEFRAME_M30)
        if m30_labels and abs(m30_labels.get("SMA21_ANGLE", 0)) >= TIME_STOP_EXCEPTION_ANGLE:
            continue

        logging.info(f"【タイムストップ】Ticket:{p.ticket} {elapsed_min:.0f}分経過 {r_now:.2f}R → 撤退")
        if close_position(p.ticket, p.type, p.volume):
            tracker = state.open_trades.pop(p.ticket, None)
            if tracker:
                log_trade_result(tracker, curr, "time_stop", state)
            send_line_notify(f"【撤退の美学】Ticket:{p.ticket}\n{elapsed_min:.0f}分経過 {r_now:.2f}R\n利益が伸びないため撤退")


# ============================================================================
# §17. トリガー検知
# ============================================================================
def detect_state_b() -> bool:
    """状態B: M1の速度・加速度で急変動を検知"""
    r1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 10)
    r5 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M5, 0, 50)
    tick = mt5.symbol_info_tick(SYMBOL)
    if r1 is None or r5 is None or tick is None or len(r1) < 6:
        return False

    df1 = pd.DataFrame(r1)
    df5 = pd.DataFrame(r5)
    df5["sma21"] = df5["close"].rolling(21).mean()
    df5["atr"] = calculate_atr(df5)
    df5["std21"] = df5["close"].rolling(21).std()
    df5["bb_width"] = price_to_pips(6 * df5["std21"])
    df_c = df5.dropna(subset=["sma21", "atr", "bb_width"])
    if len(df_c) < 2:
        return False

    closes = df1["close"].values
    velocity = np.diff(closes)
    if len(velocity) < 3:
        return False
    acceleration = np.diff(velocity)
    recent_v = velocity[-3:]
    recent_a = acceleration[-2:]
    curr_v = tick.bid - closes[-1]
    curr_v_pips = price_to_pips(curr_v)
    sma_d = df_c["sma21"].iloc[-1] - df_c["sma21"].iloc[-2]
    th = max(STATE_B_MIN_THRESHOLD_PIPS, price_to_pips(df_c["atr"].iloc[-2]) * STATE_B_ATR_MULTIPLIER)

    is_up = all(v > 0 for v in recent_v[-2:]) and all(a > 0 for a in recent_a[-1:]) and curr_v > 0
    is_down = all(v < 0 for v in recent_v[-2:]) and all(a < 0 for a in recent_a[-1:]) and curr_v < 0
    cumulative = price_to_pips(closes[-1] - closes[-3]) + curr_v_pips

    if is_up and cumulative >= th and sma_d > 0:
        return True
    if is_down and cumulative <= -th and sma_d < 0:
        return True
    if df_c["bb_width"].iloc[-1] - df_c["bb_width"].iloc[-2] >= STATE_B_BB_EXPANSION_PIPS:
        if df5["tick_volume"].iloc[-1] > df5["tick_volume"].iloc[-2] * STATE_B_VOLUME_RATIO:
            return True
    return False


def detect_state_c(price: float) -> bool:
    """状態C: SMA/±2σ接近を検知"""
    r = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M30, 0, 30)
    if r is None or len(r) < 21:
        return False
    closes = r["close"][-21:]
    m = np.mean(closes)
    s = np.std(closes, ddof=1)  # Minor6: ddof統一(Pandas rolling.std()と一致)
    if np.isnan(m) or np.isnan(s) or s == 0:
        return False
    return price_to_pips(min(abs(price - m), abs(price - (m + 2*s)), abs(price - (m - 2*s)))) <= STATE_C_PROXIMITY_PIPS


def update_trade_extremes(state: BotState) -> None:
    """保有中トレードのMFE/MAEを更新"""
    if not state.open_trades:
        return
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return
    positions = mt5.positions_get(symbol=SYMBOL)
    pos_map = {pp.ticket: pp for pp in positions} if positions else {}

    for ticket, tracker in state.open_trades.items():
        if tracker.direction == "BUY":
            cp = price_to_pips(tick.bid - tracker.entry_price)
        else:
            cp = price_to_pips(tracker.entry_price - tick.ask)
        if cp > tracker.max_favorable_pips:
            tracker.max_favorable_pips = cp
        if cp < 0 and abs(cp) > tracker.max_adverse_pips:
            tracker.max_adverse_pips = abs(cp)
        if ticket in pos_map and pos_map[ticket].sl != 0.0:
            tracker.sl_current = pos_map[ticket].sl


# ============================================================================
# §18. Gemini合議 (placeholder - charter.mdから読み込み)
# ============================================================================
def load_execution_charter() -> str:
    """charter.mdから実行憲章を読み込む。なければ埋め込みデフォルトを使用"""
    if os.path.exists(CHARTER_FILE):
        try:
            with open(CHARTER_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return "【EXECUTION_CHARTER未設定】charter.mdを配置してください。"


GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "council_a": {"type": "STRING"},
        "council_b": {"type": "STRING"},
        "council_c": {"type": "STRING"},
        "council_d": {"type": "STRING"},
        "action": {"type": "STRING", "enum": ["BUY", "SELL", "WAIT", "CLOSE"]},
        "sl": {"type": "NUMBER"},
        "reason": {"type": "STRING"},
    },
    "required": ["action", "sl", "reason", "council_a", "council_b", "council_c", "council_d"],
}


def ask_gemini_council(market_json: str, ref_images: List[PIL.Image.Image],
                       chart_images: List[PIL.Image.Image]) -> str:
    """Gemini合議を実行"""
    if gemini_client is None:
        return '{"action":"WAIT","sl":0,"reason":"未初期化","council_a":"","council_b":"","council_c":"","council_d":""}'

    charter = load_execution_charter()
    prompt = (
        f"【厳命】JSONデータ内の数値を画像の視覚印象より絶対優先せよ。"
        f"特にSMA21_ANGLE, ADX_VALUE, ADX_SLOPE, ADX_LAST3, MACD_LINE_LAST3, "
        f"STOCH_SLOW_D_LAST3, TF_ALIGNMENT, BB_WIDTH_PIPS, PRICE_SIGMA_POSITION, "
        f"RESTRICTED_TIME_ACTIVE, ENTRY_WINDOW_OPEN を必ず参照すること。"
        f"\n\n{market_json}\n{charter}"
    )
    contents: list = [prompt]
    contents.extend(chart_images)
    contents.extend(ref_images)

    try:
        from google.genai import types as genai_types
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GEMINI_RESPONSE_SCHEMA,
        )
    except Exception:
        config = None

    for attempt in range(4):
        try:
            if config:
                res = gemini_client.models.generate_content(
                    model="gemini-2.5-pro-preview-05-06", contents=contents, config=config)
            else:
                res = gemini_client.models.generate_content(
                    model="gemini-2.5-pro-preview-05-06", contents=contents)
            if not res or not hasattr(res, "text") or not res.text:
                raise Exception("Empty response")
            return res.text
        except Exception as e:
            wait = 30 + attempt * 10 if "429" in str(e).lower() else 2 ** attempt
            logging.warning(f"Gemini API リトライ({attempt+1}): {e} (待機{wait}秒)")
            time.sleep(wait)

    return '{"action":"WAIT","sl":0,"reason":"APIエラー","council_a":"","council_b":"","council_c":"","council_d":""}'


def parse_decision(decision: str) -> Tuple[str, float]:
    """Gemini応答をパースしてaction, slを抽出"""
    action, sl = "WAIT", 0.0
    try:
        clean = re.sub(r"^```(?:json)?\s*", "", decision.strip())
        clean = re.sub(r"\s*```$", "", clean)
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "action" in parsed:
            raw_act = str(parsed["action"]).upper().strip()
            if raw_act in ("BUY", "SELL", "WAIT", "CLOSE"):
                action = raw_act
            raw_sl = parsed.get("sl", 0)
            if raw_sl and float(raw_sl) > 0:
                sl = float(raw_sl)
    except Exception:
        act_m = re.search(r"ACTION:\s*(BUY|SELL|WAIT|CLOSE)", decision, re.IGNORECASE)
        action = act_m.group(1).upper() if act_m else "WAIT"
        sl_m = re.search(r"^SL:\s*([\d\.,]+)", decision, re.IGNORECASE | re.MULTILINE)
        if sl_m:
            try:
                sl = float(sl_m.group(1).replace(",", "."))
            except Exception:
                sl = 0.0

    if sl > 0:
        tick = mt5.symbol_info_tick(SYMBOL)
        if tick:
            dist = price_to_pips(abs(sl - tick.bid))
            dir_ok = (action == "BUY" and sl < tick.bid) or (action == "SELL" and sl > tick.ask) or action in ("WAIT", "CLOSE")
            if dist > SL_MAX_DISTANCE_PIPS or not dir_ok:
                logging.warning(f"SL検証失敗: {sl}")
                sl = 0.0
    return action, sl


def sanitize_gemini_output(text: str, max_length: int = 5000) -> str:
    """Gemini出力をLINE通知用にフォーマット"""
    try:
        clean = re.sub(r"^```(?:json)?\s*", "", text.strip())
        clean = re.sub(r"\s*```$", "", clean)
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "action" in parsed:
            lines = ["【4人格合議】"]
            for key, name in [("council_a", "分析官A"), ("council_b", "監査官B"),
                              ("council_c", "金庫番C"), ("council_d", "議長D")]:
                if parsed.get(key):
                    lines.append(f"■{name}: {parsed[key]}")
            lines.append(f"\n【執行】{parsed.get('action','WAIT')} SL:{parsed.get('sl',0)}")
            if parsed.get("reason"):
                lines.append(f"理由: {parsed['reason']}")
            text = "\n".join(lines)
    except Exception:
        pass
    return re.sub(r"[A-Za-z0-9_-]{30,}", "[MASKED]", text)[:max_length]


# ============================================================================
# §19. アクション処理
# ============================================================================
# §19a. PostSignalGate（エントリー前関門）— PhantomOS準拠
# ============================================================================
def check_post_signal_gate(action: str, entry_price: float, sl_price: float,
                           state: BotState, anomaly_result: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Geminiが返したBUY/SELLをPython側で物理的にチェック。
    幻覚（あり得ないロット/逆方向SL等）を遮断する最後の防衛線。
    """
    # G1: SL方向
    if action == "BUY" and sl_price >= entry_price:
        return False, f"SL方向逆転: BUYでSL({sl_price})≧entry({entry_price})"
    if action == "SELL" and sl_price <= entry_price:
        return False, f"SL方向逆転: SELLでSL({sl_price})≦entry({entry_price})"

    # G2: SL距離（最小/最大）
    sl_pips = price_to_pips(abs(entry_price - sl_price))
    if sl_pips < MIN_SL_PIPS:
        return False, f"SL幅不足: {sl_pips:.1f}pips < {MIN_SL_PIPS}"
    if sl_pips > SL_MAX_DISTANCE_PIPS:
        return False, f"SL幅過大: {sl_pips:.1f}pips > {SL_MAX_DISTANCE_PIPS}"

    # G3: リスク金額（資金の3%以内）
    acc = mt5.account_info()
    if acc:
        info = mt5.symbol_info(SYMBOL)
        if info and info.trade_tick_size > 0:
            diff = abs(entry_price - sl_price)
            loss_per_lot = (diff / info.trade_tick_size) * info.trade_tick_value
            if loss_per_lot > 0:
                max_lot = (acc.balance * GATE_MAX_RISK_PCT) / loss_per_lot
                if max_lot < info.volume_min:
                    return False, f"リスク超過: 最小ロットでも資金{GATE_MAX_RISK_PCT*100:.0f}%超"

    # G4: スプレッド正常性
    if anomaly_result.get("block_entry", False):
        return False, "異常ガードによるエントリー禁止中"

    # G5: 既存ポジション重複
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions:
        for pos in positions:
            if pos.magic == MAGIC_NUMBER:
                return False, f"既存ポジションあり: Ticket {pos.ticket}"

    # G6: M30スクイーズ（BB幅50pip未満 & SMA角度30度未満）
    m30 = fetch_and_calc_labels(mt5.TIMEFRAME_M30)
    if m30:
        bb_w = m30.get("BB_WIDTH_PIPS", 0)
        sma_a = abs(m30.get("SMA21_ANGLE", 0))
        if bb_w < 50.0 and sma_a < 30.0:
            return False, f"M30スクイーズ: BB幅{bb_w:.1f}pip, SMA角度{sma_a:.1f}度"

    logging.info(f"【Gate APPROVED】{action} entry={entry_price} sl={sl_price} sl_pips={sl_pips:.1f}")
    return True, ""


# ============================================================================
# §19b. アクション処理
# ============================================================================
def handle_action(action: str, sl: float, has_pos: bool, p: Any, decision: str,
                  is_entry_allowed: bool, state: BotState, council_label: str = "",
                  anomaly_result: Optional[Dict[str, Any]] = None) -> None:
    """Gemini合議結果に基づきトレードを実行（PostSignalGate統合）"""
    if action == "CLOSE" and has_pos:
        if close_position(p.ticket, p.type, p.volume):
            tracker = state.open_trades.pop(p.ticket, None)
            if tracker:
                deal_info = resolve_closed_trade(p.ticket)
                if deal_info:
                    log_trade_result(tracker, deal_info["exit_price"], "ai_close", state,
                                     commission=deal_info.get("commission", 0.0),
                                     swap=deal_info.get("swap", 0.0),
                                     mt5_profit=deal_info.get("profit"))
                else:
                    tick = mt5.symbol_info_tick(SYMBOL)
                    if tick:
                        log_trade_result(tracker, tick.bid if p.type == mt5.ORDER_TYPE_BUY else tick.ask, "ai_close", state)
            bal = mt5.account_info().balance if mt5.account_info() else "不明"
            send_line_notify(f"【決済完了】AI判断\n残高: {bal}円\n\n{sanitize_gemini_output(decision, 2000)}")
        return

    if not has_pos and action in ("BUY", "SELL"):
        if not is_entry_allowed:
            return
        tick = mt5.symbol_info_tick(SYMBOL)
        if not tick:
            return
        entry = tick.ask if action == "BUY" else tick.bid
        if sl <= 0:
            rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M30, 0, 30)
            if rates is not None and len(rates) >= 30:
                atr = calculate_atr(pd.DataFrame(rates)).iloc[-1]
                if not np.isnan(atr):
                    sl = round(entry - atr * 1.5 if action == "BUY" else entry + atr * 1.5, 3)
            if sl <= 0:
                sl = round(entry - pips_to_price(MIN_SL_PIPS) if action == "BUY" else entry + pips_to_price(MIN_SL_PIPS), 3)

        # ★PostSignalGate: エントリー前の最終関門
        gate_ok, gate_reason = check_post_signal_gate(
            action, entry, sl, state, anomaly_result or {})
        if not gate_ok:
            logging.warning(f"【Gate REJECTED】{gate_reason}")
            send_line_notify(f"⛔ エントリー拒否\n{gate_reason}")
            return

        success, vol, pr, risk = execute_trade(
            mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL, sl, state)
        if success:
            positions = mt5.positions_get(symbol=SYMBOL)
            if positions:
                new_ticket = positions[0].ticket
                state.open_trades[new_ticket] = TradeTracker(
                    ticket=new_ticket, direction=action, entry_price=pr,
                    sl_initial=sl, entry_time=datetime.datetime.now().isoformat(),
                    council_label=council_label,
                    market_state=get_market_snapshot_for_trade(),
                )
            send_line_notify(
                f"【{action}成功】執行:{pr}\nSL:{sl}\nロット:{vol}\n想定損失:{risk}円\n\n"
                f"{sanitize_gemini_output(decision, 2000)}"
            )


def get_market_snapshot_for_trade() -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    try:
        m30 = fetch_and_calc_labels(mt5.TIMEFRAME_M30)
        if m30:
            snapshot["bb_width_pips"] = m30.get("BB_WIDTH_PIPS")
            snapshot["sma_angle"] = m30.get("SMA21_ANGLE")
            snapshot["sma_dir"] = m30.get("SMA21_DIR")
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M30, 0, 30)
        if rates is not None and len(rates) >= 14:
            atr_val = calculate_atr(pd.DataFrame(rates)).iloc[-1]
            if not np.isnan(atr_val):
                snapshot["atr_pips"] = round(price_to_pips(atr_val), 1)
    except Exception:
        pass
    return snapshot


# ============================================================================
# §20. メインループ
# ============================================================================
_shutdown_event = threading.Event()


def _signal_handler(signum, frame):
    logging.info(f"シグナル{signum}受信。シャットダウンを開始します。")
    _shutdown_event.set()


def main_loop() -> None:
    logging.info(f"VMA v{VERSION} 起動中...")
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    prevent_sleep()

    phase_mgr = PhaseManager()
    anomaly_guard = AnomalyGuard()
    ref_images = []
    for path in REFERENCE_IMAGE_PATHS:
        if os.path.exists(path):
            try:
                with PIL.Image.open(path) as img:
                    ref_images.append(img.copy())
            except Exception:
                pass

    try:
        if not mt5.initialize():
            logging.critical("MT5初期化失敗")
            return
        state = BotState.load()

        # anomaly再発カウンタをSQLiteから復元（再起動耐性）
        halt_history = persistence_db.get_recent_anomaly_halt_times(ANOMALY_ESCALATION_WINDOW)
        if halt_history:
            anomaly_guard.restore_halt_history(halt_history)
            logging.info(f"【異常ガード復元】直近halt {len(halt_history)}件を復元")

        chart_cache = ChartCache(_shutdown_event)
        chart_cache.start()

        phase_mgr.transition("init_done")
        send_line_notify(f"🟢 VMA v{VERSION} 起動完了")

        while not _shutdown_event.is_set():
            try:
                if not check_and_reconnect_mt5():
                    time.sleep(ERROR_COOLDOWN)
                    continue

                now = datetime.datetime.now()
                tick = mt5.symbol_info_tick(SYMBOL)

                # --- §4 Python側異常ガード（毎ループ実行）---
                anomaly = anomaly_guard.check(tick)
                if anomaly["alerts"]:
                    for alert in anomaly["alerts"]:
                        logging.warning(f"【異常ガード】{alert}")

                if anomaly["manual_locked"]:
                    # 手動解除ロック: 全操作停止。LINE通知して待機。
                    if not hasattr(main_loop, '_lock_notified'):
                        send_line_notify("🔴【手動解除ロック】異常が繰り返し発生。手動確認が必要です。")
                        main_loop._lock_notified = True
                    time.sleep(MAIN_LOOP_INTERVAL)
                    continue

                if anomaly["tick_frozen"]:
                    time.sleep(ERROR_COOLDOWN)
                    continue

                if tick is None:
                    time.sleep(ERROR_COOLDOWN)
                    continue

                # 異常イベントのDB記録
                if anomaly["block_entry"] and anomaly["alerts"]:
                    spread = price_to_pips(tick.ask - tick.bid) if tick else 0.0
                    evt_type = "anomaly_halt" if anomaly_guard.is_halted() else "block_entry"
                    persistence_db.insert_anomaly_event(
                        evt_type, spread, "; ".join(anomaly["alerts"]))

                # --- DD4層チェック（PhantomOS DDMonitor準拠）---
                # ★Blocker対応: spread異常中のみDD DISQUALIFYの成行全決済をバイパス。
                #   freeze_market_orders全般ではなく、spread_anomaly限定。
                #   手動ロック中やHALT復帰待ち中の実DDは正しく検出・退場させる。
                freeze_market = anomaly["freeze_market_orders"]
                spread_is_abnormal = anomaly["spread_anomaly"]
                dd_pct = get_dd_percent(state)
                dd_stage = get_dd_stage(dd_pct)
                if dd_stage == "DISQUALIFY":
                    if spread_is_abnormal:
                        # スプレッド異常中のみ: 成行決済せずログ。正常復帰後に再判定。
                        logging.warning(
                            f"【DD4層】DD {dd_pct:.1f}% ≧ {DD_DISQUALIFY_PCT}% だが "
                            f"スプレッド異常中のため成行全決済をバイパス。正常復帰後に再判定。")
                    else:
                        # スプレッド正常 or それ以外の停止理由: 本当のDD → 退場
                        logging.critical(f"【DD DISQUALIFY】DD {dd_pct:.1f}% → 全決済+停止")
                        close_all_positions_safely(state, "dd_disqualify")
                        phase_mgr.transition("dd_lock")
                        send_line_notify(f"🔴【DD DISQUALIFY】DD {dd_pct:.1f}%\n全決済+手動解除ロック")
                        time.sleep(MAIN_LOOP_INTERVAL)
                        continue

                # --- 週末処理 ---
                if is_weekend_close_time(now):
                    close_all_positions_safely(state, "weekend_close")
                    phase_mgr.transition("weekend")
                    time.sleep(WEEKEND_SLEEP)
                    phase_mgr.transition("weekday_resume")
                    continue

                # --- 通常処理 ---
                update_economic_calendar()
                # トレーリングは常に実行（SL位置修正のみ、成行注文なし）
                process_trailing_stop(state)
                # タイムストップは成行決済を伴うため、異常時は凍結
                if not freeze_market:
                    process_time_stop(state)
                update_trade_extremes(state)

                # クローズ検知
                pos = mt5.positions_get(symbol=SYMBOL)
                has_pos = bool(pos)
                p = pos[0] if pos else None

                if state.open_trades:
                    active_tickets = {pp.ticket for pp in pos} if pos else set()
                    closed = [t for t in state.open_trades if t not in active_tickets]
                    for ct in closed:
                        tracker = state.open_trades.pop(ct)
                        deal_info = resolve_closed_trade(ct)
                        if deal_info:
                            slippage = 0.0
                            if deal_info["exit_reason"] == "sl":
                                sl_ref = tracker.sl_current if tracker.sl_current != 0.0 else tracker.sl_initial
                                if sl_ref > 0:
                                    if tracker.direction == "BUY":
                                        slippage = price_to_pips(sl_ref - deal_info["exit_price"])
                                    else:
                                        slippage = price_to_pips(deal_info["exit_price"] - sl_ref)
                            log_trade_result(tracker, deal_info["exit_price"], deal_info["exit_reason"], state,
                                             slippage_pips=slippage,
                                             commission=deal_info.get("commission", 0.0),
                                             swap=deal_info.get("swap", 0.0),
                                             mt5_profit=deal_info.get("profit"))
                        else:
                            log_trade_result(tracker, tick.bid if tracker.direction == "BUY" else tick.ask, "unknown", state)
                    if closed:
                        state.save()

                # --- トリガー判定 ---
                if not phase_mgr.phase.can_trade():
                    time.sleep(MAIN_LOOP_INTERVAL)
                    continue

                # DD HALT以上は新規エントリー禁止
                if dd_stage in ("HALT", "DISQUALIFY"):
                    time.sleep(MAIN_LOOP_INTERVAL)
                    continue

                entry_blocked_by_anomaly = anomaly["block_entry"]

                # --- 状態B → B'化: Geminiを呼ばず急変動フラグのみ記録 ---
                is_b = detect_state_b()
                is_c = has_pos and detect_state_c(tick.bid)

                if is_b and not is_restricted_time(now) and not entry_blocked_by_anomaly:
                    # B'フラグ記録（Geminiは呼ばない）
                    # ★最新優先: TTL内でも新しい急変動が来たら上書きする
                    r1 = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 5)
                    b_dir = "UNKNOWN"
                    b_cum_pips = 0.0
                    if r1 is not None and len(r1) >= 3:
                        move = r1[-1]["close"] - r1[-3]["close"]
                        b_dir = "UP" if move > 0 else "DOWN"
                        b_cum_pips = round(abs(price_to_pips(move)), 1)
                    state._rapid_move_flag = {
                        "time": now.isoformat(),
                        "timestamp": time.time(),
                        "direction": b_dir,
                        "cumulative_pips": b_cum_pips,
                    }
                    logging.info(
                        f"【B'フラグ】急変動検知 → フラグ{'上書き' if hasattr(state, '_rapid_move_flag') else '記録'} "
                        f"(方向={b_dir}, 累積={b_cum_pips}pips)")

                if not is_c:
                    state.last_state_c_check = None

                decision, triggered, council_label = "", False, ""

                # 状態C: 保有中の決済判断（freeze_market中は凍結）
                if has_pos and is_c and not freeze_market:
                    if state.last_state_c_check is None or (now - state.last_state_c_check).total_seconds() >= STATE_C_COOLDOWN:
                        council_label, triggered = "状態C", True
                        state.last_state_c_check = now
                # 状態A: M30確定後の通常合議
                elif is_entry_window(now):
                    cur_c = now.replace(minute=(30 if now.minute >= 30 else 0), second=0, microsecond=0)
                    if state.last_normal_entry_check != cur_c and not is_restricted_time(now):
                        council_label, triggered = "状態A", True
                        state.last_normal_entry_check = cur_c

                if triggered:
                    phase_mgr.transition("council_start")
                    logging.info(f"【合議開始: {council_label}】")
                    chart_cache.force_refresh()
                    time.sleep(3)

                    market_json = get_market_data_optimized()

                    # B'フラグ: ★状態Aの時のみ参照・消費する。状態Cでは使わない。
                    b_flag_note = ""
                    if council_label == "状態A":
                        if hasattr(state, '_rapid_move_flag') and state._rapid_move_flag is not None:
                            flag = state._rapid_move_flag
                            elapsed = time.time() - flag.get("timestamp", 0)
                            if elapsed <= STATE_B_FLAG_TTL_SECONDS:
                                b_flag_note = (
                                    f"\n【参考情報】直前にM1レベルの急変動を検知"
                                    f"（{flag.get('direction','N/A')}方向、"
                                    f"累積{flag.get('cumulative_pips',0)}pips、"
                                    f"{elapsed:.0f}秒前）。"
                                    f"これは参考情報であり、エントリー方向へのバイアスとして"
                                    f"使ってはならない。M30背景と不一致なら無視せよ。"
                                )
                            # 状態Aで参照したらフラグ消費（1回限り）
                            state._rapid_move_flag = None

                    decision = ask_gemini_council(
                        market_json + b_flag_note, ref_images, chart_cache.get_images())
                    phase_mgr.transition("council_done")

                    logging.info(f"【AI合議結果】\n{sanitize_gemini_output(decision, 3000)}")
                    act, sl_val = parse_decision(decision)

                    persistence_db.insert_council_log(council_label, act, sl_val, {"decision": decision[:500]})

                    entry_allowed = (is_entry_window(now) and not is_restricted_time(now)
                                     and not entry_blocked_by_anomaly
                                     and dd_stage not in ("HALT", "DISQUALIFY"))
                    handle_action(act, sl_val, has_pos, p, decision, entry_allowed, state, council_label, anomaly)
                    state.save()

                time.sleep(MAIN_LOOP_INTERVAL)

            except Exception as loop_e:
                logging.error(f"【メインループ例外】{loop_e}", exc_info=True)
                time.sleep(ERROR_COOLDOWN)

    finally:
        allow_sleep()
        state.save()
        mt5.shutdown()
        send_line_notify(f"🔴 VMA v{VERSION} 終了")
        logging.info("VMA終了。")


if __name__ == "__main__":
    main_loop()
