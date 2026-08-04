# Sprint 13.5 — Executive Dashboard Data Reconciliation & Safe Test Transaction Purge

## Scope dan baseline

- Baseline: `main` setelah merge Sprint 13, commit `c690e04eaeb31f068be6937426444b7e33efec78`.
- Branch: `agent/sprint-13-5-dashboard-reconciliation`.
- Dashboard tetap read-only. Historical Purchase tetap terisolasi dari workflow operasional.
- Workflow Integrity, Financial Invariant, Revision Engine, Mandatory PPN Denko, Multi Identity, stock posting, payment reconciliation, dan format dokumen tidak diubah.

## Definisi KPI dan tanggal bisnis

| Data/KPI | Sumber | Tanggal bisnis | Aturan |
|---|---|---|---|
| Omzet | Transaction + Historical Purchase berharga | `sales_transactions.tanggal` / `customer_purchase_history.tanggal_pembelian` | Transaction batal/cancelled dan histori nonaktif dikecualikan. |
| Margin/Laba Bersih | Transaction resmi | `sales_transactions.tanggal` | Historical Purchase tidak diberi margin atau laba buatan. |
| Customer Baru Periode Ini | Customer | `customers.created_at` | Tanggal awal dan akhir inklusif. |
| Repeat Customer | Order gabungan pada periode | Tanggal transaksi/pembelian | Lebih dari satu transaction valid + historical aktif dengan qty valid. Histori tanpa harga tetap order. |
| Top Customer | Order gabungan | Tanggal transaksi/pembelian | Omzet histori hanya bila harga/total valid; frekuensi selalu dihitung. |
| Top Product/kategori | Item transaction + historical | Tanggal transaksi/pembelian | Qty/frekuensi selalu dihitung; omzet histori hanya bila berharga; margin hanya transaction. |
| Piutang | Invoice + receipt non-Void | Tanggal transaction untuk KPI terpilih | Historical Purchase tidak pernah menambah piutang. |
| Funnel | Customer/quotation/transaction/document resmi | Tanggal bisnis masing-masing | Historical Purchase selalu dikecualikan. |

Nilai Historical Purchase memakai `total` valid. Untuk data legacy dengan `total` kosong tetapi `harga_satuan` valid, nilai dihitung deterministik sebagai `harga_satuan × qty`. Tanpa keduanya, omzet nol.

Filter Sales mengecualikan Historical Purchase karena record historis tidak memiliki sales owner. Filter customer, produk, dan kategori diterapkan pada sumber transaction dan historical secara konsisten.

## Customer analytics

- Customer Database: seluruh customer dengan `status_aktif = 1`.
- Existing Customer: customer aktif dengan status resmi `Existing Customer`/`Existing`.
- Prospek: customer aktif berstatus `Prospek`.
- Customer Tidak Aktif: `status_aktif = 0`.
- Lama Tidak Order: customer aktif yang pernah order tetapi order terakhir lebih dari 90 hari.
- Belum Pernah Order: customer aktif tanpa transaction valid dan tanpa Historical Purchase aktif.
- Klasifikasi omzet mengikuti Customer 360: Platinum > Rp200 juta, Gold > Rp50 juta, Silver > Rp10 juta, dan Bronze untuk sisanya.

## Sales funnel

1. Prospek: prospek aktif yang dibuat pada periode.
2. Quotation: quotation pada periode.
3. Deal: quotation berstatus Deal/Converted.
4. Transaction: transaction non-Batal/non-Cancelled.
5. Invoice: invoice non-Batal/non-Cancelled berdasarkan `tanggal_invoice`.
6. Receipt: receipt non-Void berdasarkan tanggal receipt.
7. Delivery: DO non-Batal/non-Cancelled berdasarkan tanggal DO.
8. Completed: transaction berstatus Selesai/Completed.

## Safe test transaction purge

### Schema dan migration

- `sales_transactions.is_test INTEGER NOT NULL DEFAULT 0`
- `sales_transactions.test_label TEXT NULL`
- `test_transaction_purge_audit` untuk snapshot permanen sebelum hard delete.

Migration additive, idempotent, backward compatible, dan tidak menandai transaction lama sebagai test. Conversion quotation tidak mengisi marker sehingga tetap default `is_test = 0`.

### Eligibility

Purge hanya diizinkan untuk `is_test = 1` dan ditolak bila ditemukan salah satu dari:

- invoice dalam status apa pun;
- receipt;
- delivery order;
- purchase order, langsung maupun melalui invoice;
- stock movement/posting;
- workflow event;
- quotation revision sebagai transaction lama/baru;
- link conversion/source quotation.

Transaction lama hanya dapat ditandai satu per satu, dengan alasan wajib, setelah pemeriksaan dependency yang sama. Transaction non-test tidak pernah menampilkan hard-delete. Cancel Transaction Sprint 12 tetap menjadi mekanisme transaksi bisnis.

### Atomic delete dan audit

Service menjalankan `BEGIN IMMEDIATE`, memeriksa ulang eligibility, memvalidasi alasan dan nomor transaction persis, lalu:

1. menyimpan snapshot header, customer, financial, status, item, actor, reason, dan dependency summary ke audit;
2. menghapus hanya `sales_transaction_items` milik transaction;
3. menghapus satu header transaction test;
4. commit atomik.

Setiap exception melakukan rollback penuh. Customer, product, Historical Purchase, transaction lain, dan workflow event tidak disentuh. Audit tidak memiliki FK ke transaction sehingga tetap ada setelah header dihapus.

## UAT

| Scenario | Hasil |
|---|---|
| A — Ibu Geugeu, 12 unit, Juli, total Rp1.440.000 | Omzet Juli Rp1.440.000; omzet tahun Rp1.560.000 termasuk transaction resmi; Top Customer Ibu Geugeu; Top Product Tempat Sampah 120 Liter Roda Hijau (12 unit); invoice/DO/PO/stock movement = 0. |
| B — Historical tanpa harga | Omzet Rp0; qty 3; frekuensi order 1. |
| C — Purge test tanpa downstream | Omzet Agustus turun Rp1.000 → Rp0; transaction hilang; audit tersisa 1. |
| D — Purge dengan invoice | Ditolak dengan alasan invoice; transaction tetap ada; tidak ada data berubah. |

## Test, performance, dan integrity

- Existing baseline: 163 test.
- Sprint 13.5: 47 test baru.
- Total: 210 test, seluruhnya lulus.
- Dashboard query plan: 58 SELECT/CTE, tanpa N+1.
- Benchmark 100 render pada SQLite UAT sintetis: median 0,609 ms; p95 0,870 ms; maksimum 1,158 ms.
- `PRAGMA integrity_check`: `ok`.
- `PRAGMA foreign_key_check`: 0 row.
- `python -m compileall -q app tests`: lulus.
- `git diff --check`: lulus.

## Risiko dan mitigasi

- Historical Purchase tidak memiliki sales owner: filter Sales mengecualikannya untuk mencegah atribusi palsu.
- Historical Purchase tidak memiliki modal/margin: hanya omzet, qty, dan frekuensi yang digabung.
- Hard delete berisiko menghilangkan konteks: marker eksplisit, pemeriksaan ketat, konfirmasi nomor, alasan wajib, snapshot audit, dan transaksi atomik membatasi risiko.
- Data dependency legacy yang tidak dikenal akan memicu kegagalan FK saat delete dan seluruh operasi rollback.

## Rollback

1. Hentikan penggunaan tombol mark/purge.
2. Revert commit Sprint 13.5 secara terbalik tanpa mengubah data production secara manual.
3. Kolom/tabel additive dapat dibiarkan untuk backward compatibility; tidak perlu destructive migration.
4. Audit purge yang sudah tercatat tidak dihapus.
5. Transaction yang sudah dipurge tidak direkonstruksi otomatis; gunakan `payload_json` audit untuk pemeriksaan/manual recovery terkontrol.

## Document freeze

| Template | SHA-256 |
|---|---|
| `quotation_print.html` | `aa1e06fadd7c92eda999ce6d203aba92341ca4df222e7fdcaf214d4fff927fd4` |
| `invoice_print.html` | `4a79d98c15052b2229d52095050664a8b0b2709ae810e5e7b34b0fc74f6637e2` |
| `receipt_print.html` | `7f77c0bbb71c24f14720fff3a42eaa66357be4fcdd3078e2478cb7c4556b21cc` |
| `delivery_order_print.html` | `fcf647739fce72ac0a40166510502b4889922d13e966a433b55dbf92105b684a` |

Seluruh hash identik dengan baseline Sprint 13.
