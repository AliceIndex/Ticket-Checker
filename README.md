# 学祭ライブ 学生証確認サービス

学祭音楽ライブの特設窓口において、スタッフが来場者の学生証を目視確認し、座席番号のステータス照会・確認登録・取消をリアルタイムに行うサーバーレスWebアプリケーションです。

---

## ディレクトリ構成

```text
.
├── index.html               # フロントエンド画面 (GitHub Pages ホスティング対象)
├── style.css                # モバイルファースト UI スタイルシート
├── app.js                   # フロントエンド制御 (揮発性担当者保持・API連携・リトライ制御)
├── MANUAL.md                # 当日スタッフ向け簡易操作マニュアル & トラブルシューティング
└── backend/                 # AWS バックエンド & 検証ツール一式
    ├── template.yaml        # AWS SAM テンプレート (DynamoDB + Lambda + HTTP API Gateway)
    ├── lambda_function.py   # AWS Lambda ハンドラー (GET / PUT /tickets/{seat_id})
    ├── seed_data.py         # DynamoDB 初期データ400件(B001〜B400)投入・リセットスクリプト
    ├── local_server.py      # ローカルE2E結合テスト用サーバー (AWS不要で動作確認可能)
    └── test_backend.py      # バックエンド自動テストスイート (Phase 3 検証項目)
```

---

## ローカルでの動作確認・テスト方法

### 1. バックエンド単体・結合テストの実行
```powershell
python backend/test_backend.py
```

### 2. ローカル統合サーバーでのブラウザ検証
AWS環境にデプロイする前でも、ローカルサーバーを起動して本番同等のAPI通信を検証できます。
```powershell
python backend/local_server.py --port 8000
```
ブラウザで `http://localhost:8000/?api=http://localhost:8000` を開くと、ローカルの `lambda_function.py` とインメモリDB（`B001`〜`B400` の400席）に接続された状態で動作します。
また、`index.html` をそのまま開いた場合（API未設定時）でも、フロントエンド内蔵のモックモード（`B001`〜`B400`）で即座にUI操作性を確認できます。

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

### 2. 対象座席（400件: B001〜B400）の初期データ投入・当日直前リセット
```powershell
# デフォルト (B001〜B400 の計400席) を UNCHECKED で一括登録
python backend/seed_data.py --table-name StudentTicketChecks --region ap-northeast-1

# 座席範囲をカスタマイズする場合
python backend/seed_data.py --ranges B:1-400 --region ap-northeast-1
```

### 3. フロントエンドの API URL 設定（GitHub Actions + Secrets 併用）
1. GitHub リポジトリ（`AliceIndex/Ticket-Checker`）の **Settings > Pages** を開き、**Build and deployment > Source** を **`GitHub Actions`** に変更します。
2. **Settings > Secrets and variables > Actions** を開き、**New repository secret** をクリックします。
   * **Name**: `API_BASE_URL`
   * **Secret**: API Gateway のデフォルトエンドポイントURL（例: `https://xxxx.execute-api.ap-northeast-1.amazonaws.com`）
3. `main` ブランチへのプッシュ時（または **Actions** タブから `Deploy to GitHub Pages` の **Run workflow** 手動実行時）に、自動で `config.js` に API URL が注入されてデプロイされます。
   * ※さらに、アクセスURLのクエリパラメータ（`?api=https://...`）や画面下部の「接続設定」からも上書き指定が可能です。

