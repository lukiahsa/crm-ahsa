import base64
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import database
import product_import


MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
database.DATABASE = Path(MODULE_TEMP_DIR.name) / "bootstrap.db"

import main


def build_workbook_bytes(extra_rows=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet["B3"] = "Tempat Sampah"
    sheet["G3"] = "Tangga"
    sheet["O3"] = "Material Handling"
    headers = {
        "B4": "Nama Produk",
        "C4": "Kapasitas",
        "D4": "Warna",
        "E4": "Harga Modal",
        "G4": "Nama Produk",
        "H4": "Merk",
        "I4": "Jenis Produk",
        "J4": "Tipe Produk",
        "K4": "Ukuran",
        "L4": "Steps",
        "M4": "Harga Modal",
        "O4": "Nama Produk",
        "P4": "Tipe",
        "Q4": "Harga Modal",
    }
    for cell, value in headers.items():
        sheet[cell] = value

    sheet.append([None] * 17)
    values_row_5 = {
        "B5": "Tempat Sampah",
        "C5": "25 Liter Pedal",
        "D5": "Kuning",
        "E5": 151000.0,
        "G5": "Tangga",
        "H5": "Denko",
        "I5": "Multipurpose",
        "J5": "MAL4x3",
        "K5": "3,5 M",
        "L5": "4x3",
        "M5": 906000.0,
        "O5": "Hand Truck",
        "P5": "HT Plastik 150",
    }
    values_row_6 = {
        "D6": "Abu ",
        "E6": 151000,
        "J6": "MAL4x4",
        "K6": "4,6 M",
        "L6": "4x4",
        "M6": 1072000,
        "P6": "HT Plastik 300",
    }
    for values in (values_row_5, values_row_6):
        for cell, value in values.items():
            sheet[cell] = value

    if extra_rows:
        for row_number, values in extra_rows.items():
            for column, value in values.items():
                sheet.cell(row=row_number, column=column, value=value)

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class ProductImportTest(unittest.TestCase):
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
        self.client = main.app.test_client()
        self.file_bytes = build_workbook_bytes()

    def tearDown(self):
        self.test_dir.cleanup()

    def parse_rows(self):
        return product_import.parse_product_workbook(self.file_bytes)

    def row_for(self, group, source_row=5):
        return next(
            row
            for row in self.parse_rows()
            if row["source_group"] == group and row["source_row"] == source_row
        )

    def test_parses_trash_product(self):
        row = self.row_for("Tempat Sampah")
        self.assertEqual(row["nama_produk"], "Tempat Sampah 25 Liter Pedal - Kuning")
        self.assertEqual(row["variant"], "25 Liter Pedal")
        self.assertEqual(row["warna"], "Kuning")
        self.assertEqual(row["brand"], "Dalton")

    def test_parses_ladder_without_losing_attributes(self):
        row = self.row_for("Tangga")
        self.assertEqual(row["nama_produk"], "Tangga Multipurpose MAL4x3")
        self.assertEqual(row["brand"], "Denko")
        self.assertEqual(row["jenis_produk"], "Multipurpose")
        self.assertEqual(row["ukuran"], "3,5 M")
        self.assertEqual(row["steps"], "4x3")

    def test_parses_material_handling(self):
        row = self.row_for("Material Handling")
        self.assertEqual(row["subkategori"], "Hand Truck")
        self.assertEqual(row["nama_produk"], "Hand Truck HT Plastik 150")
        self.assertEqual(row["kode_produk"], "HT Plastik 150")
        self.assertIsNone(row["brand"])

    def test_forward_fills_merged_source_values(self):
        trash = self.row_for("Tempat Sampah", 6)
        ladder = self.row_for("Tangga", 6)
        material = self.row_for("Material Handling", 6)
        self.assertEqual(trash["variant"], "25 Liter Pedal")
        self.assertEqual(trash["warna"], "Abu")
        self.assertEqual(ladder["brand"], "Denko")
        self.assertEqual(ladder["jenis_produk"], "Multipurpose")
        self.assertEqual(material["subkategori"], "Hand Truck")

    def test_prices_are_integer_rupiah(self):
        for row in self.parse_rows():
            amount = row["harga_modal_default"]
            if amount is not None:
                self.assertIs(type(amount), int)
        self.assertEqual(self.row_for("Tangga")["harga_modal_default"], 906000)

    def test_empty_material_price_is_warning_not_error(self):
        row = self.row_for("Material Handling")
        self.assertIsNone(row["harga_modal_default"])
        self.assertFalse(row["errors"])
        self.assertTrue(any("disimpan 0" in warning for warning in row["warnings"]))

    def test_trash_sku_is_deterministic(self):
        expected = "TS-025-PEDAL-KUNING"
        self.assertEqual(product_import.build_trash_sku("25 Liter Pedal", "Kuning"), expected)
        self.assertEqual(product_import.build_trash_sku("25  Liter Pedal ", " Kuning"), expected)

    def test_negative_price_is_rejected(self):
        file_bytes = build_workbook_bytes(extra_rows={5: {5: -1}})
        rows = product_import.parse_product_workbook(file_bytes)
        row = next(
            row
            for row in rows
            if row["source_group"] == "Tempat Sampah" and row["source_row"] == 5
        )
        self.assertTrue(any("tidak boleh negatif" in error for error in row["errors"]))

    def test_detects_duplicate_inside_file(self):
        rows = self.parse_rows()
        rows.append(dict(rows[0]))
        conn = database.get_connection()
        analyzed = product_import.analyze_product_rows(conn, rows)
        conn.close()
        self.assertEqual(analyzed[-1]["status"], "duplicate")
        self.assertEqual(analyzed[-1]["duplicate_kind"], "duplicate_in_file")

    def test_detects_duplicate_in_database(self):
        rows = self.parse_rows()
        product_import.import_product_rows(database.get_connection(), rows)

        conn = database.get_connection()
        analyzed = product_import.analyze_product_rows(conn, rows)
        conn.close()
        self.assertTrue(all(row["status"] == "duplicate" for row in analyzed))
        self.assertTrue(
            all(row["duplicate_kind"] == "existing_database" for row in analyzed)
        )

    def test_detects_same_code_with_different_name(self):
        conn = database.get_connection()
        conn.execute(
            "INSERT INTO products (kode_produk, nama_produk) VALUES (?, ?)",
            ("MAL4x3", "Nama Existing Berbeda"),
        )
        conn.commit()
        rows = self.parse_rows()
        analyzed = product_import.analyze_product_rows(conn, rows)
        conn.close()
        ladder = next(row for row in analyzed if row["kode_produk"] == "MAL4x3")
        self.assertEqual(ladder["status"], "duplicate")
        self.assertEqual(ladder["duplicate_kind"], "code_name_conflict")

    def test_rolls_back_all_rows_on_fatal_database_error(self):
        rows = self.parse_rows()
        original_insert = product_import._insert_product
        calls = 0

        def fail_after_first(conn, row):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("forced rollback")
            return original_insert(conn, row)

        conn = database.get_connection()
        with mock.patch.object(product_import, "_insert_product", fail_after_first):
            with self.assertRaises(sqlite3.OperationalError):
                product_import.import_product_rows(conn, rows)
        conn.close()

        verify = database.get_connection()
        product_count = verify.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
        category_count = verify.execute(
            "SELECT COUNT(*) AS total FROM product_categories"
        ).fetchone()["total"]
        verify.close()
        self.assertEqual(product_count, 0)
        self.assertEqual(category_count, 0)

    def test_import_twice_skips_duplicates(self):
        rows = self.parse_rows()
        first_conn = database.get_connection()
        _, first = product_import.import_product_rows(first_conn, rows)
        first_conn.close()

        second_conn = database.get_connection()
        _, second = product_import.import_product_rows(second_conn, rows)
        second_conn.close()

        self.assertEqual(first["created"], len(rows))
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["duplicate"], len(rows))

        verify = database.get_connection()
        count = verify.execute("SELECT COUNT(*) AS total FROM products").fetchone()["total"]
        verify.close()
        self.assertEqual(count, len(rows))

    def test_preview_and_confirm_route_revalidates_file(self):
        preview = self.client.post(
            "/products/import",
            data={"action": "preview", "product_file": (BytesIO(self.file_bytes), "MASTER PRODUK.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(preview.status_code, 200, preview.get_data(as_text=True))
        self.assertIn("Preview Normalisasi", preview.get_data(as_text=True))

        digest = hashlib.sha256(self.file_bytes).hexdigest()
        confirmed = self.client.post(
            "/products/import",
            data={
                "action": "confirm",
                "file_payload": base64.b64encode(self.file_bytes).decode("ascii"),
                "file_digest": digest,
                "filename": "MASTER PRODUK.xlsx",
            },
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.get_data(as_text=True))
        self.assertIn("Import selesai", confirmed.get_data(as_text=True))
        self.assertIn("Download Laporan Markdown", confirmed.get_data(as_text=True))

    def test_confirmation_rejects_modified_preview_payload(self):
        response = self.client.post(
            "/products/import",
            data={
                "action": "confirm",
                "file_payload": base64.b64encode(self.file_bytes + b"changed").decode("ascii"),
                "file_digest": hashlib.sha256(self.file_bytes).hexdigest(),
                "filename": "MASTER PRODUK.xlsx",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("File preview berubah", response.get_data(as_text=True))

    def test_schema_migration_is_idempotent(self):
        database.create_tables()
        database.create_tables()
        conn = database.get_connection()
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()
        }
        conn.close()
        self.assertTrue({"subkategori", "jenis_produk", "steps"}.issubset(columns))

    def test_products_page_renders_imported_attributes(self):
        conn = database.get_connection()
        product_import.import_product_rows(conn, self.parse_rows())
        conn.close()
        response = self.client.get("/products")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        page = response.get_data(as_text=True)
        self.assertIn("Tempat Sampah", page)
        self.assertIn("Dalton", page)
        self.assertIn("25 Liter Pedal", page)


if __name__ == "__main__":
    unittest.main()
