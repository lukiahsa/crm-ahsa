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
import main
from customer_360 import get_customer_360


PRINT_HASHES = {
    "quotation_print.html": "aa1e06fadd7c92eda999ce6d203aba92341ca4df222e7fdcaf214d4fff927fd4",
    "invoice_print.html": "4a79d98c15052b2229d52095050664a8b0b2709ae810e5e7b34b0fc74f6637e2",
    "receipt_print.html": "7f77c0bbb71c24f14720fff3a42eaa66357be4fcdd3078e2478cb7c4556b21cc",
    "delivery_order_print.html": "fcf647739fce72ac0a40166510502b4889922d13e966a433b55dbf92105b684a",
}


class Customer360RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.app.config.update(TESTING=True, SERVER_NAME="localhost")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(self.temp_dir.name) / "customer360.db"
        database.create_tables()
        self.client = main.app.test_client()
        conn = database.get_connection()
        self.customer_id = conn.execute(
            """
            INSERT INTO customers
              (nama,nama_normalisasi,whatsapp,whatsapp_normalized,instansi,pic,
               email,alamat,kota,status,status_aktif,produk,sumber)
            VALUES ('Ibu Geugeu','ibu geugeu','081234567890','6281234567890',
                    'PT Geugeu','Ibu Geugeu','geugeu@example.com','Garut','Garut',
                    'Existing Customer',1,'Tempat Sampah','Import')
            """
        ).lastrowid
        self.product_id = conn.execute(
            "INSERT INTO products(kode_produk,nama_produk,satuan,harga_jual_default,harga_modal_default) VALUES('TS-120-H','Tempat Sampah 120 Liter Roda Hijau','Unit',500000,300000)"
        ).lastrowid
        conn.commit(); conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def detail(self):
        conn = database.get_connection()
        try:
            return get_customer_360(conn, self.customer_id)
        finally:
            conn.close()

    def transaction(self, number="TRX-001", date="2026-01-10", status="Closing", total=500000, margin=200000, qty=1):
        conn = database.get_connection()
        tid = conn.execute(
            """INSERT INTO sales_transactions
               (nomor_transaksi,customer_id,tanggal,jenis_penjualan,status,total_penjualan,total_modal,margin,laba_bersih)
               VALUES(?,?,?,'Direct',?,?,?,?,?)""",
            (number, self.customer_id, date, status, total, total-margin, margin, margin),
        ).lastrowid
        conn.execute(
            """INSERT INTO sales_transaction_items
               (transaction_id,product_id,kode_produk_snapshot,nama_produk_snapshot,
                kategori_snapshot,varian_snapshot,warna_snapshot,ukuran_snapshot,satuan_snapshot,
                qty,harga_jual_satuan,subtotal_penjualan,harga_modal_satuan,subtotal_modal,margin_item)
               VALUES(?,?,'TS-120-H','Tempat Sampah 120 Liter Roda Hijau','Tempat Sampah',
                      'Roda','Hijau','120 Liter','Unit',?,?,?,?,?,?)""",
            (tid, self.product_id, qty, total//qty, total, (total-margin)//qty, total-margin, margin),
        )
        conn.commit(); conn.close()
        return tid

    def invoice(self, tid, status="DP"):
        conn = database.get_connection()
        iid = conn.execute(
            "INSERT INTO sales_invoices(transaction_id,nomor_invoice,tanggal_invoice,jatuh_tempo,status_pembayaran) VALUES(?,?,'2026-01-11','2026-02-11',?)",
            (tid, f"INV-{tid}", status),
        ).lastrowid
        conn.commit(); conn.close(); return iid

    def receipt(self, tid, iid, nominal=200000, status="Diterbitkan", suffix="1"):
        conn = database.get_connection()
        rid = conn.execute(
            """INSERT INTO payment_receipts
               (nomor_kwitansi,invoice_id,transaction_id,tanggal,jenis_pembayaran,
                metode_pembayaran,nominal,untuk_pembayaran,status)
               VALUES(?,?,?,'2026-01-12','DP','Transfer Bank',?,'Pembayaran',?)""",
            (f"KWT-{tid}-{suffix}", iid, tid, nominal, status),
        ).lastrowid
        conn.commit(); conn.close(); return rid

    def historical(self, price="500000"):
        return self.client.post(
            f"/customers/{self.customer_id}/purchase-history",
            data={"tanggal_pembelian":"2024-01-12","product_id":str(self.product_id),
                  "qty":"12","harga_satuan":price,"source":"Invoice lama"},
        )

    # 1
    def test_customer_detail_can_open(self):
        self.assertEqual(self.client.get(f"/customers/{self.customer_id}").status_code, 200)

    # 2
    def test_customer_not_found_is_404(self):
        self.assertEqual(self.client.get("/customers/999999").status_code, 404)

    # 3
    def test_complete_profile_is_rendered(self):
        body = self.client.get(f"/customers/{self.customer_id}").get_data(as_text=True)
        for value in ("PT Geugeu", "geugeu@example.com", "Garut", "6281234567890"):
            self.assertIn(value, body)

    # 4
    def test_minimal_name_and_phone_customer_can_open(self):
        conn=database.get_connection(); cid=conn.execute("INSERT INTO customers(nama,whatsapp) VALUES('Minimal','0811')").lastrowid; conn.commit(); conn.close()
        self.assertEqual(self.client.get(f"/customers/{cid}").status_code, 200)

    # 5
    def test_quotation_kpis_are_correct(self):
        conn=database.get_connection()
        conn.execute("INSERT INTO sales_quotations(nomor_penawaran,customer_id,tanggal,status) VALUES('Q-1',?,'2026-01-01','Draft')",(self.customer_id,))
        conn.execute("INSERT INTO sales_quotations(nomor_penawaran,customer_id,tanggal,status) VALUES('Q-2',?,'2026-01-02','Deal')",(self.customer_id,)); conn.commit(); conn.close()
        k=self.detail()["kpis"]; self.assertEqual((k["total_quotation"],k["quotation_aktif"],k["quotation_deal"]),(2,1,1))

    # 6
    def test_transaction_kpi_is_correct(self):
        self.transaction(); self.assertEqual(self.detail()["kpis"]["total_transaction"],1)

    # 7
    def test_turnover_excludes_cancelled_transaction(self):
        self.transaction(); self.transaction("TRX-X", status="Batal", total=900000)
        self.assertEqual(self.detail()["kpis"]["total_omzet"],500000)

    # 8
    def test_margin_is_correct(self):
        self.transaction(margin=175000); self.assertEqual(self.detail()["kpis"]["total_margin"],175000)

    # 9
    def test_repeat_order_is_correct(self):
        self.transaction(); self.transaction("TRX-002","2026-02-10")
        k=self.detail()["kpis"]; self.assertEqual(k["repeat_order"],2); self.assertTrue(k["is_repeat_customer"])

    # 10
    def test_invoice_outstanding_is_correct(self):
        self.invoice(self.transaction()); self.assertEqual(self.detail()["kpis"]["invoice_outstanding"],1)

    # 11
    def test_receivable_uses_non_void_receipts(self):
        tid=self.transaction(); iid=self.invoice(tid); self.receipt(tid,iid,200000); self.receipt(tid,iid,100000,"Void","void")
        self.assertEqual(self.detail()["kpis"]["total_piutang"],300000)

    # 12
    def test_first_and_last_order_are_correct(self):
        self.transaction(date="2025-01-01"); self.transaction("TRX-2","2026-07-01")
        k=self.detail()["kpis"]; self.assertEqual((k["order_pertama"],k["order_terakhir"]),("2025-01-01","2026-07-01"))

    # 13
    def test_favorite_product_aggregation_is_correct(self):
        self.transaction(qty=2,total=1000000); p=self.detail()["favorites"][0]
        self.assertEqual((p["total_qty"],p["omzet_produk"]),(2,1000000))

    # 14
    def test_transaction_purchase_history_is_rendered(self):
        self.transaction(); self.assertIn("TRX-001",self.client.get(f"/customers/{self.customer_id}").get_data(as_text=True))

    # 15
    def test_historical_purchase_is_rendered(self):
        self.historical(); self.assertIn("HIST-",self.client.get(f"/customers/{self.customer_id}").get_data(as_text=True))

    # 16
    def test_historical_purchase_does_not_create_invoice(self):
        self.historical(); conn=database.get_connection(); self.assertEqual(conn.execute("SELECT COUNT(*) FROM sales_invoices").fetchone()[0],0); conn.close()

    # 17
    def test_historical_purchase_does_not_create_delivery_order(self):
        self.historical(); conn=database.get_connection(); self.assertEqual(conn.execute("SELECT COUNT(*) FROM delivery_orders").fetchone()[0],0); conn.close()

    # 18
    def test_historical_purchase_does_not_create_purchase_order(self):
        self.historical(); conn=database.get_connection(); self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0],0); conn.close()

    # 19
    def test_historical_purchase_does_not_change_stock(self):
        self.historical(); conn=database.get_connection(); self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0],0); conn.close()

    # 20
    def test_historical_purchase_without_price_is_saved(self):
        self.assertEqual(self.historical("").status_code,302); self.assertIsNone(self.detail()["historical_purchases"][0]["total"])

    # 21
    def test_priced_historical_purchase_contributes_turnover(self):
        self.historical("500000"); self.assertEqual(self.detail()["kpis"]["total_omzet"],6000000)

    # 22
    def test_customer_note_can_be_created(self):
        r=self.client.post(f"/customers/{self.customer_id}/notes",data={"note_type":"Preference","note_text":"Suka warna hijau"}); self.assertEqual(r.status_code,302); self.assertEqual(len(self.detail()["notes"]),1)

    # 23
    def test_customer_note_can_be_edited(self):
        self.client.post(f"/customers/{self.customer_id}/notes",data={"note_type":"General","note_text":"Awal"}); nid=self.detail()["notes"][0]["id"]
        self.client.post(f"/customers/{self.customer_id}/notes/{nid}/edit",data={"note_type":"Complaint","note_text":"Revisi"}); self.assertEqual(self.detail()["notes"][0]["note_text"],"Revisi")

    # 24
    def test_customer_note_can_be_deactivated(self):
        self.client.post(f"/customers/{self.customer_id}/notes",data={"note_type":"General","note_text":"Arsipkan"}); nid=self.detail()["notes"][0]["id"]
        self.client.post(f"/customers/{self.customer_id}/notes/{nid}/deactivate"); self.assertEqual(self.detail()["notes"],[])

    # 25
    def test_timeline_combines_existing_events(self):
        tid=self.transaction(); conn=database.get_connection(); conn.execute("INSERT INTO workflow_events(document_type,document_id,customer_id,event_type,description) VALUES('TRANSACTION',?,?,'created','Workflow tersimpan')",(tid,self.customer_id)); conn.commit(); conn.close()
        descriptions=[e["description"] for e in self.detail()["timeline"]]; self.assertIn("Workflow tersimpan",descriptions); self.assertIn("Transaction dibuat",descriptions)

    # 26
    def test_void_receipt_remains_visible(self):
        tid=self.transaction(); iid=self.invoice(tid); self.receipt(tid,iid,status="Void"); self.assertEqual(self.detail()["receipts"][0]["status"],"Void")

    # 27
    def test_related_po_is_document_not_customer_purchase(self):
        tid=self.transaction(); conn=database.get_connection(); sid=conn.execute("INSERT INTO suppliers(nama_supplier) VALUES('Supplier Uji')").lastrowid
        conn.execute("INSERT INTO purchase_orders(nomor_po,supplier_id,transaction_id,tanggal,status,grand_total) VALUES('PO-1',?,?,'2026-01-12','Draft',300000)",(sid,tid)); conn.commit(); conn.close()
        d=self.detail(); self.assertEqual(d["purchase_orders"][0]["nomor_po"],"PO-1"); self.assertEqual(d["kpis"]["total_omzet"],500000)

    # 28
    def test_customer_search_is_backward_compatible(self):
        data=self.client.get("/api/customers/search?keyword=Geu").get_json()["results"][0]
        for key in ("id","nama","perusahaan","pic","whatsapp","email","alamat","kota","status","minat_produk"):
            self.assertIn(key,data)

    # 29
    def test_customer_search_contains_summary_insights(self):
        self.transaction(); data=self.client.get("/api/customers/search?keyword=Geu").get_json()["results"][0]
        self.assertEqual(data["jumlah_transaksi"],1); self.assertEqual(data["produk_terakhir"],"Tempat Sampah 120 Liter Roda Hijau"); self.assertIn("klasifikasi_customer",data)

    # 30
    def test_print_templates_are_byte_identical(self):
        template_dir=PROJECT_ROOT/"app"/"templates"
        for name,expected in PRINT_HASHES.items(): self.assertEqual(hashlib.sha256((template_dir/name).read_bytes()).hexdigest(),expected)

    # 31
    def test_financial_invariant_remains_exact(self):
        tid=self.transaction(total=750000,margin=250000,qty=3); conn=database.get_connection(); row=conn.execute("SELECT t.total_penjualan,SUM(i.subtotal_penjualan) detail,t.margin,SUM(i.margin_item) detail_margin FROM sales_transactions t JOIN sales_transaction_items i ON i.transaction_id=t.id WHERE t.id=?",(tid,)).fetchone(); conn.close()
        self.assertEqual((row["total_penjualan"],row["margin"]),(row["detail"],row["detail_margin"]))

    # 32
    def test_historical_purchase_does_not_emit_workflow_events(self):
        self.historical(); conn=database.get_connection(); self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflow_events").fetchone()[0],0); conn.close()

    # 33
    def test_migration_twice_is_idempotent(self):
        database.create_tables(); database.create_tables(); conn=database.get_connection(); names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}; conn.close(); self.assertTrue({"customer_notes","customer_purchase_history"}.issubset(names))

    # 34
    def test_integrity_check_is_ok(self):
        conn=database.get_connection(); self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],"ok"); conn.close()

    # 35
    def test_foreign_key_check_is_empty(self):
        self.historical(); conn=database.get_connection(); self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),[]); conn.close()

    def test_invalid_historical_date_is_rejected_without_writes(self):
        response = self.client.post(
            f"/customers/{self.customer_id}/purchase-history",
            data={"tanggal_pembelian":"2026-02-31","product_id":self.product_id,"qty":"1"},
        )
        self.assertEqual(response.status_code, 400)
        conn=database.get_connection(); self.assertEqual(conn.execute("SELECT COUNT(*) FROM customer_purchase_history").fetchone()[0],0); conn.close()

    def test_minimal_customer_can_be_completed_incrementally(self):
        conn=database.get_connection(); cid=conn.execute("INSERT INTO customers(nama,whatsapp) VALUES('Customer Minimal','081211111111')").lastrowid; conn.commit(); conn.close()
        response=self.client.post(f"/customers/{cid}/edit",data={"nama":"Customer Minimal","whatsapp":"081211111111","instansi":"PT Lengkap","pic":"Bapak PIC","email":"PIC@EXAMPLE.COM","alamat":"Bandung","kota":"Bandung","produk":"Tangga","sumber":"Import","status":"Existing Customer","status_aktif":"1","catatan":""})
        self.assertEqual(response.status_code,302)
        conn=database.get_connection(); row=conn.execute("SELECT instansi,pic,email,alamat,whatsapp_normalized FROM customers WHERE id=?",(cid,)).fetchone(); conn.close()
        self.assertEqual(tuple(row),("PT Lengkap","Bapak PIC","pic@example.com","Bandung","6281211111111"))


if __name__ == "__main__":
    unittest.main()
