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
from dashboard_bi import build_executive_dashboard, resolve_dashboard_filters


PRINT_HASHES = {
    "quotation_print.html": "dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622",
    "invoice_print.html": "7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8",
    "receipt_print.html": "4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4",
    "delivery_order_print.html": "ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5",
}


class ExecutiveDashboardRegressionTest(unittest.TestCase):
    TODAY = date(2026, 8, 4)

    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        BOOTSTRAP.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "dashboard.db"
        database.create_tables()
        self.client = main.app.test_client()
        self._seed()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _seed(self):
        conn = database.get_connection()
        category_ladder = conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('Ladder',1)"
        ).lastrowid
        category_bin = conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('Bin',1)"
        ).lastrowid
        self.product_ladder = conn.execute(
            """INSERT INTO products(kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('LAD-1','Ladder Pro',?,'Unit',100000,60000,1)""",
            (category_ladder,),
        ).lastrowid
        self.product_bin = conn.execute(
            """INSERT INTO products(kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('BIN-1','Denko Bin',?,'Unit',50000,20000,1)""",
            (category_bin,),
        ).lastrowid
        self.product_unsold = conn.execute(
            """INSERT INTO products(kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('LAD-2','Ladder Unsold',?,'Unit',80000,45000,1)""",
            (category_ladder,),
        ).lastrowid
        self.customer_repeat = conn.execute(
            """INSERT INTO customers(nama,instansi,status,status_aktif,created_at)
               VALUES('PT Repeat','Repeat Corp','Existing Customer',1,'2026-01-01')"""
        ).lastrowid
        self.customer_new = conn.execute(
            """INSERT INTO customers(nama,instansi,status,status_aktif,created_at)
               VALUES('Customer Baru','New Corp','Prospek',1,'2026-08-02')"""
        ).lastrowid
        self.customer_stale = conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Customer Stale','Existing Customer',1,'2025-01-01')"""
        ).lastrowid
        self.customer_never = conn.execute(
            """INSERT INTO customers(nama,status,status_aktif,created_at)
               VALUES('Customer Never','Prospek',1,'2025-01-01')"""
        ).lastrowid
        conn.execute(
            """INSERT INTO customer_purchase_history(
                   customer_id, product_id, tanggal_pembelian, nama_produk_snapshot,
                   qty, active)
               VALUES(?,?, '2025-01-02', 'Historical Stale', 1, 1)""",
            (self.customer_stale, self.product_bin),
        )
        warehouse = conn.execute(
            "INSERT INTO warehouses(kode_gudang,nama_gudang,aktif) VALUES('WH-DASH','Dashboard',1)"
        ).lastrowid
        conn.execute(
            "INSERT INTO product_stock(product_id,warehouse_id,stok,minimum_stok) VALUES(?,?,1,2)",
            (self.product_ladder, warehouse),
        )
        conn.execute(
            "INSERT INTO product_stock(product_id,warehouse_id,stok,minimum_stok) VALUES(?,?,10,2)",
            (self.product_bin, warehouse),
        )

        identity = conn.execute(
            "SELECT id FROM company_identities WHERE identity_type='FULL' ORDER BY is_default DESC,id LIMIT 1"
        ).fetchone()[0]
        quotation_1 = self._quotation(
            conn, "QT-DASH-1", self.customer_repeat, "2026-08-01", "Alice", "Deal",
            "2026-08-10", 200000, identity, self.product_ladder, 2, 100000,
        )
        self._quotation(
            conn, "QT-DASH-2", self.customer_new, "2026-08-02", "Bob", "Draft",
            "2026-08-03", 50000, identity, self.product_bin, 1, 50000,
        )
        transaction_1 = self._transaction(
            conn, "TRX-DASH-1", self.customer_repeat, "2026-08-01", "Alice", "Closing",
            200000, 120000, 80000, 70000, quotation_1, self.product_ladder, 2,
        )
        transaction_2 = self._transaction(
            conn, "TRX-DASH-2", self.customer_repeat, "2026-08-02", "Alice", "Selesai",
            100000, 60000, 40000, 35000, None, self.product_ladder, 1,
        )
        transaction_3 = self._transaction(
            conn, "TRX-DASH-3", self.customer_new, "2026-08-03", "Bob", "Closing",
            50000, 20000, 30000, 25000, None, self.product_bin, 1,
        )
        self._transaction(
            conn, "TRX-CANCELLED", self.customer_new, "2026-08-03", "Bob", "Cancelled",
            999000, 0, 999000, 999000, None, self.product_bin, 1,
        )
        conn.execute(
            "UPDATE sales_quotations SET converted_transaction_id=? WHERE id=?",
            (transaction_1, quotation_1),
        )
        invoice_1 = conn.execute(
            """INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice,
                   jatuh_tempo,status_pembayaran)
               VALUES(?,'INV-DASH-1','2026-08-01','2026-08-02','Belum Lunas')""",
            (transaction_1,),
        ).lastrowid
        invoice_2 = conn.execute(
            """INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice,
                   jatuh_tempo,status_pembayaran)
               VALUES(?,'INV-DASH-2','2026-08-02','2026-08-20','Lunas')""",
            (transaction_2,),
        ).lastrowid
        conn.execute(
            """INSERT INTO payment_receipts(nomor_kwitansi,invoice_id,transaction_id,tanggal,
                   jenis_pembayaran,metode_pembayaran,nominal,untuk_pembayaran,status)
               VALUES('RCPT-DASH-1',?,?,'2026-08-02','Pelunasan','Transfer',100000,'Invoice','Diterbitkan')""",
            (invoice_2, transaction_2),
        )
        conn.execute(
            """INSERT INTO payment_receipts(nomor_kwitansi,invoice_id,transaction_id,tanggal,
                   jenis_pembayaran,metode_pembayaran,nominal,untuk_pembayaran,status)
               VALUES('RCPT-DASH-VOID',?,?,'2026-08-02','DP','Transfer',50000,'Invoice','Void')""",
            (invoice_1, transaction_1),
        )
        conn.execute(
            """INSERT INTO delivery_orders(nomor_surat_jalan,transaction_id,invoice_id,warehouse_id,
                   tanggal,status) VALUES('DO-DASH-1',?,?,?,'2026-08-02','Diterima')""",
            (transaction_2, invoice_2, warehouse),
        )
        supplier = conn.execute(
            "INSERT INTO suppliers(kode_supplier,nama_supplier,status,status_aktif) VALUES('SUP-DASH','Supplier Dashboard','Aktif',1)"
        ).lastrowid
        conn.execute(
            """INSERT INTO purchase_orders(nomor_po,supplier_id,transaction_id,warehouse_id,tanggal,
                   estimasi_datang,status,grand_total,supplier_nama_snapshot)
               VALUES('PO-DASH-1',?,?,?,'2026-08-01','2026-08-02','Dikirim',120000,'Supplier Dashboard')""",
            (supplier, transaction_1, warehouse),
        )
        conn.execute(
            """INSERT INTO purchase_orders(nomor_po,supplier_id,transaction_id,warehouse_id,tanggal,
                   estimasi_datang,status,grand_total,supplier_nama_snapshot)
               VALUES('PO-DASH-2',?,?,?,'2026-08-02','2026-08-02','Selesai',60000,'Supplier Dashboard')""",
            (supplier, transaction_2, warehouse),
        )
        conn.execute(
            "INSERT INTO stock_movements(tanggal,warehouse_id,product_id,movement_type,qty,saldo_setelah) VALUES('2026-08-01',?,?, 'IN',5,5)",
            (warehouse, self.product_ladder),
        )
        conn.execute(
            "INSERT INTO stock_movements(tanggal,warehouse_id,product_id,movement_type,qty,saldo_setelah) VALUES('2026-08-02',?,?, 'OUT',3,2)",
            (warehouse, self.product_ladder),
        )
        conn.execute(
            "INSERT INTO stock_movements(tanggal,warehouse_id,product_id,movement_type,qty,saldo_setelah) VALUES('2026-08-03',?,?, 'OUT',1,9)",
            (warehouse, self.product_bin),
        )
        for index, event in enumerate((
            "QUOTATION_CREATED", "TRANSACTION_CREATED", "INVOICE_CREATED", "RECEIPT_CREATED",
            "DELIVERY_ORDER_CREATED", "PURCHASE_ORDER_CREATED", "QUOTATION_REVISED",
            "TRANSACTION_CANCELLED",
        ), start=1):
            conn.execute(
                """INSERT INTO workflow_events(document_type,document_id,customer_id,event_type,
                       description,created_by,created_at)
                   VALUES('DASHBOARD',?,?,?,'Dashboard event','QA',?)""",
                (index, self.customer_repeat, event, f"2026-08-03 0{index}:00:00"),
            )
        conn.commit()
        conn.close()

    def _quotation(self, conn, number, customer, transaction_date, sales, status,
                   valid_until, total, identity, product, qty, unit_price):
        quotation_id = conn.execute(
            """INSERT INTO sales_quotations(nomor_penawaran,customer_id,tanggal,berlaku_sampai,
                   sales,status,subtotal,dpp,grand_total,identity_id,customer_nama_snapshot)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (number, customer, transaction_date, valid_until, sales, status, total, total,
             total, identity, number),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_quotation_items(quotation_id,product_id,nama_produk_snapshot,
                   qty,harga_satuan,subtotal,harga_modal_snapshot)
               VALUES(?,?,?,?,?,?,?)""",
            (quotation_id, product, number, qty, unit_price, total, 60000),
        )
        return quotation_id

    def _transaction(self, conn, number, customer, transaction_date, sales, status,
                     revenue, cost, margin, profit, quotation, product, qty):
        transaction_id = conn.execute(
            """INSERT INTO sales_transactions(nomor_transaksi,customer_id,tanggal,jenis_penjualan,
                   referal,status,total_penjualan,total_modal,margin,laba_bersih,source_quotation_id)
               VALUES(?,? ,?,'Direct',?,?,?,?,?,?,?)""",
            (number, customer, transaction_date, sales, status, revenue, cost, margin, profit, quotation),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_transaction_items(transaction_id,product_id,nama_produk_snapshot,
                   qty,harga_jual_satuan,subtotal_penjualan,harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (transaction_id, product, number, qty, revenue // qty, revenue, cost // qty, cost, margin),
        )
        return transaction_id

    def dashboard(self, **filters):
        conn = database.get_connection()
        try:
            return build_executive_dashboard(conn, filters, today=self.TODAY)
        finally:
            conn.close()

    def test_dashboard_is_landing_page_and_alias_opens(self):
        for path in ("/", "/dashboard?period=custom&start_date=2026-08-01&end_date=2026-08-04"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Executive Dashboard", response.data)
            self.assertIn(b"Owner Alert", response.data)

    def test_kpi_revenue_margin_profit_and_receivable_are_correct(self):
        result = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04")
        self.assertEqual(result["financial"]["revenue"], 350000)
        self.assertEqual(result["financial"]["margin"], 150000)
        self.assertEqual(result["financial"]["net_profit"], 130000)
        self.assertEqual(result["kpis"]["outstanding_invoice"], 1)
        self.assertEqual(result["kpis"]["receivable"], 200000)
        self.assertEqual(result["kpis"]["stock_value"], 260000)

    def test_repeat_customer_and_customer_rank_are_correct(self):
        result = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04")
        self.assertEqual(result["customer"]["repeat"], 1)
        self.assertEqual(result["customer"]["top"][0]["nama"], "PT Repeat")
        self.assertEqual(result["customer"]["top"][0]["revenue"], 300000)

    def test_top_product_category_and_never_sold_are_correct(self):
        result = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04")
        self.assertEqual(result["product"]["best_seller"]["nama_produk"], "Ladder Pro")
        self.assertEqual(result["product"]["best_seller"]["quantity"], 3)
        self.assertEqual(result["product"]["categories"][0]["category"], "Ladder")
        self.assertEqual(result["product"]["never_sold"][0]["nama_produk"], "Ladder Unsold")

    def test_sales_funnel_counts_each_existing_stage(self):
        result = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04")
        funnel = {row["stage"]: row for row in result["funnel"]}
        self.assertEqual(funnel["Prospek"]["count"], 1)
        self.assertEqual(funnel["Quotation"]["count"], 2)
        self.assertEqual(funnel["Deal"]["count"], 1)
        self.assertEqual(funnel["Transaction"]["count"], 3)
        self.assertEqual(funnel["Invoice"]["count"], 2)
        self.assertEqual(funnel["Receipt"]["count"], 1)
        self.assertEqual(funnel["Delivery"]["count"], 1)
        self.assertEqual(funnel["Completed"]["count"], 1)

    def test_owner_alerts_are_aggregated_without_duplicates(self):
        result = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04")
        self.assertEqual(len(result["alerts"]["overdue_invoice"]), 1)
        self.assertEqual(len(result["alerts"]["low_stock"]), 1)
        self.assertEqual(len(result["alerts"]["late_po"]), 1)
        self.assertEqual(len(result["alerts"]["expired_quotation"]), 1)
        self.assertTrue(any(row["subject"] == "Customer Stale" for row in result["alerts"]["stale_customer"]))

    def test_sales_customer_product_category_and_custom_filters(self):
        alice = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04", sales="Alice")
        self.assertEqual(alice["financial"]["revenue"], 300000)
        customer = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04", customer_id=str(self.customer_new))
        self.assertEqual(customer["financial"]["revenue"], 50000)
        product = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04", product_id=str(self.product_bin))
        self.assertEqual(product["financial"]["revenue"], 50000)
        one_day = self.dashboard(period="custom", start_date="2026-08-03", end_date="2026-08-03")
        self.assertEqual(one_day["financial"]["revenue"], 50000)

    def test_period_filter_validation_is_safe(self):
        filters = resolve_dashboard_filters(
            {"period": "custom", "start_date": "2026-08-09", "end_date": "2026-08-01", "product_id": "not-a-number"},
            today=self.TODAY,
        )
        self.assertEqual(filters["start_date"], "2026-08-01")
        self.assertEqual(filters["end_date"], "2026-08-09")
        self.assertEqual(filters["product_id"], 0)

    def test_dashboard_reads_workflow_revision_and_cancellation_events(self):
        result = self.dashboard(period="custom", start_date="2026-08-01", end_date="2026-08-04")
        event_types = {row["event_type"] for row in result["recent_activity"]}
        self.assertIn("QUOTATION_REVISED", event_types)
        self.assertIn("TRANSACTION_CANCELLED", event_types)

    def test_dashboard_is_read_only_and_has_bounded_query_count(self):
        conn = database.get_connection()
        before = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("customers", "sales_transactions", "workflow_events", "stock_movements")
        }
        selects = []
        conn.set_trace_callback(lambda statement: selects.append(statement) if statement.lstrip().upper().startswith(("SELECT", "WITH")) else None)
        started = time.perf_counter()
        build_executive_dashboard(
            conn,
            {"period": "custom", "start_date": "2026-08-01", "end_date": "2026-08-04"},
            today=self.TODAY,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        conn.set_trace_callback(None)
        after = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        conn.close()
        self.assertEqual(before, after)
        self.assertLessEqual(len(selects), 60)
        self.assertLess(elapsed_ms, 500)

    def test_print_templates_remain_byte_identical(self):
        template_dir = PROJECT_ROOT / "app" / "templates"
        for name, expected in PRINT_HASHES.items():
            with self.subTest(template=name):
                self.assertEqual(hashlib.sha256((template_dir / name).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
