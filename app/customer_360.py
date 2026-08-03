"""Read-model and isolated writes for Sprint 11 Customer 360."""

from datetime import datetime


NOTE_TYPES = (
    "General", "Preference", "Follow Up", "Complaint",
    "Payment", "Delivery", "Other",
)

CLASSIFICATION_THRESHOLDS = (
    ("Platinum", 200_000_000),
    ("Gold", 50_000_000),
    ("Silver", 10_000_000),
    ("Bronze", 0),
)


def classify_customer(turnover, order_count):
    if int(order_count or 0) == 0:
        return "New"
    amount = int(turnover or 0)
    for label, minimum in CLASSIFICATION_THRESHOLDS:
        if amount > minimum or (label == "Bronze" and amount >= minimum):
            return label
    return "Bronze"


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _valid_status_sql(alias="t"):
    return f"LOWER(COALESCE({alias}.status, '')) != 'batal'"


def get_customer_360(conn, customer_id, *, year=None, product=None, status=None):
    customer = _one(conn, "SELECT * FROM customers WHERE id = ?", (customer_id,))
    if customer is None:
        return None

    valid = _valid_status_sql("t")
    transaction_summary = _one(
        conn,
        f"""
        SELECT COUNT(*) AS total_transaction,
               COALESCE(SUM(t.total_penjualan), 0) AS omzet,
               COALESCE(SUM(t.margin), 0) AS margin,
               MIN(t.tanggal) AS order_pertama,
               MAX(t.tanggal) AS order_terakhir
        FROM sales_transactions t
        WHERE t.customer_id = ? AND {valid}
        """,
        (customer_id,),
    )
    historical_summary = _one(
        conn,
        """
        SELECT COUNT(*) AS total_history,
               SUM(CASE WHEN total IS NOT NULL THEN 1 ELSE 0 END) AS priced_history,
               COALESCE(SUM(CASE WHEN total IS NOT NULL THEN total ELSE 0 END), 0) AS omzet,
               MIN(tanggal_pembelian) AS order_pertama,
               MAX(tanggal_pembelian) AS order_terakhir,
               COALESCE(SUM(qty), 0) AS units
        FROM customer_purchase_history
        WHERE customer_id = ? AND active = 1 AND COALESCE(qty, 0) > 0
        """,
        (customer_id,),
    )
    quotation_summary = _one(
        conn,
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN LOWER(status) = 'deal' THEN 1 ELSE 0 END) AS deal,
               SUM(CASE WHEN LOWER(COALESCE(status, '')) NOT IN
                    ('deal', 'batal', 'ditolak', 'kedaluwarsa') THEN 1 ELSE 0 END) AS active
        FROM sales_quotations WHERE customer_id = ?
        """,
        (customer_id,),
    )
    product_totals = _one(
        conn,
        f"""
        SELECT COUNT(DISTINCT product_key) AS products, COALESCE(SUM(qty),0) AS units
        FROM (
          SELECT COALESCE('ID-'||i.product_id,'NAME-'||LOWER(i.nama_produk_snapshot)) product_key, i.qty
          FROM sales_transaction_items i JOIN sales_transactions t ON t.id=i.transaction_id
          WHERE t.customer_id=? AND {valid}
          UNION ALL
          SELECT COALESCE('ID-'||h.product_id,'NAME-'||LOWER(h.nama_produk_snapshot)), h.qty
          FROM customer_purchase_history h WHERE h.customer_id=? AND h.active=1
        )
        """,
        (customer_id, customer_id),
    )
    invoice_summary = _one(
        conn,
        """
        SELECT
          SUM(CASE WHEN i.status_pembayaran IN ('Belum Lunas', 'DP') THEN 1 ELSE 0 END) AS outstanding,
          COALESCE(SUM(CASE WHEN i.status_pembayaran IN ('Belum Lunas', 'DP')
            THEN MAX(t.total_penjualan - t.potongan - COALESCE(p.paid, 0), 0) ELSE 0 END), 0) AS receivable
        FROM sales_invoices i
        JOIN sales_transactions t ON t.id = i.transaction_id
        LEFT JOIN (
          SELECT invoice_id, SUM(nominal) AS paid FROM payment_receipts
          WHERE status != 'Void' GROUP BY invoice_id
        ) p ON p.invoice_id = i.id
        WHERE t.customer_id = ? AND i.status_pembayaran != 'Batal'
        """,
        (customer_id,),
    )

    transaction_count = int(transaction_summary["total_transaction"] or 0)
    repeat_orders = transaction_count + int(historical_summary["total_history"] or 0)
    turnover = int(transaction_summary["omzet"] or 0) + int(historical_summary["omzet"] or 0)
    dates = [d for d in (
        transaction_summary["order_pertama"], historical_summary["order_pertama"]
    ) if d]
    last_dates = [d for d in (
        transaction_summary["order_terakhir"], historical_summary["order_terakhir"]
    ) if d]
    kpis = {
        "total_quotation": int(quotation_summary["total"] or 0),
        "quotation_aktif": int(quotation_summary["active"] or 0),
        "quotation_deal": int(quotation_summary["deal"] or 0),
        "total_transaction": transaction_count,
        "repeat_order": repeat_orders,
        "is_repeat_customer": repeat_orders > 1,
        "total_omzet": turnover,
        "total_margin": int(transaction_summary["margin"] or 0),
        "total_produk_dibeli": int(product_totals["products"] or 0),
        "total_unit_dibeli": int(product_totals["units"] or 0),
        "invoice_outstanding": int(invoice_summary["outstanding"] or 0),
        "total_piutang": int(invoice_summary["receivable"] or 0),
        "order_pertama": min(dates) if dates else None,
        "order_terakhir": max(last_dates) if last_dates else None,
        "classification": classify_customer(
            turnover,
            transaction_count + int(historical_summary["total_history"] or 0),
        ),
    }

    filters, values = ["t.customer_id = ?", valid], [customer_id]
    if year:
        filters.append("substr(t.tanggal, 1, 4) = ?")
        values.append(str(year))
    if product:
        filters.append("(i.nama_produk_snapshot LIKE ? OR i.kode_produk_snapshot LIKE ?)")
        values.extend((f"%{product}%", f"%{product}%"))
    if status:
        filters.append("t.status = ?")
        values.append(status)
    transaction_items = _rows(
        conn,
        f"""
        SELECT t.id AS transaction_id, t.tanggal, t.nomor_transaksi,
               t.status, i.*
        FROM sales_transaction_items i
        JOIN sales_transactions t ON t.id = i.transaction_id
        WHERE {' AND '.join(filters)}
        ORDER BY t.tanggal DESC, t.id DESC, i.id
        """,
        values,
    )
    history_filters, history_values = ["customer_id = ?", "active = 1"], [customer_id]
    if year:
        history_filters.append("substr(tanggal_pembelian, 1, 4) = ?")
        history_values.append(str(year))
    if product:
        history_filters.append("(nama_produk_snapshot LIKE ? OR kode_produk_snapshot LIKE ?)")
        history_values.extend((f"%{product}%", f"%{product}%"))
    if status and status != "Historical":
        history_filters.append("0 = 1")
    historical = _rows(
        conn,
        f"""
        SELECT * FROM customer_purchase_history
        WHERE {' AND '.join(history_filters)}
        ORDER BY tanggal_pembelian DESC, id DESC
        """,
        history_values,
    )

    favorites = _favorite_products(conn, customer_id)
    quotations = _rows(
        conn,
        """
        SELECT q.*, c.nama_perusahaan AS identity_name
        FROM sales_quotations q
        LEFT JOIN company_identities c ON c.id = q.identity_id
        WHERE q.customer_id = ? ORDER BY q.tanggal DESC, q.id DESC
        """,
        (customer_id,),
    )
    invoices = _rows(
        conn,
        """
        SELECT i.*, t.nomor_transaksi,
               MAX(t.total_penjualan - t.potongan, 0) AS total_tagihan,
               COALESCE(p.paid, 0) AS jumlah_dibayar_aktual,
               MAX(t.total_penjualan - t.potongan - COALESCE(p.paid, 0), 0) AS sisa_tagihan
        FROM sales_invoices i JOIN sales_transactions t ON t.id = i.transaction_id
        LEFT JOIN (SELECT invoice_id, SUM(nominal) paid FROM payment_receipts
                   WHERE status != 'Void' GROUP BY invoice_id) p ON p.invoice_id = i.id
        WHERE t.customer_id = ? ORDER BY i.tanggal_invoice DESC, i.id DESC
        """,
        (customer_id,),
    )
    receipts = _rows(
        conn,
        """
        SELECT r.*, i.nomor_invoice FROM payment_receipts r
        JOIN sales_transactions t ON t.id = r.transaction_id
        JOIN sales_invoices i ON i.id = r.invoice_id
        WHERE t.customer_id = ? ORDER BY r.tanggal DESC, r.id DESC
        """,
        (customer_id,),
    )
    delivery_orders = _rows(
        conn,
        """
        SELECT d.*, t.nomor_transaksi FROM delivery_orders d
        JOIN sales_transactions t ON t.id = d.transaction_id
        WHERE t.customer_id = ? ORDER BY d.tanggal DESC, d.id DESC
        """,
        (customer_id,),
    )
    purchase_orders = _rows(
        conn,
        """
        SELECT po.*, COALESCE(s.nama_supplier, s.nama) AS supplier_name,
               i.nomor_invoice
        FROM purchase_orders po
        LEFT JOIN suppliers s ON s.id = po.supplier_id
        LEFT JOIN sales_invoices i ON i.id = po.invoice_id
        LEFT JOIN sales_transactions td ON td.id = po.transaction_id
        LEFT JOIN sales_transactions ti ON ti.id = i.transaction_id
        WHERE COALESCE(td.customer_id, ti.customer_id) = ?
        ORDER BY po.tanggal DESC, po.id DESC
        """,
        (customer_id,),
    )
    notes = _rows(
        conn,
        "SELECT * FROM customer_notes WHERE customer_id = ? AND active = 1 ORDER BY created_at DESC, id DESC",
        (customer_id,),
    )

    return {
        "customer": customer, "kpis": kpis, "transaction_items": transaction_items,
        "historical_purchases": historical, "favorites": favorites,
        "quotations": quotations, "invoices": invoices, "receipts": receipts,
        "delivery_orders": delivery_orders, "purchase_orders": purchase_orders,
        "timeline": _timeline(conn, customer_id), "notes": notes,
        "note_types": NOTE_TYPES,
    }


def _favorite_products(conn, customer_id):
    rows = _rows(
        conn,
        f"""
        SELECT product_id, kode, nama, kategori, varian, warna, ukuran,
               SUM(qty) total_qty, COUNT(DISTINCT order_key) jumlah_transaksi,
               MAX(tanggal) pembelian_terakhir, SUM(omzet) omzet_produk
        FROM (
          SELECT i.product_id, i.kode_produk_snapshot kode, i.nama_produk_snapshot nama,
                 i.kategori_snapshot kategori, i.varian_snapshot varian,
                 i.warna_snapshot warna, i.ukuran_snapshot ukuran, i.qty,
                 'T-' || t.id order_key, t.tanggal, i.subtotal_penjualan omzet
          FROM sales_transaction_items i JOIN sales_transactions t ON t.id=i.transaction_id
          WHERE t.customer_id=? AND {_valid_status_sql('t')}
          UNION ALL
          SELECT h.product_id, h.kode_produk_snapshot, h.nama_produk_snapshot,
                 h.kategori_snapshot, h.varian_snapshot, h.warna_snapshot,
                 h.ukuran_snapshot, h.qty, 'H-' || h.id, h.tanggal_pembelian,
                 COALESCE(h.total, 0)
          FROM customer_purchase_history h WHERE h.customer_id=? AND h.active=1
        ) combined
        GROUP BY product_id, kode, nama, kategori, varian, warna, ukuran
        ORDER BY total_qty DESC, omzet_produk DESC, pembelian_terakhir DESC
        LIMIT 10
        """,
        (customer_id, customer_id),
    )
    return rows


def _timeline(conn, customer_id):
    events = []
    specs = (
        ("workflow_events", "created_at", "document_type", "document_id", "event_type", "description",
         "customer_id = ?", (customer_id,)),
        ("sales_quotation_activities a JOIN sales_quotations q ON q.id=a.quotation_id",
         "a.created_at", "'QUOTATION'", "a.quotation_id", "a.activity_type", "a.description",
         "q.customer_id = ?", (customer_id,)),
        ("payment_receipt_activities a JOIN payment_receipts r ON r.id=a.receipt_id JOIN sales_transactions t ON t.id=r.transaction_id",
         "a.created_at", "'RECEIPT'", "a.receipt_id", "a.activity_type", "a.description",
         "t.customer_id = ?", (customer_id,)),
        ("delivery_order_activities a JOIN delivery_orders d ON d.id=a.delivery_order_id JOIN sales_transactions t ON t.id=d.transaction_id",
         "a.created_at", "'DELIVERY_ORDER'", "a.delivery_order_id", "a.activity_type", "a.description",
         "t.customer_id = ?", (customer_id,)),
    )
    for table, dt, doc_type, doc_id, event_type, description, where, params in specs:
        events.extend(_rows(conn, f"SELECT {dt} event_at, {doc_type} document_type, {doc_id} document_id, {event_type} event_type, {description} description FROM {table} WHERE {where}", params))
    base_specs = (
        ("sales_transactions", "created_at", "'TRANSACTION'", "id", "'created'", "'Transaction dibuat'", "customer_id = ?"),
        ("sales_invoices i JOIN sales_transactions t ON t.id=i.transaction_id", "i.created_at", "'INVOICE'", "i.id", "'created'", "'Invoice dibuat'", "t.customer_id = ?"),
        ("purchase_orders p LEFT JOIN sales_invoices i ON i.id=p.invoice_id LEFT JOIN sales_transactions td ON td.id=p.transaction_id LEFT JOIN sales_transactions ti ON ti.id=i.transaction_id", "p.created_at", "'PURCHASE_ORDER'", "p.id", "'created'", "'Purchase order terkait dibuat'", "COALESCE(td.customer_id,ti.customer_id) = ?"),
    )
    for table, dt, doc_type, doc_id, event_type, description, where in base_specs:
        events.extend(_rows(conn, f"SELECT {dt} event_at,{doc_type} document_type,{doc_id} document_id,{event_type} event_type,{description} description FROM {table} WHERE {where}", (customer_id,)))
    numbers = {
        "QUOTATION": ("sales_quotations", "nomor_penawaran"),
        "TRANSACTION": ("sales_transactions", "nomor_transaksi"),
        "INVOICE": ("sales_invoices", "nomor_invoice"),
        "RECEIPT": ("payment_receipts", "nomor_kwitansi"),
        "DELIVERY_ORDER": ("delivery_orders", "nomor_surat_jalan"),
        "PURCHASE_ORDER": ("purchase_orders", "nomor_po"),
    }
    cache = {}
    for doc_type, (table, column) in numbers.items():
        ids = sorted({int(e["document_id"]) for e in events if str(e["document_type"] or "").upper() == doc_type and e["document_id"] is not None})
        if ids:
            placeholders = ",".join("?" for _ in ids)
            for row in conn.execute(f"SELECT id,{column} number FROM {table} WHERE id IN ({placeholders})", ids):
                cache[(doc_type, row["id"])] = row["number"]
    for event in events:
        key = (str(event["document_type"] or "").upper(), event["document_id"])
        event["document_number"] = cache.get(key)
    return sorted(events, key=lambda item: (str(item["event_at"] or ""), int(item["document_id"] or 0)), reverse=True)


def add_historical_purchase(conn, customer_id, data):
    purchase_date = str(data.get("tanggal_pembelian") or "").strip()
    try:
        datetime.strptime(purchase_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Tanggal pembelian wajib berformat YYYY-MM-DD dan valid.") from exc
    qty = int(data.get("qty") or 0)
    if qty <= 0:
        raise ValueError("Qty wajib berupa integer positif.")
    price_raw = str(data.get("harga_satuan") or "").strip()
    price = int(price_raw) if price_raw else None
    if price is not None and price < 0:
        raise ValueError("Harga satuan tidak boleh negatif.")
    product_id = int(data["product_id"]) if str(data.get("product_id") or "").strip() else None
    product = None
    if product_id:
        product = _one(conn, """
            SELECT p.*, c.nama kategori, v.nama varian, co.nama warna, s.nama ukuran
            FROM products p LEFT JOIN product_categories c ON c.id=p.category_id
            LEFT JOIN product_variants v ON v.id=p.variant_id
            LEFT JOIN product_colors co ON co.id=p.color_id
            LEFT JOIN product_sizes s ON s.id=p.size_id WHERE p.id=?
        """, (product_id,))
        if product is None:
            raise ValueError("Produk master tidak ditemukan.")
    snapshots = {
        "kode": product.get("kode_produk") if product else data.get("kode_produk_snapshot"),
        "nama": product.get("nama_produk") if product else data.get("nama_produk_snapshot"),
        "kategori": product.get("kategori") if product else data.get("kategori_snapshot"),
        "varian": product.get("varian") if product else data.get("varian_snapshot"),
        "warna": product.get("warna") if product else data.get("warna_snapshot"),
        "ukuran": product.get("ukuran") if product else data.get("ukuran_snapshot"),
        "satuan": (product.get("satuan") if product else data.get("satuan_snapshot")) or "Unit",
    }
    if not str(snapshots["nama"] or "").strip():
        raise ValueError("Nama produk wajib diisi.")
    return conn.execute(
        """
        INSERT INTO customer_purchase_history (
          customer_id, product_id, tanggal_pembelian, kode_produk_snapshot,
          nama_produk_snapshot, kategori_snapshot, varian_snapshot, warna_snapshot,
          ukuran_snapshot, satuan_snapshot, qty, harga_satuan, total, source, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (customer_id, product_id, purchase_date, snapshots["kode"],
         snapshots["nama"], snapshots["kategori"], snapshots["varian"], snapshots["warna"],
         snapshots["ukuran"], snapshots["satuan"], qty, price,
         price * qty if price is not None else None, data.get("source"), data.get("notes")),
    ).lastrowid


def add_note(conn, customer_id, note_text, note_type="General", created_by=None):
    if note_type not in NOTE_TYPES:
        raise ValueError("Tipe catatan tidak valid.")
    if not str(note_text or "").strip():
        raise ValueError("Catatan wajib diisi.")
    return conn.execute(
        "INSERT INTO customer_notes (customer_id,note_text,note_type,created_by) VALUES (?,?,?,?)",
        (customer_id, str(note_text).strip(), note_type, created_by),
    ).lastrowid


def update_note(conn, customer_id, note_id, note_text, note_type):
    if note_type not in NOTE_TYPES or not str(note_text or "").strip():
        raise ValueError("Catatan atau tipe catatan tidak valid.")
    result = conn.execute(
        "UPDATE customer_notes SET note_text=?, note_type=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND customer_id=? AND active=1",
        (str(note_text).strip(), note_type, note_id, customer_id),
    )
    if result.rowcount != 1:
        raise ValueError("Catatan aktif tidak ditemukan.")


def deactivate_note(conn, customer_id, note_id):
    result = conn.execute(
        "UPDATE customer_notes SET active=0, updated_at=CURRENT_TIMESTAMP WHERE id=? AND customer_id=? AND active=1",
        (note_id, customer_id),
    )
    if result.rowcount != 1:
        raise ValueError("Catatan aktif tidak ditemukan.")


def search_customer_insights(conn, customer_ids):
    if not customer_ids:
        return {}
    placeholders = ",".join("?" for _ in customer_ids)
    rows = _rows(conn, f"""
        SELECT c.id,
          (SELECT i.nama_produk_snapshot FROM sales_transaction_items i
           JOIN sales_transactions t ON t.id=i.transaction_id
           WHERE t.customer_id=c.id AND {_valid_status_sql('t')}
           ORDER BY t.tanggal DESC,t.id DESC,i.id DESC LIMIT 1) produk_terakhir,
          (SELECT MAX(t.tanggal) FROM sales_transactions t
           WHERE t.customer_id=c.id AND {_valid_status_sql('t')}) tanggal_order_terakhir,
          (SELECT COUNT(*) FROM sales_transactions t
           WHERE t.customer_id=c.id AND {_valid_status_sql('t')}) jumlah_transaksi,
          (SELECT COALESCE(SUM(t.total_penjualan),0) FROM sales_transactions t
           WHERE t.customer_id=c.id AND {_valid_status_sql('t')}) +
          (SELECT COALESCE(SUM(h.total),0) FROM customer_purchase_history h
           WHERE h.customer_id=c.id AND h.active=1 AND h.total IS NOT NULL) omzet,
          (SELECT COUNT(*) FROM customer_purchase_history h
           WHERE h.customer_id=c.id AND h.active=1) history_count
        FROM customers c WHERE c.id IN ({placeholders})
    """, tuple(customer_ids))
    result = {}
    for row in rows:
        row["klasifikasi_customer"] = classify_customer(
            row["omzet"], int(row["jumlah_transaksi"] or 0) + int(row["history_count"] or 0)
        )
        result[row["id"]] = row
    return result
