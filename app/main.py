import base64
import binascii
import hashlib
import hmac
import io
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from customer_import import (
    MAX_IMPORT_BYTES,
    CustomerImportError,
    build_import_report,
    consolidate_customer_records,
    import_customers_atomic,
    parse_customer_workbook,
    prepare_customer_import,
    sha256_bytes,
)
from database import create_tables, get_connection
from product_import import (
    MAX_IMPORT_FILE_SIZE,
    ProductImportError,
    analyze_product_rows,
    import_product_rows,
    parse_product_workbook,
    summarize_rows,
)
from quotation_master import (
    customer_search_result,
    customer_snapshot,
    get_customer_for_quotation,
    get_product_for_quotation,
    product_search_result,
    product_snapshot,
    quotation_item_for_display,
    search_customers,
    search_products,
)
from workflow_integrity import (
    WorkflowIntegrityError,
    normalize_idempotency_key,
    post_opening_stock,
    post_stock_for_document,
    reconcile_invoice_payment,
    record_workflow_event,
    reverse_stock_for_document,
    sync_transaction_status,
    validate_transition,
)

try:
    import qrcode
except ImportError:
    qrcode = None


app = Flask(__name__)

create_tables()






PURCHASE_ORDER_STATUSES = (
    "Draft",
    "Dikirim",
    "Diproses Supplier",
    "Barang Diterima",
    "Selesai",
    "Batal",
)

PURCHASE_ORDER_PPN_OPTIONS = (
    0,
    11,
    12,
)

SUPPLIER_TYPES = (
    "Distributor",
    "Pabrik",
    "Importir",
    "Vendor",
    "Agen",
    "Toko",
    "Jasa",
    "Lainnya",
)

SUPPLIER_STATUSES = (
    "Aktif",
    "Nonaktif",
)

SUPPLIER_PAYMENT_TERMS = (
    0,
    7,
    14,
    21,
    30,
    45,
    60,
    90,
)


TRANSACTION_STATUSES = (
    "Draft",
    "Closing",
    "Invoice",
    "Terkirim",
    "Lunas",
    "Selesai",
    "Batal",
)




RECEIPT_TYPES = (
    "DP",
    "Termin 1",
    "Termin 2",
    "Termin 3",
    "Pelunasan",
    "Pembayaran Invoice",
    "Cash",
    "Lainnya",
)

RECEIPT_METHODS = (
    "Transfer Bank",
    "Tunai",
    "Cek / BG",
    "QRIS",
    "Lainnya",
)

RECEIPT_STATUSES = (
    "Diterbitkan",
    "Void",
)

DELIVERY_ORDER_STATUSES = (
    "Draft",
    "Packing",
    "Siap Kirim",
    "Dalam Pengiriman",
    "Terkirim",
    "Diterima",
    "Batal",
)

DELIVERY_METHODS = (
    "Kirim Sendiri",
    "Ekspedisi",
    "Diambil Customer",
)

QUOTATION_STATUSES = (
    "Draft",
    "Terkirim",
    "Negosiasi",
    "Revisi",
    "Deal",
    "Expired",
    "Batal",
)

IDENTITY_TYPE_FULL = "FULL"
IDENTITY_TYPE_QUOTATION_ONLY = "QUOTATION_ONLY"
QUOTATION_ONLY_PPN_RATE = 11

DOCUMENT_TYPE_QUOTATION = "QUOTATION"
DOCUMENT_TYPE_TRANSACTION = "TRANSACTION"
DOCUMENT_TYPE_INVOICE = "INVOICE"
DOCUMENT_TYPE_DELIVERY_ORDER = "DELIVERY_ORDER"
DOCUMENT_TYPE_RECEIPT = "RECEIPT"
DOCUMENT_TYPE_PURCHASE_ORDER = "PURCHASE_ORDER"

FULL_IDENTITY_DOCUMENT_TYPES = {
    DOCUMENT_TYPE_TRANSACTION,
    DOCUMENT_TYPE_INVOICE,
    DOCUMENT_TYPE_DELIVERY_ORDER,
    DOCUMENT_TYPE_RECEIPT,
    DOCUMENT_TYPE_PURCHASE_ORDER,
}

INVOICE_PAYMENT_STATUSES = (
    "Belum Lunas",
    "DP",
    "Lunas",
    "Batal",
)










# ==========================================================
# SPRINT 10.2 — PURCHASE ORDER HELPERS
# ==========================================================
def parse_decimal(value, default=0.0):
    if value is None:
        return default

    cleaned = str(value).strip().replace(",", ".")

    if not cleaned:
        return default

    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return default


def get_purchase_order_full(conn, purchase_order_id):
    purchase_order = conn.execute(
        """
        SELECT
            purchase_orders.*,
            suppliers.kode_supplier,
            suppliers.nama_supplier AS supplier_master_nama,
            suppliers.jenis_supplier,
            suppliers.bank AS supplier_bank,
            suppliers.no_rekening AS supplier_no_rekening,
            suppliers.atas_nama AS supplier_atas_nama
        FROM purchase_orders
        JOIN suppliers
            ON purchase_orders.supplier_id = suppliers.id
        WHERE purchase_orders.id = ?
        """,
        (purchase_order_id,),
    ).fetchone()

    if purchase_order is None:
        return None, []

    items = conn.execute(
        """
        SELECT *
        FROM purchase_order_items
        WHERE purchase_order_id = ?
        ORDER BY urutan ASC, id ASC
        """,
        (purchase_order_id,),
    ).fetchall()

    return purchase_order, items


def prepare_purchase_order_items(conn, form):
    product_ids = form.getlist("product_id[]")
    names = form.getlist("nama_produk[]")
    descriptions = form.getlist("deskripsi[]")
    units = form.getlist("satuan[]")
    quantities = form.getlist("qty[]")
    prices = form.getlist("harga_satuan[]")
    discounts = form.getlist("diskon_persen[]")

    row_count = max(
        len(product_ids),
        len(names),
        len(descriptions),
        len(units),
        len(quantities),
        len(prices),
        len(discounts),
    )

    prepared_items = []
    subtotal_header = 0

    for index in range(row_count):
        product_id_raw = (
            product_ids[index].strip()
            if index < len(product_ids)
            else ""
        )
        manual_name = (
            names[index].strip()
            if index < len(names)
            else ""
        )
        description = (
            descriptions[index].strip()
            if index < len(descriptions)
            else ""
        )
        manual_unit = (
            units[index].strip()
            if index < len(units)
            else ""
        )
        qty = parse_decimal(
            quantities[index] if index < len(quantities) else 0,
            0,
        )
        unit_price = parse_integer(
            prices[index] if index < len(prices) else 0,
            0,
        )
        discount_percent = parse_decimal(
            discounts[index] if index < len(discounts) else 0,
            0,
        )

        if (
            not product_id_raw
            and not manual_name
            and qty == 0
            and unit_price == 0
        ):
            continue

        if qty <= 0:
            raise ValueError(
                f"Qty item baris ke-{index + 1} harus lebih dari 0."
            )

        if unit_price < 0:
            raise ValueError(
                f"Harga item baris ke-{index + 1} tidak valid."
            )

        discount_percent = min(max(discount_percent, 0), 100)

        product_id = None
        product_code = None
        product_name = manual_name
        product_unit = manual_unit or "Unit"

        if product_id_raw:
            try:
                product_id = int(product_id_raw)
            except ValueError as error:
                raise ValueError(
                    f"Produk baris ke-{index + 1} tidak valid."
                ) from error

            product = get_product_by_id(conn, product_id)

            if product is None:
                raise ValueError(
                    f"Produk baris ke-{index + 1} tidak ditemukan."
                )

            product_code = product["kode_produk"]
            product_name = product_name or product["nama_produk"]
            product_unit = product_unit or product["satuan"] or "Unit"

            if unit_price == 0:
                unit_price = int(
                    product["harga_modal_default"] or 0
                )

        if not product_name:
            raise ValueError(
                f"Nama barang baris ke-{index + 1} wajib diisi."
            )

        gross = round(qty * unit_price)
        discount_value = round(
            gross * discount_percent / 100
        )
        line_subtotal = max(gross - discount_value, 0)
        subtotal_header += line_subtotal

        prepared_items.append(
            {
                "product_id": product_id,
                "kode_produk_snapshot": product_code,
                "nama_produk_snapshot": product_name,
                "deskripsi_snapshot": description or None,
                "satuan_snapshot": product_unit,
                "qty": qty,
                "harga_satuan": unit_price,
                "diskon_persen": discount_percent,
                "diskon_nilai": discount_value,
                "subtotal": line_subtotal,
                "urutan": len(prepared_items) + 1,
            }
        )

    if not prepared_items:
        raise ValueError(
            "Purchase Order wajib memiliki minimal satu item."
        )

    return prepared_items, subtotal_header


def normalize_purchase_order_header(form, subtotal):
    supplier_id = parse_integer(
        form.get("supplier_id"),
        0,
    )
    tanggal = form.get("tanggal", "").strip()
    estimasi_datang = form.get(
        "estimasi_datang",
        "",
    ).strip()
    status = form.get("status", "Draft").strip()
    payment_term = parse_integer(
        form.get("payment_term"),
        0,
    )
    diskon = max(
        parse_integer(form.get("diskon"), 0),
        0,
    )
    ppn_persen = parse_decimal(
        form.get("ppn_persen"),
        0,
    )
    ongkir = max(
        parse_integer(form.get("ongkir"), 0),
        0,
    )
    biaya_lain = max(
        parse_integer(form.get("biaya_lain"), 0),
        0,
    )

    if supplier_id <= 0:
        raise ValueError("Supplier wajib dipilih.")

    if not tanggal:
        raise ValueError("Tanggal PO wajib diisi.")

    if status not in PURCHASE_ORDER_STATUSES:
        status = "Draft"

    if ppn_persen not in PURCHASE_ORDER_PPN_OPTIONS:
        ppn_persen = 0

    dasar_ppn = max(subtotal - diskon, 0)
    ppn_nilai = round(dasar_ppn * ppn_persen / 100)
    grand_total = (
        dasar_ppn
        + ppn_nilai
        + ongkir
        + biaya_lain
    )

    return {
        "supplier_id": supplier_id,
        "tanggal": tanggal,
        "estimasi_datang": estimasi_datang or None,
        "status": status,
        "payment_term": max(payment_term, 0),
        "subtotal": subtotal,
        "diskon": diskon,
        "ppn_persen": ppn_persen,
        "ppn_nilai": ppn_nilai,
        "ongkir": ongkir,
        "biaya_lain": biaya_lain,
        "grand_total": grand_total,
        "catatan": form.get("catatan", "").strip() or None,
        "syarat_ketentuan": form.get(
            "syarat_ketentuan",
            "",
        ).strip() or None,
    }


def purchase_order_qr_text(purchase_order):
    return (
        f"PURCHASE ORDER\n"
        f"No: {purchase_order['nomor_po']}\n"
        f"Supplier: {purchase_order['supplier_nama_snapshot']}\n"
        f"Tanggal: {purchase_order['tanggal']}\n"
        f"Total: {format_rupiah(purchase_order['grand_total'])}\n"
        f"Status: {purchase_order['status']}"
    )


# ==========================================================
# SPRINT 10.1 — SUPPLIER HELPERS
# ==========================================================
def sqlite_table_exists(conn, table_name):
    result = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return result is not None


def normalize_supplier_form(form):
    payment_term = parse_integer(
        form.get("payment_term"),
        0,
    )

    if payment_term not in SUPPLIER_PAYMENT_TERMS:
        payment_term = 0

    status = form.get("status", "Aktif").strip()
    if status not in SUPPLIER_STATUSES:
        status = "Aktif"

    jenis_supplier = form.get(
        "jenis_supplier",
        "Distributor",
    ).strip()

    if jenis_supplier not in SUPPLIER_TYPES:
        jenis_supplier = "Lainnya"

    return {
        "nama_supplier": form.get(
            "nama_supplier",
            "",
        ).strip(),
        "jenis_supplier": jenis_supplier,
        "alamat": form.get("alamat", "").strip(),
        "kota": form.get("kota", "").strip(),
        "provinsi": form.get("provinsi", "").strip(),
        "kode_pos": form.get("kode_pos", "").strip(),
        "pic": form.get("pic", "").strip(),
        "jabatan": form.get("jabatan", "").strip(),
        "telepon": form.get("telepon", "").strip(),
        "whatsapp": form.get("whatsapp", "").strip(),
        "email": form.get("email", "").strip(),
        "website": form.get("website", "").strip(),
        "npwp": form.get("npwp", "").strip(),
        "bank": form.get("bank", "").strip(),
        "no_rekening": form.get(
            "no_rekening",
            "",
        ).strip(),
        "atas_nama": form.get(
            "atas_nama",
            "",
        ).strip(),
        "payment_term": payment_term,
        "status": status,
        "catatan": form.get("catatan", "").strip(),
    }


def supplier_purchase_summary(conn, supplier_id=None):
    summary = {
        "total_po": 0,
        "total_nilai": 0,
        "po_terakhir": None,
        "purchase_orders": [],
    }

    if not sqlite_table_exists(conn, "purchase_orders"):
        return summary

    where_clause = ""
    params = ()

    if supplier_id is not None:
        where_clause = "WHERE supplier_id = ?"
        params = (supplier_id,)

    totals = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_po,
            COALESCE(SUM(grand_total), 0) AS total_nilai,
            MAX(tanggal) AS po_terakhir
        FROM purchase_orders
        {where_clause}
        """,
        params,
    ).fetchone()

    summary["total_po"] = totals["total_po"] or 0
    summary["total_nilai"] = totals["total_nilai"] or 0
    summary["po_terakhir"] = totals["po_terakhir"]

    if supplier_id is not None:
        summary["purchase_orders"] = conn.execute(
            """
            SELECT *
            FROM purchase_orders
            WHERE supplier_id = ?
            ORDER BY tanggal DESC, id DESC
            LIMIT 20
            """,
            (supplier_id,),
        ).fetchall()

    return summary


# ==========================================================
# SPRINT 10.0 — FOUNDATION HELPERS
# ==========================================================
NUMBERING_RESET_POLICIES = (
    "MONTHLY",
    "YEARLY",
    "NEVER",
)


def get_default_full_identity(conn):
    """Mengambil identity FULL utama tanpa bergantung nama atau code."""
    return conn.execute(
        """
        SELECT *
        FROM company_identities
        WHERE identity_type = ?
          AND is_default = 1
        LIMIT 1
        """,
        (IDENTITY_TYPE_FULL,),
    ).fetchone()


def get_company_identity(conn, identity_id, active_only=False):
    """Mengambil identity berdasarkan primary key dengan validasi status."""
    if identity_id is None:
        return None

    query = "SELECT * FROM company_identities WHERE id = ?"
    parameters = [identity_id]

    if active_only:
        query += " AND active = 1"

    return conn.execute(query, parameters).fetchone()


def get_active_quotation_identities(conn):
    """Daftar identity yang dapat dipilih saat membuat quotation."""
    return conn.execute(
        """
        SELECT *
        FROM company_identities
        WHERE active = 1
          AND identity_type IN (?, ?)
        ORDER BY is_default DESC, nama_perusahaan ASC
        """,
        (
            IDENTITY_TYPE_FULL,
            IDENTITY_TYPE_QUOTATION_ONLY,
        ),
    ).fetchall()


def validate_quotation_identity(conn, identity_id):
    """Validasi server-side identity yang dikirim form quotation."""
    try:
        normalized_identity_id = int(identity_id)
    except (TypeError, ValueError):
        raise ValueError("Identity quotation tidak valid.")

    identity = get_company_identity(
        conn,
        normalized_identity_id,
        active_only=True,
    )

    if identity is None or identity["identity_type"] not in (
        IDENTITY_TYPE_FULL,
        IDENTITY_TYPE_QUOTATION_ONLY,
    ):
        raise ValueError("Identity quotation tidak valid atau tidak aktif.")

    return identity


def get_effective_identity(
    document_type,
    quotation_id=None,
    *,
    conn=None,
):
    """
    Menentukan identity efektif untuk seluruh dokumen.

    Quotation mengikuti identity yang tersimpan. Quotation legacy tanpa
    identity otomatis menggunakan identity FULL utama. Semua dokumen
    transaksi selalu menggunakan identity FULL utama.
    """
    close_after = False

    if conn is None:
        conn = get_connection()
        close_after = True

    normalized_document_type = str(document_type or "").strip().upper()

    try:
        if normalized_document_type == DOCUMENT_TYPE_QUOTATION:
            if quotation_id is None:
                raise ValueError(
                    "quotation_id wajib diisi untuk document_type QUOTATION."
                )

            identity = conn.execute(
                """
                SELECT company_identities.*
                FROM sales_quotations
                LEFT JOIN company_identities
                    ON sales_quotations.identity_id = company_identities.id
                WHERE sales_quotations.id = ?
                """,
                (quotation_id,),
            ).fetchone()

            if identity is None or identity["id"] is None:
                identity = get_default_full_identity(conn)

            if identity is None:
                raise RuntimeError(
                    "Identity FULL utama belum tersedia."
                )

            return identity

        if normalized_document_type in FULL_IDENTITY_DOCUMENT_TYPES:
            identity = get_default_full_identity(conn)

            if identity is None:
                raise RuntimeError(
                    "Identity FULL utama belum tersedia."
                )

            return identity

        raise ValueError(
            f"Document type identity tidak didukung: {document_type}."
        )
    finally:
        if close_after:
            conn.close()


def get_effective_tax_settings(identity):
    """Tentukan aturan PPN quotation dari identity_type."""
    identity_type = identity["identity_type"] if identity else None

    if identity_type == IDENTITY_TYPE_QUOTATION_ONLY:
        return {
            "is_ppn": 1,
            "ppn_rate": QUOTATION_ONLY_PPN_RATE,
        }

    if identity_type == IDENTITY_TYPE_FULL:
        return {
            "is_ppn": 0,
            "ppn_rate": 0,
        }

    raise ValueError("Identity quotation tidak mempunyai aturan PPN.")


def calculate_quotation_totals(
    subtotal,
    discount,
    identity,
    *,
    preserve_legacy_no_ppn=False,
):
    """Hitung total quotation dengan aritmetika Rupiah berbasis integer."""
    normalized_subtotal = max(int(subtotal or 0), 0)
    normalized_discount = max(int(discount or 0), 0)
    tax_settings = get_effective_tax_settings(identity)

    if preserve_legacy_no_ppn:
        tax_settings = {
            "is_ppn": 0,
            "ppn_rate": 0,
        }

    dpp = max(normalized_subtotal - normalized_discount, 0)
    ppn_rate = int(tax_settings["ppn_rate"])
    ppn_amount = 0

    if tax_settings["is_ppn"]:
        # Pembulatan half-up ke Rupiah terdekat tanpa operasi float.
        ppn_amount = (dpp * ppn_rate + 50) // 100

    return {
        "subtotal": normalized_subtotal,
        "discount": normalized_discount,
        "dpp": dpp,
        "is_ppn": int(bool(tax_settings["is_ppn"])),
        "ppn_rate": ppn_rate,
        "ppn_amount": ppn_amount,
        "grand_total": dpp + ppn_amount,
    }


def get_effective_quotation_totals(quotation, identity):
    """Normalisasi snapshot total untuk display, termasuk quotation legacy."""
    preserve_legacy_no_ppn = bool(
        identity["identity_type"] == IDENTITY_TYPE_QUOTATION_ONLY
        and not quotation["is_ppn"]
    )

    return calculate_quotation_totals(
        quotation["subtotal"],
        quotation["diskon"],
        identity,
        preserve_legacy_no_ppn=preserve_legacy_no_ppn,
    )


def get_effective_quotation_print_settings(quotation, identity):
    """Gabungkan preferensi quotation dengan capability identity."""
    is_full_identity = (
        identity["identity_type"] == IDENTITY_TYPE_FULL
    )

    return {
        "show_discount": bool(quotation["show_discount"]),
        "show_terbilang": bool(quotation["show_terbilang"]),
        "show_qr": (
            bool(quotation["show_qr"])
            and bool(identity["allow_qr"])
        ),
        "show_catatan": bool(quotation["show_catatan"]),
        "show_terms": bool(quotation["show_terms"]),
        "show_bank": bool(quotation["show_bank"]),
        "show_signature": (
            bool(quotation["show_signature"])
            and bool(identity["allow_signature"])
        ),
        "show_footer": (
            bool(quotation["show_footer"])
            and is_full_identity
        ),
        "show_website_footer": (
            bool(identity["allow_website_footer"])
            and is_full_identity
        ),
    }


def identity_allows_transaction_conversion(identity):
    """Identity FULL dapat memasuki workflow transaksi."""
    return bool(
        identity
        and identity["identity_type"] == IDENTITY_TYPE_FULL
    )


def get_company_profile(conn=None):
    """Compatibility layer deprecated; sumber data tetap identity FULL."""
    close_after = False

    if conn is None:
        conn = get_connection()
        close_after = True

    profile = get_default_full_identity(conn)

    if close_after:
        conn.close()

    return profile


def build_document_number_preview(numbering, preview_number=1):
    """Membuat contoh nomor tanpa mengubah running number."""
    if numbering is None:
        return "-"

    separator = numbering["separator"] or "/"
    prefix = (numbering["prefix"] or "").strip()
    running_length = max(int(numbering["running_length"] or 6), 1)
    now = datetime.now()

    parts = [prefix] if prefix else []

    if int(numbering["include_year"] or 0) == 1:
        parts.append(str(now.year))

    if int(numbering["include_month"] or 0) == 1:
        parts.append(f"{now.month:02d}")

    parts.append(str(max(int(preview_number or 1), 1)).zfill(running_length))

    return separator.join(parts)


def generate_number_from_settings(document_type, conn=None, commit=True):
    """
    Menghasilkan nomor dokumen dan memperbarui running number.

    Helper ini sudah siap dipakai oleh modul PO. Modul lama belum diubah
    agar Invoice, Quotation, SJ, Kwitansi, dan transaksi tetap stabil.
    """
    close_after = False

    if conn is None:
        conn = get_connection()
        close_after = True

    numbering = conn.execute(
        """
        SELECT *
        FROM document_numbering
        WHERE document_type = ?
          AND active = 1
        """,
        (document_type,),
    ).fetchone()

    if numbering is None:
        if close_after:
            conn.close()
        raise ValueError(
            f"Pengaturan nomor dokumen {document_type} tidak ditemukan."
        )

    now = datetime.now()
    last_number = int(numbering["last_number"] or 0)
    reset_policy = (numbering["reset_policy"] or "MONTHLY").upper()
    stored_year = numbering["current_year"]
    stored_month = numbering["current_month"]

    should_reset = False

    if reset_policy == "MONTHLY":
        should_reset = (
            stored_year != now.year
            or stored_month != now.month
        )
    elif reset_policy == "YEARLY":
        should_reset = stored_year != now.year

    next_number = 1 if should_reset else last_number + 1

    generated_number = build_document_number_preview(
        numbering,
        next_number,
    )

    conn.execute(
        """
        UPDATE document_numbering
        SET last_number = ?,
            current_year = ?,
            current_month = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            next_number,
            now.year,
            now.month,
            numbering["id"],
        ),
    )

    if commit:
        conn.commit()

    if close_after:
        conn.close()

    return generated_number


# ==========================================================
# HELPER INVOICE
# ==========================================================
def parse_integer(value, default=0):
    """Mengubah input rupiah/angka menjadi integer aman."""
    if value is None:
        return default

    cleaned = str(value).strip()

    if not cleaned:
        return default

    cleaned = (
        cleaned.replace("Rp", "")
        .replace("rp", "")
        .replace(".", "")
        .replace(",", "")
        .replace(" ", "")
    )

    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return default


def format_rupiah(nilai):
    """Mengubah angka menjadi format Rupiah Indonesia."""
    try:
        nilai = int(nilai or 0)
    except (TypeError, ValueError):
        nilai = 0

    return f"Rp {nilai:,.0f}".replace(",", ".")


def angka_ke_terbilang(number):
    """Mengubah bilangan bulat menjadi terbilang Bahasa Indonesia."""
    number = int(number or 0)

    satuan = (
        "",
        "Satu",
        "Dua",
        "Tiga",
        "Empat",
        "Lima",
        "Enam",
        "Tujuh",
        "Delapan",
        "Sembilan",
        "Sepuluh",
        "Sebelas",
    )

    if number < 0:
        return "Minus " + angka_ke_terbilang(abs(number))

    if number < 12:
        return satuan[number]

    if number < 20:
        return angka_ke_terbilang(number - 10) + " Belas"

    if number < 100:
        puluhan = number // 10
        sisa = number % 10
        hasil = angka_ke_terbilang(puluhan) + " Puluh"
        return hasil if sisa == 0 else hasil + " " + angka_ke_terbilang(sisa)

    if number < 200:
        sisa = number - 100
        return "Seratus" if sisa == 0 else "Seratus " + angka_ke_terbilang(sisa)

    if number < 1000:
        ratusan = number // 100
        sisa = number % 100
        hasil = angka_ke_terbilang(ratusan) + " Ratus"
        return hasil if sisa == 0 else hasil + " " + angka_ke_terbilang(sisa)

    if number < 2000:
        sisa = number - 1000
        return "Seribu" if sisa == 0 else "Seribu " + angka_ke_terbilang(sisa)

    if number < 1_000_000:
        ribuan = number // 1000
        sisa = number % 1000
        hasil = angka_ke_terbilang(ribuan) + " Ribu"
        return hasil if sisa == 0 else hasil + " " + angka_ke_terbilang(sisa)

    if number < 1_000_000_000:
        jutaan = number // 1_000_000
        sisa = number % 1_000_000
        hasil = angka_ke_terbilang(jutaan) + " Juta"
        return hasil if sisa == 0 else hasil + " " + angka_ke_terbilang(sisa)

    if number < 1_000_000_000_000:
        miliaran = number // 1_000_000_000
        sisa = number % 1_000_000_000
        hasil = angka_ke_terbilang(miliaran) + " Miliar"
        return hasil if sisa == 0 else hasil + " " + angka_ke_terbilang(sisa)

    triliunan = number // 1_000_000_000_000
    sisa = number % 1_000_000_000_000
    hasil = angka_ke_terbilang(triliunan) + " Triliun"
    return hasil if sisa == 0 else hasil + " " + angka_ke_terbilang(sisa)


def invoice_payment_status(total_tagihan, jumlah_dibayar, current_status="Belum Lunas"):
    if current_status == "Batal":
        return "Batal"

    total_tagihan = max(int(total_tagihan or 0), 0)
    jumlah_dibayar = max(int(jumlah_dibayar or 0), 0)

    if total_tagihan > 0 and jumlah_dibayar >= total_tagihan:
        return "Lunas"

    if jumlah_dibayar > 0:
        return "DP"

    return "Belum Lunas"


BULAN_INDONESIA = (
    "",
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)


def format_tanggal_indonesia(value):
    """Format YYYY-MM-DD menjadi 25 Juli 2026."""
    if not value:
        return "-"

    try:
        tanggal = datetime.strptime(str(value), "%Y-%m-%d")
        return (
            f"{tanggal.day} "
            f"{BULAN_INDONESIA[tanggal.month]} "
            f"{tanggal.year}"
        )
    except (TypeError, ValueError):
        return str(value)


def buat_qr_data_uri(text):
    """Membuat QR Code PNG dalam bentuk data URI."""
    if not text or qrcode is None:
        return None

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(text)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    encoded = base64.b64encode(
        buffer.getvalue()
    ).decode("ascii")

    return f"data:image/png;base64,{encoded}"


# ==========================================================
# HELPER DATABASE
# ==========================================================
def get_or_create_simple_reference(conn, table_name, nama):
    nama = nama.strip()

    if not nama:
        return None

    existing = conn.execute(
        f"""
        SELECT id
        FROM {table_name}
        WHERE LOWER(nama) = LOWER(?)
        """,
        (nama,),
    ).fetchone()

    if existing:
        return existing["id"]

    cursor = conn.execute(
        f"""
        INSERT INTO {table_name} (nama)
        VALUES (?)
        """,
        (nama,),
    )

    return cursor.lastrowid


def get_or_create_category_reference(
    conn,
    table_name,
    category_id,
    nama,
):
    nama = nama.strip()

    if not nama:
        return None

    existing = conn.execute(
        f"""
        SELECT id
        FROM {table_name}
        WHERE LOWER(nama) = LOWER(?)
          AND (
                category_id = ?
                OR (
                    category_id IS NULL
                    AND ? IS NULL
                )
          )
        """,
        (
            nama,
            category_id,
            category_id,
        ),
    ).fetchone()

    if existing:
        return existing["id"]

    cursor = conn.execute(
        f"""
        INSERT INTO {table_name} (
            category_id,
            nama
        )
        VALUES (?, ?)
        """,
        (
            category_id,
            nama,
        ),
    )

    return cursor.lastrowid


def generate_document_number(
    conn,
    prefix,
    tanggal,
    table_name,
    column_name,
):
    """
    Membuat nomor dokumen dengan format:

    TRX/2026/07/000001

    Nomor urut dimulai kembali dari 000001 setiap bulan.
    """

    tahun = tanggal[0:4]
    bulan = tanggal[5:7]

    nomor_awal = f"{prefix}/{tahun}/{bulan}/"

    existing_numbers = conn.execute(
        f"""
        SELECT {column_name}
        FROM {table_name}
        WHERE {column_name} LIKE ?
        """,
        (f"{nomor_awal}%",),
    ).fetchall()

    nomor_terbesar = 0

    for row in existing_numbers:
        nomor_dokumen = row[column_name]

        if not nomor_dokumen:
            continue

        try:
            nomor_urut = int(
                nomor_dokumen.rsplit("/", 1)[-1]
            )

            if nomor_urut > nomor_terbesar:
                nomor_terbesar = nomor_urut

        except (ValueError, IndexError):
            continue

    nomor_baru = nomor_terbesar + 1

    return f"{nomor_awal}{nomor_baru:06d}"


def get_products_for_form(conn):
    return conn.execute(
        """
        SELECT
            products.*,
            product_categories.nama AS kategori_nama,
            product_brands.nama AS brand_nama,
            product_variants.nama AS varian_nama,
            product_colors.nama AS warna_nama,
            product_sizes.nama AS ukuran_nama,
            suppliers.nama AS supplier_nama

        FROM products

        LEFT JOIN product_categories
            ON products.category_id =
               product_categories.id

        LEFT JOIN product_brands
            ON products.brand_id =
               product_brands.id

        LEFT JOIN product_variants
            ON products.variant_id =
               product_variants.id

        LEFT JOIN product_colors
            ON products.color_id =
               product_colors.id

        LEFT JOIN product_sizes
            ON products.size_id =
               product_sizes.id

        LEFT JOIN suppliers
            ON products.supplier_id =
               suppliers.id

        WHERE products.status_aktif = 1

        ORDER BY
            kategori_nama,
            products.nama_produk,
            warna_nama,
            ukuran_nama
        """
    ).fetchall()


def get_product_by_id(conn, product_id):
    return get_product_for_quotation(conn, product_id)


@app.route("/api/customers/search")
def api_customer_search():
    conn = get_connection()
    try:
        customers_found = search_customers(
            conn,
            request.args.get("keyword", ""),
            request.args.get("limit", 20),
        )
        results = [
            customer_search_result(customer)
            for customer in customers_found
        ]
    finally:
        conn.close()
    return jsonify({"results": results})


@app.route("/api/products/search")
def api_product_search():
    conn = get_connection()
    try:
        products_found = search_products(
            conn,
            request.args.get("keyword", ""),
            request.args.get("limit", 20),
        )
        results = [
            product_search_result(product)
            for product in products_found
        ]
    finally:
        conn.close()
    return jsonify({"results": results})


# ==========================================================
# DASHBOARD
# ==========================================================
@app.route("/")
def dashboard():
    conn = get_connection()

    total_customer = conn.execute(
        """
        SELECT COUNT(*)
        FROM customers
        """
    ).fetchone()[0]

    prospek_aktif = conn.execute(
        """
        SELECT COUNT(*)
        FROM customers
        WHERE status IN (
            'Prospek',
            'Follow Up',
            'Penawaran'
        )
        """
    ).fetchone()[0]

    customer_closing = conn.execute(
        """
        SELECT COUNT(*)
        FROM customers
        WHERE status IN (
            'Closing',
            'Existing Customer',
            'Repeat Customer'
        )
        """
    ).fetchone()[0]

    conn.close()

    return render_template(
        "dashboard.html",
        total_customer=total_customer,
        prospek_aktif=prospek_aktif,
        customer_closing=customer_closing,
    )


# ==========================================================
# CUSTOMER
# ==========================================================
@app.route("/customers", methods=["GET", "POST"])
def customers():
    conn = get_connection()

    keyword = request.args.get(
        "keyword",
        "",
    ).strip()

    if request.method == "POST":
        nama = request.form.get(
            "nama",
            "",
        ).strip()

        whatsapp = request.form.get(
            "whatsapp",
            "",
        ).strip()

        instansi = request.form.get(
            "instansi",
            "",
        ).strip()

        kota = request.form.get(
            "kota",
            "",
        ).strip()

        produk = request.form.get(
            "produk",
            "",
        ).strip()

        sumber = request.form.get(
            "sumber",
            "",
        ).strip()

        status = request.form.get(
            "status",
            "",
        ).strip()

        catatan = request.form.get(
            "catatan",
            "",
        ).strip()

        if nama:
            conn.execute(
                """
                INSERT INTO customers (
                    nama,
                    whatsapp,
                    instansi,
                    kota,
                    produk,
                    sumber,
                    status,
                    catatan
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nama,
                    whatsapp,
                    instansi,
                    kota,
                    produk,
                    sumber,
                    status,
                    catatan,
                ),
            )

            conn.commit()

        conn.close()

        return redirect(
            url_for("customers")
        )

    if keyword:
        daftar_customer = conn.execute(
            """
            SELECT *
            FROM customers
            WHERE nama LIKE ?
               OR whatsapp LIKE ?
               OR whatsapp_normalized LIKE ?
               OR instansi LIKE ?
               OR kota LIKE ?
               OR email LIKE ?
               OR produk_existing LIKE ?
            ORDER BY id DESC
            """,
            (
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
                f"%{keyword}%",
            ),
        ).fetchall()

    else:
        daftar_customer = conn.execute(
            """
            SELECT *
            FROM customers
            ORDER BY id DESC
            """
        ).fetchall()

    conn.close()

    return render_template(
        "customers.html",
        customers=daftar_customer,
    )


def _customer_import_preview_rows(customers, limit=500):
    """Prioritaskan row konflik sebelum row reguler pada preview terbatas."""

    conflict_rows = [
        customer
        for customer in customers
        if customer.get("conflicts") or customer.get("warnings")
    ]
    regular_rows = [
        customer
        for customer in customers
        if not customer.get("conflicts") and not customer.get("warnings")
    ]
    return (conflict_rows + regular_rows)[:limit]


@app.route("/customers/import", methods=["GET", "POST"])
def import_customers():
    context = {
        "preview": None,
        "preview_rows": [],
        "encoded_file": None,
        "file_sha256": None,
        "filename": None,
        "result": None,
        "error": None,
        "preview_limit": 500,
    }

    if request.method == "GET":
        return render_template("customer_import.html", **context)

    action = request.form.get("action", "preview")
    conn = get_connection()
    try:
        if action == "confirm":
            encoded_file = request.form.get("encoded_file", "")
            filename = secure_filename(request.form.get("filename", ""))
            expected_sha256 = request.form.get("file_sha256", "")
            if not encoded_file or not filename or not expected_sha256:
                raise CustomerImportError(
                    "Payload preview tidak lengkap. Upload ulang file sebelum konfirmasi."
                )
            try:
                content = base64.b64decode(encoded_file, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise CustomerImportError(
                    "Payload file preview rusak. Upload ulang file."
                ) from exc
            if len(content) > MAX_IMPORT_BYTES:
                raise CustomerImportError("Ukuran file melebihi batas aman 10 MB.")
            actual_sha256 = sha256_bytes(content)
            if not hmac.compare_digest(actual_sha256, expected_sha256):
                raise CustomerImportError(
                    "File berubah setelah preview. Upload ulang untuk menjaga konsistensi import."
                )

            parsed = parse_customer_workbook(content)
            consolidated = consolidate_customer_records(parsed)
            batch_id = (
                datetime.now().strftime("CUST-%Y%m%d-%H%M%S-%f")
                + "-"
                + actual_sha256[:8]
            )
            imported = import_customers_atomic(
                conn,
                consolidated,
                batch_id=batch_id,
                filename=filename,
                file_sha256=actual_sha256,
            )
            context.update(
                {
                    "result": imported,
                    "filename": filename,
                    "file_sha256": actual_sha256,
                }
            )
        elif action == "preview":
            upload = request.files.get("customer_file")
            if upload is None or not upload.filename:
                raise CustomerImportError("Pilih file MASTER DATABASE FINAL.xlsx.")
            filename = secure_filename(upload.filename)
            if not filename.casefold().endswith(".xlsx"):
                raise CustomerImportError("Format file wajib XLSX.")
            content = upload.stream.read(MAX_IMPORT_BYTES + 1)
            if len(content) > MAX_IMPORT_BYTES:
                raise CustomerImportError("Ukuran file melebihi batas aman 10 MB.")
            if not content:
                raise CustomerImportError("File XLSX kosong.")

            file_sha256 = sha256_bytes(content)
            preview = prepare_customer_import(content, conn)
            preview["report_markdown"] = build_import_report(
                preview["summary"],
                preview["per_sheet"],
                filename,
                file_sha256,
                preview["customers"],
            )
            context.update(
                {
                    "preview": preview,
                    "preview_rows": _customer_import_preview_rows(
                        preview["customers"],
                        context["preview_limit"],
                    ),
                    "encoded_file": base64.b64encode(content).decode("ascii"),
                    "file_sha256": file_sha256,
                    "filename": filename,
                }
            )
        else:
            raise CustomerImportError("Aksi import tidak dikenal.")
    except (CustomerImportError, sqlite3.Error) as exc:
        context["error"] = str(exc)
        return render_template("customer_import.html", **context), 400
    finally:
        conn.close()

    return render_template("customer_import.html", **context)


@app.route(
    "/customers/<int:customer_id>/delete",
    methods=["POST"],
)
def delete_customer(customer_id):
    conn = get_connection()

    conn.execute(
        """
        DELETE FROM customers
        WHERE id = ?
        """,
        (customer_id,),
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("customers")
    )


@app.route(
    "/customers/<int:customer_id>/edit",
    methods=["GET", "POST"],
)
def edit_customer(customer_id):
    conn = get_connection()

    customer = conn.execute(
        """
        SELECT *
        FROM customers
        WHERE id = ?
        """,
        (customer_id,),
    ).fetchone()

    if customer is None:
        conn.close()

        return "Customer tidak ditemukan", 404

    if request.method == "POST":
        nama = request.form.get(
            "nama",
            "",
        ).strip()

        whatsapp = request.form.get(
            "whatsapp",
            "",
        ).strip()

        instansi = request.form.get(
            "instansi",
            "",
        ).strip()

        kota = request.form.get(
            "kota",
            "",
        ).strip()

        produk = request.form.get(
            "produk",
            "",
        ).strip()

        sumber = request.form.get(
            "sumber",
            "",
        ).strip()

        status = request.form.get(
            "status",
            "",
        ).strip()

        catatan = request.form.get(
            "catatan",
            "",
        ).strip()

        conn.execute(
            """
            UPDATE customers
            SET nama = ?,
                whatsapp = ?,
                instansi = ?,
                kota = ?,
                produk = ?,
                sumber = ?,
                status = ?,
                catatan = ?
            WHERE id = ?
            """,
            (
                nama,
                whatsapp,
                instansi,
                kota,
                produk,
                sumber,
                status,
                catatan,
                customer_id,
            ),
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for("customers")
        )

    conn.close()

    return render_template(
        "edit_customer.html",
        customer=customer,
    )


# ==========================================================
# MASTER PRODUK
# ==========================================================
@app.route("/products")
def products():
    conn = get_connection()
    keyword = request.args.get("keyword", "").strip()

    where_clause = ""
    parameters = ()
    if keyword:
        where_clause = """
        WHERE products.kode_produk LIKE ?
           OR products.nama_produk LIKE ?
           OR product_categories.nama LIKE ?
           OR product_brands.nama LIKE ?
           OR product_variants.nama LIKE ?
           OR product_colors.nama LIKE ?
           OR product_sizes.nama LIKE ?
           OR products.subkategori LIKE ?
           OR products.jenis_produk LIKE ?
           OR products.steps LIKE ?
        """
        like_keyword = f"%{keyword}%"
        parameters = (like_keyword,) * 10

    daftar_produk = conn.execute(
        f"""
        SELECT
            products.*,
            product_categories.nama AS kategori_nama,
            product_brands.nama AS brand_nama,
            product_variants.nama AS varian_nama,
            product_colors.nama AS warna_nama,
            product_sizes.nama AS ukuran_nama,
            suppliers.nama AS supplier_nama

        FROM products

        LEFT JOIN product_categories
            ON products.category_id =
               product_categories.id

        LEFT JOIN product_brands
            ON products.brand_id =
               product_brands.id

        LEFT JOIN product_variants
            ON products.variant_id =
               product_variants.id

        LEFT JOIN product_colors
            ON products.color_id =
               product_colors.id

        LEFT JOIN product_sizes
            ON products.size_id =
               product_sizes.id

        LEFT JOIN suppliers
            ON products.supplier_id =
               suppliers.id

        {where_clause}

        ORDER BY
            kategori_nama,
            products.nama_produk,
            warna_nama,
            ukuran_nama
        """,
        parameters,
    ).fetchall()

    conn.close()

    return render_template(
        "products.html",
        products=daftar_produk,
        keyword=keyword,
    )


def decode_product_import_payload(payload, expected_digest):
    """Validasi payload preview sebelum file diproses ulang saat konfirmasi."""
    if not payload or len(payload) > MAX_IMPORT_FILE_SIZE * 2:
        raise ProductImportError("Payload import tidak valid atau terlalu besar.")

    try:
        file_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ProductImportError("Payload import tidak valid.") from error

    if len(file_bytes) > MAX_IMPORT_FILE_SIZE:
        raise ProductImportError("Ukuran file melebihi batas 5 MB.")

    actual_digest = hashlib.sha256(file_bytes).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest or ""):
        raise ProductImportError(
            "File preview berubah. Silakan unggah dan preview ulang."
        )

    return file_bytes


def build_product_import_report(filename, digest, summary, rows):
    """Buat laporan Markdown yang dapat diunduh dari halaman hasil import."""
    lines = [
        "# Laporan Import Master Produk",
        "",
        f"- Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- File: {filename or 'MASTER PRODUK.xlsx'}",
        f"- SHA-256: `{digest}`",
        f"- Total baris dibaca: {summary['total']}",
        f"- Produk dibuat: {summary.get('created', 0)}",
        f"- Produk dilewati: {summary.get('skipped', 0)}",
        f"- Duplicate: {summary['duplicate']}",
        f"- Warning: {summary['warning']}",
        f"- Error: {summary['error']}",
        "",
        "## Baris yang tidak dibuat",
        "",
    ]
    skipped_rows = [
        row for row in rows if row["status"] in {"duplicate", "error"}
    ]
    if not skipped_rows:
        lines.append("Tidak ada.")
    else:
        for row in skipped_rows:
            messages = row["errors"] or [row["duplicate_message"]]
            lines.append(
                f"- {row['source_group']} baris {row['source_row']} — "
                f"{row['nama_produk'] or '-'}: {'; '.join(messages)}"
            )

    return "\n".join(lines) + "\n"


@app.route(
    "/products/import",
    methods=["GET", "POST"],
)
def import_products():
    context = {
        "rows": None,
        "summary": None,
        "error_message": None,
        "file_payload": None,
        "file_digest": None,
        "filename": None,
        "import_completed": False,
        "report_markdown": None,
    }

    if request.method == "GET":
        return render_template("product_import.html", **context)

    action = request.form.get("action", "preview").strip().lower()

    try:
        if action == "preview":
            uploaded_file = request.files.get("product_file")
            if uploaded_file is None or not uploaded_file.filename:
                raise ProductImportError("File XLSX wajib dipilih.")
            if not uploaded_file.filename.lower().endswith(".xlsx"):
                raise ProductImportError("File harus menggunakan format .xlsx.")

            file_bytes = uploaded_file.read(MAX_IMPORT_FILE_SIZE + 1)
            if len(file_bytes) > MAX_IMPORT_FILE_SIZE:
                raise ProductImportError("Ukuran file melebihi batas 5 MB.")
            filename = secure_filename(uploaded_file.filename)
        elif action == "confirm":
            file_bytes = decode_product_import_payload(
                request.form.get("file_payload", ""),
                request.form.get("file_digest", ""),
            )
            filename = secure_filename(
                request.form.get("filename", "MASTER PRODUK.xlsx")
            )
        else:
            raise ProductImportError("Aksi import tidak dikenal.")

        rows = parse_product_workbook(file_bytes)
        digest = hashlib.sha256(file_bytes).hexdigest()
        context.update(
            {
                "file_payload": base64.b64encode(file_bytes).decode("ascii"),
                "file_digest": digest,
                "filename": filename,
            }
        )

        conn = get_connection()
        try:
            if action == "confirm":
                analyzed_rows, summary = import_product_rows(conn, rows)
                context["import_completed"] = True
                context["report_markdown"] = build_product_import_report(
                    filename,
                    digest,
                    summary,
                    analyzed_rows,
                )
            else:
                analyzed_rows = analyze_product_rows(conn, rows)
                summary = summarize_rows(analyzed_rows)
        finally:
            conn.close()

        context["rows"] = analyzed_rows
        context["summary"] = summary
        return render_template("product_import.html", **context)

    except ProductImportError as error:
        context["error_message"] = str(error)
        return render_template("product_import.html", **context), 400
    except sqlite3.Error:
        context["error_message"] = (
            "Import gagal karena kesalahan database. Seluruh perubahan telah "
            "di-rollback."
        )
        return render_template("product_import.html", **context), 500


@app.route(
    "/products/add",
    methods=["GET", "POST"],
)
def add_product():
    conn = get_connection()

    if request.method == "POST":
        kode_produk = request.form.get(
            "kode_produk",
            "",
        ).strip()

        nama_produk = request.form.get(
            "nama_produk",
            "",
        ).strip()

        kategori = request.form.get(
            "kategori",
            "",
        ).strip()

        brand = request.form.get(
            "brand",
            "",
        ).strip()

        varian = request.form.get(
            "varian",
            "",
        ).strip()

        warna = request.form.get(
            "warna",
            "",
        ).strip()

        ukuran = request.form.get(
            "ukuran",
            "",
        ).strip()

        supplier = request.form.get(
            "supplier",
            "",
        ).strip()

        satuan = request.form.get(
            "satuan",
            "Unit",
        ).strip()

        status_aktif_raw = request.form.get(
            "status_aktif",
            "1",
        ).strip()

        harga_jual_raw = request.form.get(
            "harga_jual_default",
            "",
        ).strip()

        harga_modal_raw = request.form.get(
            "harga_modal_default",
            "",
        ).strip()

        if not nama_produk:
            conn.close()

            return "Nama produk wajib diisi", 400

        try:
            status_aktif = int(
                status_aktif_raw or 1
            )

            harga_jual_default = int(
                harga_jual_raw or 0
            )

            harga_modal_default = int(
                harga_modal_raw or 0
            )

        except ValueError:
            conn.close()

            return (
                "Harga atau status memiliki format tidak valid.",
                400,
            )

        try:
            category_id = get_or_create_simple_reference(
                conn,
                "product_categories",
                kategori,
            )

            brand_id = get_or_create_simple_reference(
                conn,
                "product_brands",
                brand,
            )

            variant_id = get_or_create_category_reference(
                conn,
                "product_variants",
                category_id,
                varian,
            )

            color_id = get_or_create_simple_reference(
                conn,
                "product_colors",
                warna,
            )

            size_id = get_or_create_category_reference(
                conn,
                "product_sizes",
                category_id,
                ukuran,
            )

            supplier_id = get_or_create_simple_reference(
                conn,
                "suppliers",
                supplier,
            )

            conn.execute(
                """
                INSERT INTO products (
                    kode_produk,
                    nama_produk,
                    category_id,
                    brand_id,
                    variant_id,
                    color_id,
                    size_id,
                    supplier_id,
                    satuan,
                    harga_jual_default,
                    harga_modal_default,
                    status_aktif
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    kode_produk or None,
                    nama_produk,
                    category_id,
                    brand_id,
                    variant_id,
                    color_id,
                    size_id,
                    supplier_id,
                    satuan,
                    harga_jual_default,
                    harga_modal_default,
                    status_aktif,
                ),
            )

            conn.commit()

        except sqlite3.IntegrityError as error:
            conn.rollback()
            conn.close()

            if "kode_produk" in str(error).lower():
                return (
                    "Kode produk/SKU tersebut sudah digunakan.",
                    400,
                )

            return (
                f"Gagal menyimpan produk: {error}",
                400,
            )

        except sqlite3.Error as error:
            conn.rollback()
            conn.close()

            return (
                f"Gagal menyimpan produk: {error}",
                400,
            )

        conn.close()

        return redirect(
            url_for("products")
        )

    conn.close()

    return render_template(
        "add_product.html"
    )


# ==========================================================
# DAFTAR TRANSAKSI
# ==========================================================
@app.route("/transactions")
def transactions():
    conn = get_connection()

    daftar_transaksi = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama

        FROM sales_transactions

        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id

        ORDER BY sales_transactions.id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "transactions.html",
        transactions=daftar_transaksi,
        transaction_statuses=TRANSACTION_STATUSES,
    )


# ==========================================================
# TAMBAH TRANSAKSI
# ==========================================================
@app.route(
    "/transactions/add",
    methods=["GET", "POST"],
)
def add_transaction():
    conn = get_connection()
    transaction_identity = get_effective_identity(
        DOCUMENT_TYPE_TRANSACTION,
        conn=conn,
    )

    customers_list = conn.execute(
        """
        SELECT
            id,
            nama,
            instansi
        FROM customers
        ORDER BY nama
        """
    ).fetchall()

    products_list = get_products_for_form(
        conn
    )

    if request.method == "POST":
        customer_id_raw = request.form.get(
            "customer_id",
            "",
        ).strip()

        tanggal = request.form.get(
            "tanggal",
            "",
        ).strip()

        jenis_penjualan = request.form.get(
            "jenis_penjualan",
            "",
        ).strip()

        referal = request.form.get(
            "referal",
            "",
        ).strip()

        catatan = request.form.get(
            "catatan",
            "",
        ).strip()

        biaya_lain_raw = request.form.get(
            "biaya_lain",
            "0",
        ).strip()

        keterangan_biaya = request.form.get(
            "keterangan_biaya",
            "",
        ).strip()

        admin_fee_raw = request.form.get(
            "admin_fee",
            "0",
        ).strip()

        potongan_raw = request.form.get(
            "potongan",
            "0",
        ).strip()

        product_ids = request.form.getlist(
            "product_id[]"
        )

        qty_values = request.form.getlist(
            "qty[]"
        )

        harga_jual_values = request.form.getlist(
            "harga_jual[]"
        )

        harga_modal_values = request.form.getlist(
            "harga_modal[]"
        )

        if not customer_id_raw:
            conn.close()

            return "Customer wajib dipilih.", 400

        if not tanggal:
            conn.close()

            return "Tanggal transaksi wajib diisi.", 400

        if jenis_penjualan not in (
            "Direct",
            "Marketplace",
        ):
            conn.close()

            return "Jenis penjualan tidak valid.", 400

        if not product_ids:
            conn.close()

            return "Minimal harus ada satu produk.", 400

        if not (
            len(product_ids)
            == len(qty_values)
            == len(harga_jual_values)
            == len(harga_modal_values)
        ):
            conn.close()

            return "Data item transaksi tidak lengkap.", 400

        try:
            customer_id = int(
                customer_id_raw
            )

            biaya_lain = int(
                biaya_lain_raw or 0
            )

            if jenis_penjualan == "Marketplace":
                admin_fee = int(
                    admin_fee_raw or 0
                )

                potongan = int(
                    potongan_raw or 0
                )

            else:
                admin_fee = 0
                potongan = 0

        except ValueError:
            conn.close()

            return (
                "Format biaya transaksi tidak valid.",
                400,
            )

        prepared_items = []

        try:
            for index, product_id_raw in enumerate(
                product_ids
            ):
                nomor_baris = index + 1

                if not product_id_raw:
                    raise ValueError(
                        f"Produk baris ke-{nomor_baris} "
                        "belum dipilih."
                    )

                product_id = int(
                    product_id_raw
                )

                qty = int(
                    qty_values[index] or 0
                )

                harga_jual = int(
                    harga_jual_values[index] or 0
                )

                harga_modal = int(
                    harga_modal_values[index] or 0
                )

                if qty <= 0:
                    raise ValueError(
                        f"Qty baris ke-{nomor_baris} "
                        "harus lebih dari 0."
                    )

                if harga_jual < 0 or harga_modal < 0:
                    raise ValueError(
                        f"Harga baris ke-{nomor_baris} "
                        "tidak valid."
                    )

                product = get_product_by_id(
                    conn,
                    product_id,
                )

                if product is None:
                    raise ValueError(
                        f"Produk baris ke-{nomor_baris} "
                        "tidak ditemukan."
                    )

                subtotal_penjualan = (
                    qty * harga_jual
                )

                subtotal_modal = (
                    qty * harga_modal
                )

                margin_item = (
                    subtotal_penjualan
                    - subtotal_modal
                )

                prepared_items.append(
                    {
                        "product_id": product_id,
                        "kode_produk": (
                            product["kode_produk"]
                        ),
                        "nama_produk": (
                            product["nama_produk"]
                        ),
                        "kategori": (
                            product["kategori_nama"]
                        ),
                        "brand": (
                            product["brand_nama"]
                        ),
                        "varian": (
                            product["varian_nama"]
                        ),
                        "warna": (
                            product["warna_nama"]
                        ),
                        "ukuran": (
                            product["ukuran_nama"]
                        ),
                        "satuan": (
                            product["satuan"]
                        ),
                        "qty": qty,
                        "harga_jual": harga_jual,
                        "subtotal_penjualan": (
                            subtotal_penjualan
                        ),
                        "harga_modal": harga_modal,
                        "subtotal_modal": (
                            subtotal_modal
                        ),
                        "margin_item": margin_item,
                    }
                )

            financials = calculate_transaction_financials(
                prepared_items,
                admin_fee=admin_fee,
                potongan=potongan,
                biaya_lain=biaya_lain,
            )
            total_penjualan = financials["total_penjualan"]
            admin_fee = financials["admin_fee"]
            potongan = financials["potongan"]
            jumlah_diterima = financials["jumlah_diterima"]
            total_modal = financials["total_modal"]
            margin = financials["margin"]
            biaya_lain = financials["biaya_lain"]
            laba_bersih = financials["laba_bersih"]

            nomor_transaksi = generate_document_number(
                conn=conn,
                prefix="TRX",
                tanggal=tanggal,
                table_name="sales_transactions",
                column_name="nomor_transaksi",
            )

            cursor = conn.execute(
                """
                INSERT INTO sales_transactions (
                    nomor_transaksi,
                    customer_id,
                    tanggal,
                    jenis_penjualan,
                    referal,
                    status,
                    total_penjualan,
                    admin_fee,
                    potongan,
                    jumlah_diterima,
                    total_modal,
                    margin,
                    biaya_lain,
                    keterangan_biaya,
                    laba_bersih,
                    catatan
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    nomor_transaksi,
                    customer_id,
                    tanggal,
                    jenis_penjualan,
                    referal,
                    "Draft",
                    total_penjualan,
                    admin_fee,
                    potongan,
                    jumlah_diterima,
                    total_modal,
                    margin,
                    biaya_lain,
                    keterangan_biaya,
                    laba_bersih,
                    catatan,
                ),
            )

            transaction_id = cursor.lastrowid

            for item in prepared_items:
                conn.execute(
                    """
                    INSERT INTO sales_transaction_items (
                        transaction_id,
                        product_id,
                        kode_produk_snapshot,
                        nama_produk_snapshot,
                        kategori_snapshot,
                        brand_snapshot,
                        varian_snapshot,
                        warna_snapshot,
                        ukuran_snapshot,
                        satuan_snapshot,
                        qty,
                        harga_jual_satuan,
                        subtotal_penjualan,
                        harga_modal_satuan,
                        subtotal_modal,
                        margin_item
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        transaction_id,
                        item["product_id"],
                        item["kode_produk"],
                        item["nama_produk"],
                        item["kategori"],
                        item["brand"],
                        item["varian"],
                        item["warna"],
                        item["ukuran"],
                        item["satuan"],
                        item["qty"],
                        item["harga_jual"],
                        item["subtotal_penjualan"],
                        item["harga_modal"],
                        item["subtotal_modal"],
                        item["margin_item"],
                    ),
                )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "transaction_detail",
                    transaction_id=transaction_id,
                )
            )

        except ValueError as error:
            conn.rollback()
            conn.close()

            return str(error), 400

        except sqlite3.Error as error:
            conn.rollback()
            conn.close()

            return (
                f"Gagal menyimpan transaksi: {error}",
                400,
            )

    conn.close()

    return render_template(
        "add_transaction.html",
        customers=customers_list,
        products=products_list,
        identity=transaction_identity,
    )



# ==========================================================
# EDIT TRANSAKSI
# ==========================================================
@app.route(
    "/transactions/<int:transaction_id>/edit",
    methods=["GET", "POST"],
)
def edit_transaction(transaction_id):
    conn = get_connection()
    transaction_identity = get_effective_identity(
        DOCUMENT_TYPE_TRANSACTION,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT *
        FROM sales_transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    if request.method == "POST" and transaction["status"] == "Batal":
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="action_blocked",
            description="Edit finansial ditolak karena Transaction Batal.",
            created_by=transaction["referal"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return "Transaction Batal tidak dapat diedit finansial.", 400

    if request.method == "POST":
        downstream = conn.execute(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM sales_invoices
                    WHERE transaction_id = ?
                ) AS has_invoice,
                EXISTS(
                    SELECT 1 FROM delivery_orders
                    WHERE transaction_id = ?
                ) AS has_delivery_order,
                EXISTS(
                    SELECT 1 FROM payment_receipts
                    WHERE transaction_id = ?
                ) AS has_receipt
            """,
            (transaction_id, transaction_id, transaction_id),
        ).fetchone()
        if any(int(downstream[key] or 0) for key in downstream.keys()):
            record_workflow_event(
                conn,
                document_type="TRANSACTION",
                document_id=transaction_id,
                customer_id=transaction["customer_id"],
                event_type="action_blocked",
                description=(
                    "Edit finansial transaksi ditolak karena dokumen "
                    "downstream sudah tersedia."
                ),
            )
            conn.commit()
            conn.close()
            return (
                "Transaksi yang sudah mempunyai Invoice, Delivery Order, "
                "atau Receipt tidak dapat diedit finansial. Gunakan workflow revisi.",
                400,
            )

    customers_list = conn.execute(
        """
        SELECT id, nama, instansi
        FROM customers
        ORDER BY nama
        """
    ).fetchall()

    products_list = get_products_for_form(conn)

    existing_items = conn.execute(
        """
        SELECT *
        FROM sales_transaction_items
        WHERE transaction_id = ?
        ORDER BY id ASC
        """,
        (transaction_id,),
    ).fetchall()

    if request.method == "POST":
        customer_id_raw = request.form.get("customer_id", "").strip()
        tanggal = request.form.get("tanggal", "").strip()
        jenis_penjualan = request.form.get("jenis_penjualan", "").strip()
        referal = request.form.get("referal", "").strip()
        catatan = request.form.get("catatan", "").strip()
        biaya_lain_raw = request.form.get("biaya_lain", "0").strip()
        keterangan_biaya = request.form.get("keterangan_biaya", "").strip()
        admin_fee_raw = request.form.get("admin_fee", "0").strip()
        potongan_raw = request.form.get("potongan", "0").strip()

        product_ids = request.form.getlist("product_id[]")
        qty_values = request.form.getlist("qty[]")
        harga_jual_values = request.form.getlist("harga_jual[]")
        harga_modal_values = request.form.getlist("harga_modal[]")

        if not customer_id_raw:
            conn.close()
            return "Customer wajib dipilih.", 400

        if not tanggal:
            conn.close()
            return "Tanggal transaksi wajib diisi.", 400

        if jenis_penjualan not in ("Direct", "Marketplace"):
            conn.close()
            return "Jenis penjualan tidak valid.", 400

        if not product_ids:
            conn.close()
            return "Minimal harus ada satu produk.", 400

        if not (
            len(product_ids)
            == len(qty_values)
            == len(harga_jual_values)
            == len(harga_modal_values)
        ):
            conn.close()
            return "Data item transaksi tidak lengkap.", 400

        try:
            customer_id = int(customer_id_raw)
            biaya_lain = int(biaya_lain_raw or 0)

            if jenis_penjualan == "Marketplace":
                admin_fee = int(admin_fee_raw or 0)
                potongan = int(potongan_raw or 0)
            else:
                admin_fee = 0
                potongan = 0

            prepared_items = []

            for index, product_id_raw in enumerate(product_ids):
                nomor_baris = index + 1

                if not product_id_raw:
                    raise ValueError(
                        f"Produk baris ke-{nomor_baris} belum dipilih."
                    )

                product_id = int(product_id_raw)
                qty = int(qty_values[index] or 0)
                harga_jual = int(harga_jual_values[index] or 0)
                harga_modal = int(harga_modal_values[index] or 0)

                if qty <= 0:
                    raise ValueError(
                        f"Qty baris ke-{nomor_baris} harus lebih dari 0."
                    )

                if harga_jual < 0 or harga_modal < 0:
                    raise ValueError(
                        f"Harga baris ke-{nomor_baris} tidak valid."
                    )

                product = get_product_by_id(conn, product_id)
                if product is None:
                    raise ValueError(
                        f"Produk baris ke-{nomor_baris} tidak ditemukan."
                    )

                subtotal_penjualan = qty * harga_jual
                subtotal_modal = qty * harga_modal
                margin_item = subtotal_penjualan - subtotal_modal

                prepared_items.append(
                    {
                        "product_id": product_id,
                        "kode_produk": product["kode_produk"],
                        "nama_produk": product["nama_produk"],
                        "kategori": product["kategori_nama"],
                        "brand": product["brand_nama"],
                        "varian": product["varian_nama"],
                        "warna": product["warna_nama"],
                        "ukuran": product["ukuran_nama"],
                        "satuan": product["satuan"],
                        "qty": qty,
                        "harga_jual": harga_jual,
                        "subtotal_penjualan": subtotal_penjualan,
                        "harga_modal": harga_modal,
                        "subtotal_modal": subtotal_modal,
                        "margin_item": margin_item,
                    }
                )

            financials = calculate_transaction_financials(
                prepared_items,
                admin_fee=admin_fee,
                potongan=potongan,
                biaya_lain=biaya_lain,
            )
            total_penjualan = financials["total_penjualan"]
            admin_fee = financials["admin_fee"]
            potongan = financials["potongan"]
            jumlah_diterima = financials["jumlah_diterima"]
            total_modal = financials["total_modal"]
            margin = financials["margin"]
            biaya_lain = financials["biaya_lain"]
            laba_bersih = financials["laba_bersih"]

            conn.execute(
                """
                UPDATE sales_transactions
                SET customer_id = ?,
                    tanggal = ?,
                    jenis_penjualan = ?,
                    referal = ?,
                    total_penjualan = ?,
                    admin_fee = ?,
                    potongan = ?,
                    jumlah_diterima = ?,
                    total_modal = ?,
                    margin = ?,
                    biaya_lain = ?,
                    keterangan_biaya = ?,
                    laba_bersih = ?,
                    catatan = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    customer_id,
                    tanggal,
                    jenis_penjualan,
                    referal,
                    total_penjualan,
                    admin_fee,
                    potongan,
                    jumlah_diterima,
                    total_modal,
                    margin,
                    biaya_lain,
                    keterangan_biaya,
                    laba_bersih,
                    catatan,
                    transaction_id,
                ),
            )

            conn.execute(
                """
                DELETE FROM sales_transaction_items
                WHERE transaction_id = ?
                """,
                (transaction_id,),
            )

            for item in prepared_items:
                conn.execute(
                    """
                    INSERT INTO sales_transaction_items (
                        transaction_id,
                        product_id,
                        kode_produk_snapshot,
                        nama_produk_snapshot,
                        kategori_snapshot,
                        brand_snapshot,
                        varian_snapshot,
                        warna_snapshot,
                        ukuran_snapshot,
                        satuan_snapshot,
                        qty,
                        harga_jual_satuan,
                        subtotal_penjualan,
                        harga_modal_satuan,
                        subtotal_modal,
                        margin_item
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        transaction_id,
                        item["product_id"],
                        item["kode_produk"],
                        item["nama_produk"],
                        item["kategori"],
                        item["brand"],
                        item["varian"],
                        item["warna"],
                        item["ukuran"],
                        item["satuan"],
                        item["qty"],
                        item["harga_jual"],
                        item["subtotal_penjualan"],
                        item["harga_modal"],
                        item["subtotal_modal"],
                        item["margin_item"],
                    ),
                )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "transaction_detail",
                    transaction_id=transaction_id,
                )
            )

        except ValueError as error:
            conn.rollback()
            conn.close()
            return str(error), 400

        except sqlite3.Error as error:
            conn.rollback()
            conn.close()
            return f"Gagal memperbarui transaksi: {error}", 400

    conn.close()

    return render_template(
        "edit_transaction.html",
        transaction=transaction,
        items=existing_items,
        customers=customers_list,
        products=products_list,
        identity=transaction_identity,
    )


# ==========================================================
# UBAH STATUS TRANSAKSI
# ==========================================================
@app.route(
    "/transactions/<int:transaction_id>/status",
    methods=["POST"],
)
def update_transaction_status(transaction_id):
    status = request.form.get("status", "").strip()

    if status not in TRANSACTION_STATUSES:
        return "Status transaksi tidak valid.", 400

    conn = get_connection()

    transaction = conn.execute(
        """
        SELECT *
        FROM sales_transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    if status in ("Invoice", "Terkirim", "Lunas", "Selesai"):
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="action_blocked",
            description=f"Perubahan manual ke status turunan {status} ditolak.",
        )
        conn.commit()
        conn.close()
        return "Status transaksi tersebut hanya dapat diubah oleh workflow.", 400

    if status == "Batal":
        active_downstream = conn.execute(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM sales_invoices
                    WHERE transaction_id = ?
                      AND status_pembayaran != 'Batal'
                ) AS active_invoice,
                EXISTS(
                    SELECT 1 FROM delivery_orders
                    WHERE transaction_id = ? AND status != 'Batal'
                ) AS active_delivery,
                EXISTS(
                    SELECT 1 FROM purchase_orders
                    WHERE transaction_id = ? AND status != 'Batal'
                ) AS active_purchase
            """,
            (transaction_id, transaction_id, transaction_id),
        ).fetchone()
        if any(int(active_downstream[key] or 0) for key in active_downstream.keys()):
            conn.close()
            return "Batalkan dokumen downstream aktif terlebih dahulu.", 400

    if status in ("Draft", "Closing"):
        downstream_count = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM sales_invoices WHERE transaction_id = ?)
              + (SELECT COUNT(*) FROM delivery_orders WHERE transaction_id = ?)
              + (SELECT COUNT(*) FROM payment_receipts WHERE transaction_id = ?)
            """,
            (transaction_id, transaction_id, transaction_id),
        ).fetchone()[0]
        if downstream_count:
            conn.close()
            return "Status transaksi dengan downstream tidak dapat diputar balik.", 400

    old_status = transaction["status"]
    conn.execute(
        """
        UPDATE sales_transactions
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, transaction_id),
    )
    if old_status != status:
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="status_changed",
            old_status=old_status,
            new_status=status,
            description="Status transaksi diubah melalui workflow manual terbatas.",
        )

    conn.commit()
    conn.close()

    return redirect(
        request.referrer
        or url_for(
            "transaction_detail",
            transaction_id=transaction_id,
        )
    )


# ==========================================================
# DETAIL TRANSAKSI
# ==========================================================
@app.route(
    "/transactions/<int:transaction_id>"
)
def transaction_detail(transaction_id):
    conn = get_connection()
    transaction_identity = get_effective_identity(
        DOCUMENT_TYPE_TRANSACTION,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota

        FROM sales_transactions

        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id

        WHERE sales_transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()

        return "Transaksi tidak ditemukan", 404

    items = conn.execute(
        """
        SELECT *
        FROM sales_transaction_items
        WHERE transaction_id = ?
        ORDER BY id ASC
        """,
        (transaction_id,),
    ).fetchall()

    invoice = conn.execute(
        """
        SELECT *
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    purchase_order = None

    if invoice is not None:
        purchase_order = conn.execute(
            """
            SELECT *
            FROM purchase_orders
            WHERE invoice_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (invoice["id"],),
        ).fetchone()

    conn.close()

    return render_template(
        "transaction_detail.html",
        transaction=transaction,
        items=items,
        invoice=invoice,
        purchase_order=purchase_order,
        transaction_statuses=TRANSACTION_STATUSES,
        invoice_payment_statuses=INVOICE_PAYMENT_STATUSES,
        identity=transaction_identity,
    )


# ==========================================================
# INVOICE
# ==========================================================
@app.route(
    "/transactions/<int:transaction_id>/invoice/generate",
    methods=["POST"],
)
def generate_invoice(transaction_id):
    conn = get_connection()
    get_effective_identity(
        DOCUMENT_TYPE_INVOICE,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT *
        FROM sales_transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    if transaction["status"] == "Batal":
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="action_blocked",
            description="Pembuatan Invoice ditolak karena transaksi Batal.",
        )
        conn.commit()
        conn.close()
        return "Transaksi Batal tidak dapat membuat Invoice.", 400

    existing_invoice = conn.execute(
        """
        SELECT id
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if existing_invoice is not None:
        conn.close()
        return redirect(
            url_for(
                "print_invoice",
                transaction_id=transaction_id,
            )
        )

    tanggal_invoice = transaction["tanggal"]

    nomor_invoice = generate_document_number(
        conn=conn,
        prefix="INV",
        tanggal=tanggal_invoice,
        table_name="sales_invoices",
        column_name="nomor_invoice",
    )

    try:
        conn.execute(
            """
            INSERT INTO sales_invoices (
                transaction_id,
                nomor_invoice,
                tanggal_invoice,
                status_pembayaran
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                transaction_id,
                nomor_invoice,
                tanggal_invoice,
                "Belum Lunas",
            ),
        )

        sync_transaction_status(
            conn,
            transaction_id,
            reason="Invoice dibuat dan transaksi disinkronkan.",
        )

        conn.commit()

    except sqlite3.Error as error:
        conn.rollback()
        conn.close()
        return f"Gagal membuat invoice: {error}", 400

    conn.close()

    return redirect(
        url_for(
            "print_invoice",
            transaction_id=transaction_id,
        )
    )


@app.route(
    "/transactions/<int:transaction_id>/invoice/status",
    methods=["POST"],
)
def update_invoice_status(transaction_id):
    status_pembayaran = request.form.get(
        "status_pembayaran",
        "",
    ).strip()

    if status_pembayaran not in INVOICE_PAYMENT_STATUSES:
        return "Status pembayaran invoice tidak valid.", 400

    if status_pembayaran != "Batal":
        return (
            "Status pembayaran dihitung otomatis dari Receipt non-Void. "
            "User tidak dapat mengubahnya secara manual.",
            400,
        )

    conn = get_connection()

    invoice = conn.execute(
        """
        SELECT *
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if invoice is None:
        conn.close()
        return "Invoice belum dibuat.", 404

    active_receipts = conn.execute(
        """
        SELECT COUNT(*) FROM payment_receipts
        WHERE invoice_id = ? AND status != 'Void'
        """,
        (invoice["id"],),
    ).fetchone()[0]
    if active_receipts:
        conn.close()
        return "Void seluruh Receipt aktif sebelum membatalkan Invoice.", 400

    old_status = invoice["status_pembayaran"]
    conn.execute(
        """
        UPDATE sales_invoices
        SET status_pembayaran = 'Batal', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (invoice["id"],),
    )
    record_workflow_event(
        conn,
        document_type="INVOICE",
        document_id=invoice["id"],
        event_type="cancelled",
        old_status=old_status,
        new_status="Batal",
        description="Invoice dibatalkan melalui workflow terpisah.",
    )
    sync_transaction_status(
        conn,
        transaction_id,
        reason="Transaction disinkronkan setelah Invoice dibatalkan.",
    )

    conn.commit()
    conn.close()

    return redirect(
        request.referrer
        or url_for(
            "transaction_detail",
            transaction_id=transaction_id,
        )
    )




@app.route(
    "/transactions/<int:transaction_id>/invoice/edit",
    methods=["GET", "POST"],
)
def edit_invoice(transaction_id):
    conn = get_connection()
    invoice_identity = get_effective_identity(
        DOCUMENT_TYPE_INVOICE,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM sales_transactions
        LEFT JOIN customers
            ON sales_transactions.customer_id = customers.id
        WHERE sales_transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    invoice = conn.execute(
        """
        SELECT *
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if invoice is None:
        conn.close()
        return "Invoice belum dibuat.", 404

    total_penjualan = int(transaction["total_penjualan"] or 0)
    potongan = int(transaction["potongan"] or 0)
    total_tagihan = max(total_penjualan - potongan, 0)

    if request.method == "POST":
        tanggal_invoice = request.form.get("tanggal_invoice", "").strip()
        jatuh_tempo = request.form.get("jatuh_tempo", "").strip()
        catatan = request.form.get("catatan", "").strip()

        if not tanggal_invoice:
            conn.close()
            return "Tanggal invoice wajib diisi.", 400

        conn.execute(
            """
            UPDATE sales_invoices
            SET tanggal_invoice = ?,
                jatuh_tempo = ?,
                catatan = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE transaction_id = ?
            """,
            (
                tanggal_invoice,
                jatuh_tempo or None,
                catatan or None,
                transaction_id,
            ),
        )
        reconcile_invoice_payment(conn, invoice["id"])

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "print_invoice",
                transaction_id=transaction_id,
            )
        )

    conn.close()

    return render_template(
        "invoice_edit.html",
        transaction=transaction,
        invoice=invoice,
        total_tagihan=total_tagihan,
        identity=invoice_identity,
    )


@app.route(
    "/transactions/<int:transaction_id>/invoice/print"
)
def print_invoice(transaction_id):
    conn = get_connection()
    identity = get_effective_identity(
        DOCUMENT_TYPE_INVOICE,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota,
            customers.catatan AS customer_catatan

        FROM sales_transactions

        LEFT JOIN customers
            ON sales_transactions.customer_id = customers.id

        WHERE sales_transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    invoice = conn.execute(
        """
        SELECT *
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if invoice is None:
        conn.close()
        return redirect(
            url_for(
                "transaction_detail",
                transaction_id=transaction_id,
            )
        )

    items = conn.execute(
        """
        SELECT *
        FROM sales_transaction_items
        WHERE transaction_id = ?
        ORDER BY id ASC
        """,
        (transaction_id,),
    ).fetchall()

    conn.close()

    subtotal = int(transaction["total_penjualan"] or 0)
    potongan = int(transaction["potongan"] or 0)
    total_tagihan = max(subtotal - potongan, 0)
    jumlah_dibayar = int(invoice["jumlah_dibayar"] or 0)
    sisa_tagihan = max(total_tagihan - jumlah_dibayar, 0)

    invoice_data = dict(invoice)
    invoice_data["terbilang"] = (
        angka_ke_terbilang(
            sisa_tagihan
            if jumlah_dibayar
            else total_tagihan
        )
        + " Rupiah"
    )
    invoice_data["tanggal_invoice_format"] = (
        format_tanggal_indonesia(
            invoice["tanggal_invoice"]
        )
    )
    invoice_data["jatuh_tempo_format"] = (
        format_tanggal_indonesia(
            invoice["jatuh_tempo"]
        )
        if invoice["jatuh_tempo"]
        else "-"
    )

    invoice_url = url_for(
        "print_invoice",
        transaction_id=transaction_id,
        _external=True,
    )

    qr_payload = (
        f"{identity['nama_brand']}\n"
        f"Invoice: {invoice['nomor_invoice']}\n"
        f"Customer: {transaction['customer_nama'] or '-'}\n"
        f"Total: Rp {total_tagihan:,.0f}\n"
        f"Status: {invoice['status_pembayaran']}\n"
        f"URL: {invoice_url}"
    ).replace(",", ".")

    qr_code_data_uri = buat_qr_data_uri(
        qr_payload
    )

    return render_template(
        "invoice_print.html",
        transaction=transaction,
        invoice=invoice_data,
        items=items,
        total_tagihan=total_tagihan,
        jumlah_dibayar=jumlah_dibayar,
        sisa_tagihan=sisa_tagihan,
        invoice_url=invoice_url,
        qr_code_data_uri=qr_code_data_uri,
        identity=identity,
    )


# ==========================================================
# CETAK RINGKASAN TRANSAKSI
# ==========================================================
@app.route(
    "/transactions/<int:transaction_id>/print"
)
def print_transaction(transaction_id):
    conn = get_connection()
    identity = get_effective_identity(
        DOCUMENT_TYPE_TRANSACTION,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota

        FROM sales_transactions

        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id

        WHERE sales_transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan", 404

    items = conn.execute(
        """
        SELECT *
        FROM sales_transaction_items
        WHERE transaction_id = ?
        ORDER BY id ASC
        """,
        (transaction_id,),
    ).fetchall()

    conn.close()

    return render_template(
        "transaction_print.html",
        transaction=transaction,
        items=items,
        identity=identity,
    )




def checkbox_value(form, field_name):
    return 1 if form.get(field_name) == "1" else 0


def add_quotation_activity(
    conn,
    quotation_id,
    activity_type,
    description,
    created_by=None,
):
    conn.execute(
        """
        INSERT INTO sales_quotation_activities (
            quotation_id,
            activity_type,
            description,
            created_by
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            quotation_id,
            activity_type,
            description,
            created_by or "Sistem",
        ),
    )


# ==========================================================
# QUOTATION / PENAWARAN
# ==========================================================
@app.route("/quotations")
def quotations():
    conn = get_connection()

    quotation_list = conn.execute(
        """
        SELECT
            sales_quotations.*,
            COALESCE(
                sales_quotations.customer_nama_snapshot,
                customers.nama
            ) AS customer_nama,
            COALESCE(
                sales_quotations.customer_perusahaan_snapshot,
                customers.instansi
            ) AS customer_instansi,
            company_identities.code AS identity_code,
            company_identities.identity_type AS identity_type,
            company_identities.nama_perusahaan AS identity_name
        FROM sales_quotations
        LEFT JOIN customers
            ON sales_quotations.customer_id = customers.id
        LEFT JOIN company_identities
            ON sales_quotations.identity_id = company_identities.id
        ORDER BY sales_quotations.id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "quotations.html",
        quotations=quotation_list,
        quotation_statuses=QUOTATION_STATUSES,
        format_tanggal_indonesia=format_tanggal_indonesia,
    )


def calculate_quotation_item_subtotal(qty, unit_price, item_discount):
    """Hitung subtotal item quotation sebagai integer non-negatif."""
    return max(
        max(int(qty or 0), 0)
        * max(int(unit_price or 0), 0)
        - max(int(item_discount or 0), 0),
        0,
    )


def allocate_global_discount(items, global_discount):
    """Alokasikan diskon global dan hasilkan snapshot finansial item."""
    allocated_items = []

    for stable_index, item in enumerate(items):
        allocated_item = dict(item)
        allocated_item["subtotal_awal"] = (
            calculate_quotation_item_subtotal(
                item["qty"],
                item["harga_satuan"],
                item["diskon_item"],
            )
        )
        allocated_item["subtotal_modal"] = max(
            int(allocated_item.get("subtotal_modal", 0) or 0),
            0,
        )
        allocated_item["diskon_global_alokasi"] = 0
        allocated_item["subtotal_akhir"] = allocated_item[
            "subtotal_awal"
        ]
        allocated_item["margin_item"] = (
            allocated_item["subtotal_akhir"]
            - allocated_item["subtotal_modal"]
        )
        allocated_item["_stable_index"] = stable_index
        allocated_items.append(allocated_item)

    total_item_base = sum(
        item["subtotal_awal"] for item in allocated_items
    )
    effective_discount = min(
        max(int(global_discount or 0), 0),
        total_item_base,
    )

    if not allocated_items or total_item_base == 0:
        for item in allocated_items:
            item.pop("_stable_index", None)
        return allocated_items

    for item in allocated_items:
        item["diskon_global_alokasi"] = (
            effective_discount
            * item["subtotal_awal"]
            // total_item_base
        )

    remaining_discount = effective_discount - sum(
        item["diskon_global_alokasi"]
        for item in allocated_items
    )

    remainder_order = sorted(
        allocated_items,
        key=lambda item: (
            -item["subtotal_awal"],
            item["_stable_index"],
        ),
    )

    for item in remainder_order:
        if remaining_discount == 0:
            break

        available = (
            item["subtotal_awal"]
            - item["diskon_global_alokasi"]
        )
        extra = min(available, remaining_discount)
        item["diskon_global_alokasi"] += extra
        remaining_discount -= extra

    if remaining_discount != 0:
        raise ValueError("Sisa diskon global tidak dapat dialokasikan.")

    for item in allocated_items:
        item["subtotal_akhir"] = max(
            item["subtotal_awal"]
            - item["diskon_global_alokasi"],
            0,
        )
        item["margin_item"] = (
            item["subtotal_akhir"] - item["subtotal_modal"]
        )
        item.pop("_stable_index", None)

    return allocated_items


def calculate_transaction_financials(
    items,
    admin_fee=0,
    potongan=0,
    biaya_lain=0,
):
    """Bentuk header finansial hanya dari snapshot detail transaction."""
    total_penjualan = 0
    total_modal = 0
    margin = 0

    for item in items:
        subtotal_penjualan = max(
            int(item["subtotal_penjualan"] or 0),
            0,
        )
        subtotal_modal = max(
            int(item["subtotal_modal"] or 0),
            0,
        )
        margin_item = int(item["margin_item"] or 0)

        if margin_item != subtotal_penjualan - subtotal_modal:
            raise ValueError(
                "Invariant margin item transaksi tidak konsisten."
            )

        total_penjualan += subtotal_penjualan
        total_modal += subtotal_modal
        margin += margin_item

    if margin != total_penjualan - total_modal:
        raise ValueError("Invariant margin transaksi tidak konsisten.")

    normalized_admin_fee = max(int(admin_fee or 0), 0)
    normalized_potongan = max(int(potongan or 0), 0)
    normalized_biaya_lain = max(int(biaya_lain or 0), 0)
    jumlah_diterima = (
        total_penjualan
        - normalized_admin_fee
        - normalized_potongan
    )
    laba_bersih = (
        margin
        - normalized_admin_fee
        - normalized_potongan
        - normalized_biaya_lain
    )

    return {
        "total_penjualan": total_penjualan,
        "admin_fee": normalized_admin_fee,
        "potongan": normalized_potongan,
        "jumlah_diterima": jumlah_diterima,
        "total_modal": total_modal,
        "margin": margin,
        "biaya_lain": normalized_biaya_lain,
        "laba_bersih": laba_bersih,
    }


def get_quotation_with_customer_snapshot(conn, quotation_id):
    return conn.execute(
        """
        SELECT
            sales_quotations.*,
            COALESCE(
                sales_quotations.customer_nama_snapshot,
                customers.nama
            ) AS customer_nama,
            COALESCE(
                sales_quotations.customer_perusahaan_snapshot,
                customers.instansi
            ) AS customer_instansi,
            COALESCE(
                sales_quotations.customer_pic_snapshot,
                customers.nama
            ) AS customer_pic,
            COALESCE(
                sales_quotations.customer_whatsapp_snapshot,
                customers.whatsapp_normalized,
                customers.whatsapp
            ) AS customer_whatsapp,
            COALESCE(
                sales_quotations.customer_email_snapshot,
                customers.email
            ) AS customer_email,
            COALESCE(
                sales_quotations.customer_alamat_snapshot,
                customers.alamat
            ) AS customer_alamat,
            COALESCE(
                sales_quotations.customer_kota_snapshot,
                customers.kota
            ) AS customer_kota,
            COALESCE(
                sales_quotations.customer_status_snapshot,
                customers.status
            ) AS customer_status,
            COALESCE(
                sales_quotations.customer_minat_snapshot,
                customers.produk
            ) AS customer_minat
        FROM sales_quotations
        LEFT JOIN customers
            ON sales_quotations.customer_id = customers.id
        WHERE sales_quotations.id = ?
        """,
        (quotation_id,),
    ).fetchone()


def quotation_customer_selection(quotation):
    if quotation is None or quotation["customer_id"] is None:
        return None
    return {
        "id": int(quotation["customer_id"]),
        "nama": quotation["customer_nama"],
        "perusahaan": quotation["customer_instansi"],
        "pic": quotation["customer_pic"],
        "whatsapp": quotation["customer_whatsapp"],
        "email": quotation["customer_email"],
        "alamat": quotation["customer_alamat"],
        "kota": quotation["customer_kota"],
        "status": quotation["customer_status"],
        "minat_produk": quotation["customer_minat"],
    }


def quotation_item_selection(item):
    display_item = quotation_item_for_display(item)
    return {
        "product_id": item["product_id"],
        "kode_produk": item["kode_produk_snapshot"],
        "nama_produk": item["nama_produk_snapshot"],
        "satuan": item["satuan_snapshot"] or "Unit",
        "harga_jual_default": int(item["harga_satuan"] or 0),
        "qty": int(item["qty"] or 0),
        "harga_satuan": int(item["harga_satuan"] or 0),
        "diskon_item": int(item["diskon_item"] or 0),
        "spesifikasi": display_item["spesifikasi_lines"],
    }


def prepare_quotation_customer(conn, customer_id_raw):
    try:
        customer_id = int(customer_id_raw)
    except (TypeError, ValueError) as error:
        raise ValueError("Customer tidak ditemukan.") from error

    customer = get_customer_for_quotation(
        conn,
        customer_id,
    )
    if customer is None:
        raise ValueError("Customer tidak ditemukan.")
    if not int(customer["status_aktif"] or 0):
        raise ValueError("Customer tidak aktif.")

    return customer_id, customer_snapshot(customer)


def prepare_quotation_items(conn, form):
    product_ids = form.getlist("product_id[]")
    qty_values = form.getlist("qty[]")
    harga_values = form.getlist("harga_satuan[]")
    diskon_item_values = form.getlist("diskon_item[]")

    if not product_ids:
        raise ValueError("Minimal harus ada satu produk.")

    if not (
        len(product_ids)
        == len(qty_values)
        == len(harga_values)
        == len(diskon_item_values)
    ):
        raise ValueError("Data item penawaran tidak lengkap.")

    prepared_items = []
    subtotal_penawaran = 0

    for index, product_id_raw in enumerate(product_ids):
        nomor_baris = index + 1

        if not product_id_raw:
            raise ValueError(
                f"Produk baris ke-{nomor_baris} belum dipilih."
            )

        try:
            product_id = int(product_id_raw)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Produk baris ke-{nomor_baris} tidak ditemukan."
            ) from error
        qty = parse_integer(qty_values[index])
        harga_satuan = parse_integer(harga_values[index])
        diskon_item = parse_integer(diskon_item_values[index])

        if qty <= 0:
            raise ValueError(
                f"Qty baris ke-{nomor_baris} harus lebih dari 0."
            )

        if harga_satuan < 0 or diskon_item < 0:
            raise ValueError(
                f"Harga atau diskon baris ke-{nomor_baris} tidak valid."
            )

        product = get_product_for_quotation(
            conn,
            product_id,
        )

        if product is None:
            raise ValueError(
                f"Produk baris ke-{nomor_baris} tidak ditemukan."
            )
        if not int(product["status_aktif"] or 0):
            raise ValueError(
                f"Produk baris ke-{nomor_baris} tidak aktif."
            )

        snapshot = product_snapshot(product)

        subtotal_item = calculate_quotation_item_subtotal(
            qty,
            harga_satuan,
            diskon_item,
        )
        subtotal_penawaran += subtotal_item

        prepared_items.append(
            {
                **snapshot,
                "qty": qty,
                "harga_satuan": harga_satuan,
                "diskon_item": diskon_item,
                "subtotal": subtotal_item,
            }
        )

    return prepared_items, subtotal_penawaran


def insert_quotation_items(conn, quotation_id, prepared_items):
    for item in prepared_items:
        conn.execute(
            """
            INSERT INTO sales_quotation_items (
                quotation_id,
                product_id,
                kode_produk_snapshot,
                nama_produk_snapshot,
                kategori_snapshot,
                brand_snapshot,
                varian_snapshot,
                warna_snapshot,
                ukuran_snapshot,
                satuan_snapshot,
                subkategori_snapshot,
                jenis_produk_snapshot,
                steps_snapshot,
                spesifikasi_snapshot,
                harga_modal_snapshot,
                qty,
                harga_satuan,
                diskon_item,
                subtotal
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                quotation_id,
                item["product_id"],
                item["kode_produk"],
                item["nama_produk"],
                item["kategori"],
                item["brand"],
                item["varian"],
                item["warna"],
                item["ukuran"],
                item["satuan"],
                item["subkategori"],
                item["jenis_produk"],
                item["steps"],
                item["spesifikasi"],
                item["harga_modal"],
                item["qty"],
                item["harga_satuan"],
                item["diskon_item"],
                item["subtotal"],
            ),
        )


@app.route(
    "/quotations/add",
    methods=["GET", "POST"],
)
def add_quotation():
    conn = get_connection()

    identities_list = get_active_quotation_identities(conn)
    default_identity = get_default_full_identity(conn)

    if request.method == "POST":
        identity_id_raw = request.form.get("identity_id", "").strip()

        if not identity_id_raw and default_identity is not None:
            identity_id_raw = str(default_identity["id"])

        customer_id_raw = request.form.get("customer_id", "").strip()
        tanggal = request.form.get("tanggal", "").strip()
        berlaku_sampai = request.form.get("berlaku_sampai", "").strip()
        sales = request.form.get("sales", "").strip()
        diskon_global = parse_integer(
            request.form.get("diskon", "0")
        )
        catatan = request.form.get("catatan", "").strip()
        syarat_ketentuan = request.form.get(
            "syarat_ketentuan",
            "",
        ).strip()

        if not customer_id_raw:
            conn.close()
            return "Customer wajib dipilih.", 400

        if not tanggal:
            conn.close()
            return "Tanggal penawaran wajib diisi.", 400

        try:
            if diskon_global < 0:
                raise ValueError("Diskon global tidak boleh negatif.")

            identity = validate_quotation_identity(
                conn,
                identity_id_raw,
            )
            customer_id, customer_data = prepare_quotation_customer(
                conn,
                customer_id_raw,
            )
            prepared_items, subtotal_penawaran = (
                prepare_quotation_items(conn, request.form)
            )

            totals = calculate_quotation_totals(
                subtotal_penawaran,
                diskon_global,
                identity,
            )

            nomor_penawaran = generate_document_number(
                conn=conn,
                prefix="QT",
                tanggal=tanggal,
                table_name="sales_quotations",
                column_name="nomor_penawaran",
            )

            cursor = conn.execute(
                """
                INSERT INTO sales_quotations (
                    nomor_penawaran,
                    customer_id,
                    tanggal,
                    berlaku_sampai,
                    sales,
                    status,
                    revisi,
                    subtotal,
                    diskon,
                    is_ppn,
                    ppn_rate,
                    dpp,
                    ppn_amount,
                    grand_total,
                    catatan,
                    syarat_ketentuan,
                    identity_id,
                    customer_nama_snapshot,
                    customer_perusahaan_snapshot,
                    customer_pic_snapshot,
                    customer_whatsapp_snapshot,
                    customer_email_snapshot,
                    customer_alamat_snapshot,
                    customer_kota_snapshot,
                    customer_status_snapshot,
                    customer_minat_snapshot
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    nomor_penawaran,
                    customer_id,
                    tanggal,
                    berlaku_sampai or None,
                    sales or None,
                    "Draft",
                    0,
                    totals["subtotal"],
                    totals["discount"],
                    totals["is_ppn"],
                    totals["ppn_rate"],
                    totals["dpp"],
                    totals["ppn_amount"],
                    totals["grand_total"],
                    catatan or None,
                    syarat_ketentuan or None,
                    identity["id"],
                    customer_data["customer_nama_snapshot"],
                    customer_data["customer_perusahaan_snapshot"],
                    customer_data["customer_pic_snapshot"],
                    customer_data["customer_whatsapp_snapshot"],
                    customer_data["customer_email_snapshot"],
                    customer_data["customer_alamat_snapshot"],
                    customer_data["customer_kota_snapshot"],
                    customer_data["customer_status_snapshot"],
                    customer_data["customer_minat_snapshot"],
                ),
            )

            quotation_id = cursor.lastrowid
            insert_quotation_items(
                conn,
                quotation_id,
                prepared_items,
            )

            add_quotation_activity(
                conn,
                quotation_id,
                "created",
                "Penawaran dibuat dengan status Draft.",
                sales or "Sistem",
            )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "quotation_detail",
                    quotation_id=quotation_id,
                )
            )

        except (ValueError, sqlite3.Error) as error:
            conn.rollback()
            conn.close()
            return f"Gagal menyimpan penawaran: {error}", 400

    conn.close()

    return render_template(
        "add_quotation.html",
        identities=identities_list,
        selected_customer=None,
        initial_items=[],
        default_identity_id=(
            default_identity["id"] if default_identity else None
        ),
    )


@app.route(
    "/quotations/<int:quotation_id>/edit",
    methods=["GET", "POST"],
)
def edit_quotation(quotation_id):
    conn = get_connection()

    quotation = get_quotation_with_customer_snapshot(
        conn,
        quotation_id,
    )

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    if request.method == "POST" and quotation["converted_transaction_id"]:
        add_quotation_activity(
            conn,
            quotation_id,
            "blocked",
            "Edit finansial ditolak karena penawaran sudah dikonversi.",
            quotation["sales"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return (
            "Identity quotation yang sudah dikonversi tidak dapat diubah. "
            "Item, harga, dan customer juga dikunci; gunakan workflow revisi.",
            400,
        )

    identities_list = get_active_quotation_identities(conn)
    current_identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )

    existing_items = conn.execute(
        """
        SELECT *
        FROM sales_quotation_items
        WHERE quotation_id = ?
        ORDER BY id ASC
        """,
        (quotation_id,),
    ).fetchall()

    if request.method == "POST":
        identity_id_raw = request.form.get(
            "identity_id",
            str(current_identity["id"]),
        ).strip()
        customer_id_raw = request.form.get("customer_id", "").strip()
        tanggal = request.form.get("tanggal", "").strip()
        berlaku_sampai = request.form.get("berlaku_sampai", "").strip()
        sales = request.form.get("sales", "").strip()
        diskon_global = parse_integer(
            request.form.get("diskon", "0")
        )
        catatan = request.form.get("catatan", "").strip()
        syarat_ketentuan = request.form.get(
            "syarat_ketentuan",
            "",
        ).strip()

        if not customer_id_raw or not tanggal:
            conn.close()
            return "Customer dan tanggal wajib diisi.", 400

        try:
            if diskon_global < 0:
                raise ValueError("Diskon global tidak boleh negatif.")

            identity = validate_quotation_identity(
                conn,
                identity_id_raw,
            )

            if (
                quotation["converted_transaction_id"]
                and identity["id"] != current_identity["id"]
            ):
                raise ValueError(
                    "Identity quotation yang sudah dikonversi "
                    "tidak dapat diubah."
                )

            customer_id, customer_data = prepare_quotation_customer(
                conn,
                customer_id_raw,
            )
            prepared_items, subtotal_penawaran = (
                prepare_quotation_items(conn, request.form)
            )
            totals = calculate_quotation_totals(
                subtotal_penawaran,
                diskon_global,
                identity,
            )

            conn.execute(
                """
                UPDATE sales_quotations
                SET identity_id = ?,
                    customer_id = ?,
                    tanggal = ?,
                    berlaku_sampai = ?,
                    sales = ?,
                    subtotal = ?,
                    diskon = ?,
                    is_ppn = ?,
                    ppn_rate = ?,
                    dpp = ?,
                    ppn_amount = ?,
                    grand_total = ?,
                    catatan = ?,
                    syarat_ketentuan = ?,
                    customer_nama_snapshot = ?,
                    customer_perusahaan_snapshot = ?,
                    customer_pic_snapshot = ?,
                    customer_whatsapp_snapshot = ?,
                    customer_email_snapshot = ?,
                    customer_alamat_snapshot = ?,
                    customer_kota_snapshot = ?,
                    customer_status_snapshot = ?,
                    customer_minat_snapshot = ?,
                    status = CASE
                        WHEN status = 'Deal' THEN status
                        ELSE 'Revisi'
                    END,
                    revisi = revisi + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    identity["id"],
                    customer_id,
                    tanggal,
                    berlaku_sampai or None,
                    sales or None,
                    totals["subtotal"],
                    totals["discount"],
                    totals["is_ppn"],
                    totals["ppn_rate"],
                    totals["dpp"],
                    totals["ppn_amount"],
                    totals["grand_total"],
                    catatan or None,
                    syarat_ketentuan or None,
                    customer_data["customer_nama_snapshot"],
                    customer_data["customer_perusahaan_snapshot"],
                    customer_data["customer_pic_snapshot"],
                    customer_data["customer_whatsapp_snapshot"],
                    customer_data["customer_email_snapshot"],
                    customer_data["customer_alamat_snapshot"],
                    customer_data["customer_kota_snapshot"],
                    customer_data["customer_status_snapshot"],
                    customer_data["customer_minat_snapshot"],
                    quotation_id,
                ),
            )

            conn.execute(
                """
                DELETE FROM sales_quotation_items
                WHERE quotation_id = ?
                """,
                (quotation_id,),
            )

            insert_quotation_items(
                conn,
                quotation_id,
                prepared_items,
            )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "quotation_detail",
                    quotation_id=quotation_id,
                )
            )

        except (ValueError, sqlite3.Error) as error:
            conn.rollback()
            conn.close()
            return f"Gagal memperbarui penawaran: {error}", 400

    conn.close()

    return render_template(
        "edit_quotation.html",
        quotation=quotation,
        items=existing_items,
        identities=identities_list,
        current_identity=current_identity,
        selected_customer=quotation_customer_selection(quotation),
        initial_items=[
            quotation_item_selection(item)
            for item in existing_items
        ],
    )


@app.route("/quotations/<int:quotation_id>")
def quotation_detail(quotation_id):
    conn = get_connection()

    quotation = get_quotation_with_customer_snapshot(
        conn,
        quotation_id,
    )

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )

    item_rows = conn.execute(
        """
        SELECT *
        FROM sales_quotation_items
        WHERE quotation_id = ?
        ORDER BY id ASC
        """,
        (quotation_id,),
    ).fetchall()
    items = [
        quotation_item_for_display(item)
        for item in item_rows
    ]

    activities = conn.execute(
        """
        SELECT *
        FROM sales_quotation_activities
        WHERE quotation_id = ?
        ORDER BY id DESC
        """,
        (quotation_id,),
    ).fetchall()

    can_convert = identity_allows_transaction_conversion(identity)
    conn.close()

    whatsapp_number = "".join(
        char
        for char in str(
            quotation["customer_whatsapp"] or ""
        )
        if char.isdigit()
    )

    if whatsapp_number.startswith("0"):
        whatsapp_number = "62" + whatsapp_number[1:]

    whatsapp_message = (
        f"Yth. {quotation['customer_nama'] or 'Bapak/Ibu'}, "
        f"berikut penawaran dari {identity['nama_brand']} "
        f"{quotation['nomor_penawaran']} dengan nilai "
        f"Rp {int(quotation['grand_total'] or 0):,.0f}. "
        f"Silakan hubungi kami apabila ada yang ingin didiskusikan."
    ).replace(",", ".")

    quotation_data = dict(quotation)
    quotation_totals = get_effective_quotation_totals(
        quotation,
        identity,
    )
    quotation_data.update(quotation_totals)
    quotation_data["diskon"] = quotation_totals["discount"]
    quotation_data["tanggal_format"] = (
        format_tanggal_indonesia(quotation["tanggal"])
    )
    quotation_data["berlaku_sampai_format"] = (
        format_tanggal_indonesia(quotation["berlaku_sampai"])
        if quotation["berlaku_sampai"]
        else "-"
    )

    return render_template(
        "quotation_detail.html",
        quotation=quotation_data,
        items=items,
        quotation_statuses=QUOTATION_STATUSES,
        whatsapp_number=whatsapp_number,
        whatsapp_message=whatsapp_message,
        activities=activities,
        identity=identity,
        can_convert=can_convert,
    )


@app.route(
    "/quotations/<int:quotation_id>/status",
    methods=["POST"],
)
def update_quotation_status(quotation_id):
    status = request.form.get("status", "").strip()

    if status not in QUOTATION_STATUSES:
        return "Status penawaran tidak valid.", 400

    conn = get_connection()

    exists = conn.execute(
        "SELECT id FROM sales_quotations WHERE id = ?",
        (quotation_id,),
    ).fetchone()

    if exists is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    conn.execute(
        """
        UPDATE sales_quotations
        SET status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, quotation_id),
    )

    add_quotation_activity(
        conn,
        quotation_id,
        "status",
        f"Status penawaran diubah menjadi {status}.",
        "Sistem",
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "quotation_detail",
            quotation_id=quotation_id,
        )
    )


@app.route(
    "/quotations/<int:quotation_id>/print-settings",
    methods=["POST"],
)
def update_quotation_print_settings(quotation_id):
    conn = get_connection()

    quotation = conn.execute(
        """
        SELECT *
        FROM sales_quotations
        WHERE id = ?
        """,
        (quotation_id,),
    ).fetchone()

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )

    conn.execute(
        """
        UPDATE sales_quotations
        SET show_discount = ?,
            show_terbilang = ?,
            show_qr = ?,
            show_catatan = ?,
            show_terms = ?,
            show_bank = ?,
            show_signature = ?,
            show_footer = ?,
            auto_hide_zero = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            checkbox_value(request.form, "show_discount"),
            checkbox_value(request.form, "show_terbilang"),
            (
                checkbox_value(request.form, "show_qr")
                if identity["allow_qr"]
                else 0
            ),
            checkbox_value(request.form, "show_catatan"),
            checkbox_value(request.form, "show_terms"),
            checkbox_value(request.form, "show_bank"),
            (
                checkbox_value(request.form, "show_signature")
                if identity["allow_signature"]
                else 0
            ),
            (
                checkbox_value(request.form, "show_footer")
                if identity["identity_type"] == IDENTITY_TYPE_FULL
                else 0
            ),
            checkbox_value(request.form, "auto_hide_zero"),
            quotation_id,
        ),
    )

    conn.commit()
    conn.close()

    action = request.form.get("action", "save")

    if action == "print":
        return redirect(
            url_for(
                "print_quotation",
                quotation_id=quotation_id,
            )
        )

    return redirect(
        url_for(
            "quotation_detail",
            quotation_id=quotation_id,
        )
    )


@app.route(
    "/quotations/<int:quotation_id>/print"
)
def print_quotation(quotation_id):
    conn = get_connection()

    quotation = get_quotation_with_customer_snapshot(
        conn,
        quotation_id,
    )

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )
    print_settings = get_effective_quotation_print_settings(
        quotation,
        identity,
    )

    item_rows = conn.execute(
        """
        SELECT *
        FROM sales_quotation_items
        WHERE quotation_id = ?
        ORDER BY id ASC
        """,
        (quotation_id,),
    ).fetchall()
    items = [
        quotation_item_for_display(item)
        for item in item_rows
    ]

    add_quotation_activity(
        conn,
        quotation_id,
        "print",
        "Preview PDF penawaran dibuka.",
        quotation["sales"] or "Sistem",
    )

    conn.commit()
    conn.close()

    quotation_data = dict(quotation)
    quotation_totals = get_effective_quotation_totals(
        quotation,
        identity,
    )
    quotation_data.update(quotation_totals)
    quotation_data["diskon"] = quotation_totals["discount"]
    quotation_data["tanggal_format"] = (
        format_tanggal_indonesia(quotation["tanggal"])
    )
    quotation_data["berlaku_sampai_format"] = (
        format_tanggal_indonesia(quotation["berlaku_sampai"])
        if quotation["berlaku_sampai"]
        else "-"
    )
    quotation_data["terbilang"] = (
        angka_ke_terbilang(
            quotation_totals["grand_total"]
        )
        + " Rupiah"
    )

    quotation_url = url_for(
        "print_quotation",
        quotation_id=quotation_id,
        _external=True,
    )

    qr_code_data_uri = None

    if print_settings["show_qr"]:
        qr_payload = (
            f"{identity['nama_brand']}\n"
            f"Penawaran: {quotation['nomor_penawaran']}\n"
            f"Customer: {quotation['customer_nama'] or '-'}\n"
            f"Total: Rp {quotation_totals['grand_total']:,.0f}\n"
            f"Status: {quotation['status']}\n"
            f"URL: {quotation_url}"
        ).replace(",", ".")
        qr_code_data_uri = buat_qr_data_uri(qr_payload)

    show_discount = print_settings["show_discount"]
    auto_hide_zero = bool(quotation["auto_hide_zero"])

    if auto_hide_zero and quotation_totals["discount"] == 0:
        show_discount = False

    return render_template(
        "quotation_print.html",
        quotation=quotation_data,
        items=items,
        identity=identity,
        qr_code_data_uri=qr_code_data_uri,
        show_discount=show_discount,
        show_terbilang=print_settings["show_terbilang"],
        show_qr=print_settings["show_qr"],
        show_catatan=print_settings["show_catatan"],
        show_terms=print_settings["show_terms"],
        show_bank=print_settings["show_bank"],
        show_signature=print_settings["show_signature"],
        show_footer=print_settings["show_footer"],
        show_website_footer=print_settings["show_website_footer"],
    )


@app.route(
    "/quotations/<int:quotation_id>/whatsapp"
)
def send_quotation_whatsapp(quotation_id):
    conn = get_connection()

    quotation = get_quotation_with_customer_snapshot(
        conn,
        quotation_id,
    )

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )

    whatsapp_number = "".join(
        char
        for char in str(quotation["customer_whatsapp"] or "")
        if char.isdigit()
    )

    if whatsapp_number.startswith("0"):
        whatsapp_number = "62" + whatsapp_number[1:]

    if not whatsapp_number:
        conn.close()
        return "Nomor WhatsApp customer belum tersedia.", 400

    message = (
        f"Yth. {quotation['customer_nama'] or 'Bapak/Ibu'}, "
        f"berikut penawaran dari {identity['nama_brand']} "
        f"{quotation['nomor_penawaran']} dengan nilai "
        f"Rp {int(quotation['grand_total'] or 0):,.0f}. "
        f"Silakan hubungi kami apabila ada yang ingin didiskusikan."
    ).replace(",", ".")

    add_quotation_activity(
        conn,
        quotation_id,
        "whatsapp",
        "Tautan pengiriman penawaran melalui WhatsApp dibuka.",
        quotation["sales"] or "Sistem",
    )

    if quotation["status"] == "Draft":
        conn.execute(
            """
            UPDATE sales_quotations
            SET status = 'Terkirim',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (quotation_id,),
        )

        add_quotation_activity(
            conn,
            quotation_id,
            "status",
            "Status otomatis berubah menjadi Terkirim.",
            quotation["sales"] or "Sistem",
        )

    conn.commit()
    conn.close()

    from urllib.parse import quote

    return redirect(
        f"https://wa.me/{whatsapp_number}?text={quote(message)}"
    )


@app.route(
    "/quotations/<int:quotation_id>/duplicate",
    methods=["POST"],
)
def duplicate_quotation(quotation_id):
    conn = get_connection()

    source = get_quotation_with_customer_snapshot(
        conn,
        quotation_id,
    )

    if source is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    source_identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )

    items = conn.execute(
        """
        SELECT *
        FROM sales_quotation_items
        WHERE quotation_id = ?
        ORDER BY id ASC
        """,
        (quotation_id,),
    ).fetchall()

    try:
        duplicated_items = []
        duplicated_subtotal = 0

        for item in items:
            duplicated_item = dict(item)
            duplicated_item["subtotal"] = (
                calculate_quotation_item_subtotal(
                    item["qty"],
                    item["harga_satuan"],
                    item["diskon_item"],
                )
            )
            duplicated_subtotal += duplicated_item["subtotal"]
            duplicated_items.append(duplicated_item)

        totals = calculate_quotation_totals(
            duplicated_subtotal,
            source["diskon"],
            source_identity,
        )

        nomor_baru = generate_document_number(
            conn=conn,
            prefix="QT",
            tanggal=source["tanggal"],
            table_name="sales_quotations",
            column_name="nomor_penawaran",
        )

        cursor = conn.execute(
            """
            INSERT INTO sales_quotations (
                nomor_penawaran,
                customer_id,
                tanggal,
                berlaku_sampai,
                sales,
                status,
                revisi,
                subtotal,
                diskon,
                is_ppn,
                ppn_rate,
                dpp,
                ppn_amount,
                grand_total,
                catatan,
                syarat_ketentuan,
                identity_id,
                show_discount,
                show_terbilang,
                show_qr,
                show_catatan,
                show_terms,
                show_bank,
                show_signature,
                show_footer,
                auto_hide_zero,
                customer_nama_snapshot,
                customer_perusahaan_snapshot,
                customer_pic_snapshot,
                customer_whatsapp_snapshot,
                customer_email_snapshot,
                customer_alamat_snapshot,
                customer_kota_snapshot,
                customer_status_snapshot,
                customer_minat_snapshot
            )
            VALUES (
                ?, ?, ?, ?, ?, 'Draft', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                nomor_baru,
                source["customer_id"],
                source["tanggal"],
                source["berlaku_sampai"],
                source["sales"],
                totals["subtotal"],
                totals["discount"],
                totals["is_ppn"],
                totals["ppn_rate"],
                totals["dpp"],
                totals["ppn_amount"],
                totals["grand_total"],
                source["catatan"],
                source["syarat_ketentuan"],
                source_identity["id"],
                source["show_discount"],
                source["show_terbilang"],
                source["show_qr"],
                source["show_catatan"],
                source["show_terms"],
                source["show_bank"],
                source["show_signature"],
                source["show_footer"],
                source["auto_hide_zero"],
                source["customer_nama"],
                source["customer_instansi"],
                source["customer_pic"],
                source["customer_whatsapp"],
                source["customer_email"],
                source["customer_alamat"],
                source["customer_kota"],
                source["customer_status"],
                source["customer_minat"],
            ),
        )

        new_id = cursor.lastrowid

        for item in duplicated_items:
            conn.execute(
                """
                INSERT INTO sales_quotation_items (
                    quotation_id,
                    product_id,
                    kode_produk_snapshot,
                    nama_produk_snapshot,
                    kategori_snapshot,
                    brand_snapshot,
                    varian_snapshot,
                    warna_snapshot,
                    ukuran_snapshot,
                    satuan_snapshot,
                    subkategori_snapshot,
                    jenis_produk_snapshot,
                    steps_snapshot,
                    spesifikasi_snapshot,
                    harga_modal_snapshot,
                    qty,
                    harga_satuan,
                    diskon_item,
                    subtotal
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    new_id,
                    item["product_id"],
                    item["kode_produk_snapshot"],
                    item["nama_produk_snapshot"],
                    item["kategori_snapshot"],
                    item["brand_snapshot"],
                    item["varian_snapshot"],
                    item["warna_snapshot"],
                    item["ukuran_snapshot"],
                    item["satuan_snapshot"],
                    item["subkategori_snapshot"],
                    item["jenis_produk_snapshot"],
                    item["steps_snapshot"],
                    item["spesifikasi_snapshot"],
                    item["harga_modal_snapshot"],
                    item["qty"],
                    item["harga_satuan"],
                    item["diskon_item"],
                    item["subtotal"],
                ),
            )

        add_quotation_activity(
            conn,
            new_id,
            "duplicated",
            f"Penawaran diduplikasi dari {source['nomor_penawaran']}.",
            source["sales"] or "Sistem",
        )

        add_quotation_activity(
            conn,
            quotation_id,
            "duplicated",
            f"Penawaran diduplikasi menjadi {nomor_baru}.",
            source["sales"] or "Sistem",
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "edit_quotation",
                quotation_id=new_id,
            )
        )

    except sqlite3.Error as error:
        conn.rollback()
        conn.close()
        return f"Gagal menduplikasi penawaran: {error}", 400


@app.route(
    "/quotations/<int:quotation_id>/delete",
    methods=["POST"],
)
def delete_quotation(quotation_id):
    conn = get_connection()

    quotation = conn.execute(
        """
        SELECT converted_transaction_id
        FROM sales_quotations
        WHERE id = ?
        """,
        (quotation_id,),
    ).fetchone()

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    if quotation["converted_transaction_id"]:
        conn.close()
        return (
            "Penawaran yang sudah dikonversi tidak dapat dihapus.",
            400,
        )

    conn.execute(
        """
        DELETE FROM sales_quotations
        WHERE id = ?
        """,
        (quotation_id,),
    )

    conn.commit()
    conn.close()

    return redirect(url_for("quotations"))


@app.route(
    "/quotations/<int:quotation_id>/convert",
    methods=["POST"],
)
def convert_quotation_to_transaction(quotation_id):
    conn = get_connection()

    quotation = conn.execute(
        """
        SELECT *
        FROM sales_quotations
        WHERE id = ?
        """,
        (quotation_id,),
    ).fetchone()

    if quotation is None:
        conn.close()
        return "Penawaran tidak ditemukan.", 404

    if quotation["status"] in ("Batal", "Expired"):
        add_quotation_activity(
            conn,
            quotation_id,
            "blocked",
            f"Konversi ditolak karena status penawaran {quotation['status']}.",
            quotation["sales"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return (
            f"Quotation {quotation['status']} tidak dapat dikonversi menjadi Transaction.",
            400,
        )

    identity = get_effective_identity(
        DOCUMENT_TYPE_QUOTATION,
        quotation_id,
        conn=conn,
    )

    if not identity_allows_transaction_conversion(identity):
        add_quotation_activity(
            conn,
            quotation_id,
            "blocked",
            "Konversi ditolak karena identity tidak mengizinkan Transaction.",
            quotation["sales"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return (
            "Quotation Denko tidak dapat dikonversi menjadi Transaction. "
            "Silakan buat ulang Quotation menggunakan Identity Ahsa.",
            400,
        )

    if quotation["converted_transaction_id"]:
        transaction_id = quotation["converted_transaction_id"]
        conn.close()
        return redirect(
            url_for(
                "transaction_detail",
                transaction_id=transaction_id,
            )
        )

    items = conn.execute(
        """
        SELECT *
        FROM sales_quotation_items
        WHERE quotation_id = ?
        ORDER BY id ASC
        """,
        (quotation_id,),
    ).fetchall()

    try:
        if not items:
            raise ValueError(
                "Quotation tidak memiliki item untuk dikonversi."
            )

        allocation_inputs = []

        for item in items:
            product = get_product_by_id(
                conn,
                item["product_id"],
            )
            qty = max(int(item["qty"] or 0), 0)
            if item["harga_modal_snapshot"] is not None:
                harga_modal = max(
                    int(item["harga_modal_snapshot"] or 0),
                    0,
                )
            else:
                harga_modal = max(
                    int(product["harga_modal_default"] or 0)
                    if product
                    else 0,
                    0,
                )
            allocation_item = dict(item)
            allocation_item["qty"] = qty
            allocation_item["harga_modal"] = harga_modal
            allocation_item["subtotal_modal"] = qty * harga_modal
            allocation_inputs.append(allocation_item)

        allocated_items = allocate_global_discount(
            allocation_inputs,
            quotation["diskon"],
        )

        nomor_transaksi = generate_document_number(
            conn=conn,
            prefix="TRX",
            tanggal=quotation["tanggal"],
            table_name="sales_transactions",
            column_name="nomor_transaksi",
        )

        prepared_items = []

        for item in allocated_items:
            qty = int(item["qty"] or 0)
            harga_jual = int(item["harga_satuan"] or 0)

            prepared_items.append(
                {
                    "product_id": item["product_id"],
                    "kode": item["kode_produk_snapshot"],
                    "nama": item["nama_produk_snapshot"],
                    "kategori": item["kategori_snapshot"],
                    "brand": item["brand_snapshot"],
                    "varian": item["varian_snapshot"],
                    "warna": item["warna_snapshot"],
                    "ukuran": item["ukuran_snapshot"],
                    "satuan": item["satuan_snapshot"],
                    "qty": qty,
                    "harga_jual": harga_jual,
                    "subtotal_penjualan": int(
                        item["subtotal_akhir"]
                    ),
                    "harga_modal": int(item["harga_modal"]),
                    "subtotal_modal": int(
                        item["subtotal_modal"]
                    ),
                    "margin_item": int(item["margin_item"]),
                }
            )

        financials = calculate_transaction_financials(
            prepared_items
        )
        total_penjualan = financials["total_penjualan"]
        total_modal = financials["total_modal"]
        margin = financials["margin"]

        cursor = conn.execute(
            """
            INSERT INTO sales_transactions (
                nomor_transaksi,
                customer_id,
                tanggal,
                jenis_penjualan,
                referal,
                status,
                total_penjualan,
                admin_fee,
                potongan,
                jumlah_diterima,
                total_modal,
                margin,
                biaya_lain,
                keterangan_biaya,
                laba_bersih,
                catatan,
                source_quotation_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nomor_transaksi,
                quotation["customer_id"],
                quotation["tanggal"],
                "Direct",
                quotation["sales"],
                "Closing",
                total_penjualan,
                financials["admin_fee"],
                financials["potongan"],
                financials["jumlah_diterima"],
                total_modal,
                margin,
                financials["biaya_lain"],
                None,
                financials["laba_bersih"],
                (
                    f"Berasal dari penawaran "
                    f"{quotation['nomor_penawaran']}"
                ),
                quotation_id,
            ),
        )

        transaction_id = cursor.lastrowid

        for item in prepared_items:
            conn.execute(
                """
                INSERT INTO sales_transaction_items (
                    transaction_id,
                    product_id,
                    kode_produk_snapshot,
                    nama_produk_snapshot,
                    kategori_snapshot,
                    brand_snapshot,
                    varian_snapshot,
                    warna_snapshot,
                    ukuran_snapshot,
                    satuan_snapshot,
                    qty,
                    harga_jual_satuan,
                    subtotal_penjualan,
                    harga_modal_satuan,
                    subtotal_modal,
                    margin_item
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    item["product_id"],
                    item["kode"],
                    item["nama"],
                    item["kategori"],
                    item["brand"],
                    item["varian"],
                    item["warna"],
                    item["ukuran"],
                    item["satuan"],
                    item["qty"],
                    item["harga_jual"],
                    item["subtotal_penjualan"],
                    item["harga_modal"],
                    item["subtotal_modal"],
                    item["margin_item"],
                ),
            )

        conn.execute(
            """
            UPDATE sales_quotations
            SET status = 'Deal',
                converted_transaction_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (transaction_id, quotation_id),
        )

        add_quotation_activity(
            conn,
            quotation_id,
            "converted",
            f"Penawaran dikonversi menjadi transaksi {nomor_transaksi}.",
            quotation["sales"] or "Sistem",
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "transaction_detail",
                transaction_id=transaction_id,
            )
        )

    except (ValueError, sqlite3.Error) as error:
        conn.rollback()
        conn.close()
        return f"Gagal mengubah penawaran menjadi transaksi: {error}", 400






# ==========================================================
# DELIVERY ORDER / SURAT JALAN
# ==========================================================
def add_delivery_order_activity(
    conn,
    delivery_order_id,
    activity_type,
    description,
    created_by=None,
):
    conn.execute(
        """
        INSERT INTO delivery_order_activities (
            delivery_order_id,
            activity_type,
            description,
            created_by
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            delivery_order_id,
            activity_type,
            description,
            created_by or "Sistem",
        ),
    )


@app.route("/delivery-orders")
def delivery_orders():
    conn = get_connection()

    delivery_order_list = conn.execute(
        """
        SELECT
            delivery_orders.*,
            sales_transactions.nomor_transaksi,
            sales_invoices.nomor_invoice,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi
        FROM delivery_orders
        INNER JOIN sales_transactions
            ON delivery_orders.transaction_id =
               sales_transactions.id
        LEFT JOIN sales_invoices
            ON delivery_orders.invoice_id =
               sales_invoices.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        ORDER BY delivery_orders.id DESC
        """
    ).fetchall()

    available_transactions = conn.execute(
        """
        SELECT
            sales_transactions.id,
            sales_transactions.nomor_transaksi,
            sales_transactions.tanggal,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            sales_invoices.nomor_invoice
        FROM sales_transactions
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        LEFT JOIN sales_invoices
            ON sales_invoices.transaction_id =
               sales_transactions.id
        LEFT JOIN delivery_orders
            ON delivery_orders.transaction_id =
               sales_transactions.id
        WHERE delivery_orders.id IS NULL
          AND sales_transactions.status != 'Batal'
        ORDER BY sales_transactions.id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "delivery_orders.html",
        delivery_orders=delivery_order_list,
        available_transactions=available_transactions,
        delivery_order_statuses=DELIVERY_ORDER_STATUSES,
        format_tanggal_indonesia=format_tanggal_indonesia,
    )


@app.route(
    "/transactions/<int:transaction_id>/delivery-order/generate",
    methods=["POST"],
)
def generate_delivery_order(transaction_id):
    conn = get_connection()
    get_effective_identity(
        DOCUMENT_TYPE_DELIVERY_ORDER,
        conn=conn,
    )

    transaction = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota,
            customers.catatan AS customer_catatan
        FROM sales_transactions
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE sales_transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    if transaction["status"] == "Batal":
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="action_blocked",
            description="Pembuatan Delivery Order ditolak karena transaksi Batal.",
        )
        conn.commit()
        conn.close()
        return "Transaksi Batal tidak dapat membuat Delivery Order.", 400

    existing = conn.execute(
        """
        SELECT id
        FROM delivery_orders
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if existing is not None:
        conn.close()
        return redirect(
            url_for(
                "delivery_order_detail",
                delivery_order_id=existing["id"],
            )
        )

    invoice = conn.execute(
        """
        SELECT id, nomor_invoice
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    transaction_items = conn.execute(
        """
        SELECT *
        FROM sales_transaction_items
        WHERE transaction_id = ?
        ORDER BY id ASC
        """,
        (transaction_id,),
    ).fetchall()

    if not transaction_items:
        conn.close()
        return "Transaksi belum memiliki item produk.", 400

    try:
        nomor_surat_jalan = generate_document_number(
            conn=conn,
            prefix="SJ",
            tanggal=transaction["tanggal"],
            table_name="delivery_orders",
            column_name="nomor_surat_jalan",
        )

        alamat_pengiriman = (
            transaction["customer_catatan"]
            or transaction["customer_kota"]
            or ""
        )

        cursor = conn.execute(
            """
            INSERT INTO delivery_orders (
                nomor_surat_jalan,
                transaction_id,
                invoice_id,
                tanggal,
                status,
                alamat_pengiriman,
                pic_penerima,
                telepon_penerima,
                metode_pengiriman,
                catatan
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nomor_surat_jalan,
                transaction_id,
                invoice["id"] if invoice else None,
                transaction["tanggal"],
                "Draft",
                alamat_pengiriman,
                transaction["customer_nama"],
                transaction["customer_whatsapp"],
                "Kirim Sendiri",
                (
                    f"Surat jalan berasal dari transaksi "
                    f"{transaction['nomor_transaksi']}."
                ),
            ),
        )

        delivery_order_id = cursor.lastrowid

        for item in transaction_items:
            conn.execute(
                """
                INSERT INTO delivery_order_items (
                    delivery_order_id,
                    transaction_item_id,
                    product_id,
                    kode_produk_snapshot,
                    nama_produk_snapshot,
                    kategori_snapshot,
                    brand_snapshot,
                    varian_snapshot,
                    warna_snapshot,
                    ukuran_snapshot,
                    satuan_snapshot,
                    qty,
                    keterangan
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_order_id,
                    item["id"],
                    item["product_id"],
                    item["kode_produk_snapshot"],
                    item["nama_produk_snapshot"],
                    item["kategori_snapshot"],
                    item["brand_snapshot"],
                    item["varian_snapshot"],
                    item["warna_snapshot"],
                    item["ukuran_snapshot"],
                    item["satuan_snapshot"],
                    item["qty"],
                    None,
                ),
            )

        add_delivery_order_activity(
            conn,
            delivery_order_id,
            "created",
            (
                f"Surat jalan dibuat dari transaksi "
                f"{transaction['nomor_transaksi']}."
            ),
            transaction["referal"] or "Sistem",
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "edit_delivery_order",
                delivery_order_id=delivery_order_id,
            )
        )

    except sqlite3.Error as error:
        conn.rollback()
        conn.close()
        return f"Gagal membuat surat jalan: {error}", 400


@app.route(
    "/delivery-orders/<int:delivery_order_id>"
)
def delivery_order_detail(delivery_order_id):
    conn = get_connection()

    delivery_order = conn.execute(
        """
        SELECT
            delivery_orders.*,
            sales_transactions.nomor_transaksi,
            sales_transactions.referal AS sales,
            sales_invoices.nomor_invoice,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM delivery_orders
        INNER JOIN sales_transactions
            ON delivery_orders.transaction_id =
               sales_transactions.id
        LEFT JOIN sales_invoices
            ON delivery_orders.invoice_id =
               sales_invoices.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE delivery_orders.id = ?
        """,
        (delivery_order_id,),
    ).fetchone()

    if delivery_order is None:
        conn.close()
        return "Surat jalan tidak ditemukan.", 404

    items = conn.execute(
        """
        SELECT *
        FROM delivery_order_items
        WHERE delivery_order_id = ?
        ORDER BY id ASC
        """,
        (delivery_order_id,),
    ).fetchall()

    activities = conn.execute(
        """
        SELECT *
        FROM delivery_order_activities
        WHERE delivery_order_id = ?
        ORDER BY id DESC
        """,
        (delivery_order_id,),
    ).fetchall()

    conn.close()

    delivery_order_data = dict(delivery_order)
    delivery_order_data["tanggal_format"] = (
        format_tanggal_indonesia(delivery_order["tanggal"])
    )
    delivery_order_data["tanggal_diterima_format"] = (
        format_tanggal_indonesia(
            delivery_order["tanggal_diterima"]
        )
        if delivery_order["tanggal_diterima"]
        else "-"
    )

    return render_template(
        "delivery_order_detail.html",
        delivery_order=delivery_order_data,
        items=items,
        activities=activities,
        delivery_order_statuses=DELIVERY_ORDER_STATUSES,
    )


@app.route(
    "/delivery-orders/<int:delivery_order_id>/edit",
    methods=["GET", "POST"],
)
def edit_delivery_order(delivery_order_id):
    conn = get_connection()

    delivery_order = conn.execute(
        """
        SELECT
            delivery_orders.*,
            sales_transactions.nomor_transaksi,
            sales_transactions.referal AS sales,
            sales_invoices.nomor_invoice,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM delivery_orders
        INNER JOIN sales_transactions
            ON delivery_orders.transaction_id =
               sales_transactions.id
        LEFT JOIN sales_invoices
            ON delivery_orders.invoice_id =
               sales_invoices.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE delivery_orders.id = ?
        """,
        (delivery_order_id,),
    ).fetchone()

    if delivery_order is None:
        conn.close()
        return "Surat jalan tidak ditemukan.", 404

    if request.method == "POST" and delivery_order["status"] != "Draft":
        record_workflow_event(
            conn,
            document_type="DELIVERY_ORDER",
            document_id=delivery_order_id,
            event_type="action_blocked",
            description="Edit item Delivery Order non-Draft ditolak.",
            created_by=delivery_order["sales"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return "Delivery Order non-Draft tidak dapat diedit itemnya.", 400

    items = conn.execute(
        """
        SELECT *
        FROM delivery_order_items
        WHERE delivery_order_id = ?
        ORDER BY id ASC
        """,
        (delivery_order_id,),
    ).fetchall()

    if request.method == "POST":
        tanggal = request.form.get("tanggal", "").strip()
        alamat_pengiriman = request.form.get(
            "alamat_pengiriman",
            "",
        ).strip()
        pic_penerima = request.form.get(
            "pic_penerima",
            "",
        ).strip()
        telepon_penerima = request.form.get(
            "telepon_penerima",
            "",
        ).strip()
        metode_pengiriman = request.form.get(
            "metode_pengiriman",
            "",
        ).strip()
        ekspedisi = request.form.get("ekspedisi", "").strip()
        nomor_resi = request.form.get("nomor_resi", "").strip()
        driver = request.form.get("driver", "").strip()
        kendaraan = request.form.get("kendaraan", "").strip()
        nomor_polisi = request.form.get(
            "nomor_polisi",
            "",
        ).strip()
        jam_keluar = request.form.get("jam_keluar", "").strip()
        estimasi_tiba = request.form.get(
            "estimasi_tiba",
            "",
        ).strip()
        tanggal_diterima = request.form.get(
            "tanggal_diterima",
            "",
        ).strip()
        jam_diterima = request.form.get(
            "jam_diterima",
            "",
        ).strip()
        catatan = request.form.get("catatan", "").strip()

        item_ids = request.form.getlist("item_id[]")
        qty_values = request.form.getlist("qty[]")
        keterangan_values = request.form.getlist(
            "keterangan_item[]"
        )

        if not tanggal:
            conn.close()
            return "Tanggal surat jalan wajib diisi.", 400

        if metode_pengiriman not in DELIVERY_METHODS:
            conn.close()
            return "Metode pengiriman tidak valid.", 400

        if not (
            len(item_ids)
            == len(qty_values)
            == len(keterangan_values)
        ):
            conn.close()
            return "Data item surat jalan tidak lengkap.", 400

        try:
            conn.execute(
                """
                UPDATE delivery_orders
                SET tanggal = ?,
                    alamat_pengiriman = ?,
                    pic_penerima = ?,
                    telepon_penerima = ?,
                    metode_pengiriman = ?,
                    ekspedisi = ?,
                    nomor_resi = ?,
                    driver = ?,
                    kendaraan = ?,
                    nomor_polisi = ?,
                    jam_keluar = ?,
                    estimasi_tiba = ?,
                    tanggal_diterima = ?,
                    jam_diterima = ?,
                    catatan = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    tanggal,
                    alamat_pengiriman or None,
                    pic_penerima or None,
                    telepon_penerima or None,
                    metode_pengiriman,
                    ekspedisi or None,
                    nomor_resi or None,
                    driver or None,
                    kendaraan or None,
                    nomor_polisi or None,
                    jam_keluar or None,
                    estimasi_tiba or None,
                    tanggal_diterima or None,
                    jam_diterima or None,
                    catatan or None,
                    delivery_order_id,
                ),
            )

            for index, item_id_raw in enumerate(item_ids):
                item_id = int(item_id_raw)
                qty = parse_integer(qty_values[index])

                if qty <= 0:
                    raise ValueError(
                        f"Qty item ke-{index + 1} harus lebih dari 0."
                    )

                conn.execute(
                    """
                    UPDATE delivery_order_items
                    SET qty = ?,
                        keterangan = ?
                    WHERE id = ?
                      AND delivery_order_id = ?
                    """,
                    (
                        qty,
                        keterangan_values[index].strip() or None,
                        item_id,
                        delivery_order_id,
                    ),
                )

            add_delivery_order_activity(
                conn,
                delivery_order_id,
                "updated",
                "Data pengiriman dan item surat jalan diperbarui.",
                delivery_order["sales"] or "Sistem",
            )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "delivery_order_detail",
                    delivery_order_id=delivery_order_id,
                )
            )

        except (ValueError, sqlite3.Error) as error:
            conn.rollback()
            conn.close()
            return f"Gagal memperbarui surat jalan: {error}", 400

    conn.close()

    return render_template(
        "delivery_order_edit.html",
        delivery_order=delivery_order,
        items=items,
        delivery_methods=DELIVERY_METHODS,
    )


@app.route(
    "/delivery-orders/<int:delivery_order_id>/status",
    methods=["POST"],
)
def update_delivery_order_status(delivery_order_id):
    status = request.form.get("status", "").strip()

    if status not in DELIVERY_ORDER_STATUSES:
        return "Status surat jalan tidak valid.", 400

    conn = get_connection()

    delivery_order = conn.execute(
        """
        SELECT
            delivery_orders.*,
            delivery_orders.transaction_id,
            sales_transactions.customer_id,
            sales_transactions.referal AS sales
        FROM delivery_orders
        INNER JOIN sales_transactions
            ON delivery_orders.transaction_id =
               sales_transactions.id
        WHERE delivery_orders.id = ?
        """,
        (delivery_order_id,),
    ).fetchone()

    if delivery_order is None:
        conn.close()
        return "Surat jalan tidak ditemukan.", 404

    try:
        should_change = validate_transition(
            "DELIVERY_ORDER",
            delivery_order["status"],
            status,
        )
        if not should_change:
            conn.close()
            return redirect(
                url_for(
                    "delivery_order_detail",
                    delivery_order_id=delivery_order_id,
                )
            )

        if status == "Terkirim":
            post_stock_for_document(
                conn,
                "DELIVERY_ORDER",
                delivery_order_id,
                actor=delivery_order["sales"] or "Sistem",
            )
        elif status == "Batal":
            reverse_stock_for_document(
                conn,
                "DELIVERY_ORDER",
                delivery_order_id,
                actor=delivery_order["sales"] or "Sistem",
            )

        conn.execute(
            """
            UPDATE delivery_orders
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, delivery_order_id),
        )
        add_delivery_order_activity(
            conn,
            delivery_order_id,
            "status",
            f"Status surat jalan diubah menjadi {status}.",
            delivery_order["sales"] or "Sistem",
        )
        record_workflow_event(
            conn,
            document_type="DELIVERY_ORDER",
            document_id=delivery_order_id,
            customer_id=delivery_order["customer_id"],
            event_type="status_changed",
            old_status=delivery_order["status"],
            new_status=status,
            description=f"Status Delivery Order diubah menjadi {status}.",
            created_by=delivery_order["sales"] or "Sistem",
        )
        sync_transaction_status(
            conn,
            delivery_order["transaction_id"],
            reason="Transaction disinkronkan dari status Delivery Order.",
            actor=delivery_order["sales"] or "Sistem",
        )
        conn.commit()
        conn.close()

    except (WorkflowIntegrityError, sqlite3.Error) as error:
        conn.rollback()
        conn.close()
        return f"Gagal memperbarui status surat jalan: {error}", 400

    return redirect(
        url_for(
            "delivery_order_detail",
            delivery_order_id=delivery_order_id,
        )
    )


@app.route(
    "/delivery-orders/<int:delivery_order_id>/print"
)
def print_delivery_order(delivery_order_id):
    conn = get_connection()
    identity = get_effective_identity(
        DOCUMENT_TYPE_DELIVERY_ORDER,
        conn=conn,
    )

    delivery_order = conn.execute(
        """
        SELECT
            delivery_orders.*,
            sales_transactions.nomor_transaksi,
            sales_transactions.referal AS sales,
            sales_invoices.nomor_invoice,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM delivery_orders
        INNER JOIN sales_transactions
            ON delivery_orders.transaction_id =
               sales_transactions.id
        LEFT JOIN sales_invoices
            ON delivery_orders.invoice_id =
               sales_invoices.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE delivery_orders.id = ?
        """,
        (delivery_order_id,),
    ).fetchone()

    if delivery_order is None:
        conn.close()
        return "Surat jalan tidak ditemukan.", 404

    items = conn.execute(
        """
        SELECT *
        FROM delivery_order_items
        WHERE delivery_order_id = ?
        ORDER BY id ASC
        """,
        (delivery_order_id,),
    ).fetchall()

    add_delivery_order_activity(
        conn,
        delivery_order_id,
        "print",
        "Preview PDF surat jalan dibuka.",
        delivery_order["sales"] or "Sistem",
    )

    conn.commit()
    conn.close()

    delivery_order_data = dict(delivery_order)
    delivery_order_data["tanggal_format"] = (
        format_tanggal_indonesia(delivery_order["tanggal"])
    )

    qr_payload = (
        f"{identity['nama_brand']}\n"
        f"Surat Jalan: {delivery_order['nomor_surat_jalan']}\n"
        f"Transaksi: {delivery_order['nomor_transaksi']}\n"
        f"Customer: {delivery_order['customer_nama'] or '-'}\n"
        f"Status: {delivery_order['status']}"
    )

    qr_code_data_uri = buat_qr_data_uri(qr_payload)

    return render_template(
        "delivery_order_print.html",
        delivery_order=delivery_order_data,
        items=items,
        qr_code_data_uri=qr_code_data_uri,
        identity=identity,
    )


@app.route(
    "/delivery-orders/<int:delivery_order_id>/delete",
    methods=["POST"],
)
def delete_delivery_order(delivery_order_id):
    conn = get_connection()

    delivery_order = conn.execute(
        """
        SELECT status
        FROM delivery_orders
        WHERE id = ?
        """,
        (delivery_order_id,),
    ).fetchone()

    if delivery_order is None:
        conn.close()
        return "Surat jalan tidak ditemukan.", 404

    if delivery_order["status"] not in ("Draft", "Batal"):
        conn.close()
        return (
            "Hanya surat jalan Draft atau Batal yang dapat dihapus.",
            400,
        )

    movement_count = conn.execute(
        """
        SELECT COUNT(*) FROM stock_movements
        WHERE source_type = 'DELIVERY_ORDER' AND source_id = ?
        """,
        (delivery_order_id,),
    ).fetchone()[0]
    if movement_count:
        conn.close()
        return "Surat jalan dengan stock movement tidak dapat dihapus.", 400

    conn.execute(
        """
        DELETE FROM delivery_orders
        WHERE id = ?
        """,
        (delivery_order_id,),
    )

    conn.commit()
    conn.close()

    return redirect(url_for("delivery_orders"))






# ==========================================================
# RECEIPTS / KWITANSI
# ==========================================================
def add_receipt_activity(
    conn,
    receipt_id,
    activity_type,
    description,
    created_by=None,
):
    conn.execute(
        """
        INSERT INTO payment_receipt_activities (
            receipt_id,
            activity_type,
            description,
            created_by
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            receipt_id,
            activity_type,
            description,
            created_by or "Sistem",
        ),
    )


def calculate_invoice_payment_summary(conn, invoice_id):
    try:
        return reconcile_invoice_payment(conn, invoice_id)
    except WorkflowIntegrityError:
        return None


@app.route("/receipts")
def receipts():
    conn = get_connection()

    receipt_list = conn.execute(
        """
        SELECT
            payment_receipts.*,
            sales_invoices.nomor_invoice,
            sales_transactions.nomor_transaksi,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi
        FROM payment_receipts
        INNER JOIN sales_invoices
            ON payment_receipts.invoice_id =
               sales_invoices.id
        INNER JOIN sales_transactions
            ON payment_receipts.transaction_id =
               sales_transactions.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        ORDER BY payment_receipts.id DESC
        """
    ).fetchall()

    invoice_list = conn.execute(
        """
        SELECT
            sales_invoices.id,
            sales_invoices.nomor_invoice,
            sales_invoices.status_pembayaran,
            sales_transactions.nomor_transaksi,
            sales_transactions.total_penjualan,
            sales_transactions.potongan,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi
        FROM sales_invoices
        INNER JOIN sales_transactions
            ON sales_invoices.transaction_id =
               sales_transactions.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE sales_invoices.status_pembayaran != 'Batal'
        ORDER BY sales_invoices.id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "receipts.html",
        receipts=receipt_list,
        invoices=invoice_list,
        format_tanggal_indonesia=format_tanggal_indonesia,
    )


@app.route(
    "/invoices/<int:invoice_id>/receipts/add",
    methods=["GET", "POST"],
)
def add_receipt(invoice_id):
    conn = get_connection()
    get_effective_identity(
        DOCUMENT_TYPE_RECEIPT,
        conn=conn,
    )

    invoice = conn.execute(
        """
        SELECT
            sales_invoices.*,
            sales_transactions.nomor_transaksi,
            sales_transactions.referal AS sales,
            sales_transactions.total_penjualan,
            sales_transactions.potongan,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM sales_invoices
        INNER JOIN sales_transactions
            ON sales_invoices.transaction_id =
               sales_transactions.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE sales_invoices.id = ?
        """,
        (invoice_id,),
    ).fetchone()

    if invoice is None:
        conn.close()
        return "Invoice tidak ditemukan.", 404

    if invoice["status_pembayaran"] == "Batal":
        record_workflow_event(
            conn,
            document_type="INVOICE",
            document_id=invoice_id,
            event_type="action_blocked",
            description="Pembuatan Receipt ditolak karena Invoice Batal.",
            created_by=invoice["sales"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return "Invoice Batal tidak dapat menerima Receipt.", 400

    summary = calculate_invoice_payment_summary(conn, invoice_id)

    if request.method == "POST":
        tanggal = request.form.get("tanggal", "").strip()
        jenis_pembayaran = request.form.get(
            "jenis_pembayaran",
            "",
        ).strip()
        metode_pembayaran = request.form.get(
            "metode_pembayaran",
            "",
        ).strip()
        bank = request.form.get("bank", "").strip()
        nomor_referensi = request.form.get(
            "nomor_referensi",
            "",
        ).strip()
        nominal = parse_integer(
            request.form.get("nominal", "0")
        )
        untuk_pembayaran = request.form.get(
            "untuk_pembayaran",
            "",
        ).strip()
        catatan = request.form.get("catatan", "").strip()

        if not tanggal:
            conn.close()
            return "Tanggal kwitansi wajib diisi.", 400

        if jenis_pembayaran not in RECEIPT_TYPES:
            conn.close()
            return "Jenis pembayaran tidak valid.", 400

        if metode_pembayaran not in RECEIPT_METHODS:
            conn.close()
            return "Metode pembayaran tidak valid.", 400

        if nominal <= 0:
            conn.close()
            return "Nominal pembayaran harus lebih dari 0.", 400

        if not untuk_pembayaran:
            untuk_pembayaran = (
                f"{jenis_pembayaran} Invoice "
                f"{invoice['nomor_invoice']}"
            )

        try:
            idempotency_key = normalize_idempotency_key(
                request.form.get("idempotency_key"),
                "RECEIPT",
                invoice_id,
                tanggal,
                jenis_pembayaran,
                metode_pembayaran,
                bank,
                nomor_referensi,
                nominal,
                untuk_pembayaran,
            )
        except WorkflowIntegrityError as error:
            conn.close()
            return str(error), 400

        existing_receipt = conn.execute(
            """
            SELECT id FROM payment_receipts
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if existing_receipt:
            conn.close()
            return redirect(
                url_for(
                    "receipt_detail",
                    receipt_id=existing_receipt["id"],
                )
            )

        if nominal > summary["sisa_tagihan"]:
            conn.close()
            return (
                "Nominal pembayaran melebihi sisa tagihan.",
                400,
            )

        try:
            nomor_kwitansi = generate_document_number(
                conn=conn,
                prefix="KWT",
                tanggal=tanggal,
                table_name="payment_receipts",
                column_name="nomor_kwitansi",
            )

            cursor = conn.execute(
                """
                INSERT INTO payment_receipts (
                    nomor_kwitansi,
                    invoice_id,
                    transaction_id,
                    tanggal,
                    jenis_pembayaran,
                    metode_pembayaran,
                    bank,
                    nomor_referensi,
                    nominal,
                    untuk_pembayaran,
                    catatan,
                    status,
                    idempotency_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    nomor_kwitansi,
                    invoice_id,
                    invoice["transaction_id"],
                    tanggal,
                    jenis_pembayaran,
                    metode_pembayaran,
                    bank or None,
                    nomor_referensi or None,
                    nominal,
                    untuk_pembayaran,
                    catatan or None,
                    "Diterbitkan",
                    idempotency_key,
                ),
            )

            receipt_id = cursor.lastrowid

            add_receipt_activity(
                conn,
                receipt_id,
                "created",
                (
                    f"Kwitansi dibuat untuk pembayaran "
                    f"{jenis_pembayaran} sebesar "
                    f"Rp {nominal:,.0f}."
                ).replace(",", "."),
                invoice["sales"] or "Sistem",
            )
            record_workflow_event(
                conn,
                document_type="RECEIPT",
                document_id=receipt_id,
                event_type="payment_created",
                description=(
                    f"Payment Rp {nominal:,.0f} dicatat."
                ).replace(",", "."),
                idempotency_key=f"EVENT:{idempotency_key}",
                created_by=invoice["sales"] or "Sistem",
            )

            calculate_invoice_payment_summary(
                conn,
                invoice_id,
            )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "receipt_detail",
                    receipt_id=receipt_id,
                )
            )

        except (WorkflowIntegrityError, sqlite3.Error) as error:
            conn.rollback()
            conn.close()
            return f"Gagal membuat kwitansi: {error}", 400

    conn.commit()
    conn.close()

    return render_template(
        "receipt_add.html",
        invoice=invoice,
        summary=summary,
        receipt_types=RECEIPT_TYPES,
        receipt_methods=RECEIPT_METHODS,
    )


@app.route("/receipts/<int:receipt_id>")
def receipt_detail(receipt_id):
    conn = get_connection()

    receipt = conn.execute(
        """
        SELECT
            payment_receipts.*,
            sales_invoices.nomor_invoice,
            sales_transactions.nomor_transaksi,
            sales_transactions.referal AS sales,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM payment_receipts
        INNER JOIN sales_invoices
            ON payment_receipts.invoice_id =
               sales_invoices.id
        INNER JOIN sales_transactions
            ON payment_receipts.transaction_id =
               sales_transactions.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE payment_receipts.id = ?
        """,
        (receipt_id,),
    ).fetchone()

    if receipt is None:
        conn.close()
        return "Kwitansi tidak ditemukan.", 404

    activities = conn.execute(
        """
        SELECT *
        FROM payment_receipt_activities
        WHERE receipt_id = ?
        ORDER BY id DESC
        """,
        (receipt_id,),
    ).fetchall()

    summary = calculate_invoice_payment_summary(
        conn,
        receipt["invoice_id"],
    )

    conn.commit()
    conn.close()

    receipt_data = dict(receipt)
    receipt_data["tanggal_format"] = (
        format_tanggal_indonesia(receipt["tanggal"])
    )
    receipt_data["terbilang"] = (
        angka_ke_terbilang(
            int(receipt["nominal"] or 0)
        )
        + " Rupiah"
    )

    return render_template(
        "receipt_detail.html",
        receipt=receipt_data,
        activities=activities,
        summary=summary,
        receipt_statuses=RECEIPT_STATUSES,
    )


@app.route(
    "/receipts/<int:receipt_id>/status",
    methods=["POST"],
)
def update_receipt_status(receipt_id):
    status = request.form.get("status", "").strip()

    if status not in RECEIPT_STATUSES:
        return "Status kwitansi tidak valid.", 400

    conn = get_connection()

    receipt = conn.execute(
        """
        SELECT
            payment_receipts.*,
            sales_transactions.referal AS sales
        FROM payment_receipts
        INNER JOIN sales_transactions
            ON payment_receipts.transaction_id =
               sales_transactions.id
        WHERE payment_receipts.id = ?
        """,
        (receipt_id,),
    ).fetchone()

    if receipt is None:
        conn.close()
        return "Kwitansi tidak ditemukan.", 404

    if receipt["status"] == status:
        conn.close()
        return redirect(
            url_for("receipt_detail", receipt_id=receipt_id)
        )

    if receipt["status"] == "Void" or status != "Void":
        conn.close()
        return "Receipt Void bersifat final dan tidak dapat diaktifkan kembali.", 400

    conn.execute(
        """
        UPDATE payment_receipts
        SET status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, receipt_id),
    )

    add_receipt_activity(
        conn,
        receipt_id,
        "status",
        f"Status kwitansi diubah menjadi {status}.",
        receipt["sales"] or "Sistem",
    )
    record_workflow_event(
        conn,
        document_type="RECEIPT",
        document_id=receipt_id,
        event_type="payment_voided",
        old_status=receipt["status"],
        new_status="Void",
        description="Payment di-Void dan invoice dihitung ulang.",
        idempotency_key=f"EVENT:RECEIPT:{receipt_id}:VOID",
        created_by=receipt["sales"] or "Sistem",
    )

    calculate_invoice_payment_summary(
        conn,
        receipt["invoice_id"],
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "receipt_detail",
            receipt_id=receipt_id,
        )
    )


@app.route("/receipts/<int:receipt_id>/print")
def print_receipt(receipt_id):
    conn = get_connection()
    identity = get_effective_identity(
        DOCUMENT_TYPE_RECEIPT,
        conn=conn,
    )

    receipt = conn.execute(
        """
        SELECT
            payment_receipts.*,
            sales_invoices.nomor_invoice,
            sales_transactions.nomor_transaksi,
            sales_transactions.referal AS sales,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi,
            customers.whatsapp AS customer_whatsapp,
            customers.kota AS customer_kota
        FROM payment_receipts
        INNER JOIN sales_invoices
            ON payment_receipts.invoice_id =
               sales_invoices.id
        INNER JOIN sales_transactions
            ON payment_receipts.transaction_id =
               sales_transactions.id
        LEFT JOIN customers
            ON sales_transactions.customer_id =
               customers.id
        WHERE payment_receipts.id = ?
        """,
        (receipt_id,),
    ).fetchone()

    if receipt is None:
        conn.close()
        return "Kwitansi tidak ditemukan.", 404

    add_receipt_activity(
        conn,
        receipt_id,
        "print",
        "Preview PDF kwitansi dibuka.",
        receipt["sales"] or "Sistem",
    )

    conn.commit()
    conn.close()

    receipt_data = dict(receipt)
    receipt_data["tanggal_format"] = (
        format_tanggal_indonesia(receipt["tanggal"])
    )
    receipt_data["terbilang"] = (
        angka_ke_terbilang(
            int(receipt["nominal"] or 0)
        )
        + " Rupiah"
    )

    qr_payload = (
        f"{identity['nama_brand']}\n"
        f"Kwitansi: {receipt['nomor_kwitansi']}\n"
        f"Invoice: {receipt['nomor_invoice']}\n"
        f"Customer: {receipt['customer_nama'] or '-'}\n"
        f"Nominal: Rp {int(receipt['nominal'] or 0):,.0f}\n"
        f"Status: {receipt['status']}"
    ).replace(",", ".")

    qr_code_data_uri = buat_qr_data_uri(qr_payload)

    return render_template(
        "receipt_print.html",
        receipt=receipt_data,
        qr_code_data_uri=qr_code_data_uri,
        identity=identity,
    )


@app.route(
    "/receipts/<int:receipt_id>/delete",
    methods=["POST"],
)
def delete_receipt(receipt_id):
    conn = get_connection()

    receipt = conn.execute(
        """
        SELECT invoice_id, status
        FROM payment_receipts
        WHERE id = ?
        """,
        (receipt_id,),
    ).fetchone()

    if receipt is None:
        conn.close()
        return "Kwitansi tidak ditemukan.", 404

    conn.close()
    return (
        "Receipt tidak dapat dihapus. Gunakan Void agar audit pembayaran tetap utuh.",
        400,
    )






# ==========================================================
# OPTIONAL INVENTORY / WAREHOUSE
# ==========================================================
def inventory_is_enabled(conn):
    row = conn.execute("SELECT inventory_enabled FROM erp_settings WHERE id = 1").fetchone()
    return bool(row and row["inventory_enabled"])

@app.route("/settings/inventory", methods=["GET", "POST"])
def inventory_settings():
    conn = get_connection()
    if request.method == "POST":
        enabled = 1 if request.form.get("inventory_enabled") == "1" else 0
        default_warehouse_id = parse_integer(
            request.form.get("default_warehouse_id"),
            0,
        )
        if default_warehouse_id:
            warehouse = conn.execute(
                "SELECT id FROM warehouses WHERE id = ? AND aktif = 1",
                (default_warehouse_id,),
            ).fetchone()
            if warehouse is None:
                conn.close()
                return "Default warehouse tidak valid.", 400
        conn.execute(
            """
            UPDATE erp_settings
            SET inventory_enabled = ?,
                default_warehouse_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (enabled, default_warehouse_id or None),
        )
        conn.commit(); conn.close()
        return redirect(url_for("inventory_settings"))
    settings = conn.execute("SELECT * FROM erp_settings WHERE id = 1").fetchone()
    warehouse_rows = conn.execute(
        "SELECT id, kode_gudang, nama_gudang FROM warehouses WHERE aktif = 1 ORDER BY nama_gudang"
    ).fetchall()
    conn.close()
    return render_template(
        "inventory_settings.html",
        settings=settings,
        warehouses=warehouse_rows,
    )

@app.route("/warehouses")
def warehouses():
    conn = get_connection()
    if not inventory_is_enabled(conn):
        conn.close(); return redirect(url_for("inventory_settings"))
    rows = conn.execute("""
        SELECT warehouses.*, COUNT(DISTINCT product_stock.product_id) AS total_produk,
               COALESCE(SUM(product_stock.stok), 0) AS total_stok
        FROM warehouses
        LEFT JOIN product_stock ON warehouses.id = product_stock.warehouse_id
        GROUP BY warehouses.id ORDER BY warehouses.id DESC
    """).fetchall()
    conn.close()
    return render_template("warehouses.html", warehouses=rows)

@app.route("/warehouses/add", methods=["GET", "POST"])
def add_warehouse():
    conn = get_connection()
    if not inventory_is_enabled(conn):
        conn.close(); return redirect(url_for("inventory_settings"))
    if request.method == "POST":
        kode = request.form.get("kode_gudang", "").strip()
        nama = request.form.get("nama_gudang", "").strip()
        alamat = request.form.get("alamat", "").strip()
        pic = request.form.get("penanggung_jawab", "").strip()
        aktif = 1 if request.form.get("aktif") == "1" else 0
        if not kode or not nama:
            conn.close(); return "Kode dan nama gudang wajib diisi.", 400
        try:
            conn.execute("INSERT INTO warehouses (kode_gudang,nama_gudang,alamat,penanggung_jawab,aktif) VALUES (?,?,?,?,?)", (kode,nama,alamat or None,pic or None,aktif))
            conn.commit(); conn.close(); return redirect(url_for("warehouses"))
        except sqlite3.IntegrityError:
            conn.rollback(); conn.close(); return "Kode gudang sudah digunakan.", 400
    conn.close(); return render_template("warehouse_add.html")

@app.route("/warehouses/<int:warehouse_id>")
def warehouse_detail(warehouse_id):
    conn = get_connection()
    if not inventory_is_enabled(conn):
        conn.close(); return redirect(url_for("inventory_settings"))
    warehouse = conn.execute("SELECT * FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
    if warehouse is None:
        conn.close(); return "Gudang tidak ditemukan.", 404
    stocks = conn.execute("""
        SELECT product_stock.*, products.kode_produk, products.nama_produk, products.satuan
        FROM product_stock INNER JOIN products ON product_stock.product_id = products.id
        WHERE product_stock.warehouse_id = ? ORDER BY products.nama_produk
    """, (warehouse_id,)).fetchall()
    summary = conn.execute("""
        SELECT COUNT(*) AS total_produk, COALESCE(SUM(stok),0) AS total_stok,
               COALESCE(SUM(CASE WHEN stok <= minimum_stok THEN 1 ELSE 0 END),0) AS stok_menipis
        FROM product_stock WHERE warehouse_id = ?
    """, (warehouse_id,)).fetchone()
    conn.close(); return render_template("warehouse_detail.html", warehouse=warehouse, stocks=stocks, summary=summary)

@app.route("/stocks")
def stocks():
    conn = get_connection()
    if not inventory_is_enabled(conn):
        conn.close(); return redirect(url_for("inventory_settings"))
    rows = conn.execute("""
        SELECT product_stock.*, products.kode_produk, products.nama_produk, products.satuan,
               warehouses.kode_gudang, warehouses.nama_gudang
        FROM product_stock
        INNER JOIN products ON product_stock.product_id = products.id
        INNER JOIN warehouses ON product_stock.warehouse_id = warehouses.id
        ORDER BY warehouses.nama_gudang, products.nama_produk
    """).fetchall()
    conn.close(); return render_template("stocks.html", stocks=rows)

@app.route("/stocks/setup", methods=["GET", "POST"])
def setup_stock():
    conn = get_connection()
    if not inventory_is_enabled(conn):
        conn.close(); return redirect(url_for("inventory_settings"))
    warehouses_list = conn.execute("SELECT * FROM warehouses WHERE aktif = 1 ORDER BY nama_gudang").fetchall()
    products_list = conn.execute("SELECT id,kode_produk,nama_produk,satuan FROM products WHERE status_aktif = 1 ORDER BY nama_produk").fetchall()
    if request.method == "POST":
        warehouse_id = parse_integer(request.form.get("warehouse_id"))
        product_id = parse_integer(request.form.get("product_id"))
        stok_awal = parse_integer(request.form.get("stok_awal"))
        minimum = parse_integer(request.form.get("minimum_stok"))
        tanggal = request.form.get("tanggal", "").strip()
        catatan = request.form.get("catatan", "").strip()
        if warehouse_id <= 0 or product_id <= 0 or not tanggal:
            conn.close(); return "Gudang, produk, dan tanggal wajib diisi.", 400
        if stok_awal < 0 or minimum < 0:
            conn.close(); return "Stok tidak boleh negatif.", 400
        try:
            post_opening_stock(
                conn,
                warehouse_id=warehouse_id,
                product_id=product_id,
                quantity=stok_awal,
                minimum_stock=minimum,
                tanggal=tanggal,
                catatan=catatan,
                idempotency_key=request.form.get("idempotency_key"),
            )
            conn.commit(); conn.close(); return redirect(url_for("stocks"))
        except (WorkflowIntegrityError, sqlite3.Error) as error:
            conn.rollback()
            conn.close()
            return f"Gagal mencatat opening stock: {error}", 400
    conn.close(); return render_template("stock_setup.html", warehouses=warehouses_list, products=products_list)

@app.route("/stocks/<int:warehouse_id>/<int:product_id>/movements")
def stock_movements(warehouse_id, product_id):
    conn = get_connection()
    if not inventory_is_enabled(conn):
        conn.close(); return redirect(url_for("inventory_settings"))
    stock = conn.execute("""
        SELECT product_stock.*, products.kode_produk, products.nama_produk, products.satuan,
               warehouses.kode_gudang, warehouses.nama_gudang
        FROM product_stock
        INNER JOIN products ON product_stock.product_id = products.id
        INNER JOIN warehouses ON product_stock.warehouse_id = warehouses.id
        WHERE product_stock.warehouse_id=? AND product_stock.product_id=?
    """, (warehouse_id,product_id)).fetchone()
    if stock is None:
        conn.close(); return "Data stok tidak ditemukan.", 404
    movements = conn.execute("SELECT * FROM stock_movements WHERE warehouse_id=? AND product_id=? ORDER BY tanggal DESC,id DESC", (warehouse_id,product_id)).fetchall()
    conn.close(); return render_template("stock_movements.html", stock=stock, movements=movements, format_tanggal_indonesia=format_tanggal_indonesia)










# ==========================================================
# SPRINT 10.3 — GENERATE PURCHASE ORDER DARI INVOICE
# ==========================================================
@app.route(
    "/transactions/<int:transaction_id>/invoice/purchase-order/generate",
    methods=["GET", "POST"],
)
def generate_purchase_order_from_invoice(transaction_id):
    conn = get_connection()

    transaction = conn.execute(
        """
        SELECT
            sales_transactions.*,
            customers.nama AS customer_nama,
            customers.instansi AS customer_instansi
        FROM sales_transactions
        LEFT JOIN customers
            ON sales_transactions.customer_id = customers.id
        WHERE sales_transactions.id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        conn.close()
        return "Transaksi tidak ditemukan.", 404

    if transaction["status"] == "Batal":
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="action_blocked",
            description="Pembuatan Purchase Order ditolak karena Transaction Batal.",
            created_by=transaction["referal"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return "Transaction Batal tidak dapat membuat Purchase Order.", 400

    invoice = conn.execute(
        """
        SELECT *
        FROM sales_invoices
        WHERE transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if invoice is None:
        conn.close()
        return (
            "Invoice belum dibuat. Buat invoice terlebih dahulu.",
            400,
        )

    if invoice["status_pembayaran"] == "Batal":
        record_workflow_event(
            conn,
            document_type="INVOICE",
            document_id=invoice["id"],
            customer_id=transaction["customer_id"],
            event_type="action_blocked",
            description="Pembuatan Purchase Order ditolak karena Invoice Batal.",
            created_by=transaction["referal"] or "Sistem",
        )
        conn.commit()
        conn.close()
        return "Invoice Batal tidak dapat membuat Purchase Order.", 400

    existing_po = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE invoice_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (invoice["id"],),
    ).fetchone()

    if existing_po is not None:
        conn.close()
        return redirect(
            url_for(
                "purchase_order_detail",
                purchase_order_id=existing_po["id"],
            )
        )

    invoice_items = conn.execute(
        """
        SELECT *
        FROM sales_transaction_items
        WHERE transaction_id = ?
        ORDER BY id ASC
        """,
        (transaction_id,),
    ).fetchall()

    suppliers = conn.execute(
        """
        SELECT *
        FROM suppliers
        WHERE status = 'Aktif'
        ORDER BY nama_supplier COLLATE NOCASE
        """
    ).fetchall()

    if request.method == "POST":
        supplier_id = parse_integer(
            request.form.get("supplier_id"),
            0,
        )
        estimasi_datang = request.form.get(
            "estimasi_datang",
            "",
        ).strip()
        payment_term = max(
            parse_integer(
                request.form.get("payment_term"),
                0,
            ),
            0,
        )
        ppn_persen = parse_decimal(
            request.form.get("ppn_persen"),
            0,
        )
        ongkir = max(
            parse_integer(request.form.get("ongkir"), 0),
            0,
        )
        biaya_lain = max(
            parse_integer(
                request.form.get("biaya_lain"),
                0,
            ),
            0,
        )
        catatan = request.form.get(
            "catatan",
            "",
        ).strip()
        syarat_ketentuan = request.form.get(
            "syarat_ketentuan",
            "",
        ).strip()

        if supplier_id <= 0:
            conn.close()
            return "Supplier wajib dipilih.", 400

        if ppn_persen not in PURCHASE_ORDER_PPN_OPTIONS:
            ppn_persen = 0

        supplier = conn.execute(
            """
            SELECT *
            FROM suppliers
            WHERE id = ?
            """,
            (supplier_id,),
        ).fetchone()

        if supplier is None:
            conn.close()
            return "Supplier tidak ditemukan.", 404

        if not invoice_items:
            conn.close()
            return "Invoice tidak memiliki item.", 400

        prepared_items = []
        subtotal = 0

        for index, item in enumerate(invoice_items, start=1):
            qty = float(item["qty"] or 0)
            harga_modal = int(
                item["harga_modal_satuan"] or 0
            )
            item_subtotal = round(qty * harga_modal)
            subtotal += item_subtotal

            description_parts = [
                item["kategori_snapshot"],
                item["brand_snapshot"],
                item["varian_snapshot"],
                item["warna_snapshot"],
                item["ukuran_snapshot"],
            ]
            description = " • ".join(
                str(value)
                for value in description_parts
                if value
            )

            prepared_items.append(
                {
                    "product_id": item["product_id"],
                    "kode_produk_snapshot": (
                        item["kode_produk_snapshot"]
                    ),
                    "nama_produk_snapshot": (
                        item["nama_produk_snapshot"]
                    ),
                    "deskripsi_snapshot": (
                        description or None
                    ),
                    "satuan_snapshot": (
                        item["satuan_snapshot"] or "Unit"
                    ),
                    "qty": qty,
                    "harga_satuan": harga_modal,
                    "diskon_persen": 0,
                    "diskon_nilai": 0,
                    "subtotal": item_subtotal,
                    "urutan": index,
                }
            )

        ppn_nilai = round(
            subtotal * ppn_persen / 100
        )
        grand_total = (
            subtotal
            + ppn_nilai
            + ongkir
            + biaya_lain
        )

        try:
            nomor_po = generate_number_from_settings(
                "PURCHASE_ORDER",
                conn=conn,
                commit=False,
            )

            cursor = conn.execute(
                """
                INSERT INTO purchase_orders (
                    nomor_po,
                    supplier_id,
                    invoice_id,
                    transaction_id,
                    tanggal,
                    estimasi_datang,
                    status,
                    supplier_nama_snapshot,
                    supplier_alamat_snapshot,
                    supplier_pic_snapshot,
                    supplier_whatsapp_snapshot,
                    supplier_email_snapshot,
                    supplier_npwp_snapshot,
                    payment_term,
                    subtotal,
                    diskon,
                    ppn_persen,
                    ppn_nilai,
                    ongkir,
                    biaya_lain,
                    grand_total,
                    catatan,
                    syarat_ketentuan
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, 'Draft',
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, 0, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    nomor_po,
                    supplier_id,
                    invoice["id"],
                    transaction_id,
                    datetime.now().strftime("%Y-%m-%d"),
                    estimasi_datang or None,
                    supplier["nama_supplier"],
                    supplier["alamat"],
                    supplier["pic"],
                    supplier["whatsapp"]
                    or supplier["telepon"],
                    supplier["email"],
                    supplier["npwp"],
                    payment_term
                    or supplier["payment_term"]
                    or 0,
                    subtotal,
                    ppn_persen,
                    ppn_nilai,
                    ongkir,
                    biaya_lain,
                    grand_total,
                    catatan or None,
                    syarat_ketentuan or None,
                ),
            )

            purchase_order_id = cursor.lastrowid

            for item in prepared_items:
                conn.execute(
                    """
                    INSERT INTO purchase_order_items (
                        purchase_order_id,
                        product_id,
                        kode_produk_snapshot,
                        nama_produk_snapshot,
                        deskripsi_snapshot,
                        satuan_snapshot,
                        qty,
                        harga_satuan,
                        diskon_persen,
                        diskon_nilai,
                        subtotal,
                        urutan
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        purchase_order_id,
                        item["product_id"],
                        item["kode_produk_snapshot"],
                        item["nama_produk_snapshot"],
                        item["deskripsi_snapshot"],
                        item["satuan_snapshot"],
                        item["qty"],
                        item["harga_satuan"],
                        item["diskon_persen"],
                        item["diskon_nilai"],
                        item["subtotal"],
                        item["urutan"],
                    ),
                )

            conn.execute(
                """
                UPDATE sales_invoices
                SET purchase_order_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    purchase_order_id,
                    invoice["id"],
                ),
            )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "edit_purchase_order",
                    purchase_order_id=purchase_order_id,
                )
            )

        except sqlite3.Error as error:
            conn.rollback()
            conn.close()
            return (
                f"Gagal membuat Purchase Order: {error}",
                400,
            )

    default_terms = (
        "Harga sudah sesuai kesepakatan.\n"
        "Barang dikirim sesuai estimasi pada PO.\n"
        "Mohon cantumkan nomor PO pada invoice dan surat jalan."
    )

    conn.close()

    return render_template(
        "generate_po_from_invoice.html",
        transaction=transaction,
        invoice=invoice,
        items=invoice_items,
        suppliers=suppliers,
        ppn_options=PURCHASE_ORDER_PPN_OPTIONS,
        today=datetime.now().strftime("%Y-%m-%d"),
        default_terms=default_terms,
        format_rupiah=format_rupiah,
    )


# ==========================================================
# SPRINT 10.2 — PURCHASE ORDER
# ==========================================================
@app.route("/purchase-orders")
def purchase_orders():
    keyword = request.args.get("q", "").strip()
    supplier_filter = parse_integer(
        request.args.get("supplier_id"),
        0,
    )
    status_filter = request.args.get(
        "status",
        "",
    ).strip()
    date_from = request.args.get(
        "date_from",
        "",
    ).strip()
    date_to = request.args.get(
        "date_to",
        "",
    ).strip()

    conn = get_connection()

    conditions = []
    params = []

    if keyword:
        conditions.append(
            """
            (
                purchase_orders.nomor_po LIKE ?
                OR purchase_orders.supplier_nama_snapshot LIKE ?
                OR purchase_orders.catatan LIKE ?
            )
            """
        )
        search_value = f"%{keyword}%"
        params.extend(
            [
                search_value,
                search_value,
                search_value,
            ]
        )

    if supplier_filter > 0:
        conditions.append(
            "purchase_orders.supplier_id = ?"
        )
        params.append(supplier_filter)

    if status_filter in PURCHASE_ORDER_STATUSES:
        conditions.append(
            "purchase_orders.status = ?"
        )
        params.append(status_filter)

    if date_from:
        conditions.append(
            "purchase_orders.tanggal >= ?"
        )
        params.append(date_from)

    if date_to:
        conditions.append(
            "purchase_orders.tanggal <= ?"
        )
        params.append(date_to)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(
            conditions
        )

    po_rows = conn.execute(
        f"""
        SELECT
            purchase_orders.*,
            suppliers.kode_supplier
        FROM purchase_orders
        JOIN suppliers
            ON purchase_orders.supplier_id = suppliers.id
        {where_clause}
        ORDER BY
            purchase_orders.tanggal DESC,
            purchase_orders.id DESC
        """,
        tuple(params),
    ).fetchall()

    stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total_po,
            SUM(CASE WHEN status = 'Draft' THEN 1 ELSE 0 END)
                AS total_draft,
            SUM(
                CASE
                    WHEN status IN (
                        'Dikirim',
                        'Diproses Supplier',
                        'Barang Diterima'
                    )
                    THEN 1
                    ELSE 0
                END
            ) AS total_proses,
            SUM(CASE WHEN status = 'Selesai' THEN 1 ELSE 0 END)
                AS total_selesai,
            SUM(CASE WHEN status = 'Batal' THEN 1 ELSE 0 END)
                AS total_batal,
            COALESCE(
                SUM(
                    CASE
                        WHEN status != 'Batal'
                        THEN grand_total
                        ELSE 0
                    END
                ),
                0
            ) AS total_nilai
        FROM purchase_orders
        """
    ).fetchone()

    supplier_rows = conn.execute(
        """
        SELECT id, kode_supplier, nama_supplier
        FROM suppliers
        WHERE status = 'Aktif'
        ORDER BY nama_supplier COLLATE NOCASE
        """
    ).fetchall()

    conn.close()

    return render_template(
        "purchase_orders.html",
        purchase_orders=po_rows,
        stats=stats,
        suppliers=supplier_rows,
        statuses=PURCHASE_ORDER_STATUSES,
        keyword=keyword,
        supplier_filter=supplier_filter,
        status_filter=status_filter,
        date_from=date_from,
        date_to=date_to,
        format_rupiah=format_rupiah,
        format_tanggal_indonesia=format_tanggal_indonesia,
    )


@app.route(
    "/purchase-orders/add",
    methods=["GET", "POST"],
)
def add_purchase_order():
    conn = get_connection()
    get_effective_identity(
        DOCUMENT_TYPE_PURCHASE_ORDER,
        conn=conn,
    )

    supplier_rows = conn.execute(
        """
        SELECT *
        FROM suppliers
        WHERE status = 'Aktif'
        ORDER BY nama_supplier COLLATE NOCASE
        """
    ).fetchall()

    products_list = get_products_for_form(conn)

    if request.method == "POST":
        try:
            prepared_items, subtotal = (
                prepare_purchase_order_items(
                    conn,
                    request.form,
                )
            )
            header = normalize_purchase_order_header(
                request.form,
                subtotal,
            )
            # Status awal selalu Draft; transition stock wajib melalui
            # endpoint status agar posting dan rollback tidak dapat dilewati.
            header["status"] = "Draft"

            supplier = conn.execute(
                """
                SELECT *
                FROM suppliers
                WHERE id = ?
                """,
                (header["supplier_id"],),
            ).fetchone()

            if supplier is None:
                raise ValueError(
                    "Supplier tidak ditemukan."
                )

            nomor_po = generate_number_from_settings(
                "PURCHASE_ORDER",
                conn=conn,
                commit=False,
            )

            cursor = conn.execute(
                """
                INSERT INTO purchase_orders (
                    nomor_po,
                    supplier_id,
                    tanggal,
                    estimasi_datang,
                    status,
                    supplier_nama_snapshot,
                    supplier_alamat_snapshot,
                    supplier_pic_snapshot,
                    supplier_whatsapp_snapshot,
                    supplier_email_snapshot,
                    supplier_npwp_snapshot,
                    payment_term,
                    subtotal,
                    diskon,
                    ppn_persen,
                    ppn_nilai,
                    ongkir,
                    biaya_lain,
                    grand_total,
                    catatan,
                    syarat_ketentuan,
                    dikirim_pada,
                    selesai_pada
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    nomor_po,
                    header["supplier_id"],
                    header["tanggal"],
                    header["estimasi_datang"],
                    header["status"],
                    supplier["nama_supplier"],
                    supplier["alamat"],
                    supplier["pic"],
                    supplier["whatsapp"]
                    or supplier["telepon"],
                    supplier["email"],
                    supplier["npwp"],
                    header["payment_term"]
                    or supplier["payment_term"]
                    or 0,
                    header["subtotal"],
                    header["diskon"],
                    header["ppn_persen"],
                    header["ppn_nilai"],
                    header["ongkir"],
                    header["biaya_lain"],
                    header["grand_total"],
                    header["catatan"],
                    header["syarat_ketentuan"],
                    (
                        datetime.now()
                        if header["status"] != "Draft"
                        else None
                    ),
                    (
                        datetime.now()
                        if header["status"] == "Selesai"
                        else None
                    ),
                ),
            )

            purchase_order_id = cursor.lastrowid

            for item in prepared_items:
                conn.execute(
                    """
                    INSERT INTO purchase_order_items (
                        purchase_order_id,
                        product_id,
                        kode_produk_snapshot,
                        nama_produk_snapshot,
                        deskripsi_snapshot,
                        satuan_snapshot,
                        qty,
                        harga_satuan,
                        diskon_persen,
                        diskon_nilai,
                        subtotal,
                        urutan
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        purchase_order_id,
                        item["product_id"],
                        item["kode_produk_snapshot"],
                        item["nama_produk_snapshot"],
                        item["deskripsi_snapshot"],
                        item["satuan_snapshot"],
                        item["qty"],
                        item["harga_satuan"],
                        item["diskon_persen"],
                        item["diskon_nilai"],
                        item["subtotal"],
                        item["urutan"],
                    ),
                )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "purchase_order_detail",
                    purchase_order_id=purchase_order_id,
                )
            )

        except ValueError as error:
            conn.rollback()
            conn.close()
            return str(error), 400

        except sqlite3.Error as error:
            conn.rollback()
            conn.close()
            return (
                f"Gagal menyimpan Purchase Order: {error}",
                400,
            )

    default_terms = (
        "Harga sudah sesuai kesepakatan.\n"
        "Barang dikirim sesuai estimasi pada PO.\n"
        "Mohon cantumkan nomor PO pada invoice dan surat jalan."
    )

    conn.close()

    return render_template(
        "purchase_order_form.html",
        page_title="Buat Purchase Order",
        purchase_order=None,
        items=[],
        suppliers=supplier_rows,
        products=products_list,
        statuses=PURCHASE_ORDER_STATUSES,
        ppn_options=PURCHASE_ORDER_PPN_OPTIONS,
        today=datetime.now().strftime("%Y-%m-%d"),
        default_terms=default_terms,
    )


@app.route(
    "/purchase-orders/<int:purchase_order_id>"
)
def purchase_order_detail(purchase_order_id):
    conn = get_connection()
    purchase_order, items = get_purchase_order_full(
        conn,
        purchase_order_id,
    )

    if purchase_order is None:
        conn.close()
        return "Purchase Order tidak ditemukan.", 404

    conn.close()

    return render_template(
        "purchase_order_detail.html",
        purchase_order=purchase_order,
        items=items,
        statuses=PURCHASE_ORDER_STATUSES,
        format_rupiah=format_rupiah,
        format_tanggal_indonesia=format_tanggal_indonesia,
    )


@app.route(
    "/purchase-orders/<int:purchase_order_id>/edit",
    methods=["GET", "POST"],
)
def edit_purchase_order(purchase_order_id):
    conn = get_connection()
    purchase_order, items = get_purchase_order_full(
        conn,
        purchase_order_id,
    )

    if purchase_order is None:
        conn.close()
        return "Purchase Order tidak ditemukan.", 404

    if request.method == "POST" and purchase_order["status"] != "Draft":
        record_workflow_event(
            conn,
            document_type="PURCHASE_ORDER",
            document_id=purchase_order_id,
            event_type="action_blocked",
            description="Edit finansial PO non-Draft ditolak.",
        )
        conn.commit()
        conn.close()
        return "Purchase Order non-Draft tidak dapat diedit finansial.", 400

    supplier_rows = conn.execute(
        """
        SELECT *
        FROM suppliers
        ORDER BY
            CASE status
                WHEN 'Aktif' THEN 1
                ELSE 2
            END,
            nama_supplier COLLATE NOCASE
        """
    ).fetchall()

    products_list = get_products_for_form(conn)

    if request.method == "POST":
        try:
            prepared_items, subtotal = (
                prepare_purchase_order_items(
                    conn,
                    request.form,
                )
            )
            header = normalize_purchase_order_header(
                request.form,
                subtotal,
            )
            # Edit finansial Draft tidak boleh sekaligus menjalankan
            # transition workflow/stock posting.
            header["status"] = purchase_order["status"]

            supplier = conn.execute(
                """
                SELECT *
                FROM suppliers
                WHERE id = ?
                """,
                (header["supplier_id"],),
            ).fetchone()

            if supplier is None:
                raise ValueError(
                    "Supplier tidak ditemukan."
                )

            sent_at = purchase_order["dikirim_pada"]
            completed_at = purchase_order["selesai_pada"]

            if (
                header["status"] != "Draft"
                and sent_at is None
            ):
                sent_at = datetime.now()

            if (
                header["status"] == "Selesai"
                and completed_at is None
            ):
                completed_at = datetime.now()

            conn.execute(
                """
                UPDATE purchase_orders
                SET supplier_id = ?,
                    tanggal = ?,
                    estimasi_datang = ?,
                    status = ?,
                    supplier_nama_snapshot = ?,
                    supplier_alamat_snapshot = ?,
                    supplier_pic_snapshot = ?,
                    supplier_whatsapp_snapshot = ?,
                    supplier_email_snapshot = ?,
                    supplier_npwp_snapshot = ?,
                    payment_term = ?,
                    subtotal = ?,
                    diskon = ?,
                    ppn_persen = ?,
                    ppn_nilai = ?,
                    ongkir = ?,
                    biaya_lain = ?,
                    grand_total = ?,
                    catatan = ?,
                    syarat_ketentuan = ?,
                    dikirim_pada = ?,
                    selesai_pada = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    header["supplier_id"],
                    header["tanggal"],
                    header["estimasi_datang"],
                    header["status"],
                    supplier["nama_supplier"],
                    supplier["alamat"],
                    supplier["pic"],
                    supplier["whatsapp"]
                    or supplier["telepon"],
                    supplier["email"],
                    supplier["npwp"],
                    header["payment_term"]
                    or supplier["payment_term"]
                    or 0,
                    header["subtotal"],
                    header["diskon"],
                    header["ppn_persen"],
                    header["ppn_nilai"],
                    header["ongkir"],
                    header["biaya_lain"],
                    header["grand_total"],
                    header["catatan"],
                    header["syarat_ketentuan"],
                    sent_at,
                    completed_at,
                    purchase_order_id,
                ),
            )

            conn.execute(
                """
                DELETE FROM purchase_order_items
                WHERE purchase_order_id = ?
                """,
                (purchase_order_id,),
            )

            for item in prepared_items:
                conn.execute(
                    """
                    INSERT INTO purchase_order_items (
                        purchase_order_id,
                        product_id,
                        kode_produk_snapshot,
                        nama_produk_snapshot,
                        deskripsi_snapshot,
                        satuan_snapshot,
                        qty,
                        harga_satuan,
                        diskon_persen,
                        diskon_nilai,
                        subtotal,
                        urutan
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        purchase_order_id,
                        item["product_id"],
                        item["kode_produk_snapshot"],
                        item["nama_produk_snapshot"],
                        item["deskripsi_snapshot"],
                        item["satuan_snapshot"],
                        item["qty"],
                        item["harga_satuan"],
                        item["diskon_persen"],
                        item["diskon_nilai"],
                        item["subtotal"],
                        item["urutan"],
                    ),
                )

            conn.commit()
            conn.close()

            return redirect(
                url_for(
                    "purchase_order_detail",
                    purchase_order_id=purchase_order_id,
                )
            )

        except ValueError as error:
            conn.rollback()
            conn.close()
            return str(error), 400

        except sqlite3.Error as error:
            conn.rollback()
            conn.close()
            return (
                f"Gagal memperbarui Purchase Order: {error}",
                400,
            )

    conn.close()

    return render_template(
        "purchase_order_form.html",
        page_title="Edit Purchase Order",
        purchase_order=purchase_order,
        items=items,
        suppliers=supplier_rows,
        products=products_list,
        statuses=PURCHASE_ORDER_STATUSES,
        ppn_options=PURCHASE_ORDER_PPN_OPTIONS,
        today=datetime.now().strftime("%Y-%m-%d"),
        default_terms="",
    )


@app.route(
    "/purchase-orders/<int:purchase_order_id>/status",
    methods=["POST"],
)
def update_purchase_order_status(purchase_order_id):
    status = request.form.get("status", "").strip()

    if status not in PURCHASE_ORDER_STATUSES:
        return "Status Purchase Order tidak valid.", 400

    conn = get_connection()

    purchase_order = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        """,
        (purchase_order_id,),
    ).fetchone()

    if purchase_order is None:
        conn.close()
        return "Purchase Order tidak ditemukan.", 404

    try:
        should_change = validate_transition(
            "PURCHASE_ORDER",
            purchase_order["status"],
            status,
        )
        if not should_change:
            conn.close()
            return redirect(
                url_for(
                    "purchase_order_detail",
                    purchase_order_id=purchase_order_id,
                )
            )

        if status == "Barang Diterima":
            post_stock_for_document(
                conn,
                "PURCHASE_ORDER",
                purchase_order_id,
            )
        elif status == "Batal":
            reverse_stock_for_document(
                conn,
                "PURCHASE_ORDER",
                purchase_order_id,
            )

        sent_at = purchase_order["dikirim_pada"]
        completed_at = purchase_order["selesai_pada"]
        if status != "Draft" and sent_at is None:
            sent_at = datetime.now()
        if status == "Selesai" and completed_at is None:
            completed_at = datetime.now()

        conn.execute(
            """
            UPDATE purchase_orders
            SET status = ?,
                dikirim_pada = ?,
                selesai_pada = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, sent_at, completed_at, purchase_order_id),
        )
        record_workflow_event(
            conn,
            document_type="PURCHASE_ORDER",
            document_id=purchase_order_id,
            event_type="status_changed",
            old_status=purchase_order["status"],
            new_status=status,
            description=f"Status Purchase Order diubah menjadi {status}.",
        )
        conn.commit()
        conn.close()
    except (WorkflowIntegrityError, sqlite3.Error) as error:
        conn.rollback()
        conn.close()
        return f"Gagal mengubah status Purchase Order: {error}", 400

    return redirect(
        url_for(
            "purchase_order_detail",
            purchase_order_id=purchase_order_id,
        )
    )


@app.route(
    "/purchase-orders/<int:purchase_order_id>/duplicate",
    methods=["POST"],
)
def duplicate_purchase_order(purchase_order_id):
    conn = get_connection()
    purchase_order, items = get_purchase_order_full(
        conn,
        purchase_order_id,
    )

    if purchase_order is None:
        conn.close()
        return "Purchase Order tidak ditemukan.", 404

    try:
        new_number = generate_number_from_settings(
            "PURCHASE_ORDER",
            conn=conn,
            commit=False,
        )

        cursor = conn.execute(
            """
            INSERT INTO purchase_orders (
                nomor_po,
                supplier_id,
                invoice_id,
                transaction_id,
                tanggal,
                estimasi_datang,
                status,
                supplier_nama_snapshot,
                supplier_alamat_snapshot,
                supplier_pic_snapshot,
                supplier_whatsapp_snapshot,
                supplier_email_snapshot,
                supplier_npwp_snapshot,
                payment_term,
                subtotal,
                diskon,
                ppn_persen,
                ppn_nilai,
                ongkir,
                biaya_lain,
                grand_total,
                catatan,
                syarat_ketentuan
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, 'Draft', ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                new_number,
                purchase_order["supplier_id"],
                None,
                None,
                datetime.now().strftime("%Y-%m-%d"),
                purchase_order["estimasi_datang"],
                purchase_order["supplier_nama_snapshot"],
                purchase_order["supplier_alamat_snapshot"],
                purchase_order["supplier_pic_snapshot"],
                purchase_order["supplier_whatsapp_snapshot"],
                purchase_order["supplier_email_snapshot"],
                purchase_order["supplier_npwp_snapshot"],
                purchase_order["payment_term"],
                purchase_order["subtotal"],
                purchase_order["diskon"],
                purchase_order["ppn_persen"],
                purchase_order["ppn_nilai"],
                purchase_order["ongkir"],
                purchase_order["biaya_lain"],
                purchase_order["grand_total"],
                purchase_order["catatan"],
                purchase_order["syarat_ketentuan"],
            ),
        )

        new_id = cursor.lastrowid

        for item in items:
            conn.execute(
                """
                INSERT INTO purchase_order_items (
                    purchase_order_id,
                    product_id,
                    kode_produk_snapshot,
                    nama_produk_snapshot,
                    deskripsi_snapshot,
                    satuan_snapshot,
                    qty,
                    harga_satuan,
                    diskon_persen,
                    diskon_nilai,
                    subtotal,
                    urutan
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    item["product_id"],
                    item["kode_produk_snapshot"],
                    item["nama_produk_snapshot"],
                    item["deskripsi_snapshot"],
                    item["satuan_snapshot"],
                    item["qty"],
                    item["harga_satuan"],
                    item["diskon_persen"],
                    item["diskon_nilai"],
                    item["subtotal"],
                    item["urutan"],
                ),
            )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "edit_purchase_order",
                purchase_order_id=new_id,
            )
        )

    except sqlite3.Error as error:
        conn.rollback()
        conn.close()
        return (
            f"Gagal menduplikasi Purchase Order: {error}",
            400,
        )


@app.route(
    "/purchase-orders/<int:purchase_order_id>/delete",
    methods=["POST"],
)
def delete_purchase_order(purchase_order_id):
    conn = get_connection()

    purchase_order = conn.execute(
        """
        SELECT *
        FROM purchase_orders
        WHERE id = ?
        """,
        (purchase_order_id,),
    ).fetchone()

    if purchase_order is None:
        conn.close()
        return "Purchase Order tidak ditemukan.", 404

    if purchase_order["status"] not in ("Draft", "Batal"):
        conn.close()
        return (
            "PO hanya dapat dihapus ketika berstatus Draft atau Batal.",
            400,
        )

    movement_count = conn.execute(
        """
        SELECT COUNT(*) FROM stock_movements
        WHERE source_type = 'PURCHASE_ORDER' AND source_id = ?
        """,
        (purchase_order_id,),
    ).fetchone()[0]
    if movement_count:
        conn.close()
        return "PO yang mempunyai stock movement tidak dapat dihapus.", 400

    conn.execute(
        """
        DELETE FROM purchase_order_items
        WHERE purchase_order_id = ?
        """,
        (purchase_order_id,),
    )
    conn.execute(
        """
        DELETE FROM purchase_orders
        WHERE id = ?
        """,
        (purchase_order_id,),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("purchase_orders"))


@app.route(
    "/purchase-orders/<int:purchase_order_id>/print"
)
def print_purchase_order(purchase_order_id):
    conn = get_connection()
    purchase_order, items = get_purchase_order_full(
        conn,
        purchase_order_id,
    )

    if purchase_order is None:
        conn.close()
        return "Purchase Order tidak ditemukan.", 404

    company = get_effective_identity(
        DOCUMENT_TYPE_PURCHASE_ORDER,
        conn=conn,
    )

    print_settings = conn.execute(
        """
        SELECT
            po_show_cost_details,
            po_show_company_footer
        FROM erp_settings
        WHERE id = 1
        """
    ).fetchone()

    qr_code = buat_qr_data_uri(
        purchase_order_qr_text(purchase_order)
    )

    conn.close()

    return render_template(
        "purchase_order_print.html",
        purchase_order=purchase_order,
        items=items,
        company=company,
        print_settings=print_settings,
        qr_code=qr_code,
        format_rupiah=format_rupiah,
        format_tanggal_indonesia=format_tanggal_indonesia,
    )


# ==========================================================
# SPRINT 10.1 — MASTER SUPPLIER
# ==========================================================
@app.route("/suppliers")
def suppliers():
    keyword = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    type_filter = request.args.get("jenis", "").strip()

    conn = get_connection()

    conditions = []
    params = []

    if keyword:
        conditions.append(
            """
            (
                kode_supplier LIKE ?
                OR nama_supplier LIKE ?
                OR pic LIKE ?
                OR whatsapp LIKE ?
                OR telepon LIKE ?
                OR email LIKE ?
                OR kota LIKE ?
            )
            """
        )
        search_value = f"%{keyword}%"
        params.extend([search_value] * 7)

    if status_filter in SUPPLIER_STATUSES:
        conditions.append("status = ?")
        params.append(status_filter)

    if type_filter in SUPPLIER_TYPES:
        conditions.append("jenis_supplier = ?")
        params.append(type_filter)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    supplier_rows = conn.execute(
        f"""
        SELECT *
        FROM suppliers
        {where_clause}
        ORDER BY
            CASE status
                WHEN 'Aktif' THEN 1
                ELSE 2
            END,
            nama_supplier COLLATE NOCASE ASC
        """,
        tuple(params),
    ).fetchall()

    stats = conn.execute(
        """
        SELECT
            COUNT(*) AS total_supplier,
            SUM(CASE WHEN status = 'Aktif' THEN 1 ELSE 0 END)
                AS supplier_aktif,
            SUM(CASE WHEN status = 'Nonaktif' THEN 1 ELSE 0 END)
                AS supplier_nonaktif
        FROM suppliers
        """
    ).fetchone()

    purchase_summary = supplier_purchase_summary(conn)
    conn.close()

    return render_template(
        "suppliers.html",
        suppliers=supplier_rows,
        stats=stats,
        purchase_summary=purchase_summary,
        keyword=keyword,
        status_filter=status_filter,
        type_filter=type_filter,
        supplier_types=SUPPLIER_TYPES,
        supplier_statuses=SUPPLIER_STATUSES,
        format_rupiah=format_rupiah,
    )


@app.route("/suppliers/add", methods=["GET", "POST"])
def add_supplier():
    if request.method == "POST":
        data = normalize_supplier_form(request.form)

        if not data["nama_supplier"]:
            return "Nama supplier wajib diisi.", 400

        conn = get_connection()

        try:
            kode_supplier = generate_number_from_settings(
                "SUPPLIER",
                conn=conn,
                commit=False,
            )

            conn.execute(
                """
                INSERT INTO suppliers (
                    kode_supplier,
                    nama,
                    nama_supplier,
                    jenis_supplier,
                    alamat,
                    kota,
                    provinsi,
                    kode_pos,
                    pic,
                    jabatan,
                    telepon,
                    whatsapp,
                    email,
                    website,
                    npwp,
                    bank,
                    no_rekening,
                    atas_nama,
                    payment_term,
                    status,
                    catatan,
                    status_aktif
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    kode_supplier,
                    data["nama_supplier"],
                    data["nama_supplier"],
                    data["jenis_supplier"],
                    data["alamat"] or None,
                    data["kota"] or None,
                    data["provinsi"] or None,
                    data["kode_pos"] or None,
                    data["pic"] or None,
                    data["jabatan"] or None,
                    data["telepon"] or None,
                    data["whatsapp"] or None,
                    data["email"] or None,
                    data["website"] or None,
                    data["npwp"] or None,
                    data["bank"] or None,
                    data["no_rekening"] or None,
                    data["atas_nama"] or None,
                    data["payment_term"],
                    data["status"],
                    data["catatan"] or None,
                    1 if data["status"] == "Aktif" else 0,
                ),
            )

            supplier_id = conn.execute(
                "SELECT last_insert_rowid() AS id"
            ).fetchone()["id"]

            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise

        conn.close()

        return redirect(
            url_for(
                "supplier_detail",
                supplier_id=supplier_id,
            )
        )

    return render_template(
        "supplier_form.html",
        page_title="Tambah Supplier",
        supplier=None,
        supplier_types=SUPPLIER_TYPES,
        supplier_statuses=SUPPLIER_STATUSES,
        payment_terms=SUPPLIER_PAYMENT_TERMS,
    )


@app.route("/suppliers/<int:supplier_id>")
def supplier_detail(supplier_id):
    conn = get_connection()

    supplier = conn.execute(
        """
        SELECT *
        FROM suppliers
        WHERE id = ?
        """,
        (supplier_id,),
    ).fetchone()

    if supplier is None:
        conn.close()
        return "Supplier tidak ditemukan.", 404

    purchase_summary = supplier_purchase_summary(
        conn,
        supplier_id,
    )

    conn.close()

    return render_template(
        "supplier_detail.html",
        supplier=supplier,
        purchase_summary=purchase_summary,
        format_rupiah=format_rupiah,
    )


@app.route(
    "/suppliers/<int:supplier_id>/edit",
    methods=["GET", "POST"],
)
def edit_supplier(supplier_id):
    conn = get_connection()

    supplier = conn.execute(
        """
        SELECT *
        FROM suppliers
        WHERE id = ?
        """,
        (supplier_id,),
    ).fetchone()

    if supplier is None:
        conn.close()
        return "Supplier tidak ditemukan.", 404

    if request.method == "POST":
        data = normalize_supplier_form(request.form)

        if not data["nama_supplier"]:
            conn.close()
            return "Nama supplier wajib diisi.", 400

        conn.execute(
            """
            UPDATE suppliers
            SET nama = ?,
                nama_supplier = ?,
                jenis_supplier = ?,
                alamat = ?,
                kota = ?,
                provinsi = ?,
                kode_pos = ?,
                pic = ?,
                jabatan = ?,
                telepon = ?,
                whatsapp = ?,
                email = ?,
                website = ?,
                npwp = ?,
                bank = ?,
                no_rekening = ?,
                atas_nama = ?,
                payment_term = ?,
                status = ?,
                status_aktif = ?,
                catatan = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                data["nama_supplier"],
                data["nama_supplier"],
                data["jenis_supplier"],
                data["alamat"] or None,
                data["kota"] or None,
                data["provinsi"] or None,
                data["kode_pos"] or None,
                data["pic"] or None,
                data["jabatan"] or None,
                data["telepon"] or None,
                data["whatsapp"] or None,
                data["email"] or None,
                data["website"] or None,
                data["npwp"] or None,
                data["bank"] or None,
                data["no_rekening"] or None,
                data["atas_nama"] or None,
                data["payment_term"],
                data["status"],
                1 if data["status"] == "Aktif" else 0,
                data["catatan"] or None,
                supplier_id,
            ),
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "supplier_detail",
                supplier_id=supplier_id,
            )
        )

    conn.close()

    return render_template(
        "supplier_form.html",
        page_title="Edit Supplier",
        supplier=supplier,
        supplier_types=SUPPLIER_TYPES,
        supplier_statuses=SUPPLIER_STATUSES,
        payment_terms=SUPPLIER_PAYMENT_TERMS,
    )


@app.route(
    "/suppliers/<int:supplier_id>/delete",
    methods=["POST"],
)
def delete_supplier(supplier_id):
    conn = get_connection()

    supplier = conn.execute(
        """
        SELECT *
        FROM suppliers
        WHERE id = ?
        """,
        (supplier_id,),
    ).fetchone()

    if supplier is None:
        conn.close()
        return "Supplier tidak ditemukan.", 404

    if sqlite_table_exists(conn, "purchase_orders"):
        po_count = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM purchase_orders
            WHERE supplier_id = ?
            """,
            (supplier_id,),
        ).fetchone()["total"]

        if po_count > 0:
            conn.close()
            return (
                "Supplier tidak dapat dihapus karena sudah memiliki "
                "riwayat Purchase Order. Ubah status menjadi Nonaktif.",
                400,
            )

    conn.execute(
        """
        DELETE FROM suppliers
        WHERE id = ?
        """,
        (supplier_id,),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("suppliers"))




@app.route(
    "/settings/purchase-order-print",
    methods=["GET", "POST"],
)
def purchase_order_print_settings():
    conn = get_connection()

    if request.method == "POST":
        show_cost_details = (
            1
            if request.form.get("po_show_cost_details") == "1"
            else 0
        )
        show_company_footer = (
            1
            if request.form.get("po_show_company_footer") == "1"
            else 0
        )

        conn.execute(
            """
            UPDATE erp_settings
            SET po_show_cost_details = ?,
                po_show_company_footer = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (
                show_cost_details,
                show_company_footer,
            ),
        )
        conn.commit()
        conn.close()

        return redirect(
            url_for("purchase_order_print_settings")
        )

    settings = conn.execute(
        """
        SELECT
            po_show_cost_details,
            po_show_company_footer
        FROM erp_settings
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    return render_template(
        "purchase_order_print_settings.html",
        settings=settings,
    )


# ==========================================================
# SPRINT 10.0 — FOUNDATION SETTINGS
# ==========================================================
@app.route("/settings")
def settings_home():
    conn = get_connection()

    profile = get_company_profile(conn)

    erp_setting = conn.execute(
        """
        SELECT
            inventory_enabled,
            po_show_cost_details,
            po_show_company_footer
        FROM erp_settings
        WHERE id = 1
        """
    ).fetchone()

    numbering_count = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM document_numbering
        WHERE active = 1
        """
    ).fetchone()

    conn.close()

    return render_template(
        "settings_home.html",
        profile=profile,
        inventory_setting=erp_setting,
        po_print_setting=erp_setting,
        numbering_count=numbering_count,
    )


@app.route("/settings/company", methods=["GET", "POST"])
def company_profile_settings():
    conn = get_connection()
    identities = get_active_quotation_identities(conn)

    if request.method == "POST":
        identity_id_raw = request.form.get("identity_id", "").strip()

        try:
            identity_id = int(identity_id_raw)
        except (TypeError, ValueError):
            conn.close()
            return "Identity perusahaan tidak valid.", 400

        profile = get_company_identity(
            conn,
            identity_id,
            active_only=True,
        )

        if profile is None:
            conn.close()
            return "Identity perusahaan tidak ditemukan.", 404

        values = {
            "nama_perusahaan": request.form.get(
                "nama_perusahaan",
                "",
            ).strip(),
            "nama_brand": request.form.get(
                "nama_brand",
                "",
            ).strip(),
            "alamat": request.form.get("alamat", "").strip(),
            "kota": request.form.get("kota", "").strip(),
            "provinsi": request.form.get(
                "provinsi",
                "",
            ).strip(),
            "kode_pos": request.form.get(
                "kode_pos",
                "",
            ).strip(),
            "telepon": request.form.get(
                "telepon",
                "",
            ).strip(),
            "whatsapp": request.form.get(
                "whatsapp",
                "",
            ).strip(),
            "email": request.form.get("email", "").strip(),
            "website": request.form.get(
                "website",
                "",
            ).strip(),
            "npwp": request.form.get("npwp", "").strip(),
            "bank": request.form.get("bank", "").strip(),
            "no_rekening": request.form.get(
                "no_rekening",
                "",
            ).strip(),
            "atas_nama": request.form.get(
                "atas_nama",
                "",
            ).strip(),
            "footer_invoice": request.form.get(
                "footer_invoice",
                "",
            ).strip(),
            "footer_quotation": request.form.get(
                "footer_quotation",
                "",
            ).strip(),
            "footer_purchase_order": request.form.get(
                "footer_purchase_order",
                "",
            ).strip(),
            "footer_delivery_order": request.form.get(
                "footer_delivery_order",
                "",
            ).strip(),
            "footer_receipt": request.form.get(
                "footer_receipt",
                "",
            ).strip(),
        }

        if not values["nama_perusahaan"]:
            conn.close()
            return "Nama perusahaan wajib diisi.", 400

        if not values["nama_brand"]:
            conn.close()
            return "Nama brand wajib diisi.", 400

        conn.execute(
            """
            UPDATE company_identities
            SET nama_perusahaan = ?,
                nama_brand = ?,
                alamat = ?,
                kota = ?,
                provinsi = ?,
                kode_pos = ?,
                telepon = ?,
                whatsapp = ?,
                email = ?,
                website = ?,
                npwp = ?,
                bank = ?,
                no_rekening = ?,
                atas_nama = ?,
                footer_invoice = ?,
                footer_quotation = ?,
                footer_purchase_order = ?,
                footer_delivery_order = ?,
                footer_receipt = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                values["nama_perusahaan"],
                values["nama_brand"],
                values["alamat"] or None,
                values["kota"] or None,
                values["provinsi"] or None,
                values["kode_pos"] or None,
                values["telepon"] or None,
                values["whatsapp"] or None,
                values["email"] or None,
                values["website"] or None,
                values["npwp"] or None,
                values["bank"] or None,
                values["no_rekening"] or None,
                values["atas_nama"] or None,
                values["footer_invoice"] or None,
                values["footer_quotation"] or None,
                values["footer_purchase_order"] or None,
                values["footer_delivery_order"] or None,
                values["footer_receipt"] or None,
                identity_id,
            ),
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "company_profile_settings",
                identity_id=identity_id,
            )
        )

    selected_identity_id = request.args.get("identity_id", "").strip()

    try:
        selected_identity_id = int(selected_identity_id)
    except (TypeError, ValueError):
        selected_identity_id = None

    profile = get_company_identity(
        conn,
        selected_identity_id,
        active_only=True,
    )

    if profile is None:
        profile = get_default_full_identity(conn)

    conn.close()

    return render_template(
        "company_profile_settings.html",
        profile=profile,
        identities=identities,
    )


@app.route("/settings/numbering")
def numbering_settings():
    conn = get_connection()

    numbering_rows = conn.execute(
        """
        SELECT *
        FROM document_numbering
        ORDER BY
            CASE document_type
                WHEN 'TRANSACTION' THEN 1
                WHEN 'INVOICE' THEN 2
                WHEN 'QUOTATION' THEN 3
                WHEN 'DELIVERY_ORDER' THEN 4
                WHEN 'PURCHASE_ORDER' THEN 5
                WHEN 'RECEIPT' THEN 6
                WHEN 'CUSTOMER' THEN 7
                WHEN 'SUPPLIER' THEN 8
                ELSE 99
            END,
            document_name
        """
    ).fetchall()

    previews = {
        row["id"]: build_document_number_preview(
            row,
            max(int(row["last_number"] or 0) + 1, 1),
        )
        for row in numbering_rows
    }

    conn.close()

    return render_template(
        "numbering_settings.html",
        numberings=numbering_rows,
        previews=previews,
    )


@app.route(
    "/settings/numbering/<int:numbering_id>/edit",
    methods=["GET", "POST"],
)
def edit_numbering_setting(numbering_id):
    conn = get_connection()

    numbering = conn.execute(
        """
        SELECT *
        FROM document_numbering
        WHERE id = ?
        """,
        (numbering_id,),
    ).fetchone()

    if numbering is None:
        conn.close()
        return "Pengaturan penomoran tidak ditemukan.", 404

    if request.method == "POST":
        document_name = request.form.get(
            "document_name",
            "",
        ).strip()
        prefix = request.form.get("prefix", "").strip().upper()
        separator = request.form.get(
            "separator",
            "/",
        )
        include_year = (
            1
            if request.form.get("include_year") == "1"
            else 0
        )
        include_month = (
            1
            if request.form.get("include_month") == "1"
            else 0
        )
        running_length = parse_integer(
            request.form.get("running_length"),
            6,
        )
        reset_policy = request.form.get(
            "reset_policy",
            "MONTHLY",
        ).strip().upper()
        active = (
            1
            if request.form.get("active") == "1"
            else 0
        )

        allowed_separators = ("/", "-", ".", "")
        if separator not in allowed_separators:
            separator = "/"

        if not document_name or not prefix:
            conn.close()
            return "Nama dokumen dan prefix wajib diisi.", 400

        if running_length < 3 or running_length > 10:
            conn.close()
            return "Panjang running number harus 3 sampai 10.", 400

        if reset_policy not in NUMBERING_RESET_POLICIES:
            conn.close()
            return "Kebijakan reset tidak valid.", 400

        conn.execute(
            """
            UPDATE document_numbering
            SET document_name = ?,
                prefix = ?,
                separator = ?,
                include_year = ?,
                include_month = ?,
                running_length = ?,
                reset_policy = ?,
                active = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                document_name,
                prefix,
                separator,
                include_year,
                include_month,
                running_length,
                reset_policy,
                active,
                numbering_id,
            ),
        )

        conn.commit()
        conn.close()

        return redirect(url_for("numbering_settings"))

    preview = build_document_number_preview(
        numbering,
        max(int(numbering["last_number"] or 0) + 1, 1),
    )

    conn.close()

    return render_template(
        "numbering_edit.html",
        numbering=numbering,
        preview=preview,
        reset_policies=NUMBERING_RESET_POLICIES,
    )



# ==========================================================
# MENJALANKAN APLIKASI
# ==========================================================
if __name__ == "__main__":
    app.run(debug=True)
