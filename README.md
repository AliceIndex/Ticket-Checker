# 学祭ライブ 学生証確認・入場管理サービス

学祭音楽ライブの特設窓口での「学生証確認」、入場ゲートでの「実入場受付・二重入場防止」、および本部向けの「リアルタイム入場統計ダッシュボード」を提供するサーバーレスWebアプリケーションです。

---

## ディレクトリ構成

```text
.
├── index.html               # トップポータル画面 (モード選択ハブ)
├── check.html               # 学生証確認モード (特設窓口用)
├── admission.html           # 実入場受付モード (入場ゲート用)
├── data.html                # 入場データ統計ダッシュボード (本部・統括用)
├── config.js                # 共通APIベースURL設定
├── style.css                # 共通モバイルファースト UI スタイルシート
├── app-common.js            # 共通ユーティリティ (URL解決・リンク同期・通信制御)
├── app-check.js             # 学生証確認モード制御スクリプト
├── app-admission.js         # 実入場受付モード制御スクリプト
├── app-data.js              # データ集計・時間帯推移・リスト制御スクリプト
├── MANUAL.md                # 当日スタッフ向け操作マニュアル (窓口・ゲート・本部編)
├── 仕様書.md                # 要件定義・技術仕様書
├── 変更計画書.md            # サイト構成変更・入場管理機能追加 変更計画書
├── 実装計画書.md            # 実装計画書
└── backend/                 # AWS バックエンド & 検証ツール一式
    ├── template.yaml        # AWS SAM テンプレート (DynamoDB 2テーブル + Lambda + HTTP API)
    ├── lambda_function.py   # AWS Lambda ハンドラー (/tickets/* & /admissions/*)
    ├── seed_data.py         # DynamoDB 初期データ800件投入・リセットスクリプト (両テーブル対応)
    ├── local_server.py      # ローカルE2E結合テスト用サーバー (AWS不要で全機能検証可能)
    └── test_backend.py      # バックエンド自動テストスイート
```

---

## ローカルでの動作確認・テスト方法

### 1. バックエンド単体・結合テストの実行
```powershell
python backend/test_backend.py
```

### 2. ローカル統合サーバーでのブラウザE2E検証
AWS環境にデプロイする前でも、ローカルサーバーを起動して本番同等のAPI通信を検証できます。
```powershell
python backend/local_server.py --port 8000
```
ブラウザで `http://localhost:8000/?api=http://localhost:8000` を開くと、トップポータルから「実入場受付モード」「学生証確認モード」「入場データダッシュボード」のすべてがローカルの `lambda_function.py` とインメモリDB（`A001`〜`A400`, `B001`〜`B400` 計800席）に接続された状態で動作します。
また、各HTMLファイルを直接開いた場合（API未設定時）でも、内蔵のローカル検証モードで即座にUI操作性を確認できます。

---

## AWS デプロイ & 初期データ投入手順

### 1. バックエンドのデプロイ (AWS SAM)
```powershell
cd backend
sam build
sam deploy --guided
```
* パラメータ `AllowedOrigin` には GitHub Pages のオリジン（例: `https://aliceindex.github.io`、検証中は `*`）を指定します。
* デプロイ完了時に出力される `ApiEndpoint`（例: `https://xxxx.execute-api.ap-northeast-1.amazonaws.com`）を控えます。

### 2. 対象座席（800件: A001〜A400, B001〜B400）の初期データ投入・当日直前リセット
```powershell
# 確認用テーブル (StudentTicketChecks) と 入場用テーブル (StudentTicketAdmissions) の両方を一括初期化
python backend/seed_data.py --region ap-northeast-1

# 片方のテーブルのみ初期化する場合
python backend/seed_data.py --target checks --region ap-northeast-1
python backend/seed_data.py --target admissions --region ap-northeast-1
```

### 3. フロントエンドの API URL 設定（GitHub Actions + Secrets 併用）
1. GitHub リポジトリの **Settings > Pages** で **Source** を **`GitHub Actions`** に設定します。
2. **Settings > Secrets and variables > Actions** で `API_BASE_URL` に API Gateway のエンドポイントURLを設定します。
3. `main` ブランチへのプッシュ時に、自動で `config.js` に API URL が注入されて全ページ（`index.html`, `check.html`, `admission.html`, `data.html`）がデプロイされます。
