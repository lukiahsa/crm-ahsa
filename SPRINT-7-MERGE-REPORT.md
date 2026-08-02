# Sprint 7 — Merge Report

## 1. Ringkasan Merge

| Item | Hasil |
|---|---|
| Repository | `lukiahsa/crm-ahsa` |
| Pull Request | `#3 — Sprint 7 Import Master Produk Existing` |
| Base | `main` |
| Head | `agent/sprint-7-import-master-product` |
| Metode | Merge commit; tidak squash dan tidak rebase |
| Head yang divalidasi | `2e87151192a6ec52b1d35f17e8ab19fe77be817b` |
| Merge commit SHA | `b93c0b04016375f07c87827250d95f5ad203183b` |
| Application HEAD setelah merge | `b93c0b04016375f07c87827250d95f5ad203183b` |
| Status PR | Merged |

Branch Sprint 7 lebih dahulu diperbarui terhadap `main` terbaru agar seluruh
fitur Sprint 8 tetap terbawa. PR kemudian dipindahkan dari Draft menjadi Ready
for Review dan di-merge setelah status GitHub `mergeable`, seluruh regression,
UAT file resmi, serta pemeriksaan integritas lulus.

## 2. Conflict dan Penyelesaian

Satu conflict ditemukan pada bagian import Python di `app/main.py` ketika
`main` Sprint 8 digabung ke branch Sprint 7.

Resolusi mempertahankan seluruh sisi yang diperlukan:

- `hashlib` dan import engine produk Sprint 7;
- import engine customer Sprint 8;
- route `/products/import` dan `/customers/import`;
- schema audit `customer_import_batches` dan `customer_import_changes`;
- kolom produk `subkategori`, `jenis_produk`, dan `steps`;
- seluruh aturan Multi Identity, PPN Denko, dan Financial Invariant existing.

File lain diselesaikan otomatis oleh Git karena perubahannya kompatibel. Audit
manual memastikan daftar produk/customer, requirements, templates, dan tests
Sprint 7 serta Sprint 8 tetap tersedia.

Baseline digabung melalui commit:

```text
343fea2b669a9726ab0a73774e082860073deae9
merge(main): integrate Sprint 8 into product import branch
```

Tidak ada force-push, squash, atau rebase terhadap histori branch yang telah
dipublikasikan.

## 3. Commit Sprint 7 yang Dipertahankan

1. `6ad2011441dd1728d43cf9975f5c6a43c01d616b` — `feat(product): add normalized XLSX import engine`
2. `4c18be6920b1de1c5200fd5c5bb965be5d1e6aeb` — `feat(product): add import preview and confirmation workflow`
3. `9fdcde5c7eef1bb624e66804bd1efc26359d86df` — `test(product): cover master product import workflow`
4. `5d350d4b6364a00c057de5516e5f69fca6ff7f8c` — `docs(product): document Sprint 7 master import`
5. `653b8d40dc081c188c730bedfe3f90e9a0159e98` — `docs(product): record published branch history`
6. `bca2c94cbae43d75a5e9686848ab8d6b80868cbc` — `fix(product): leave selling price unset during import`
7. `343fea2b669a9726ab0a73774e082860073deae9` — `merge(main): integrate Sprint 8 into product import branch`
8. `2e87151192a6ec52b1d35f17e8ab19fe77be817b` — `fix(product): add searchable master product list`

Seluruh commit tetap terlihat pada histori karena PR menggunakan merge commit.

## 4. Dependency dan Fresh Environment

- `openpyxl` tercatat secara eksplisit dalam `requirements.txt`.
- `pip install -r requirements.txt` berhasil pada virtual environment baru.
- Import Flask, pandas, openpyxl, Pillow, dan qrcode berhasil.
- `python app/main.py` berhasil start pada salinan aplikasi dengan database
  SQLite sementara.
- Tidak ada dependency yang hanya tersedia melalui instalasi manual tersembunyi.

Server development hanya digunakan sebagai smoke test startup. Aplikasi tidak
dijalankan terhadap database repository atau database production.

## 5. Final Validation Sebelum Merge

Perintah:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q app tests
git diff --check
```

Hasil:

```text
Ran 56 tests in 3.817s
OK

compileall: OK
git diff --check: OK
working tree: clean
branch vs origin branch: ahead 0, behind 0
```

Regression gabungan mencakup Sprint 6, Sprint 7, dan Sprint 8, termasuk Multi
Identity, PPN Denko, Financial Invariant, import produk, dan import customer.

## 6. UAT Import Master Produk Resmi

UAT menggunakan `MASTER PRODUK.xlsx` pada database SQLite sementara.

| Metrik | Hasil |
|---|---:|
| Total baris dibaca | 284 |
| Valid | 184 |
| Warning | 100 |
| Duplicate pada database kosong | 0 |
| Error | 0 |
| Produk dibuat pada import pertama | 284 |
| Produk dibuat pada import kedua | 0 |
| Duplicate yang dilewati pada import kedua | 284 |
| Tempat Sampah | 69 |
| Tangga | 115 |
| Material Handling | 100 |
| Harga jual nonzero yang diisi importer | 0 |
| Harga modal nonzero Material Handling | 0 |

Verifikasi fungsional:

- `/products`: HTTP 200;
- `/products/import`: HTTP 200;
- `/customers`: HTTP 200;
- `/customers/import`: HTTP 200;
- `/quotations/add`: HTTP 200;
- produk `Tempat Sampah 240 Liter` dapat ditemukan melalui pencarian
  `/products`;
- produk hasil import tersedia pada selector quotation existing;
- import file customer resmi yang identik untuk kedua kali menghasilkan 0
  customer baru dan 4.608 customer dilewati;
- `PRAGMA integrity_check`: `ok`;
- `PRAGMA foreign_key_check`: 0 pelanggaran.

## 7. Validasi Pasca-Merge

Checkout lokal disinkronkan dengan:

```bash
git switch main
git pull --ff-only origin main
```

Hasil:

```text
Ran 56 tests in 3.906s
OK

compileall: OK
git diff --check: OK
main vs origin/main: ahead 0, behind 0
working tree: clean
```

Route import produk dan customer tetap aktif setelah merge. Branch Sprint 7
tidak dihapus agar masih tersedia untuk verifikasi lokal dan visual.

## 8. Status Migration dan Database Production

- Migration produk bersifat additive dan idempotent.
- Kolom `subkategori`, `jenis_produk`, dan `steps` ditambahkan tanpa menghapus
  atau mengganti kolom produk lama.
- Migration customer Sprint 8 dan tabel audit tetap dipertahankan.
- Migration production belum dijalankan.
- Database production tidak dibuka, tidak ditulis, dan tidak di-backfill.
- File runtime `database/crm.db` tidak dibuat di repository selama validasi.

## 9. Risiko

- Database target dapat memiliki duplicate existing sehingga jumlah produk yang
  benar-benar dibuat bisa lebih rendah dari hasil UAT database kosong.
- Seratus produk Material Handling belum memiliki harga modal resmi; importer
  menyimpan nilai 0 sesuai schema dan menandainya sebagai warning.
- Harga jual sengaja tidak diisi karena tidak tersedia pada file sumber resmi.
- Penambahan pencarian `/products` masih menggunakan pencarian `LIKE`; pagination
  dan index pencarian dapat diperlukan bila volume master meningkat jauh.
- Authentication dan CSRF tetap mengikuti kondisi aplikasi existing dan berada
  di luar scope Sprint 7.
- Migration atau import langsung tanpa backup tetap berisiko pada data
  operasional walaupun transaksi import bersifat atomik.

## 10. Prosedur Backup Sebelum Uji Lokal

1. Hentikan sementara proses yang menulis ke SQLite.
2. Pastikan path database dari konfigurasi deployment, bukan dari asumsi nama
   file.
3. Buat backup konsisten menggunakan SQLite backup API atau perintah `.backup`.
4. Simpan satu backup immutable dan buat salinan kedua khusus dry-run.
5. Catat checksum, ukuran file, timestamp, serta row count produk dan customer.
6. Jalankan `PRAGMA integrity_check` dan `PRAGMA foreign_key_check` pada backup.
7. Jalankan migration dua kali pada salinan untuk membuktikan idempotency.
8. Preview `MASTER PRODUK.xlsx` pada salinan dan review duplicate/warning/error.
9. Konfirmasi import hanya pada salinan, ulangi import, dan pastikan 0 produk baru.
10. Jalankan seluruh regression serta sampling produk sebelum meminta approval
    production.

## 11. Rollback

Jika error fatal terjadi sebelum commit, transaction import otomatis melakukan
rollback seluruh produk dan master referensi dalam batch tersebut.

Jika rollback diperlukan setelah import berhasil:

1. hentikan write aplikasi;
2. arsipkan database bermasalah untuk audit;
3. pulihkan backup SQLite yang sudah diverifikasi;
4. jalankan `PRAGMA integrity_check` dan `PRAGMA foreign_key_check`;
5. cocokkan row count dan sampel produk/customer;
6. jalankan seluruh regression sebelum aplikasi dibuka kembali.

Rollback kode dilakukan dengan revert merge commit PR #3. Kolom additive dapat
dibiarkan karena nullable dan backward-compatible; penghapusan kolom tidak
direkomendasikan.

## 12. Status Akhir

- PR #3 merged menggunakan merge commit.
- Histori delapan commit Sprint 7 dan update baseline dipertahankan.
- Main lulus seluruh 56 regression pasca-merge.
- Dependency fresh environment dan startup aplikasi tervalidasi.
- Database production tidak disentuh.
- Migration production tetap pending sampai backup dan dry-run disetujui.
- Branch Sprint 7 tidak dihapus.
- Branch Sprint 9 tidak dibuat pada pekerjaan ini.
