# VMA Handover
最終更新: 2026-04-05

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.505
- 段階: v5.504(Gemini承認/GPT差し戻し) → v5.505修正 → 再監査待ち
- GitHub: https://github.com/gulf7765-ux/VMA

### v5.505で対応した監査指摘
- P-M1(採用): 状態A/C別のaction契約がPython側で閉じていない → handle_actionに契約違反検証+WAITへ正規化+ログ
- P-M2(採用): 通貨ペア対応の過大主張 → TradeTrackerにsymbol追加、log_trade_resultでtracker.symbol使用
- P-m1(採用): HANDOVER版ズレ → v5.505に統一

### 将来マルチペア方針（総司令官指示）
- 多数通貨ペアで運用予定。同時に2ポジション以上は持たない。
- TradeTrackerにsymbolフィールドを追加済み（構造受口確保）。
- 現時点はUSDJPY単体で妥当。マルチペア化は将来課題。

### リポジトリ構成
- vma_bot.py (2608行) メインBOT
- analyzer.py (678行) パフォーマンス分析
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章 v2
- HANDOVER.md / README.md / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.505時点）
1. AnomalyGuard: スプレッド/凍結/ジャンプ即時検知
2. DD 4層: WARNING(8%)/REDUCTION(10%)/HALT(15%)/DISQUALIFY(20%)
3. PostSignalGate G1-G7
4. freeze_market_orders: 成行凍結、トレーリング維持
5. B'フラグ: 急変動メモ→状態Aのみ参照
6. タイムストップ: 180分+1R未満→撤退
7. 非線形R倍数トレーリング
8. DD DISQUALIFYバイパス: spread_anomaly起因のみ
9. §21 SDM: 連敗/SL被弾率/API失敗率/損益比
10. 状態A/C action契約検証: 契約違反→WAITへ正規化

### 修正履歴
v5.000→...→v5.401(承認)→v5.500~v5.503(SDM承認)→v5.504(analyzer+charter)→v5.505

### 次のステップ
1. ~~自壊前兆監視~~ → 完了
2. ~~analyzer.py移植~~ → 完了（監査中）
3. ~~VMA専用憲章整備~~ → charter v2完了（監査中）
4. デモ環境テスト

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 最新提出: v5.505 — 再監査待ち

### 監査ラウンド履歴（抜粋）
6. v5.401 → **両者承認**
9. v5.502 → **両者承認**（SDM完了）
10. v5.504 → Gemini承認、GPT差し戻し（A/C契約+通貨ペア主張）
11. v5.505 → 再監査待ち

### 監査官の最後の発言
- GPT: 「charter v2が導入した状態別契約をPython側が正式契約として受けていない」
- Gemini: 「完全承認。Phase 4/5の中核完成」

---

## C. 運用ルール

- 毎回こうへいに平易な説明を添える
- ユーザーへのメッセージには毎回更新日時を記載せよ
- 末尾タグ [GEMINI_LAST] / [GPT_LAST] を毎回記載
- 監査官メッセージは1通統合・コードブロック・冒頭で名乗る
- PATはpush後即除去。.envは絶対にコミットしない
- VMAメソッドのトレードロジックは不可侵（変更はユーザー承認必須）
