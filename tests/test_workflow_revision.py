import hashlib
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
from customer_360 import get_customer_360
from workflow_revision import can_unlock_quotation
from workflow_integrity import sync_transaction_status


PRINT_HASHES = {
    "quotation_print.html": "dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622",
    "invoice_print.html": "7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8",
    "receipt_print.html": "4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4",
    "delivery_order_print.html": "ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5",
}


class WorkflowRevisionRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    @classmethod
    def tearDownClass(cls):
        BOOTSTRAP.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "revision.db"
        database.create_tables()
        self.client = main.app.test_client()
        conn = database.get_connection()
        self.customer_id = conn.execute(
            "INSERT INTO customers(nama,whatsapp,status,status_aktif) VALUES('Customer Revision','0812','Existing Customer',1)"
        ).lastrowid
        self.product_id = conn.execute(
            "INSERT INTO products(kode_produk,nama_produk,satuan,harga_jual_default,harga_modal_default,status_aktif) VALUES('REV-1','Produk Revision','Unit',100000,60000,1)"
        ).lastrowid
        self.identity_id = conn.execute(
            "SELECT id FROM company_identities WHERE identity_type='FULL' ORDER BY is_default DESC,id LIMIT 1"
        ).fetchone()[0]
        self.quotation_id = conn.execute(
            """INSERT INTO sales_quotations(
                 nomor_penawaran,customer_id,tanggal,sales,status,revisi,subtotal,dpp,
                 grand_total,identity_id,customer_nama_snapshot)
               VALUES('QT-REV-001',?,'2026-08-03','QA','Deal',0,100000,100000,100000,?,'Customer Revision')""",
            (self.customer_id, self.identity_id),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_quotation_items(
                 quotation_id,product_id,kode_produk_snapshot,nama_produk_snapshot,
                 satuan_snapshot,qty,harga_satuan,subtotal,harga_modal_snapshot)
               VALUES(?,?,'REV-1','Produk Revision','Unit',1,100000,100000,60000)""",
            (self.quotation_id, self.product_id),
        )
        self.transaction_id = conn.execute(
            """INSERT INTO sales_transactions(
                 nomor_transaksi,customer_id,tanggal,jenis_penjualan,referal,status,
                 total_penjualan,total_modal,margin,laba_bersih,source_quotation_id)
               VALUES('TRX-REV-001',?,'2026-08-03','Direct','QA','Closing',100000,60000,40000,40000,?)""",
            (self.customer_id, self.quotation_id),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_transaction_items(
                 transaction_id,product_id,kode_produk_snapshot,nama_produk_snapshot,
                 satuan_snapshot,qty,harga_jual_satuan,subtotal_penjualan,
                 harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,'REV-1','Produk Revision','Unit',1,100000,100000,60000,60000,40000)""",
            (self.transaction_id, self.product_id),
        )
        conn.execute(
            "UPDATE sales_quotations SET converted_transaction_id=? WHERE id=?",
            (self.transaction_id, self.quotation_id),
        )
        conn.commit(); conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def cancel(self, reason="Customer meminta revisi spesifikasi"):
        return self.client.post(
            f"/transactions/{self.transaction_id}/cancel",
            data={"reason": reason, "created_by": "QA"},
        )

    def unlock(self):
        return self.client.post(
            f"/transactions/{self.transaction_id}/unlock-quotation",
            data={"created_by": "QA"},
        )

    def add_invoice(self, status="Belum Lunas"):
        conn=database.get_connection()
        invoice_id=conn.execute(
            "INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice,status_pembayaran) VALUES(?,'INV-REV','2026-08-03',?)",
            (self.transaction_id,status),
        ).lastrowid
        conn.commit(); conn.close(); return invoice_id

    def revision_payload(self):
        return {
            "identity_id": str(self.identity_id),
            "customer_id": str(self.customer_id),
            "tanggal": "2026-08-04",
            "berlaku_sampai": "2026-08-18",
            "sales": "QA",
            "diskon": "0",
            "catatan": "Revision tersimpan",
            "syarat_ketentuan": "Tetap",
            "product_id[]": [str(self.product_id)],
            "qty[]": ["2"],
            "harga_satuan[]": ["100000"],
            "diskon_item[]": ["0"],
        }

    def test_cancel_transaction_succeeds_without_deleting_it(self):
        self.assertEqual(self.cancel().status_code,302)
        conn=database.get_connection(); row=conn.execute("SELECT status FROM sales_transactions WHERE id=?",(self.transaction_id,)).fetchone(); count=conn.execute("SELECT COUNT(*) FROM sales_transactions WHERE id=?",(self.transaction_id,)).fetchone()[0]; conn.close()
        self.assertEqual((row["status"],count),("Cancelled",1))

    def test_cancel_requires_reason(self):
        self.assertEqual(self.cancel("").status_code,400)
        conn=database.get_connection(); self.assertEqual(conn.execute("SELECT status FROM sales_transactions WHERE id=?",(self.transaction_id,)).fetchone()[0],"Closing"); conn.close()

    def test_cancel_fails_when_invoice_exists(self):
        self.add_invoice(); response=self.cancel()
        self.assertEqual(response.status_code,400); self.assertIn("Invoice",response.get_data(as_text=True))

    def test_unlock_succeeds_after_cancel(self):
        self.cancel(); self.assertEqual(self.unlock().status_code,302)
        conn=database.get_connection(); q=conn.execute("SELECT status,converted_transaction_id,revisi FROM sales_quotations WHERE id=?",(self.quotation_id,)).fetchone(); t=conn.execute("SELECT source_quotation_id FROM sales_transactions WHERE id=?",(self.transaction_id,)).fetchone(); conn.close()
        self.assertEqual((q["status"],q["converted_transaction_id"],q["revisi"],t["source_quotation_id"]),("Revision Allowed",None,1,None))

    def test_unlock_fails_before_cancel(self):
        self.assertEqual(self.unlock().status_code,400)
        conn=database.get_connection(); result=can_unlock_quotation(conn,self.transaction_id); conn.close(); self.assertFalse(result["allowed"])

    def test_unlock_fails_with_active_downstream(self):
        self.add_invoice(); conn=database.get_connection(); conn.execute("UPDATE sales_transactions SET status='Cancelled' WHERE id=?",(self.transaction_id,)); conn.commit(); conn.close()
        response=self.unlock(); self.assertEqual(response.status_code,400); self.assertIn("Invoice",response.get_data(as_text=True))

    def test_unlocked_quotation_becomes_editable(self):
        self.cancel(); self.unlock()
        self.assertEqual(self.client.get(f"/quotations/{self.quotation_id}/edit").status_code,200)
        response=self.client.post(f"/quotations/{self.quotation_id}/edit",data=self.revision_payload())
        self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        conn=database.get_connection(); q=conn.execute("SELECT status,revisi,nomor_penawaran FROM sales_quotations WHERE id=?",(self.quotation_id,)).fetchone(); conn.close()
        self.assertEqual((q["status"],q["revisi"],q["nomor_penawaran"]),("Revisi",1,"QT-REV-001"))

    def test_revision_history_links_old_and_new_transaction(self):
        self.cancel(); self.unlock(); self.client.post(f"/quotations/{self.quotation_id}/edit",data=self.revision_payload())
        response=self.client.post(f"/quotations/{self.quotation_id}/convert"); self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        conn=database.get_connection(); revision=conn.execute("SELECT * FROM quotation_revisions WHERE quotation_id=?",(self.quotation_id,)).fetchone(); q=conn.execute("SELECT nomor_penawaran FROM sales_quotations WHERE id=?",(self.quotation_id,)).fetchone(); conn.close()
        self.assertEqual(revision["old_transaction_id"],self.transaction_id); self.assertIsNotNone(revision["new_transaction_id"]); self.assertEqual(q["nomor_penawaran"],"QT-REV-001")

    def test_all_revision_workflow_events_are_recorded(self):
        self.cancel(); self.unlock(); self.client.post(f"/quotations/{self.quotation_id}/edit",data=self.revision_payload())
        conn=database.get_connection(); events={r[0] for r in conn.execute("SELECT event_type FROM workflow_events")}; conn.close()
        self.assertTrue({"TRANSACTION_CANCELLED","QUOTATION_UNLOCKED","QUOTATION_REVISED"}.issubset(events))

    def test_customer_360_timeline_reads_new_events(self):
        self.cancel(); self.unlock(); conn=database.get_connection(); detail=get_customer_360(conn,self.customer_id); conn.close()
        event_types={event["event_type"] for event in detail["timeline"]}; self.assertIn("TRANSACTION_CANCELLED",event_types); self.assertIn("QUOTATION_UNLOCKED",event_types)

    def test_financial_invariant_remains_valid_after_revision_conversion(self):
        self.cancel(); self.unlock(); self.client.post(f"/quotations/{self.quotation_id}/edit",data=self.revision_payload()); self.client.post(f"/quotations/{self.quotation_id}/convert")
        conn=database.get_connection(); new_id=conn.execute("SELECT converted_transaction_id FROM sales_quotations WHERE id=?",(self.quotation_id,)).fetchone()[0]; row=conn.execute("SELECT t.total_penjualan,t.total_modal,t.margin,SUM(i.subtotal_penjualan) detail_sales,SUM(i.subtotal_modal) detail_cost,SUM(i.margin_item) detail_margin FROM sales_transactions t JOIN sales_transaction_items i ON i.transaction_id=t.id WHERE t.id=?",(new_id,)).fetchone(); conn.close()
        self.assertEqual((row["total_penjualan"],row["total_modal"],row["margin"]),(row["detail_sales"],row["detail_cost"],row["detail_margin"]))

    def test_cancelled_transaction_rejects_downstream_generation(self):
        self.cancel()
        self.assertEqual(self.client.post(f"/transactions/{self.transaction_id}/invoice/generate").status_code,400)
        self.assertEqual(self.client.post(f"/transactions/{self.transaction_id}/delivery-order/generate").status_code,400)
        self.assertEqual(self.client.get(f"/transactions/{self.transaction_id}/invoice/purchase-order/generate").status_code,400)

    def test_cancelled_status_is_terminal_during_status_sync(self):
        self.cancel(); conn=database.get_connection(); status=sync_transaction_status(conn,self.transaction_id,reason="QA terminal check"); conn.commit(); transaction=conn.execute("SELECT status FROM sales_transactions WHERE id=?",(self.transaction_id,)).fetchone()[0]; conn.close()
        self.assertEqual((status,transaction),("Cancelled","Cancelled"))

    def test_transaction_and_quotation_ui_show_revision_controls(self):
        transaction_page=self.client.get(f"/transactions/{self.transaction_id}").get_data(as_text=True); quotation_page=self.client.get(f"/quotations/{self.quotation_id}").get_data(as_text=True)
        self.assertIn("Cancel Transaction",transaction_page); self.assertIn("Workflow Revision",quotation_page); self.assertIn("Unlock Quotation",quotation_page)

    def test_migration_is_idempotent_and_database_is_valid(self):
        database.create_tables(); database.create_tables(); conn=database.get_connection(); self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok"); self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),[]); indexes={r[1] for r in conn.execute("PRAGMA index_list(quotation_revisions)")}; conn.close(); self.assertIn("idx_quotation_revisions_quotation",indexes)

    def test_document_print_templates_are_unchanged(self):
        directory=PROJECT_ROOT/"app"/"templates"
        for name,expected in PRINT_HASHES.items(): self.assertEqual(hashlib.sha256((directory/name).read_bytes()).hexdigest(),expected)


if __name__ == "__main__":
    unittest.main()
