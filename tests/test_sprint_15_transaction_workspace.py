import io
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import database

BOOTSTRAP = tempfile.TemporaryDirectory()
database.DATABASE = Path(BOOTSTRAP.name) / "bootstrap.db"
import main
from module_manager import update_optional_modules
from transaction_workspace import get_transaction_workspace
from workflow_integrity import record_workflow_event


class TransactionWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        BOOTSTRAP.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "workspace.db"
        database.create_tables()
        self.client = main.app.test_client()
        conn = database.get_connection()
        update_optional_modules(
            conn,
            {
                "quotation", "invoice", "receipt", "delivery_order",
                "purchase_order", "warehouse", "accounting", "purchasing",
            },
            actor="QA Sprint 15",
        )
        category_id = conn.execute(
            "INSERT INTO product_categories(nama,status_aktif) VALUES('Workspace',1)"
        ).lastrowid
        self.product_id = conn.execute(
            """INSERT INTO products(
                   kode_produk,nama_produk,category_id,satuan,
                   harga_jual_default,harga_modal_default,status_aktif)
               VALUES('WS-120','Tempat Sampah 120 Liter',?,'Unit',100000,60000,1)""",
            (category_id,),
        ).lastrowid
        self.customer_id = conn.execute(
            """INSERT INTO customers(
                   nama,instansi,whatsapp,whatsapp_normalized,pic,kota,sumber,
                   status,status_aktif,created_at)
               VALUES('Customer Workspace','PT Workspace','081234567890',
                      '6281234567890','Bapak Andi','Bandung','Referensi Owner',
                      'Existing Customer',1,'2026-08-01')"""
        ).lastrowid
        self.supplier_id = conn.execute(
            """INSERT INTO suppliers(
                   kode_supplier,nama_supplier,status,status_aktif,payment_term)
               VALUES('SUP-WS','Supplier Workspace','Aktif',1,7)"""
        ).lastrowid
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_transaction(self, *, number="TRX/2026/08/015", status="Closing"):
        conn = database.get_connection()
        transaction_id = conn.execute(
            """INSERT INTO sales_transactions(
                   nomor_transaksi,customer_id,tanggal,jenis_penjualan,referal,status,
                   total_penjualan,potongan,jumlah_diterima,total_modal,margin,
                   biaya_lain,laba_bersih,catatan)
               VALUES(?,?,'2026-08-05','Direct','Sales Luki',?,
                      100000,0,100000,60000,40000,5000,35000,'Catatan transaksi')""",
            (number, self.customer_id, status),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_transaction_items(
                   transaction_id,product_id,kode_produk_snapshot,nama_produk_snapshot,
                   kategori_snapshot,satuan_snapshot,qty,harga_jual_satuan,
                   subtotal_penjualan,harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,'WS-120','Tempat Sampah 120 Liter','Workspace','Unit',
                      1,100000,100000,60000,60000,40000)""",
            (transaction_id, self.product_id),
        )
        conn.commit()
        conn.close()
        return transaction_id

    def test_workspace_render_contains_all_required_sections_and_header(self):
        transaction_id = self.make_transaction()
        response = self.client.get(f"/transactions/{transaction_id}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for text in (
            "Transaction Workspace", "TRX/2026/08/015", "Sales Luki",
            "Customer Workspace", "Bapak Andi", "081234567890",
            "Referensi Owner", "Payment Summary", "Financial Summary",
            "Document Status", "Activity Timeline", "Document History",
            "Notes", "Attachment", "Customer Snapshot",
        ):
            self.assertIn(text, html)

    def test_payment_summary_combines_dp_and_settlement_without_engine_write(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        for number, kind, nominal in (
            ("KWT/2026/08/000001", "DP", 30000),
            ("KWT/2026/08/000002", "Pelunasan", 70000),
        ):
            conn.execute(
                """INSERT INTO transaction_receipts(
                       nomor_kwitansi,transaction_id,tanggal,jenis_pembayaran,
                       metode_pembayaran,nominal,untuk_pembayaran,status)
                   VALUES(?,?,'2026-08-05',?,'Transfer Bank',?,'Pembayaran','Diterbitkan')""",
                (number, transaction_id, kind, nominal),
            )
        before = dict(conn.execute(
            "SELECT * FROM sales_transactions WHERE id=?", (transaction_id,)
        ).fetchone())
        workspace = get_transaction_workspace(conn, transaction_id, {})
        after = dict(conn.execute(
            "SELECT * FROM sales_transactions WHERE id=?", (transaction_id,)
        ).fetchone())
        conn.close()
        self.assertEqual(workspace["payment"], {
            "grand_total": 100000,
            "dp": 30000,
            "pelunasan": 70000,
            "paid_total": 100000,
            "outstanding": 0,
            "status": "Lunas",
        })
        self.assertEqual(before, after)

    def test_financial_summary_reads_frozen_transaction_snapshot(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        workspace = get_transaction_workspace(conn, transaction_id, {})
        conn.close()
        self.assertEqual(workspace["financial"]["modal"], 60000)
        self.assertEqual(workspace["financial"]["subtotal"], 100000)
        self.assertEqual(workspace["financial"]["margin"], 40000)
        self.assertEqual(workspace["financial"]["profit"], 35000)
        self.assertEqual(workspace["financial"]["margin_percent"], 40.0)

    def test_timeline_combines_creation_status_revision_and_workspace_events(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=self.customer_id,
            event_type="status_changed",
            old_status="Draft",
            new_status="Closing",
            description="Status Transaction berubah dari Draft menjadi Closing.",
            created_by="QA",
        )
        conn.execute(
            """INSERT INTO transaction_workspace_notes(transaction_id,note_text,created_by)
               VALUES(?, 'Hubungi customer besok.', 'QA')""",
            (transaction_id,),
        )
        conn.commit()
        workspace = get_transaction_workspace(conn, transaction_id, {})
        conn.close()
        event_types = [event["event_type"] for event in workspace["timeline"]]
        self.assertIn("Transaction dibuat", event_types)
        self.assertIn("Status berubah", event_types)
        self.assertIn("Catatan internal", event_types)
        self.assertTrue(any("Draft menjadi Closing" in event["description"] for event in workspace["timeline"]))

    def test_document_history_and_status_after_full_transaction_first_uat(self):
        transaction_id = self.make_transaction()
        self.assertEqual(
            self.client.post(f"/transactions/{transaction_id}/invoice/generate").status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/transactions/{transaction_id}/receipt/generate",
                data={
                    "tanggal": "2026-08-05", "jenis_pembayaran": "DP",
                    "metode_pembayaran": "Transfer Bank", "nominal": "30000",
                    "created_by": "QA",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/transactions/{transaction_id}/delivery-order/generate"
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/transactions/{transaction_id}/purchase-order/generate",
                data={"supplier_id": self.supplier_id, "ppn_persen": "0"},
            ).status_code,
            302,
        )
        response = self.client.get(f"/transactions/{transaction_id}")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for number_prefix in ("INV/", "KWT/", "SJ/", "PO/"):
            self.assertIn(number_prefix, html)
        for label in ("Invoice dibuat", "Receipt dibuat", "DO dibuat", "PO dibuat"):
            self.assertIn(label, html)
        conn = database.get_connection()
        workspace = get_transaction_workspace(conn, transaction_id, {})
        conn.close()
        statuses = {row["module_key"]: row for row in workspace["document_statuses"]}
        for module_key in ("invoice", "receipt", "delivery_order", "purchase_order"):
            self.assertTrue(statuses[module_key]["created"])
            self.assertEqual(statuses[module_key]["count"], 1)
        self.assertFalse(statuses["quotation"]["created"])
        self.assertEqual(len(workspace["document_history"]), 4)

    def test_module_manager_hides_every_optional_workspace_action(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        update_optional_modules(conn, [], actor="QA")
        conn.commit()
        conn.close()
        html = self.client.get(f"/transactions/{transaction_id}").get_data(as_text=True)
        for label in (
            "Generate Invoice", "Generate Receipt", "Generate Delivery Order",
            "Generate Purchase Order",
        ):
            self.assertNotIn(label, html)
        for module_key in (
            "quotation", "invoice", "receipt", "delivery_order", "purchase_order",
        ):
            self.assertEqual(
                self.client.get(
                    f"/transactions/{transaction_id}/receipt/generate"
                    if module_key == "receipt"
                    else "/invoices" if module_key == "invoice"
                    else "/quotations" if module_key == "quotation"
                    else "/delivery-orders" if module_key == "delivery_order"
                    else "/purchase-orders"
                ).status_code,
                404,
            )

    def test_customer_snapshot_uses_customer360_service_data(self):
        transaction_id = self.make_transaction()
        conn = database.get_connection()
        conn.execute(
            """INSERT INTO customer_purchase_history(
                   customer_id,product_id,tanggal_pembelian,nama_produk_snapshot,
                   kategori_snapshot,qty,harga_satuan,total,active)
               VALUES(?,?,'2026-08-06','Tempat Sampah 120 Liter','Workspace',
                      2,50000,100000,1)""",
            (self.customer_id, self.product_id),
        )
        conn.commit()
        snapshot = get_transaction_workspace(conn, transaction_id, {})["customer_snapshot"]
        conn.close()
        self.assertTrue(snapshot["is_repeat_customer"])
        self.assertEqual(snapshot["historical_purchase_count"], 1)
        self.assertEqual(snapshot["total_revenue"], 200000)
        self.assertEqual(snapshot["last_order"], "2026-08-06")
        self.assertEqual(snapshot["top_product"], "Tempat Sampah 120 Liter")

    def test_internal_note_and_attachment_are_isolated_from_print(self):
        transaction_id = self.make_transaction()
        note_text = "INTERNAL-ONLY-SPRINT-15"
        response = self.client.post(
            f"/transactions/{transaction_id}/workspace-notes",
            data={"note_text": note_text, "created_by": "QA"},
        )
        self.assertEqual(response.status_code, 302)
        payload = b"%PDF-1.4\nworkspace-test"
        response = self.client.post(
            f"/transactions/{transaction_id}/attachments",
            data={
                "attachment_type": "Bukti Transfer",
                "uploaded_by": "QA",
                "attachment": (io.BytesIO(payload), "bukti-transfer.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        workspace_html = self.client.get(
            f"/transactions/{transaction_id}"
        ).get_data(as_text=True)
        self.assertIn(note_text, workspace_html)
        self.assertIn("bukti-transfer.pdf", workspace_html)
        print_html = self.client.get(
            f"/transactions/{transaction_id}/print"
        ).get_data(as_text=True)
        self.assertNotIn(note_text, print_html)
        self.assertNotIn("bukti-transfer.pdf", print_html)
        conn = database.get_connection()
        attachment_id = conn.execute(
            "SELECT id FROM transaction_attachments WHERE transaction_id=?",
            (transaction_id,),
        ).fetchone()[0]
        conn.close()
        download = self.client.get(
            f"/transactions/{transaction_id}/attachments/{attachment_id}"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, payload)

    def test_attachment_validation_and_additive_migration_integrity(self):
        transaction_id = self.make_transaction()
        rejected = self.client.post(
            f"/transactions/{transaction_id}/attachments",
            data={
                "attachment_type": "Dokumen Lain",
                "attachment": (io.BytesIO(b"unsafe"), "script.exe"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(rejected.status_code, 400)
        database.create_tables()
        database.create_tables()
        conn = database.get_connection()
        for table in ("transaction_workspace_notes", "transaction_attachments"):
            self.assertIsNotNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone())
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()


if __name__ == "__main__":
    unittest.main()
