import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import customer_import
import database


MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
database.DATABASE = Path(MODULE_TEMP_DIR.name) / "bootstrap.db"

import main


HEADERS = {
    name: definition.headers
    for name, definition in customer_import.SHEET_DEFINITIONS.items()
}


def workbook_bytes(rows_by_sheet=None):
    rows_by_sheet = rows_by_sheet or {}
    workbook = Workbook()
    workbook.remove(workbook.active)
    for sheet_name, headers in HEADERS.items():
        worksheet = workbook.create_sheet(sheet_name)
        worksheet.append(headers)
        for row in rows_by_sheet.get(sheet_name, []):
            worksheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def seven_sheet_fixture():
    return workbook_bytes(
        {
            "Customer Tempat Sampah": [("Andi", "081111111111")],
            "Customer Tangga": [("Budi", "082222222222")],
            "Customer MH": [("Citra", "083333333333")],
            "Belum Terklasifikasi": [("Dedi", "084444444444")],
            "Existing Produk Tempat Sampah": [("Eka", "085555555555")],
            "Existing Produk Tangga": [("Fina", "086666666666")],
            "Existing Produk MH": [
                (
                    1,
                    "Gilang",
                    "087777777777",
                    "PT Gilang",
                    "Hand Pallet",
                    "gilang@example.com",
                    "Bandung",
                )
            ],
        }
    )


class CustomerImportRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        MODULE_TEMP_DIR.cleanup()

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.test_dir.name) / "test.db"
        database.create_tables()
        self.conn = database.get_connection()

    def tearDown(self):
        self.conn.close()
        self.test_dir.cleanup()

    def prepare(self, content):
        parsed = customer_import.parse_customer_workbook(content)
        return customer_import.consolidate_customer_records(parsed)

    def test_parses_all_seven_sheets(self):
        parsed = customer_import.parse_customer_workbook(seven_sheet_fixture())
        self.assertEqual(list(parsed["per_sheet"]), list(HEADERS))
        self.assertEqual(parsed["total_rows"], 7)
        self.assertTrue(all(value["rows"] == 1 for value in parsed["per_sheet"].values()))

    def test_parses_two_column_sheet_mapping(self):
        parsed = customer_import.parse_customer_workbook(
            workbook_bytes({"Customer Tangga": [(" Nama  Tangga ", "081234567890")]})
        )
        record = parsed["records"][0]
        self.assertEqual(record["nama_asli"], "Nama Tangga")
        self.assertEqual(record["interests"], ["Tangga"])
        self.assertEqual(record["status"], "Prospek")

    def test_parses_existing_mh_seven_column_mapping(self):
        parsed = customer_import.parse_customer_workbook(
            workbook_bytes(
                {
                    "Existing Produk MH": [
                        (1, "PIC", "081234567890", "PT Uji", "Forklift", "PIC@EXAMPLE.COM", "Bandung")
                    ]
                }
            )
        )
        record = parsed["records"][0]
        self.assertEqual(record["instansi"], "PT Uji")
        self.assertEqual(record["produk_existing"], "Forklift")
        self.assertEqual(record["email"], "pic@example.com")
        self.assertEqual(record["alamat"], "Bandung")
        self.assertEqual(record["status"], "Existing Customer")

    def test_normalizes_08_to_62(self):
        result = customer_import.normalize_whatsapp("08123456789")
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalized"], "628123456789")

    def test_normalizes_plus_62(self):
        result = customer_import.normalize_whatsapp("+628123456789")
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalized"], "628123456789")

    def test_normalizes_phone_spacing_and_punctuation(self):
        result = customer_import.normalize_whatsapp("62 812-3456-(789)")
        self.assertTrue(result["valid"])
        self.assertEqual(result["normalized"], "628123456789")

    def test_rejects_invalid_phone_without_guessing(self):
        result = customer_import.normalize_whatsapp("812-not-certain")
        self.assertFalse(result["valid"])
        self.assertIsNone(result["normalized"])

    def test_duplicate_across_sheets_becomes_one_customer(self):
        consolidated = self.prepare(
            workbook_bytes(
                {
                    "Customer Tangga": [("Customer Sama", "081234567890")],
                    "Existing Produk Tempat Sampah": [("Customer Sama", "+6281234567890")],
                }
            )
        )
        self.assertEqual(consolidated["summary"]["unique_ready"], 1)
        self.assertEqual(consolidated["summary"]["duplicate_cross_sheet"], 1)

    def test_existing_customer_status_wins_over_prospect(self):
        customer = self.prepare(
            workbook_bytes(
                {
                    "Customer Tangga": [("Customer Sama", "081234567890")],
                    "Existing Produk Tempat Sampah": [("Customer Sama", "081234567890")],
                }
            )
        )["customers"][0]
        self.assertEqual(customer["status"], "Existing Customer")

    def test_product_interests_are_combined(self):
        customer = self.prepare(
            workbook_bytes(
                {
                    "Customer Tangga": [("Customer Sama", "081234567890")],
                    "Existing Produk Tempat Sampah": [("Customer Sama", "081234567890")],
                }
            )
        )["customers"][0]
        self.assertEqual(customer["produk"], "Tangga; Tempat Sampah")

    def test_complete_source_data_is_not_replaced_by_blank_duplicate(self):
        customer = self.prepare(
            workbook_bytes(
                {
                    "Customer Tangga": [("PIC Lengkap", "081234567890")],
                    "Existing Produk MH": [
                        (1, "PIC Lengkap", "081234567890", "PT Lengkap", "Hand Pallet", "pic@example.com", "Alamat Lengkap")
                    ],
                }
            )
        )["customers"][0]
        self.assertEqual(customer["instansi"], "PT Lengkap")
        self.assertEqual(customer["email"], "pic@example.com")
        self.assertEqual(customer["alamat"], "Alamat Lengkap")

    def test_database_duplicate_is_detected_and_non_destructive(self):
        self.conn.execute(
            "INSERT INTO customers (nama, whatsapp, instansi, status) VALUES (?, ?, ?, ?)",
            ("Nama Database", "081234567890", "PT Existing", "Prospek"),
        )
        self.conn.commit()
        consolidated = self.prepare(
            workbook_bytes(
                {"Existing Produk Tangga": [("Nama Import", "081234567890")]}
            )
        )
        preview = customer_import.analyze_database_duplicates(self.conn, consolidated)
        self.assertEqual(preview["summary"]["duplicate_database"], 1)
        self.assertEqual(preview["customers"][0]["action"], "MERGE")
        customer_import.import_customers_atomic(
            self.conn,
            consolidated,
            batch_id="BATCH-DB-DUP",
            filename="master.xlsx",
            file_sha256="abc",
        )
        row = self.conn.execute("SELECT * FROM customers").fetchone()
        self.assertEqual(row["nama"], "Nama Database")
        self.assertEqual(row["instansi"], "PT Existing")
        self.assertEqual(row["status"], "Existing Customer")
        audit = self.conn.execute(
            "SELECT * FROM customer_import_changes WHERE batch_id = 'BATCH-DB-DUP'"
        ).fetchone()
        self.assertEqual(audit["action"], "MERGE")
        self.assertIn("status", audit["changed_fields"])
        self.assertIn("Prospek", audit["before_values"])

    def test_import_twice_does_not_create_duplicate(self):
        content = workbook_bytes(
            {"Customer Tempat Sampah": [("Customer Ulang", "081234567890")]}
        )
        consolidated = self.prepare(content)
        first = customer_import.import_customers_atomic(
            self.conn,
            consolidated,
            batch_id="BATCH-FIRST",
            filename="master.xlsx",
            file_sha256="same-file",
        )
        second = customer_import.import_customers_atomic(
            self.conn,
            consolidated,
            batch_id="BATCH-SECOND",
            filename="master.xlsx",
            file_sha256="same-file",
        )
        count = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        self.assertEqual(first["summary"]["created_count"], 1)
        self.assertEqual(second["summary"]["created_count"], 0)
        self.assertEqual(second["summary"]["skipped_count"], 1)
        self.assertTrue(second["summary"]["duplicate_file"])
        self.assertEqual(count, 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_import_batches").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customer_import_changes").fetchone()[0],
            1,
        )

    def test_fatal_import_error_rolls_back_entire_batch(self):
        consolidated = self.prepare(
            workbook_bytes(
                {
                    "Customer Tempat Sampah": [
                        ("Customer Satu", "081111111111"),
                        ("Customer Dua", "082222222222"),
                    ]
                }
            )
        )
        original_insert = customer_import._insert_customer
        call_count = 0

        def fail_on_second(conn, customer, batch_id):
            nonlocal call_count
            call_count += 1
            original_insert(conn, customer, batch_id)
            if call_count == 2:
                raise RuntimeError("simulated fatal error")

        with patch("customer_import._insert_customer", side_effect=fail_on_second):
            with self.assertRaises(RuntimeError):
                customer_import.import_customers_atomic(
                    self.conn,
                    consolidated,
                    batch_id="BATCH-ROLLBACK",
                    filename="master.xlsx",
                    file_sha256="rollback",
                )

        customer_count = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        batch_count = self.conn.execute("SELECT COUNT(*) FROM customer_import_batches").fetchone()[0]
        change_count = self.conn.execute("SELECT COUNT(*) FROM customer_import_changes").fetchone()[0]
        self.assertEqual(customer_count, 0)
        self.assertEqual(batch_count, 0)
        self.assertEqual(change_count, 0)

    def test_unclassified_without_keyword_is_not_forced(self):
        customer = self.prepare(
            workbook_bytes(
                {"Belum Terklasifikasi": [("Nama Tanpa Keyword", "081234567890")]}
            )
        )["customers"][0]
        self.assertIsNone(customer["produk"])
        self.assertEqual(customer["klasifikasi_produk"], "Belum Terklasifikasi")

    def test_invalid_email_is_preserved_as_raw_only(self):
        record = customer_import.parse_customer_workbook(
            workbook_bytes(
                {
                    "Existing Produk MH": [
                        (1, "PIC", "081234567890", "PT Uji", "Stacker", "bukan-email", "Bandung")
                    ]
                }
            )
        )["records"][0]
        self.assertEqual(record["email_raw"], "bukan-email")
        self.assertIsNone(record["email"])
        self.assertTrue(record["warnings"])

    def test_migration_is_additive_and_idempotent(self):
        database.create_tables()
        database.create_tables()
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(customers)").fetchall()
        }
        self.assertIn("nama_asli", columns)
        self.assertIn("whatsapp_normalized", columns)
        self.assertIn("import_batch_id", columns)
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='customer_import_batches'"
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='customer_import_changes'"
            ).fetchone()
        )

    def test_preview_route_accepts_valid_workbook_without_writing_database(self):
        client = main.app.test_client()
        response = client.post(
            "/customers/import",
            data={
                "action": "preview",
                "customer_file": (io.BytesIO(seven_sheet_fixture()), "MASTER DATABASE FINAL.xlsx"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ringkasan Preview", response.data)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
