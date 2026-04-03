# VMA Handover
最終更新: 2026-04-04

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.501
- 段階: v5.500監査差し戻し → v5.501修正済み → 再監査待ち
- GitHub: https://github.com/gulf7765-ux/VMA

### v5.501で対応した監査指摘（v5.500差し戻し分）
- G-M1(採用): タイマー稼働中にcheck()がNORMALを返すねじれ → CRITICAL強制維持
- G-M2(採用): 全敗時avg_loss_win_ratio=99.0→CRITICAL誤発動 → Noneに修正
- P-M1(採用): CRITICAL母数が5件と曖昧 → SDM_CRITICAL_MIN_TRADES=10に分離
- G-m1(採用): 連続WAIT監視の削除 → 状態CのKEEP誤検知+レンジ正常動作との混同を排除
- P-M2(採用): READMEの異常ガード表が旧仕様 → 実装に合わせて全面修正
- P-m1(採用): HANDOVER別SHA → 全ファイル同一コミットに統一
- P-m2(採用): WAITフォールバックJSONにtp:0追加

### SDM設計方針（総司令官承認済み）
- **B案**: レンジ相場でも回し続ける。DDが管理。SDMはBOT故障検知に専念。
- SDMが検知すべきもの: API故障、7連敗以上の構造的崩壊、SL被弾率異常
- SDMが検知すべきでないもの: レンジ相場の一時的勝率低下、WAITの連続

### リポジトリ構成
- vma_bot.py (2599行) メインBOT
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章
- HANDOVER.md 本ファイル
- README.md（異常ガード表修正済み） / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.501時点）
1. AnomalyGuard: スプレッド/凍結/ジャンプ即時検知、段階復帰、手動ロック
2. DD 4層: WARNING(8%)/REDUCTION(10%)/HALT(15%)/DISQUALIFY(20%)
3. PostSignalGate G1-G7: SL方向/距離/リスク金額/スプレッド/重複/スクイーズ/RR比
4. freeze_market_orders: 成行凍結（CLOSE含む）、トレーリング維持
5. B'フラグ: 急変動メモ→状態Aのみ参照→バイアス禁止
6. タイムストップ: 180分+1R未満→撤退
7. 非線形R倍数トレーリング: 1R/2R/3R/4R段階制
8. DD DISQUALIFYバイパス: spread_anomaly起因のみ
9. §21 SelfDestructionMonitor: 自壊前兆3段階(CAUTION/WARNING/CRITICAL)

### 修正履歴
v5.000→v5.100→v5.200→v5.300→v5.400→v5.401(承認)→v5.500(SDM追加)→v5.501(監査修正)

### 次のステップ
1. ~~自壊前兆監視~~ → v5.501で実装・修正済み、再監査待ち
2. analyzer.py移植
3. VMA専用憲章整備

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- api.github.comブロック。リポ作成はこうへい手動。
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 最新提出: v5.501 — 再監査待ち
v5.500差し戻し(Major2+Minor1 from Gemini, Major2+Minor2 from GPT)を全件修正。

### 監査ラウンド履歴
1. v5.000 → 差し戻し（Blocker4件）
2. v5.100 → 方針承認
3. v5.200 → Gemini承認、GPT差し戻し
4. v5.300 → Gemini承認、GPT差し戻し
5. v5.400 → Gemini承認、GPT差し戻し
6. v5.401 → **両者承認**
7. v5.500 → 差し戻し（SDM論理破綻）
8. v5.501 → 再監査待ち（全件修正）

### 監査官の最後の発言
- GPT: 「CRITICALの入口を整理。停止条件を一本化」
- Gemini: 「先に停止条件を一本化してください」

---

## C. 運用ルール

- 毎回こうへいに平易な説明を添える
- ユーザーへのメッセージには毎回更新日時を記載
- 末尾タグ [GEMINI_LAST] / [GPT_LAST] を毎回記載
- 監査官メッセージは1通統合・コードブロック・冒頭で名乗る
- PATはpush後即除去。.envは絶対にコミットしない
- VMAメソッドのトレードロジックは不可侵（変更はユーザー承認必須）
