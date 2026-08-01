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

    def create_quotation(
        self,
        identity_type,
        *,
        unit_price=100000,
        global_discount=0,
        item_discount=0,
        qty=1,
        extra_data=None,
    ):
        data = {
            "identity_id": str(self.identity_id(identity_type)),
            "customer_id": str(self.customer_id),
            "tanggal": "2026-08-01",
            "berlaku_sampai": "2026-08-15",
            "sales": "QA Engineer",
            "diskon": str(global_discount),
            "catatan": "Regression test",
            "syarat_ketentuan": "Syarat regression test",
            "product_id[]": [str(self.product_id)],
            "qty[]": [str(qty)],
            "harga_satuan[]": [str(unit_price)],
            "diskon_item[]": [str(item_discount)],
        }

        if extra_data:
            data.update(extra_data)

        response = self.client.post(
            "/quotations/add",
            data=data,
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
        self.assert_transaction_financial_invariants(
            quotation["converted_transaction_id"]
        )
        return quotation_id, quotation["converted_transaction_id"]

    def assert_transaction_financial_invariants(self, transaction_id):
        conn = database.get_connection()
        transaction = conn.execute(
            """
            SELECT total_penjualan, total_modal, margin
            FROM sales_transactions
            WHERE id = ?
            """,
            (transaction_id,),
        ).fetchone()
        detail_totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(subtotal_penjualan), 0)
                    AS total_penjualan,
                COALESCE(SUM(subtotal_modal), 0)
                    AS total_modal,
                COALESCE(SUM(margin_item), 0)
                    AS margin
            FROM sales_transaction_items
            WHERE transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
        conn.close()

        self.assertIsNotNone(transaction)
        self.assertEqual(
            transaction["total_penjualan"],
            detail_totals["total_penjualan"],
        )
        self.assertEqual(
            transaction["total_modal"],
            detail_totals["total_modal"],
        )
        self.assertEqual(
            transaction["margin"],
            detail_totals["margin"],
        )
        self.assertEqual(
            transaction["margin"],
            transaction["total_penjualan"]
            - transaction["total_modal"],
        )
        for value in (
            transaction["total_penjualan"],
            transaction["total_modal"],
            transaction["margin"],
            detail_totals["total_penjualan"],
            detail_totals["total_modal"],
            detail_totals["margin"],
        ):
            self.assertIsInstance(value, int)

        return transaction, detail_totals

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
        self.assertEqual(identities[1]["allow_signature"], 1)
        self.assertEqual(identities[1]["allow_website_footer"], 0)
        self.assertEqual(identities[1]["allow_transaction_conversion"], 0)
        self.assertEqual(
            identities[1]["website"],
            "https://www.handliftbandung.com",
        )
        self.assertEqual(identities[1]["email"], "luki@denko.co.id")
        self.assertEqual(identities[1]["whatsapp"], "082117126895")
        self.assertEqual(
            identities[1]["bank"],
            "BCA Cab. Metro Trade Center",
        )
        self.assertEqual(identities[1]["no_rekening"], "6395758989")
        self.assertEqual(
            identities[1]["signature_path"],
            "images/signature_denko.png",
        )
        self.assertEqual(
            identities[1]["signature_name"],
            "Luki Lukmanul Hakim",
        )
        self.assertEqual(
            identities[1]["signature_title"],
            "Sales Executive",
        )
        self.assertEqual(
            identities[1]["signature_email"],
            "luki@denko.co.id",
        )
        self.assertTrue(
            (APP_DIR / "static/images/signature_denko.png").is_file()
        )

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

    def test_existing_denko_stub_is_migrated_to_official_profile(self):
        conn = database.get_connection()
        conn.execute(
            """
            UPDATE company_identities
            SET alamat = '',
                kota = '',
                provinsi = '',
                kode_pos = '',
                whatsapp = '',
                email = '',
                website = '',
                bank = '',
                no_rekening = '',
                signature_path = NULL,
                signature_name = NULL,
                signature_title = NULL,
                signature_email = NULL,
                allow_signature = 0
            WHERE code = 'DENKO'
            """
        )
        conn.commit()
        conn.close()

        database.create_tables()
        database.create_tables()

        conn = database.get_connection()
        denko = conn.execute(
            """
            SELECT *
            FROM company_identities
            WHERE code = 'DENKO'
            """
        ).fetchone()
        conn.close()

        self.assertEqual(
            denko["website"],
            "https://www.handliftbandung.com",
        )
        self.assertEqual(denko["no_rekening"], "6395758989")
        self.assertEqual(
            denko["signature_path"],
            "images/signature_denko.png",
        )
        self.assertEqual(denko["signature_title"], "Sales Executive")
        self.assertEqual(denko["allow_signature"], 1)
        self.assertEqual(denko["allow_qr"], 0)
        self.assertEqual(denko["allow_website_footer"], 0)
        self.assertEqual(denko["allow_transaction_conversion"], 0)

    def test_quotation_tax_rules_ignore_request_tampering(self):
        ahsa_id = self.create_quotation(
            main.IDENTITY_TYPE_FULL,
            unit_price=10000000,
            extra_data={
                "is_ppn": "1",
                "ppn_rate": "99",
            },
        )
        denko_id = self.create_quotation(
            main.IDENTITY_TYPE_QUOTATION_ONLY,
            unit_price=10000000,
            extra_data={
                "is_ppn": "0",
                "ppn_rate": "7",
            },
        )

        edit_response = self.client.post(
            f"/quotations/{denko_id}/edit",
            data={
                "identity_id": str(
                    self.identity_id(main.IDENTITY_TYPE_QUOTATION_ONLY)
                ),
                "customer_id": str(self.customer_id),
                "tanggal": "2026-08-01",
                "berlaku_sampai": "2026-08-15",
                "sales": "QA Engineer",
                "diskon": "0",
                "is_ppn": "0",
                "ppn_rate": "22",
                "catatan": "Regression test",
                "syarat_ketentuan": "Syarat regression test",
                "product_id[]": [str(self.product_id)],
                "qty[]": ["1"],
                "harga_satuan[]": ["10000000"],
                "diskon_item[]": ["0"],
            },
        )
        self.assertEqual(edit_response.status_code, 302)

        conn = database.get_connection()
        ahsa = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (ahsa_id,),
        ).fetchone()
        denko = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (denko_id,),
        ).fetchone()
        denko_identity = conn.execute(
            """
            SELECT *
            FROM company_identities
            WHERE identity_type = ?
            """,
            (main.IDENTITY_TYPE_QUOTATION_ONLY,),
        ).fetchone()
        conn.close()

        self.assertEqual(ahsa["is_ppn"], 0)
        self.assertEqual(ahsa["ppn_rate"], 0)
        self.assertEqual(ahsa["ppn_amount"], 0)
        self.assertEqual(ahsa["dpp"], 10000000)
        self.assertEqual(ahsa["grand_total"], 10000000)

        self.assertEqual(denko["subtotal"], 10000000)
        self.assertEqual(denko["diskon"], 0)
        self.assertEqual(denko["is_ppn"], 1)
        self.assertEqual(denko["ppn_rate"], 11)
        self.assertEqual(denko["dpp"], 10000000)
        self.assertEqual(denko["ppn_amount"], 1100000)
        self.assertEqual(denko["grand_total"], 11100000)

        rounded = main.calculate_quotation_totals(
            105,
            0,
            denko_identity,
        )
        self.assertIsInstance(rounded["ppn_amount"], int)
        self.assertEqual(rounded["ppn_amount"], 12)

    def test_denko_discount_pdf_and_terbilang_use_taxed_total(self):
        denko_id = self.create_quotation(
            main.IDENTITY_TYPE_QUOTATION_ONLY,
            unit_price=10000000,
            global_discount=1000000,
        )
        ahsa_id = self.create_quotation(
            main.IDENTITY_TYPE_FULL,
            unit_price=10000000,
            global_discount=1000000,
        )

        conn = database.get_connection()
        denko = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (denko_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(denko["dpp"], 9000000)
        self.assertEqual(denko["ppn_amount"], 990000)
        self.assertEqual(denko["grand_total"], 9990000)

        denko_html = self.client.get(
            f"/quotations/{denko_id}/print"
        ).get_data(as_text=True)
        ahsa_html = self.client.get(
            f"/quotations/{ahsa_id}/print"
        ).get_data(as_text=True)

        self.assertIn("<strong>DPP</strong>", denko_html)
        self.assertIn("<strong>PPN 11%</strong>", denko_html)
        self.assertIn("Rp 9.000.000", denko_html)
        self.assertIn("Rp 990.000", denko_html)
        self.assertIn("Rp 9.990.000", denko_html)
        self.assertIn(
            "Sembilan Juta Sembilan Ratus Sembilan Puluh Ribu Rupiah",
            denko_html,
        )
        self.assertNotIn("<strong>DPP</strong>", ahsa_html)
        self.assertNotIn("<strong>PPN 11%</strong>", ahsa_html)

    def test_duplicate_denko_recalculates_mandatory_tax(self):
        denko_id = self.create_quotation(
            main.IDENTITY_TYPE_QUOTATION_ONLY,
            unit_price=10000000,
        )

        conn = database.get_connection()
        conn.execute(
            """
            UPDATE sales_quotations
            SET is_ppn = 0,
                ppn_rate = 0,
                dpp = 0,
                ppn_amount = 0,
                grand_total = 10000000
            WHERE id = ?
            """,
            (denko_id,),
        )
        conn.commit()
        conn.close()

        response = self.client.post(
            f"/quotations/{denko_id}/duplicate"
        )
        self.assertEqual(response.status_code, 302)
        duplicate_id = int(response.headers["Location"].split("/")[-2])

        conn = database.get_connection()
        duplicate = conn.execute(
            """
            SELECT
                sales_quotations.*,
                company_identities.identity_type
            FROM sales_quotations
            JOIN company_identities
                ON sales_quotations.identity_id = company_identities.id
            WHERE sales_quotations.id = ?
            """,
            (duplicate_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(
            duplicate["identity_type"],
            main.IDENTITY_TYPE_QUOTATION_ONLY,
        )
        self.assertEqual(duplicate["is_ppn"], 1)
        self.assertEqual(duplicate["ppn_rate"], 11)
        self.assertEqual(duplicate["dpp"], 10000000)
        self.assertEqual(duplicate["ppn_amount"], 1100000)
        self.assertEqual(duplicate["grand_total"], 11100000)

    def test_tax_migration_is_idempotent_and_preserves_legacy_total(self):
        denko_identity_id = self.identity_id(
            main.IDENTITY_TYPE_QUOTATION_ONLY
        )
        conn = database.get_connection()
        legacy_id = conn.execute(
            """
            INSERT INTO sales_quotations (
                nomor_penawaran,
                customer_id,
                tanggal,
                subtotal,
                diskon,
                grand_total,
                identity_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "QT-DENKO-BEFORE-PPN",
                self.customer_id,
                "2026-07-31",
                10000000,
                0,
                10000000,
                denko_identity_id,
            ),
        ).lastrowid
        conn.commit()
        conn.close()

        database.create_tables()
        database.create_tables()

        conn = database.get_connection()
        legacy = conn.execute(
            "SELECT * FROM sales_quotations WHERE id = ?",
            (legacy_id,),
        ).fetchone()
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(sales_quotations)"
            ).fetchall()
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()

        self.assertTrue(
            {"is_ppn", "ppn_rate", "dpp", "ppn_amount"}.issubset(columns)
        )
        self.assertEqual(legacy["is_ppn"], 0)
        self.assertEqual(legacy["ppn_rate"], 0)
        self.assertEqual(legacy["dpp"], 10000000)
        self.assertEqual(legacy["ppn_amount"], 0)
        self.assertEqual(legacy["grand_total"], 10000000)
        self.assertEqual(integrity, "ok")

        legacy_html = self.client.get(
            f"/quotations/{legacy_id}/print"
        ).get_data(as_text=True)
        self.assertNotIn("<strong>PPN 11%</strong>", legacy_html)
        self.assertIn("Rp 10.000.000", legacy_html)

    def test_global_discount_allocation_is_proportional(self):
        allocated = main.allocate_global_discount(
            [
                {
                    "qty": 1,
                    "harga_satuan": 6000000,
                    "diskon_item": 0,
                },
                {
                    "qty": 1,
                    "harga_satuan": 4000000,
                    "diskon_item": 0,
                },
            ],
            1000000,
        )

        self.assertEqual(
            [item["subtotal_awal"] for item in allocated],
            [6000000, 4000000],
        )
        self.assertEqual(
            [item["diskon_global_alokasi"] for item in allocated],
            [600000, 400000],
        )
        self.assertEqual(
            [item["subtotal_akhir"] for item in allocated],
            [5400000, 3600000],
        )
        self.assertEqual(
            sum(
                item["diskon_global_alokasi"]
                for item in allocated
            ),
            1000000,
        )

    def test_global_discount_allocation_handles_rounding_and_edges(self):
        equal_items = [
            {
                "qty": 1,
                "harga_satuan": 1,
                "diskon_item": 0,
            }
            for _ in range(3)
        ]
        rounded = main.allocate_global_discount(
            equal_items,
            2,
        )

        self.assertEqual(
            [item["diskon_global_alokasi"] for item in rounded],
            [1, 1, 0],
        )
        self.assertEqual(
            sum(
                item["diskon_global_alokasi"]
                for item in rounded
            ),
            2,
        )

        single = main.allocate_global_discount(
            [{"qty": 1, "harga_satuan": 100, "diskon_item": 0}],
            40,
        )
        self.assertEqual(single[0]["diskon_global_alokasi"], 40)
        self.assertEqual(single[0]["subtotal_akhir"], 60)

        excessive = main.allocate_global_discount(
            [
                {"qty": 1, "harga_satuan": 60, "diskon_item": 0},
                {"qty": 1, "harga_satuan": 40, "diskon_item": 0},
            ],
            200,
        )
        self.assertEqual(
            [item["diskon_global_alokasi"] for item in excessive],
            [60, 40],
        )
        self.assertEqual(
            [item["subtotal_akhir"] for item in excessive],
            [0, 0],
        )

        no_discount = main.allocate_global_discount(
            [{"qty": 2, "harga_satuan": 50, "diskon_item": 10}],
            0,
        )
        self.assertEqual(no_discount[0]["subtotal_awal"], 90)
        self.assertEqual(no_discount[0]["diskon_global_alokasi"], 0)
        self.assertEqual(no_discount[0]["subtotal_akhir"], 90)

        zero_base = main.allocate_global_discount(
            [{"qty": 1, "harga_satuan": 0, "diskon_item": 0}],
            10,
        )
        self.assertEqual(zero_base[0]["subtotal_akhir"], 0)
        self.assertEqual(
            main.allocate_global_discount([], 10),
            [],
        )

        for scenario in (rounded, single, excessive, no_discount, zero_base):
            for item in scenario:
                self.assertIsInstance(item["subtotal_awal"], int)
                self.assertIsInstance(
                    item["diskon_global_alokasi"],
                    int,
                )
                self.assertIsInstance(item["subtotal_akhir"], int)
                self.assertIsInstance(item["margin_item"], int)
                self.assertGreaterEqual(item["subtotal_akhir"], 0)

    def test_financial_engine_handles_many_items_and_combined_discounts(self):
        allocated = main.allocate_global_discount(
            [
                {
                    "qty": 2,
                    "harga_satuan": 4000000,
                    "diskon_item": 2000000,
                    "subtotal_modal": 2000000,
                },
                {
                    "qty": 1,
                    "harga_satuan": 4000000,
                    "diskon_item": 0,
                    "subtotal_modal": 1000000,
                },
                {
                    "qty": 0,
                    "harga_satuan": 1000000,
                    "diskon_item": 0,
                    "subtotal_modal": 0,
                },
                {
                    "qty": 1,
                    "harga_satuan": 0,
                    "diskon_item": 0,
                    "subtotal_modal": 0,
                },
                {
                    "qty": -2,
                    "harga_satuan": 100,
                    "diskon_item": 0,
                    "subtotal_modal": 0,
                },
                {
                    "qty": 1,
                    "harga_satuan": 100,
                    "diskon_item": 200,
                    "subtotal_modal": 0,
                },
            ],
            1000000,
        )

        self.assertEqual(
            [item["subtotal_awal"] for item in allocated],
            [6000000, 4000000, 0, 0, 0, 0],
        )
        self.assertEqual(
            [item["diskon_global_alokasi"] for item in allocated],
            [600000, 400000, 0, 0, 0, 0],
        )
        self.assertEqual(
            [item["subtotal_akhir"] for item in allocated],
            [5400000, 3600000, 0, 0, 0, 0],
        )
        self.assertTrue(
            all(item["subtotal_akhir"] >= 0 for item in allocated)
        )

        financials = main.calculate_transaction_financials(
            [
                {
                    "subtotal_penjualan": item["subtotal_akhir"],
                    "subtotal_modal": item["subtotal_modal"],
                    "margin_item": item["margin_item"],
                }
                for item in allocated
            ]
        )
        self.assertEqual(financials["total_penjualan"], 9000000)
        self.assertEqual(financials["total_modal"], 3000000)
        self.assertEqual(financials["margin"], 6000000)
        self.assertEqual(
            financials["margin"],
            financials["total_penjualan"] - financials["total_modal"],
        )

    def test_marketplace_add_and_edit_preserve_financial_invariants(self):
        add_response = self.client.post(
            "/transactions/add",
            data={
                "customer_id": str(self.customer_id),
                "tanggal": "2026-08-02",
                "jenis_penjualan": "Marketplace",
                "referal": "Marketplace QA",
                "admin_fee": "100000",
                "potongan": "50000",
                "biaya_lain": "25000",
                "keterangan_biaya": "Biaya QA",
                "catatan": "Invariant add",
                "product_id[]": [
                    str(self.product_id),
                    str(self.product_id),
                ],
                "qty[]": ["1", "1"],
                "harga_jual[]": ["600000", "400000"],
                "harga_modal[]": ["200000", "100000"],
            },
        )
        self.assertEqual(
            add_response.status_code,
            302,
            add_response.get_data(as_text=True),
        )
        transaction_id = int(
            add_response.headers["Location"].rstrip("/").split("/")[-1]
        )
        self.assert_transaction_financial_invariants(transaction_id)

        conn = database.get_connection()
        transaction = conn.execute(
            "SELECT * FROM sales_transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(transaction["total_penjualan"], 1000000)
        self.assertEqual(transaction["total_modal"], 300000)
        self.assertEqual(transaction["margin"], 700000)
        self.assertEqual(transaction["jumlah_diterima"], 850000)
        self.assertEqual(transaction["laba_bersih"], 525000)

        edit_response = self.client.post(
            f"/transactions/{transaction_id}/edit",
            data={
                "customer_id": str(self.customer_id),
                "tanggal": "2026-08-03",
                "jenis_penjualan": "Marketplace",
                "referal": "Marketplace QA Edit",
                "admin_fee": "70000",
                "potongan": "30000",
                "biaya_lain": "20000",
                "keterangan_biaya": "Biaya QA Edit",
                "catatan": "Invariant edit",
                "product_id[]": [
                    str(self.product_id),
                    str(self.product_id),
                ],
                "qty[]": ["1", "1"],
                "harga_jual[]": ["400000", "300000"],
                "harga_modal[]": ["100000", "100000"],
            },
        )
        self.assertEqual(
            edit_response.status_code,
            302,
            edit_response.get_data(as_text=True),
        )
        self.assert_transaction_financial_invariants(transaction_id)

        conn = database.get_connection()
        edited = conn.execute(
            "SELECT * FROM sales_transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(edited["total_penjualan"], 700000)
        self.assertEqual(edited["total_modal"], 200000)
        self.assertEqual(edited["margin"], 500000)
        self.assertEqual(edited["jumlah_diterima"], 600000)
        self.assertEqual(edited["laba_bersih"], 380000)

    def test_ahsa_conversion_allocates_global_discount_to_details(self):
        conn = database.get_connection()
        second_product_id = conn.execute(
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
                "QA-002",
                "Produk Regression QA Kedua",
                "Unit",
                100000,
                40000,
            ),
        ).lastrowid
        conn.commit()
        conn.close()

        response = self.client.post(
            "/quotations/add",
            data={
                "identity_id": str(
                    self.identity_id(main.IDENTITY_TYPE_FULL)
                ),
                "customer_id": str(self.customer_id),
                "tanggal": "2026-08-01",
                "berlaku_sampai": "2026-08-15",
                "sales": "QA Engineer",
                "diskon": "1000000",
                "product_id[]": [
                    str(self.product_id),
                    str(second_product_id),
                ],
                "qty[]": ["1", "1"],
                "harga_satuan[]": ["6000000", "4000000"],
                "diskon_item[]": ["0", "0"],
            },
        )
        self.assertEqual(response.status_code, 302)
        quotation_id = int(
            response.headers["Location"].rstrip("/").split("/")[-1]
        )

        conversion = self.client.post(
            f"/quotations/{quotation_id}/convert"
        )
        self.assertEqual(
            conversion.status_code,
            302,
            conversion.get_data(as_text=True),
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
        details = conn.execute(
            """
            SELECT subtotal_penjualan, subtotal_modal, margin_item
            FROM sales_transaction_items
            WHERE transaction_id = ?
            ORDER BY id ASC
            """,
            (quotation["converted_transaction_id"],),
        ).fetchall()
        conn.close()

        transaction, detail_totals = (
            self.assert_transaction_financial_invariants(
                quotation["converted_transaction_id"]
            )
        )
        self.assertEqual(transaction["total_penjualan"], 9000000)
        self.assertEqual(detail_totals["total_penjualan"], 9000000)
        self.assertEqual(
            [item["subtotal_penjualan"] for item in details],
            [5400000, 3600000],
        )
        self.assertEqual(
            [
                6000000 - details[0]["subtotal_penjualan"],
                4000000 - details[1]["subtotal_penjualan"],
            ],
            [600000, 400000],
        )
        self.assertEqual(
            transaction["margin"],
            sum(item["margin_item"] for item in details),
        )
        self.assertEqual(
            transaction["total_modal"],
            sum(item["subtotal_modal"] for item in details),
        )

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
        self.assertIn("Kantor Cabang Bandung", denko_html)
        self.assertIn("BCA Cab. Metro Trade Center", denko_html)
        self.assertIn("6395758989", denko_html)
        self.assertIn("www.handliftbandung.com", denko_html)
        self.assertIn("images/signature_denko.png", denko_html)
        self.assertIn("Luki Lukmanul Hakim", denko_html)
        self.assertIn("Sales Executive", denko_html)
        self.assertIn("luki@denko.co.id", denko_html)
        self.assertNotIn("images/logo-ahsa.png", denko_html)
        self.assertNotIn("distributordalton.com", denko_html)
        self.assertNotIn("data:image/png;base64", denko_html)
        self.assertNotIn("PT Ahsa Cahaya Persada", denko_html)
        self.assertNotIn("<footer", denko_html)

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
        self.assertEqual(persisted["show_signature"], 1)

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
        self.assertIn("www.handliftbandung.com", denko_html)
        self.assertIn("images/signature_denko.png", denko_html)
        self.assertIn("Luki Lukmanul Hakim", denko_html)
        self.assertNotIn("images/logo-ahsa.png", denko_html)
        self.assertNotIn("PT Ahsa Cahaya Persada", denko_html)
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
