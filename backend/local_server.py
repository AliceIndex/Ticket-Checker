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
from seed_data import generate_initial_admission_records, generate_initial_records


class _InMemoryBatchWriter:
    def __init__(self, store: dict[str, dict]):
        self._store = store

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def put_item(self, Item: dict) -> None:
        self._store[Item["seat_id"]] = dict(Item)


class InMemoryDynamoTable:
    """DynamoDB Table (get_item / update_item / put_item / scan / batch_writer) のインメモリ互換モック"""

    def __init__(self, initial_records: list[dict]):
        self.store: dict[str, dict] = {
            item["seat_id"]: dict(item) for item in initial_records
        }

    def get_item(self, Key: dict) -> dict:
        seat_id = Key.get("seat_id")
        if seat_id in self.store:
            return {"Item": dict(self.store[seat_id])}
        return {}

    def put_item(self, Item: dict) -> dict:
        self.store[Item["seat_id"]] = dict(Item)
        return {}

    def batch_writer(self) -> _InMemoryBatchWriter:
        return _InMemoryBatchWriter(self.store)

    def scan(self, **kwargs) -> dict:
        return {"Items": [dict(item) for item in self.store.values()]}

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
        if ":status" in ExpressionAttributeValues:
            record["status"] = ExpressionAttributeValues[":status"]
        if ":updated_at" in ExpressionAttributeValues:
            record["updated_at"] = ExpressionAttributeValues[":updated_at"]
        if ":updated_by" in ExpressionAttributeValues:
            record["updated_by"] = ExpressionAttributeValues[":updated_by"]
        if ":admitted_at" in ExpressionAttributeValues:
            record["admitted_at"] = ExpressionAttributeValues[":admitted_at"]
        if ":admitted_by" in ExpressionAttributeValues:
            record["admitted_by"] = ExpressionAttributeValues[":admitted_by"]
        return {"Attributes": dict(record)}


TICKET_PATH_RE = re.compile(r"^/tickets/([^/]+)$")
ADMISSION_ITEM_PATH_RE = re.compile(r"^/admissions/([^/]+)$")
ADMISSION_LIST_PATH_RE = re.compile(r"^/admissions/?$")


def create_handler_class(
    mock_table: InMemoryDynamoTable,
    mock_admissions_table: InMemoryDynamoTable,
    root_dir: Path,
):
    class LocalDevHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root_dir), **kwargs)

        def _handle_api(self, method: str):
            parsed = urlparse(self.path)
            path = parsed.path

            ticket_match = TICKET_PATH_RE.match(path)
            admission_item_match = ADMISSION_ITEM_PATH_RE.match(path)
            admission_list_match = ADMISSION_LIST_PATH_RE.match(path)

            if not (ticket_match or admission_item_match or admission_list_match):
                return False

            path_params = {}
            if ticket_match:
                path_params["seat_id"] = ticket_match.group(1)
            elif admission_item_match:
                path_params["seat_id"] = admission_item_match.group(1)

            content_length = int(self.headers.get("Content-Length", 0))
            body = (
                self.rfile.read(content_length).decode("utf-8")
                if content_length > 0
                else None
            )

            event = {
                "rawPath": path,
                "requestContext": {"http": {"method": method, "path": path}},
                "pathParameters": path_params,
                "body": body,
            }
            response = lambda_handler(
                event,
                table=mock_table,
                admissions_table=mock_admissions_table,
            )

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

    check_records = generate_initial_records(["A:1-100", "B:1-310"])
    admission_records = generate_initial_admission_records(["A:1-100", "B:1-310", "C:1-390"])
    mock_table = InMemoryDynamoTable(check_records)
    mock_admissions_table = InMemoryDynamoTable(admission_records)
    root_dir = Path(__file__).resolve().parent.parent

    handler_cls = create_handler_class(mock_table, mock_admissions_table, root_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_cls)
    print(f"ローカル検証サーバー起動: http://localhost:{args.port}/?api=http://localhost:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nサーバーを停止しました。")


if __name__ == "__main__":
    main()
