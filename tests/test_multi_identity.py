import sys
import sqlite3
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


class MultiIdentityRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(
            TESTING=True,
            SERVER_NAME="localhost",
        )

    @classmethod
    def tearDownClass(cls):
        MODULE_TEMP_DIR.cleanup()

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.test_dir.name) / "test.db"
        database.create_tables()
        self.client = main.app.test_client()

        conn = database.get_connection()
        self.customer_id = conn.execute(
            """
            INSERT INTO customers (
                nama,
                whatsapp,
                instansi,
                kota
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "Customer QA",
                "08123456789",
                "PT Customer QA",
                "Garut",
            ),
        ).lastrowid
        self.product_id = conn.execute(
            """
            INSERT INTO products (
                kode_produk,
                nama_produk,
                satuan,
                harga_jual_default,
                harga_modal_default
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "QA-001",
                "Produk Regression QA",
                "Unit",
                100000,
                50000,
            ),
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        self.test_dir.cleanup()

    def identity_id(self, identity_type):
        conn = database.get_connection()
        row = conn.execute(
            """
            SELECT id
            FROM company_identities
            WHERE identity_type = ?
            """,
            (identity_type,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        return row["id"]

    def create_quotation(self, identity_type):
        response = self.client.post(
            "/quotations/add",
            data={
                "identity_id": str(self.identity_id(identity_type)),
                "customer_id": str(self.customer_id),
                "tanggal": "2026-08-01",
                "berlaku_sampai": "2026-08-15",
                "sales": "QA Engineer",
                "diskon": "0",
                "catatan": "Regression test",
                "syarat_ketentuan": "Syarat regression test",
                "product_id[]": [str(self.product_id)],
                "qty[]": ["1"],
                "harga_satuan[]": ["100000"],
                "diskon_item[]": ["0"],
            },
        )
        self.assertEqual(
            response.status_code,
            302,
            response.get_data(as_text=True),
        )
        return int(response.headers["Location"].rstrip("/").split("/")[-1])

    def convert_ahsa_quotation(self):
        quotation_id = self.create_quotation(main.IDENTITY_TYPE_FULL)
        response = self.client.post(
            f"/quotations/{quotation_id}/convert",
            data={
                "identity_id": str(
                    self.identity_id(main.IDENTITY_TYPE_QUOTATION_ONLY)
                )
            },
        )
        self.assertEqual(
            response.status_code,
            302,
            response.get_data(as_text=True),
        )

        conn = database.get_connection()
        quotation = conn.execute(
            """
            SELECT converted_transaction_id
            FROM sales_quotations
            WHERE id = ?
            """,
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(quotation["converted_transaction_id"])
        return quotation_id, quotation["converted_transaction_id"]

    def test_identity_seed_and_legacy_quotation_fallback(self):
        database.create_tables()
        conn = database.get_connection()
        identities = conn.execute(
            """
            SELECT *
            FROM company_identities
            ORDER BY is_default DESC, id
            """
        ).fetchall()

        self.assertEqual(len(identities), 2)
        self.assertEqual(identities[0]["identity_type"], "FULL")
        self.assertEqual(identities[0]["is_default"], 1)
        self.assertEqual(identities[0]["logo_path"], "images/logo-ahsa.png")
        self.assertEqual(identities[1]["identity_type"], "QUOTATION_ONLY")
        self.assertEqual(identities[1]["logo_path"], "images/denko_logo.png")
        self.assertEqual(identities[1]["allow_qr"], 0)
        self.assertEqual(identities[1]["allow_signature"], 0)
        self.assertEqual(identities[1]["allow_website_footer"], 0)
        self.assertEqual(identities[1]["allow_transaction_conversion"], 0)

        legacy_id = conn.execute(
            """
            INSERT INTO sales_quotations (
                nomor_penawaran,
                customer_id,
                tanggal
            )
            VALUES (?, ?, ?)
            """,
            ("QT-LEGACY-QA", self.customer_id, "2026-08-01"),
        ).lastrowid
        legacy = conn.execute(
            """
            SELECT identity_id
            FROM sales_quotations
            WHERE id = ?
            """,
            (legacy_id,),
        ).fetchone()
        self.assertEqual(legacy["identity_id"], identities[0]["id"])
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()

    def test_existing_legacy_database_is_backfilled_to_full_identity(self):
        current_database = database.DATABASE

        with tempfile.TemporaryDirectory() as legacy_dir:
            legacy_database = Path(legacy_dir) / "legacy.db"
            raw_conn = sqlite3.connect(legacy_database)
            raw_conn.execute(
                """
                CREATE TABLE company_profile (
                    id INTEGER PRIMARY KEY,
                    nama_perusahaan TEXT,
                    nama_brand TEXT
                )
                """
            )
            raw_conn.execute(
                """
                INSERT INTO company_profile (
                    id,
                    nama_perusahaan,
                    nama_brand
                )
                VALUES (1, 'PT Ahsa Legacy', 'Ahsa Legacy')
                """
            )
            raw_conn.execute(
                """
                CREATE TABLE sales_quotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nomor_penawaran TEXT NOT NULL UNIQUE,
                    customer_id INTEGER,
                    tanggal TEXT NOT NULL
                )
                """
            )
            raw_conn.execute(
                """
                INSERT INTO sales_quotations (
                    nomor_penawaran,
                    tanggal
                )
                VALUES ('QT-BEFORE-SPRINT-6', '2026-07-01')
                """
            )
            raw_conn.commit()
            raw_conn.close()

            try:
                database.DATABASE = legacy_database
                database.create_tables()
                migrated_conn = database.get_connection()
                migrated = migrated_conn.execute(
                    """
                    SELECT
                        sales_quotations.identity_id,
                        company_identities.identity_type,
                        company_identities.nama_perusahaan
                    FROM sales_quotations
                    JOIN company_identities
                        ON sales_quotations.identity_id =
                           company_identities.id
                    WHERE sales_quotations.nomor_penawaran = ?
                    """,
                    ("QT-BEFORE-SPRINT-6",),
                ).fetchone()
                self.assertIsNotNone(migrated)
                self.assertEqual(migrated["identity_type"], "FULL")
                self.assertEqual(
                    migrated["nama_perusahaan"],
                    "PT Ahsa Legacy",
                )
                migrated_conn.close()
            finally:
                database.DATABASE = current_database

    def test_ahsa_quotation_converts_to_transaction(self):
        quotation_id, transaction_id = self.convert_ahsa_quotation()
        conn = database.get_connection()
        transaction = conn.execute(
            """
            SELECT source_quotation_id
            FROM sales_transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(transaction["source_quotation_id"], quotation_id)

    def test_denko_quotation_conversion_is_rejected_server_side(self):
        quotation_id = self.create_quotation(
            main.IDENTITY_TYPE_QUOTATION_ONLY
        )
        response = self.client.post(
            f"/quotations/{quotation_id}/convert",
            data={"identity_id": str(self.identity_id(main.IDENTITY_TYPE_FULL))},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Quotation Denko tidak dapat dikonversi menjadi Transaction.",
            response.get_data(as_text=True),
        )

        conn = database.get_connection()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM sales_transactions").fetchone()[0],
            0,
        )
        quotation = conn.execute(
            """
            SELECT converted_transaction_id
            FROM sales_quotations
            WHERE id = ?
            """,
            (quotation_id,),
        ).fetchone()
        conn.close()
        self.assertIsNone(quotation["converted_transaction_id"])

    def test_quotation_pdf_uses_effective_identity_capabilities(self):
        ahsa_id = self.create_quotation(main.IDENTITY_TYPE_FULL)
        denko_id = self.create_quotation(main.IDENTITY_TYPE_QUOTATION_ONLY)

        ahsa_html = self.client.get(
            f"/quotations/{ahsa_id}/print"
        ).get_data(as_text=True)
        denko_html = self.client.get(
            f"/quotations/{denko_id}/print"
        ).get_data(as_text=True)

        self.assertIn("images/logo-ahsa.png", ahsa_html)
        self.assertIn("distributordalton.com", ahsa_html)
        self.assertIn("data:image/png;base64", ahsa_html)
        self.assertIn("Luki Lukmanul Hakim", ahsa_html)

        self.assertIn("images/denko_logo.png", denko_html)
        self.assertIn("PT Denko Wahana Sakti", denko_html)
        self.assertNotIn("images/logo-ahsa.png", denko_html)
        self.assertNotIn("distributordalton.com", denko_html)
        self.assertNotIn("data:image/png;base64", denko_html)
        self.assertNotIn("Luki Lukmanul Hakim", denko_html)

    def test_denko_footer_is_suppressed_server_side(self):
        denko_id = self.create_quotation(
            main.IDENTITY_TYPE_QUOTATION_ONLY
        )
        denko_settings = self.client.post(
            f"/quotations/{denko_id}/print-settings",
            data={
                "show_footer": "1",
                "show_qr": "1",
                "show_signature": "1",
            },
        )
        self.assertEqual(denko_settings.status_code, 302)

        conn = database.get_connection()
        persisted = conn.execute(
            """
            SELECT show_footer, show_qr, show_signature
            FROM sales_quotations
            WHERE id = ?
            """,
            (denko_id,),
        ).fetchone()
        self.assertEqual(persisted["show_footer"], 0)
        self.assertEqual(persisted["show_qr"], 0)
        self.assertEqual(persisted["show_signature"], 0)

        conn.execute(
            """
            UPDATE sales_quotations
            SET show_footer = 1,
                show_qr = 1,
                show_signature = 1
            WHERE id = ?
            """,
            (denko_id,),
        )
        conn.commit()
        conn.close()

        denko_html = self.client.get(
            f"/quotations/{denko_id}/print"
        ).get_data(as_text=True)

        ahsa_id = self.create_quotation(main.IDENTITY_TYPE_FULL)
        ahsa_settings = self.client.post(
            f"/quotations/{ahsa_id}/print-settings",
            data={
                "show_footer": "1",
                "show_qr": "1",
                "show_signature": "1",
            },
        )
        self.assertEqual(ahsa_settings.status_code, 302)
        ahsa_html = self.client.get(
            f"/quotations/{ahsa_id}/print"
        ).get_data(as_text=True)

        self.assertNotIn("<footer", denko_html)
        self.assertNotIn("distributordalton.com", denko_html)
        self.assertNotIn("data:image/png;base64", denko_html)
        self.assertNotIn("Luki Lukmanul Hakim", denko_html)
        self.assertIn('<footer class="footer', ahsa_html)
        self.assertIn("distributordalton.com", ahsa_html)

    def test_duplicate_preserves_identity_and_converted_identity_is_locked(self):
        denko_id = self.create_quotation(main.IDENTITY_TYPE_QUOTATION_ONLY)
        duplicate = self.client.post(f"/quotations/{denko_id}/duplicate")
        self.assertEqual(duplicate.status_code, 302)
        duplicate_id = int(duplicate.headers["Location"].split("/")[-2])

        conn = database.get_connection()
        original = conn.execute(
            "SELECT identity_id FROM sales_quotations WHERE id = ?",
            (denko_id,),
        ).fetchone()
        copied = conn.execute(
            "SELECT identity_id FROM sales_quotations WHERE id = ?",
            (duplicate_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(original["identity_id"], copied["identity_id"])

        ahsa_quote, _ = self.convert_ahsa_quotation()
        response = self.client.post(
            f"/quotations/{ahsa_quote}/edit",
            data={
                "identity_id": str(
                    self.identity_id(main.IDENTITY_TYPE_QUOTATION_ONLY)
                ),
                "customer_id": str(self.customer_id),
                "tanggal": "2026-08-01",
                "berlaku_sampai": "2026-08-15",
                "sales": "QA Engineer",
                "diskon": "0",
                "catatan": "",
                "syarat_ketentuan": "",
                "product_id[]": [str(self.product_id)],
                "qty[]": ["1"],
                "harga_satuan[]": ["100000"],
                "diskon_item[]": ["0"],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Identity quotation yang sudah dikonversi tidak dapat diubah.",
            response.get_data(as_text=True),
        )

    def test_capabilities_cannot_be_changed_from_company_profile_ui(self):
        identity_id = self.identity_id(main.IDENTITY_TYPE_FULL)
        response = self.client.post(
            "/settings/company",
            data={
                "identity_id": str(identity_id),
                "nama_perusahaan": "PT Ahsa Cahaya Persada",
                "nama_brand": "Ahsa Equipment",
                "alamat": "Garut",
                "kota": "Garut",
                "provinsi": "Jawa Barat",
                "kode_pos": "",
                "telepon": "",
                "whatsapp": "082117126895",
                "email": "",
                "website": "distributordalton.com",
                "npwp": "",
                "bank": "BCA",
                "no_rekening": "1483353085",
                "atas_nama": "PT Ahsa Cahaya Persada",
                "footer_invoice": "",
                "footer_quotation": "",
                "footer_purchase_order": "",
                "footer_delivery_order": "",
                "footer_receipt": "",
                "identity_type": "QUOTATION_ONLY",
                "allow_qr": "0",
                "allow_signature": "0",
                "allow_website_footer": "0",
                "allow_transaction_conversion": "0",
                "logo_path": "images/denko_logo.png",
            },
        )
        self.assertEqual(response.status_code, 302)

        conn = database.get_connection()
        identity = conn.execute(
            "SELECT * FROM company_identities WHERE id = ?",
            (identity_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(identity["identity_type"], "FULL")
        self.assertEqual(identity["allow_qr"], 1)
        self.assertEqual(identity["allow_signature"], 1)
        self.assertEqual(identity["allow_website_footer"], 1)
        self.assertEqual(identity["allow_transaction_conversion"], 1)
        self.assertEqual(identity["logo_path"], "images/logo-ahsa.png")

    def test_transaction_documents_always_use_full_identity(self):
        _, transaction_id = self.convert_ahsa_quotation()
        denko_id = self.identity_id(main.IDENTITY_TYPE_QUOTATION_ONLY)

        invoice_response = self.client.post(
            f"/transactions/{transaction_id}/invoice/generate",
            data={"identity_id": str(denko_id)},
        )
        self.assertEqual(invoice_response.status_code, 302)

        delivery_response = self.client.post(
            f"/transactions/{transaction_id}/delivery-order/generate",
            data={"identity_id": str(denko_id)},
        )
        self.assertEqual(delivery_response.status_code, 302)

        conn = database.get_connection()
        invoice = conn.execute(
            "SELECT id FROM sales_invoices WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        delivery_order = conn.execute(
            "SELECT id FROM delivery_orders WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        conn.close()

        receipt_response = self.client.post(
            f"/invoices/{invoice['id']}/receipts/add",
            data={
                "identity_id": str(denko_id),
                "tanggal": "2026-08-01",
                "jenis_pembayaran": "Pelunasan",
                "metode_pembayaran": "Transfer Bank",
                "bank": "BCA",
                "nomor_referensi": "QA-REF",
                "nominal": "100000",
                "untuk_pembayaran": "Pelunasan QA",
                "catatan": "",
            },
        )
        self.assertEqual(
            receipt_response.status_code,
            302,
            receipt_response.get_data(as_text=True),
        )

        conn = database.get_connection()
        receipt = conn.execute(
            "SELECT id FROM payment_receipts WHERE invoice_id = ?",
            (invoice["id"],),
        ).fetchone()
        supplier_id = conn.execute(
            """
            INSERT INTO suppliers (
                kode_supplier,
                nama_supplier,
                status
            )
            VALUES (?, ?, ?)
            """,
            ("SUP-QA", "Supplier QA", "Aktif"),
        ).lastrowid
        purchase_order_id = conn.execute(
            """
            INSERT INTO purchase_orders (
                nomor_po,
                supplier_id,
                transaction_id,
                tanggal,
                status,
                supplier_nama_snapshot,
                grand_total
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PO-QA-001",
                supplier_id,
                transaction_id,
                "2026-08-01",
                "Draft",
                "Supplier QA",
                50000,
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO purchase_order_items (
                purchase_order_id,
                product_id,
                kode_produk_snapshot,
                nama_produk_snapshot,
                satuan_snapshot,
                qty,
                harga_satuan,
                subtotal
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                purchase_order_id,
                self.product_id,
                "QA-001",
                "Produk Regression QA",
                "Unit",
                1,
                50000,
                50000,
            ),
        )
        conn.commit()
        conn.close()

        document_urls = (
            f"/transactions/{transaction_id}/print",
            f"/transactions/{transaction_id}/invoice/print",
            f"/delivery-orders/{delivery_order['id']}/print",
            f"/receipts/{receipt['id']}/print",
            f"/purchase-orders/{purchase_order_id}/print",
        )

        for url in document_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code,
                    200,
                    response.get_data(as_text=True),
                )
                html = response.get_data(as_text=True)
                self.assertIn("images/logo-ahsa.png", html)
                self.assertNotIn("images/denko_logo.png", html)
                self.assertNotIn("PT Denko Wahana Sakti", html)

    def test_all_jinja_templates_compile(self):
        for template_name in main.app.jinja_env.list_templates():
            with self.subTest(template=template_name):
                main.app.jinja_env.get_template(template_name)


if __name__ == "__main__":
    unittest.main()
