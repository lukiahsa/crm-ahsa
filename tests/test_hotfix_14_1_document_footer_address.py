import hashlib
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


NEW_ADDRESS_LINES = (
    "Kawasan Industri De Primaterra",
    "Jl Raya Sapan, Blok E2, Tegalluar",
    "Bojongsoang, Bandung",
)

SALES_TEMPLATE_HASHES = {
    "quotation_print.html": "dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622",
    "invoice_print.html": "7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8",
    "receipt_print.html": "4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4",
    "delivery_order_print.html": "ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5",
}

PURCHASE_ORDER_BASELINE_HASH = (
    "139d248828adbaa55d6b8672a65d4ebc6d04d00a6e2c7cece305d81075ff6064"
)


class DocumentFooterAddressHotfixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        database.DATABASE = Path(cls.temp_dir.name) / "footer-address.db"
        database.create_tables()

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def template_source(self, name):
        return (TEMPLATE_DIR / name).read_text(encoding="utf-8")

    def test_non_po_footer_uses_exact_new_three_line_address(self):
        for name in SALES_TEMPLATE_HASHES:
            with self.subTest(template=name):
                source = self.template_source(name)
                footer = source[source.index("<footer"):source.rindex("</footer>")]
                for line in NEW_ADDRESS_LINES:
                    self.assertIn(line, footer)
                self.assertNotIn("identity['alamat']", footer)
                self.assertNotIn("Kp. Jati", footer)
                self.assertNotIn("Dangdeur", footer)
                self.assertNotIn("Banyuresmi", footer)

    def test_non_po_template_hashes_are_new_frozen_baseline(self):
        actual = {
            name: hashlib.sha256((TEMPLATE_DIR / name).read_bytes()).hexdigest()
            for name in SALES_TEMPLATE_HASHES
        }
        self.assertEqual(actual, SALES_TEMPLATE_HASHES)

    def test_purchase_order_template_is_byte_identical_to_sprint_14(self):
        path = TEMPLATE_DIR / "purchase_order_print.html"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), PURCHASE_ORDER_BASELINE_HASH)
        source = path.read_text(encoding="utf-8")
        self.assertIn('company["alamat"]', source)
        self.assertIn('company["kota"]', source)
        self.assertNotIn(NEW_ADDRESS_LINES[0], source)

    def test_purchase_order_address_source_remains_old_ahsa_identity(self):
        conn = database.get_connection()
        company = conn.execute(
            """SELECT alamat, kota, provinsi FROM company_identities
               WHERE code = 'AHSA' AND identity_type = 'FULL'"""
        ).fetchone()
        conn.close()
        self.assertIn("Kp. Jati", company["alamat"])
        self.assertIn("Dangdeur", company["alamat"])
        self.assertIn("Banyuresmi", company["alamat"])
        self.assertEqual(company["kota"], "Garut")
        self.assertEqual(company["provinsi"], "Jawa Barat")

    def test_non_address_document_controls_remain_available(self):
        sources = {
            name: self.template_source(name) for name in SALES_TEMPLATE_HASHES
        }
        required_by_template = {
            "quotation_print.html": (
                "nomor_penawaran", "logo_path", "qr_code_data_uri",
                "no_rekening", "whatsapp", "website", "signature_path",
            ),
            "invoice_print.html": (
                "nomor_invoice", "logo_path", "qr_code_data_uri",
                "no_rekening", "whatsapp", "website", "signature_path",
            ),
            "receipt_print.html": (
                "nomor_kwitansi", "logo_path", "qr_code_data_uri",
                "whatsapp", "website", "signature_path",
            ),
            "delivery_order_print.html": (
                "nomor_surat_jalan", "logo_path", "qr_code_data_uri",
                "whatsapp", "website",
            ),
        }
        for name, controls in required_by_template.items():
            for control in controls:
                with self.subTest(template=name, control=control):
                    self.assertIn(control, sources[name])


if __name__ == "__main__":
    unittest.main()
