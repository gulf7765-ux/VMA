# VMA — VM Advance Trading System

BB背景分析最優先 × Gemini 4ペルソナ合議 × Python側異常ガード  
FXデイトレード自動売買システム（MT5）

## アーキテクチャ

```
supervisor.py          プロセス監視 & 自動再起動
  └── vma_bot.py       メインBOT
        ├── AnomalyGuard     Python側即時異常検知（スプレッド/凍結/ジャンプ）
        ├── BotPhase状態マシン  INITIALIZING → MONITORING → COUNCIL_PENDING ...
        ├── Gemini合議        4ペルソナ（分析官/監査官/金庫番/議長）
        ├── TrailingStop     非線形R倍数段階制
        ├── TimeStop         撤退の美学（3時間撤退）
        ├── PersistenceDB    SQLite + JSONL二重永続化
        └── LINE通知          エントリー/決済/異常/緊急停止
```

## セットアップ

1. `.env.example` を `.env` にコピーしてAPIキーを設定
2. `pip install MetaTrader5 requests python-dotenv pandas numpy mplfinance matplotlib Pillow google-genai scipy`
3. `charter.md` にGemini実行憲章を配置
4. 参照画像3枚を配置: `image_head_fake.png`, `image_chop.png`, `image_expansion_convergence.png`

## 起動

```bash
python supervisor.py
```

## Python側異常ガード（AnomalyGuard）

Gemini APIを使わず、メインループ内(10秒間隔)でPython側が即時判定:

| 異常種別 | 閾値 | 動作 |
|---|---|---|
| スプレッド警告 | 3.0 pips | ログ警告 |
| スプレッド禁止 | 5.0 pips | エントリー禁止 + 成行操作凍結（既存ポジはSLに委任） |
| スプレッド異常 | 10.0 pips | エントリー禁止 + 成行操作凍結（既存ポジはSLに委任） |
| ティック凍結 | 30秒 | エントリー禁止 |
| 価格ジャンプ | 30 pips | エントリー禁止 |

**設計原則**: 安全装置が致命傷を引き起こしてはならない（PhantomOS実装憲章第4条）。
スプレッド異常時に成行決済すると不利なレートで約定するリスクがあるため、
成行操作を凍結し、既存ポジションのSLに決済を委任する。
