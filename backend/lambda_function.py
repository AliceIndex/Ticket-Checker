import json
import os
import re
from datetime import datetime, timezone

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None

    class ClientError(Exception):
        """ローカルテスト用 botocore.exceptions.ClientError 互換クラス"""

        def __init__(self, error_response: dict, operation_name: str):
            super().__init__(str(error_response))
            self.response = error_response
            self.operation_name = operation_name


TABLE_NAME = os.environ.get("TABLE_NAME", "StudentTicketChecks")
ADMISSIONS_TABLE_NAME = os.environ.get("ADMISSIONS_TABLE_NAME", "StudentTicketAdmissions")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

SEAT_ID_PATTERN = re.compile(r"^[A-Z][0-9]{3}$")
VALID_STATUSES = {"UNCHECKED", "CHECKED"}
VALID_ADMISSION_STATUSES = {"UNADMITTED", "ADMITTED"}

_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        if boto3 is None:
            raise RuntimeError("boto3 がインストールされていません。")
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def get_table():
    """学生証確認用 DynamoDB Table リソースを遅延初期化して返却する。"""
    return _get_dynamodb().Table(TABLE_NAME)


def get_admissions_table():
    """実入場記録用 DynamoDB Table リソースを遅延初期化して返却する。"""
    return _get_dynamodb().Table(ADMISSIONS_TABLE_NAME)


def build_response(status_code: int, body: dict) -> dict:
    """CORSヘッダー付きのHTTPレスポンスを生成する。"""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }


def normalize_seat_id(raw_seat_id: str | None) -> str | None:
    """座席番号を大文字化・トリムし、正規表現 (^[A-Z][0-9]{3}$) を検証する。"""
    if not raw_seat_id:
        return None
    seat_id = raw_seat_id.strip().upper()
    if not SEAT_ID_PATTERN.match(seat_id):
        return None
    return seat_id


def handle_get_ticket(seat_id: str, table=None) -> dict:
    """GET /tickets/{seat_id}: 座席の確認ステータスを取得する。"""
    if table is None:
        table = get_table()

    try:
        res = table.get_item(Key={"seat_id": seat_id})
    except ClientError as e:
        return build_response(
            500,
            {
                "error": "DATABASE_ERROR",
                "message": "データベース照会中にエラーが発生しました。",
                "detail": str(e),
            },
        )

    item = res.get("Item")
    if not item:
        return build_response(
            404,
            {
                "error": "SEAT_NOT_FOUND",
                "message": f"座席番号 {seat_id} は学生証確認の対象外です。",
                "seat_id": seat_id,
            },
        )

    return build_response(
        200,
        {
            "seat_id": item["seat_id"],
            "status": item.get("status", "UNCHECKED"),
            "updated_at": item.get("updated_at"),
            "updated_by": item.get("updated_by"),
        },
    )


def handle_put_ticket(seat_id: str, raw_body: str | None, table=None) -> dict:
    """PUT /tickets/{seat_id}: 座席の確認ステータスを更新（登録 / 取消）する。"""
    if table is None:
        table = get_table()

    if not raw_body:
        return build_response(
            400,
            {
                "error": "INVALID_BODY",
                "message": "リクエストボディが空です。",
            },
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return build_response(
            400,
            {
                "error": "INVALID_JSON",
                "message": "リクエストボディのJSON形式が不正です。",
            },
        )

    new_status = payload.get("status")
    operator_name = payload.get("operator_name")

    if new_status not in VALID_STATUSES:
        return build_response(
            400,
            {
                "error": "INVALID_STATUS",
                "message": "status は 'CHECKED' または 'UNCHECKED' を指定してください。",
            },
        )

    if not isinstance(operator_name, str) or not operator_name.strip():
        return build_response(
            400,
            {
                "error": "INVALID_OPERATOR",
                "message": "担当者名 (operator_name) の入力は必須です。",
            },
        )

    operator_name = operator_name.strip()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        res = table.update_item(
            Key={"seat_id": seat_id},
            UpdateExpression="SET #st = :status, updated_at = :updated_at, updated_by = :updated_by",
            ConditionExpression="attribute_exists(seat_id)",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":status": new_status,
                ":updated_at": now_iso,
                ":updated_by": operator_name,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "ConditionalCheckFailedException":
            return build_response(
                404,
                {
                    "error": "SEAT_NOT_FOUND",
                    "message": f"座席番号 {seat_id} は学生証確認の対象外です。",
                    "seat_id": seat_id,
                },
            )
        return build_response(
            500,
            {
                "error": "DATABASE_ERROR",
                "message": "データベース更新中にエラーが発生しました。",
                "detail": str(e),
            },
        )

    updated = res.get("Attributes", {})
    return build_response(
        200,
        {
            "seat_id": updated.get("seat_id", seat_id),
            "status": updated.get("status", new_status),
            "updated_at": updated.get("updated_at", now_iso),
            "updated_by": updated.get("updated_by", operator_name),
        },
    )


def handle_get_admission(seat_id: str, admissions_table=None) -> dict:
    """GET /admissions/{seat_id}: 座席の入場ステータスを取得する。"""
    if admissions_table is None:
        admissions_table = get_admissions_table()

    try:
        res = admissions_table.get_item(Key={"seat_id": seat_id})
    except ClientError as e:
        return build_response(
            500,
            {
                "error": "DATABASE_ERROR",
                "message": "入場データベース照会中にエラーが発生しました。",
                "detail": str(e),
            },
        )

    item = res.get("Item")
    if not item:
        return build_response(
            404,
            {
                "error": "SEAT_NOT_FOUND",
                "message": f"座席番号 {seat_id} は入場対象外です。",
                "seat_id": seat_id,
            },
        )

    return build_response(
        200,
        {
            "seat_id": item["seat_id"],
            "status": item.get("status", "UNADMITTED"),
            "admitted_at": item.get("admitted_at"),
            "admitted_by": item.get("admitted_by"),
        },
    )


def handle_put_admission(seat_id: str, raw_body: str | None, admissions_table=None) -> dict:
    """PUT /admissions/{seat_id}: 座席の入場ステータスを更新（入場確定 / 取消）する。"""
    if admissions_table is None:
        admissions_table = get_admissions_table()

    if not raw_body:
        return build_response(
            400,
            {
                "error": "INVALID_BODY",
                "message": "リクエストボディが空です。",
            },
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return build_response(
            400,
            {
                "error": "INVALID_JSON",
                "message": "リクエストボディのJSON形式が不正です。",
            },
        )

    new_status = payload.get("status")
    operator_name = payload.get("operator_name")

    if new_status not in VALID_ADMISSION_STATUSES:
        return build_response(
            400,
            {
                "error": "INVALID_STATUS",
                "message": "status は 'ADMITTED' または 'UNADMITTED' を指定してください。",
            },
        )

    if not isinstance(operator_name, str) or not operator_name.strip():
        return build_response(
            400,
            {
                "error": "INVALID_OPERATOR",
                "message": "担当者名 (operator_name) の入力は必須です。",
            },
        )

    operator_name = operator_name.strip()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if new_status == "ADMITTED":
        admitted_at_val = now_iso
        admitted_by_val = operator_name
    else:
        admitted_at_val = None
        admitted_by_val = None

    try:
        res = admissions_table.update_item(
            Key={"seat_id": seat_id},
            UpdateExpression="SET #st = :status, admitted_at = :admitted_at, admitted_by = :admitted_by",
            ConditionExpression="attribute_exists(seat_id)",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":status": new_status,
                ":admitted_at": admitted_at_val,
                ":admitted_by": admitted_by_val,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == "ConditionalCheckFailedException":
            return build_response(
                404,
                {
                    "error": "SEAT_NOT_FOUND",
                    "message": f"座席番号 {seat_id} は入場対象外です。",
                    "seat_id": seat_id,
                },
            )
        return build_response(
            500,
            {
                "error": "DATABASE_ERROR",
                "message": "入場データベース更新中にエラーが発生しました。",
                "detail": str(e),
            },
        )

    updated = res.get("Attributes", {})
    return build_response(
        200,
        {
            "seat_id": updated.get("seat_id", seat_id),
            "status": updated.get("status", new_status),
            "admitted_at": updated.get("admitted_at", admitted_at_val),
            "admitted_by": updated.get("admitted_by", admitted_by_val),
        },
    )


def handle_list_admissions(admissions_table=None) -> dict:
    """GET /admissions: 全座席の入場ステータス一覧・集計データを取得する。"""
    if admissions_table is None:
        admissions_table = get_admissions_table()

    items: list[dict] = []
    scan_kwargs: dict = {}

    try:
        while True:
            res = admissions_table.scan(**scan_kwargs)
            items.extend(res.get("Items", []))
            last_key = res.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
    except ClientError as e:
        return build_response(
            500,
            {
                "error": "DATABASE_ERROR",
                "message": "入場データ一覧取得中にエラーが発生しました。",
                "detail": str(e),
            },
        )

    sorted_items = sorted(items, key=lambda x: x.get("seat_id", ""))
    formatted_items = [
        {
            "seat_id": item["seat_id"],
            "status": item.get("status", "UNADMITTED"),
            "admitted_at": item.get("admitted_at"),
            "admitted_by": item.get("admitted_by"),
        }
        for item in sorted_items
    ]
    total_seats = len(formatted_items)
    admitted_count = sum(1 for item in formatted_items if item["status"] == "ADMITTED")

    return build_response(
        200,
        {
            "total_seats": total_seats,
            "admitted_count": admitted_count,
            "items": formatted_items,
        },
    )


def lambda_handler(event: dict, context=None, table=None, admissions_table=None) -> dict:
    """API Gateway (HTTP API v2 / REST API v1) からのイベントを処理するメインハンドラー。"""
    # AWSコンソールの「Test」ボタンから {"action": "seed"} を渡された場合、A001〜A400, B001〜B400 を一括初期化する
    if isinstance(event, dict) and event.get("action") == "seed":
        seed_target = event.get("target", "both")
        blocks = event.get("blocks", ["A", "B"])
        max_num = int(event.get("max_num", 400))
        seeded_tables = []

        if seed_target in ("checks", "both"):
            target_checks = table if table is not None else get_table()
            with target_checks.batch_writer() as batch:
                for blk in blocks:
                    for i in range(1, max_num + 1):
                        batch.put_item(
                            Item={
                                "seat_id": f"{blk}{i:03d}",
                                "status": "UNCHECKED",
                                "updated_at": None,
                                "updated_by": None,
                            }
                        )
            seeded_tables.append("StudentTicketChecks")

        if seed_target in ("admissions", "both"):
            target_admissions = admissions_table if admissions_table is not None else get_admissions_table()
            with target_admissions.batch_writer() as batch:
                for blk in blocks:
                    for i in range(1, max_num + 1):
                        batch.put_item(
                            Item={
                                "seat_id": f"{blk}{i:03d}",
                                "status": "UNADMITTED",
                                "admitted_at": None,
                                "admitted_by": None,
                            }
                        )
            seeded_tables.append("StudentTicketAdmissions")

        total_count = len(blocks) * max_num
        return build_response(
            200,
            {
                "message": f"初期データ投入（計{total_count}席 × {len(seeded_tables)}テーブル）が完了しました。",
                "count": total_count,
                "tables": seeded_tables,
            },
        )

    # HTTPメソッドの取得 (HTTP API v2 と REST API v1 両対応)
    http_method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()

    if http_method == "OPTIONS":
        return build_response(200, {"message": "OK"})

    raw_path = (
        event.get("rawPath")
        or event.get("requestContext", {}).get("http", {}).get("path")
        or event.get("path")
        or ""
    ).rstrip("/")

    path_params = event.get("pathParameters") or {}
    raw_seat_id = path_params.get("seat_id")

    # /admissions エンドポイント系のルーティング
    if raw_path == "/admissions" or (raw_path.startswith("/admissions") and "seat_id" not in path_params and raw_path.count("/") == 1):
        if http_method == "GET":
            return handle_list_admissions(admissions_table=admissions_table)
        return build_response(
            405,
            {
                "error": "METHOD_NOT_ALLOWED",
                "message": f"HTTPメソッド {http_method} は許可されていません。",
            },
        )

    is_admissions_route = raw_path.startswith("/admissions/")

    seat_id = normalize_seat_id(raw_seat_id)
    if not seat_id:
        return build_response(
            400,
            {
                "error": "INVALID_SEAT_FORMAT",
                "message": "座席番号の形式が不正です（英大文字1文字＋数字3文字 例: A001 / B001）。",
            },
        )

    if is_admissions_route:
        if http_method == "GET":
            return handle_get_admission(seat_id, admissions_table=admissions_table)
        elif http_method == "PUT":
            return handle_put_admission(seat_id, event.get("body"), admissions_table=admissions_table)
        else:
            return build_response(
                405,
                {
                    "error": "METHOD_NOT_ALLOWED",
                    "message": f"HTTPメソッド {http_method} は許可されていません。",
                },
            )

    # デフォルト: /tickets/{seat_id}
    if http_method == "GET":
        return handle_get_ticket(seat_id, table=table)
    elif http_method == "PUT":
        return handle_put_ticket(seat_id, event.get("body"), table=table)
    else:
        return build_response(
            405,
            {
                "error": "METHOD_NOT_ALLOWED",
                "message": f"HTTPメソッド {http_method} は許可されていません。",
            },
        )
