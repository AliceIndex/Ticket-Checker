#!/usr/bin/env python3
"""
学祭ライブ 学生証確認サービス
ローカル開発・結合テスト用サーバー (backend/local_server.py)

AWS 環境に接続せずにローカルでフロントエンド (index.html / app.js) と
Lambda ハンドラー (backend/lambda_function.py) のエンドツーエンド疎通を検証できます。

起動方法:
  python backend/local_server.py --port 8000
  ブラウザで http://localhost:8000/?api=http://localhost:8000 にアクセス
"""

import argparse
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from lambda_function import ClientError, lambda_handler
from seed_data import generate_initial_records


class InMemoryDynamoTable:
    """DynamoDB Table (get_item / update_item / put_item) のインメモリ互換モック"""

    def __init__(self, initial_records: list[dict]):
        self.store: dict[str, dict] = {
            item["seat_id"]: dict(item) for item in initial_records
        }

    def get_item(self, Key: dict) -> dict:
        seat_id = Key.get("seat_id")
        if seat_id in self.store:
            return {"Item": dict(self.store[seat_id])}
        return {}

    def update_item(
        self,
        Key: dict,
        UpdateExpression: str,
        ConditionExpression: str,
        ExpressionAttributeNames: dict,
        ExpressionAttributeValues: dict,
        ReturnValues: str = "ALL_NEW",
    ) -> dict:
        seat_id = Key.get("seat_id")
        if ConditionExpression == "attribute_exists(seat_id)" and seat_id not in self.store:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The conditional request failed",
                    }
                },
                "UpdateItem",
            )

        record = self.store.setdefault(seat_id, {"seat_id": seat_id})
        record["status"] = ExpressionAttributeValues[":status"]
        record["updated_at"] = ExpressionAttributeValues[":updated_at"]
        record["updated_by"] = ExpressionAttributeValues[":updated_by"]
        return {"Attributes": dict(record)}


TICKET_PATH_RE = re.compile(r"^/tickets/([^/]+)$")


def create_handler_class(mock_table: InMemoryDynamoTable, root_dir: Path):
    class LocalDevHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root_dir), **kwargs)

        def _handle_api(self, method: str):
            parsed = urlparse(self.path)
            match = TICKET_PATH_RE.match(parsed.path)
            if not match:
                return False

            seat_id = match.group(1)
            content_length = int(self.headers.get("Content-Length", 0))
            body = (
                self.rfile.read(content_length).decode("utf-8")
                if content_length > 0
                else None
            )

            event = {
                "requestContext": {"http": {"method": method}},
                "pathParameters": {"seat_id": seat_id},
                "body": body,
            }
            response = lambda_handler(event, table=mock_table)

            self.send_response(response["statusCode"])
            for k, v in response.get("headers", {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(response["body"].encode("utf-8"))
            return True

        def do_OPTIONS(self):
            if not self._handle_api("OPTIONS"):
                self.send_response(200)
                self.end_headers()

        def do_GET(self):
            if not self._handle_api("GET"):
                super().do_GET()

        def do_PUT(self):
            if not self._handle_api("PUT"):
                self.send_error(404, "Not Found")

    return LocalDevHandler


def main():
    parser = argparse.ArgumentParser(description="ローカル統合検証サーバー")
    parser.add_argument("--port", type=int, default=8000, help="待受ポート番号 (デフォルト: 8000)")
    args = parser.parse_args()

    records = generate_initial_records(["A:1-400", "B:1-400"])
    mock_table = InMemoryDynamoTable(records)
    root_dir = Path(__file__).resolve().parent.parent

    handler_cls = create_handler_class(mock_table, root_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_cls)
    print(f"ローカル検証サーバー起動: http://localhost:{args.port}/?api=http://localhost:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを停止しました。")


if __name__ == "__main__":
    main()
