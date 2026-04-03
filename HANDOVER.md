# VMA Handover
最終更新: 2026-04-04

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.500
- 段階: §21 SelfDestructionMonitor実装完了 → 監査提出
- GitHub: https://github.com/gulf7765-ux/VMA
- 最新SHA: 5fd04a450585752e2651dc9902eef6b971178d45

### 監査結果（前回v5.401）
- **GPT: 承認（Blocker 0 / Major 0）** Minor 2件のみ
- **Gemini: 完全承認（Blocker 0 / Major 0）** v5.300から3回連続承認

### GPT残Minor（v5.401からの持ち越し）
1. WAIT系フォールバックJSONにtp:0が未設定
2. ~~docstring先頭がv5.400のまま~~ → v5.500で解消

### リポジトリ構成
- vma_bot.py (2605行) メインBOT（v5.401の2219行から+386行）
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章（TP追加済み）
- HANDOVER.md 本ファイル
- README.md / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.500時点、9層）
1. AnomalyGuard: スプレッド/凍結/ジャンプ即時検知、段階復帰、手動ロック
2. DD 4層: WARNING(8%)/REDUCTION(10%)/HALT(15%)/DISQUALIFY(20%)
3. PostSignalGate G1-G7: SL方向/距離/リスク金額/スプレッド/重複/スクイーズ/RR比
4. freeze_market_orders: 成行凍結（CLOSE含む）、トレーリング維持
5. B'フラグ: 急変動メモ→状態Aのみ参照→バイアス禁止
6. タイムストップ: 180分+1R未満→撤退
7. 非線形R倍数トレーリング: 1R/2R/3R/4R段階制
8. DD DISQUALIFYバイパス: spread_anomaly起因のみ
9. **§21 SelfDestructionMonitor（NEW）**: 自壊前兆3段階検知

### §21 SelfDestructionMonitor設計概要
- データソース: SQLite trades + council_logs（ローリング集計）
- チェック間隔: 5分（SDM_CHECK_INTERVAL_SECONDS）
- 最小データ要件: 5トレード以上で判定開始（誤判定回避）
- 3段階エスカレーション:
  - CAUTION: LINE通知のみ
  - WARNING: リスク自動半減
  - CRITICAL: 1時間エントリー停止
- DD4層/連敗リスク減との複合適用
- 再起動耐性: CRITICAL pause_untilをBotStateに永続化
- 重複通知防止: レベル上昇時のみLINE通知
- CRITICAL解除: 時間経過のみ（自然解除）

### 配線ポイント（6箇所）
1. §1定数: SDM_*閾値（既存、変更なし）
2. §5 PersistenceDB: get_recent_council_logs()追加
3. §21 SelfDestructionMonitorクラス新設（約200行）
4. BotState: sdm_critical_pause_until追加（save/load）
5. get_dynamic_risk(): SDM WARNING→risk*0.5、CRITICAL→0.0
6. main_loop(): 5分間隔チェック + entry_allowed条件追加 + pause復元

### 修正履歴
v5.000→v5.100→v5.200→v5.300→v5.400→v5.401(承認)→v5.500(SDM)

### 次のステップ
1. ~~自壊前兆監視~~ → v5.500で実装完了
2. analyzer.py移植
3. VMA専用憲章整備

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- api.github.comブロック。リポ作成はこうへい手動。
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 最新提出: v5.500 (SHA: 5fd04a4)
前回v5.401がGPT/Gemini両監査官承認済み。今回は追加機能の監査。

### 監査ラウンド履歴
1. v5.000 → 差し戻し（Blocker4件）
2. v5.100 → 方針承認
3. v5.200 → Gemini承認、GPT差し戻し
4. v5.300 → Gemini承認、GPT差し戻し
5. v5.400 → Gemini承認、GPT差し戻し
6. v5.401 → **両者承認**
7. v5.500 → §21 SelfDestructionMonitor追加（監査中）

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
