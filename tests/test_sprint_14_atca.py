import hashlib
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import database

BOOTSTRAP = tempfile.TemporaryDirectory()
database.DATABASE = Path(BOOTSTRAP.name) / "bootstrap.db"
import main
from customer_360 import get_customer_360
from dashboard_bi import build_atca_dashboard
from module_manager import get_module_states, update_optional_modules


PRINT_HASHES = {
    "quotation_print.html": "aa1e06fadd7c92eda999ce6d203aba92341ca4df222e7fdcaf214d4fff927fd4",
    "invoice_print.html": "4a79d98c15052b2229d52095050664a8b0b2709ae810e5e7b34b0fc74f6637e2",
    "receipt_print.html": "7f77c0bbb71c24f14720fff3a42eaa66357be4fcdd3078e2478cb7c4556b21cc",
    "delivery_order_print.html": "fcf647739fce72ac0a40166510502b4889922d13e966a433b55dbf92105b684a",
}


class AtcaArchitectureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        BOOTSTRAP.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "atca.db"
        database.create_tables()
        self.client = main.app.test_client()
        conn = database.get_connection()
        self.category_id = conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('ATCA',1)"
        ).lastrowid
        self.product_id = conn.execute(
            """INSERT INTO products(
                   kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('ATCA-1','Produk ATCA',?,'Unit',100000,60000,1)""",
            (self.category_id,),
        ).lastrowid
        self.customer_id = conn.execute(
            """INSERT INTO customers(nama,instansi,status,status_aktif,created_at)
               VALUES('Customer ATCA','PT ATCA','Existing Customer',1,'2026-08-01')"""
        ).lastrowid
        self.supplier_id = conn.execute(
            """INSERT INTO suppliers(
                   kode_supplier,nama_supplier,status,status_aktif,payment_term)
               VALUES('SUP-ATCA','Supplier ATCA','Aktif',1,7)"""
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_transaction(self, *, status="Closing", number="TRX-ATCA-1"):
        conn = database.get_connection()
        transaction_id = conn.execute(
            """INSERT INTO sales_transactions(
                   nomor_transaksi,customer_id,tanggal,jenis_penjualan,referal,status,
                   total_penjualan,total_modal,margin,laba_bersih)
               VALUES(?,?,'2026-08-04','Direct','Sales ATCA',?,100000,60000,40000,35000)""",
            (number, self.customer_id, status),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_transaction_items(
                   transaction_id,product_id,kode_produk_snapshot,nama_produk_snapshot,
                   kategori_snapshot,satuan_snapshot,qty,harga_jual_satuan,
                   subtotal_penjualan,harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,'ATCA-1','Produk ATCA','ATCA','Unit',1,100000,100000,60000,60000,40000)""",
            (transaction_id, self.product_id),
        )
        conn.commit()
        conn.close()
        return transaction_id

    def test_transaction_first_requires_no_optional_document(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        self.assertIsNone(
            conn.execute(
                "SELECT source_quotation_id FROM sales_transactions WHERE id=?",
                (transaction_id,),
            ).fetchone()[0]
        )
        for table in (
            "sales_invoices", "payment_receipts", "transaction_receipts",
            "delivery_orders", "purchase_orders", "stock_movements",
        ):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        dashboard = build_atca_dashboard(conn, {"period": "month"}, today=date(2026, 8, 4))
        self.assertEqual(dashboard["financial"]["revenue"], 100000)
        customer = get_customer_360(conn, self.customer_id)
        self.assertEqual(customer["kpis"]["total_transaction"], 1)
        self.assertEqual(len(customer["transaction_items"]), 1)
        conn.close()

    def test_generate_invoice_directly_from_transaction(self):
        transaction_id = self.make_transaction()
        response = self.client.post(f"/transactions/{transaction_id}/invoice/generate")
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        self.assertEqual(
            conn.execute("SELECT transaction_id FROM sales_invoices").fetchone()[0],
            transaction_id,
        )
        conn.close()

    def test_generate_delivery_order_without_invoice(self):
        transaction_id = self.make_transaction()
        response = self.client.post(
            f"/transactions/{transaction_id}/delivery-order/generate"
        )
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        row = conn.execute(
            "SELECT transaction_id,invoice_id FROM delivery_orders"
        ).fetchone()
        self.assertEqual(row["transaction_id"], transaction_id)
        self.assertIsNone(row["invoice_id"])
        conn.close()

    def test_generate_receipt_without_invoice(self):
        transaction_id = self.make_transaction()
        response = self.client.post(
            f"/transactions/{transaction_id}/receipt/generate",
            data={
                "tanggal": "2026-08-04", "jenis_pembayaran": "DP",
                "metode_pembayaran": "Transfer Bank", "nominal": "50000",
                "created_by": "QA",
            },
        )
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sales_invoices").fetchone()[0], 0)
        receipt = conn.execute(
            "SELECT transaction_id,nominal FROM transaction_receipts"
        ).fetchone()
        self.assertEqual((receipt["transaction_id"], receipt["nominal"]), (transaction_id, 50000))
        conn.close()

    def test_generate_purchase_order_without_invoice(self):
        transaction_id = self.make_transaction()
        response = self.client.post(
            f"/transactions/{transaction_id}/purchase-order/generate",
            data={"supplier_id": self.supplier_id, "ppn_persen": "0"},
        )
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        po = conn.execute(
            "SELECT transaction_id,invoice_id FROM purchase_orders"
        ).fetchone()
        self.assertEqual(po["transaction_id"], transaction_id)
        self.assertIsNone(po["invoice_id"])
        conn.close()

    def test_module_off_hides_menu_and_blocks_owned_routes(self):
        transaction_id = self.make_transaction()
        response = self.client.post(
            "/settings/modules",
            data={"enabled_modules": ["warehouse", "accounting", "purchasing"]},
        )
        self.assertEqual(response.status_code, 302)
        dashboard_html = self.client.get("/dashboard").get_data(as_text=True)
        for href in (
            'href="/quotations"', 'href="/receipts"',
            'href="/delivery-orders"', 'href="/purchase-orders"',
            'href="/stocks"',
        ):
            self.assertNotIn(href, dashboard_html)
        transaction_html = self.client.get(
            f"/transactions/{transaction_id}"
        ).get_data(as_text=True)
        for label in (
            "Generate Invoice", "Generate Receipt", "Generate Delivery Order",
            "Generate Purchase Order",
        ):
            self.assertNotIn(label, transaction_html)
        customer_html = self.client.get(
            f"/customers/{self.customer_id}"
        ).get_data(as_text=True)
        self.assertNotIn("Buat Quotation", customer_html)
        for method, path in (
            ("get", "/quotations"),
            ("post", f"/transactions/{transaction_id}/invoice/generate"),
            ("get", f"/transactions/{transaction_id}/receipt/generate"),
            ("post", f"/transactions/{transaction_id}/delivery-order/generate"),
            ("get", f"/transactions/{transaction_id}/purchase-order/generate"),
            ("get", "/stocks"),
        ):
            self.assertEqual(getattr(self.client, method)(path).status_code, 404, path)

    def test_core_modules_cannot_be_disabled(self):
        conn = database.get_connection()
        update_optional_modules(conn, [], actor="QA")
        conn.commit()
        states = get_module_states(conn)
        self.assertTrue(all(states[key]["enabled"] for key in (
            "customer", "product", "transaction", "historical_purchase",
            "dashboard", "customer_360",
        )))
        with self.assertRaises(Exception):
            conn.execute("UPDATE system_modules SET is_enabled=0 WHERE module_key='transaction'")
        conn.close()

    def test_inventory_off_posts_no_stock_movement(self):
        transaction_id = self.make_transaction()
        self.client.post(f"/transactions/{transaction_id}/delivery-order/generate")
        conn = database.get_connection()
        delivery_id = conn.execute("SELECT id FROM delivery_orders").fetchone()[0]
        self.assertEqual(conn.execute("SELECT inventory_enabled FROM erp_settings").fetchone()[0], 0)
        conn.close()
        for status in ("Packing", "Siap Kirim", "Dalam Pengiriman", "Terkirim"):
            response = self.client.post(
                f"/delivery-orders/{delivery_id}/status", data={"status": status}
            )
            self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0], 0)
        conn.close()

    def test_historical_purchase_is_core_but_stays_isolated(self):
        conn = database.get_connection()
        conn.execute(
            """INSERT INTO customer_purchase_history(
                   customer_id,product_id,tanggal_pembelian,nama_produk_snapshot,
                   kategori_snapshot,qty,harga_satuan,total,active)
               VALUES(?,?,'2026-08-03','Produk ATCA','ATCA',2,100000,200000,1)""",
            (self.customer_id, self.product_id),
        )
        conn.commit()
        dashboard = build_atca_dashboard(conn, {"period": "month"}, today=date(2026, 8, 4))
        self.assertEqual(dashboard["financial"]["revenue"], 200000)
        self.assertEqual(dashboard["product"]["top"][0]["quantity"], 2)
        for table in (
            "sales_transactions", "sales_invoices", "payment_receipts",
            "transaction_receipts", "delivery_orders", "purchase_orders",
            "stock_movements", "workflow_events",
        ):
            self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        conn.close()

    def test_dashboard_query_plan_never_reads_optional_tables(self):
        self.make_transaction()
        conn = database.get_connection()
        statements = []
        conn.set_trace_callback(statements.append)
        started = time.perf_counter()
        dashboard = build_atca_dashboard(conn, {}, today=date(2026, 8, 4))
        elapsed_ms = (time.perf_counter() - started) * 1000
        conn.set_trace_callback(None)
        conn.close()
        sql = "\n".join(statements).lower()
        for table in (
            "sales_quotations", "sales_invoices", "payment_receipts",
            "transaction_receipts", "delivery_orders", "purchase_orders",
            "stock_movements", "product_stock", "workflow_events",
        ):
            self.assertNotIn(table, sql)
        self.assertEqual([row["stage"] for row in dashboard["funnel"]], ["Customer", "Transaction", "Selesai"])
        self.assertLess(elapsed_ms, 500)

    def test_migration_twice_integrity_and_foreign_keys(self):
        database.create_tables()
        database.create_tables()
        conn = database.get_connection()
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM system_modules").fetchone()[0], 15)
        self.assertIn(
            "transaction_receipts",
            {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")},
        )
        conn.close()

    def test_print_templates_remain_frozen(self):
        template_dir = APP_DIR / "templates"
        actual = {
            name: hashlib.sha256((template_dir / name).read_bytes()).hexdigest()
            for name in PRINT_HASHES
        }
        self.assertEqual(actual, PRINT_HASHES)


if __name__ == "__main__":
    unittest.main()
