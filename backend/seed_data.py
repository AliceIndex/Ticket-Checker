#!/usr/bin/env python3
"""
学祭ライブ 学生証確認サービス
初期データ投入・全件初期化スクリプト (T1-2 & T5-3)

使い方:
  # デフォルト設定 (A001〜A200, B001〜B200 の計400席) を DynamoDB に投入/リセット
  python backend/seed_data.py

  # ドライラン（DynamoDBへは書き込まず、生成される座席リストを確認）
  python backend/seed_data.py --dry-run

  # ブロックや席数をカスタマイズする場合 (例: A001〜A150, B001〜B150, C001〜C100)
  python backend/seed_data.py --ranges A:1-150 B:1-150 C:1-100

  # ローカルモック用JSONファイルを出力する場合
  python backend/seed_data.py --export-json backend/initial_seats.json
"""

import argparse
import json
import sys
from pathlib import Path


def parse_range_spec(spec: str) -> list[str]:
    """
    'A:1-200' のような範囲指定文字列から ['A001', ..., 'A200'] を生成する。
    """
    try:
        prefix, num_range = spec.split(":")
        prefix = prefix.strip().upper()
        if len(prefix) != 1 or not prefix.isalpha():
            raise ValueError(f"ブロック文字は英字1文字である必要があります: {prefix}")
        start_str, end_str = num_range.split("-")
        start, end = int(start_str), int(end_str)
        if start < 1 or end > 999 or start > end:
            raise ValueError(f"番号範囲は 1〜999 の範囲で指定してください: {num_range}")
        return [f"{prefix}{i:03d}" for i in range(start, end + 1)]
    except Exception as e:
        raise argparse.ArgumentTypeError(
            f"範囲指定 '{spec}' の形式が不正です（例: A:1-200）: {e}"
        )


def generate_initial_records(range_specs: list[str]) -> list[dict]:
    """指定されたレンジから初期レコード一覧を生成する。"""
    seats: list[str] = []
    for spec in range_specs:
        seats.extend(parse_range_spec(spec))

    # 重複排除しつつ順序維持
    unique_seats = list(dict.fromkeys(seats))
    return [
        {
            "seat_id": seat_id,
            "status": "UNCHECKED",
            "updated_at": None,
            "updated_by": None,
        }
        for seat_id in unique_seats
    ]


def seed_dynamodb(table_name: str, region: str | None, records: list[dict]) -> None:
    """DynamoDB の batch_writer を使用して一括投入・初期化する。"""
    import boto3

    session_kwargs = {}
    if region:
        session_kwargs["region_name"] = region

    dynamodb = boto3.resource("dynamodb", **session_kwargs)
    table = dynamodb.Table(table_name)

    print(f"DynamoDB テーブル '{table_name}' へ {len(records)} 件の座席データを投入中...")
    with table.batch_writer() as batch:
        for idx, item in enumerate(records, start=1):
            batch.put_item(Item=item)
            if idx % 100 == 0 or idx == len(records):
                print(f"  進捗: {idx} / {len(records)} 件完了")

    print("全レコードの初期データ投入（UNCHECKED 初期化）が完了しました。")


def main():
    parser = argparse.ArgumentParser(
        description="StudentTicketChecks テーブルの初期データ投入・リセットスクリプト"
    )
    parser.add_argument(
        "--table-name",
        default="StudentTicketChecks",
        help="対象の DynamoDB テーブル名 (デフォルト: StudentTicketChecks)",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS リージョン (例: ap-northeast-1)",
    )
    parser.add_argument(
        "--ranges",
        nargs="+",
        default=["B:1-400"],
        help="生成する座席範囲のリスト (デフォルト: B:1-400 計400席)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DynamoDB への書き込みを行わず、生成内容のみ確認する",
    )
    parser.add_argument(
        "--export-json",
        default=None,
        help="生成した初期データを JSON ファイルに出力するパス",
    )

    args = parser.parse_args()
    records = generate_initial_records(args.ranges)

    print(
        f"生成された対象座席数: {len(records)} 件 "
        f"(先頭: {records[0]['seat_id']} 〜 末尾: {records[-1]['seat_id']})"
    )

    if args.export_json:
        out_path = Path(args.export_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"初期データ JSON を出力しました: {out_path}")

    if args.dry_run:
        print("[Dry Run] DynamoDB への書き込みはスキップしました。")
        sys.exit(0)

    seed_dynamodb(args.table_name, args.region, records)


if __name__ == "__main__":
    main()

