# AHSA Transaction-Centric CRM Architecture (ATCA) v1.0

## Tujuan dan prinsip

ATCA menjadikan `sales_transactions` sebagai pusat proses bisnis. Transaksi langsung tetap sah tanpa Quotation, Invoice, Receipt, Delivery Order, Purchase Order, atau Inventory. Dokumen tersebut adalah child document opsional; bukan prasyarat transaksi.

Arsitektur ini additive dan backward compatible. Data lama, workflow integrity, financial invariant, revision engine, PPN Denko, multi identity, Customer360, Historical Purchase, import, nomor dokumen, dan template print tetap dipertahankan.

## Struktur module

Module Manager tersedia melalui **System → Module Manager** (`/settings/modules`).

Core module selalu aktif dan tidak dapat dimatikan:

- Customer
- Product
- Transaction
- Historical Purchase
- Dashboard
- Customer360

Optional module:

- Quotation
- Invoice
- Receipt
- Delivery Order
- Purchase Order
- Inventory
- Warehouse
- Accounting
- Purchasing

Semua optional module mempertahankan status aktif saat migrasi kecuali Inventory, yang mengikuti flag legacy `erp_settings.inventory_enabled` dan default-nya OFF. Perubahan dari Module Manager menyinkronkan flag Inventory lama tanpa mengubah engine Inventory.

Ketika optional module OFF:

- item navigasi dan tombol generate miliknya disembunyikan;
- route miliknya mengembalikan HTTP 404 sebelum workflow route dijalankan;
- Dashboard ATCA tidak membaca tabelnya;
- workflow dan posting miliknya tidak dijalankan.

## Transaction-first workflow

Alur inti adalah:

`Customer → Transaction → Selesai`

Transaction direct/manual tidak memerlukan `source_quotation_id`. Alur Quotation lama tetap valid untuk backward compatibility, dengan arah baru:

`Quotation → Deal/converted → Generate Transaction`

Child document dapat dibuat dari halaman detail Transaction:

- Invoice: `POST /transactions/<id>/invoice/generate`
- Receipt langsung: `GET|POST /transactions/<id>/receipt/generate`
- Delivery Order: `POST /transactions/<id>/delivery-order/generate`
- Purchase Order: `GET|POST /transactions/<id>/purchase-order/generate`

URL PO legacy `/transactions/<id>/invoice/purchase-order/generate` tetap tersedia. Contract lama yang menolak Invoice Batal juga tetap berlaku pada URL legacy, sedangkan URL ATCA baru bersumber dari Transaction dan tidak mensyaratkan Invoice.

## Receipt langsung

Receipt langsung disimpan pada tabel additive `transaction_receipts`. Tabel ini merupakan child Transaction dan sengaja terisolasi dari `payment_receipts`, sehingga tidak mengubah payment reconciliation, status pembayaran Invoice, atau template `receipt_print.html`.

Nomor tetap memakai format KWT yang sudah berlaku. Generator memeriksa nomor pada `payment_receipts` dan `transaction_receipts` agar tidak membuat collision lintas tabel. Receipt langsung ikut dianggap dependency saat edit/cancel/purge Transaction diperiksa.

## Inventory OFF

Engine Inventory tidak diubah. Caller workflow DO dan PO memeriksa `erp_settings.inventory_enabled` sebelum memanggil stock posting/reversal.

Ketika Inventory OFF:

- Transaction tetap dapat dibuat dan diselesaikan;
- DO/PO opsional tetap dapat menjalankan status dokumennya;
- tidak ada `stock_movements`, costing, atau stock card yang diposting.

Ketika Inventory ON, behavior engine dan posting lama tetap berlaku.

## Dashboard dan satu sumber finansial

Route `/dashboard` memakai read model ATCA dari `build_atca_dashboard`. Read model ini hanya mengeksekusi query ke core source:

- `customers`
- `products` dan `product_categories`
- `sales_transactions` dan `sales_transaction_items`
- `customer_purchase_history`

KPI utama tidak membaca Quotation, Invoice, Receipt, Delivery Order, Purchase Order, Inventory, atau `workflow_events`, sekalipun optional module tersebut ON.

Semua Revenue, Margin, Net Profit, Top Customer, Top Product, Repeat Customer, trend, kategori, dan rata-rata pembelian berasal dari service/read model yang sama.

### Revenue, margin, dan net profit

- Revenue = Transaction valid non-Batal/non-Cancelled + Historical Purchase aktif yang memiliki harga/total valid.
- Historical Purchase tanpa harga menambah qty, frekuensi, product analytics, dan repeat order tetapi tidak menambah Revenue.
- Margin dan Net Profit hanya berasal dari Transaction; Historical Purchase tidak mengarang margin atau costing.
- Filter sales tidak mengatribusikan Historical Purchase kepada sales tertentu.

### Customer dan product analytics

- Repeat Customer = lebih dari satu kejadian order gabungan Transaction valid + Historical Purchase aktif.
- Top Customer memakai jumlah order, revenue, margin Transaction, dan tanggal bisnis terakhir dari gabungan core source.
- Top Product memakai qty, jumlah order, customer, revenue, repeat, dan tanggal pembelian terakhir dari gabungan core source.
- Historical Purchase memakai `tanggal_pembelian`, tidak pernah `created_at`.

### Funnel ATCA

Funnel utama hanya:

`Customer → Transaction → Selesai`

Historical Purchase tidak masuk funnel. Quotation dan child document juga tidak masuk funnel KPI utama karena bersifat opsional.

### Recent Activity dan Owner Alert

Recent Activity dibentuk sebagai timeline core dari Transaction dan Historical Purchase. Owner Alert core menggunakan level `Info` untuk customer lama tidak order dan `Safe` jika tidak ada alert. Dashboard tidak membaca workflow event atau alert dokumen opsional.

## Migration

Migration dijalankan melalui `create_tables()` dan bersifat additive/idempotent:

- tabel `system_modules`;
- seed enam core module dan sembilan optional module;
- tabel `transaction_receipts`;
- index `idx_system_modules_type_order`;
- index `idx_transaction_receipts_transaction`.

Tidak ada drop table, perubahan tipe kolom lama, backfill transaksi, atau perubahan data production eksternal.

## Backward compatibility

- Data dan URL dokumen lama tetap valid.
- Dashboard BI legacy tetap tersedia pada `build_executive_dashboard(..., atca=False)` untuk regression service lama; route aplikasi menggunakan `atca=True`.
- Customer360 service dan Historical Purchase service tidak diubah.
- Workflow Integrity, Financial Invariant, Revision Engine, dan Inventory engine tidak diubah.
- Cancel Transaction tetap menjadi proses transaksi bisnis normal.
- Direct Receipt tidak mengubah rekonsiliasi Invoice.

## Test dan UAT

Automated coverage Sprint 14 berada di `tests/test_sprint_14_atca.py` dan mencakup:

- Transaction tanpa Quotation/Invoice/Receipt/DO/PO;
- Dashboard dan Customer360 tetap menghitung Transaction;
- Generate Invoice, Receipt, DO, dan PO dari Transaction;
- menu/tombol hidden dan route 404 ketika module OFF;
- core module tidak dapat dimatikan;
- Inventory OFF tidak membuat stock movement;
- Historical Purchase tetap terisolasi;
- query trace Dashboard tidak membaca optional table;
- migration dua kali, integrity check, foreign key check;
- hash empat template print tetap identik.

UAT wajib dipetakan ke automated scenario berikut:

1. Customer WA → Deal → Transaction → Dashboard naik: `test_transaction_first_requires_no_optional_document`.
2. Transaction → Generate Invoice: `test_generate_invoice_directly_from_transaction`.
3. Transaction → Generate DO: `test_generate_delivery_order_without_invoice`.
4. Transaction → Generate Receipt: `test_generate_receipt_without_invoice`.
5. Transaction → Generate PO: `test_generate_purchase_order_without_invoice`.
6. Inventory OFF → Transaction/DO → tidak ada stock movement: `test_inventory_off_posts_no_stock_movement`.

## Performance

Dashboard memakai fixed aggregate query plan; jumlah query tidak bertambah mengikuti jumlah row dan tidak melakukan N+1. Pengukuran dilakukan pada SQLite sementara memakai `time.perf_counter` dan trace callback SQLite.

Benchmark final menggunakan 2.000 Transaction, 2.000 item, 1.000 Historical Purchase, 250 customer, dan 25 produk; satu warm-up lalu 30 iterasi:

- query per render: 35 fixed queries;
- median: 22,492 ms;
- p95: 24,731 ms;
- maksimum: 27,284 ms;
- target maksimum: < 500 ms;
- optional table yang terbaca pada trace: tidak ada;
- `PRAGMA integrity_check`: `ok`;
- pelanggaran `PRAGMA foreign_key_check`: `0`.

## Risiko dan mitigasi

- **Tabel Receipt ganda:** dipisahkan sengaja untuk menjaga financial invariant. UI memberi label bahwa direct Receipt bukan rekonsiliasi Invoice.
- **Optional module dimatikan saat data lama ada:** data tidak dihapus; route/UI hanya dinonaktifkan dan dapat dipulihkan dengan mengaktifkan module.
- **Inventory flag legacy:** Module Manager menyinkronkan flag lama sehingga engine existing tetap menjadi sumber perilaku posting.
- **PO lama berasal dari Invoice:** URL dan hubungan lama dipertahankan; PO baru boleh memiliki `invoice_id = NULL` dan selalu menyimpan `transaction_id`.

## Rollback

Rollback aplikasi dilakukan dengan kembali ke commit baseline sebelum Sprint 14. Karena migration additive, tabel `system_modules` dan `transaction_receipts` boleh dibiarkan tanpa digunakan. Jangan drop tabel pada rollback UAT bila sudah berisi audit/data; eksport data direct Receipt terlebih dahulu jika rollback harus menghapus fitur.

Untuk rollback konfigurasi tanpa rollback code, aktifkan kembali optional module melalui Module Manager. Ini tidak mengubah atau membangkitkan data operasional.

## Document freeze

Hash SHA-256 yang wajib tetap identik:

- `quotation_print.html`: `aa1e06fadd7c92eda999ce6d203aba92341ca4df222e7fdcaf214d4fff927fd4`
- `invoice_print.html`: `4a79d98c15052b2229d52095050664a8b0b2709ae810e5e7b34b0fc74f6637e2`
- `receipt_print.html`: `7f77c0bbb71c24f14720fff3a42eaa66357be4fcdd3078e2478cb7c4556b21cc`
- `delivery_order_print.html`: `fcf647739fce72ac0a40166510502b4889922d13e966a433b55dbf92105b684a`
