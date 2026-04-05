# VMA Handover
最終更新: 2026-04-04

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.502
- 段階: v5.501 GPT差し戻し → v5.502修正済み → 再監査待ち
- GitHub: https://github.com/gulf7765-ux/VMA

### v5.502で対応した監査指摘（v5.501 GPT差し戻し分）
- P-M1(採用): 勝率閾値がSDMに残っている→B案方針と矛盾 → 勝率を全段階から完全削除
- P-M2(採用): CRITICAL→WARNING→再CRITICAL時の再通知欠落 → レベル低下時に通知記憶も引き下げ
- P-m1(採用): §21コメントに「全部WAIT病」残留 → 現在の責務に合わせて更新

### SDM設計方針（最終整理）
- **B案**: レンジ相場でも回し続ける。勝率低下はDDが管理。SDMはBOT故障検知に専念。
- SDMトリガー指標（故障/構造崩壊寄り）:
  - 連敗数（3/5/7）: SL配置やエントリーロジックの構造的問題
  - SL被弾率（60%/75%）: 損切り位置の構造的不適合
  - API失敗率（20%/40%）: Gemini APIの不安定・故障
  - 損益比（平均損失/平均利益 >= 3.0）: SL/TP配置の構造的崩壊
- SDMトリガーから除外した指標:
  - 勝率: レンジ相場で正常に下がる → DD4層が管轄
  - 連続WAIT: 状態CのKEEP誤検知+レンジ正常動作 → 監視不要

### リポジトリ構成
- vma_bot.py (2583行) メインBOT
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章
- HANDOVER.md 本ファイル
- README.md（異常ガード表修正済み） / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.502時点）
1. AnomalyGuard: スプレッド/凍結/ジャンプ即時検知、段階復帰、手動ロック
2. DD 4層: WARNING(8%)/REDUCTION(10%)/HALT(15%)/DISQUALIFY(20%)
3. PostSignalGate G1-G7: SL方向/距離/リスク金額/スプレッド/重複/スクイーズ/RR比
4. freeze_market_orders: 成行凍結（CLOSE含む）、トレーリング維持
5. B'フラグ: 急変動メモ→状態Aのみ参照→バイアス禁止
6. タイムストップ: 180分+1R未満→撤退
7. 非線形R倍数トレーリング: 1R/2R/3R/4R段階制
8. DD DISQUALIFYバイパス: spread_anomaly起因のみ
9. §21 SDM: 連敗/SL被弾率/API失敗率/損益比の4指標で故障検知

### 修正履歴
v5.000→v5.100→v5.200→v5.300→v5.400→v5.401(承認)→v5.500(SDM)→v5.501→v5.502

### 次のステップ
1. ~~自壊前兆監視~~ → v5.502で実装・修正済み、再監査待ち
2. analyzer.py移植
3. VMA専用憲章整備

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- api.github.comブロック。リポ作成はこうへい手動。
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 最新提出: v5.502 — 再監査待ち
v5.501: Gemini完全承認、GPT差し戻し(Major2+Minor1)を全件修正。

### 監査ラウンド履歴
1. v5.000 → 差し戻し（Blocker4件）
2. v5.100 → 方針承認
3. v5.200 → Gemini承認、GPT差し戻し
4. v5.300 → Gemini承認、GPT差し戻し
5. v5.400 → Gemini承認、GPT差し戻し
6. v5.401 → **両者承認**
7. v5.500 → 差し戻し（SDM論理破綻）
8. v5.501 → Gemini承認、GPT差し戻し（責務分離不徹底）
9. v5.502 → 再監査待ち（勝率削除+通知修正）

### 監査官の最後の発言
- GPT: 「SDMをBOT故障検知に限定するなら勝率閾値を外せ。通知欠落バグも直せ」
- Gemini: 「Blocker 0 / Major 0 で完全承認」

---

## C. 運用ルール

- 毎回こうへいに平易な説明を添える
- ユーザーへのメッセージには毎回更新日時を記載
- 末尾タグ [GEMINI_LAST] / [GPT_LAST] を毎回記載
- 監査官メッセージは1通統合・コードブロック・冒頭で名乗る
- PATはpush後即除去。.envは絶対にコミットしない
- VMAメソッドのトレードロジックは不可侵（変更はユーザー承認必須）
