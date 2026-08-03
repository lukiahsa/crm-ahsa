import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import database
import main
from workflow_integrity import (
    WorkflowIntegrityError,
    post_opening_stock,
    post_stock_for_document,
    reconcile_invoice_payment,
    reverse_stock_for_document,
    sync_transaction_status,
)


class WorkflowIntegrityRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "workflow.db"
        database.create_tables()
        self.client = main.app.test_client()
        self.sequence = 0
        conn = database.get_connection()
        self.customer_id = conn.execute(
            "INSERT INTO customers (nama, status) VALUES ('Customer Workflow', 'Existing Customer')"
        ).lastrowid
        self.product_id = conn.execute(
            """
            INSERT INTO products (
                kode_produk, nama_produk, satuan,
                harga_jual_default, harga_modal_default
            ) VALUES ('WF-001', 'Produk Workflow', 'Unit', 100000, 60000)
            """
        ).lastrowid
        self.warehouse_id = conn.execute(
            "INSERT INTO warehouses (kode_gudang, nama_gudang) VALUES ('WH-QA', 'Gudang QA')"
        ).lastrowid
        conn.execute(
            """
            UPDATE erp_settings
            SET inventory_enabled = 1, default_warehouse_id = ?
            WHERE id = 1
            """,
            (self.warehouse_id,),
        )
        self.supplier_id = conn.execute(
            "INSERT INTO suppliers (nama_supplier, status) VALUES ('Supplier QA', 'Aktif')"
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_transaction(self, *, status="Draft", source_quotation_id=None):
        self.sequence += 1
        conn = database.get_connection()
        transaction_id = conn.execute(
            """
            INSERT INTO sales_transactions (
                nomor_transaksi, customer_id, tanggal, jenis_penjualan,
                referal, status, total_penjualan, total_modal, margin,
                laba_bersih, source_quotation_id
            ) VALUES (?, ?, '2026-08-03', 'Direct', 'QA', ?, 100000, 60000, 40000, 40000, ?)
            """,
            (f"TRX-{id(self)}-{self.sequence}", self.customer_id, status, source_quotation_id),
        ).lastrowid
        item_id = conn.execute(
            """
            INSERT INTO sales_transaction_items (
                transaction_id, product_id, kode_produk_snapshot,
                nama_produk_snapshot, satuan_snapshot, qty,
                harga_jual_satuan, subtotal_penjualan,
                harga_modal_satuan, subtotal_modal, margin_item
            ) VALUES (?, ?, 'WF-001', 'Produk Workflow', 'Unit', 1,
                      100000, 100000, 60000, 60000, 40000)
            """,
            (transaction_id, self.product_id),
        ).lastrowid
        conn.commit()
        conn.close()
        return transaction_id, item_id

    def make_invoice(self, transaction_id, *, status="Belum Lunas"):
        conn = database.get_connection()
        invoice_id = conn.execute(
            """
            INSERT INTO sales_invoices (
                transaction_id, nomor_invoice, tanggal_invoice, status_pembayaran
            ) VALUES (?, ?, '2026-08-03', ?)
            """,
            (transaction_id, f"INV-{transaction_id}", status),
        ).lastrowid
        conn.commit()
        conn.close()
        return invoice_id

    def make_receipt(self, transaction_id, invoice_id, nominal, *, key, status="Diterbitkan"):
        conn = database.get_connection()
        receipt_id = conn.execute(
            """
            INSERT INTO payment_receipts (
                nomor_kwitansi, invoice_id, transaction_id, tanggal,
                jenis_pembayaran, metode_pembayaran, nominal,
                untuk_pembayaran, status, idempotency_key
            ) VALUES (?, ?, ?, '2026-08-03', 'DP', 'Transfer Bank', ?,
                      'Pembayaran QA', ?, ?)
            """,
            (f"KWT-{key}", invoice_id, transaction_id, nominal, status, key),
        ).lastrowid
        conn.commit()
        conn.close()
        return receipt_id

    def make_delivery_order(self, transaction_id, item_id, *, status="Draft"):
        conn = database.get_connection()
        delivery_id = conn.execute(
            """
            INSERT INTO delivery_orders (
                nomor_surat_jalan, transaction_id, warehouse_id, tanggal, status
            ) VALUES (?, ?, ?, '2026-08-03', ?)
            """,
            (f"SJ-{transaction_id}", transaction_id, self.warehouse_id, status),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO delivery_order_items (
                delivery_order_id, transaction_item_id, product_id,
                nama_produk_snapshot, satuan_snapshot, qty
            ) VALUES (?, ?, ?, 'Produk Workflow', 'Unit', 1)
            """,
            (delivery_id, item_id, self.product_id),
        )
        conn.commit()
        conn.close()
        return delivery_id

    def make_purchase_order(self, *, invoice_id=None, transaction_id=None, status="Draft"):
        conn = database.get_connection()
        po_id = conn.execute(
            """
            INSERT INTO purchase_orders (
                nomor_po, supplier_id, invoice_id, transaction_id,
                warehouse_id, tanggal, status
            ) VALUES (?, ?, ?, ?, ?, '2026-08-03', ?)
            """,
            (
                f"PO-{id(self)}-{status}", self.supplier_id, invoice_id,
                transaction_id, self.warehouse_id, status,
            ),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO purchase_order_items (
                purchase_order_id, product_id, nama_produk_snapshot,
                satuan_snapshot, qty, harga_satuan, subtotal
            ) VALUES (?, ?, 'Produk Workflow', 'Unit', 1, 60000, 60000)
            """,
            (po_id, self.product_id),
        )
        conn.commit()
        conn.close()
        return po_id

    def stock_balance(self):
        conn = database.get_connection()
        row = conn.execute(
            "SELECT stok FROM product_stock WHERE warehouse_id = ? AND product_id = ?",
            (self.warehouse_id, self.product_id),
        ).fetchone()
        conn.close()
        return int(row["stok"] or 0) if row else 0

    def test_invoice_cannot_be_marked_paid_without_receipt(self):
        transaction_id, _ = self.make_transaction()
        self.make_invoice(transaction_id)
        response = self.client.post(
            f"/transactions/{transaction_id}/invoice/status",
            data={"status_pembayaran": "Lunas"},
        )
        self.assertEqual(response.status_code, 400)
        conn = database.get_connection()
        self.assertEqual(conn.execute(
            "SELECT status_pembayaran FROM sales_invoices WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()[0], "Belum Lunas")
        conn.close()

    def test_full_and_partial_receipts_reconcile_invoice(self):
        transaction_id, _ = self.make_transaction()
        invoice_id = self.make_invoice(transaction_id)
        self.make_receipt(transaction_id, invoice_id, 40000, key="PARTIAL")
        conn = database.get_connection()
        partial = reconcile_invoice_payment(conn, invoice_id)
        self.assertEqual((partial["total_dibayar"], partial["sisa_tagihan"], partial["status_pembayaran"]), (40000, 60000, "DP"))
        conn.commit(); conn.close()
        self.make_receipt(transaction_id, invoice_id, 60000, key="FULL")
        conn = database.get_connection()
        full = reconcile_invoice_payment(conn, invoice_id)
        self.assertEqual((full["total_dibayar"], full["status_pembayaran"]), (100000, "Lunas"))
        conn.commit(); conn.close()

    def test_void_full_receipt_recalculates_invoice_and_transaction(self):
        transaction_id, _ = self.make_transaction()
        invoice_id = self.make_invoice(transaction_id)
        receipt_id = self.make_receipt(transaction_id, invoice_id, 100000, key="VOID")
        conn = database.get_connection(); reconcile_invoice_payment(conn, invoice_id); conn.commit(); conn.close()
        response = self.client.post(f"/receipts/{receipt_id}/status", data={"status": "Void"})
        self.assertEqual(response.status_code, 302)
        conn = database.get_connection()
        invoice = conn.execute("SELECT status_pembayaran, jumlah_dibayar FROM sales_invoices WHERE id = ?", (invoice_id,)).fetchone()
        transaction = conn.execute("SELECT status FROM sales_transactions WHERE id = ?", (transaction_id,)).fetchone()
        self.assertEqual((invoice["status_pembayaran"], invoice["jumlah_dibayar"]), ("Belum Lunas", 0))
        self.assertEqual(transaction["status"], "Invoice")
        conn.close()

    def test_duplicate_receipt_request_is_idempotent(self):
        transaction_id, _ = self.make_transaction()
        invoice_id = self.make_invoice(transaction_id)
        payload = {
            "tanggal": "2026-08-03", "jenis_pembayaran": "Pembayaran Invoice",
            "metode_pembayaran": "Transfer Bank", "nominal": "100000",
            "untuk_pembayaran": "Pelunasan", "idempotency_key": "retry-1",
        }
        self.assertEqual(self.client.post(f"/invoices/{invoice_id}/receipts/add", data=payload).status_code, 302)
        self.assertEqual(self.client.post(f"/invoices/{invoice_id}/receipts/add", data=payload).status_code, 302)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM payment_receipts WHERE invoice_id = ?", (invoice_id,)).fetchone()[0], 1)
        conn.close()

    def test_cancelled_invoice_rejects_receipt_and_purchase_order(self):
        transaction_id, _ = self.make_transaction()
        invoice_id = self.make_invoice(transaction_id, status="Batal")
        receipt_response = self.client.post(f"/invoices/{invoice_id}/receipts/add", data={})
        po_response = self.client.get(f"/transactions/{transaction_id}/invoice/purchase-order/generate")
        self.assertEqual(receipt_response.status_code, 400)
        self.assertEqual(po_response.status_code, 400)

    def test_cancelled_expired_and_denko_quotations_reject_conversion(self):
        conn = database.get_connection()
        denko_id = conn.execute("SELECT id FROM company_identities WHERE identity_type = 'QUOTATION_ONLY'").fetchone()[0]
        for index, (status, identity_id) in enumerate((("Batal", None), ("Expired", None), ("Draft", denko_id)), start=1):
            conn.execute(
                """
                INSERT INTO sales_quotations (
                    nomor_penawaran, customer_id, tanggal, status, identity_id
                ) VALUES (?, ?, '2026-08-03', ?, ?)
                """,
                (f"QT-BLOCK-{index}", self.customer_id, status, identity_id),
            )
        conn.commit(); ids = [r[0] for r in conn.execute("SELECT id FROM sales_quotations ORDER BY id DESC LIMIT 3")]; conn.close()
        for quotation_id in ids:
            self.assertEqual(self.client.post(f"/quotations/{quotation_id}/convert").status_code, 400)

    def test_cancelled_transaction_rejects_invoice_delivery_and_purchase(self):
        transaction_id, _ = self.make_transaction(status="Batal")
        self.make_invoice(transaction_id)
        self.assertEqual(self.client.post(f"/transactions/{transaction_id}/invoice/generate").status_code, 400)
        self.assertEqual(self.client.post(f"/transactions/{transaction_id}/delivery-order/generate").status_code, 400)
        self.assertEqual(self.client.get(f"/transactions/{transaction_id}/invoice/purchase-order/generate").status_code, 400)

    def test_converted_quotation_and_downstream_transaction_are_financially_locked(self):
        transaction_id, _ = self.make_transaction()
        conn = database.get_connection()
        quotation_id = conn.execute(
            """
            INSERT INTO sales_quotations (
                nomor_penawaran, customer_id, tanggal, status, converted_transaction_id
            ) VALUES ('QT-LOCK', ?, '2026-08-03', 'Deal', ?)
            """, (self.customer_id, transaction_id),
        ).lastrowid
        conn.execute("UPDATE sales_transactions SET source_quotation_id = ? WHERE id = ?", (quotation_id, transaction_id))
        conn.commit(); conn.close()
        self.assertEqual(self.client.post(f"/quotations/{quotation_id}/edit", data={}).status_code, 400)
        self.make_invoice(transaction_id)
        self.assertEqual(self.client.post(f"/transactions/{transaction_id}/edit", data={}).status_code, 400)

    def test_payment_delivery_order_is_deterministic_in_both_orders(self):
        for paid_first in (True, False):
            transaction_id, item_id = self.make_transaction()
            invoice_id = self.make_invoice(transaction_id)
            delivery_id = self.make_delivery_order(transaction_id, item_id, status="Diterima")
            self.make_receipt(transaction_id, invoice_id, 100000, key=f"ORDER-{paid_first}")
            conn = database.get_connection()
            if paid_first:
                reconcile_invoice_payment(conn, invoice_id)
                sync_transaction_status(conn, transaction_id, reason="delivery after paid")
            else:
                sync_transaction_status(conn, transaction_id, reason="delivered before paid")
                reconcile_invoice_payment(conn, invoice_id)
            conn.commit()
            self.assertEqual(conn.execute("SELECT status FROM sales_transactions WHERE id = ?", (transaction_id,)).fetchone()[0], "Selesai")
            conn.close()

    def test_po_received_posts_one_in_and_retry_is_noop(self):
        po_id = self.make_purchase_order()
        first = self.client.post(f"/purchase-orders/{po_id}/status", data={"status": "Barang Diterima"})
        second = self.client.post(f"/purchase-orders/{po_id}/status", data={"status": "Barang Diterima"})
        self.assertEqual((first.status_code, second.status_code, self.stock_balance()), (302, 302, 1))
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source_type='PURCHASE_ORDER' AND source_id=? AND movement_type='IN'", (po_id,)).fetchone()[0], 1)
        conn.close()

    def test_delivery_sent_posts_one_out_and_received_posts_no_second_out(self):
        conn = database.get_connection()
        post_opening_stock(conn, warehouse_id=self.warehouse_id, product_id=self.product_id, quantity=2, minimum_stock=0, tanggal="2026-08-03", catatan="QA", idempotency_key="opening-do")
        conn.commit(); conn.close()
        transaction_id, item_id = self.make_transaction()
        delivery_id = self.make_delivery_order(transaction_id, item_id)
        self.assertEqual(self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Terkirim"}).status_code, 302)
        self.assertEqual(self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Terkirim"}).status_code, 302)
        self.assertEqual(self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Diterima"}).status_code, 302)
        self.assertEqual(self.stock_balance(), 1)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source_type='DELIVERY_ORDER' AND source_id=? AND movement_type='OUT'", (delivery_id,)).fetchone()[0], 1)
        conn.close()

    def test_insufficient_stock_rolls_back_status_and_movements(self):
        transaction_id, item_id = self.make_transaction()
        delivery_id = self.make_delivery_order(transaction_id, item_id)
        response = self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Terkirim"})
        self.assertEqual(response.status_code, 400)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT status FROM delivery_orders WHERE id = ?", (delivery_id,)).fetchone()[0], "Draft")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source_type='DELIVERY_ORDER' AND source_id=?", (delivery_id,)).fetchone()[0], 0)
        conn.close()

    def test_failure_injection_rolls_back_transition(self):
        po_id = self.make_purchase_order()
        with patch(
            "main.post_stock_for_document",
            side_effect=WorkflowIntegrityError("Injected stock failure"),
        ):
            response = self.client.post(
                f"/purchase-orders/{po_id}/status",
                data={"status": "Barang Diterima"},
            )
        self.assertEqual(response.status_code, 400)
        conn = database.get_connection()
        self.assertEqual(
            conn.execute(
                "SELECT status FROM purchase_orders WHERE id = ?",
                (po_id,),
            ).fetchone()[0],
            "Draft",
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE source_type='PURCHASE_ORDER' AND source_id=?",
                (po_id,),
            ).fetchone()[0],
            0,
        )
        conn.close()

    def test_po_and_delivery_cancellation_create_one_reversal(self):
        po_id = self.make_purchase_order()
        self.client.post(f"/purchase-orders/{po_id}/status", data={"status": "Barang Diterima"})
        self.assertEqual(self.client.post(f"/purchase-orders/{po_id}/status", data={"status": "Batal"}).status_code, 302)
        self.assertEqual(self.client.post(f"/purchase-orders/{po_id}/status", data={"status": "Batal"}).status_code, 302)
        self.assertEqual(self.stock_balance(), 0)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source_type='PURCHASE_ORDER' AND source_id=? AND movement_type='REVERSAL'", (po_id,)).fetchone()[0], 1)
        conn.close()

        conn = database.get_connection()
        post_opening_stock(conn, warehouse_id=self.warehouse_id, product_id=self.product_id, quantity=1, minimum_stock=0, tanggal="2026-08-03", catatan="QA", idempotency_key="opening-reversal")
        conn.commit(); conn.close()
        transaction_id, item_id = self.make_transaction()
        delivery_id = self.make_delivery_order(transaction_id, item_id)
        self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Terkirim"})
        self.assertEqual(self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Batal"}).status_code, 302)
        self.assertEqual(self.client.post(f"/delivery-orders/{delivery_id}/status", data={"status": "Batal"}).status_code, 302)
        conn = database.get_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements WHERE source_type='DELIVERY_ORDER' AND source_id=? AND movement_type='REVERSAL'", (delivery_id,)).fetchone()[0], 1)
        conn.close()

    def test_opening_stock_duplicate_request_does_not_double(self):
        conn = database.get_connection()
        args = dict(warehouse_id=self.warehouse_id, product_id=self.product_id, quantity=5, minimum_stock=1, tanggal="2026-08-03", catatan="QA", idempotency_key="same-opening")
        self.assertTrue(post_opening_stock(conn, **args))
        self.assertFalse(post_opening_stock(conn, **args))
        conn.commit(); conn.close()
        self.assertEqual(self.stock_balance(), 5)

    def test_database_constraints_prevent_duplicate_conversion_po_and_keys(self):
        conn = database.get_connection()
        quotation_id = conn.execute("INSERT INTO sales_quotations (nomor_penawaran, tanggal) VALUES ('QT-UNIQUE', '2026-08-03')").lastrowid
        conn.commit()
        conn.close()
        self.make_transaction(source_quotation_id=quotation_id)
        conn = database.get_connection()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO sales_transactions (tanggal, jenis_penjualan, source_quotation_id) VALUES ('2026-08-03', 'Direct', ?)", (quotation_id,))
        conn.rollback(); conn.close()

        transaction_id, _ = self.make_transaction()
        invoice_id = self.make_invoice(transaction_id)
        self.make_purchase_order(invoice_id=invoice_id, transaction_id=transaction_id)
        conn = database.get_connection()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO purchase_orders (nomor_po, supplier_id, invoice_id, tanggal) VALUES ('PO-DUP', ?, ?, '2026-08-03')", (self.supplier_id, invoice_id))
        conn.close()

    def test_financial_header_detail_invariants_remain_exact(self):
        transaction_id, _ = self.make_transaction()
        conn = database.get_connection()
        header = conn.execute("SELECT total_penjualan, total_modal, margin FROM sales_transactions WHERE id = ?", (transaction_id,)).fetchone()
        detail = conn.execute("SELECT SUM(subtotal_penjualan), SUM(subtotal_modal), SUM(margin_item) FROM sales_transaction_items WHERE transaction_id = ?", (transaction_id,)).fetchone()
        self.assertEqual(tuple(header), tuple(detail))
        conn.close()

    def test_migration_is_idempotent_and_integrity_is_ok(self):
        database.create_tables()
        database.create_tables()
        conn = database.get_connection()
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        for table, index_name in (
            ("sales_transactions", "uq_sales_transactions_source_quotation"),
            ("purchase_orders", "uq_purchase_orders_invoice"),
            ("payment_receipts", "uq_payment_receipts_idempotency"),
            ("stock_movements", "uq_stock_movements_idempotency"),
        ):
            self.assertIn(index_name, [row["name"] for row in conn.execute(f"PRAGMA index_list({table})")])
        conn.close()


if __name__ == "__main__":
    unittest.main()
