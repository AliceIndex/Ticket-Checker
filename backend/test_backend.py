#!/usr/bin/env python3
"""
学祭ライブ 学生証確認サービス
バックエンド単体・結合テストスイート (backend/test_backend.py)

実装計画書 Phase 3 (T3-1〜T3-3) の検証項目を網羅:
  - T3-1: 正常系 GET / PUT の読み書き疎通
  - T3-2: 二重登録確認 (CHECKED 状態の取得) および「取消 (UNCHECKED)」へのリカバリー操作
  - T3-3: 異常系・エッジケース
    - 対象外座席番号 (Z999 等) -> 404 Not Found
    - 不正なフォーマット (A1, 1234, ABCD 等) -> 400 Bad Request
    - 空文字・スペースのみの担当者名での送信阻止 -> 400 Bad Request
    - 小文字入力 (b012) の自動正規化確認
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lambda_function import lambda_handler
from local_server import InMemoryDynamoTable
from seed_data import generate_initial_records


class TestTicketServiceBackend(unittest.TestCase):
    def setUp(self):
        # 初期データ 800件 (A001〜A400, B001〜B400) を投入
        records = generate_initial_records(["A:1-400", "B:1-400"])
        self.table = InMemoryDynamoTable(records)

    def _invoke(self, method: str, seat_id: str, body: dict | None = None) -> tuple[int, dict]:
        event = {
            "requestContext": {"http": {"method": method}},
            "pathParameters": {"seat_id": seat_id},
            "body": json.dumps(body, ensure_ascii=False) if body is not None else None,
        }
        res = lambda_handler(event, table=self.table)
        return res["statusCode"], json.loads(res["body"])

    def test_initial_seed_count(self):
        """T1-2: 初期データ800件 (A001〜A400, B001〜B400) が正しくUNCHECKEDで生成されているか"""
        self.assertEqual(len(self.table.store), 800)
        for seat_id in ["A001", "A400", "B001", "B400"]:
            status, data = self._invoke("GET", seat_id)
            self.assertEqual(status, 200)
            self.assertEqual(data["seat_id"], seat_id)
            self.assertEqual(data["status"], "UNCHECKED")
            self.assertIsNone(data["updated_at"])
            self.assertIsNone(data["updated_by"])

    def test_check_in_and_recovery_flow(self):
        """T3-1 & T3-2: CHECKED登録 -> 再照会(二重登録確認) -> UNCHECKED取消の一連フロー"""
        # 1. 初回照会 (UNCHECKED)
        status, data = self._invoke("GET", "B012")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "UNCHECKED")

        # 2. 確認済みに更新 (CHECKED by 佐藤)
        status, data = self._invoke(
            "PUT", "B012", {"status": "CHECKED", "operator_name": "佐藤"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["seat_id"], "B012")
        self.assertEqual(data["status"], "CHECKED")
        self.assertEqual(data["updated_by"], "佐藤")
        self.assertIsNotNone(data["updated_at"])

        # 3. 別端末からの再照会想定 (CHECKED と担当者名が返却されること)
        status, data = self._invoke("GET", "B012")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "CHECKED")
        self.assertEqual(data["updated_by"], "佐藤")

        # 4. 誤操作による取消 (UNCHECKED by 鈴木)
        status, data = self._invoke(
            "PUT", "B012", {"status": "UNCHECKED", "operator_name": "鈴木"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "UNCHECKED")
        self.assertEqual(data["updated_by"], "鈴木")

    def test_out_of_range_seat_returns_404(self):
        """T3-3: 対象外の座席番号 (Z999, A401, B401, C001, D001) のGET/PUTで404が返るか"""
        for invalid_seat in ["Z999", "A401", "A999", "B401", "B999", "C001", "D001"]:
            status, data = self._invoke("GET", invalid_seat)
            self.assertEqual(status, 404, f"{invalid_seat} should be 404 on GET")
            self.assertEqual(data["error"], "SEAT_NOT_FOUND")

            status_put, _ = self._invoke(
                "PUT", invalid_seat, {"status": "CHECKED", "operator_name": "佐藤"}
            )
            self.assertEqual(status_put, 404, f"{invalid_seat} should be 404 on PUT")

    def test_invalid_seat_format_returns_400(self):
        """T3-3: 不正なフォーマット (B1, 1234, ABCD, B0001) の入力で400が返るか"""
        for bad_format in ["B1", "1234", "ABCD", "B0001", "1B00", ""]:
            status, data = self._invoke("GET", bad_format)
            self.assertEqual(status, 400, f"'{bad_format}' should be 400")
            self.assertEqual(data["error"], "INVALID_SEAT_FORMAT")

    def test_empty_or_whitespace_operator_rejected(self):
        """T3-3: 空文字・スペースのみの担当者名での送信阻止"""
        for bad_operator in ["", "   ", "\t\n", None]:
            status, data = self._invoke(
                "PUT", "B001", {"status": "CHECKED", "operator_name": bad_operator}
            )
            self.assertEqual(status, 400)
            self.assertEqual(data["error"], "INVALID_OPERATOR")

    def test_lowercase_seat_id_normalized(self):
        """小文字でリクエストされた場合も大文字正規化されて処理されるか"""
        status, data = self._invoke("GET", "b005")
        self.assertEqual(status, 200)
        self.assertEqual(data["seat_id"], "B005")


if __name__ == "__main__":
    unittest.main(verbosity=2)

