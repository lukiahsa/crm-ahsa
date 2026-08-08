# Deployment D1 — Mobile Daily Operation Readiness

## Baseline dan Scope

- Baseline: `28dc8f69e457c3ca2898e59a7f9e5438a22a574c`
- Baseline label: Merge Pull Request #12 Sprint 15 Transaction Workspace
- Branch: `agent/deploy-d1-mobile-optimization`
- Scope: responsive layout, mobile navigation, accessibility, core sales workflow, dan wrapper preview dokumen.
- Tidak ada perubahan schema, query Dashboard, perhitungan finansial, workflow, numbering, import logic, storage attachment, atau isi template print.
- Inventory dan Warehouse tetap tidak diaktifkan.

## Pendekatan Responsive

Satu stylesheet `app/static/css/mobile.css` dan satu script ringan `app/static/js/mobile.js` dimuat melalui partial Jinja pada halaman operasional. Tidak ada framework atau dependency frontend baru.

Breakpoint:

- Mobile: `<= 767px`
- Tablet: `768px–1023px`
- Desktop: `>= 1024px`

Perubahan utama:

- Navigation hamburger dengan menu aktif dan target sentuh 44 px.
- Optional menu hanya dirender ketika Module Manager mengaktifkan modulnya.
- Form menjadi satu kolom pada mobile; input WhatsApp memakai `tel`, email memakai `email`.
- Daftar desktop otomatis menjadi card berlabel pada mobile.
- Tabel item ber-input menjadi item card agar qty, harga, diskon, modal, subtotal, dan tombol hapus tetap mudah digunakan.
- KPI mobile memakai satu/dua kolom; chart dan activity tidak melewati viewport.
- Action Panel Transaction Workspace berpindah ke awal alur mobile dan memakai grid dua kolom.
- Dialog dibatasi tinggi/lebar viewport dan tetap dapat ditutup dari HP.
- Wrapper preview baru menempatkan tombol Kembali dan Cetak/Simpan PDF di luar template print yang dibekukan.

## Navigasi

Menu mobile inti:

- Dashboard
- Customer
- Product
- Quotation bila ON
- Transaction
- Invoice bila ON
- Receipt bila ON
- Delivery Order bila ON
- Purchase Order bila ON
- Settings

Tidak ada menu optional yang dirender saat modul OFF. Route optional tetap diblokir oleh policy existing.

## Halaman yang Diaudit dan Dioptimalkan

1. Dashboard
2. Daftar dan Tambah Customer
3. Edit Customer
4. Customer 360
5. Master dan Tambah Produk
6. Import Customer dan Product
7. Daftar, Form, Edit, dan Detail Quotation
8. Daftar, Form, Edit, dan Transaction Workspace
9. Form/Generate Invoice
10. Daftar, Form/Generate, dan Detail Receipt
11. Daftar, Form/Generate, dan Detail Delivery Order
12. Daftar, Form/Generate, dan Detail Purchase Order
13. Module Manager
14. Settings utama, Company Identity, dan Numbering
15. Preview Transaction, Quotation, Invoice, Receipt, Delivery Order, dan Purchase Order

## Wrapper Preview Dokumen

Route additive read-only:

`/document-preview/<document_type>/<document_id>`

Jenis yang diizinkan dibatasi oleh mapping internal: Transaction, Quotation, Invoice, Receipt, Delivery Order, dan Purchase Order. Wrapper tetap menghormati Module Manager. Iframe hanya memuat route print existing; tombol Cetak/Simpan PDF memanggil dialog print browser pada dokumen tersebut.

Tidak ada isi template print yang diubah. Purchase Order tetap membaca alamat identity Ahsa existing, yaitu Kp. Jati.

## File Berubah

Backend additive:

- `app/main.py`

Asset baru:

- `app/static/css/mobile.css`
- `app/static/js/mobile.js`
- `app/templates/_mobile_head.html`
- `app/templates/_mobile_navigation.html`
- `app/templates/document_preview.html`

Template operasional:

- Dashboard, Customer, Customer 360, Product
- Quotation list/form/edit/detail
- Transaction list/form/edit/workspace
- Invoice form
- Receipt list/form/detail
- Delivery Order list/form/detail
- Purchase Order list/form/detail/generate
- Module Manager dan Settings utama
- Import Customer dan Import Product

Test:

- `tests/test_deploy_d1_mobile_optimization.py`

## UAT Operasional

| Skenario | Hasil | Bukti utama |
|---|---:|---|
| A. Tambah Customer | Lulus | POST, redirect, dan customer tampil; sekitar 16 ms pada SQLite sementara |
| B. Buat Quotation | Lulus | Quotation tersimpan dan wrapper preview HTTP 200; sekitar 7 ms |
| C. Direct Transaction | Lulus | Transaction tersimpan dan Workspace HTTP 200; sekitar 32 ms |
| D. Generate Dokumen | Lulus | Invoice, Receipt, dan Delivery Order masing-masing tercatat satu; sekitar 12 ms gabungan |
| E. Upload Attachment | Lulus | PNG valid dari memori tersimpan dan muncul di Workspace; byte JPG palsu ditolak sesuai guard |
| F. Module Manager | Lulus | Invoice OFF menghilangkan action dan route generate mengembalikan HTTP 404 |
| G. Desktop Regression | Lulus | Dashboard dan Workspace stabil pada 1366×768 |

Seluruh data UAT dibuat pada SQLite sementara.

## UAT Viewport dan Overflow

| Viewport | Halaman | Horizontal overflow | Target tombol utama |
|---|---|---:|---:|
| 360×800 | Dashboard | Tidak | Lulus |
| 390×844 | Customer list/add, Workspace, Preview | Tidak | Lulus |
| 412×915 | Quotation dan Transaction form | Tidak | Lulus |
| 768×1024 | Dashboard tablet | Tidak | Lulus |
| 1366×768 | Dashboard dan Workspace desktop | Tidak | Desktop tetap stabil |

Pengukuran memakai Chromium headless aktual. Browser binary hanya dipakai sebagai alat UAT sementara dan tidak ditambahkan ke repository.

## Screenshot Evidence

Screenshot tidak di-commit. Artefak UAT berada di direktori workspace `uat-evidence-deploy-d1/`:

- `mobile-dashboard.png`
- `mobile-customer-list.png`
- `mobile-add-customer.png`
- `mobile-quotation-form.png`
- `mobile-transaction-form.png`
- `mobile-transaction-workspace.png`
- `mobile-document-preview.png`
- `tablet-dashboard.png`
- `desktop-dashboard.png`
- `desktop-transaction-workspace.png`

## Regression dan Integritas

- Baseline sebelum perubahan: 236 test lulus.
- Setelah D1: 248 test lulus.
- Test baru D1: 12.
- `python -m compileall -q app tests`: lulus.
- Seluruh template Jinja dapat dikompilasi.
- `node --check app/static/js/mobile.js`: lulus.
- `git diff --check`: bersih.
- SQLite sementara: `PRAGMA integrity_check = ok`.
- SQLite sementara: `PRAGMA foreign_key_check` kosong.
- Hash `app/database.py` tetap baseline; tidak ada migration baru.

## Document Freeze

SHA-256 tetap identik:

- Quotation: `dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622`
- Invoice: `7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8`
- Receipt: `4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4`
- Delivery Order: `ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5`
- Purchase Order: `139d248828adbaa55d6b8672a65d4ebc6d04d00a6e2c7cece305d81075ff6064`

## Performance Impact

- Tidak ada query baru berulang dan tidak ada perubahan query Dashboard.
- Tidak ada request API polling baru.
- Asset tambahan hanya satu CSS dan satu JS tanpa dependency runtime.
- Wrapper preview menambah satu request iframe hanya ketika user membuka preview.
- Tidak ada perubahan pada operasi database halaman existing.

## Risiko

- Konversi tabel ke card dilakukan setelah DOM siap. Pada perangkat yang mematikan JavaScript, tabel tetap dapat digunakan melalui style existing, tetapi pengalaman card tidak aktif.
- Chart.js Dashboard tetap memakai CDN existing. Dalam UAT jaringan terisolasi, chart tidak dimuat; definisi KPI dan layout tidak berubah. Lokalisasi asset Chart.js sengaja tidak dimasukkan agar D1 tidak menambah library besar.
- Selector CSS modern `:has()` digunakan hanya untuk penyempurnaan overflow; fungsi inti tetap bekerja bila selector tidak didukung.

## Rollback

Rollback dilakukan dengan `git revert` terhadap commit D1 dalam urutan terbalik. Tidak ada rollback database karena D1 tidak membuat migration atau data baru. Jika hanya wrapper preview yang perlu dilepas, revert commit preview/workspace dan kembalikan link ke route print existing.

## Ditunda ke Deployment Berikutnya

- PWA, service worker, offline cache, install prompt, dan push notification.
- VPS/deployment production.
- Native Web Share / native WhatsApp share.
- Bundling Chart.js lokal.
- Editing produk dari mobile.
- Perubahan Inventory, Warehouse, atau business workflow.
- Perubahan visual dokumen print A4/A5.
