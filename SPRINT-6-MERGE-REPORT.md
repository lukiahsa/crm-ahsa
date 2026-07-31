# SPRINT 6 - MERGE REPORT

## Ringkasan Merge

| Item | Hasil |
|---|---|
| Repository | `lukiahsa/crm-ahsa` |
| Pull Request | [#1 - Sprint 6 - Multi Identity Ahsa and Denko](https://github.com/lukiahsa/crm-ahsa/pull/1) |
| Base | `main` |
| Head | `agent/sprint-6-multi-identity` |
| Merge method | Merge commit, tanpa squash atau rebase |
| Merge commit SHA | `60368a6672cc7a51f99d10d6177e830dba956a08` |
| Head Sprint 6 | `a29d24e1a9e3bd8206b4fe88b670e3b42f9aace5` |
| Waktu verifikasi | 1 Agustus 2026, Asia/Jakarta |

PR telah di-merge setelah branch diverifikasi bersih, berada `ahead 8` dan
`behind 0` terhadap `main`, serta mencakup commit final `a29d24e`.

## Test Result Setelah Merge

Regression dijalankan dari branch `main` setelah sinkronisasi terhadap
`origin/main`. Test menggunakan database sementara yang dibuat oleh test suite;
aplikasi dan migration tidak dijalankan terhadap `database/crm.db` production.

Perintah:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
```

Hasil:

```text
Ran 11 tests in 0.938s

OK
```

Seluruh skenario berikut lulus:

1. Quotation Ahsa dapat dikonversi menjadi transaction.
2. Quotation Denko ditolak oleh server saat conversion.
3. Capability identity tidak dapat dimanipulasi melalui Company Profile UI.
4. Denko tetap tanpa QR dan seluruh elemen footer meskipun request dimanipulasi.
5. Profil resmi, logo, dan signature Denko muncul pada quotation Denko.
6. Logo, nama, website footer, dan signature Ahsa tidak muncul pada Denko.
7. Invoice, Delivery Order, Receipt, Purchase Order, dan transaction print
   selalu menggunakan identity Ahsa.
8. Quotation legacy otomatis menggunakan identity Ahsa.
9. Seed stub Denko dimigrasikan secara idempotent ke profil resmi.
10. Duplicate dan identity lock setelah conversion tetap berfungsi.
11. Seluruh template Jinja dapat dikompilasi.

## Daftar Commit Sprint 6

| Urutan | Commit | Message |
|---:|---|---|
| 1 | `cb1a8f6` | `feat(identity): add company identity foundation` |
| 2 | `64c89e8` | `feat(identity): enforce server-side document rules` |
| 3 | `2b55e2b` | `feat(identity): render dynamic company identities` |
| 4 | `9be5d2e` | `test(identity): cover multi-identity regression paths` |
| 5 | `5977c3c` | `fix(identity): polish identity document layouts` |
| 6 | `90c6a07` | `docs(sprint-6): add implementation report` |
| 7 | `75b5eec` | `fix(identity): suppress all footer content for Denko quotations` |
| 8 | `a29d24e` | `fix(identity): finalize official Denko identity profile` |

Merge commit memiliki dua parent, yaitu baseline `main` dan head Sprint 6.
Dengan demikian delapan commit tetap terlihat pada histori Git.

## Status Main

- `main` berhasil di-fast-forward secara lokal ke merge commit `60368a6`.
- Regression pasca-merge lulus 11/11.
- Working tree bersih setelah test pasca-merge.
- Commit laporan ini dibuat terpisah di atas merge commit.
- Migration production belum dijalankan.

## Status Branch Sprint 6

- Branch lokal `agent/sprint-6-multi-identity` tetap tersedia.
- Branch remote `origin/agent/sprint-6-multi-identity` tetap tersedia.
- Branch tetap menunjuk ke `a29d24e` dan tidak dihapus setelah merge.
- Setelah merge, branch tidak memiliki commit unik terhadap `main`; perbedaan
  yang tersisa adalah merge commit dan commit laporan pada `main`.

## Risiko yang Tetap Dicatat

- Reprint dokumen lama mengikuti profil identity terbaru karena profile belum
  menggunakan snapshot atau versioning per dokumen.
- Authentication, authorization, dan CSRF protection masih di luar scope
  Sprint 6 dan tetap menjadi blocker deployment publik.
- Migration additive telah diuji otomatis, tetapi belum boleh dijalankan pada
  database production sebelum backup dan uji salinan database selesai.

## Langkah Backup Sebelum Migration

Jangan menjalankan aplikasi versi Sprint 6 terhadap database production sebelum
langkah berikut selesai:

1. Hentikan aplikasi, worker, dan proses lain yang dapat menulis ke SQLite.
2. Catat ukuran dan checksum database production:

   ```bash
   ls -lh database/crm.db
   sha256sum database/crm.db
   ```

3. Buat direktori backup di luar repository dan gunakan fasilitas backup
   SQLite agar salinan konsisten:

   ```bash
   mkdir -p ../crm-ahsa-backups
   sqlite3 database/crm.db ".backup '../crm-ahsa-backups/crm-pre-sprint6.db'"
   ```

4. Verifikasi backup tanpa mengubah database production:

   ```bash
   sqlite3 ../crm-ahsa-backups/crm-pre-sprint6.db "PRAGMA integrity_check;"
   sha256sum ../crm-ahsa-backups/crm-pre-sprint6.db
   ```

5. Simpan satu salinan tambahan pada storage terpisah dengan akses terbatas.

Hasil `PRAGMA integrity_check` wajib `ok`. Jangan lanjut jika backup kosong,
tidak dapat dibuka, atau integrity check gagal.

## Langkah Uji Lokal Berikutnya

1. Gunakan salinan database, bukan file production asli.
2. Jalankan migration hanya pada salinan tersebut.
3. Jalankan kembali regression test 11/11.
4. Periksa tabel `company_identities`, kolom `identity_id` quotation, backfill
   quotation legacy, dan `PRAGMA integrity_check`.
5. Buat quotation Ahsa, convert ke transaction, lalu periksa Invoice, Delivery
   Order, Receipt, dan Purchase Order tetap menggunakan Ahsa.
6. Buat quotation Denko dan pastikan conversion mendapat HTTP 400.
7. Periksa visual PDF Denko: profil dan signature Denko muncul, sedangkan QR,
   logo Ahsa, website Ahsa, dan seluruh footer tidak muncul.
8. Periksa visual PDF Ahsa dan pastikan workflow serta footer lama tidak berubah.
9. Dokumentasikan hasil staging dan minta approval terpisah sebelum deployment
   atau migration production.

