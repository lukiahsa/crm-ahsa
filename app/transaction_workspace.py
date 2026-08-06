"""Read-model and isolated writes for the Sprint 15 Transaction Workspace."""

from hashlib import sha256

from customer_360 import get_customer_360


ATTACHMENT_TYPES = (
    "PDF",
    "Foto",
    "Surat Jalan",
    "Bukti Transfer",
    "Dokumen Lain",
)
ALLOWED_ATTACHMENT_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "doc",
    "docx",
    "xls",
    "xlsx",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class TransactionWorkspaceError(ValueError):
    pass


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def _active_transaction_status(alias="t"):
    return f"LOWER(COALESCE({alias}.status, '')) NOT IN ('batal', 'cancelled')"


def get_transaction_workspace(conn, transaction_id, module_states):
    """Build the complete workspace without mutating any existing engine."""
    transaction_id = int(transaction_id)
    transaction = _one(
        conn,
        """
        SELECT t.*, c.nama AS customer_nama, c.instansi AS customer_instansi,
               c.whatsapp AS customer_whatsapp,
               COALESCE(c.whatsapp_normalized, c.whatsapp) AS customer_whatsapp_normalized,
               c.kota AS customer_kota, c.pic AS customer_pic,
               c.sumber AS customer_sumber
        FROM sales_transactions t
        LEFT JOIN customers c ON c.id = t.customer_id
        WHERE t.id = ?
        """,
        (transaction_id,),
    )
    if transaction is None:
        return None

    items = _rows(
        conn,
        """SELECT * FROM sales_transaction_items
           WHERE transaction_id = ? ORDER BY id""",
        (transaction_id,),
    )

    quotations = _rows(
        conn,
        """
        SELECT DISTINCT q.*
        FROM sales_quotations q
        WHERE q.id = ?
           OR q.id IN (
                SELECT quotation_id FROM quotation_revisions
                WHERE old_transaction_id = ? OR new_transaction_id = ?
           )
        ORDER BY q.tanggal DESC, q.id DESC
        """,
        (transaction["source_quotation_id"], transaction_id, transaction_id),
    )
    invoices = _rows(
        conn,
        """SELECT * FROM sales_invoices
           WHERE transaction_id = ? ORDER BY created_at DESC, id DESC""",
        (transaction_id,),
    )
    invoice_receipts = _rows(
        conn,
        """SELECT r.*, 'INVOICE_RECEIPT' AS receipt_source
           FROM payment_receipts r
           WHERE r.transaction_id = ?
           ORDER BY r.created_at DESC, r.id DESC""",
        (transaction_id,),
    )
    direct_receipts = _rows(
        conn,
        """SELECT r.*, 'TRANSACTION_RECEIPT' AS receipt_source
           FROM transaction_receipts r
           WHERE r.transaction_id = ?
           ORDER BY r.created_at DESC, r.id DESC""",
        (transaction_id,),
    )
    receipts = sorted(
        invoice_receipts + direct_receipts,
        key=lambda row: (str(row.get("created_at") or ""), int(row["id"])),
        reverse=True,
    )
    delivery_orders = _rows(
        conn,
        """SELECT * FROM delivery_orders
           WHERE transaction_id = ? ORDER BY created_at DESC, id DESC""",
        (transaction_id,),
    )
    purchase_orders = _rows(
        conn,
        """
        SELECT po.* FROM purchase_orders po
        WHERE po.transaction_id = ?
           OR po.invoice_id IN (
                SELECT id FROM sales_invoices WHERE transaction_id = ?
           )
        ORDER BY po.created_at DESC, po.id DESC
        """,
        (transaction_id, transaction_id),
    )

    payment = _payment_summary(transaction, receipts)
    financial = _financial_summary(transaction)
    document_statuses, document_history = _document_read_model(
        transaction_id,
        quotations,
        invoices,
        receipts,
        delivery_orders,
        purchase_orders,
        module_states,
    )

    notes = _rows(
        conn,
        """SELECT * FROM transaction_workspace_notes
           WHERE transaction_id = ? ORDER BY created_at DESC, id DESC""",
        (transaction_id,),
    )
    attachments = _rows(
        conn,
        """SELECT id, transaction_id, attachment_type, original_filename,
                  stored_filename, mime_type, file_size, sha256, uploaded_by,
                  created_at
           FROM transaction_attachments
           WHERE transaction_id = ? ORDER BY created_at DESC, id DESC""",
        (transaction_id,),
    )
    customer_360 = (
        get_customer_360(conn, transaction["customer_id"])
        if transaction["customer_id"] is not None
        else None
    )
    customer_snapshot = _customer_snapshot(customer_360)
    timeline = _activity_timeline(
        conn,
        transaction,
        quotations,
        invoices,
        receipts,
        delivery_orders,
        purchase_orders,
        notes,
        attachments,
    )

    return {
        "transaction": transaction,
        "items": items,
        "payment": payment,
        "financial": financial,
        "document_statuses": document_statuses,
        "document_history": document_history,
        "timeline": timeline,
        "notes": notes,
        "attachments": attachments,
        "customer_snapshot": customer_snapshot,
        "documents": {
            "quotations": quotations,
            "invoices": invoices,
            "receipts": receipts,
            "delivery_orders": delivery_orders,
            "purchase_orders": purchase_orders,
        },
    }


def _payment_summary(transaction, receipts):
    active = [
        row for row in receipts
        if str(row.get("status") or "").casefold() != "void"
    ]
    paid_total = sum(max(int(row.get("nominal") or 0), 0) for row in active)
    dp = sum(
        max(int(row.get("nominal") or 0), 0)
        for row in active
        if str(row.get("jenis_pembayaran") or "").strip().casefold() == "dp"
    )
    grand_total = max(
        int(transaction.get("total_penjualan") or 0)
        - int(transaction.get("potongan") or 0),
        0,
    )
    outstanding = max(grand_total - paid_total, 0)
    return {
        "grand_total": grand_total,
        "dp": dp,
        "pelunasan": max(paid_total - dp, 0),
        "paid_total": paid_total,
        "outstanding": outstanding,
        "status": "Lunas" if grand_total > 0 and outstanding == 0 else "Belum Lunas",
    }


def _financial_summary(transaction):
    subtotal = int(transaction.get("total_penjualan") or 0)
    margin = int(transaction.get("margin") or 0)
    return {
        "modal": int(transaction.get("total_modal") or 0),
        "subtotal": subtotal,
        "margin": margin,
        "profit": int(transaction.get("laba_bersih") or 0),
        "margin_percent": (margin * 100 / subtotal) if subtotal else 0,
    }


def _enabled(module_states, module_key):
    state = module_states.get(module_key)
    return True if state is None else bool(state.get("enabled"))


def _document_read_model(
    transaction_id,
    quotations,
    invoices,
    receipts,
    delivery_orders,
    purchase_orders,
    module_states,
):
    specs = (
        ("quotation", "Quotation", quotations, "nomor_penawaran"),
        ("invoice", "Invoice", invoices, "nomor_invoice"),
        ("receipt", "Receipt", receipts, "nomor_kwitansi"),
        ("delivery_order", "Delivery Order", delivery_orders, "nomor_surat_jalan"),
        ("purchase_order", "Purchase Order", purchase_orders, "nomor_po"),
    )
    statuses = []
    history = []
    for module_key, label, rows, number_key in specs:
        last = rows[0] if rows else None
        statuses.append(
            {
                "module_key": module_key,
                "label": label,
                "enabled": _enabled(module_states, module_key),
                "created": bool(rows),
                "count": len(rows),
                "last_number": last.get(number_key) if last else None,
            }
        )
        for row in rows:
            entry = {
                "module_key": module_key,
                "label": label,
                "number": row.get(number_key),
                "status": row.get("status") or row.get("status_pembayaran") or "Dibuat",
                "date": (
                    row.get("tanggal")
                    or row.get("tanggal_invoice")
                    or row.get("created_at")
                ),
                "created_at": row.get("created_at") or "",
                "enabled": _enabled(module_states, module_key),
            }
            if module_key == "quotation":
                entry.update(endpoint="quotation_detail", parameter="quotation_id", id=row["id"])
            elif module_key == "invoice":
                entry.update(endpoint="print_invoice", parameter="transaction_id", id=transaction_id)
            elif module_key == "receipt":
                direct = row.get("receipt_source") == "TRANSACTION_RECEIPT"
                entry.update(
                    endpoint="transaction_receipt_detail" if direct else "receipt_detail",
                    parameter="receipt_id",
                    id=row["id"],
                )
            elif module_key == "delivery_order":
                entry.update(endpoint="delivery_order_detail", parameter="delivery_order_id", id=row["id"])
            else:
                entry.update(endpoint="purchase_order_detail", parameter="purchase_order_id", id=row["id"])
            history.append(entry)
    history.sort(
        key=lambda row: (str(row.get("created_at") or row.get("date") or ""), int(row["id"])),
        reverse=True,
    )
    return statuses, history


def _customer_snapshot(customer_360):
    if not customer_360:
        return None
    kpis = customer_360["kpis"]
    favorites = customer_360.get("favorites") or []
    return {
        "customer_id": customer_360["customer"]["id"],
        "customer_name": customer_360["customer"]["nama"],
        "is_repeat_customer": bool(kpis["is_repeat_customer"]),
        "historical_purchase_count": len(customer_360.get("historical_purchases") or []),
        "total_revenue": int(kpis["total_omzet"] or 0),
        "last_order": kpis["order_terakhir"],
        "top_product": favorites[0]["nama"] if favorites else None,
    }


def _activity_timeline(
    conn,
    transaction,
    quotations,
    invoices,
    receipts,
    delivery_orders,
    purchase_orders,
    notes,
    attachments,
):
    events = []

    def add(event_at, event_type, description, *, actor=None, document_type=None):
        events.append(
            {
                "event_at": event_at or "",
                "event_type": event_type,
                "description": description,
                "actor": actor,
                "document_type": document_type,
            }
        )

    add(
        transaction.get("created_at") or transaction.get("tanggal"),
        "Transaction dibuat",
        f"Transaction {transaction.get('nomor_transaksi') or '-'} dibuat.",
        actor=transaction.get("referal"),
        document_type="TRANSACTION",
    )
    base_specs = (
        (quotations, "created_at", "Quotation dibuat", "nomor_penawaran", "QUOTATION"),
        (invoices, "created_at", "Invoice dibuat", "nomor_invoice", "INVOICE"),
        (receipts, "created_at", "Receipt dibuat", "nomor_kwitansi", "RECEIPT"),
        (delivery_orders, "created_at", "DO dibuat", "nomor_surat_jalan", "DELIVERY_ORDER"),
        (purchase_orders, "created_at", "PO dibuat", "nomor_po", "PURCHASE_ORDER"),
    )
    for rows, date_key, event_type, number_key, document_type in base_specs:
        for row in rows:
            add(
                row.get(date_key) or row.get("tanggal") or row.get("tanggal_invoice"),
                event_type,
                f"{event_type.replace(' dibuat', '')} {row.get(number_key) or '-'} dibuat.",
                actor=row.get("created_by"),
                document_type=document_type,
            )

    related = {
        "TRANSACTION": {transaction["id"]},
        "QUOTATION": {row["id"] for row in quotations},
        "INVOICE": {row["id"] for row in invoices},
        "RECEIPT": {
            row["id"] for row in receipts
            if row.get("receipt_source") == "INVOICE_RECEIPT"
        },
        "TRANSACTION_RECEIPT": {
            row["id"] for row in receipts
            if row.get("receipt_source") == "TRANSACTION_RECEIPT"
        },
        "DELIVERY_ORDER": {row["id"] for row in delivery_orders},
        "PURCHASE_ORDER": {row["id"] for row in purchase_orders},
    }
    workflow_events = _rows(
        conn,
        """SELECT * FROM workflow_events
           WHERE customer_id = ? OR (document_type = 'TRANSACTION' AND document_id = ?)
           ORDER BY created_at DESC, id DESC""",
        (transaction.get("customer_id"), transaction["id"]),
    )
    for row in workflow_events:
        document_type = str(row.get("document_type") or "").upper()
        if int(row.get("document_id") or 0) not in related.get(document_type, set()):
            continue
        event_type = str(row.get("event_type") or "Workflow Event")
        if "cancel" in event_type.casefold():
            label = "Cancellation"
        elif "status" in event_type.casefold() or row.get("new_status"):
            label = "Status berubah"
        else:
            label = "Workflow Event"
        add(
            row.get("created_at"),
            label,
            row.get("description") or event_type,
            actor=row.get("created_by"),
            document_type=document_type,
        )

    revisions = _rows(
        conn,
        """SELECT * FROM quotation_revisions
           WHERE old_transaction_id = ? OR new_transaction_id = ?
           ORDER BY created_at DESC, id DESC""",
        (transaction["id"], transaction["id"]),
    )
    for row in revisions:
        add(
            row.get("created_at"),
            "Revision",
            f"Revision {row.get('revision_no')} — {row.get('reason') or '-'}",
            actor=row.get("created_by"),
            document_type="QUOTATION",
        )
    for row in notes:
        add(
            row.get("created_at"),
            "Catatan internal",
            row.get("note_text"),
            actor=row.get("created_by"),
            document_type="TRANSACTION",
        )
    for row in attachments:
        add(
            row.get("created_at"),
            "Attachment",
            f"{row.get('attachment_type')}: {row.get('original_filename')}",
            actor=row.get("uploaded_by"),
            document_type="TRANSACTION",
        )

    events.sort(
        key=lambda row: (str(row.get("event_at") or ""), row.get("event_type") or ""),
        reverse=True,
    )
    return events


def add_workspace_note(conn, transaction_id, note_text, created_by=None):
    note = str(note_text or "").strip()
    if not note:
        raise TransactionWorkspaceError("Catatan internal wajib diisi.")
    if len(note) > 4000:
        raise TransactionWorkspaceError("Catatan internal maksimal 4.000 karakter.")
    if _one(conn, "SELECT id FROM sales_transactions WHERE id = ?", (transaction_id,)) is None:
        raise TransactionWorkspaceError("Transaction tidak ditemukan.")
    return conn.execute(
        """INSERT INTO transaction_workspace_notes(transaction_id, note_text, created_by)
           VALUES (?, ?, ?)""",
        (int(transaction_id), note, str(created_by or "").strip() or "Sistem"),
    ).lastrowid


def add_transaction_attachment(
    conn,
    transaction_id,
    *,
    attachment_type,
    original_filename,
    stored_filename,
    mime_type,
    content,
    uploaded_by=None,
):
    if attachment_type not in ATTACHMENT_TYPES:
        raise TransactionWorkspaceError("Jenis attachment tidak valid.")
    original = str(original_filename or "").strip()
    stored = str(stored_filename or "").strip()
    extension = stored.rsplit(".", 1)[-1].casefold() if "." in stored else ""
    if not stored or extension not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise TransactionWorkspaceError("Format attachment tidak didukung.")
    payload = bytes(content or b"")
    if not payload:
        raise TransactionWorkspaceError("File attachment kosong.")
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise TransactionWorkspaceError("Ukuran attachment maksimal 10 MB.")
    if _one(conn, "SELECT id FROM sales_transactions WHERE id = ?", (transaction_id,)) is None:
        raise TransactionWorkspaceError("Transaction tidak ditemukan.")
    return conn.execute(
        """
        INSERT INTO transaction_attachments(
            transaction_id, attachment_type, original_filename,
            stored_filename, mime_type, file_size, sha256, content, uploaded_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(transaction_id),
            attachment_type,
            original,
            stored,
            str(mime_type or "application/octet-stream"),
            len(payload),
            sha256(payload).hexdigest(),
            payload,
            str(uploaded_by or "").strip() or "Sistem",
        ),
    ).lastrowid


def get_transaction_attachment(conn, transaction_id, attachment_id):
    return _one(
        conn,
        """SELECT * FROM transaction_attachments
           WHERE id = ? AND transaction_id = ?""",
        (int(attachment_id), int(transaction_id)),
    )
