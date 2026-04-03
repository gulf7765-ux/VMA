"""
VMA Supervisor — プロセス監視＆自動再起動
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
使い方: python supervisor.py

機能:
  - vma_bot.py をサブプロセスとして起動・監視
  - 異常終了時に10秒待機後に自動再起動
  - LINE通知でクラッシュ/再起動を報告
  - 最大連続クラッシュ回数(5回)で完全停止
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import subprocess
import sys
import os
import time
import logging

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_SCRIPT = os.path.join(BASE_DIR, "vma_bot.py")
RESTART_WAIT = 10
MAX_CONSECUTIVE_CRASHES = 5
STABLE_RUN_SECONDS = 300

load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))
LINE_ACCESS_TOKEN = os.environ.get("LINE_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SUPERVISOR] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "supervisor.log"), encoding="utf-8"),
    ],
)


def send_line(msg: str) -> None:
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


def main():
    logging.info(f"Supervisor起動: 監視対象={TARGET_SCRIPT}")
    send_line("🔵 [Supervisor] VMA プロセス監視を開始しました。")

    consecutive_crashes = 0

    while True:
        start_time = time.time()
        logging.info(f"BOTプロセスを起動します... (クラッシュ回数: {consecutive_crashes})")

        try:
            proc = subprocess.Popen([sys.executable, TARGET_SCRIPT], cwd=BASE_DIR)
            exit_code = proc.wait()
        except KeyboardInterrupt:
            logging.info("Supervisor: Ctrl+Cで中断。")
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
            send_line("🟡 [Supervisor] 手動停止されました。")
            break
        except Exception as e:
            logging.error(f"プロセス起動失敗: {e}")
            exit_code = -1

        elapsed = time.time() - start_time

        if exit_code == 0:
            logging.info("BOTが正常終了。Supervisorも終了。")
            send_line("🟢 [Supervisor] VMA BOTが正常終了しました。")
            break

        if elapsed >= STABLE_RUN_SECONDS:
            consecutive_crashes = 0

        consecutive_crashes += 1
        logging.error(
            f"BOT異常終了 (code={exit_code}, 稼働{elapsed:.0f}秒, "
            f"連続{consecutive_crashes}/{MAX_CONSECUTIVE_CRASHES})"
        )

        if consecutive_crashes >= MAX_CONSECUTIVE_CRASHES:
            msg = (
                f"🔴 [Supervisor] VMA BOTが{MAX_CONSECUTIVE_CRASHES}回連続クラッシュ。\n"
                f"exit_code={exit_code}\n自動再起動を停止。手動確認が必要です。"
            )
            logging.critical(msg)
            send_line(msg)
            break

        send_line(
            f"🔄 [Supervisor] VMA BOTクラッシュ (code={exit_code})。\n"
            f"{RESTART_WAIT}秒後に再起動 ({consecutive_crashes}/{MAX_CONSECUTIVE_CRASHES})"
        )
        logging.info(f"{RESTART_WAIT}秒後に再起動...")
        time.sleep(RESTART_WAIT)

    logging.info("Supervisor終了。")


if __name__ == "__main__":
    main()
