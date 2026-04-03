# VMA Handover
最終更新: 2026-04-03

---

## A. Claude実務向けHandover

### プロジェクト状態
- プロジェクト名: VMA（VM Advance Trading System）
- 現在バージョン: v5.000
- 段階: ひな形構築完了 → 初回監査待ち
- GitHub: https://github.com/gulf7765-ux/VMA
- 最新SHA: 91bb14667016153ca249164f2f4f91847f3d174a（初回監査提出中）

### リポジトリ構成
```
VMA/
  vma_bot.py        (1736行) メインBOT
  supervisor.py     (120行)  プロセス監視
  README.md                  プロジェクト概要
  requirements.txt           依存パッケージ
  .env.example               環境変数テンプレート
  .gitignore                 除外設定
  HANDOVER.md                本ファイル
```

### 未配置ファイル（次ステップで対応必要）
- charter.md: Gemini実行憲章（これがないとGeminiがまともに判断できない）
- image_head_fake.png / image_chop.png / image_expansion_convergence.png: 参照画像
- analyzer.py: パフォーマンス分析（後日移植）

### 監査官への提出状況
- GPT: 初回メッセージ送付済み（返答待ち）
- Gemini: 初回メッセージ送付済み（返答待ち）
- 主要論点: トリガー体系（状態BのVMAメソッドとの矛盾）についてA/B/C案を提示中
- Claude推奨: B案（状態Bを監視強化モードに限定し、エントリーはM30確定まで待つ）

### 意図的除外項目（v4.003にあったが今回除外）
- 攻撃モード（ATTACK_RISK）
- UltimateRiskAnalyzer（Bootstrap信頼区間）
- self_destruction_precursor_detector（自壊前兆検知）
- ヘルスチェックHTTPサーバー

### 技術的な注意事項
- Geminiモデル名: gemini-2.5-pro-preview-05-06（利用可能性は要確認）
- GitHub push: api.github.comがClaudeの環境からブロックされている。
  リポジトリ作成はこうへいが手動で行う必要がある。pushはgithub.com経由で可能。
- PATトークン: こうへいがスレッドごとに提供する。

---

## B. 監査官向けHandover

### プロジェクト概要
VMAはBB背景分析を絶対的第1条件とするFXデイトレード自動売買BOT。
MT5 + Python + Gemini API構成。
VMAメソッド（詳細版50章）に基づき、BB±3σ/±2σのレジーム分類を
最優先にした売買判断を行う。

### 初回提出の概要
- SHA: 91bb14667016153ca249164f2f4f91847f3d174a
- 主要ファイル: vma_bot.py（1736行）、supervisor.py（120行）
- ベース: vm_advance_final v4.003（レジーム改訂前）
- 主な追加: AnomalyGuard（Python側即時異常検知）
- 主な除外: 攻撃モード、Bootstrap分析、自壊前兆検知

### 監査で提起した論点
1. トリガー体系: 状態B（M1急変動検知）がVMAの
   「M30確定ベース・フライング厳禁」と矛盾。A/B/C案を提示。
2. AnomalyGuardの閾値妥当性（仮値）
3. ANOMALY_HALTEDの自動復帰の安全性
4. ADX/MACD/ストキャスをPython側で計算してGeminiに渡すべきか
5. charter.md未配置問題

### 監査官の最後の指摘
- GPT: （初回・未発言）
- Gemini: （初回・未発言）

---

## C. 運用ルール（全スレッド共通・必ず遵守）

### ユーザー（こうへい）への説明義務
- **毎回、こうへいに向けて平易な日本語で説明を添えること。**
- 監査官向けメッセージ（コードブロック）だけを出力して終わりにしない。
- 専門用語には必ず一言補足する。
- 何をやっていて、何が起きていて、次に何をすべきかを常に伝える。
- こうへいはプログラミング素人。丁寧に、しかし冗長にならずに。

### メッセージ末尾の更新日時
- ユーザーへのメッセージには毎回更新日時を記載すること。

### 末尾タグ（絶対遵守）
- 毎回のメッセージ末尾に以下を記載:
  [GEMINI_LAST] （Geminiの最後の実質的発言）
  [GPT_LAST] （GPTの最後の実質的発言）

### 監査官メッセージの統合
- GPT・Gemini向けの監査メッセージは常に1通に統合して提出。
- 2通以上に分割しない。

### GitHub運用
- PATトークンはこうへいがスレッド冒頭で提供。毎回要求すること。
- pushは毎回単一commitで行う。
- SHA固定raw URLを監査官メッセージに必ず含める。
