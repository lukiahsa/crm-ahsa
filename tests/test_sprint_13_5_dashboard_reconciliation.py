import hashlib
import sys
import tempfile
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
from dashboard_bi import build_executive_dashboard
from test_transaction_purge import (
    TestTransactionPurgeError,
    can_purge_test_transaction,
    mark_test_transaction,
    purge_test_transaction,
)
from workflow_revision import cancel_transaction


PRINT_HASHES = {
    "quotation_print.html": "dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622",
    "invoice_print.html": "7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8",
    "receipt_print.html": "4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4",
    "delivery_order_print.html": "ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5",
}


class Sprint135DashboardReconciliationTest(unittest.TestCase):
    TODAY = date(2026, 8, 4)

    @classmethod
    def tearDownClass(cls):
        BOOTSTRAP.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "sprint-13-5.db"
        database.create_tables()
        self.conn = database.get_connection()
        self._seed()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def _seed(self):
        self.category = self.conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('Tempat Sampah',1)"
        ).lastrowid
        self.other_category = self.conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('Tangga',1)"
        ).lastrowid
        self.product = self.conn.execute(
            """INSERT INTO products(kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('TS-120-H','Tempat Sampah 120 Liter Roda Hijau',?,'Unit',120000,70000,1)""",
            (self.category,),
        ).lastrowid
        self.other_product = self.conn.execute(
            """INSERT INTO products(kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('TG-1','Tangga',?,'Unit',500000,350000,1)""",
            (self.other_category,),
        ).lastrowid
        self.geugeu = self.conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Ibu Geugeu','Existing Customer',1,'2026-06-01')"""
        ).lastrowid
        self.new_customer = self.conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Prospek Agustus','Prospek',1,'2026-08-02')"""
        ).lastrowid
        self.never_customer = self.conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Belum Order','Prospek',1,'2025-01-01')"""
        ).lastrowid
        self.stale_customer = self.conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Lama Order','Existing Customer',1,'2024-01-01')"""
        ).lastrowid
        self.inactive_customer = self.conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Nonaktif','Prospek',0,'2025-01-01')"""
        ).lastrowid
        self.official = self._transaction(
            "TRX-OFFICIAL", self.geugeu, "2026-06-10", self.product,
            revenue=120000, cost=70000, is_test=0, status="Selesai",
        )
        self.history_priced = self.conn.execute(
            """INSERT INTO customer_purchase_history(
                   customer_id,product_id,tanggal_pembelian,kode_produk_snapshot,
                   nama_produk_snapshot,kategori_snapshot,qty,harga_satuan,total,
                   created_at,active)
               VALUES(?,?,?,?,?,?,?,?,?,'2026-08-03 10:00:00',1)""",
            (
                self.geugeu, self.product, "2026-07-15", "TS-120-H",
                "Tempat Sampah 120 Liter Roda Hijau", "Tempat Sampah", 12,
                120000, 1440000,
            ),
        ).lastrowid
        self.history_unpriced = self.conn.execute(
            """INSERT INTO customer_purchase_history(
                   customer_id,product_id,tanggal_pembelian,nama_produk_snapshot,
                   kategori_snapshot,qty,harga_satuan,total,active)
               VALUES(?,?,?,?,?,?,NULL,NULL,1)""",
            (
                self.new_customer, self.other_product, "2026-07-20",
                "Tangga", "Tangga", 3,
            ),
        ).lastrowid
        self.conn.execute(
            """INSERT INTO customer_purchase_history(
                   customer_id,product_id,tanggal_pembelian,nama_produk_snapshot,
                   kategori_snapshot,qty,total,active)
               VALUES(?,?,?,?,?,?,?,1)""",
            (
                self.stale_customer, self.product, "2025-01-10",
                "Tempat Sampah Lama", "Tempat Sampah", 1, 100000,
            ),
        )
        self.supplier = self.conn.execute(
            "INSERT INTO suppliers(kode_supplier,nama_supplier,status,status_aktif) VALUES('SUP-T','Supplier Test','Aktif',1)"
        ).lastrowid
        self.warehouse = self.conn.execute(
            "INSERT INTO warehouses(kode_gudang,nama_gudang,aktif) VALUES('WH-T','Test',1)"
        ).lastrowid
        self.conn.commit()

    def _transaction(self, number, customer, when, product, *, revenue=1000, cost=500,
                     is_test=1, status="Draft"):
        transaction_id = self.conn.execute(
            """INSERT INTO sales_transactions(
                   nomor_transaksi,customer_id,tanggal,jenis_penjualan,referal,status,
                   total_penjualan,total_modal,margin,laba_bersih,is_test,test_label)
               VALUES(?,?,?,'Direct','QA',?,?,?,?,?,?,?)""",
            (
                number, customer, when, status, revenue, cost, revenue - cost,
                revenue - cost, is_test, "QA" if is_test else None,
            ),
        ).lastrowid
        self.conn.execute(
            """INSERT INTO sales_transaction_items(
                   transaction_id,product_id,nama_produk_snapshot,qty,harga_jual_satuan,
                   subtotal_penjualan,harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,?,1,?,?,?,?,?)""",
            (
                transaction_id, product, "Test Item", revenue, revenue,
                cost, cost, revenue - cost,
            ),
        )
        return transaction_id

    def dashboard(self, start="2026-07-01", end="2026-07-31", **extra):
        args = {"period": "custom", "start_date": start, "end_date": end, **extra}
        return build_executive_dashboard(self.conn, args, today=self.TODAY)

    def _isolated_test(self, suffix="1"):
        transaction_id = self._transaction(
            f"TRX-TEST-{suffix}", self.new_customer, "2026-08-01", self.other_product,
        )
        self.conn.commit()
        return transaction_id

    def test_01_historical_july_enters_july_revenue(self):
        self.assertEqual(self.dashboard()["financial"]["revenue"], 1440000)

    def test_02_historical_enters_year_revenue(self):
        self.assertEqual(self.dashboard("2026-01-01", "2026-12-31")["financial"]["revenue"], 1560000)

    def test_03_old_historical_uses_correct_trend_month(self):
        self.conn.execute(
            """INSERT INTO customer_purchase_history(customer_id,product_id,tanggal_pembelian,
                   nama_produk_snapshot,qty,total,active) VALUES(?,?,'2025-10-09','Old',1,321000,1)""",
            (self.geugeu, self.product),
        )
        trend = {row["label"]: row for row in self.dashboard()["trend"]}
        self.assertEqual(trend["Oct 2025"]["revenue"], 321000)

    def test_04_created_at_does_not_replace_purchase_date(self):
        self.assertEqual(self.dashboard("2026-08-01", "2026-08-31")["financial"]["revenue"], 0)

    def test_05_priced_historical_adds_revenue(self):
        self.assertEqual(self.dashboard()["financial"]["revenue"], 12 * 120000)

    def test_06_unpriced_historical_does_not_add_revenue(self):
        result = self.dashboard(customer_id=str(self.new_customer))
        self.assertEqual(result["financial"]["revenue"], 0)

    def test_07_unpriced_historical_still_adds_quantity(self):
        result = self.dashboard(customer_id=str(self.new_customer))
        self.assertEqual(result["product"]["top"][0]["quantity"], 3)

    def test_08_historical_enters_top_product(self):
        self.assertEqual(self.dashboard()["product"]["top"][0]["id"], self.product)

    def test_09_historical_enters_top_customer(self):
        self.assertEqual(self.dashboard()["customer"]["top"][0]["nama"], "Ibu Geugeu")

    def test_10_historical_enters_repeat_customer(self):
        self.conn.execute(
            """INSERT INTO customer_purchase_history(customer_id,product_id,tanggal_pembelian,
                   nama_produk_snapshot,qty,active) VALUES(?,?,'2026-07-16','Second',1,1)""",
            (self.geugeu, self.product),
        )
        self.assertEqual(self.dashboard()["customer"]["repeat"], 1)

    def test_11_historical_never_enters_sales_funnel(self):
        self.assertTrue(all(row["count"] == 0 for row in self.dashboard()["funnel"]))

    def test_12_existing_customer_status_is_exact(self):
        self.assertEqual(self.dashboard()["customer"]["existing"], 2)

    def test_13_new_customer_is_period_scoped(self):
        self.assertEqual(self.dashboard("2026-08-01", "2026-08-31")["customer"]["new"], 1)

    def test_14_never_order_is_separate_from_stale(self):
        customer = self.dashboard()["customer"]
        self.assertIn("Belum Order", {row["nama"] for row in customer["never_order"]})
        self.assertNotIn("Belum Order", {row["nama"] for row in customer["stale"]})

    def test_15_custom_range_is_inclusive(self):
        self.assertEqual(self.dashboard("2026-07-15", "2026-07-15")["financial"]["revenue"], 1440000)

    def test_16_customer_filter_applies_to_history(self):
        self.assertEqual(self.dashboard(customer_id=str(self.geugeu))["financial"]["revenue"], 1440000)

    def test_17_product_filter_applies_to_history(self):
        self.assertEqual(self.dashboard(product_id=str(self.product))["financial"]["revenue"], 1440000)

    def test_18_category_filter_applies_to_history(self):
        self.assertEqual(self.dashboard(category_id=str(self.other_category))["financial"]["revenue"], 0)

    def test_19_sales_filter_excludes_unowned_history(self):
        self.assertEqual(self.dashboard(sales="QA")["financial"]["transactions"], 0)

    def test_20_transaction_can_be_marked_test(self):
        transaction_id = self._transaction("TRX-MARK", self.geugeu, "2026-08-01", self.product, is_test=0)
        self.conn.commit()
        mark_test_transaction(self.conn, transaction_id, reason="Data latihan")
        self.assertEqual(self.conn.execute("SELECT is_test FROM sales_transactions WHERE id=?", (transaction_id,)).fetchone()[0], 1)

    def test_21_non_test_transaction_cannot_be_purged(self):
        self.assertFalse(can_purge_test_transaction(self.conn, self.official)["allowed"])

    def test_22_isolated_test_transaction_can_be_purged(self):
        transaction_id = self._isolated_test("PURGE")
        purge_test_transaction(self.conn, transaction_id, reason="QA", confirmation_number="TRX-TEST-PURGE")
        self.assertIsNone(self.conn.execute("SELECT id FROM sales_transactions WHERE id=?", (transaction_id,)).fetchone())

    def test_23_purge_requires_reason(self):
        transaction_id = self._isolated_test("REASON")
        with self.assertRaisesRegex(TestTransactionPurgeError, "Alasan"):
            purge_test_transaction(self.conn, transaction_id, reason="", confirmation_number="TRX-TEST-REASON")

    def test_24_purge_requires_exact_confirmation(self):
        transaction_id = self._isolated_test("CONFIRM")
        with self.assertRaisesRegex(TestTransactionPurgeError, "tidak cocok"):
            purge_test_transaction(self.conn, transaction_id, reason="QA", confirmation_number="WRONG")

    def test_25_purge_fails_with_invoice(self):
        transaction_id = self._isolated_test("INV")
        self.conn.execute("INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice) VALUES(?,'INV-T','2026-08-01')", (transaction_id,))
        self.conn.commit()
        self.assertIn("invoice", " ".join(can_purge_test_transaction(self.conn, transaction_id)["reasons"]).lower())

    def test_26_purge_fails_with_receipt(self):
        transaction_id = self._isolated_test("RCPT")
        invoice = self.conn.execute("INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice) VALUES(?,'INV-R','2026-08-01')", (transaction_id,)).lastrowid
        self.conn.execute("""INSERT INTO payment_receipts(nomor_kwitansi,invoice_id,transaction_id,tanggal,
            jenis_pembayaran,metode_pembayaran,nominal,untuk_pembayaran) VALUES('R-T',?,?,'2026-08-01','DP','Cash',1,'Invoice')""", (invoice, transaction_id))
        self.conn.commit()
        self.assertIn("receipt", " ".join(can_purge_test_transaction(self.conn, transaction_id)["reasons"]).lower())

    def test_27_purge_fails_with_delivery_order(self):
        transaction_id = self._isolated_test("DO")
        self.conn.execute("INSERT INTO delivery_orders(nomor_surat_jalan,transaction_id,tanggal) VALUES('DO-T',?,'2026-08-01')", (transaction_id,))
        self.conn.commit()
        self.assertIn("delivery", " ".join(can_purge_test_transaction(self.conn, transaction_id)["reasons"]).lower())

    def test_28_purge_fails_with_purchase_order(self):
        transaction_id = self._isolated_test("PO")
        self.conn.execute("INSERT INTO purchase_orders(nomor_po,supplier_id,transaction_id,tanggal) VALUES('PO-T',?,?,'2026-08-01')", (self.supplier, transaction_id))
        self.conn.commit()
        self.assertIn("purchase", " ".join(can_purge_test_transaction(self.conn, transaction_id)["reasons"]).lower())

    def test_29_purge_fails_with_stock_movement(self):
        transaction_id = self._isolated_test("STOCK")
        self.conn.execute("""INSERT INTO stock_movements(tanggal,warehouse_id,product_id,movement_type,qty,
            saldo_setelah,source_type,source_id) VALUES('2026-08-01',?,?,'OUT',1,0,'TRANSACTION',?)""", (self.warehouse, self.product, transaction_id))
        self.conn.commit()
        self.assertIn("stock", " ".join(can_purge_test_transaction(self.conn, transaction_id)["reasons"]).lower())

    def test_30_purge_fails_with_quotation_revision(self):
        transaction_id = self._isolated_test("REV")
        identity = self.conn.execute("SELECT id FROM company_identities WHERE identity_type='FULL' LIMIT 1").fetchone()[0]
        quotation = self.conn.execute("""INSERT INTO sales_quotations(nomor_penawaran,tanggal,status,
            identity_id,subtotal,dpp,grand_total) VALUES('QT-T','2026-08-01','Draft',?,0,0,0)""", (identity,)).lastrowid
        self.conn.execute("INSERT INTO quotation_revisions(quotation_id,revision_no,reason,old_transaction_id) VALUES(?,1,'QA',?)", (quotation, transaction_id))
        self.conn.commit()
        self.assertIn("revision", " ".join(can_purge_test_transaction(self.conn, transaction_id)["reasons"]).lower())

    def test_31_purge_is_atomic_and_rolls_back(self):
        transaction_id = self._isolated_test("ROLLBACK")
        def fail(_stage):
            raise RuntimeError("failure injection")
        with self.assertRaises(RuntimeError):
            purge_test_transaction(self.conn, transaction_id, reason="QA", confirmation_number="TRX-TEST-ROLLBACK", _failure_hook=fail)
        self.assertIsNotNone(self.conn.execute("SELECT id FROM sales_transactions WHERE id=?", (transaction_id,)).fetchone())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM test_transaction_purge_audit").fetchone()[0], 0)

    def test_32_purge_audit_survives_delete(self):
        transaction_id = self._isolated_test("AUDIT")
        purge_test_transaction(self.conn, transaction_id, reason="QA audit", confirmation_number="TRX-TEST-AUDIT", actor="Tester")
        row = self.conn.execute("SELECT * FROM test_transaction_purge_audit WHERE transaction_id_snapshot=?", (transaction_id,)).fetchone()
        self.assertEqual(row["reason"], "QA audit")

    def test_33_customer_and_product_master_survive_purge(self):
        transaction_id = self._isolated_test("MASTER")
        purge_test_transaction(self.conn, transaction_id, reason="QA", confirmation_number="TRX-TEST-MASTER")
        self.assertIsNotNone(self.conn.execute("SELECT id FROM customers WHERE id=?", (self.new_customer,)).fetchone())
        self.assertIsNotNone(self.conn.execute("SELECT id FROM products WHERE id=?", (self.other_product,)).fetchone())

    def test_34_historical_purchase_survives_purge(self):
        transaction_id = self._isolated_test("HISTORY")
        purge_test_transaction(self.conn, transaction_id, reason="QA", confirmation_number="TRX-TEST-HISTORY")
        self.assertIsNotNone(self.conn.execute("SELECT id FROM customer_purchase_history WHERE id=?", (self.history_unpriced,)).fetchone())

    def test_35_cancel_transaction_existing_still_works(self):
        transaction_id = self._transaction("TRX-CANCEL", self.geugeu, "2026-08-01", self.product, is_test=0)
        self.conn.commit()
        cancel_transaction(self.conn, transaction_id, reason="Bisnis batal", actor="QA")
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT status FROM sales_transactions WHERE id=?", (transaction_id,)).fetchone()[0], "Cancelled")

    def test_36_workflow_integrity_data_is_not_deleted(self):
        before = self.conn.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0]
        transaction_id = self._isolated_test("WF")
        purge_test_transaction(self.conn, transaction_id, reason="QA", confirmation_number="TRX-TEST-WF")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0], before)

    def test_37_revision_engine_schema_remains_available(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(quotation_revisions)")}
        self.assertTrue({"old_transaction_id", "new_transaction_id"}.issubset(columns))

    def test_38_financial_invariant_remains_exact(self):
        row = self.conn.execute("""SELECT t.total_penjualan,SUM(i.subtotal_penjualan),
            t.total_modal,SUM(i.subtotal_modal),t.margin,SUM(i.margin_item)
            FROM sales_transactions t JOIN sales_transaction_items i ON i.transaction_id=t.id
            WHERE t.id=? GROUP BY t.id""", (self.official,)).fetchone()
        self.assertEqual(tuple(row), (120000, 120000, 70000, 70000, 50000, 50000))

    def test_39_customer_360_remains_consistent(self):
        result = get_customer_360(self.conn, self.geugeu)
        self.assertEqual(result["kpis"]["total_unit_dibeli"], 13)
        self.assertTrue(result["kpis"]["is_repeat_customer"])

    def test_40_print_hashes_are_frozen(self):
        for name, expected in PRINT_HASHES.items():
            self.assertEqual(hashlib.sha256((PROJECT_ROOT / "app" / "templates" / name).read_bytes()).hexdigest(), expected)

    def test_41_migration_twice_is_idempotent(self):
        database.create_tables()
        database.create_tables()
        columns = [row[1] for row in self.conn.execute("PRAGMA table_info(sales_transactions)")]
        self.assertEqual(columns.count("is_test"), 1)
        self.assertEqual(columns.count("test_label"), 1)

    def test_42_integrity_check_is_ok(self):
        self.assertEqual(self.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_43_foreign_key_check_is_empty(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_44_invoice_kpi_uses_invoice_business_date(self):
        self.conn.execute(
            """INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice,
                   status_pembayaran) VALUES(?,'INV-BUSINESS-DATE','2026-07-31','Belum Lunas')""",
            (self.official,),
        )
        self.conn.commit()
        result = self.dashboard("2026-07-31", "2026-07-31")
        self.assertEqual(result["financial"]["outstanding_invoice"], 1)
        self.assertEqual(result["financial"]["receivable"], 120000)

    def test_45_legacy_or_conversion_insert_defaults_to_non_test(self):
        transaction_id = self.conn.execute(
            """INSERT INTO sales_transactions(nomor_transaksi,customer_id,tanggal,
                   jenis_penjualan,status) VALUES('TRX-DEFAULT-NON-TEST',?,'2026-08-01','Direct','Draft')""",
            (self.geugeu,),
        ).lastrowid
        self.assertEqual(self.conn.execute("SELECT is_test FROM sales_transactions WHERE id=?", (transaction_id,)).fetchone()[0], 0)

    def test_46_non_test_detail_never_shows_hard_delete(self):
        client = main.app.test_client()
        response = client.get(f"/transactions/{self.official}")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Hapus Transaksi Uji Coba", response.data)

    def test_47_eligible_test_detail_shows_controlled_purge(self):
        transaction_id = self._isolated_test("UI")
        client = main.app.test_client()
        response = client.get(f"/transactions/{transaction_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hapus Transaksi Uji Coba", response.data)


if __name__ == "__main__":
    unittest.main()
