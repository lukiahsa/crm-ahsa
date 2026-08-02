"""Master customer/product helpers for Quotation Engine V2."""

SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LIMIT = 20


def _value(record, key, default=None):
    if record is None:
        return default
    try:
        value = record[key]
    except (KeyError, IndexError):
        value = default
    return value


def normalize_search_limit(value):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = SEARCH_MAX_LIMIT
    return max(1, min(limit, SEARCH_MAX_LIMIT))


def get_customer_for_quotation(conn, customer_id, *, active_only=False):
    try:
        normalized_id = int(customer_id)
    except (TypeError, ValueError):
        return None

    query = "SELECT * FROM customers WHERE id = ?"
    if active_only:
        query += " AND COALESCE(status_aktif, 1) = 1"
    return conn.execute(query, (normalized_id,)).fetchone()


PRODUCT_SELECT = """
    SELECT
        products.*,
        product_categories.nama AS kategori_nama,
        product_brands.nama AS brand_nama,
        product_variants.nama AS varian_nama,
        product_colors.nama AS warna_nama,
        product_sizes.nama AS ukuran_nama
    FROM products
    LEFT JOIN product_categories
        ON products.category_id = product_categories.id
    LEFT JOIN product_brands
        ON products.brand_id = product_brands.id
    LEFT JOIN product_variants
        ON products.variant_id = product_variants.id
    LEFT JOIN product_colors
        ON products.color_id = product_colors.id
    LEFT JOIN product_sizes
        ON products.size_id = product_sizes.id
"""


def get_product_for_quotation(conn, product_id, *, active_only=False):
    try:
        normalized_id = int(product_id)
    except (TypeError, ValueError):
        return None

    query = PRODUCT_SELECT + " WHERE products.id = ?"
    if active_only:
        query += " AND COALESCE(products.status_aktif, 1) = 1"
    return conn.execute(query, (normalized_id,)).fetchone()


def search_customers(conn, keyword, limit=SEARCH_MAX_LIMIT):
    normalized_keyword = str(keyword or "").strip()
    if len(normalized_keyword) < SEARCH_MIN_LENGTH:
        return []

    like_keyword = f"%{normalized_keyword}%"
    result_limit = normalize_search_limit(limit)
    return conn.execute(
        """
        SELECT *
        FROM customers
        WHERE COALESCE(status_aktif, 1) = 1
          AND (
                nama LIKE ? COLLATE NOCASE
             OR instansi LIKE ? COLLATE NOCASE
             OR whatsapp LIKE ? COLLATE NOCASE
             OR whatsapp_normalized LIKE ? COLLATE NOCASE
             OR email LIKE ? COLLATE NOCASE
             OR kota LIKE ? COLLATE NOCASE
             OR status LIKE ? COLLATE NOCASE
          )
        ORDER BY
            CASE WHEN nama LIKE ? COLLATE NOCASE THEN 0 ELSE 1 END,
            nama COLLATE NOCASE,
            id
        LIMIT ?
        """,
        (
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            f"{normalized_keyword}%",
            result_limit,
        ),
    ).fetchall()


def search_products(conn, keyword, limit=SEARCH_MAX_LIMIT):
    normalized_keyword = str(keyword or "").strip()
    if len(normalized_keyword) < SEARCH_MIN_LENGTH:
        return []

    like_keyword = f"%{normalized_keyword}%"
    result_limit = normalize_search_limit(limit)
    return conn.execute(
        PRODUCT_SELECT
        + """
        WHERE COALESCE(products.status_aktif, 1) = 1
          AND (
                products.kode_produk LIKE ? COLLATE NOCASE
             OR products.nama_produk LIKE ? COLLATE NOCASE
             OR product_categories.nama LIKE ? COLLATE NOCASE
             OR products.subkategori LIKE ? COLLATE NOCASE
             OR product_brands.nama LIKE ? COLLATE NOCASE
             OR product_variants.nama LIKE ? COLLATE NOCASE
             OR product_sizes.nama LIKE ? COLLATE NOCASE
             OR product_colors.nama LIKE ? COLLATE NOCASE
             OR products.jenis_produk LIKE ? COLLATE NOCASE
             OR products.steps LIKE ? COLLATE NOCASE
          )
        ORDER BY
            CASE
                WHEN products.kode_produk LIKE ? COLLATE NOCASE THEN 0
                WHEN products.nama_produk LIKE ? COLLATE NOCASE THEN 1
                ELSE 2
            END,
            products.nama_produk COLLATE NOCASE,
            products.id
        LIMIT ?
        """,
        (
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            like_keyword,
            f"{normalized_keyword}%",
            f"{normalized_keyword}%",
            result_limit,
        ),
    ).fetchall()


def customer_search_result(customer):
    whatsapp = (
        _value(customer, "whatsapp_normalized")
        or _value(customer, "whatsapp")
    )
    return {
        "id": int(_value(customer, "id")),
        "nama": _value(customer, "nama"),
        "perusahaan": _value(customer, "instansi"),
        "pic": _value(customer, "nama"),
        "whatsapp": whatsapp,
        "email": _value(customer, "email"),
        "alamat": _value(customer, "alamat"),
        "kota": _value(customer, "kota"),
        "status": _value(customer, "status"),
        "minat_produk": _value(customer, "produk"),
        "klasifikasi_produk": _value(
            customer,
            "klasifikasi_produk",
        ),
    }


SPECIFICATION_FIELDS = (
    ("Brand", "brand_nama", "brand_snapshot"),
    ("Kategori", "kategori_nama", "kategori_snapshot"),
    ("Subkategori", "subkategori", "subkategori_snapshot"),
    ("Jenis", "jenis_produk", "jenis_produk_snapshot"),
    ("Varian", "varian_nama", "varian_snapshot"),
    ("Warna", "warna_nama", "warna_snapshot"),
    ("Ukuran", "ukuran_nama", "ukuran_snapshot"),
    ("Steps", "steps", "steps_snapshot"),
)


def product_specification_lines(product, *, snapshot=False):
    lines = []
    for label, master_key, snapshot_key in SPECIFICATION_FIELDS:
        value = _value(
            product,
            snapshot_key if snapshot else master_key,
        )
        if value is not None and str(value).strip():
            lines.append({"label": label, "value": str(value).strip()})
    return lines


def product_search_result(product):
    lines = product_specification_lines(product)
    return {
        "id": int(_value(product, "id")),
        "kode_produk": _value(product, "kode_produk"),
        "nama_produk": _value(product, "nama_produk"),
        "kategori": _value(product, "kategori_nama"),
        "subkategori": _value(product, "subkategori"),
        "brand": _value(product, "brand_nama"),
        "varian": _value(product, "varian_nama"),
        "warna": _value(product, "warna_nama"),
        "ukuran": _value(product, "ukuran_nama"),
        "jenis_produk": _value(product, "jenis_produk"),
        "steps": _value(product, "steps"),
        "satuan": _value(product, "satuan") or "Unit",
        "harga_modal_default": int(
            _value(product, "harga_modal_default", 0) or 0
        ),
        "harga_jual_default": int(
            _value(product, "harga_jual_default", 0) or 0
        ),
        "spesifikasi": lines,
    }


def customer_snapshot(customer):
    result = customer_search_result(customer)
    return {
        "customer_nama_snapshot": result["nama"],
        "customer_perusahaan_snapshot": result["perusahaan"],
        "customer_pic_snapshot": result["pic"],
        "customer_whatsapp_snapshot": result["whatsapp"],
        "customer_email_snapshot": result["email"],
        "customer_alamat_snapshot": result["alamat"],
        "customer_kota_snapshot": result["kota"],
        "customer_status_snapshot": result["status"],
        "customer_minat_snapshot": result["minat_produk"],
    }


def product_snapshot(product):
    result = product_search_result(product)
    specification_text = "\n".join(
        f"{line['label']}: {line['value']}"
        for line in result["spesifikasi"]
    )
    return {
        "product_id": result["id"],
        "kode_produk": result["kode_produk"],
        "nama_produk": result["nama_produk"],
        "kategori": result["kategori"],
        "brand": result["brand"],
        "varian": result["varian"],
        "warna": result["warna"],
        "ukuran": result["ukuran"],
        "satuan": result["satuan"],
        "subkategori": result["subkategori"],
        "jenis_produk": result["jenis_produk"],
        "steps": result["steps"],
        "spesifikasi": specification_text or None,
        "harga_modal": result["harga_modal_default"],
        "harga_jual_default": result["harga_jual_default"],
        "spesifikasi_lines": result["spesifikasi"],
    }


def quotation_item_for_display(item):
    result = dict(item)
    result["spesifikasi_lines"] = product_specification_lines(
        item,
        snapshot=True,
    )
    return result
