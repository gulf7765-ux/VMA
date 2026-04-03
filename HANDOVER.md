# VMA Handover
最終更新: 2026-04-03

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.401
- 段階: **両監査官承認済み** → 次フェーズ（自壊前兆監視）へ移行
- GitHub: https://github.com/gulf7765-ux/VMA
- 最新SHA: 26f678e749e956f0f6ba156a154ffb53cc71acca

### 監査結果
- **GPT: 承認（Blocker 0 / Major 0）** Minor 2件のみ
- **Gemini: 完全承認（Blocker 0 / Major 0）** v5.300から3回連続承認

### GPT残Minor（次便で対応可）
1. WAIT系フォールバックJSONにtp:0が未設定
2. docstring先頭がv5.400のまま（VERSION定数は5.401）

### リポジトリ構成
- vma_bot.py (2219行) メインBOT
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章（TP追加済み）
- HANDOVER.md 本ファイル
- README.md / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.401時点）
1. AnomalyGuard: スプレッド/凍結/ジャンプ即時検知、段階復帰、手動ロック
2. DD 4層: WARNING(8%)/REDUCTION(10%)/HALT(15%)/DISQUALIFY(20%)
3. PostSignalGate G1-G7: SL方向/距離/リスク金額/スプレッド/重複/スクイーズ/RR比
4. freeze_market_orders: 成行凍結（CLOSE含む）、トレーリング維持
5. B'フラグ: 急変動メモ→状態Aのみ参照→バイアス禁止
6. タイムストップ: 180分+1R未満→撤退
7. 非線形R倍数トレーリング: 1R/2R/3R/4R段階制
8. DD DISQUALIFYバイパス: spread_anomaly起因のみ

### 修正履歴
v5.000→v5.100→v5.200→v5.300→v5.400→v5.401(承認)

### 次のステップ（両監査官同意済み）
1. 自壊前兆監視
2. analyzer.py移植
3. VMA専用憲章整備

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- api.github.comブロック。リポ作成はこうへい手動。
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 承認済み: v5.401 (SHA: 26f678e)
GPT/Gemini両監査官承認。Blocker 0 / Major 0。

### 監査ラウンド履歴
1. v5.000 → 差し戻し（Blocker4件）
2. v5.100 → 方針承認
3. v5.200 → Gemini承認、GPT差し戻し
4. v5.300 → Gemini承認、GPT差し戻し
5. v5.400 → Gemini承認、GPT差し戻し
6. v5.401 → **両者承認**

### 監査官の最後の発言
- GPT: 「承認。次は自壊前兆監視→analyzer→憲章」
- Gemini: 「完全承認。次フェーズ着手を承認」

---

## C. 運用ルール

- 毎回こうへいに平易な説明を添える
- 末尾タグ [GEMINI_LAST] / [GPT_LAST] を毎回記載
- 監査官メッセージは1通統合・コードブロック・冒頭で名乗る
- PATはpush後即除去。.envは絶対にコミットしない
- VMAメソッドのトレードロジックは不可侵（変更はユーザー承認必須）
- 更新日時を毎回記載
