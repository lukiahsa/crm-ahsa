# Sprint 8 — Merge Report

## 1. Ringkasan Merge

| Item | Hasil |
|---|---|
| Repository | `lukiahsa/crm-ahsa` |
| Pull Request | `#4 — Sprint 8 Import Master Database Customer Existing` |
| Base | `main` |
| Head | `agent/sprint-8-import-master-customer` |
| Metode | Merge commit; tidak squash dan tidak rebase |
| Head yang divalidasi | `f9b7057dc85ff2056f018eb98339ad495b1addd6` |
| Merge commit SHA | `5e7052e2a026b5f194e470dca236c646734a5819` |
| Application HEAD setelah merge | `5e7052e2a026b5f194e470dca236c646734a5819` |
| Status PR | Merged |

Merge dilakukan setelah PR dinyatakan mergeable, tanpa conflict, dan final
validation lulus. PR yang masih berstatus draft dipindahkan menjadi ready for
review sesuai approval Technical Lead, kemudian di-merge menggunakan merge
commit.

## 2. Commit Sprint 8 yang Dipertahankan

1. `7b37c2207803ff44018303f57a082298ea0231c1` — `feat(customer): add atomic master customer import engine`
2. `c959c23f7821532f586b83913190fdc86e1e6217` — `feat(customer): add customer import preview workflow`
3. `f9b7057dc85ff2056f018eb98339ad495b1addd6` — `test(customer): cover customer import regression and documentation`

Seluruh commit tetap terlihat dalam histori karena merge tidak menggunakan
squash atau rebase.

## 3. Final Validation Sebelum Merge

Perintah:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q app tests
git diff --check
```

Hasil:

```text
Ran 38 tests in 2.873s
OK

compileall: OK
git diff --check: OK
working tree: clean
```

Test membuktikan:

- migration additive dan idempotent;
- route `/customers/import` aktif;
- preview tidak menulis customer ke database;
- import memakai transaksi atomik dan rollback pada error fatal;
- file identik tidak membuat duplicate pada import kedua;
- deduplikasi lintas sheet bekerja;
- Existing Customer mengalahkan Prospek;
- merge tidak menimpa informasi existing dengan nilai kosong;
- audit batch dan perubahan per customer tercatat.

## 4. UAT Import File Resmi

UAT memakai `MASTER DATABASE FINAL.xlsx` dengan SHA-256:

```text
7320fd752952a3044f917cf6fa822a0ce1786e3b856cde5c3ce331fae7942e26
```

UAT dijalankan pada database SQLite sementara, bukan database production.

| Metrik | Hasil |
|---|---:|
| Baris sumber | 4.958 |
| Baris error yang tidak diimport | 2 |
| Customer unik diimport | 4.608 |
| Existing Customer | 369 |
| Prospek | 4.239 |
| Duplicate lintas sheet | 348 |
| Audit batch | 1 |
| Audit perubahan | 4.608 |
| Customer dibuat pada import kedua | 0 |
| Customer dilewati pada import kedua | 4.608 |

Integrity result:

```text
PRAGMA integrity_check: ok
PRAGMA foreign_key_check: 0 violation
```

Dua baris error tidak mempunyai nama dan nomor WhatsApp yang dapat divalidasi.
Nilai tersebut tidak ditebak dan tidak dimasukkan ke tabel customer.

## 5. Validasi Pasca-Merge

Checkout lokal disinkronkan dengan:

```bash
git switch main
git pull --ff-only origin main
```

Hasil:

```text
Ran 38 tests in 2.850s
OK

compileall: OK
git diff --check: OK
main vs origin/main: ahead 0, behind 0
working tree: clean
```

## 6. Status Migration dan Database Production

- Migration customer bersifat additive dan idempotent.
- Sebelas kolom metadata ditambahkan tanpa menghapus atau mengganti kolom lama.
- Tabel `customer_import_batches` dan `customer_import_changes` ditambahkan untuk
  audit import.
- Migration production belum dijalankan.
- Database production tidak dibuka, tidak ditulis, dan tidak di-backfill.
- Repository tidak memuat file runtime `database/crm.db` pada validasi ini.

## 7. Risiko Data Historis

- Duplicate database aktual baru dapat diketahui saat preview terhadap salinan
  database operasional.
- Sebanyak 73 baris sumber tidak memiliki nomor WhatsApp valid tetapi masih bisa
  menjadi customer bila nama tersedia.
- Dua baris gagal validasi minimum dan memerlukan koreksi manual pada sumber
  resmi bila ingin dimasukkan.
- Deduplikasi tanpa WhatsApp/email membutuhkan nama dan perusahaan sekaligus;
  nama saja tidak dipakai untuk menghindari penggabungan customer berbeda.
- Merge yang sudah di-commit dapat mengisi field kosong, menggabungkan minat dan
  sumber, atau menaikkan Prospek menjadi Existing Customer.

## 8. Prosedur Backup Sebelum Uji Lokal

1. Hentikan sementara seluruh proses yang menulis ke SQLite.
2. Verifikasi path database dari environment aplikasi; jangan menebak nama file.
3. Buat backup konsisten dengan SQLite backup API atau perintah `.backup`.
4. Salin juga konfigurasi deployment yang menentukan path database.
5. Jalankan `PRAGMA integrity_check` pada backup dan pastikan hasilnya `ok`.
6. Simpan satu backup immutable dan gunakan salinan kedua untuk dry-run.
7. Catat checksum, ukuran file, row count customer, dan timestamp backup.
8. Jalankan migration dua kali pada salinan untuk membuktikan idempotency.
9. Jalankan preview import, review konflik, warning, error, dan jumlah merge.
10. Jalankan import hanya pada salinan lokal sebelum meminta approval production.

## 9. Langkah Rollback

Jika error terjadi sebelum commit, transaction engine otomatis melakukan rollback
customer, batch, dan audit changes.

Jika rollback diperlukan setelah commit:

1. hentikan write aplikasi;
2. arsipkan database bermasalah untuk audit;
3. pulihkan backup SQLite terverifikasi;
4. jalankan `PRAGMA integrity_check` dan `PRAGMA foreign_key_check`;
5. cocokkan row count dan sampel customer;
6. jalankan seluruh regression test sebelum aplikasi dibuka kembali.

Tabel `customer_import_changes` menyimpan before/after untuk analisis dampak,
tetapi reversal otomatis pasca-commit belum termasuk Sprint 8.

## 10. Status Akhir

- PR #4 merged menggunakan merge commit.
- Histori tiga commit Sprint 8 dipertahankan.
- Main lulus seluruh regression pasca-merge.
- Database production tidak disentuh.
- Migration production tetap pending sampai backup dan dry-run disetujui.
