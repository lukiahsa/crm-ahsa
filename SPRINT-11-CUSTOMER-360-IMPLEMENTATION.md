# Sprint 11 — Customer 360° Implementation

## Baseline dan scope

- Baseline: `4d9af121b7ef5f3306321defbe2f7ec750c0e95f` (merge PR #6 Sprint 10B).
- Branch: `agent/sprint-11-customer-360`.
- Customer 360 adalah read-model agregasi dan dua write-model terisolasi: catatan customer serta histori pembelian lama.
- Workflow Integrity, Financial Invariant Engine, Multi Identity, PPN Denko, stock posting, payment reconciliation, downstream guard, dan format dokumen tidak diubah.

## Arsitektur

`app/customer_360.py` menjadi service bisnis untuk:

- klasifikasi customer;
- KPI dan agregasi produk;
- histori transaction dan historical purchase;
- quotation, invoice, receipt, delivery order, dan PO terkait;
- timeline dari event/data existing;
- notes dan historical purchase;
- insight ringkas API search.

`app/main.py` hanya menangani request, validasi customer, transaksi database atomik, redirect, dan rendering. `app/templates/customer_detail.html` menampilkan seluruh read-model tanpa mengubah desain global.

## Schema dan migration

Migration berada di `create_tables()` dan bersifat additive, idempotent, serta backward compatible.

### `customer_notes`

Kolom: `id`, `customer_id`, `note_text`, `note_type`, `created_at`, `updated_at`, `created_by`, `active`. Catatan dinonaktifkan dengan soft delete (`active=0`), tidak pernah hard-delete.

### `customer_purchase_history`

Kolom: `id`, `customer_id`, `product_id` nullable, seluruh snapshot produk, `tanggal_pembelian`, `qty`, `harga_satuan`, `total`, `source`, `notes`, timestamps, dan `active`.

Constraint penting:

- `qty > 0`;
- nominal integer dan non-negatif;
- FK customer cascade, FK product set-null;
- tidak ada FK/hook menuju transaction, invoice, receipt, DO, PO, stock, atau workflow.

Kolom additive `customers.pic` ditambahkan agar profil dapat dilengkapi bertahap.

Index ditambahkan pada customer/date/product notes dan purchase history, customer pada transaction/quotation, invoice transaction, receipt invoice/status, serta workflow event customer/date.

Tidak ada backfill otomatis.

## Query agregasi dan performance

Customer 360 menggunakan query agregasi terpisah dan query daftar terbatasi per jenis dokumen. Tidak ada loop query per item. Timeline menggunakan query per sumber dan pemetaan nomor dokumen secara batch per jenis dokumen, sehingga jumlah query tetap terhadap pertumbuhan event.

API search tetap memakai pencarian existing (minimal dua karakter, maksimum 20, customer aktif), lalu satu query insight untuk seluruh ID hasil. Tidak ada query per hasil customer.

## Definisi KPI

- Total omzet: jumlah `sales_transactions.total_penjualan` non-Batal ditambah historical purchase aktif yang memiliki harga/total.
- Total margin: jumlah `sales_transactions.margin` non-Batal; histori lama tidak mengarang margin.
- Repeat order: jumlah transaksi non-Batal ditambah historical purchase berharga. Repeat customer jika hasil lebih dari satu.
- Invoice outstanding: invoice aktif berstatus `Belum Lunas` atau `DP`.
- Total piutang: `MAX(total_penjualan - potongan - receipt non-Void, 0)` untuk invoice aktif outstanding.
- Order pertama/terakhir: tanggal minimum/maksimum transaksi valid dan historical purchase berharga.
- Produk/unit: agregasi item transaksi valid dan historical purchase aktif.
- Produk favorit: maksimum 10, urutan total qty, omzet, lalu pembelian terakhir.

Semua nominal dihitung dengan integer Rupiah.

## Historical purchase design

Produk master boleh dipilih. Jika dipilih, snapshot kode/nama/kategori/varian/warna/ukuran/satuan disalin. Tanpa master, nama snapshot wajib diisi. Harga opsional; jika ada, `total = qty × harga_satuan`.

Write hanya menuju `customer_purchase_history`. Regression test membuktikan tidak membuat invoice, DO, PO, stock movement, atau workflow event. Histori tanpa harga tersimpan tetapi tidak menambah omzet/repeat; histori berharga menambah omzet/repeat.

## Timeline design

Timeline menggabungkan:

- `workflow_events`;
- `sales_quotation_activities`;
- `delivery_order_activities`;
- `payment_receipt_activities`;
- record creation transaction, invoice, dan PO terkait yang benar-benar tersimpan.

Hasil diurutkan terbaru ke terlama dan memuat waktu, tipe, ID/nomor dokumen, deskripsi, serta link detail bila route tersedia. Tidak ada event sintetis tanpa record sumber.

## Customer classification

Threshold terpusat pada `CLASSIFICATION_THRESHOLDS`:

- New: belum memiliki transaksi valid atau historical purchase aktif;
- Bronze: sampai Rp10.000.000;
- Silver: di atas Rp10.000.000 sampai Rp50.000.000;
- Gold: di atas Rp50.000.000 sampai Rp200.000.000;
- Platinum: di atas Rp200.000.000.

Klasifikasi dihitung saat dibaca dan tidak mengubah status customer.

## Route

- `GET /customers/<customer_id>`
- `POST /customers/<customer_id>/purchase-history`
- `POST /customers/<customer_id>/notes`
- `POST /customers/<customer_id>/notes/<note_id>/edit`
- `POST /customers/<customer_id>/notes/<note_id>/deactivate`
- `GET /api/customers/search` diperkaya secara backward compatible.

## File berubah

- `app/database.py`
- `app/customer_360.py`
- `app/main.py`
- `app/templates/customer_detail.html`
- `app/templates/customers.html`
- `app/templates/edit_customer.html`
- `tests/test_customer_360.py`
- `SPRINT-11-CUSTOMER-360-IMPLEMENTATION.md`

## Test dan UAT

- 35 test Sprint 11 mencakup daftar wajib, termasuk failure path, isolation, API compatibility, migration, integrity, dan template freeze.
- 99 test existing tetap dijalankan untuk Financial Invariant Engine, Workflow Integrity, Multi Identity, quotation master, import product, dan import customer.
- UAT memakai SQLite sementara untuk customer minimal/edit profil, historical purchase, transaction KPI, piutang DP, dan timeline.
- `PRAGMA integrity_check = ok`; `PRAGMA foreign_key_check` kosong.

## Risiko dan mitigasi

- Data legacy mungkin memiliki status/ejaan tanggal yang tidak seragam: query mempertahankan data dan tidak melakukan backfill produksi.
- Snapshot produk manual bergantung pada input user: nama dan qty divalidasi; pilihan master disarankan.
- Timeline dapat berisi dua jejak untuk satu aksi bila activity dan workflow event memang keduanya tersimpan; keduanya adalah audit record nyata dan tidak dideduplikasi secara spekulatif.

## Rollback

Rollback kode dilakukan dengan revert commit Sprint 11. Tabel/kolom additive dapat dibiarkan tanpa memengaruhi workflow lama. Jika penghapusan schema benar-benar diperlukan, lakukan hanya melalui migration terencana pada salinan/maintenance database—bukan otomatis oleh rollback aplikasi.

## Bukti document format freeze

SHA-256 baseline dan hasil akhir wajib identik:

- `quotation_print.html`: `aa1e06fadd7c92eda999ce6d203aba92341ca4df222e7fdcaf214d4fff927fd4`
- `invoice_print.html`: `4a79d98c15052b2229d52095050664a8b0b2709ae810e5e7b34b0fc74f6637e2`
- `receipt_print.html`: `7f77c0bbb71c24f14720fff3a42eaa66357be4fcdd3078e2478cb7c4556b21cc`
- `delivery_order_print.html`: `fcf647739fce72ac0a40166510502b4889922d13e966a433b55dbf92105b684a`

`git diff` untuk keempat template kosong.
