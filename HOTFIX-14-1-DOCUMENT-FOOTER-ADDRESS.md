# Hotfix Sprint 14.1 — Non-PO Document Footer Address

## Baseline dan branch

- Baseline: `3555a005e8736a772b5083d29c24f7feb0c90bef` — Merge pull request #11 Sprint 14 ATCA v1.0.
- Branch: `agent/hotfix-14-1-document-footer-address`.
- Scope: alamat footer dokumen penjualan PT Ahsa, kecuali Purchase Order.
- Database production: tidak disentuh.

## Audit sumber alamat

Sebelum hotfix, keempat footer non-PO membaca `company_identities.alamat` melalui object `identity`. Purchase Order membaca sumber identity yang sama melalui object `company`.

Mengubah `company_profile`, `company_identities`, seed database, company settings, atau helper global akan ikut mengubah Purchase Order. Karena itu hotfix memakai perubahan paling sempit: alamat tiga baris ditulis hanya pada blok footer empat template penjualan.

Tidak ada perubahan pada:

- company identity/profile dan alamat legal global;
- database atau migration;
- alamat customer, pengiriman, gudang, atau supplier;
- Workflow Integrity, Financial Invariant, ATCA Module Manager, Dashboard, atau Customer360;
- nomor, logo, warna, ukuran halaman, tabel, QR, tanda tangan, rekening, WhatsApp, atau website.

Audit `transaction_print.html` menunjukkan dokumen tersebut hanya memiliki catatan internal dan tidak mempunyai footer alamat PT Ahsa, sehingga tidak perlu diubah.

## Alamat

Alamat lama yang tetap menjadi sumber global dan tetap digunakan Purchase Order:

```text
Kp. Jati, Desa Dangdeur,
Kecamatan Banyuresmi,
Garut, Jawa Barat
```

Alamat footer baru untuk dokumen non-PO:

```text
Kawasan Industri De Primaterra
Jl Raya Sapan, Blok E2, Tegalluar
Bojongsoang, Bandung
```

## Dokumen yang berubah

- `app/templates/quotation_print.html`
- `app/templates/invoice_print.html`
- `app/templates/receipt_print.html`
- `app/templates/delivery_order_print.html`

Tidak diubah:

- `app/templates/purchase_order_print.html`
- seluruh source/configuration alamat Purchase Order.

## Hash template sebelum dan sesudah

| Template | SHA-256 sebelum | SHA-256 sesudah |
|---|---|---|
| Quotation | `aa1e06fadd7c92eda999ce6d203aba92341ca4df222e7fdcaf214d4fff927fd4` | `dfbf494b864cfe360466c56e7cfe4a9856fbcab270f2c37f6b21bdbdca61d622` |
| Invoice | `4a79d98c15052b2229d52095050664a8b0b2709ae810e5e7b34b0fc74f6637e2` | `7d2dd66793137bacae464d9dd09036733f1d7785cb77fca4772ed6d775dc55e8` |
| Receipt | `7f77c0bbb71c24f14720fff3a42eaa66357be4fcdd3078e2478cb7c4556b21cc` | `4b29a0eaa4a59999d96778045b98e8165058c7859c790ad5c56b90aada2ba6a4` |
| Delivery Order | `fcf647739fce72ac0a40166510502b4889922d13e966a433b55dbf92105b684a` | `ac33d7a218eba2cc70f306004e1779781bdb64c6c6fa0960e55dbf82ac36f8b5` |
| Purchase Order | `139d248828adbaa55d6b8672a65d4ebc6d04d00a6e2c7cece305d81075ff6064` | `139d248828adbaa55d6b8672a65d4ebc6d04d00a6e2c7cece305d81075ff6064` |

Hash Purchase Order identik secara byte dengan baseline Sprint 14.

## Regression test

Test baru: `tests/test_hotfix_14_1_document_footer_address.py`.

Coverage:

1. Empat footer non-PO mengandung tepat tiga baris alamat baru.
2. Footer tersebut tidak lagi membaca `identity['alamat']` dan tidak memuat alamat lama.
3. Hash baru empat template dibekukan.
4. Purchase Order tidak mengandung alamat baru.
5. Hash Purchase Order tetap identik dengan Sprint 14.
6. PO tetap membaca `company["alamat"]` dan `company["kota"]`.
7. Identity Ahsa yang dibaca PO tetap berisi Kp. Jati, Dangdeur, Banyuresmi, Garut, Jawa Barat.
8. Nomor dokumen, logo, QR, rekening, WhatsApp, website, dan tanda tangan tetap tersedia.

Hasil validasi final:

- `python -m unittest discover -s tests -p "test_*.py" -v`: **227 tests, OK**.
- `python -m compileall -q app tests`: lulus.
- `git diff --check`: lulus.
- `PRAGMA integrity_check`: `ok`.
- `PRAGMA foreign_key_check`: `0` pelanggaran.

Semua database untuk test dan UAT dibuat di temporary directory.

## UAT visual

UAT memakai data sintetis pada SQLite sementara. Kelima route print Flask dirender menjadi PDF menggunakan WeasyPrint, lalu halaman pertama dirender menjadi PNG untuk inspeksi visual.

| Dokumen | HTTP | Alamat | Hasil visual |
|---|---:|---|---|
| Penawaran | 200 | Baru | Tiga baris terbaca; tidak terpotong; tidak menimpa ikon/batas footer. |
| Invoice | 200 | Baru | Tiga baris terbaca; tetap berada dalam panel biru. |
| Kwitansi | 200 | Baru | Tiga baris terbaca pada footer A5 landscape. |
| Surat Jalan | 200 | Baru | Tiga baris terbaca; tidak bertabrakan dengan QR atau area tanda tangan. |
| Purchase Order | 200 | Lama | Kp. Jati/Dangdeur/Banyuresmi/Garut tetap tampil; alamat baru tidak ada. |

Tidak diperlukan perubahan font-size, line-height, tinggi footer, atau layout utama.

Screenshot UAT:

- `quotation.png`
- `invoice.png`
- `receipt.png`
- `delivery_order.png`
- `purchase_order.png`

## Risiko

- Alamat footer non-PO sekarang sengaja terpisah dari alamat legal/global. Jika alamat operasional berubah lagi, empat blok footer harus diperbarui bersama dan hash test diperbarui secara eksplisit.
- Denko tidak terdampak: capability `show_footer` untuk identity `QUOTATION_ONLY` tetap menghilangkan footer sesuai aturan existing.
- PO tetap bergantung pada identity Ahsa lama sesuai pengecualian hotfix.

## Rollback

Rollback aman dilakukan dengan me-revert tiga commit hotfix dari urutan terakhir ke pertama, tanpa migration atau pemulihan database. Alternatif sempit adalah mengembalikan empat blok footer ke ekspresi `identity['alamat']` dan memulihkan hash test Sprint 14.

Purchase Order tidak membutuhkan rollback karena tidak pernah berubah.
