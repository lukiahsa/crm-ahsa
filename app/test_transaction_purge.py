"""Strict, audited hard-delete policy for isolated test transactions only."""

import json


class TestTransactionPurgeError(ValueError):
    """Raised when a transaction cannot safely be marked or purged."""


DEPENDENCY_LABELS = {
    "invoice": "Memiliki invoice (termasuk invoice cancelled).",
    "receipt": "Memiliki receipt/kwitansi.",
    "delivery_order": "Memiliki delivery order.",
    "purchase_order": "Memiliki purchase order.",
    "stock_movement": "Memiliki stock movement atau stock posting.",
    "workflow_event": "Memiliki workflow event/posting.",
    "quotation_revision": "Menjadi transaksi lama/baru pada quotation revision.",
    "quotation_link": "Terhubung dengan quotation operasional.",
}


def _transaction_snapshot(conn, transaction_id):
    row = conn.execute(
        """SELECT t.*, c.nama AS customer_name
           FROM sales_transactions t
           LEFT JOIN customers c ON c.id = t.customer_id
           WHERE t.id = ?""",
        (transaction_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _dependency_counts(conn, transaction):
    transaction_id = int(transaction["id"])
    number = str(transaction.get("nomor_transaksi") or "")
    row = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM sales_invoices i WHERE i.transaction_id = :id) AS invoice,
             (SELECT COUNT(*) FROM payment_receipts r WHERE r.transaction_id = :id) AS receipt,
             (SELECT COUNT(*) FROM delivery_orders d WHERE d.transaction_id = :id) AS delivery_order,
             (SELECT COUNT(*) FROM purchase_orders po
                WHERE po.transaction_id = :id OR po.invoice_id IN
                    (SELECT i.id FROM sales_invoices i WHERE i.transaction_id = :id)) AS purchase_order,
             (SELECT COUNT(*) FROM stock_movements sm
                WHERE (sm.source_id = :id AND LOWER(COALESCE(sm.source_type, '')) IN
                       ('transaction', 'sales_transaction', 'sales transaction'))
                   OR (:number != '' AND sm.referensi = :number)) AS stock_movement,
             (SELECT COUNT(*) FROM workflow_events we
                WHERE UPPER(COALESCE(we.document_type, '')) IN
                      ('TRANSACTION', 'SALES_TRANSACTION') AND we.document_id = :id) AS workflow_event,
             (SELECT COUNT(*) FROM quotation_revisions qr
                WHERE qr.old_transaction_id = :id OR qr.new_transaction_id = :id) AS quotation_revision,
             (SELECT COUNT(*) FROM sales_quotations q
                WHERE q.converted_transaction_id = :id) +
             CASE WHEN :source_quotation_id IS NULL THEN 0 ELSE 1 END AS quotation_link
        """,
        {
            "id": transaction_id,
            "number": number,
            "source_quotation_id": transaction.get("source_quotation_id"),
        },
    ).fetchone()
    return {key: int(row[key] or 0) for key in DEPENDENCY_LABELS}


def can_purge_test_transaction(conn, transaction_id):
    """Return an explainable, read-only eligibility decision."""
    transaction = _transaction_snapshot(conn, transaction_id)
    if transaction is None:
        return {
            "allowed": False,
            "transaction": None,
            "reasons": ["Transaksi tidak ditemukan."],
            "dependencies": {},
        }
    dependencies = _dependency_counts(conn, transaction)
    reasons = []
    if int(transaction.get("is_test") or 0) != 1:
        reasons.append("Transaksi bukan Transaksi Uji Coba.")
    reasons.extend(
        label for key, label in DEPENDENCY_LABELS.items() if dependencies[key] > 0
    )
    return {
        "allowed": not reasons,
        "transaction": transaction,
        "reasons": reasons,
        "dependencies": dependencies,
    }


def can_mark_test_transaction(conn, transaction_id):
    """Legacy/manual rows may be marked only while operationally isolated."""
    transaction = _transaction_snapshot(conn, transaction_id)
    if transaction is None:
        return {
            "allowed": False,
            "transaction": None,
            "reasons": ["Transaksi tidak ditemukan."],
            "dependencies": {},
        }
    dependencies = _dependency_counts(conn, transaction)
    reasons = [
        label for key, label in DEPENDENCY_LABELS.items() if dependencies[key] > 0
    ]
    if int(transaction.get("is_test") or 0) == 1:
        reasons.append("Transaksi sudah ditandai sebagai Transaksi Uji Coba.")
    return {
        "allowed": not reasons,
        "transaction": transaction,
        "reasons": reasons,
        "dependencies": dependencies,
    }


def mark_test_transaction(conn, transaction_id, *, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise TestTransactionPurgeError("Alasan penandaan wajib diisi.")
    try:
        conn.execute("BEGIN IMMEDIATE")
        eligibility = can_mark_test_transaction(conn, transaction_id)
        if not eligibility["allowed"]:
            raise TestTransactionPurgeError(" ".join(eligibility["reasons"]))
        conn.execute(
            """UPDATE sales_transactions
               SET is_test = 1, test_label = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (reason, transaction_id),
        )
        conn.commit()
        return can_purge_test_transaction(conn, transaction_id)
    except Exception:
        conn.rollback()
        raise


def purge_test_transaction(
    conn,
    transaction_id,
    *,
    reason,
    confirmation_number,
    actor="Sistem",
    _failure_hook=None,
):
    """Atomically snapshot, audit, and delete one isolated test transaction."""
    reason = str(reason or "").strip()
    confirmation_number = str(confirmation_number or "").strip()
    actor = str(actor or "").strip() or "Sistem"
    if not reason:
        raise TestTransactionPurgeError("Alasan penghapusan wajib diisi.")
    if not confirmation_number:
        raise TestTransactionPurgeError("Konfirmasi nomor transaksi wajib diisi.")

    try:
        conn.execute("BEGIN IMMEDIATE")
        eligibility = can_purge_test_transaction(conn, transaction_id)
        if not eligibility["allowed"]:
            raise TestTransactionPurgeError(" ".join(eligibility["reasons"]))
        transaction = eligibility["transaction"]
        if confirmation_number != str(transaction.get("nomor_transaksi") or ""):
            raise TestTransactionPurgeError("Nomor transaksi konfirmasi tidak cocok.")

        items = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM sales_transaction_items WHERE transaction_id = ? ORDER BY id",
                (transaction_id,),
            ).fetchall()
        ]
        payload = {
            "transaction": transaction,
            "items": items,
            "eligibility": eligibility["dependencies"],
        }
        cursor = conn.execute(
            """INSERT INTO test_transaction_purge_audit (
                   transaction_id_snapshot, nomor_transaction_snapshot, customer_id,
                   customer_name_snapshot, tanggal_snapshot, total_penjualan_snapshot,
                   total_modal_snapshot, margin_snapshot, status_snapshot, reason,
                   purged_by, payload_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                transaction["id"],
                transaction.get("nomor_transaksi") or "-",
                transaction.get("customer_id"),
                transaction.get("customer_name"),
                transaction.get("tanggal"),
                int(transaction.get("total_penjualan") or 0),
                int(transaction.get("total_modal") or 0),
                int(transaction.get("margin") or 0),
                transaction.get("status"),
                reason,
                actor,
                json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True),
            ),
        )
        audit_id = cursor.lastrowid
        if _failure_hook:
            _failure_hook("after_audit")
        conn.execute(
            "DELETE FROM sales_transaction_items WHERE transaction_id = ?",
            (transaction_id,),
        )
        if _failure_hook:
            _failure_hook("after_items")
        deleted = conn.execute(
            "DELETE FROM sales_transactions WHERE id = ? AND is_test = 1",
            (transaction_id,),
        ).rowcount
        if deleted != 1:
            raise TestTransactionPurgeError("Transaksi berubah saat purge; operasi dibatalkan.")
        conn.commit()
        return {
            "audit_id": audit_id,
            "transaction_id": transaction_id,
            "nomor_transaksi": transaction.get("nomor_transaksi"),
        }
    except Exception:
        conn.rollback()
        raise
