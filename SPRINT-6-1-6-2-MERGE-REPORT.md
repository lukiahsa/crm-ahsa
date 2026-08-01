# Sprint 6.1 & 6.2 — Merge Report

## 1. Ringkasan merge

| Item | Hasil |
|---|---|
| Repository | `lukiahsa/crm-ahsa` |
| Pull Request | `#2 — Sprint 6.1 Mandatory PPN 11% for Denko Quotations` |
| Head | `agent/hotfix-sprint-6-denko-ppn` |
| Base | `main` |
| Metode | Merge commit; tidak squash dan tidak rebase |
| Expected head | `ba260b633dd3e3c18fe61654f9c9fa12c83cb575` |
| Merge commit SHA | `3357941e2da9223c492d9d350d847fa00e6a20e0` |
| Final application HEAD hasil merge | `3357941e2da9223c492d9d350d847fa00e6a20e0` |
| Status PR | Merged |

Merge commit mempunyai dua parent:

- `7a91260e30c4a692c86b06262e0912c413eb78f4` — baseline `main`;
- `ba260b633dd3e3c18fe61654f9c9fa12c83cb575` — final head Sprint 6.1/6.2.

File laporan ini ditambahkan setelah merge sebagai commit dokumentasi terpisah.
Karena sebuah commit tidak dapat mencantumkan SHA dirinya sendiri, SHA commit
dokumentasi dicatat pada handoff akhir dan histori GitHub.

## 2. Daftar commit yang dipertahankan

Seluruh delapan commit branch dipertahankan oleh merge commit:

1. `8669f19ccfe859c47d986f1dbfc18d79c993601b` — `feat(quotation): apply mandatory VAT to Denko quotations`
2. `513d2ad5d7bd7692f57b113d472ef3a6bc75546d` — `test(quotation): cover mandatory Denko VAT rules`
3. `228bbedfc8fefe1fa420cea0cc92e273e56d2070` — `docs(quotation): document Sprint 6.1 Denko VAT`
4. `3a528b1946e8d496f178b1e571cc6e7dab49853c` — `docs(quotation): record published hotfix history`
5. `4340243d183af66fbc0e125bf22a9fcb4af38307` — `fix(finance): preserve transaction header-detail invariants`
6. `f263a6651df5044f94288da59f64053decb2b62a` — `refactor(finance): centralize transaction calculations`
7. `5eeb93521bf96a3302c2b35232f7eb15aabaa1bf` — `test(finance): cover invariant engine edge cases`
8. `ba260b633dd3e3c18fe61654f9c9fa12c83cb575` — `fix(finance): preserve transaction financial invariants`

## 3. Validasi sebelum merge

Kondisi yang diverifikasi:

- PR open, mergeable, tanpa conflict, dan bukan draft;
- head lokal, origin branch, dan PR sama-sama menunjuk ke `ba260b633...`;
- working tree bersih;
- branch ahead 8 dan behind 0 terhadap `origin/main`.

Perintah:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q app tests
git diff --check
```

Hasil:

```text
Ran 20 tests in 1.516s
OK

compileall: OK
git diff --check: OK
```

## 4. Validasi setelah merge

Setelah merge, checkout disinkronkan dengan:

```bash
git switch main
git pull --ff-only origin main
```

`main` dan `origin/main` sama-sama menunjuk ke merge commit
`3357941e2da9223c492d9d350d847fa00e6a20e0` sebelum commit laporan.

Hasil regression pasca-merge:

```text
Ran 20 tests in 1.519s
OK

compileall: OK
git diff --check: OK
```

Test menggunakan database SQLite sementara milik test suite. Aplikasi tidak
dijalankan terhadap database production.

## 5. Bukti financial invariant

Financial engine membentuk header transaction hanya dari snapshot detail final.
Regression memverifikasi tanpa toleransi satu rupiah:

```text
SUM(sales_transaction_items.subtotal_penjualan)
    = sales_transactions.total_penjualan

SUM(sales_transaction_items.subtotal_modal)
    = sales_transactions.total_modal

SUM(sales_transaction_items.margin_item)
    = sales_transactions.margin
```

Kasus Rp6.000.000 dan Rp4.000.000 dengan diskon global Rp1.000.000:

| Item | Alokasi diskon | Subtotal akhir |
|---|---:|---:|
| A | Rp600.000 | Rp5.400.000 |
| B | Rp400.000 | Rp3.600.000 |
| Total | Rp1.000.000 | Rp9.000.000 |

Test juga mencakup diskon nol, diskon berlebih, sisa pembulatan, satu item,
banyak item, diskon item ditambah diskon global, add/edit Marketplace, serta
penolakan conversion Denko.

## 6. Status migration dan database production

- Migration Sprint 6.1 bersifat additive, idempotent, dan backward compatible.
- Sprint 6.2 tidak menambahkan atau mengubah schema.
- Migration belum dijalankan pada database production.
- Database production tidak dibuka, tidak ditulis, dan tidak di-backfill selama
  validasi maupun merge.
- Backfill transaction historis tidak termasuk merge ini.

Quotation legacy tetap dianggap tanpa PPN agar total historis tidak berubah.
Quotation Denko legacy menerapkan PPN terbaru saat diedit atau diduplikasi sesuai
aturan Sprint 6.1.

## 7. Risiko data historis

Transaction historis yang dibuat sebelum financial invariant engine mungkin
memiliki perbedaan antara header dan jumlah detail. Merge ini tidak mengubah data
tersebut secara otomatis.

Risiko utama:

- laporan historis dapat memakai header yang tidak sama dengan detail;
- margin Marketplace historis dapat memakai definisi setelah fee, sedangkan
  engine baru menyimpan margin kotor dan memakai `laba_bersih` untuk nilai akhir;
- backfill tanpa snapshot dan audit dapat mengubah angka historis secara salah;
- migration atau backfill langsung pada production tanpa backup dapat membuat
  pemulihan sulit.

Audit dan backfill harus menjadi pekerjaan terpisah dengan laporan dampak dan
approval Technical Lead.

## 8. Prosedur backup dan uji lokal berikutnya

1. Jadwalkan maintenance window dan hentikan sementara seluruh proses write.
2. Identifikasi file SQLite yang benar dari konfigurasi environment production;
   jangan berasumsi berdasarkan nama atau lokasi lokal.
3. Catat ukuran file, timestamp, ownership, dan checksum sebelum backup.
4. Buat backup konsisten menggunakan SQLite backup API atau perintah `.backup`,
   bukan menyalin file saat aplikasi masih menulis.
5. Simpan backup dengan timestamp pada storage terpisah dan buat salinan kedua
   yang tidak akan dimodifikasi.
6. Jalankan `PRAGMA integrity_check;` pada backup dan pastikan hasilnya `ok`.
7. Salin backup ke environment lokal/staging yang terisolasi.
8. Catat row count tabel utama dan total finansial sebelum migration.
9. Jalankan migration pada salinan tersebut dua kali untuk membuktikan
   idempotency.
10. Jalankan kembali `PRAGMA integrity_check;`, seluruh 20 regression test, dan
    perbandingan row count serta total historis.
11. Jalankan query audit invariant dalam mode read-only dan buat laporan dampak;
    jangan melakukan backfill pada tahap ini.
12. Lanjutkan deployment production hanya setelah backup dapat dipulihkan,
    hasil dry run disetujui, dan tersedia rencana rollback tertulis.

Contoh backup SQLite setelah path diverifikasi dan write dihentikan:

```bash
sqlite3 /path/verified/crm.db \
  ".backup '/path/backup/crm-ahsa-YYYYMMDD-HHMMSS.db'"
sqlite3 /path/backup/crm-ahsa-YYYYMMDD-HHMMSS.db \
  "PRAGMA integrity_check;"
```

Path di atas hanya contoh dan tidak boleh disalin langsung ke production tanpa
verifikasi konfigurasi sebenarnya.

## 9. Status akhir

- PR #2 telah di-merge menggunakan merge commit.
- Delapan commit Sprint 6.1/6.2 tetap terlihat dalam histori.
- Branch Sprint belum dihapus.
- Main telah lulus regression pasca-merge.
- Database production tidak disentuh.
- Migration dan audit/backfill production tetap pending.
