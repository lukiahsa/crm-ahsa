"""Read-only business intelligence queries for the executive dashboard.

The module deliberately owns no workflow state.  It builds a bounded read model
from the existing ERP tables so dashboard rendering never performs per-row
queries and never mutates operational data.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta


ACTIVE_QUOTATION_STATUSES = (
    "Draft",
    "Terkirim",
    "Negosiasi",
    "Revisi",
    "Revision Allowed",
)
OPEN_PO_STATUSES = ("Draft", "Dikirim", "Diproses Supplier", "Barang Diterima")


def _value(args, key, default=""):
    value = args.get(key, default) if args is not None else default
    return str(value or "").strip()


def _iso_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def resolve_dashboard_filters(args, today=None):
    today = today or date.today()
    period = _value(args, "period", "month").lower()
    if period not in {"day", "week", "month", "year", "custom"}:
        period = "month"

    if period == "day":
        start = end = today
    elif period == "week":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "year":
        start, end = date(today.year, 1, 1), today
    elif period == "custom":
        start = _iso_date(_value(args, "start_date")) or date(today.year, today.month, 1)
        end = _iso_date(_value(args, "end_date")) or today
        if start > end:
            start, end = end, start
    else:
        start, end = date(today.year, today.month, 1), today

    def positive_int(key):
        try:
            result = int(_value(args, key, "0"))
        except ValueError:
            return 0
        return result if result > 0 else 0

    return {
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sales": _value(args, "sales"),
        "customer_id": positive_int("customer_id"),
        "product_id": positive_int("product_id"),
        "category_id": positive_int("category_id"),
        "today": today.isoformat(),
    }


def _dimension_conditions(filters, transaction_alias="t", item_table="sales_transaction_items"):
    conditions = []
    params = []
    if filters["sales"]:
        conditions.append(f"COALESCE({transaction_alias}.referal, '') = ?")
        params.append(filters["sales"])
    if filters["customer_id"]:
        conditions.append(f"{transaction_alias}.customer_id = ?")
        params.append(filters["customer_id"])
    if filters["product_id"] or filters["category_id"]:
        item_conditions = [f"di.transaction_id = {transaction_alias}.id"]
        if filters["product_id"]:
            item_conditions.append("di.product_id = ?")
            params.append(filters["product_id"])
        if filters["category_id"]:
            item_conditions.append("dp.category_id = ?")
            params.append(filters["category_id"])
        conditions.append(
            f"EXISTS (SELECT 1 FROM {item_table} di "
            f"LEFT JOIN products dp ON dp.id = di.product_id WHERE {' AND '.join(item_conditions)})"
        )
    return conditions, params


def _transaction_where(filters, start=None, end=None, alias="t"):
    conditions = [f"LOWER(COALESCE({alias}.status, '')) NOT IN ('batal', 'cancelled')"]
    params = []
    if start:
        conditions.append(f"date({alias}.tanggal) >= date(?)")
        params.append(start)
    if end:
        conditions.append(f"date({alias}.tanggal) <= date(?)")
        params.append(end)
    dimensional, dimensional_params = _dimension_conditions(filters, alias)
    conditions.extend(dimensional)
    params.extend(dimensional_params)
    return " AND ".join(conditions), params


def _quotation_where(filters, start=None, end=None, alias="q"):
    conditions, params = [], []
    if start:
        conditions.append(f"date({alias}.tanggal) >= date(?)")
        params.append(start)
    if end:
        conditions.append(f"date({alias}.tanggal) <= date(?)")
        params.append(end)
    if filters["sales"]:
        conditions.append(f"COALESCE({alias}.sales, '') = ?")
        params.append(filters["sales"])
    if filters["customer_id"]:
        conditions.append(f"{alias}.customer_id = ?")
        params.append(filters["customer_id"])
    if filters["product_id"] or filters["category_id"]:
        item_conditions = [f"qi.quotation_id = {alias}.id"]
        if filters["product_id"]:
            item_conditions.append("qi.product_id = ?")
            params.append(filters["product_id"])
        if filters["category_id"]:
            item_conditions.append("p.category_id = ?")
            params.append(filters["category_id"])
        conditions.append(
            "EXISTS (SELECT 1 FROM sales_quotation_items qi "
            f"LEFT JOIN products p ON p.id = qi.product_id WHERE {' AND '.join(item_conditions)})"
        )
    return (" AND ".join(conditions) if conditions else "1 = 1"), params


def _scalar(conn, sql, params=()):
    row = conn.execute(sql, tuple(params)).fetchone()
    return int((row[0] if row else 0) or 0)


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def _money_summary(conn, filters, start, end):
    where, params = _transaction_where(filters, start, end)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS transactions,
               COALESCE(SUM(t.total_penjualan), 0) AS revenue,
               COALESCE(SUM(t.margin), 0) AS margin,
               COALESCE(SUM(t.laba_bersih), 0) AS net_profit,
               COALESCE(AVG(t.total_penjualan), 0) AS average_transaction
        FROM sales_transactions t
        WHERE {where}
        """,
        tuple(params),
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def _invoice_summary(conn, filters, start, end):
    where, params = _transaction_where(filters, start, end)
    row = conn.execute(
        f"""
        WITH paid AS (
            SELECT invoice_id, SUM(nominal) AS amount
            FROM payment_receipts
            WHERE LOWER(COALESCE(status, '')) != 'void'
            GROUP BY invoice_id
        )
        SELECT
            SUM(CASE WHEN i.status_pembayaran != 'Lunas' THEN 1 ELSE 0 END) AS outstanding_count,
            COALESCE(SUM(CASE WHEN i.status_pembayaran != 'Lunas'
                THEN MAX(COALESCE(t.total_penjualan, 0) - COALESCE(t.potongan, 0)
                         - COALESCE(paid.amount, 0), 0) ELSE 0 END), 0) AS receivable
        FROM sales_invoices i
        JOIN sales_transactions t ON t.id = i.transaction_id
        LEFT JOIN paid ON paid.invoice_id = i.id
        WHERE {where}
        """,
        tuple(params),
    ).fetchone()
    return {
        "outstanding_count": int(row["outstanding_count"] or 0),
        "receivable": int(row["receivable"] or 0),
    }


def _executive_kpis(conn, filters):
    today = _iso_date(filters["today"])
    month_start = date(today.year, today.month, 1).isoformat()
    year_start = date(today.year, 1, 1).isoformat()
    selected = _money_summary(conn, filters, filters["start_date"], filters["end_date"])
    invoice = _invoice_summary(conn, filters, filters["start_date"], filters["end_date"])
    day = _money_summary(conn, filters, filters["today"], filters["today"])
    month = _money_summary(conn, filters, month_start, filters["today"])
    year = _money_summary(conn, filters, year_start, filters["today"])
    q_where, q_params = _quotation_where(filters, filters["start_date"], filters["end_date"])
    q_row = conn.execute(
        f"""
        SELECT SUM(CASE WHEN status IN ({','.join('?' for _ in ACTIVE_QUOTATION_STATUSES)})
                        THEN 1 ELSE 0 END) AS active_count,
               SUM(CASE WHEN status = 'Deal' THEN 1 ELSE 0 END) AS deal_count
        FROM sales_quotations q WHERE {q_where}
        """,
        tuple(ACTIVE_QUOTATION_STATUSES) + tuple(q_params),
    ).fetchone()
    transaction_where, transaction_params = _transaction_where(
        filters, filters["start_date"], filters["end_date"]
    )
    repeat_customer = _scalar(
        conn,
        f"SELECT COUNT(*) FROM (SELECT t.customer_id FROM sales_transactions t "
        f"WHERE {transaction_where} AND t.customer_id IS NOT NULL "
        "GROUP BY t.customer_id HAVING COUNT(*) > 1)",
        transaction_params,
    )
    customer_conditions = ["date(c.created_at) BETWEEN date(?) AND date(?)"]
    customer_params = [filters["start_date"], filters["end_date"]]
    if filters["customer_id"]:
        customer_conditions.append("c.id = ?")
        customer_params.append(filters["customer_id"])
    new_customer = _scalar(
        conn,
        f"SELECT COUNT(*) FROM customers c WHERE {' AND '.join(customer_conditions)}",
        customer_params,
    )
    po_conditions = ["date(po.tanggal) BETWEEN date(?) AND date(?)"]
    po_params = [filters["start_date"], filters["end_date"]]
    if filters["customer_id"]:
        po_conditions.append("t.customer_id = ?")
        po_params.append(filters["customer_id"])
    purchase_outstanding = _scalar(
        conn,
        f"""SELECT COUNT(*) FROM purchase_orders po
            LEFT JOIN sales_transactions t ON t.id = po.transaction_id
            WHERE {' AND '.join(po_conditions)}
              AND po.status IN ({','.join('?' for _ in OPEN_PO_STATUSES)})""",
        po_params + list(OPEN_PO_STATUSES),
    )
    stock_filter, stock_params = _product_filter(filters, "p")
    stock_row = conn.execute(
        f"""
        SELECT COALESCE(SUM(ps.stok * p.harga_modal_default), 0) AS stock_value,
               SUM(CASE WHEN ps.stok <= ps.minimum_stok THEN 1 ELSE 0 END) AS low_stock
        FROM product_stock ps JOIN products p ON p.id = ps.product_id
        WHERE {stock_filter}
        """,
        tuple(stock_params),
    ).fetchone()
    return {
        "revenue_today": day["revenue"],
        "revenue_month": month["revenue"],
        "revenue_year": year["revenue"],
        "margin_month": month["margin"],
        "net_profit_month": month["net_profit"],
        "new_customer": new_customer,
        "repeat_customer": repeat_customer,
        "active_quotation": int(q_row["active_count"] or 0),
        "deal_quotation": int(q_row["deal_count"] or 0),
        "outstanding_invoice": invoice["outstanding_count"],
        "receivable": invoice["receivable"],
        "purchase_outstanding": purchase_outstanding,
        "stock_value": int(stock_row["stock_value"] or 0),
        "low_stock": int(stock_row["low_stock"] or 0),
        "selected_revenue": selected["revenue"],
    }


def _sales_performance(conn, filters):
    q_where, q_params = _quotation_where(filters, filters["start_date"], filters["end_date"])
    t_where, t_params = _transaction_where(filters, filters["start_date"], filters["end_date"])
    return _rows(
        conn,
        f"""
        WITH quotation AS (
            SELECT COALESCE(NULLIF(q.sales, ''), 'Tanpa Sales') AS sales,
                   COUNT(*) AS quotation_count,
                   SUM(CASE WHEN q.status = 'Deal' THEN 1 ELSE 0 END) AS closing
            FROM sales_quotations q WHERE {q_where}
            GROUP BY COALESCE(NULLIF(q.sales, ''), 'Tanpa Sales')
        ), transaction_metric AS (
            SELECT COALESCE(NULLIF(t.referal, ''), 'Tanpa Sales') AS sales,
                   SUM(t.total_penjualan) AS revenue, SUM(t.margin) AS margin,
                   AVG(t.total_penjualan) AS average_transaction
            FROM sales_transactions t WHERE {t_where}
            GROUP BY COALESCE(NULLIF(t.referal, ''), 'Tanpa Sales')
        ), repeats AS (
            SELECT sales, COUNT(*) AS repeat_customer FROM (
                SELECT COALESCE(NULLIF(t.referal, ''), 'Tanpa Sales') AS sales, t.customer_id
                FROM sales_transactions t WHERE {t_where} AND t.customer_id IS NOT NULL
                GROUP BY COALESCE(NULLIF(t.referal, ''), 'Tanpa Sales'), t.customer_id
                HAVING COUNT(*) > 1
            ) grouped GROUP BY sales
        ), names AS (
            SELECT sales FROM quotation UNION SELECT sales FROM transaction_metric
        )
        SELECT names.sales,
               COALESCE(quotation.quotation_count, 0) AS quotation_count,
               COALESCE(quotation.closing, 0) AS closing,
               CASE WHEN COALESCE(quotation.quotation_count, 0) = 0 THEN 0
                    ELSE ROUND(100.0 * quotation.closing / quotation.quotation_count, 1) END AS conversion_rate,
               COALESCE(transaction_metric.revenue, 0) AS revenue,
               COALESCE(transaction_metric.margin, 0) AS margin,
               COALESCE(repeats.repeat_customer, 0) AS repeat_customer,
               CAST(COALESCE(transaction_metric.average_transaction, 0) AS INTEGER) AS average_transaction
        FROM names
        LEFT JOIN quotation ON quotation.sales = names.sales
        LEFT JOIN transaction_metric ON transaction_metric.sales = names.sales
        LEFT JOIN repeats ON repeats.sales = names.sales
        ORDER BY revenue DESC, names.sales COLLATE NOCASE
        """,
        q_params + t_params + t_params,
    )


def _customer_analytics(conn, filters):
    t_where, t_params = _transaction_where(filters, filters["start_date"], filters["end_date"])
    customer_filter = ""
    customer_filter_params = []
    if filters["customer_id"]:
        customer_filter = " AND c.id = ?"
        customer_filter_params.append(filters["customer_id"])
    new_count = _scalar(
        conn,
        "SELECT COUNT(*) FROM customers c WHERE date(c.created_at) BETWEEN date(?) AND date(?)" + customer_filter,
        [filters["start_date"], filters["end_date"]] + customer_filter_params,
    )
    existing_count = _scalar(
        conn,
        "SELECT COUNT(*) FROM customers c WHERE c.status_aktif = 1 "
        "AND date(c.created_at) < date(?)" + customer_filter,
        [filters["start_date"]] + customer_filter_params,
    )
    top = _rows(
        conn,
        f"""
        SELECT c.id, c.nama, c.instansi, COUNT(t.id) AS transaction_count,
               SUM(t.total_penjualan) AS revenue, SUM(t.margin) AS margin,
               MAX(t.tanggal) AS last_order
        FROM sales_transactions t JOIN customers c ON c.id = t.customer_id
        WHERE {t_where}
        GROUP BY c.id, c.nama, c.instansi
        ORDER BY revenue DESC, c.nama COLLATE NOCASE LIMIT 10
        """,
        t_params,
    )
    repeat_count = sum(1 for row in top if int(row["transaction_count"] or 0) > 1)
    # Count all repeats, not only the displayed top ten.
    repeat_count = _scalar(
        conn,
        f"SELECT COUNT(*) FROM (SELECT t.customer_id FROM sales_transactions t WHERE {t_where} "
        "AND t.customer_id IS NOT NULL GROUP BY t.customer_id HAVING COUNT(*) > 1)",
        t_params,
    )
    tier_row = conn.execute(
        f"""
        SELECT SUM(CASE WHEN revenue > 200000000 THEN 1 ELSE 0 END) AS platinum,
               SUM(CASE WHEN revenue > 75000000 AND revenue <= 200000000 THEN 1 ELSE 0 END) AS gold
        FROM (SELECT t.customer_id, SUM(t.total_penjualan) AS revenue
              FROM sales_transactions t WHERE {t_where} AND t.customer_id IS NOT NULL
              GROUP BY t.customer_id)
        """,
        tuple(t_params),
    ).fetchone()
    stale_before = (_iso_date(filters["today"]) - timedelta(days=90)).isoformat()
    stale = _rows(
        conn,
        f"""
        SELECT c.id, c.nama, c.instansi, MAX(t.tanggal) AS last_order,
               CAST(julianday(?) - julianday(MAX(t.tanggal)) AS INTEGER) AS inactive_days
        FROM customers c LEFT JOIN sales_transactions t ON t.customer_id = c.id
          AND LOWER(COALESCE(t.status, '')) NOT IN ('batal', 'cancelled')
        WHERE c.status_aktif = 1 {customer_filter}
        GROUP BY c.id, c.nama, c.instansi
        HAVING last_order IS NULL OR date(last_order) < date(?)
        ORDER BY last_order IS NULL DESC, last_order ASC LIMIT 10
        """,
        [filters["today"]] + customer_filter_params + [stale_before],
    )
    outstanding = _outstanding_customers(conn, filters)
    return {
        "new": new_count,
        "existing": existing_count,
        "repeat": repeat_count,
        "platinum": int(tier_row["platinum"] or 0),
        "gold": int(tier_row["gold"] or 0),
        "inactive": _scalar(conn, "SELECT COUNT(*) FROM customers WHERE status_aktif = 0"),
        "top": top,
        "stale": stale,
        "outstanding": outstanding,
    }


def _outstanding_customers(conn, filters):
    where, params = _transaction_where(filters, filters["start_date"], filters["end_date"])
    return _rows(
        conn,
        f"""
        WITH paid AS (
            SELECT invoice_id, SUM(nominal) AS amount FROM payment_receipts
            WHERE LOWER(COALESCE(status, '')) != 'void' GROUP BY invoice_id
        )
        SELECT c.id, c.nama, COUNT(i.id) AS invoice_count,
               SUM(MAX(t.total_penjualan - t.potongan - COALESCE(paid.amount, 0), 0)) AS outstanding
        FROM sales_invoices i JOIN sales_transactions t ON t.id = i.transaction_id
        JOIN customers c ON c.id = t.customer_id LEFT JOIN paid ON paid.invoice_id = i.id
        WHERE i.status_pembayaran != 'Lunas' AND {where}
        GROUP BY c.id, c.nama HAVING outstanding > 0
        ORDER BY outstanding DESC LIMIT 10
        """,
        params,
    )


def _product_filter(filters, alias="p"):
    conditions, params = ["1 = 1"], []
    if filters["product_id"]:
        conditions.append(f"{alias}.id = ?")
        params.append(filters["product_id"])
    if filters["category_id"]:
        conditions.append(f"{alias}.category_id = ?")
        params.append(filters["category_id"])
    return " AND ".join(conditions), params


def _product_analytics(conn, filters):
    t_where, t_params = _transaction_where(filters, filters["start_date"], filters["end_date"])
    product_where, product_params = _product_filter(filters, "p")
    top = _rows(
        conn,
        f"""
        SELECT p.id, p.kode_produk, p.nama_produk, COALESCE(pc.nama, sti.kategori_snapshot, '-') AS category,
               SUM(sti.qty) AS quantity, SUM(sti.subtotal_penjualan) AS revenue,
               SUM(sti.margin_item) AS margin, COUNT(DISTINCT t.id) AS transaction_count,
               COUNT(DISTINCT t.customer_id) AS customer_count,
               COUNT(DISTINCT CASE WHEN repeated.customer_id IS NOT NULL THEN t.customer_id END) AS repeat_customer
        FROM sales_transaction_items sti JOIN sales_transactions t ON t.id = sti.transaction_id
        JOIN products p ON p.id = sti.product_id LEFT JOIN product_categories pc ON pc.id = p.category_id
        LEFT JOIN (SELECT customer_id FROM sales_transactions
                   WHERE LOWER(COALESCE(status, '')) NOT IN ('batal', 'cancelled')
                   GROUP BY customer_id HAVING COUNT(*) > 1) repeated ON repeated.customer_id = t.customer_id
        WHERE {t_where} AND {product_where}
        GROUP BY p.id, p.kode_produk, p.nama_produk, pc.nama, sti.kategori_snapshot
        ORDER BY quantity DESC, revenue DESC LIMIT 10
        """,
        t_params + product_params,
    )
    highest_margin = sorted(top, key=lambda row: int(row["margin"] or 0), reverse=True)[:5]
    most_repeat = sorted(top, key=lambda row: int(row["repeat_customer"] or 0), reverse=True)[:5]
    never_sold = _rows(
        conn,
        f"""SELECT p.id, p.kode_produk, p.nama_produk, COALESCE(pc.nama, '-') AS category
            FROM products p LEFT JOIN product_categories pc ON pc.id = p.category_id
            WHERE p.status_aktif = 1 AND {product_where}
              AND NOT EXISTS (SELECT 1 FROM sales_transaction_items sti
                              JOIN sales_transactions t ON t.id = sti.transaction_id
                              WHERE sti.product_id = p.id
                                AND LOWER(COALESCE(t.status, '')) NOT IN ('batal', 'cancelled'))
            ORDER BY p.nama_produk COLLATE NOCASE LIMIT 10""",
        product_params,
    )
    category = _rows(
        conn,
        f"""
        SELECT COALESCE(pc.nama, sti.kategori_snapshot, 'Tanpa Kategori') AS category,
               SUM(sti.qty) AS quantity, SUM(sti.subtotal_penjualan) AS revenue
        FROM sales_transaction_items sti JOIN sales_transactions t ON t.id = sti.transaction_id
        LEFT JOIN products p ON p.id = sti.product_id LEFT JOIN product_categories pc ON pc.id = p.category_id
        WHERE {t_where} AND {product_where}
        GROUP BY COALESCE(pc.nama, sti.kategori_snapshot, 'Tanpa Kategori')
        ORDER BY revenue DESC
        """,
        t_params + product_params,
    )
    return {
        "top": top,
        "best_seller": top[0] if top else None,
        "highest_margin": highest_margin,
        "most_repeat": most_repeat,
        "never_sold": never_sold,
        "categories": category,
    }


def _sales_funnel(conn, filters):
    q_where, q_params = _quotation_where(filters, filters["start_date"], filters["end_date"])
    t_where, t_params = _transaction_where(filters, filters["start_date"], filters["end_date"])
    customer_conditions = ["c.status IN ('Prospek', 'Follow Up', 'Penawaran')"]
    customer_params = []
    if filters["customer_id"]:
        customer_conditions.append("c.id = ?")
        customer_params.append(filters["customer_id"])
    quotation_count = _scalar(conn, f"SELECT COUNT(*) FROM sales_quotations q WHERE {q_where}", q_params)
    deal_count = _scalar(conn, f"SELECT COUNT(*) FROM sales_quotations q WHERE {q_where} AND q.status = 'Deal'", q_params)
    transaction_count = _scalar(conn, f"SELECT COUNT(*) FROM sales_transactions t WHERE {t_where}", t_params)
    stages = [
        ("Prospek", _scalar(conn, f"SELECT COUNT(*) FROM customers c WHERE {' AND '.join(customer_conditions)}", customer_params)),
        ("Quotation", quotation_count),
        ("Deal", deal_count),
        ("Transaction", transaction_count),
        ("Invoice", _document_transaction_count(conn, "sales_invoices", "transaction_id", "tanggal_invoice", filters, "1 = 1")),
        ("Receipt", _document_transaction_count(conn, "payment_receipts", "transaction_id", "tanggal", filters, "LOWER(COALESCE(d.status, '')) != 'void'")),
        ("Delivery", _document_transaction_count(conn, "delivery_orders", "transaction_id", "tanggal", filters, "1 = 1")),
        ("Completed", _scalar(conn, f"SELECT COUNT(*) FROM sales_transactions t WHERE {t_where} AND t.status = 'Selesai'", t_params)),
    ]
    result = []
    previous = None
    for name, count in stages:
        conversion = 100.0 if previous is None and count else (100.0 * count / previous if previous else 0.0)
        result.append({"stage": name, "count": count, "conversion_rate": round(conversion, 1)})
        previous = count
    return result


def _document_transaction_count(conn, table, transaction_column, date_column, filters, extra):
    where, params = _transaction_where(filters, alias="t")
    params = [filters["start_date"], filters["end_date"]] + params
    return _scalar(
        conn,
        f"""SELECT COUNT(DISTINCT d.{transaction_column}) FROM {table} d
            JOIN sales_transactions t ON t.id = d.{transaction_column}
            WHERE date(d.{date_column}) BETWEEN date(?) AND date(?) AND {extra} AND {where}""",
        params,
    )


def _purchase_analytics(conn, filters):
    conditions = ["date(po.tanggal) BETWEEN date(?) AND date(?)"]
    params = [filters["start_date"], filters["end_date"]]
    if filters["customer_id"]:
        conditions.append("t.customer_id = ?")
        params.append(filters["customer_id"])
    base = " AND ".join(conditions)
    row = conn.execute(
        f"""
        SELECT SUM(CASE WHEN po.status IN ({','.join('?' for _ in OPEN_PO_STATUSES)}) THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN po.status IN ('Draft','Dikirim','Diproses Supplier') THEN 1 ELSE 0 END) AS outstanding,
               SUM(CASE WHEN po.status = 'Selesai' THEN 1 ELSE 0 END) AS completed,
               COALESCE(SUM(CASE WHEN po.status != 'Batal' THEN po.grand_total ELSE 0 END), 0) AS value
        FROM purchase_orders po LEFT JOIN sales_transactions t ON t.id = po.transaction_id
        WHERE {base}
        """,
        tuple(OPEN_PO_STATUSES) + tuple(params),
    ).fetchone()
    suppliers = _rows(
        conn,
        f"""
        SELECT COALESCE(NULLIF(s.nama_supplier, ''), s.nama, po.supplier_nama_snapshot, '-') AS supplier,
               COUNT(*) AS po_count, SUM(po.grand_total) AS value
        FROM purchase_orders po LEFT JOIN suppliers s ON s.id = po.supplier_id
        LEFT JOIN sales_transactions t ON t.id = po.transaction_id
        WHERE {base} AND po.status != 'Batal'
        GROUP BY po.supplier_id, supplier ORDER BY value DESC LIMIT 10
        """,
        params,
    )
    return {
        "active": int(row["active"] or 0),
        "outstanding": int(row["outstanding"] or 0),
        "completed": int(row["completed"] or 0),
        "value": int(row["value"] or 0),
        "suppliers": suppliers,
        "top_by_count": max(suppliers, key=lambda item: item["po_count"], default=None),
        "top_by_value": suppliers[0] if suppliers else None,
    }


def _inventory_analytics(conn, filters):
    product_where, product_params = _product_filter(filters, "p")
    stock = conn.execute(
        f"""SELECT COALESCE(SUM(ps.stok), 0) AS available,
                   SUM(CASE WHEN ps.stok <= ps.minimum_stok THEN 1 ELSE 0 END) AS low_stock
            FROM product_stock ps JOIN products p ON p.id = ps.product_id WHERE {product_where}""",
        tuple(product_params),
    ).fetchone()
    movement = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN sm.movement_type = 'IN' THEN sm.qty ELSE 0 END), 0) AS stock_in,
                   COALESCE(SUM(CASE WHEN sm.movement_type = 'OUT' THEN sm.qty ELSE 0 END), 0) AS stock_out
            FROM stock_movements sm JOIN products p ON p.id = sm.product_id
            WHERE date(sm.tanggal) BETWEEN date(?) AND date(?) AND {product_where}""",
        tuple([filters["start_date"], filters["end_date"]] + product_params),
    ).fetchone()
    movers = _rows(
        conn,
        f"""SELECT p.id, p.nama_produk, SUM(sm.qty) AS quantity
            FROM stock_movements sm JOIN products p ON p.id = sm.product_id
            WHERE sm.movement_type = 'OUT' AND date(sm.tanggal) BETWEEN date(?) AND date(?)
              AND {product_where}
            GROUP BY p.id, p.nama_produk ORDER BY quantity DESC""",
        [filters["start_date"], filters["end_date"]] + product_params,
    )
    return {
        "stock_in": int(movement["stock_in"] or 0),
        "stock_out": int(movement["stock_out"] or 0),
        "available": int(stock["available"] or 0),
        "low_stock": int(stock["low_stock"] or 0),
        "fast_moving": movers[:5],
        "slow_moving": list(reversed(movers[-5:])),
    }


def _recent_activity(conn, filters):
    conditions = ["date(we.created_at) BETWEEN date(?) AND date(?)"]
    params = [filters["start_date"], filters["end_date"]]
    if filters["customer_id"]:
        conditions.append("we.customer_id = ?")
        params.append(filters["customer_id"])
    return _rows(
        conn,
        f"""
        SELECT we.id, we.document_type, we.document_id, we.event_type, we.old_status,
               we.new_status, we.description, we.created_by, we.created_at, c.nama AS customer_name
        FROM workflow_events we LEFT JOIN customers c ON c.id = we.customer_id
        WHERE {' AND '.join(conditions)}
        ORDER BY we.created_at DESC, we.id DESC LIMIT 20
        """,
        params,
    )


def _owner_alerts(conn, filters):
    t_where, t_params = _transaction_where(filters, alias="t")
    overdue = _rows(
        conn,
        f"""
        WITH paid AS (SELECT invoice_id, SUM(nominal) amount FROM payment_receipts
                       WHERE LOWER(COALESCE(status, '')) != 'void' GROUP BY invoice_id)
        SELECT i.id, i.nomor_invoice AS reference, c.nama AS subject, i.jatuh_tempo AS due_date,
               MAX(t.total_penjualan - t.potongan - COALESCE(paid.amount, 0), 0) AS amount
        FROM sales_invoices i JOIN sales_transactions t ON t.id = i.transaction_id
        LEFT JOIN customers c ON c.id = t.customer_id LEFT JOIN paid ON paid.invoice_id = i.id
        WHERE i.status_pembayaran != 'Lunas' AND date(i.jatuh_tempo) < date(?) AND {t_where}
        ORDER BY i.jatuh_tempo ASC LIMIT 10
        """,
        [filters["today"]] + t_params,
    )
    product_where, product_params = _product_filter(filters, "p")
    low_stock = _rows(
        conn,
        f"""SELECT p.id, p.kode_produk AS reference, p.nama_produk AS subject,
                   SUM(ps.stok) AS stock, SUM(ps.minimum_stok) AS minimum_stock
            FROM product_stock ps JOIN products p ON p.id = ps.product_id
            WHERE {product_where} GROUP BY p.id, p.kode_produk, p.nama_produk
            HAVING stock <= minimum_stock ORDER BY stock ASC LIMIT 10""",
        product_params,
    )
    po_conditions = ["po.status IN ('Draft','Dikirim','Diproses Supplier')", "date(po.estimasi_datang) < date(?)"]
    po_params = [filters["today"]]
    if filters["customer_id"]:
        po_conditions.append("t.customer_id = ?")
        po_params.append(filters["customer_id"])
    late_po = _rows(
        conn,
        f"""SELECT po.id, po.nomor_po AS reference,
                   COALESCE(s.nama_supplier, s.nama, po.supplier_nama_snapshot, '-') AS subject,
                   po.estimasi_datang AS due_date, po.grand_total AS amount
            FROM purchase_orders po LEFT JOIN suppliers s ON s.id = po.supplier_id
            LEFT JOIN sales_transactions t ON t.id = po.transaction_id
            WHERE {' AND '.join(po_conditions)} ORDER BY po.estimasi_datang ASC LIMIT 10""",
        po_params,
    )
    q_where, q_params = _quotation_where(filters, alias="q")
    expired = _rows(
        conn,
        f"""SELECT q.id, q.nomor_penawaran AS reference,
                   COALESCE(c.nama, q.customer_nama_snapshot, '-') AS subject,
                   q.berlaku_sampai AS due_date, q.grand_total AS amount
            FROM sales_quotations q LEFT JOIN customers c ON c.id = q.customer_id
            WHERE date(q.berlaku_sampai) < date(?)
              AND q.status IN ({','.join('?' for _ in ACTIVE_QUOTATION_STATUSES)}) AND {q_where}
            ORDER BY q.berlaku_sampai ASC LIMIT 10""",
        [filters["today"]] + list(ACTIVE_QUOTATION_STATUSES) + q_params,
    )
    stale = _customer_analytics_stale_alert(conn, filters)
    return {
        "overdue_invoice": overdue,
        "stale_customer": stale,
        "low_stock": low_stock,
        "late_po": late_po,
        "expired_quotation": expired,
        "total": len(overdue) + len(stale) + len(low_stock) + len(late_po) + len(expired),
    }


def _customer_analytics_stale_alert(conn, filters):
    stale_before = (_iso_date(filters["today"]) - timedelta(days=90)).isoformat()
    conditions = ["c.status_aktif = 1"]
    params = []
    if filters["customer_id"]:
        conditions.append("c.id = ?")
        params.append(filters["customer_id"])
    return _rows(
        conn,
        f"""SELECT c.id, c.nama AS subject, MAX(t.tanggal) AS last_order
            FROM customers c LEFT JOIN sales_transactions t ON t.customer_id = c.id
              AND LOWER(COALESCE(t.status, '')) NOT IN ('batal', 'cancelled')
            WHERE {' AND '.join(conditions)} GROUP BY c.id, c.nama
            HAVING last_order IS NULL OR date(last_order) < date(?)
            ORDER BY last_order IS NULL DESC, last_order ASC LIMIT 10""",
        params + [stale_before],
    )


def _monthly_trend(conn, filters):
    today = _iso_date(filters["today"])
    start_month = date(today.year, today.month, 1)
    months = []
    for offset in range(11, -1, -1):
        year = start_month.year
        month = start_month.month - offset
        while month <= 0:
            year -= 1
            month += 12
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        summary = _money_summary(conn, filters, start.isoformat(), end.isoformat())
        months.append({"label": start.strftime("%b %Y"), **summary})
    return months


def _filter_options(conn):
    return {
        "sales": [row[0] for row in conn.execute(
            "SELECT sales FROM (SELECT sales FROM sales_quotations WHERE sales IS NOT NULL AND TRIM(sales) != '' "
            "UNION SELECT referal FROM sales_transactions WHERE referal IS NOT NULL AND TRIM(referal) != '') ORDER BY sales COLLATE NOCASE"
        ).fetchall()],
        "customers": _rows(conn, "SELECT id, nama FROM customers WHERE status_aktif = 1 ORDER BY nama COLLATE NOCASE"),
        "products": _rows(conn, "SELECT id, kode_produk, nama_produk FROM products WHERE status_aktif = 1 ORDER BY nama_produk COLLATE NOCASE"),
        "categories": _rows(conn, "SELECT id, nama FROM product_categories WHERE status_aktif = 1 ORDER BY nama COLLATE NOCASE"),
    }


def build_executive_dashboard(conn, args=None, today=None):
    """Return the complete dashboard read model using a fixed query plan."""
    filters = resolve_dashboard_filters(args, today=today)
    financial = _money_summary(conn, filters, filters["start_date"], filters["end_date"])
    invoice = _invoice_summary(conn, filters, filters["start_date"], filters["end_date"])
    purchase = _purchase_analytics(conn, filters)
    financial.update({
        "receivable": invoice["receivable"],
        "outstanding_invoice": invoice["outstanding_count"],
        "purchase_order_value": purchase["value"],
        "purchase_value": purchase["value"],
    })
    return {
        "filters": filters,
        "filter_options": _filter_options(conn),
        "kpis": _executive_kpis(conn, filters),
        "sales_performance": _sales_performance(conn, filters),
        "customer": _customer_analytics(conn, filters),
        "product": _product_analytics(conn, filters),
        "funnel": _sales_funnel(conn, filters),
        "financial": financial,
        "purchase": purchase,
        "inventory": _inventory_analytics(conn, filters),
        "recent_activity": _recent_activity(conn, filters),
        "alerts": _owner_alerts(conn, filters),
        "trend": _monthly_trend(conn, filters),
    }
