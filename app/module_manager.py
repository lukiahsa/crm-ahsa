"""ATCA module registry, policy, and route mapping."""


class ModuleManagerError(ValueError):
    pass


CORE_MODULE_KEYS = (
    "customer",
    "product",
    "transaction",
    "historical_purchase",
    "dashboard",
    "customer_360",
)

OPTIONAL_MODULE_KEYS = (
    "quotation",
    "invoice",
    "receipt",
    "delivery_order",
    "purchase_order",
    "inventory",
    "warehouse",
    "accounting",
    "purchasing",
)


def get_module_states(conn):
    rows = conn.execute(
        """SELECT module_key, module_name, module_type, is_core,
                  is_enabled, sort_order, updated_at, updated_by
           FROM system_modules ORDER BY sort_order, module_key"""
    ).fetchall()
    return {
        row["module_key"]: {
            "key": row["module_key"],
            "name": row["module_name"],
            "type": row["module_type"],
            "is_core": bool(row["is_core"]),
            "enabled": bool(row["is_enabled"]),
            "sort_order": int(row["sort_order"] or 0),
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }
        for row in rows
    }


def module_is_enabled(conn, module_key):
    if module_key in CORE_MODULE_KEYS:
        return True
    row = conn.execute(
        "SELECT is_enabled FROM system_modules WHERE module_key = ?",
        (module_key,),
    ).fetchone()
    return bool(row and row["is_enabled"])


def update_optional_modules(conn, enabled_keys, *, actor="Sistem"):
    enabled = {str(key).strip() for key in enabled_keys}
    unknown = enabled.difference(OPTIONAL_MODULE_KEYS)
    if unknown:
        raise ModuleManagerError(
            "Module tidak dikenal: " + ", ".join(sorted(unknown))
        )
    actor = str(actor or "").strip() or "Sistem"
    for module_key in OPTIONAL_MODULE_KEYS:
        conn.execute(
            """UPDATE system_modules
               SET is_enabled = ?, updated_at = CURRENT_TIMESTAMP, updated_by = ?
               WHERE module_key = ? AND is_core = 0""",
            (1 if module_key in enabled else 0, actor, module_key),
        )
    # Keep the legacy inventory flag synchronized without changing its engine.
    conn.execute(
        """UPDATE erp_settings SET inventory_enabled = ?, updated_at = CURRENT_TIMESTAMP
           WHERE id = 1""",
        (1 if "inventory" in enabled else 0,),
    )
    return get_module_states(conn)


def module_for_request(path, endpoint=None):
    """Resolve optional ownership before any route workflow executes."""
    path = str(path or "")
    endpoint = str(endpoint or "")
    if path.startswith("/settings/modules"):
        return None
    if path.startswith("/transactions/") and "/purchase-order/" in path:
        return "purchase_order"
    if path.startswith("/purchase-orders"):
        return "purchase_order"
    if path.startswith("/transactions/") and "/delivery-order/" in path:
        return "delivery_order"
    if path.startswith("/delivery-orders"):
        return "delivery_order"
    if path.startswith("/transactions/") and "/receipt" in path:
        return "receipt"
    if (
        path.startswith("/receipts")
        or path.startswith("/transaction-receipts")
        or path.startswith("/invoices/") and "/receipts" in path
    ):
        return "receipt"
    if path.startswith("/transactions/") and "/invoice" in path:
        return "invoice"
    if path.startswith("/invoices"):
        return "invoice"
    if path.startswith("/quotations"):
        return "quotation"
    if path.startswith("/stocks") or path.startswith("/settings/inventory"):
        return "inventory"
    if path.startswith("/warehouses"):
        return "warehouse"
    if path.startswith("/suppliers"):
        return "purchasing"
    return None
