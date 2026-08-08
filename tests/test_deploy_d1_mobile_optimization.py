import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
TEMPLATE_DIR = APP_DIR / "templates"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import database

BOOTSTRAP = tempfile.TemporaryDirectory()
database.DATABASE = Path(BOOTSTRAP.name) / "bootstrap.db"
import main
from module_manager import update_optional_modules


PRINT_HASHES = {
    "quotation_print.html": "dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622",
    "invoice_print.html": "7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8",
    "receipt_print.html": "4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4",
    "delivery_order_print.html": "ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5",
    "purchase_order_print.html": "139d248828adbaa55d6b8672a65d4ebc6d04d00a6e2c7cece305d81075ff6064",
}


class DeployD1MobileOptimizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        BOOTSTRAP.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "mobile.db"
        database.create_tables()
        self.client = main.app.test_client()
        conn = database.get_connection()
        update_optional_modules(
            conn,
            {
                "quotation", "invoice", "receipt", "delivery_order",
                "purchase_order", "purchasing", "accounting",
            },
            actor="QA Deployment D1",
        )
        category_id = conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('Mobile',1)"
        ).lastrowid
        self.product_id = conn.execute(
            """INSERT INTO products(
                   kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('MOB-120','Tempat Sampah Mobile',?,'Unit',100000,60000,1)""",
            (category_id,),
        ).lastrowid
        self.customer_id = conn.execute(
            """INSERT INTO customers(
                   nama,instansi,whatsapp,whatsapp_normalized,pic,kota,sumber,
                   status,status_aktif,created_at)
               VALUES('Customer Mobile','PT Mobile','081234567890',
                      '6281234567890','Bapak Mobile','Bandung','WhatsApp Organik',
                      'Existing Customer',1,'2026-08-08')"""
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_transaction(self):
        conn = database.get_connection()
        transaction_id = conn.execute(
            """INSERT INTO sales_transactions(
                   nomor_transaksi,customer_id,tanggal,jenis_penjualan,referal,status,
                   total_penjualan,total_modal,margin,laba_bersih)
               VALUES('TRX-MOBILE-001',?,'2026-08-08','Direct','Luki','Closing',
                      100000,60000,40000,40000)""",
            (self.customer_id,),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_transaction_items(
                   transaction_id,product_id,kode_produk_snapshot,nama_produk_snapshot,
                   kategori_snapshot,satuan_snapshot,qty,harga_jual_satuan,
                   subtotal_penjualan,harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,'MOB-120','Tempat Sampah Mobile','Mobile','Unit',
                      1,100000,100000,60000,60000,40000)""",
            (transaction_id, self.product_id),
        )
        conn.commit()
        conn.close()
        return transaction_id

    def test_core_daily_operation_pages_remain_http_200(self):
        for path in (
            "/dashboard", "/customers", f"/customers/{self.customer_id}",
            "/products", "/products/add", "/quotations", "/quotations/add",
            "/transactions", "/transactions/add", "/receipts",
            "/delivery-orders", "/settings", "/settings/modules",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_mobile_stylesheet_and_navigation_are_loaded(self):
        response = self.client.get("/customers")
        html = response.get_data(as_text=True)
        self.assertIn("/static/css/mobile.css", html)
        self.assertIn("data-mobile-navigation", html)
        self.assertIn('aria-controls="mobile-primary-menu"', html)
        css_response = self.client.get("/static/css/mobile.css")
        js_response = self.client.get("/static/js/mobile.js")
        try:
            self.assertEqual(css_response.status_code, 200)
            self.assertEqual(js_response.status_code, 200)
        finally:
            css_response.close()
            js_response.close()

    def test_mobile_navigation_respects_optional_module_manager(self):
        conn = database.get_connection()
        update_optional_modules(conn, {"quotation"}, actor="QA Module OFF")
        conn.commit()
        conn.close()
        html = self.client.get("/customers").get_data(as_text=True)
        mobile_menu = re.search(
            r'<nav class="mobile-menu".*?</nav>', html, flags=re.DOTALL
        ).group(0)
        self.assertIn("Dashboard", mobile_menu)
        self.assertIn("Customer", mobile_menu)
        self.assertIn("Product", mobile_menu)
        self.assertIn("Quotation", mobile_menu)
        self.assertIn("Transaction", mobile_menu)
        self.assertNotIn("Receipt", mobile_menu)
        self.assertNotIn("Delivery Order", mobile_menu)
        self.assertNotIn("Purchase Order", mobile_menu)
        self.assertEqual(self.client.get("/receipts").status_code, 404)

    def test_transaction_workspace_actions_follow_modules(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        update_optional_modules(
            conn,
            {"quotation", "invoice", "delivery_order"},
            actor="QA Workspace",
        )
        conn.commit()
        conn.close()
        html = self.client.get(f"/transactions/{transaction_id}").get_data(as_text=True)
        action_panel = html[html.index('id="action-panel"'):html.index("</aside>")]
        self.assertIn("Generate Invoice", action_panel)
        self.assertIn("Generate Delivery Order", action_panel)
        self.assertNotIn("Generate Receipt", action_panel)
        self.assertNotIn("Generate Purchase Order", action_panel)

    def test_customer_form_keeps_fields_and_mobile_input_types(self):
        html = self.client.get("/customers").get_data(as_text=True)
        for field in ("nama", "whatsapp", "instansi", "kota", "produk", "sumber", "status", "catatan"):
            self.assertIn(f'name="{field}"', html)
        self.assertIn('id="customer-whatsapp" type="tel"', html)
        self.assertIn('class="full mobile-save-bar"', html)

    def test_quotation_form_keeps_existing_post_structure(self):
        html = self.client.get("/quotations/add").get_data(as_text=True)
        for field in (
            "identity_id", "customer_id", "sales", "tanggal", "berlaku_sampai",
            "product_id[]", "qty[]", "harga_satuan[]", "diskon_item[]",
            "diskon", "catatan", "syarat_ketentuan",
        ):
            self.assertIn(f'name="{field}"', html)
        self.assertIn("data-quotation-form", html)

    def test_transaction_form_keeps_existing_post_structure(self):
        html = self.client.get("/transactions/add").get_data(as_text=True)
        for field in (
            "customer_id", "tanggal", "jenis_penjualan", "referal",
            "product_id[]", "qty[]", "harga_jual[]", "harga_modal[]",
        ):
            self.assertIn(f'name="{field}"', html)

    def test_document_preview_shell_keeps_print_document_separate(self):
        transaction_id = self.make_transaction()
        response = self.client.get(
            f"/document-preview/transaction/{transaction_id}"
        )
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Cetak / Simpan PDF", html)
        self.assertIn(f'/transactions/{transaction_id}/print', html)
        self.assertIn("Dokumen asli tidak diubah", html)
        self.assertEqual(
            self.client.get(f"/document-preview/unknown/{transaction_id}").status_code,
            404,
        )

    def test_frozen_print_templates_remain_byte_identical(self):
        for name, expected_hash in PRINT_HASHES.items():
            with self.subTest(template=name):
                actual = hashlib.sha256((TEMPLATE_DIR / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected_hash)

    def test_purchase_order_print_and_address_remain_unchanged(self):
        source = (TEMPLATE_DIR / "purchase_order_print.html").read_text("utf-8")
        self.assertEqual(
            hashlib.sha256(source.encode()).hexdigest(),
            PRINT_HASHES["purchase_order_print.html"],
        )
        conn = database.get_connection()
        company = conn.execute(
            "SELECT alamat FROM company_identities WHERE code='AHSA'"
        ).fetchone()
        conn.close()
        self.assertIn("Kp. Jati", company["alamat"])

    def test_notes_and_attachments_do_not_appear_in_print_templates(self):
        combined = "\n".join(
            (TEMPLATE_DIR / name).read_text("utf-8")
            for name in (*PRINT_HASHES, "transaction_print.html")
        )
        for internal_marker in (
            "workspace-notes", "transaction-attachments", "note_text",
            "attachment_type", "Upload Attachment",
        ):
            self.assertNotIn(internal_marker, combined)

    def test_deployment_adds_no_database_migration(self):
        database_source = (APP_DIR / "database.py").read_bytes()
        self.assertEqual(
            hashlib.sha256(database_source).hexdigest(),
            "39aed6eeb2a417cf5f4de53460b09834e3068338106c6acdbf77b5a4847d9a21",
        )
        conn = database.get_connection()
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()


if __name__ == "__main__":
    unittest.main()
