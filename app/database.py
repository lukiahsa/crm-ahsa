import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "database"
DATABASE = DATABASE_DIR / "crm.db"

DATABASE_DIR.mkdir(parents=True, exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row

    # Mengaktifkan foreign key SQLite.
    conn.execute("PRAGMA foreign_keys = ON")

    return conn


def table_exists(conn, table_name):
    result = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return result is not None


def column_exists(conn, table_name, column_name):
    if not table_exists(conn, table_name):
        return False

    columns = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return any(
        column["name"] == column_name
        for column in columns
    )


def ensure_column(
    conn,
    table_name,
    column_name,
    column_definition,
):
    """
    Menambahkan kolom baru ke tabel lama tanpa menghapus data.
    """

    if not table_exists(conn, table_name):
        return

    if not column_exists(
        conn,
        table_name,
        column_name,
    ):
        conn.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {column_definition}
            """
        )


def create_tables():
    conn = get_connection()
    cursor = conn.cursor()

    # ==========================================================
    # CUSTOMERS
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL,
            whatsapp TEXT,
            instansi TEXT,
            kota TEXT,
            produk TEXT,
            sumber TEXT,
            status TEXT,
            catatan TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # ==========================================================
    # MASTER REFERENSI PRODUK
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS product_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL UNIQUE,
            status_aktif INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS product_brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL UNIQUE,
            status_aktif INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS product_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            nama TEXT NOT NULL,
            status_aktif INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (category_id)
                REFERENCES product_categories (id)
                ON DELETE SET NULL,

            UNIQUE (category_id, nama)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS product_colors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama TEXT NOT NULL UNIQUE,
            status_aktif INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS product_sizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            nama TEXT NOT NULL,
            status_aktif INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (category_id)
                REFERENCES product_categories (id)
                ON DELETE SET NULL,

            UNIQUE (category_id, nama)
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_supplier TEXT UNIQUE,
            nama TEXT,
            nama_supplier TEXT,
            jenis_supplier TEXT NOT NULL DEFAULT 'Distributor',
            alamat TEXT,
            kota TEXT,
            provinsi TEXT,
            kode_pos TEXT,
            pic TEXT,
            jabatan TEXT,
            kontak TEXT,
            telepon TEXT,
            whatsapp TEXT,
            email TEXT,
            website TEXT,
            npwp TEXT,
            bank TEXT,
            no_rekening TEXT,
            atas_nama TEXT,
            payment_term INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Aktif',
            status_aktif INTEGER NOT NULL DEFAULT 1,
            catatan TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # ==========================================================
    # MASTER PRODUK
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            kode_produk TEXT UNIQUE,
            nama_produk TEXT NOT NULL,

            category_id INTEGER,
            brand_id INTEGER,
            variant_id INTEGER,
            color_id INTEGER,
            size_id INTEGER,
            supplier_id INTEGER,

            satuan TEXT NOT NULL DEFAULT 'Unit',

            harga_jual_default INTEGER NOT NULL DEFAULT 0,
            harga_modal_default INTEGER NOT NULL DEFAULT 0,

            status_aktif INTEGER NOT NULL DEFAULT 1,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (category_id)
                REFERENCES product_categories (id)
                ON DELETE SET NULL,

            FOREIGN KEY (brand_id)
                REFERENCES product_brands (id)
                ON DELETE SET NULL,

            FOREIGN KEY (variant_id)
                REFERENCES product_variants (id)
                ON DELETE SET NULL,

            FOREIGN KEY (color_id)
                REFERENCES product_colors (id)
                ON DELETE SET NULL,

            FOREIGN KEY (size_id)
                REFERENCES product_sizes (id)
                ON DELETE SET NULL,

            FOREIGN KEY (supplier_id)
                REFERENCES suppliers (id)
                ON DELETE SET NULL
        )
        """
    )

    # ==========================================================
    # MIGRASI PRODUCTS VERSI LAMA
    # ==========================================================
    ensure_column(
        conn,
        "products",
        "category_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "products",
        "brand_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "products",
        "variant_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "products",
        "color_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "products",
        "size_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "products",
        "supplier_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "products",
        "satuan",
        "TEXT NOT NULL DEFAULT 'Unit'",
    )

    ensure_column(
        conn,
        "products",
        "harga_jual_default",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "products",
        "harga_modal_default",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "products",
        "status_aktif",
        "INTEGER NOT NULL DEFAULT 1",
    )

    ensure_column(
        conn,
        "products",
        "created_at",
        "TIMESTAMP",
    )

    ensure_column(
        conn,
        "products",
        "updated_at",
        "TIMESTAMP",
    )

    # Memindahkan data kategori lama ke master referensi.
    if column_exists(conn, "products", "kategori"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO product_categories (nama)
            SELECT DISTINCT TRIM(kategori)
            FROM products
            WHERE kategori IS NOT NULL
              AND TRIM(kategori) != ''
            """
        )

        cursor.execute(
            """
            UPDATE products
            SET category_id = (
                SELECT product_categories.id
                FROM product_categories
                WHERE LOWER(product_categories.nama)
                    = LOWER(TRIM(products.kategori))
            )
            WHERE category_id IS NULL
              AND kategori IS NOT NULL
              AND TRIM(kategori) != ''
            """
        )

    # Memindahkan data brand lama apabila kolomnya tersedia.
    if column_exists(conn, "products", "brand"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO product_brands (nama)
            SELECT DISTINCT TRIM(brand)
            FROM products
            WHERE brand IS NOT NULL
              AND TRIM(brand) != ''
            """
        )

        cursor.execute(
            """
            UPDATE products
            SET brand_id = (
                SELECT product_brands.id
                FROM product_brands
                WHERE LOWER(product_brands.nama)
                    = LOWER(TRIM(products.brand))
            )
            WHERE brand_id IS NULL
              AND brand IS NOT NULL
              AND TRIM(brand) != ''
            """
        )

    # Memindahkan data varian lama.
    if column_exists(conn, "products", "varian"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO product_variants (
                category_id,
                nama
            )
            SELECT DISTINCT
                products.category_id,
                TRIM(products.varian)
            FROM products
            WHERE products.varian IS NOT NULL
              AND TRIM(products.varian) != ''
            """
        )

        cursor.execute(
            """
            UPDATE products
            SET variant_id = (
                SELECT product_variants.id
                FROM product_variants
                WHERE LOWER(product_variants.nama)
                    = LOWER(TRIM(products.varian))
                  AND (
                        product_variants.category_id
                            = products.category_id
                        OR product_variants.category_id IS NULL
                  )
                LIMIT 1
            )
            WHERE variant_id IS NULL
              AND varian IS NOT NULL
              AND TRIM(varian) != ''
            """
        )

    # Memindahkan data warna lama.
    if column_exists(conn, "products", "warna"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO product_colors (nama)
            SELECT DISTINCT TRIM(warna)
            FROM products
            WHERE warna IS NOT NULL
              AND TRIM(warna) != ''
            """
        )

        cursor.execute(
            """
            UPDATE products
            SET color_id = (
                SELECT product_colors.id
                FROM product_colors
                WHERE LOWER(product_colors.nama)
                    = LOWER(TRIM(products.warna))
            )
            WHERE color_id IS NULL
              AND warna IS NOT NULL
              AND TRIM(warna) != ''
            """
        )

    # Memindahkan data ukuran lama.
    if column_exists(conn, "products", "ukuran"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO product_sizes (
                category_id,
                nama
            )
            SELECT DISTINCT
                products.category_id,
                TRIM(products.ukuran)
            FROM products
            WHERE products.ukuran IS NOT NULL
              AND TRIM(products.ukuran) != ''
            """
        )

        cursor.execute(
            """
            UPDATE products
            SET size_id = (
                SELECT product_sizes.id
                FROM product_sizes
                WHERE LOWER(product_sizes.nama)
                    = LOWER(TRIM(products.ukuran))
                  AND (
                        product_sizes.category_id
                            = products.category_id
                        OR product_sizes.category_id IS NULL
                  )
                LIMIT 1
            )
            WHERE size_id IS NULL
              AND ukuran IS NOT NULL
              AND TRIM(ukuran) != ''
            """
        )

    # Memindahkan data supplier lama apabila tersedia.
    if column_exists(conn, "products", "supplier"):
        cursor.execute(
            """
            INSERT OR IGNORE INTO suppliers (nama)
            SELECT DISTINCT TRIM(supplier)
            FROM products
            WHERE supplier IS NOT NULL
              AND TRIM(supplier) != ''
            """
        )

        cursor.execute(
            """
            UPDATE products
            SET supplier_id = (
                SELECT suppliers.id
                FROM suppliers
                WHERE LOWER(suppliers.nama)
                    = LOWER(TRIM(products.supplier))
            )
            WHERE supplier_id IS NULL
              AND supplier IS NOT NULL
              AND TRIM(supplier) != ''
            """
        )

    # ==========================================================
    # KEPALA TRANSAKSI PENJUALAN
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            nomor_transaksi TEXT UNIQUE,
            customer_id INTEGER,
            tanggal TEXT NOT NULL,

            jenis_penjualan TEXT NOT NULL,
            referal TEXT,
            status TEXT NOT NULL DEFAULT 'Draft',

            total_penjualan INTEGER NOT NULL DEFAULT 0,

            admin_fee INTEGER NOT NULL DEFAULT 0,
            potongan INTEGER NOT NULL DEFAULT 0,
            jumlah_diterima INTEGER NOT NULL DEFAULT 0,

            total_modal INTEGER NOT NULL DEFAULT 0,
            margin INTEGER NOT NULL DEFAULT 0,

            biaya_lain INTEGER NOT NULL DEFAULT 0,
            keterangan_biaya TEXT,

            laba_bersih INTEGER NOT NULL DEFAULT 0,
            catatan TEXT,

            source_quotation_id INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (customer_id)
                REFERENCES customers (id)
                ON DELETE SET NULL
        )
        """
    )

    # Memastikan semua kolom kepala transaksi tersedia.
    ensure_column(
        conn,
        "sales_transactions",
        "nomor_transaksi",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "customer_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "tanggal",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "jenis_penjualan",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "referal",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "status",
        "TEXT NOT NULL DEFAULT 'Draft'",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "total_penjualan",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "admin_fee",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "potongan",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "jumlah_diterima",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "total_modal",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "margin",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "biaya_lain",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "keterangan_biaya",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "laba_bersih",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "catatan",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "created_at",
        "TIMESTAMP",
    )

    ensure_column(
        conn,
        "sales_transactions",
        "updated_at",
        "TIMESTAMP",
    )

    # Relasi transaksi yang berasal dari quotation.
    # Baris ini juga otomatis menambah kolom pada database lama.
    ensure_column(
        conn,
        "sales_transactions",
        "source_quotation_id",
        "INTEGER",
    )


    # ==========================================================
    # QUOTATION ACTIVITY LOG
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_quotation_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quotation_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (quotation_id)
                REFERENCES sales_quotations (id)
                ON DELETE CASCADE
        )
        """
    )

    ensure_column(
        conn,
        "sales_quotation_activities",
        "quotation_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "sales_quotation_activities",
        "activity_type",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_activities",
        "description",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_activities",
        "created_by",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_activities",
        "created_at",
        "TIMESTAMP",
    )


    # ==========================================================
    # DELIVERY ORDER / SURAT JALAN
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            nomor_surat_jalan TEXT NOT NULL UNIQUE,
            transaction_id INTEGER NOT NULL UNIQUE,
            invoice_id INTEGER,

            tanggal TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Draft',

            alamat_pengiriman TEXT,
            pic_penerima TEXT,
            telepon_penerima TEXT,

            metode_pengiriman TEXT DEFAULT 'Kirim Sendiri',
            ekspedisi TEXT,
            nomor_resi TEXT,
            driver TEXT,
            kendaraan TEXT,
            nomor_polisi TEXT,

            jam_keluar TEXT,
            estimasi_tiba TEXT,
            tanggal_diterima TEXT,
            jam_diterima TEXT,

            catatan TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (transaction_id)
                REFERENCES sales_transactions (id)
                ON DELETE CASCADE,

            FOREIGN KEY (invoice_id)
                REFERENCES sales_invoices (id)
                ON DELETE SET NULL
        )
        """
    )

    ensure_column(
        conn,
        "delivery_orders",
        "nomor_surat_jalan",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "transaction_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "invoice_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "tanggal",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "status",
        "TEXT NOT NULL DEFAULT 'Draft'",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "alamat_pengiriman",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "pic_penerima",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "telepon_penerima",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "metode_pengiriman",
        "TEXT DEFAULT 'Kirim Sendiri'",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "ekspedisi",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "nomor_resi",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "driver",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "kendaraan",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "nomor_polisi",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "jam_keluar",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "estimasi_tiba",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "tanggal_diterima",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "jam_diterima",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "catatan",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "created_at",
        "TIMESTAMP",
    )
    ensure_column(
        conn,
        "delivery_orders",
        "updated_at",
        "TIMESTAMP",
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            delivery_order_id INTEGER NOT NULL,
            transaction_item_id INTEGER,
            product_id INTEGER,

            kode_produk_snapshot TEXT,
            nama_produk_snapshot TEXT NOT NULL,
            kategori_snapshot TEXT,
            brand_snapshot TEXT,
            varian_snapshot TEXT,
            warna_snapshot TEXT,
            ukuran_snapshot TEXT,
            satuan_snapshot TEXT,

            qty INTEGER NOT NULL DEFAULT 1,
            keterangan TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (delivery_order_id)
                REFERENCES delivery_orders (id)
                ON DELETE CASCADE,

            FOREIGN KEY (transaction_item_id)
                REFERENCES sales_transaction_items (id)
                ON DELETE SET NULL,

            FOREIGN KEY (product_id)
                REFERENCES products (id)
                ON DELETE SET NULL
        )
        """
    )

    ensure_column(
        conn,
        "delivery_order_items",
        "delivery_order_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "transaction_item_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "product_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "kode_produk_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "nama_produk_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "kategori_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "brand_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "varian_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "warna_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "ukuran_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "satuan_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "qty",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "keterangan",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_items",
        "created_at",
        "TIMESTAMP",
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS delivery_order_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            delivery_order_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_by TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (delivery_order_id)
                REFERENCES delivery_orders (id)
                ON DELETE CASCADE
        )
        """
    )

    ensure_column(
        conn,
        "delivery_order_activities",
        "delivery_order_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "delivery_order_activities",
        "activity_type",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_activities",
        "description",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_activities",
        "created_by",
        "TEXT",
    )
    ensure_column(
        conn,
        "delivery_order_activities",
        "created_at",
        "TIMESTAMP",
    )

    cursor.execute(
        """
        UPDATE delivery_orders
        SET status = 'Draft'
        WHERE status IS NULL
           OR TRIM(status) = ''
        """
    )


    # ==========================================================
    # RECEIPTS / KWITANSI PEMBAYARAN
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            nomor_kwitansi TEXT NOT NULL UNIQUE,
            invoice_id INTEGER NOT NULL,
            transaction_id INTEGER NOT NULL,

            tanggal TEXT NOT NULL,
            jenis_pembayaran TEXT NOT NULL,
            metode_pembayaran TEXT NOT NULL,
            bank TEXT,
            nomor_referensi TEXT,

            nominal INTEGER NOT NULL DEFAULT 0,
            untuk_pembayaran TEXT NOT NULL,
            catatan TEXT,

            status TEXT NOT NULL DEFAULT 'Diterbitkan',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (invoice_id)
                REFERENCES sales_invoices (id)
                ON DELETE CASCADE,

            FOREIGN KEY (transaction_id)
                REFERENCES sales_transactions (id)
                ON DELETE CASCADE
        )
        """
    )

    ensure_column(conn, "payment_receipts", "nomor_kwitansi", "TEXT")
    ensure_column(conn, "payment_receipts", "invoice_id", "INTEGER")
    ensure_column(conn, "payment_receipts", "transaction_id", "INTEGER")
    ensure_column(conn, "payment_receipts", "tanggal", "TEXT")
    ensure_column(conn, "payment_receipts", "jenis_pembayaran", "TEXT")
    ensure_column(conn, "payment_receipts", "metode_pembayaran", "TEXT")
    ensure_column(conn, "payment_receipts", "bank", "TEXT")
    ensure_column(conn, "payment_receipts", "nomor_referensi", "TEXT")
    ensure_column(
        conn,
        "payment_receipts",
        "nominal",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(conn, "payment_receipts", "untuk_pembayaran", "TEXT")
    ensure_column(conn, "payment_receipts", "catatan", "TEXT")
    ensure_column(
        conn,
        "payment_receipts",
        "status",
        "TEXT NOT NULL DEFAULT 'Diterbitkan'",
    )
    ensure_column(conn, "payment_receipts", "created_at", "TIMESTAMP")
    ensure_column(conn, "payment_receipts", "updated_at", "TIMESTAMP")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_receipt_activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            description TEXT NOT NULL,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (receipt_id)
                REFERENCES payment_receipts (id)
                ON DELETE CASCADE
        )
        """
    )

    ensure_column(
        conn,
        "payment_receipt_activities",
        "receipt_id",
        "INTEGER",
    )
    ensure_column(
        conn,
        "payment_receipt_activities",
        "activity_type",
        "TEXT",
    )
    ensure_column(
        conn,
        "payment_receipt_activities",
        "description",
        "TEXT",
    )
    ensure_column(
        conn,
        "payment_receipt_activities",
        "created_by",
        "TEXT",
    )
    ensure_column(
        conn,
        "payment_receipt_activities",
        "created_at",
        "TIMESTAMP",
    )

    cursor.execute(
        """
        UPDATE payment_receipts
        SET status = 'Diterbitkan'
        WHERE status IS NULL
           OR TRIM(status) = ''
        """
    )


    # ==========================================================
    # ERP SETTINGS & OPTIONAL INVENTORY
    # ==========================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS erp_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            inventory_enabled INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    ensure_column(conn, "erp_settings", "inventory_enabled", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(
        conn,
        "erp_settings",
        "po_show_cost_details",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "erp_settings",
        "po_show_company_footer",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(conn, "erp_settings", "created_at", "TIMESTAMP")
    ensure_column(conn, "erp_settings", "updated_at", "TIMESTAMP")
    cursor.execute("INSERT OR IGNORE INTO erp_settings (id, inventory_enabled) VALUES (1, 0)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS warehouses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kode_gudang TEXT NOT NULL UNIQUE,
            nama_gudang TEXT NOT NULL,
            alamat TEXT,
            penanggung_jawab TEXT,
            aktif INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    ensure_column(conn, "warehouses", "kode_gudang", "TEXT")
    ensure_column(conn, "warehouses", "nama_gudang", "TEXT")
    ensure_column(conn, "warehouses", "alamat", "TEXT")
    ensure_column(conn, "warehouses", "penanggung_jawab", "TEXT")
    ensure_column(conn, "warehouses", "aktif", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "warehouses", "created_at", "TIMESTAMP")
    ensure_column(conn, "warehouses", "updated_at", "TIMESTAMP")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            warehouse_id INTEGER NOT NULL,
            stok INTEGER NOT NULL DEFAULT 0,
            minimum_stok INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id, warehouse_id),
            FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE,
            FOREIGN KEY (warehouse_id) REFERENCES warehouses (id) ON DELETE CASCADE
        )
    """)
    ensure_column(conn, "product_stock", "product_id", "INTEGER")
    ensure_column(conn, "product_stock", "warehouse_id", "INTEGER")
    ensure_column(conn, "product_stock", "stok", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "product_stock", "minimum_stok", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "product_stock", "updated_at", "TIMESTAMP")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tanggal TEXT NOT NULL,
            warehouse_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            movement_type TEXT NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            saldo_setelah INTEGER NOT NULL DEFAULT 0,
            referensi TEXT,
            catatan TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (warehouse_id) REFERENCES warehouses (id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE CASCADE
        )
    """)
    ensure_column(conn, "stock_movements", "tanggal", "TEXT")
    ensure_column(conn, "stock_movements", "warehouse_id", "INTEGER")
    ensure_column(conn, "stock_movements", "product_id", "INTEGER")
    ensure_column(conn, "stock_movements", "movement_type", "TEXT")
    ensure_column(conn, "stock_movements", "qty", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "stock_movements", "saldo_setelah", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "stock_movements", "referensi", "TEXT")
    ensure_column(conn, "stock_movements", "catatan", "TEXT")
    ensure_column(conn, "stock_movements", "created_at", "TIMESTAMP")


    # ==========================================================
    # SPRINT 10.0 — COMPANY PROFILE
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS company_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            nama_perusahaan TEXT NOT NULL DEFAULT 'PT Ahsa Cahaya Persada',
            nama_brand TEXT NOT NULL DEFAULT 'Ahsa Equipment',
            alamat TEXT,
            kota TEXT,
            provinsi TEXT,
            kode_pos TEXT,
            telepon TEXT,
            whatsapp TEXT,
            email TEXT,
            website TEXT,
            npwp TEXT,
            bank TEXT,
            no_rekening TEXT,
            atas_nama TEXT,
            logo_path TEXT,
            footer_invoice TEXT,
            footer_quotation TEXT,
            footer_purchase_order TEXT,
            footer_delivery_order TEXT,
            footer_receipt TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    company_profile_columns = {
        "nama_perusahaan": "TEXT NOT NULL DEFAULT 'PT Ahsa Cahaya Persada'",
        "nama_brand": "TEXT NOT NULL DEFAULT 'Ahsa Equipment'",
        "alamat": "TEXT",
        "kota": "TEXT",
        "provinsi": "TEXT",
        "kode_pos": "TEXT",
        "telepon": "TEXT",
        "whatsapp": "TEXT",
        "email": "TEXT",
        "website": "TEXT",
        "npwp": "TEXT",
        "bank": "TEXT",
        "no_rekening": "TEXT",
        "atas_nama": "TEXT",
        "logo_path": "TEXT",
        "footer_invoice": "TEXT",
        "footer_quotation": "TEXT",
        "footer_purchase_order": "TEXT",
        "footer_delivery_order": "TEXT",
        "footer_receipt": "TEXT",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    for column_name, column_definition in company_profile_columns.items():
        ensure_column(
            conn,
            "company_profile",
            column_name,
            column_definition,
        )

    cursor.execute(
        """
        INSERT OR IGNORE INTO company_profile (
            id,
            nama_perusahaan,
            nama_brand,
            alamat,
            kota,
            provinsi,
            telepon,
            whatsapp,
            email,
            website,
            bank,
            atas_nama
        )
        VALUES (
            1,
            'PT Ahsa Cahaya Persada',
            'Ahsa Equipment',
            'Kp. Jati, Desa Dangdeur, Kecamatan Banyuresmi',
            'Garut',
            'Jawa Barat',
            '',
            '082117126895',
            '',
            'distributordalton.com',
            'BCA',
            'PT Ahsa Cahaya Persada'
        )
        """
    )

    # ==========================================================
    # SPRINT 6 — COMPANY IDENTITIES
    # company_identities adalah single source of truth.
    # company_profile dipertahankan sebagai compatibility layer
    # untuk instalasi lama dan tidak lagi dibaca oleh aplikasi.
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS company_identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            identity_type TEXT NOT NULL
                CHECK (identity_type IN ('FULL', 'QUOTATION_ONLY')),
            is_default INTEGER NOT NULL DEFAULT 0
                CHECK (is_default IN (0, 1)),

            nama_perusahaan TEXT NOT NULL,
            nama_brand TEXT NOT NULL,
            alamat TEXT,
            kota TEXT,
            provinsi TEXT,
            kode_pos TEXT,
            telepon TEXT,
            whatsapp TEXT,
            email TEXT,
            website TEXT,
            npwp TEXT,

            bank TEXT,
            no_rekening TEXT,
            atas_nama TEXT,

            logo_path TEXT NOT NULL,
            signature_path TEXT,
            signature_name TEXT,
            signature_title TEXT,
            signature_email TEXT,

            footer_invoice TEXT,
            footer_quotation TEXT,
            footer_purchase_order TEXT,
            footer_delivery_order TEXT,
            footer_receipt TEXT,

            allow_qr INTEGER NOT NULL DEFAULT 0
                CHECK (allow_qr IN (0, 1)),
            allow_signature INTEGER NOT NULL DEFAULT 0
                CHECK (allow_signature IN (0, 1)),
            allow_website_footer INTEGER NOT NULL DEFAULT 0
                CHECK (allow_website_footer IN (0, 1)),
            allow_transaction_conversion INTEGER NOT NULL DEFAULT 0
                CHECK (allow_transaction_conversion IN (0, 1)),
            active INTEGER NOT NULL DEFAULT 1
                CHECK (active IN (0, 1)),

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    company_identity_columns = {
        "code": "TEXT",
        "identity_type": "TEXT NOT NULL DEFAULT 'QUOTATION_ONLY'",
        "is_default": "INTEGER NOT NULL DEFAULT 0",
        "nama_perusahaan": "TEXT",
        "nama_brand": "TEXT",
        "alamat": "TEXT",
        "kota": "TEXT",
        "provinsi": "TEXT",
        "kode_pos": "TEXT",
        "telepon": "TEXT",
        "whatsapp": "TEXT",
        "email": "TEXT",
        "website": "TEXT",
        "npwp": "TEXT",
        "bank": "TEXT",
        "no_rekening": "TEXT",
        "atas_nama": "TEXT",
        "logo_path": "TEXT",
        "signature_path": "TEXT",
        "signature_name": "TEXT",
        "signature_title": "TEXT",
        "signature_email": "TEXT",
        "footer_invoice": "TEXT",
        "footer_quotation": "TEXT",
        "footer_purchase_order": "TEXT",
        "footer_delivery_order": "TEXT",
        "footer_receipt": "TEXT",
        "allow_qr": "INTEGER NOT NULL DEFAULT 0",
        "allow_signature": "INTEGER NOT NULL DEFAULT 0",
        "allow_website_footer": "INTEGER NOT NULL DEFAULT 0",
        "allow_transaction_conversion": "INTEGER NOT NULL DEFAULT 0",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    for column_name, column_definition in company_identity_columns.items():
        ensure_column(
            conn,
            "company_identities",
            column_name,
            column_definition,
        )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_company_identities_single_default
        ON company_identities (is_default)
        WHERE is_default = 1
        """
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO company_identities (
            code,
            identity_type,
            is_default,
            nama_perusahaan,
            nama_brand,
            alamat,
            kota,
            provinsi,
            kode_pos,
            telepon,
            whatsapp,
            email,
            website,
            npwp,
            bank,
            no_rekening,
            atas_nama,
            logo_path,
            signature_path,
            signature_name,
            footer_invoice,
            footer_quotation,
            footer_purchase_order,
            footer_delivery_order,
            footer_receipt,
            allow_qr,
            allow_signature,
            allow_website_footer,
            allow_transaction_conversion,
            active
        )
        SELECT
            'AHSA',
            'FULL',
            1,
            COALESCE(NULLIF(TRIM(nama_perusahaan), ''),
                     'PT Ahsa Cahaya Persada'),
            COALESCE(NULLIF(TRIM(nama_brand), ''), 'Ahsa Equipment'),
            alamat,
            kota,
            provinsi,
            kode_pos,
            telepon,
            whatsapp,
            email,
            COALESCE(NULLIF(TRIM(website), ''),
                     'distributordalton.com'),
            npwp,
            COALESCE(NULLIF(TRIM(bank), ''), 'BCA'),
            COALESCE(NULLIF(TRIM(no_rekening), ''), '1483353085'),
            COALESCE(NULLIF(TRIM(atas_nama), ''),
                     'PT Ahsa Cahaya Persada'),
            'images/logo-ahsa.png',
            'images/logo-ahsa.png',
            'Luki Lukmanul Hakim',
            footer_invoice,
            footer_quotation,
            footer_purchase_order,
            footer_delivery_order,
            footer_receipt,
            1,
            1,
            1,
            1,
            1
        FROM company_profile
        WHERE id = 1
        """
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO company_identities (
            code,
            identity_type,
            is_default,
            nama_perusahaan,
            nama_brand,
            alamat,
            kota,
            provinsi,
            kode_pos,
            telepon,
            whatsapp,
            email,
            website,
            bank,
            no_rekening,
            atas_nama,
            logo_path,
            signature_path,
            signature_name,
            signature_title,
            signature_email,
            footer_quotation,
            allow_qr,
            allow_signature,
            allow_website_footer,
            allow_transaction_conversion,
            active
        )
        VALUES (
            'DENKO',
            'QUOTATION_ONLY',
            0,
            'PT Denko Wahana Sakti',
            'Denko',
            'Kantor Cabang Bandung\nKawasan Industri De Prima Terra Blok E2/11\nJl. Raya Sapan\nBojongsoang',
            'Kabupaten Bandung',
            'Jawa Barat',
            '40288',
            '',
            '082117126895',
            'luki@denko.co.id',
            'https://www.handliftbandung.com',
            'BCA Cab. Metro Trade Center',
            '6395758989',
            'PT Denko Wahana Sakti',
            'images/denko_logo.png',
            'images/signature_denko.png',
            'Luki Lukmanul Hakim',
            'Sales Executive',
            'luki@denko.co.id',
            'PT Denko Wahana Sakti',
            0,
            1,
            0,
            0,
            1
        )
        """
    )

    # Lengkapi seed stub Denko dari Sprint 6 awal satu kali. Kondisi ini
    # menjaga profile resmi yang sudah pernah disunting agar tidak ditimpa
    # kembali pada setiap startup aplikasi.
    cursor.execute(
        """
        UPDATE company_identities
        SET alamat = ?,
            kota = ?,
            provinsi = ?,
            kode_pos = ?,
            whatsapp = ?,
            email = ?,
            website = ?,
            bank = ?,
            no_rekening = ?,
            atas_nama = ?,
            signature_path = ?,
            signature_name = ?,
            signature_title = ?,
            signature_email = ?,
            allow_qr = 0,
            allow_signature = 1,
            allow_website_footer = 0,
            allow_transaction_conversion = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE code = 'DENKO'
          AND COALESCE(TRIM(website), '') = ''
          AND COALESCE(TRIM(signature_path), '') = ''
        """,
        (
            "Kantor Cabang Bandung\n"
            "Kawasan Industri De Prima Terra Blok E2/11\n"
            "Jl. Raya Sapan\n"
            "Bojongsoang",
            "Kabupaten Bandung",
            "Jawa Barat",
            "40288",
            "082117126895",
            "luki@denko.co.id",
            "https://www.handliftbandung.com",
            "BCA Cab. Metro Trade Center",
            "6395758989",
            "PT Denko Wahana Sakti",
            "images/signature_denko.png",
            "Luki Lukmanul Hakim",
            "Sales Executive",
            "luki@denko.co.id",
        ),
    )

    # ==========================================================
    # SPRINT 10.0 — DOCUMENT NUMBERING ENGINE
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_numbering (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_type TEXT NOT NULL UNIQUE,
            document_name TEXT NOT NULL,
            prefix TEXT NOT NULL,
            separator TEXT NOT NULL DEFAULT '/',
            include_year INTEGER NOT NULL DEFAULT 1,
            include_month INTEGER NOT NULL DEFAULT 1,
            running_length INTEGER NOT NULL DEFAULT 6,
            current_year INTEGER,
            current_month INTEGER,
            last_number INTEGER NOT NULL DEFAULT 0,
            reset_policy TEXT NOT NULL DEFAULT 'MONTHLY',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    numbering_columns = {
        "document_name": "TEXT",
        "prefix": "TEXT",
        "separator": "TEXT NOT NULL DEFAULT '/'",
        "include_year": "INTEGER NOT NULL DEFAULT 1",
        "include_month": "INTEGER NOT NULL DEFAULT 1",
        "running_length": "INTEGER NOT NULL DEFAULT 6",
        "current_year": "INTEGER",
        "current_month": "INTEGER",
        "last_number": "INTEGER NOT NULL DEFAULT 0",
        "reset_policy": "TEXT NOT NULL DEFAULT 'MONTHLY'",
        "active": "INTEGER NOT NULL DEFAULT 1",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    for column_name, column_definition in numbering_columns.items():
        ensure_column(
            conn,
            "document_numbering",
            column_name,
            column_definition,
        )

    default_numbering = (
        ("INVOICE", "Invoice Penjualan", "INV", "/", 1, 1, 6, "MONTHLY"),
        ("QUOTATION", "Quotation / Penawaran", "QTN", "/", 1, 1, 6, "MONTHLY"),
        ("DELIVERY_ORDER", "Surat Jalan", "SJ", "/", 1, 1, 6, "MONTHLY"),
        ("PURCHASE_ORDER", "Purchase Order", "PO", "/", 1, 1, 6, "MONTHLY"),
        ("RECEIPT", "Kwitansi", "KWT", "/", 1, 1, 6, "MONTHLY"),
        ("TRANSACTION", "Transaksi Penjualan", "TRX", "/", 1, 1, 6, "MONTHLY"),
        ("CUSTOMER", "Kode Customer", "CST", "-", 0, 0, 6, "NEVER"),
        ("SUPPLIER", "Kode Supplier", "SUP", "-", 0, 0, 6, "NEVER")
    )

    cursor.executemany(
        """
        INSERT OR IGNORE INTO document_numbering (
            document_type,
            document_name,
            prefix,
            separator,
            include_year,
            include_month,
            running_length,
            reset_policy
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        default_numbering,
    )


    # ==========================================================
    # SPRINT 10.1 — MIGRASI MASTER SUPPLIER LAMA
    # ==========================================================
    supplier_columns = {
        "kode_supplier": "TEXT",
        "nama": "TEXT",
        "nama_supplier": "TEXT",
        "jenis_supplier": "TEXT NOT NULL DEFAULT 'Distributor'",
        "alamat": "TEXT",
        "kota": "TEXT",
        "provinsi": "TEXT",
        "kode_pos": "TEXT",
        "pic": "TEXT",
        "jabatan": "TEXT",
        "kontak": "TEXT",
        "telepon": "TEXT",
        "whatsapp": "TEXT",
        "email": "TEXT",
        "website": "TEXT",
        "npwp": "TEXT",
        "bank": "TEXT",
        "no_rekening": "TEXT",
        "atas_nama": "TEXT",
        "payment_term": "INTEGER NOT NULL DEFAULT 0",
        "status": "TEXT NOT NULL DEFAULT 'Aktif'",
        "status_aktif": "INTEGER NOT NULL DEFAULT 1",
        "catatan": "TEXT",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    for column_name, column_definition in supplier_columns.items():
        ensure_column(
            conn,
            "suppliers",
            column_name,
            column_definition,
        )

    cursor.execute(
        """
        UPDATE suppliers
        SET nama_supplier = COALESCE(
                NULLIF(TRIM(nama_supplier), ''),
                NULLIF(TRIM(nama), ''),
                'Supplier Tanpa Nama'
            ),
            nama = COALESCE(
                NULLIF(TRIM(nama), ''),
                NULLIF(TRIM(nama_supplier), ''),
                'Supplier Tanpa Nama'
            ),
            whatsapp = COALESCE(
                NULLIF(TRIM(whatsapp), ''),
                NULLIF(TRIM(kontak), '')
            ),
            status = CASE
                WHEN status_aktif = 0 THEN 'Nonaktif'
                ELSE COALESCE(NULLIF(TRIM(status), ''), 'Aktif')
            END,
            updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
        """
    )

    supplier_rows_without_code = cursor.execute(
        """
        SELECT id
        FROM suppliers
        WHERE kode_supplier IS NULL
           OR TRIM(kode_supplier) = ''
        ORDER BY id
        """
    ).fetchall()

    for supplier_row in supplier_rows_without_code:
        cursor.execute(
            """
            UPDATE suppliers
            SET kode_supplier = ?
            WHERE id = ?
            """,
            (
                f"SUP-{int(supplier_row['id']):06d}",
                supplier_row["id"],
            ),
        )

    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_suppliers_code_unique
        ON suppliers (kode_supplier)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_suppliers_name
        ON suppliers (nama_supplier)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_suppliers_status
        ON suppliers (status)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_suppliers_city
        ON suppliers (kota)
        """
    )

    supplier_max_id = cursor.execute(
        """
        SELECT COALESCE(MAX(id), 0) AS max_id
        FROM suppliers
        """
    ).fetchone()["max_id"]

    cursor.execute(
        """
        UPDATE document_numbering
        SET last_number = CASE
                WHEN last_number < ? THEN ?
                ELSE last_number
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE document_type = 'SUPPLIER'
        """,
        (
            supplier_max_id,
            supplier_max_id,
        ),
    )


    # ==========================================================
    # SPRINT 10.2 — PURCHASE ORDER
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nomor_po TEXT NOT NULL UNIQUE,
            supplier_id INTEGER NOT NULL,
            invoice_id INTEGER,
            transaction_id INTEGER,
            tanggal TEXT NOT NULL,
            estimasi_datang TEXT,
            status TEXT NOT NULL DEFAULT 'Draft',
            supplier_nama_snapshot TEXT,
            supplier_alamat_snapshot TEXT,
            supplier_pic_snapshot TEXT,
            supplier_whatsapp_snapshot TEXT,
            supplier_email_snapshot TEXT,
            supplier_npwp_snapshot TEXT,
            payment_term INTEGER NOT NULL DEFAULT 0,
            subtotal INTEGER NOT NULL DEFAULT 0,
            diskon INTEGER NOT NULL DEFAULT 0,
            ppn_persen REAL NOT NULL DEFAULT 0,
            ppn_nilai INTEGER NOT NULL DEFAULT 0,
            ongkir INTEGER NOT NULL DEFAULT 0,
            biaya_lain INTEGER NOT NULL DEFAULT 0,
            grand_total INTEGER NOT NULL DEFAULT 0,
            catatan TEXT,
            syarat_ketentuan TEXT,
            dikirim_pada TIMESTAMP,
            selesai_pada TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
            FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id),
            FOREIGN KEY (transaction_id) REFERENCES sales_transactions(id)
        )
        """
    )

    purchase_order_columns = {
        "invoice_id": "INTEGER",
        "transaction_id": "INTEGER",
        "estimasi_datang": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'Draft'",
        "supplier_nama_snapshot": "TEXT",
        "supplier_alamat_snapshot": "TEXT",
        "supplier_pic_snapshot": "TEXT",
        "supplier_whatsapp_snapshot": "TEXT",
        "supplier_email_snapshot": "TEXT",
        "supplier_npwp_snapshot": "TEXT",
        "payment_term": "INTEGER NOT NULL DEFAULT 0",
        "subtotal": "INTEGER NOT NULL DEFAULT 0",
        "diskon": "INTEGER NOT NULL DEFAULT 0",
        "ppn_persen": "REAL NOT NULL DEFAULT 0",
        "ppn_nilai": "INTEGER NOT NULL DEFAULT 0",
        "ongkir": "INTEGER NOT NULL DEFAULT 0",
        "biaya_lain": "INTEGER NOT NULL DEFAULT 0",
        "grand_total": "INTEGER NOT NULL DEFAULT 0",
        "catatan": "TEXT",
        "syarat_ketentuan": "TEXT",
        "dikirim_pada": "TIMESTAMP",
        "selesai_pada": "TIMESTAMP",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }

    for column_name, column_definition in purchase_order_columns.items():
        ensure_column(
            conn,
            "purchase_orders",
            column_name,
            column_definition,
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_order_id INTEGER NOT NULL,
            product_id INTEGER,
            kode_produk_snapshot TEXT,
            nama_produk_snapshot TEXT NOT NULL,
            deskripsi_snapshot TEXT,
            satuan_snapshot TEXT,
            qty REAL NOT NULL DEFAULT 1,
            harga_satuan INTEGER NOT NULL DEFAULT 0,
            diskon_persen REAL NOT NULL DEFAULT 0,
            diskon_nilai INTEGER NOT NULL DEFAULT 0,
            subtotal INTEGER NOT NULL DEFAULT 0,
            urutan INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (purchase_order_id)
                REFERENCES purchase_orders(id)
                ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
        """
    )

    purchase_order_item_columns = {
        "product_id": "INTEGER",
        "kode_produk_snapshot": "TEXT",
        "nama_produk_snapshot": "TEXT",
        "deskripsi_snapshot": "TEXT",
        "satuan_snapshot": "TEXT",
        "qty": "REAL NOT NULL DEFAULT 1",
        "harga_satuan": "INTEGER NOT NULL DEFAULT 0",
        "diskon_persen": "REAL NOT NULL DEFAULT 0",
        "diskon_nilai": "INTEGER NOT NULL DEFAULT 0",
        "subtotal": "INTEGER NOT NULL DEFAULT 0",
        "urutan": "INTEGER NOT NULL DEFAULT 0",
        "created_at": "TIMESTAMP",
    }

    for column_name, column_definition in purchase_order_item_columns.items():
        ensure_column(
            conn,
            "purchase_order_items",
            column_name,
            column_definition,
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS purchase_order_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_order_id INTEGER NOT NULL,
            jenis_file TEXT,
            nama_file TEXT NOT NULL,
            path_file TEXT NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (purchase_order_id)
                REFERENCES purchase_orders(id)
                ON DELETE CASCADE
        )
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_purchase_orders_supplier
        ON purchase_orders (supplier_id)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_purchase_orders_status
        ON purchase_orders (status)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_purchase_orders_date
        ON purchase_orders (tanggal)
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_purchase_order_items_po
        ON purchase_order_items (purchase_order_id)
        """
    )

    # ==========================================================
    # INVOICE PENJUALAN
    # Satu transaksi memiliki maksimal satu invoice aktif.
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_id INTEGER NOT NULL UNIQUE,
            nomor_invoice TEXT NOT NULL UNIQUE,
            tanggal_invoice TEXT NOT NULL,
            jatuh_tempo TEXT,
            status_pembayaran TEXT NOT NULL DEFAULT 'Belum Lunas',
            catatan TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (transaction_id)
                REFERENCES sales_transactions (id)
                ON DELETE CASCADE
        )
        """
    )

    ensure_column(conn, "sales_invoices", "transaction_id", "INTEGER")
    ensure_column(conn, "sales_invoices", "nomor_invoice", "TEXT")
    ensure_column(conn, "sales_invoices", "tanggal_invoice", "TEXT")
    ensure_column(conn, "sales_invoices", "jatuh_tempo", "TEXT")
    ensure_column(
        conn,
        "sales_invoices",
        "status_pembayaran",
        "TEXT NOT NULL DEFAULT 'Belum Lunas'",
    )
    ensure_column(conn, "sales_invoices", "catatan", "TEXT")
    ensure_column(
        conn,
        "sales_invoices",
        "purchase_order_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "sales_invoices",
        "jumlah_dibayar",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_invoices",
        "dp_persen",
        "REAL NOT NULL DEFAULT 0",
    )
    ensure_column(conn, "sales_invoices", "created_at", "TIMESTAMP")
    ensure_column(conn, "sales_invoices", "updated_at", "TIMESTAMP")

    # ==========================================================
    # ITEM TRANSAKSI
    # Snapshot menjaga histori harga dan detail produk lama.
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_transaction_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            transaction_id INTEGER NOT NULL,
            product_id INTEGER,

            kode_produk_snapshot TEXT,
            nama_produk_snapshot TEXT NOT NULL,
            kategori_snapshot TEXT,
            brand_snapshot TEXT,
            varian_snapshot TEXT,
            warna_snapshot TEXT,
            ukuran_snapshot TEXT,
            satuan_snapshot TEXT,

            qty INTEGER NOT NULL DEFAULT 1,

            harga_jual_satuan INTEGER NOT NULL DEFAULT 0,
            subtotal_penjualan INTEGER NOT NULL DEFAULT 0,

            harga_modal_satuan INTEGER NOT NULL DEFAULT 0,
            subtotal_modal INTEGER NOT NULL DEFAULT 0,

            margin_item INTEGER NOT NULL DEFAULT 0,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (transaction_id)
                REFERENCES sales_transactions (id)
                ON DELETE CASCADE,

            FOREIGN KEY (product_id)
                REFERENCES products (id)
                ON DELETE SET NULL
        )
        """
    )

    # ==========================================================
    # MIGRASI KOLOM SALES_TRANSACTION_ITEMS
    # ==========================================================
    ensure_column(
        conn,
        "sales_transaction_items",
        "transaction_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "product_id",
        "INTEGER",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "kode_produk_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "nama_produk_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "kategori_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "brand_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "varian_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "warna_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "ukuran_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "satuan_snapshot",
        "TEXT",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "qty",
        "INTEGER NOT NULL DEFAULT 1",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "harga_jual_satuan",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "subtotal_penjualan",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "harga_modal_satuan",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "subtotal_modal",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "margin_item",
        "INTEGER NOT NULL DEFAULT 0",
    )

    ensure_column(
        conn,
        "sales_transaction_items",
        "created_at",
        "TIMESTAMP",
    )


    # ==========================================================
    # QUOTATION / PENAWARAN
    # Tax snapshot disimpan per quotation agar reprint konsisten dan
    # quotation legacy tetap backward compatible.
    # ==========================================================
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_quotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            nomor_penawaran TEXT NOT NULL UNIQUE,
            customer_id INTEGER,
            tanggal TEXT NOT NULL,
            berlaku_sampai TEXT,

            sales TEXT,
            status TEXT NOT NULL DEFAULT 'Draft',
            revisi INTEGER NOT NULL DEFAULT 0,

            subtotal INTEGER NOT NULL DEFAULT 0,
            diskon INTEGER NOT NULL DEFAULT 0,
            is_ppn INTEGER NOT NULL DEFAULT 0
                CHECK (is_ppn IN (0, 1)),
            ppn_rate INTEGER NOT NULL DEFAULT 0
                CHECK (ppn_rate >= 0),
            dpp INTEGER NOT NULL DEFAULT 0
                CHECK (dpp >= 0),
            ppn_amount INTEGER NOT NULL DEFAULT 0
                CHECK (ppn_amount >= 0),
            grand_total INTEGER NOT NULL DEFAULT 0,

            catatan TEXT,
            syarat_ketentuan TEXT,

            show_discount INTEGER NOT NULL DEFAULT 1,
            show_terbilang INTEGER NOT NULL DEFAULT 1,
            show_qr INTEGER NOT NULL DEFAULT 1,
            show_catatan INTEGER NOT NULL DEFAULT 1,
            show_terms INTEGER NOT NULL DEFAULT 1,
            show_bank INTEGER NOT NULL DEFAULT 1,
            show_signature INTEGER NOT NULL DEFAULT 1,
            show_footer INTEGER NOT NULL DEFAULT 1,
            auto_hide_zero INTEGER NOT NULL DEFAULT 1,

            identity_id INTEGER,
            converted_transaction_id INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (customer_id)
                REFERENCES customers (id)
                ON DELETE SET NULL,

            FOREIGN KEY (converted_transaction_id)
                REFERENCES sales_transactions (id)
                ON DELETE SET NULL,

            FOREIGN KEY (identity_id)
                REFERENCES company_identities (id)
                ON UPDATE RESTRICT
                ON DELETE RESTRICT
        )
        """
    )

    ensure_column(conn, "sales_quotations", "nomor_penawaran", "TEXT")
    ensure_column(conn, "sales_quotations", "customer_id", "INTEGER")
    ensure_column(conn, "sales_quotations", "tanggal", "TEXT")
    ensure_column(conn, "sales_quotations", "berlaku_sampai", "TEXT")
    ensure_column(conn, "sales_quotations", "sales", "TEXT")
    ensure_column(
        conn,
        "sales_quotations",
        "status",
        "TEXT NOT NULL DEFAULT 'Draft'",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "revisi",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "subtotal",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "diskon",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "is_ppn",
        "INTEGER NOT NULL DEFAULT 0 CHECK (is_ppn IN (0, 1))",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "ppn_rate",
        "INTEGER NOT NULL DEFAULT 0 CHECK (ppn_rate >= 0)",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "dpp",
        "INTEGER NOT NULL DEFAULT 0 CHECK (dpp >= 0)",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "ppn_amount",
        "INTEGER NOT NULL DEFAULT 0 CHECK (ppn_amount >= 0)",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "grand_total",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(conn, "sales_quotations", "catatan", "TEXT")
    ensure_column(conn, "sales_quotations", "syarat_ketentuan", "TEXT")
    ensure_column(
        conn,
        "sales_quotations",
        "show_discount",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_terbilang",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_qr",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_catatan",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_terms",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_bank",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_signature",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "show_footer",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "auto_hide_zero",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotations",
        "identity_id",
        (
            "INTEGER REFERENCES company_identities(id) "
            "ON UPDATE RESTRICT ON DELETE RESTRICT"
        ),
    )
    ensure_column(
        conn,
        "sales_quotations",
        "converted_transaction_id",
        "INTEGER",
    )
    ensure_column(conn, "sales_quotations", "created_at", "TIMESTAMP")
    ensure_column(conn, "sales_quotations", "updated_at", "TIMESTAMP")

    # Semua quotation sebelum hotfix 6.1 dianggap tanpa PPN. DPP dapat
    # diturunkan secara aman dari total existing tanpa mengubah grand_total.
    # Baris dengan is_ppn = 1 adalah snapshot baru dan tidak ditimpa.
    cursor.execute(
        """
        UPDATE sales_quotations
        SET is_ppn = COALESCE(is_ppn, 0),
            ppn_rate = CASE
                WHEN COALESCE(is_ppn, 0) = 0 THEN 0
                ELSE COALESCE(ppn_rate, 0)
            END,
            dpp = CASE
                WHEN COALESCE(is_ppn, 0) = 0 THEN MAX(
                    COALESCE(subtotal, 0) - COALESCE(diskon, 0),
                    0
                )
                ELSE COALESCE(dpp, 0)
            END,
            ppn_amount = CASE
                WHEN COALESCE(is_ppn, 0) = 0 THEN 0
                ELSE COALESCE(ppn_amount, 0)
            END
        """
    )

    cursor.execute(
        """
        UPDATE sales_quotations
        SET identity_id = (
            SELECT id
            FROM company_identities
            WHERE identity_type = 'FULL'
              AND is_default = 1
            LIMIT 1
        )
        WHERE identity_id IS NULL
        """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sales_quotations_identity_id
        ON sales_quotations (identity_id)
        """
    )

    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS
            trg_sales_quotations_identity_insert
        AFTER INSERT ON sales_quotations
        FOR EACH ROW
        WHEN NEW.identity_id IS NULL
        BEGIN
            UPDATE sales_quotations
            SET identity_id = (
                SELECT id
                FROM company_identities
                WHERE identity_type = 'FULL'
                  AND is_default = 1
                LIMIT 1
            )
            WHERE id = NEW.id;
        END
        """
    )

    cursor.execute(
        """
        CREATE TRIGGER IF NOT EXISTS
            trg_sales_quotations_identity_update
        AFTER UPDATE OF identity_id ON sales_quotations
        FOR EACH ROW
        WHEN NEW.identity_id IS NULL
        BEGIN
            UPDATE sales_quotations
            SET identity_id = (
                SELECT id
                FROM company_identities
                WHERE identity_type = 'FULL'
                  AND is_default = 1
                LIMIT 1
            )
            WHERE id = NEW.id;
        END
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_quotation_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            quotation_id INTEGER NOT NULL,
            product_id INTEGER,

            kode_produk_snapshot TEXT,
            nama_produk_snapshot TEXT NOT NULL,
            kategori_snapshot TEXT,
            brand_snapshot TEXT,
            varian_snapshot TEXT,
            warna_snapshot TEXT,
            ukuran_snapshot TEXT,
            satuan_snapshot TEXT,

            qty INTEGER NOT NULL DEFAULT 1,
            harga_satuan INTEGER NOT NULL DEFAULT 0,
            diskon_item INTEGER NOT NULL DEFAULT 0,
            subtotal INTEGER NOT NULL DEFAULT 0,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (quotation_id)
                REFERENCES sales_quotations (id)
                ON DELETE CASCADE,

            FOREIGN KEY (product_id)
                REFERENCES products (id)
                ON DELETE SET NULL
        )
        """
    )

    ensure_column(conn, "sales_quotation_items", "quotation_id", "INTEGER")
    ensure_column(conn, "sales_quotation_items", "product_id", "INTEGER")
    ensure_column(
        conn,
        "sales_quotation_items",
        "kode_produk_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "nama_produk_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "kategori_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "brand_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "varian_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "warna_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "ukuran_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "satuan_snapshot",
        "TEXT",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "qty",
        "INTEGER NOT NULL DEFAULT 1",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "harga_satuan",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "diskon_item",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "subtotal",
        "INTEGER NOT NULL DEFAULT 0",
    )
    ensure_column(
        conn,
        "sales_quotation_items",
        "created_at",
        "TIMESTAMP",
    )

    cursor.execute(
        """
        UPDATE sales_quotations
        SET status = 'Draft'
        WHERE status IS NULL
           OR TRIM(status) = ''
        """
    )

    cursor.execute(
        """
        UPDATE sales_quotations
        SET show_discount = COALESCE(show_discount, 1),
            show_terbilang = COALESCE(show_terbilang, 1),
            show_qr = COALESCE(show_qr, 1),
            show_catatan = COALESCE(show_catatan, 1),
            show_terms = COALESCE(show_terms, 1),
            show_bank = COALESCE(show_bank, 1),
            show_signature = COALESCE(show_signature, 1),
            show_footer = COALESCE(show_footer, 1),
            auto_hide_zero = COALESCE(auto_hide_zero, 1)
        """
    )

    # Mengisi transaksi lama yang belum mempunyai status.
    cursor.execute(
        """
        UPDATE sales_transactions
        SET status = 'Draft'
        WHERE status IS NULL
           OR TRIM(status) = ''
        """
    )

    conn.commit()
    conn.close()
