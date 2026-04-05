# VMA Handover
最終更新: 2026-04-05

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.505
- 段階: **全フェーズ両監査官承認完了** → デモ稼働準備へ
- GitHub: https://github.com/gulf7765-ux/VMA

### 監査結果（v5.505）
- **GPT: 承認** Major 2は説明補正で取り下げ。残り全件解消。
- **Gemini: 完全承認（Blocker 0 / Major 0）**

### 完了した全フェーズ
1. ~~v5.401: BOT本体+安全装置~~ → 両者承認
2. ~~v5.503: §21 SDM自壊前兆監視~~ → 両者承認
3. ~~v5.504: analyzer.py移植~~ → 両者承認
4. ~~v5.505: charter v2 + A/C契約検証 + symbol構造受口~~ → 両者承認

### 将来マルチペア方針（総司令官指示・メモリ記録済み）
- 複数通貨ペアのチャートを巡回監視→最初にエントリー条件成立したペアでエントリー→決済までそのペアのみ監視→決済後に巡回監視モードへ戻る
- 同時2ポジション以上は持たない
- 現時点はUSDJPY単体で妥当。マルチペア化はmain_loopの構造変更を伴う大改修であり、将来課題

### リポジトリ構成
- vma_bot.py (2608行) メインBOT
- analyzer.py (678行) パフォーマンス分析
- supervisor.py (120行) プロセス監視
- charter.md Gemini実行憲章 v2
- HANDOVER.md / README.md / requirements.txt / .env.example / .gitignore

### 実装済み安全装置（v5.505時点・全10層）
1. AnomalyGuard: スプレッド/凍結/ジャンプ即時検知
2. DD 4層: WARNING(8%)/REDUCTION(10%)/HALT(15%)/DISQUALIFY(20%)
3. PostSignalGate G1-G7
4. freeze_market_orders: 成行凍結、トレーリング維持
5. B'フラグ: 急変動メモ→状態Aのみ参照
6. タイムストップ: 180分+1R未満→撤退
7. 非線形R倍数トレーリング
8. DD DISQUALIFYバイパス: spread_anomaly起因のみ
9. §21 SDM: 連敗/SL被弾率/API失敗率/損益比
10. 状態A/C action契約検証

### 修正履歴
v5.000→v5.401(承認)→v5.503(SDM承認)→v5.505(analyzer+charter承認)

### 次のステップ
- デモ環境テスト

### 技術注意
- Geminiモデル: gemini-2.5-pro-preview-05-06
- PATはスレッドごと提供。push後即除去。

---

## B. 監査官向けHandover

### 最新承認: v5.505 (SHA: 576e62c)
GPT/Gemini両監査官承認。全フェーズ完了。

### 監査ラウンド履歴（主要マイルストーン）
6. v5.401 → **両者承認**（BOT本体+安全装置）
9. v5.502/v5.503 → **両者承認**（SDM自壊前兆監視）
11. v5.505 → **両者承認**（analyzer+charter v2+A/C契約）

### 監査官の最後の発言
- GPT: 「Major 2は説明補正で解消。v5.505差し戻し理由としては使わない」
- Gemini: 「全監査指摘事項がクリア。次のステップへの進行を許可」

---

## C. 運用ルール

- 毎回こうへいに平易な説明を添える
- ユーザーへのメッセージには毎回更新日時を記載せよ
- 末尾タグ [GEMINI_LAST] / [GPT_LAST] を毎回記載
- 監査官メッセージは1通統合・コードブロック・冒頭で名乗る
- PATはpush後即除去。.envは絶対にコミットしない
- VMAメソッドのトレードロジックは不可侵（変更はユーザー承認必須）
