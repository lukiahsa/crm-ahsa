"""Controlled cancellation and quotation revision workflow for Sprint 12."""

from workflow_integrity import WorkflowIntegrityError, record_workflow_event


TRANSACTION_CANCELLED = "Cancelled"
QUOTATION_REVISION_ALLOWED = "Revision Allowed"


DEPENDENCY_RULES = (
    (
        "Invoice",
        """
        SELECT COUNT(*) FROM sales_invoices
        WHERE transaction_id = ? AND status_pembayaran != 'Batal'
        """,
        lambda transaction_id: (transaction_id,),
    ),
    (
        "Receipt",
        """
        SELECT COUNT(*) FROM payment_receipts
        WHERE transaction_id = ? AND status != 'Void'
        """,
        lambda transaction_id: (transaction_id,),
    ),
    (
        "Delivery Order",
        """
        SELECT COUNT(*) FROM delivery_orders
        WHERE transaction_id = ? AND status != 'Batal'
        """,
        lambda transaction_id: (transaction_id,),
    ),
    (
        "Purchase Order",
        """
        SELECT COUNT(*) FROM purchase_orders
        WHERE status != 'Batal'
          AND (
                transaction_id = ?
             OR invoice_id IN (
                  SELECT id FROM sales_invoices WHERE transaction_id = ?
             )
          )
        """,
        lambda transaction_id: (transaction_id, transaction_id),
    ),
)


def _transaction(conn, transaction_id):
    row = conn.execute(
        "SELECT * FROM sales_transactions WHERE id = ?",
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise WorkflowIntegrityError("Transaction tidak ditemukan.")
    return row


def workflow_dependencies(conn, transaction_id):
    """Return active downstream dependencies with a bounded query count."""
    transaction_id = int(transaction_id)
    blockers = []
    counts = {}
    for label, statement, parameters in DEPENDENCY_RULES:
        count = int(
            conn.execute(statement, parameters(transaction_id)).fetchone()[0]
            or 0
        )
        counts[label] = count
        if count:
            blockers.append(label)
    return {"blockers": blockers, "counts": counts}


def _blocked_message(blockers):
    if not blockers:
        return None
    documents = "\n".join(f"- {label}" for label in blockers)
    return (
        "Quotation tidak dapat direvisi.\n\n"
        "Masih memiliki:\n"
        f"{documents}\n\n"
        "Silakan batalkan dokumen tersebut terlebih dahulu."
    )


def cancellation_eligibility(conn, transaction_id):
    transaction = _transaction(conn, transaction_id)
    dependencies = workflow_dependencies(conn, transaction_id)
    blockers = dependencies["blockers"]
    allowed = transaction["status"] not in ("Batal", TRANSACTION_CANCELLED) and not blockers
    if transaction["status"] == TRANSACTION_CANCELLED:
        message = "Transaction sudah Cancelled."
    elif transaction["status"] == "Batal":
        message = "Transaction legacy berstatus Batal dan tidak dapat dibatalkan ulang."
    else:
        message = _blocked_message(blockers)
    return {
        "allowed": allowed,
        "blockers": blockers,
        "counts": dependencies["counts"],
        "message": message,
    }


def can_unlock_quotation(conn, transaction_id):
    """Evaluate unlock eligibility without mutating workflow state."""
    transaction = _transaction(conn, transaction_id)
    dependencies = workflow_dependencies(conn, transaction_id)
    blockers = dependencies["blockers"]
    quotation_id = transaction["source_quotation_id"]
    if not quotation_id:
        linked = conn.execute(
            """
            SELECT quotation_id
            FROM quotation_revisions
            WHERE old_transaction_id = ? OR new_transaction_id = ?
            ORDER BY revision_no DESC, id DESC
            LIMIT 1
            """,
            (transaction_id, transaction_id),
        ).fetchone()
        quotation_id = linked["quotation_id"] if linked else None

    quotation = None
    if quotation_id:
        quotation = conn.execute(
            "SELECT status, converted_transaction_id FROM sales_quotations WHERE id = ?",
            (quotation_id,),
        ).fetchone()
    already_unlocked = bool(
        quotation
        and quotation["status"] == QUOTATION_REVISION_ALLOWED
        and quotation["converted_transaction_id"] is None
    )

    allowed = (
        transaction["status"] == TRANSACTION_CANCELLED
        and quotation_id is not None
        and not blockers
        and not already_unlocked
    )
    if transaction["status"] != TRANSACTION_CANCELLED:
        message = "Unlock hanya tersedia setelah Transaction berstatus Cancelled."
    elif quotation_id is None:
        message = "Transaction tidak berasal dari Quotation."
    elif already_unlocked:
        message = "Quotation sudah Revision Allowed."
    else:
        message = _blocked_message(blockers)
    return {
        "allowed": allowed,
        "quotation_id": quotation_id,
        "blockers": blockers,
        "counts": dependencies["counts"],
        "message": message,
    }


def _quotation_activity(conn, quotation_id, activity_type, description, actor):
    conn.execute(
        """
        INSERT INTO sales_quotation_activities (
            quotation_id, activity_type, description, created_by
        ) VALUES (?, ?, ?, ?)
        """,
        (quotation_id, activity_type, description, actor or "Sistem"),
    )


def cancel_transaction(conn, transaction_id, *, reason, actor="Sistem"):
    reason = str(reason or "").strip()
    if not reason:
        raise WorkflowIntegrityError("Alasan pembatalan wajib diisi.")
    transaction = _transaction(conn, transaction_id)
    eligibility = cancellation_eligibility(conn, transaction_id)
    if not eligibility["allowed"]:
        raise WorkflowIntegrityError(
            eligibility["message"] or "Transaction tidak dapat dibatalkan."
        )

    conn.execute(
        """
        UPDATE sales_transactions
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (TRANSACTION_CANCELLED, transaction_id),
    )
    record_workflow_event(
        conn,
        document_type="TRANSACTION",
        document_id=transaction_id,
        customer_id=transaction["customer_id"],
        event_type="TRANSACTION_CANCELLED",
        old_status=transaction["status"],
        new_status=TRANSACTION_CANCELLED,
        description=f"Transaction dibatalkan. Alasan: {reason}",
        created_by=actor,
    )
    return {"transaction_id": transaction_id, "status": TRANSACTION_CANCELLED}


def unlock_quotation(conn, transaction_id, *, reason=None, actor="Sistem"):
    transaction = _transaction(conn, transaction_id)
    eligibility = can_unlock_quotation(conn, transaction_id)
    if not eligibility["allowed"]:
        raise WorkflowIntegrityError(
            eligibility["message"] or "Quotation tidak dapat direvisi."
        )

    quotation_id = int(eligibility["quotation_id"])
    quotation = conn.execute(
        "SELECT * FROM sales_quotations WHERE id = ?",
        (quotation_id,),
    ).fetchone()
    if quotation is None:
        raise WorkflowIntegrityError("Quotation sumber tidak ditemukan.")

    existing = conn.execute(
        """
        SELECT * FROM quotation_revisions
        WHERE quotation_id = ? AND old_transaction_id = ?
        ORDER BY revision_no DESC LIMIT 1
        """,
        (quotation_id, transaction_id),
    ).fetchone()
    if existing and quotation["status"] == QUOTATION_REVISION_ALLOWED:
        return {
            "quotation_id": quotation_id,
            "revision_no": int(existing["revision_no"]),
        }

    latest_revision = conn.execute(
        "SELECT COALESCE(MAX(revision_no), 0) FROM quotation_revisions WHERE quotation_id = ?",
        (quotation_id,),
    ).fetchone()[0]
    revision_no = max(int(quotation["revisi"] or 0), int(latest_revision or 0)) + 1
    revision_reason = str(reason or "").strip() or (
        f"Revisi setelah pembatalan Transaction {transaction['nomor_transaksi']}."
    )

    conn.execute(
        """
        INSERT INTO quotation_revisions (
            quotation_id, revision_no, reason, old_transaction_id, created_by
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (quotation_id, revision_no, revision_reason, transaction_id, actor),
    )
    conn.execute(
        """
        UPDATE sales_quotations
        SET status = ?, revisi = ?, converted_transaction_id = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (QUOTATION_REVISION_ALLOWED, revision_no, quotation_id),
    )
    # Unique conversion constraint tetap dipertahankan. Relasi historis pindah
    # ke quotation_revisions agar revision berikutnya dapat dikonversi aman.
    conn.execute(
        """
        UPDATE sales_transactions
        SET source_quotation_id = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (transaction_id,),
    )
    description = (
        f"Quotation {quotation['nomor_penawaran']} dibuka untuk Revision {revision_no}. "
        f"Alasan: {revision_reason}"
    )
    record_workflow_event(
        conn,
        document_type="QUOTATION",
        document_id=quotation_id,
        customer_id=quotation["customer_id"],
        event_type="QUOTATION_UNLOCKED",
        old_status=quotation["status"],
        new_status=QUOTATION_REVISION_ALLOWED,
        description=description,
        created_by=actor,
    )
    _quotation_activity(conn, quotation_id, "unlocked", description, actor)
    return {"quotation_id": quotation_id, "revision_no": revision_no}


def mark_quotation_revised(conn, quotation_id, *, actor="Sistem"):
    quotation = conn.execute(
        "SELECT * FROM sales_quotations WHERE id = ?",
        (quotation_id,),
    ).fetchone()
    if quotation is None:
        raise WorkflowIntegrityError("Quotation tidak ditemukan.")
    description = (
        f"Quotation {quotation['nomor_penawaran']} Revision "
        f"{int(quotation['revisi'] or 0)} disimpan."
    )
    record_workflow_event(
        conn,
        document_type="QUOTATION",
        document_id=quotation_id,
        customer_id=quotation["customer_id"],
        event_type="QUOTATION_REVISED",
        old_status=QUOTATION_REVISION_ALLOWED,
        new_status="Revisi",
        description=description,
        created_by=actor,
    )
    _quotation_activity(conn, quotation_id, "revised", description, actor)


def link_revision_transaction(conn, quotation_id, transaction_id):
    """Attach a new conversion to the latest open revision, if one exists."""
    result = conn.execute(
        """
        UPDATE quotation_revisions
        SET new_transaction_id = ?
        WHERE id = (
            SELECT id FROM quotation_revisions
            WHERE quotation_id = ? AND new_transaction_id IS NULL
            ORDER BY revision_no DESC, id DESC LIMIT 1
        )
        """,
        (transaction_id, quotation_id),
    )
    return result.rowcount == 1


def transaction_revision_context(conn, transaction_id):
    transaction = _transaction(conn, transaction_id)
    eligibility = cancellation_eligibility(conn, transaction_id)
    unlock = can_unlock_quotation(conn, transaction_id)
    revision = conn.execute(
        """
        SELECT qr.*, q.nomor_penawaran, q.status AS quotation_status
        FROM quotation_revisions qr
        JOIN sales_quotations q ON q.id = qr.quotation_id
        WHERE qr.old_transaction_id = ? OR qr.new_transaction_id = ?
        ORDER BY qr.revision_no DESC, qr.id DESC LIMIT 1
        """,
        (transaction_id, transaction_id),
    ).fetchone()
    return {
        "status": transaction["status"],
        "display_status": (
            QUOTATION_REVISION_ALLOWED
            if revision and revision["quotation_status"] == QUOTATION_REVISION_ALLOWED
            else transaction["status"]
        ),
        "can_cancel": eligibility,
        "can_unlock": unlock,
        "revision": dict(revision) if revision else None,
    }


def quotation_revision_context(conn, quotation_id):
    quotation = conn.execute(
        "SELECT * FROM sales_quotations WHERE id = ?",
        (quotation_id,),
    ).fetchone()
    if quotation is None:
        raise WorkflowIntegrityError("Quotation tidak ditemukan.")
    revisions = conn.execute(
        """
        SELECT qr.*, old_t.nomor_transaksi AS old_transaction_number,
               new_t.nomor_transaksi AS new_transaction_number
        FROM quotation_revisions qr
        LEFT JOIN sales_transactions old_t ON old_t.id = qr.old_transaction_id
        LEFT JOIN sales_transactions new_t ON new_t.id = qr.new_transaction_id
        WHERE qr.quotation_id = ?
        ORDER BY qr.revision_no DESC, qr.id DESC
        """,
        (quotation_id,),
    ).fetchall()
    transaction = None
    if quotation["converted_transaction_id"]:
        transaction = conn.execute(
            "SELECT * FROM sales_transactions WHERE id = ?",
            (quotation["converted_transaction_id"],),
        ).fetchone()
    elif revisions:
        candidate = revisions[0]["old_transaction_id"] or revisions[0]["new_transaction_id"]
        if candidate:
            transaction = conn.execute(
                "SELECT * FROM sales_transactions WHERE id = ?",
                (candidate,),
            ).fetchone()
    unlock = can_unlock_quotation(conn, transaction["id"]) if transaction else None
    return {
        "transaction": dict(transaction) if transaction else None,
        "unlock": unlock,
        "revisions": [dict(row) for row in revisions],
        "editable": quotation["converted_transaction_id"] is None,
    }
