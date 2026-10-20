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
from seed_data import generate_initial_admission_records, generate_initial_records


class TestTicketServiceBackend(unittest.TestCase):
    def setUp(self):
        # 初期データ 800件 (A001〜A400, B001〜B400) を両テーブルに投入
        ranges = ["A:1-400", "B:1-400"]
        check_records = generate_initial_records(ranges)
        admission_records = generate_initial_admission_records(ranges)
        self.table = InMemoryDynamoTable(check_records)
        self.admissions_table = InMemoryDynamoTable(admission_records)

    def _invoke(
        self,
        method: str,
        seat_id: str | None = None,
        body: dict | None = None,
        raw_path: str | None = None,
    ) -> tuple[int, dict]:
        if raw_path is None:
            raw_path = f"/tickets/{seat_id or ''}"
        path_params = {"seat_id": seat_id} if seat_id is not None else {}
        event = {
            "rawPath": raw_path,
            "requestContext": {"http": {"method": method, "path": raw_path}},
            "pathParameters": path_params,
            "body": json.dumps(body, ensure_ascii=False) if body is not None else None,
        }
        res = lambda_handler(
            event,
            table=self.table,
            admissions_table=self.admissions_table,
        )
        return res["statusCode"], json.loads(res["body"])

    def _invoke_admission(
        self, method: str, seat_id: str, body: dict | None = None
    ) -> tuple[int, dict]:
        return self._invoke(
            method, seat_id=seat_id, body=body, raw_path=f"/admissions/{seat_id}"
        )

    def _invoke_admissions_list(self, method: str = "GET") -> tuple[int, dict]:
        return self._invoke(method, seat_id=None, body=None, raw_path="/admissions")

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

    def test_admissions_initial_state(self):
        """新設入場テーブル 800件が UNADMITTED で初期化されているか"""
        self.assertEqual(len(self.admissions_table.store), 800)
        for seat_id in ["A001", "A400", "B001", "B400"]:
            status, data = self._invoke_admission("GET", seat_id)
            self.assertEqual(status, 200)
            self.assertEqual(data["seat_id"], seat_id)
            self.assertEqual(data["status"], "UNADMITTED")
            self.assertIsNone(data["admitted_at"])
            self.assertIsNone(data["admitted_by"])

    def test_admission_register_duplicate_and_cancel_flow(self):
        """実入場登録 (ADMITTED) -> 二重入場照会 -> 入場取消 (UNADMITTED) フローおよび確認DBとの独立性"""
        # 1. 入場確定 (ADMITTED by 田中)
        status, data = self._invoke_admission(
            "PUT", "A015", {"status": "ADMITTED", "operator_name": "田中"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["seat_id"], "A015")
        self.assertEqual(data["status"], "ADMITTED")
        self.assertEqual(data["admitted_by"], "田中")
        self.assertIsNotNone(data["admitted_at"])

        # 2. 再照会（二重入場検知用）
        status, data = self._invoke_admission("GET", "A015")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ADMITTED")
        self.assertEqual(data["admitted_by"], "田中")

        # 3. 学生証確認DB (StudentTicketChecks) には影響していないことを確認
        status_chk, data_chk = self._invoke("GET", "A015")
        self.assertEqual(status_chk, 200)
        self.assertEqual(data_chk["status"], "UNCHECKED")

        # 4. 誤操作による入場取消 (UNADMITTED by 高橋)
        status, data = self._invoke_admission(
            "PUT", "A015", {"status": "UNADMITTED", "operator_name": "高橋"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "UNADMITTED")
        self.assertIsNone(data["admitted_at"])
        self.assertIsNone(data["admitted_by"])

    def test_admissions_list_and_summary(self):
        """GET /admissions による全件取得・入場数集計の検証"""
        self._invoke_admission("PUT", "A001", {"status": "ADMITTED", "operator_name": "田中"})
        self._invoke_admission("PUT", "A002", {"status": "ADMITTED", "operator_name": "田中"})
        self._invoke_admission("PUT", "B010", {"status": "ADMITTED", "operator_name": "山本"})

        status, data = self._invoke_admissions_list("GET")
        self.assertEqual(status, 200)
        self.assertEqual(data["total_seats"], 800)
        self.assertEqual(data["admitted_count"], 3)
        self.assertEqual(len(data["items"]), 800)

        admitted_seats = [item["seat_id"] for item in data["items"] if item["status"] == "ADMITTED"]
        self.assertEqual(admitted_seats, ["A001", "A002", "B010"])

    def test_admissions_out_of_range_and_validation(self):
        """入場APIでの対象外座席(404)、不正ステータス(400)、担当者名未入力(400)の検証"""
        status, data = self._invoke_admission("GET", "Z999")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "SEAT_NOT_FOUND")

        status, data = self._invoke_admission(
            "PUT", "A001", {"status": "CHECKED", "operator_name": "田中"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "INVALID_STATUS")

        status, data = self._invoke_admission(
            "PUT", "A001", {"status": "ADMITTED", "operator_name": "   "}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "INVALID_OPERATOR")

    def test_seed_action_initializes_both_tables(self):
        """Lambda の seed アクションで確認DB・入場DBの両方が800件初期化されるか"""
        empty_checks = InMemoryDynamoTable([])
        empty_admissions = InMemoryDynamoTable([])
        res = lambda_handler(
            {"action": "seed"},
            table=empty_checks,
            admissions_table=empty_admissions,
        )
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(len(empty_checks.store), 800)
        self.assertEqual(len(empty_admissions.store), 800)


if __name__ == "__main__":
    unittest.main(verbosity=2)

