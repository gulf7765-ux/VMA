# VMA Handover
最終更新: 2026-04-05

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.503
- 段階: **v5.502両監査官承認** → v5.503(GPT Minor対応) → 次フェーズ移行
- GitHub: https://github.com/gulf7765-ux/VMA

### 監査結果（v5.502時点）
- **GPT: 承認（Blocker 0 / Major 0 / Minor 1）** Minor=再起動時通知記憶未復元→v5.503で解消
- **Gemini: 完全承認（Blocker 0 / Major 0）**

### SDM設計方針（確定）
- B案: レンジ相場でも回す。勝率低下はDD管轄。SDMはBOT故障検知に専念。
- SDMトリガー: 連敗数(3/5/7), SL被弾率(60%/75%), API失敗率(20%/40%), 損益比(>=3.0)
- 除外: 勝率（DD管轄）, 連続WAIT（正常動作の誤検知）

### リポジトリ構成
- vma_bot.py (2585行) メインBOT
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章
- HANDOVER.md / README.md / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.503時点）
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
v5.000→v5.100→v5.200→v5.300→v5.400→v5.401(承認)→v5.500(SDM)→v5.501→v5.502(承認)→v5.503

### 次のステップ（両監査官同意済み）
1. ~~自壊前兆監視~~ → v5.503で完了
2. analyzer.py移植
3. VMA専用憲章整備

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- api.github.comブロック。リポ作成はこうへい手動。
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 最新: v5.503 (v5.502両監査官承認 + GPT Minor解消)

### 監査ラウンド履歴
1. v5.000 → 差し戻し（Blocker4件）
2. v5.100 → 方針承認
3. v5.200 → Gemini承認、GPT差し戻し
4. v5.300 → Gemini承認、GPT差し戻し
5. v5.400 → Gemini承認、GPT差し戻し
6. v5.401 → **両者承認**
7. v5.500 → 差し戻し（SDM論理破綻）
8. v5.501 → Gemini承認、GPT差し戻し（責務分離不徹底）
9. v5.502 → **両者承認**（GPT Minor 1のみ）
10. v5.503 → GPT Minor解消（restore_pause通知記憶同期）

### 監査官の最後の発言
- GPT: 「承認。片肺修正は見当たらない。影響範囲も概ね閉じている」
- Gemini: 「完全承認。VMAシステムの防衛網の根幹となる実装はすべて完了」

---

## C. 運用ルール

- 毎回こうへいに平易な説明を添える
- ユーザーへのメッセージには毎回更新日時を記載せよ
- 末尾タグ [GEMINI_LAST] / [GPT_LAST] を毎回記載
- 監査官メッセージは1通統合・コードブロック・冒頭で名乗る
- PATはpush後即除去。.envは絶対にコミットしない
- VMAメソッドのトレードロジックは不可侵（変更はユーザー承認必須）
