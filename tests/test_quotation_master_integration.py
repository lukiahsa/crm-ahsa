import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import database


MODULE_TEMP_DIR = tempfile.TemporaryDirectory()
database.DATABASE = Path(MODULE_TEMP_DIR.name) / "bootstrap.db"

import main


class QuotationMasterIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        MODULE_TEMP_DIR.cleanup()

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.test_dir.name) / "quotation-v2.db"
        database.create_tables()
        self.client = main.app.test_client()
        self._seed_master()

    def tearDown(self):
        self.test_dir.cleanup()

    def _seed_master(self):
        conn = database.get_connection()
        self.customer_id = conn.execute(
            """
            INSERT INTO customers (
                nama, whatsapp, whatsapp_normalized, instansi, kota,
                produk, status, email, alamat, status_aktif
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                "Budi Snapshot",
                "081234567890",
                "6281234567890",
                "PT Customer Resmi",
                "Bandung",
                "Tempat Sampah; Tangga",
                "Existing Customer",
                "budi@example.com",
                "Jl. Snapshot No. 1",
            ),
        ).lastrowid
        self.inactive_customer_id = conn.execute(
            """
            INSERT INTO customers (nama, status, status_aktif)
            VALUES ('Customer Nonaktif', 'Prospek', 0)
            """
        ).lastrowid

        category_ids = {}
        for category in ("Tempat Sampah", "Tangga", "Material Handling"):
            category_ids[category] = conn.execute(
                "INSERT INTO product_categories (nama) VALUES (?)",
                (category,),
            ).lastrowid
        dalton_id = conn.execute(
            "INSERT INTO product_brands (nama) VALUES ('Dalton')"
        ).lastrowid
        denko_id = conn.execute(
            "INSERT INTO product_brands (nama) VALUES ('Denko')"
        ).lastrowid
        green_id = conn.execute(
            "INSERT INTO product_colors (nama) VALUES ('Hijau')"
        ).lastrowid
        trash_variant_id = conn.execute(
            """
            INSERT INTO product_variants (category_id, nama)
            VALUES (?, '240 Liter Roda')
            """,
            (category_ids["Tempat Sampah"],),
        ).lastrowid
        ladder_variant_id = conn.execute(
            """
            INSERT INTO product_variants (category_id, nama)
            VALUES (?, 'Extension')
            """,
            (category_ids["Tangga"],),
        ).lastrowid
        ladder_size_id = conn.execute(
            """
            INSERT INTO product_sizes (category_id, nama)
            VALUES (?, '8 Meter')
            """,
            (category_ids["Tangga"],),
        ).lastrowid

        self.trash_product_id = conn.execute(
            """
            INSERT INTO products (
                kode_produk, nama_produk, category_id, brand_id,
                variant_id, color_id, satuan, harga_jual_default,
                harga_modal_default, jenis_produk, status_aktif
            ) VALUES (?, ?, ?, ?, ?, ?, 'Unit', ?, ?, ?, 1)
            """,
            (
                "TS-240-RODA-HIJAU",
                "Tempat Sampah 240 Liter Roda - Hijau",
                category_ids["Tempat Sampah"],
                dalton_id,
                trash_variant_id,
                green_id,
                700000,
                451234,
                "HDPE",
            ),
        ).lastrowid
        self.ladder_product_id = conn.execute(
            """
            INSERT INTO products (
                kode_produk, nama_produk, category_id, brand_id,
                variant_id, size_id, satuan, harga_jual_default,
                harga_modal_default, jenis_produk, steps, status_aktif
            ) VALUES (?, ?, ?, ?, ?, ?, 'Unit', ?, ?, ?, ?, 1)
            """,
            (
                "EXT-8M-2S",
                "Tangga Fiberglass Extension 8 Meter",
                category_ids["Tangga"],
                denko_id,
                ladder_variant_id,
                ladder_size_id,
                3500000,
                2500000,
                "Fiberglass",
                "2 Section",
            ),
        ).lastrowid
        self.material_product_id = conn.execute(
            """
            INSERT INTO products (
                kode_produk, nama_produk, category_id, subkategori,
                satuan, harga_jual_default, harga_modal_default,
                status_aktif
            ) VALUES (?, ?, ?, ?, 'Unit', 0, 0, 1)
            """,
            (
                "HP-DALTON-25",
                "Hand Pallet HP Dalton 2.5 T",
                category_ids["Material Handling"],
                "Hand Pallet",
            ),
        ).lastrowid
        self.inactive_product_id = conn.execute(
            """
            INSERT INTO products (kode_produk, nama_produk, status_aktif)
            VALUES ('INACTIVE-1', 'Produk Nonaktif', 0)
            """
        ).lastrowid

        identities = conn.execute(
            "SELECT id, identity_type FROM company_identities"
        ).fetchall()
        self.identity_ids = {
            row["identity_type"]: row["id"]
            for row in identities
        }
        conn.commit()
        conn.close()

    def quotation_payload(
        self,
        *,
        identity_type=main.IDENTITY_TYPE_FULL,
        customer_id=None,
        product_ids=None,
        quantities=None,
        prices=None,
        item_discounts=None,
        global_discount="0",
    ):
        product_ids = product_ids or [self.trash_product_id]
        quantities = quantities or ["1"] * len(product_ids)
        prices = prices or ["10000000"] * len(product_ids)
        item_discounts = item_discounts or ["0"] * len(product_ids)
        return {
            "identity_id": str(self.identity_ids[identity_type]),
            "customer_id": str(customer_id or self.customer_id),
            "tanggal": "2026-08-03",
            "berlaku_sampai": "2026-08-17",
            "sales": "Luki",
            "diskon": str(global_discount),
            "catatan": "Snapshot test",
            "syarat_ketentuan": "Syarat snapshot",
            "product_id[]": [str(value) for value in product_ids],
            "qty[]": [str(value) for value in quantities],
            "harga_satuan[]": [str(value) for value in prices],
            "diskon_item[]": [str(value) for value in item_discounts],
            "is_ppn": "0",
            "ppn_rate": "99",
        }

    def create_quotation(self, **kwargs):
        response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(**kwargs),
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        conn = database.get_connection()
        quotation_id = conn.execute(
            "SELECT MAX(id) AS id FROM sales_quotations"
        ).fetchone()["id"]
        conn.close()
        return quotation_id

    def test_customer_search_by_name_phone_email_city_and_status(self):
        for keyword in (
            "Budi",
            "6281234567890",
            "budi@example.com",
            "Bandung",
            "Existing Customer",
        ):
            with self.subTest(keyword=keyword):
                response = self.client.get(
                    "/api/customers/search",
                    query_string={"keyword": keyword},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["results"][0]["id"], self.customer_id)

    def test_customer_search_requires_two_characters_and_caps_twenty(self):
        conn = database.get_connection()
        conn.executemany(
            "INSERT INTO customers (nama, status_aktif) VALUES (?, 1)",
            [(f"Limit Customer {number:02d}",) for number in range(25)],
        )
        conn.commit()
        conn.close()
        short = self.client.get("/api/customers/search?keyword=L")
        limited = self.client.get(
            "/api/customers/search?keyword=Limit&limit=999"
        )
        self.assertEqual(short.get_json()["results"], [])
        self.assertEqual(len(limited.get_json()["results"]), 20)

    def test_product_search_by_code_name_and_attributes(self):
        for keyword, expected_id in (
            ("TS-240", self.trash_product_id),
            ("Tangga Fiberglass", self.ladder_product_id),
            ("2 Section", self.ladder_product_id),
            ("Hand Pallet", self.material_product_id),
        ):
            with self.subTest(keyword=keyword):
                response = self.client.get(
                    "/api/products/search",
                    query_string={"keyword": keyword},
                )
                ids = [row["id"] for row in response.get_json()["results"]]
                self.assertIn(expected_id, ids)

    def test_product_search_requires_two_characters_and_excludes_inactive(self):
        self.assertEqual(
            self.client.get("/api/products/search?keyword=T").get_json()["results"],
            [],
        )
        results = self.client.get(
            "/api/products/search?keyword=INACTIVE"
        ).get_json()["results"]
        self.assertEqual(results, [])

    def test_forms_use_ajax_without_loading_all_master_rows(self):
        add_page = self.client.get("/quotations/add").get_data(as_text=True)
        self.assertIn("/api/customers/search", add_page)
        self.assertIn("/api/products/search", add_page)
        self.assertNotIn("Budi Snapshot", add_page)
        self.assertNotIn("TS-240-RODA-HIJAU", add_page)

    def test_customer_snapshot_does_not_change_with_master(self):
        quotation_id = self.create_quotation()
        conn = database.get_connection()
        conn.execute(
            """
            UPDATE customers
            SET nama = 'Nama Master Baru', alamat = 'Alamat Master Baru'
            WHERE id = ?
            """,
            (self.customer_id,),
        )
        conn.commit()
        quotation = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(quotation["customer_nama_snapshot"], "Budi Snapshot")
        page = self.client.get(f"/quotations/{quotation_id}/print").get_data(as_text=True)
        self.assertIn("Budi Snapshot", page)
        self.assertIn("Jl. Snapshot No. 1", page)
        self.assertNotIn("Nama Master Baru", page)

    def test_product_snapshot_is_complete_and_internal_cost_is_hidden(self):
        quotation_id = self.create_quotation()
        conn = database.get_connection()
        item = conn.execute(
            "SELECT * FROM sales_quotation_items WHERE quotation_id = ?",
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(item["kode_produk_snapshot"], "TS-240-RODA-HIJAU")
        self.assertEqual(item["jenis_produk_snapshot"], "HDPE")
        self.assertEqual(item["harga_modal_snapshot"], 451234)
        self.assertIn("Brand: Dalton", item["spesifikasi_snapshot"])
        page = self.client.get(f"/quotations/{quotation_id}/print").get_data(as_text=True)
        self.assertNotIn("451.234", page)

    def test_specification_omits_empty_master_fields(self):
        quotation_id = self.create_quotation(
            product_ids=[self.material_product_id],
            prices=["1500000"],
        )
        page = self.client.get(f"/quotations/{quotation_id}/print").get_data(as_text=True)
        self.assertIn("Subkategori", page)
        self.assertIn("Hand Pallet", page)
        self.assertNotIn('<span class="label">Brand</span>', page)

    def test_ahsa_quotation_remains_without_ppn(self):
        quotation_id = self.create_quotation()
        conn = database.get_connection()
        quotation = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(quotation["is_ppn"], 0)
        self.assertEqual(quotation["ppn_amount"], 0)
        self.assertEqual(quotation["grand_total"], 10000000)

    def test_denko_quotation_enforces_ppn_eleven_percent(self):
        quotation_id = self.create_quotation(
            identity_type=main.IDENTITY_TYPE_QUOTATION_ONLY
        )
        conn = database.get_connection()
        quotation = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(quotation["is_ppn"], 1)
        self.assertEqual(quotation["ppn_rate"], 11)
        self.assertEqual(quotation["ppn_amount"], 1100000)
        self.assertEqual(quotation["grand_total"], 11100000)

    def test_invalid_customer_is_rejected(self):
        response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(customer_id=999999),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Customer tidak ditemukan", response.get_data(as_text=True))

    def test_inactive_customer_is_rejected(self):
        response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(customer_id=self.inactive_customer_id),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Customer tidak aktif", response.get_data(as_text=True))

    def test_invalid_product_is_rejected(self):
        response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(product_ids=[999999]),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tidak ditemukan", response.get_data(as_text=True))

    def test_inactive_product_is_rejected(self):
        response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(product_ids=[self.inactive_product_id]),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tidak aktif", response.get_data(as_text=True))

    def test_zero_and_negative_quantity_are_rejected(self):
        for qty in ("0", "-1"):
            with self.subTest(qty=qty):
                response = self.client.post(
                    "/quotations/add",
                    data=self.quotation_payload(quantities=[qty]),
                )
                self.assertEqual(response.status_code, 400)

    def test_negative_price_is_rejected(self):
        response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(prices=["-1"]),
        )
        self.assertEqual(response.status_code, 400)

    def test_negative_item_and_global_discount_are_rejected(self):
        item_response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(item_discounts=["-1"]),
        )
        global_response = self.client.post(
            "/quotations/add",
            data=self.quotation_payload(global_discount="-1"),
        )
        self.assertEqual(item_response.status_code, 400)
        self.assertEqual(global_response.status_code, 400)
        self.assertIn("Diskon global", global_response.get_data(as_text=True))

    def test_quotation_without_items_is_rejected(self):
        payload = self.quotation_payload()
        for key in ("product_id[]", "qty[]", "harga_satuan[]", "diskon_item[]"):
            payload.pop(key)
        response = self.client.post("/quotations/add", data=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Minimal harus ada", response.get_data(as_text=True))

    def test_duplicate_preserves_customer_product_and_snapshots(self):
        source_id = self.create_quotation(
            identity_type=main.IDENTITY_TYPE_QUOTATION_ONLY
        )
        conn = database.get_connection()
        conn.execute(
            "UPDATE customers SET nama = 'Customer Berubah' WHERE id = ?",
            (self.customer_id,),
        )
        conn.execute(
            "UPDATE products SET nama_produk = 'Produk Berubah' WHERE id = ?",
            (self.trash_product_id,),
        )
        conn.commit()
        conn.close()
        response = self.client.post(f"/quotations/{source_id}/duplicate")
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        duplicated = conn.execute(
            "SELECT * FROM sales_quotations ORDER BY id DESC LIMIT 1"
        ).fetchone()
        duplicated_item = conn.execute(
            "SELECT * FROM sales_quotation_items WHERE quotation_id = ?",
            (duplicated["id"],),
        ).fetchone()
        conn.close()
        self.assertEqual(duplicated["customer_id"], self.customer_id)
        self.assertEqual(duplicated["customer_nama_snapshot"], "Budi Snapshot")
        self.assertEqual(duplicated_item["product_id"], self.trash_product_id)
        self.assertEqual(
            duplicated_item["nama_produk_snapshot"],
            "Tempat Sampah 240 Liter Roda - Hijau",
        )
        self.assertEqual(duplicated["ppn_rate"], 11)

    def test_edit_rebuilds_snapshot_from_selected_master(self):
        quotation_id = self.create_quotation()
        conn = database.get_connection()
        conn.execute(
            "UPDATE customers SET nama = 'Customer Master Updated' WHERE id = ?",
            (self.customer_id,),
        )
        conn.execute(
            "UPDATE products SET nama_produk = 'Produk Master Updated' WHERE id = ?",
            (self.trash_product_id,),
        )
        conn.commit()
        conn.close()
        response = self.client.post(
            f"/quotations/{quotation_id}/edit",
            data=self.quotation_payload(),
        )
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        conn = database.get_connection()
        quotation = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (quotation_id,),
        ).fetchone()
        item = conn.execute(
            "SELECT * FROM sales_quotation_items WHERE quotation_id = ?",
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(quotation["customer_nama_snapshot"], "Customer Master Updated")
        self.assertEqual(item["nama_produk_snapshot"], "Produk Master Updated")

    def test_pdf_uses_customer_and_product_snapshots(self):
        quotation_id = self.create_quotation(
            product_ids=[self.ladder_product_id],
            prices=["4000000"],
        )
        page = self.client.get(f"/quotations/{quotation_id}/print").get_data(as_text=True)
        for expected in (
            "Budi Snapshot",
            "PT Customer Resmi",
            "Jl. Snapshot No. 1",
            "6281234567890",
            "Tangga Fiberglass Extension 8 Meter",
            "2 Section",
        ):
            self.assertIn(expected, page)

    def test_legacy_quotation_without_snapshots_still_prints(self):
        conn = database.get_connection()
        quotation_id = conn.execute(
            """
            INSERT INTO sales_quotations (
                nomor_penawaran, customer_id, tanggal, subtotal,
                dpp, grand_total
            ) VALUES ('QT/LEGACY/V2', ?, '2026-08-03', 1000, 1000, 1000)
            """,
            (self.customer_id,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO sales_quotation_items (
                quotation_id, product_id, nama_produk_snapshot,
                qty, harga_satuan, subtotal
            ) VALUES (?, ?, 'Snapshot Legacy', 1, 1000, 1000)
            """,
            (quotation_id, self.trash_product_id),
        )
        conn.commit()
        conn.close()
        response = self.client.get(f"/quotations/{quotation_id}/print")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Budi Snapshot", response.get_data(as_text=True))
        self.assertIn("Snapshot Legacy", response.get_data(as_text=True))

    def test_ahsa_conversion_preserves_financial_invariant_with_snapshots(self):
        quotation_id = self.create_quotation(
            product_ids=[self.trash_product_id, self.ladder_product_id],
            quantities=["1", "1"],
            prices=["6000000", "4000000"],
            global_discount="1000000",
        )
        response = self.client.post(f"/quotations/{quotation_id}/convert")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        conn = database.get_connection()
        transaction = conn.execute(
            """
            SELECT sales_transactions.*
            FROM sales_transactions
            JOIN sales_quotations
              ON sales_quotations.converted_transaction_id = sales_transactions.id
            WHERE sales_quotations.id = ?
            """,
            (quotation_id,),
        ).fetchone()
        sums = conn.execute(
            """
            SELECT
                SUM(subtotal_penjualan) AS penjualan,
                SUM(subtotal_modal) AS modal,
                SUM(margin_item) AS margin
            FROM sales_transaction_items
            WHERE transaction_id = ?
            """,
            (transaction["id"],),
        ).fetchone()
        conn.close()
        self.assertEqual(transaction["total_penjualan"], sums["penjualan"])
        self.assertEqual(transaction["total_modal"], sums["modal"])
        self.assertEqual(transaction["margin"], sums["margin"])

    def test_denko_remains_blocked_from_transaction_conversion(self):
        quotation_id = self.create_quotation(
            identity_type=main.IDENTITY_TYPE_QUOTATION_ONLY
        )
        response = self.client.post(f"/quotations/{quotation_id}/convert")
        self.assertEqual(response.status_code, 400)
        self.assertIn("tidak dapat dikonversi", response.get_data(as_text=True))

    def test_migration_is_additive_and_idempotent(self):
        database.create_tables()
        database.create_tables()
        conn = database.get_connection()
        customer_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(customers)")
        }
        quotation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(sales_quotations)")
        }
        item_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sales_quotation_items)")
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertIn("status_aktif", customer_columns)
        self.assertIn("customer_nama_snapshot", quotation_columns)
        self.assertTrue(
            {
                "subkategori_snapshot",
                "jenis_produk_snapshot",
                "steps_snapshot",
                "spesifikasi_snapshot",
                "harga_modal_snapshot",
            }.issubset(item_columns)
        )
        self.assertEqual(integrity, "ok")


if __name__ == "__main__":
    unittest.main()
