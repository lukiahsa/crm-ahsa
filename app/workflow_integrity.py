"""Central workflow, payment, and stock integrity services."""

import hashlib
from decimal import Decimal, InvalidOperation


class WorkflowIntegrityError(ValueError):
    """Business rule violation that must abort the current transaction."""


DO_TRANSITIONS = {
    "Draft": {"Packing", "Siap Kirim", "Terkirim", "Batal"},
    "Packing": {"Siap Kirim", "Dalam Pengiriman", "Terkirim", "Batal"},
    "Siap Kirim": {"Dalam Pengiriman", "Terkirim", "Batal"},
    "Dalam Pengiriman": {"Terkirim", "Batal"},
    "Terkirim": {"Diterima", "Batal"},
    "Diterima": {"Batal"},
    "Batal": set(),
}

PO_TRANSITIONS = {
    "Draft": {"Dikirim", "Diproses Supplier", "Barang Diterima", "Batal"},
    "Dikirim": {"Diproses Supplier", "Barang Diterima", "Batal"},
    "Diproses Supplier": {"Barang Diterima", "Batal"},
    "Barang Diterima": {"Selesai", "Batal"},
    "Selesai": {"Batal"},
    "Batal": set(),
}


def make_idempotency_key(namespace, *parts):
    canonical = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def normalize_idempotency_key(value, namespace, *fallback_parts):
    supplied = str(value or "").strip()
    if supplied:
        if len(supplied) > 180:
            raise WorkflowIntegrityError("Idempotency key terlalu panjang.")
        return f"{namespace}:{supplied}"
    return make_idempotency_key(namespace, *fallback_parts)


def record_workflow_event(
    conn,
    *,
    document_type,
    document_id,
    event_type,
    description,
    customer_id=None,
    old_status=None,
    new_status=None,
    idempotency_key=None,
    created_by="Sistem",
):
    statement = "INSERT"
    if idempotency_key:
        statement = "INSERT OR IGNORE"
    conn.execute(
        f"""
        {statement} INTO workflow_events (
            document_type,
            document_id,
            customer_id,
            event_type,
            old_status,
            new_status,
            description,
            idempotency_key,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_type,
            int(document_id),
            customer_id,
            event_type,
            old_status,
            new_status,
            description,
            idempotency_key,
            created_by or "Sistem",
        ),
    )


def validate_transition(document_type, current_status, target_status):
    if current_status == target_status:
        return False

    transitions = {
        "DELIVERY_ORDER": DO_TRANSITIONS,
        "PURCHASE_ORDER": PO_TRANSITIONS,
    }.get(document_type)
    if transitions is None:
        raise WorkflowIntegrityError(
            f"Transition document {document_type} belum didukung."
        )

    allowed = transitions.get(current_status, set())
    if target_status not in allowed:
        raise WorkflowIntegrityError(
            f"Status {document_type} tidak dapat diubah "
            f"dari {current_status} menjadi {target_status}."
        )
    return True


def _transaction_workflow_rows(conn, transaction_id):
    transaction = conn.execute(
        "SELECT * FROM sales_transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if transaction is None:
        raise WorkflowIntegrityError("Transaksi tidak ditemukan.")

    invoice = conn.execute(
        "SELECT * FROM sales_invoices WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    delivery_order = conn.execute(
        "SELECT * FROM delivery_orders WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    return transaction, invoice, delivery_order


def derive_transaction_status(transaction, invoice, delivery_order):
    current_status = transaction["status"] or "Draft"
    if current_status in ("Batal", "Cancelled"):
        return current_status

    invoice_active = invoice is not None and invoice["status_pembayaran"] != "Batal"
    paid = invoice_active and invoice["status_pembayaran"] == "Lunas"
    delivery_status = delivery_order["status"] if delivery_order else None
    delivered = delivery_status == "Diterima"
    shipped = delivery_status in ("Terkirim", "Diterima")

    if paid and delivered:
        return "Selesai"
    if paid:
        return "Lunas"
    if shipped:
        return "Terkirim"
    if invoice_active:
        return "Invoice"
    if transaction["source_quotation_id"] and target_status != "Cancelled":
        return "Closing"
    return "Draft"


def sync_transaction_status(conn, transaction_id, *, reason, actor="Sistem"):
    transaction, invoice, delivery_order = _transaction_workflow_rows(
        conn,
        transaction_id,
    )
    target_status = derive_transaction_status(
        transaction,
        invoice,
        delivery_order,
    )
    old_status = transaction["status"] or "Draft"

    if target_status != old_status:
        conn.execute(
            """
            UPDATE sales_transactions
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (target_status, transaction_id),
        )
        record_workflow_event(
            conn,
            document_type="TRANSACTION",
            document_id=transaction_id,
            customer_id=transaction["customer_id"],
            event_type="status_synchronized",
            old_status=old_status,
            new_status=target_status,
            description=reason,
            created_by=actor,
        )

    if transaction["source_quotation_id"]:
        conn.execute(
            """
            UPDATE sales_quotations
            SET status = 'Deal', updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status != 'Deal'
            """,
            (transaction["source_quotation_id"],),
        )

    return target_status


def invoice_total_tagihan(invoice):
    return max(
        int(invoice["total_penjualan"] or 0)
        - int(invoice["potongan"] or 0),
        0,
    )


def reconcile_invoice_payment(conn, invoice_id, *, actor="Sistem"):
    invoice = conn.execute(
        """
        SELECT
            sales_invoices.*,
            sales_transactions.customer_id,
            sales_transactions.total_penjualan,
            sales_transactions.potongan
        FROM sales_invoices
        INNER JOIN sales_transactions
            ON sales_invoices.transaction_id = sales_transactions.id
        WHERE sales_invoices.id = ?
        """,
        (invoice_id,),
    ).fetchone()
    if invoice is None:
        raise WorkflowIntegrityError("Invoice tidak ditemukan.")

    total_tagihan = invoice_total_tagihan(invoice)
    total_paid = int(
        conn.execute(
            """
            SELECT COALESCE(SUM(nominal), 0)
            FROM payment_receipts
            WHERE invoice_id = ? AND status != 'Void'
            """,
            (invoice_id,),
        ).fetchone()[0]
        or 0
    )
    sisa_tagihan = max(total_tagihan - total_paid, 0)

    if invoice["status_pembayaran"] == "Batal":
        target_status = "Batal"
    elif total_paid == 0:
        target_status = "Belum Lunas"
    elif total_paid < total_tagihan:
        target_status = "DP"
    else:
        target_status = "Lunas"

    if total_tagihan > 0:
        basis_points = (total_paid * 10000 + total_tagihan // 2) // total_tagihan
        dp_percent = basis_points / 100
    else:
        dp_percent = 0

    changed = (
        int(invoice["jumlah_dibayar"] or 0) != total_paid
        or invoice["status_pembayaran"] != target_status
    )
    conn.execute(
        """
        UPDATE sales_invoices
        SET jumlah_dibayar = ?,
            dp_persen = ?,
            status_pembayaran = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (total_paid, dp_percent, target_status, invoice_id),
    )

    if changed:
        record_workflow_event(
            conn,
            document_type="INVOICE",
            document_id=invoice_id,
            customer_id=invoice["customer_id"],
            event_type="payment_synchronized",
            old_status=invoice["status_pembayaran"],
            new_status=target_status,
            description=(
                "Invoice disinkronkan dari receipt non-Void: "
                f"total dibayar Rp {total_paid:,}."
            ).replace(",", "."),
            created_by=actor,
        )

    transaction_status = sync_transaction_status(
        conn,
        invoice["transaction_id"],
        reason="Status transaksi disinkronkan dari payment dan delivery.",
        actor=actor,
    )
    return {
        "invoice": invoice,
        "total_tagihan": total_tagihan,
        "total_dibayar": total_paid,
        "sisa_tagihan": sisa_tagihan,
        "status_pembayaran": target_status,
        "transaction_status": transaction_status,
    }


def _whole_stock_quantity(value, *, label):
    try:
        quantity = Decimal(str(value or 0))
    except InvalidOperation as error:
        raise WorkflowIntegrityError(f"Qty stock {label} tidak valid.") from error
    if quantity <= 0 or quantity != quantity.to_integral_value():
        raise WorkflowIntegrityError(
            f"Qty stock {label} harus bilangan bulat lebih dari 0."
        )
    return int(quantity)


def resolve_default_warehouse(conn):
    configured = conn.execute(
        """
        SELECT warehouses.id
        FROM erp_settings
        JOIN warehouses
          ON erp_settings.default_warehouse_id = warehouses.id
        WHERE erp_settings.id = 1 AND warehouses.aktif = 1
        """
    ).fetchone()
    if configured:
        return int(configured["id"])

    active = conn.execute(
        "SELECT id FROM warehouses WHERE aktif = 1 ORDER BY id"
    ).fetchall()
    if len(active) != 1:
        raise WorkflowIntegrityError(
            "Tentukan satu default warehouse sebelum stock posting."
        )
    warehouse_id = int(active[0]["id"])
    conn.execute(
        "UPDATE erp_settings SET default_warehouse_id = ? WHERE id = 1",
        (warehouse_id,),
    )
    return warehouse_id


def _stock_document(conn, source_type, source_id):
    configs = {
        "PURCHASE_ORDER": {
            "header_table": "purchase_orders",
            "item_table": "purchase_order_items",
            "item_fk": "purchase_order_id",
            "number_column": "nomor_po",
            "movement_type": "IN",
        },
        "DELIVERY_ORDER": {
            "header_table": "delivery_orders",
            "item_table": "delivery_order_items",
            "item_fk": "delivery_order_id",
            "number_column": "nomor_surat_jalan",
            "movement_type": "OUT",
        },
    }
    config = configs.get(source_type)
    if config is None:
        raise WorkflowIntegrityError("Tipe sumber stock tidak didukung.")

    header = conn.execute(
        f"SELECT * FROM {config['header_table']} WHERE id = ?",
        (source_id,),
    ).fetchone()
    if header is None:
        raise WorkflowIntegrityError("Dokumen sumber stock tidak ditemukan.")
    items = conn.execute(
        f"""
        SELECT * FROM {config['item_table']}
        WHERE {config['item_fk']} = ?
        ORDER BY id
        """,
        (source_id,),
    ).fetchall()
    if not items:
        raise WorkflowIntegrityError("Dokumen tidak memiliki item stock.")
    return config, header, items


def post_stock_for_document(conn, source_type, source_id, *, actor="Sistem"):
    config, header, items = _stock_document(conn, source_type, source_id)
    warehouse_id = int(header["warehouse_id"] or 0) or resolve_default_warehouse(conn)
    if not header["warehouse_id"]:
        conn.execute(
            f"UPDATE {config['header_table']} SET warehouse_id = ? WHERE id = ?",
            (warehouse_id, source_id),
        )

    pending = []
    required_out = {}
    for item in items:
        product_id = int(item["product_id"] or 0)
        if product_id <= 0:
            raise WorkflowIntegrityError(
                f"Item {item['id']} tidak mempunyai product_id untuk stock posting."
            )
        quantity = _whole_stock_quantity(item["qty"], label=f"item {item['id']}")
        key = f"STOCK:{source_type}:{source_id}:{item['id']}:{config['movement_type']}"
        existing = conn.execute(
            "SELECT id FROM stock_movements WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if existing:
            continue
        pending.append((item, product_id, quantity, key))
        if config["movement_type"] == "OUT":
            required_out[product_id] = required_out.get(product_id, 0) + quantity

    for product_id, required in required_out.items():
        stock = conn.execute(
            """
            SELECT stok FROM product_stock
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (warehouse_id, product_id),
        ).fetchone()
        available = int(stock["stok"] or 0) if stock else 0
        if available < required:
            raise WorkflowIntegrityError(
                f"Stok produk {product_id} tidak cukup: tersedia {available}, "
                f"dibutuhkan {required}."
            )

    for item, product_id, quantity, key in pending:
        stock = conn.execute(
            """
            SELECT * FROM product_stock
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (warehouse_id, product_id),
        ).fetchone()
        current = int(stock["stok"] or 0) if stock else 0
        delta = quantity if config["movement_type"] == "IN" else -quantity
        new_balance = current + delta
        if new_balance < 0:
            raise WorkflowIntegrityError("Stock posting menghasilkan stok negatif.")
        if stock:
            conn.execute(
                """
                UPDATE product_stock
                SET stok = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_balance, stock["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO product_stock (product_id, warehouse_id, stok)
                VALUES (?, ?, ?)
                """,
                (product_id, warehouse_id, new_balance),
            )
        conn.execute(
            """
            INSERT INTO stock_movements (
                tanggal, warehouse_id, product_id, movement_type,
                qty, saldo_setelah, referensi, catatan,
                source_type, source_id, source_item_id, idempotency_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                header["tanggal"],
                warehouse_id,
                product_id,
                config["movement_type"],
                quantity,
                new_balance,
                header[config["number_column"]],
                f"Stock {config['movement_type']} otomatis dari {source_type}.",
                source_type,
                source_id,
                item["id"],
                key,
            ),
        )

    if pending:
        record_workflow_event(
            conn,
            document_type=source_type,
            document_id=source_id,
            event_type="stock_posted",
            description=(
                f"{len(pending)} stock movement {config['movement_type']} dibuat."
            ),
            idempotency_key=f"EVENT:STOCK:{source_type}:{source_id}:{config['movement_type']}",
            created_by=actor,
        )
    return len(pending)


def reverse_stock_for_document(conn, source_type, source_id, *, actor="Sistem"):
    originals = conn.execute(
        """
        SELECT stock_movements.*
        FROM stock_movements
        LEFT JOIN stock_movements AS reversal
          ON reversal.reversal_of_id = stock_movements.id
        WHERE stock_movements.source_type = ?
          AND stock_movements.source_id = ?
          AND stock_movements.movement_type IN ('IN', 'OUT')
          AND reversal.id IS NULL
        ORDER BY stock_movements.id
        """,
        (source_type, source_id),
    ).fetchall()

    required_out = {}
    for movement in originals:
        if movement["movement_type"] == "IN":
            stock_key = (
                int(movement["warehouse_id"]),
                int(movement["product_id"]),
            )
            required_out[stock_key] = required_out.get(stock_key, 0) + int(
                movement["qty"] or 0
            )
    for (warehouse_id, product_id), required in required_out.items():
        stock = conn.execute(
            """
            SELECT stok FROM product_stock
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (warehouse_id, product_id),
        ).fetchone()
        available = int(stock["stok"] or 0) if stock else 0
        if available < required:
            raise WorkflowIntegrityError(
                "Reversal PO ditolak karena stok sudah terpakai dan akan negatif."
            )

    for movement in originals:
        stock = conn.execute(
            """
            SELECT * FROM product_stock
            WHERE warehouse_id = ? AND product_id = ?
            """,
            (movement["warehouse_id"], movement["product_id"]),
        ).fetchone()
        current = int(stock["stok"] or 0) if stock else 0
        quantity = int(movement["qty"] or 0)
        delta = -quantity if movement["movement_type"] == "IN" else quantity
        new_balance = current + delta
        if new_balance < 0:
            raise WorkflowIntegrityError("Stock reversal menghasilkan stok negatif.")
        if stock:
            conn.execute(
                "UPDATE product_stock SET stok = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_balance, stock["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO product_stock (product_id, warehouse_id, stok) VALUES (?, ?, ?)",
                (movement["product_id"], movement["warehouse_id"], new_balance),
            )
        key = f"STOCK:REVERSAL:{movement['id']}"
        conn.execute(
            """
            INSERT INTO stock_movements (
                tanggal, warehouse_id, product_id, movement_type,
                qty, saldo_setelah, referensi, catatan,
                source_type, source_id, source_item_id,
                idempotency_key, reversal_of_id
            )
            VALUES (DATE('now'), ?, ?, 'REVERSAL', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                movement["warehouse_id"],
                movement["product_id"],
                quantity,
                new_balance,
                movement["referensi"],
                f"Reversal movement {movement['id']}.",
                source_type,
                source_id,
                movement["source_item_id"],
                key,
                movement["id"],
            ),
        )

    if originals:
        record_workflow_event(
            conn,
            document_type=source_type,
            document_id=source_id,
            event_type="stock_reversed",
            description=f"{len(originals)} stock movement direversal.",
            idempotency_key=f"EVENT:REVERSAL:{source_type}:{source_id}",
            created_by=actor,
        )
    return len(originals)


def post_opening_stock(
    conn,
    *,
    warehouse_id,
    product_id,
    quantity,
    minimum_stock,
    tanggal,
    catatan,
    idempotency_key,
):
    quantity = int(quantity)
    if quantity < 0:
        raise WorkflowIntegrityError("Opening stock tidak boleh negatif.")
    key = normalize_idempotency_key(
        idempotency_key,
        "OPENING",
        warehouse_id,
        product_id,
        quantity,
        minimum_stock,
        tanggal,
        catatan,
    )
    existing_movement = conn.execute(
        "SELECT id FROM stock_movements WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if existing_movement:
        return False

    stock = conn.execute(
        """
        SELECT * FROM product_stock
        WHERE warehouse_id = ? AND product_id = ?
        """,
        (warehouse_id, product_id),
    ).fetchone()
    current = int(stock["stok"] or 0) if stock else 0
    new_balance = current + quantity
    if stock:
        conn.execute(
            """
            UPDATE product_stock
            SET stok = ?, minimum_stok = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_balance, minimum_stock, stock["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO product_stock (
                product_id, warehouse_id, stok, minimum_stok
            ) VALUES (?, ?, ?, ?)
            """,
            (product_id, warehouse_id, new_balance, minimum_stock),
        )
    conn.execute(
        """
        INSERT INTO stock_movements (
            tanggal, warehouse_id, product_id, movement_type,
            qty, saldo_setelah, referensi, catatan,
            source_type, source_id, source_item_id, idempotency_key
        )
        VALUES (?, ?, ?, 'OPENING', ?, ?, 'STOK AWAL', ?, 'OPENING', ?, ?, ?)
        """,
        (
            tanggal,
            warehouse_id,
            product_id,
            quantity,
            new_balance,
            catatan or None,
            warehouse_id,
            product_id,
            key,
        ),
    )
    return True
