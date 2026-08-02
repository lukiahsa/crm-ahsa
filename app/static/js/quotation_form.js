(function () {
    "use strict";

    const form = document.querySelector("[data-quotation-form]");
    if (!form) return;

    const customerEndpoint = form.dataset.customerSearchUrl;
    const productEndpoint = form.dataset.productSearchUrl;
    const rows = document.getElementById("rows");
    const template = document.getElementById("rowTemplate");
    const globalDiscount = document.getElementById("globalDiscount");
    const identityRadios = document.querySelectorAll(
        'input[name="identity_id"]'
    );
    const taxNotice = document.getElementById("taxNotice");
    const dppRow = document.getElementById("dppRow");
    const ppnRow = document.getElementById("ppnRow");

    function readJson(id, fallback) {
        const element = document.getElementById(id);
        if (!element) return fallback;
        try {
            return JSON.parse(element.textContent);
        } catch (error) {
            return fallback;
        }
    }

    function debounce(callback, delay) {
        let timer;
        return function (...args) {
            clearTimeout(timer);
            timer = setTimeout(() => callback.apply(this, args), delay);
        };
    }

    function escapeHtml(value) {
        const node = document.createElement("span");
        node.textContent = value == null ? "" : String(value);
        return node.innerHTML;
    }

    function moneyValue(value) {
        const normalized = String(value == null ? "0" : value)
            .replace(/[^0-9-]/g, "");
        const parsed = Number(normalized);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function rupiah(value) {
        return "Rp " + Math.max(Number(value) || 0, 0).toLocaleString("id-ID");
    }

    async function search(endpoint, keyword) {
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set("keyword", keyword);
        url.searchParams.set("limit", "20");
        const response = await fetch(url, {
            headers: {Accept: "application/json"},
        });
        if (!response.ok) throw new Error("Pencarian gagal.");
        return (await response.json()).results || [];
    }

    function isDenkoTax() {
        const selected = document.querySelector(
            'input[name="identity_id"]:checked'
        );
        return Boolean(
            selected && selected.dataset.identityType === "QUOTATION_ONLY"
        );
    }

    function calculatePreview() {
        let subtotal = 0;
        rows.querySelectorAll("tr").forEach((row) => {
            const qty = moneyValue(row.querySelector(".qty").value);
            const price = moneyValue(row.querySelector(".price").value);
            const discount = moneyValue(
                row.querySelector(".discount").value
            );
            const total = Math.max(qty * price - discount, 0);
            row.querySelector(".rowTotal").textContent = rupiah(total);
            subtotal += total;
        });

        const dpp = Math.max(
            subtotal - moneyValue(globalDiscount.value),
            0
        );
        const withPpn = isDenkoTax();
        const ppn = withPpn ? Math.floor((dpp * 11 + 50) / 100) : 0;
        document.getElementById("subtotal").textContent = rupiah(subtotal);
        document.getElementById("dpp").textContent = rupiah(dpp);
        document.getElementById("ppn").textContent = rupiah(ppn);
        document.getElementById("grand").textContent = rupiah(dpp + ppn);
        dppRow.hidden = !withPpn;
        ppnRow.hidden = !withPpn;
        taxNotice.classList.toggle("ppn", withPpn);
        taxNotice.textContent = withPpn
            ? "PPN 11% diterapkan otomatis dan tidak dapat dinonaktifkan."
            : "Tanpa PPN.";
    }

    function customerTitle(customer) {
        return [customer.nama, customer.perusahaan]
            .filter(Boolean)
            .join(" — ");
    }

    function customerDetails(customer) {
        return [
            customer.whatsapp && `WA ${customer.whatsapp}`,
            customer.email,
            customer.kota,
            customer.status,
            customer.minat_produk && `Minat: ${customer.minat_produk}`,
        ].filter(Boolean);
    }

    function setupCustomerAutocomplete() {
        const searchInput = document.getElementById("customerSearch");
        const hiddenInput = document.getElementById("customerId");
        const resultBox = document.getElementById("customerResults");
        const selectedBox = document.getElementById("customerSelection");

        function selectCustomer(customer) {
            hiddenInput.value = customer.id;
            searchInput.value = customerTitle(customer);
            selectedBox.hidden = false;
            selectedBox.innerHTML = `<strong>${escapeHtml(customer.nama)}</strong>
                <span>${customerDetails(customer).map(escapeHtml).join(" · ") || "Data customer terpilih"}</span>`;
            resultBox.hidden = true;
            resultBox.innerHTML = "";
        }

        function renderResults(results) {
            resultBox.innerHTML = "";
            if (!results.length) {
                resultBox.innerHTML = '<div class="empty-result">Customer tidak ditemukan.</div>';
            } else {
                results.forEach((customer) => {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "search-result";
                    button.innerHTML = `<strong>${escapeHtml(customerTitle(customer))}</strong>
                        <span>${customerDetails(customer).map(escapeHtml).join(" · ") || "-"}</span>`;
                    button.addEventListener("click", () => selectCustomer(customer));
                    resultBox.appendChild(button);
                });
            }
            resultBox.hidden = false;
        }

        const runSearch = debounce(async () => {
            const keyword = searchInput.value.trim();
            if (keyword.length < 2) {
                resultBox.hidden = true;
                return;
            }
            try {
                renderResults(await search(customerEndpoint, keyword));
            } catch (error) {
                resultBox.innerHTML = '<div class="empty-result">Pencarian customer gagal.</div>';
                resultBox.hidden = false;
            }
        }, 250);

        searchInput.addEventListener("input", () => {
            hiddenInput.value = "";
            selectedBox.hidden = true;
            runSearch();
        });

        const initialCustomer = readJson("selectedCustomerData", null);
        if (initialCustomer && initialCustomer.id) {
            selectCustomer(initialCustomer);
        }
    }

    function productTitle(product) {
        return `${product.kode_produk || "-"} — ${product.nama_produk || "Produk"}`;
    }

    function specificationText(product) {
        return (product.spesifikasi || [])
            .map((line) => `${line.label}: ${line.value}`)
            .join(" · ");
    }

    function bindProductRow(row, initialData) {
        const idInput = row.querySelector(".product-id");
        const searchInput = row.querySelector(".product-search");
        const resultBox = row.querySelector(".product-results");
        const selectedBox = row.querySelector(".product-selected");
        const priceInput = row.querySelector(".price");

        function selectProduct(product, preservePrice) {
            idInput.value = product.id || product.product_id;
            searchInput.value = productTitle(product);
            selectedBox.hidden = false;
            selectedBox.innerHTML = `<strong>${escapeHtml(product.nama_produk)}</strong>
                <span>${escapeHtml(specificationText(product) || product.satuan || "Unit")}</span>`;
            resultBox.hidden = true;
            resultBox.innerHTML = "";
            if (!preservePrice) {
                priceInput.value = product.harga_jual_default || 0;
            }
            calculatePreview();
        }

        function renderResults(results) {
            resultBox.innerHTML = "";
            if (!results.length) {
                resultBox.innerHTML = '<div class="empty-result">Produk tidak ditemukan.</div>';
            } else {
                results.forEach((product) => {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "search-result";
                    button.innerHTML = `<strong>${escapeHtml(productTitle(product))}</strong>
                        <span>${escapeHtml(specificationText(product) || product.satuan || "Unit")}</span>`;
                    button.addEventListener("click", () => selectProduct(product, false));
                    resultBox.appendChild(button);
                });
            }
            resultBox.hidden = false;
        }

        const runSearch = debounce(async () => {
            const keyword = searchInput.value.trim();
            if (keyword.length < 2) {
                resultBox.hidden = true;
                return;
            }
            try {
                renderResults(await search(productEndpoint, keyword));
            } catch (error) {
                resultBox.innerHTML = '<div class="empty-result">Pencarian produk gagal.</div>';
                resultBox.hidden = false;
            }
        }, 250);

        searchInput.addEventListener("input", () => {
            idInput.value = "";
            selectedBox.hidden = true;
            runSearch();
        });
        row.querySelectorAll("input.qty,input.price,input.discount").forEach(
            (input) => input.addEventListener("input", calculatePreview)
        );
        row.querySelector(".remove").addEventListener("click", () => {
            if (rows.children.length === 1) {
                window.alert("Minimal satu produk.");
                return;
            }
            row.remove();
            calculatePreview();
        });

        if (initialData && initialData.product_id) {
            selectProduct(initialData, true);
        }
    }

    function addRow(data) {
        const fragment = template.content.cloneNode(true);
        const row = fragment.querySelector("tr");
        rows.appendChild(fragment);
        row.querySelector(".qty").value = data && data.qty ? data.qty : 1;
        row.querySelector(".price").value =
            data && data.harga_satuan != null ? data.harga_satuan : 0;
        row.querySelector(".discount").value =
            data && data.diskon_item != null ? data.diskon_item : 0;
        bindProductRow(row, data || null);
        calculatePreview();
    }

    setupCustomerAutocomplete();
    document.getElementById("addRow").addEventListener("click", () => addRow());
    globalDiscount.addEventListener("input", calculatePreview);
    identityRadios.forEach((radio) =>
        radio.addEventListener("change", calculatePreview)
    );

    const initialItems = readJson("initialQuotationItems", []);
    if (initialItems.length) {
        initialItems.forEach(addRow);
    } else {
        addRow();
    }

    const presets = {
        tempat_sampah: {
            catatan: "Produk tempat sampah sesuai spesifikasi yang tercantum pada penawaran.",
            terms: "Harga belum termasuk ongkos kirim apabila tidak disebutkan.\nWarna dan ketersediaan stok mengikuti konfirmasi terakhir.\nLead time mengikuti jumlah pesanan dan ketersediaan barang.\nGaransi mengikuti ketentuan pabrikan.",
        },
        tangga: {
            catatan: "Produk tangga sesuai merk, ukuran, steps dan varian yang tercantum.",
            terms: "Harga belum termasuk ongkos kirim apabila tidak disebutkan.\nSpesifikasi tinggi dan jumlah steps mengikuti produk yang dipilih.\nGaransi mengikuti ketentuan pabrikan.\nPenawaran berlaku sampai tanggal yang tercantum.",
        },
        project: {
            catatan: "Penawaran untuk kebutuhan project sesuai jumlah dan spesifikasi yang disepakati.",
            terms: "Harga dan jadwal pengiriman mengikuti kesepakatan project.\nPembayaran sesuai termin yang disepakati.\nPerubahan jumlah atau spesifikasi dapat memengaruhi harga.\nPenawaran berlaku sampai tanggal yang tercantum.",
        },
    };
    document.querySelectorAll("[data-preset]").forEach((button) => {
        button.addEventListener("click", () => {
            const preset = presets[button.dataset.preset];
            document.getElementById("catatan").value = preset.catatan;
            document.getElementById("terms").value = preset.terms;
        });
    });
})();
