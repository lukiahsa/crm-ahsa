# Sprint 15 — Transaction Workspace

## Ringkasan

Sprint 15 mengubah halaman detail Transaction menjadi satu Workspace operasional.
Transaction tetap menjadi pusat ATCA; Invoice, Receipt, Delivery Order, dan
Purchase Order tetap child document opsional. Dashboard, Customer360,
Historical Purchase, Workflow Integrity, Revision Engine, Financial Engine,
Inventory, Warehouse, Purchase Order, PPN Denko, dan template print tidak
diubah.

## Git

- Branch: `agent/sprint-15-transaction-workspace`
- Baseline efektif: `9eeb9e208a42b84f06acdb93320b605e729fe597`
- Baseline tersebut terdiri dari merge Sprint 14 `3555a00` dan tiga commit
  Hotfix 14.1.
- Merge dan squash tidak dilakukan.

## Arsitektur

`app/transaction_workspace.py` adalah read-model khusus halaman Workspace.
Service membaca snapshot Transaction, child document, event workflow, revision,
dan Customer360 tanpa menulis ulang status atau angka pada engine lama.

Payment Summary dihitung read-only:

- Grand Total = `MAX(total_penjualan - potongan, 0)`.
- Pembayaran = Receipt Invoice non-Void + Receipt Transaction non-Void.
- DP = Receipt aktif dengan jenis `DP`.
- Pelunasan = total pembayaran aktif selain DP.
- Outstanding = `MAX(Grand Total - total pembayaran, 0)`.
- Status = Lunas bila Grand Total positif dan Outstanding nol; selain itu Belum
  Lunas.

Financial Summary hanya memproyeksikan field snapshot yang sudah dihasilkan
Financial Engine:

- Modal = `total_modal`.
- Subtotal = `total_penjualan`.
- Margin = `margin`.
- Profit = `laba_bersih`.
- Persentase Margin = `margin / total_penjualan × 100` dengan guard nol.

Tidak ada recalculation atau update terhadap Financial Engine.

## Migration

Migration additive dan idempotent menambah:

1. `transaction_workspace_notes`
2. `transaction_attachments`
3. `idx_transaction_workspace_notes_tx`
4. `idx_transaction_attachments_tx`

Notes internal sengaja dipisahkan dari `sales_transactions.catatan`, sehingga
tidak masuk template print. Attachment disimpan pada tabel additive dengan nama
asli, nama aman, MIME type, ukuran, SHA-256, metadata pengunggah, dan BLOB.
Storage lama tidak diubah. Batas upload 10 MB dengan allowlist PDF, gambar,
DOC/DOCX, dan XLS/XLSX.

## Section Workspace

1. Header: nomor, status, tanggal, sales, customer, PIC, WA, referal, jenis,
   margin, dan profit.
2. Action Panel: Invoice, Receipt, Delivery Order, Purchase Order, Print, dan
   WhatsApp.
3. Payment Summary.
4. Financial Summary.
5. Document Status.
6. Activity Timeline terbaru-dahulu.
7. Document History dengan link dokumen.
8. Notes internal.
9. Attachment.
10. Customer Snapshot dari service Customer360.

Tabel item dan Workflow Control lama tetap tersedia di halaman yang sama.

## Module Manager

Action Invoice, Receipt, Delivery Order, dan Purchase Order memakai policy
`module_enabled`. Ketika module OFF:

- tombol Generate/Open hilang;
- status menampilkan `Module OFF`;
- link history tidak dapat dibuka dari Workspace;
- route tetap diblokir oleh guard ATCA sebelum workflow berjalan.

Print Transaction dan WhatsApp tetap action core Transaction.

## Timeline dan Document History

Timeline menggabungkan:

- Transaction dibuat;
- Quotation, Invoice, Receipt, DO, dan PO dibuat;
- perubahan status dan workflow event;
- cancellation;
- quotation revision;
- Notes internal;
- Attachment.

Document History memuat Quotation, Invoice, Receipt Invoice, Receipt langsung,
Delivery Order, dan Purchase Order terkait Transaction. Query PO juga mencakup
legacy PO yang terhubung melalui Invoice untuk backward compatibility.

## Customer Snapshot

Workspace memanggil `get_customer_360` dan hanya memproyeksikan:

- Customer;
- repeat customer;
- jumlah Historical Purchase;
- total omzet;
- last order;
- top product.

Definisi Customer360 tidak diduplikasi atau diubah.

## Test dan Regression

Perintah:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q app tests
git diff --check
```

Hasil:

- Existing baseline: 227 test lulus.
- Test Sprint 15: 9 test lulus.
- Total regression: 236 test lulus.
- Compileall: lulus.
- Diff check: lulus.
- Migration dijalankan berulang: lulus.
- `PRAGMA integrity_check`: `ok`.
- `PRAGMA foreign_key_check`: 0 violation.
- Lima template print tetap memiliki SHA-256 yang sama dengan Hotfix 14.1.

## UAT

Skenario wajib:

1. Customer tanpa Quotation.
2. Transaction dibuat.
3. Generate Invoice.
4. Generate Receipt langsung dari Transaction.
5. Generate Delivery Order.
6. Generate Purchase Order.
7. Workspace dibuka kembali.

Hasil: Invoice, Receipt, Delivery Order, dan Purchase Order berstatus sudah
dibuat, masing-masing menampilkan jumlah satu, nomor terakhir, link history, dan
event creation pada timeline. Quotation tetap `Belum dibuat`. Notes dan
Attachment tidak tampil pada print Transaction.

UAT visual memakai database SQLite sintetis terpisah dengan Transaction
`TRX/2026/08/000015`, customer Ibu Geugeu, 12 unit Tempat Sampah 120 Liter Roda
Hijau, DP 50%, serta empat child document. Pemeriksaan visual mencakup layout
desktop, sticky Action Panel, header, dua summary, Document Status, Timeline,
dan Document History. Setelah render awal, summary diubah menjadi satu baris per
metrik agar angka Rupiah tidak overlap pada viewport sempit.

## Risiko dan Mitigasi

- Banyak child event dapat memperpanjang halaman: timeline dan history memakai
  urutan terbaru-dahulu dan query bounded, tanpa query per baris.
- Attachment memperbesar file SQLite: ukuran tiap file dibatasi 10 MB dan hash
  disimpan untuk audit.
- Receipt langsung dan Receipt Invoice dapat hidup bersama: Payment Summary
  hanya membaca pembayaran non-Void dan tidak mengubah reconciliation engine.
- Module dapat dimatikan setelah dokumen ada: metadata tetap terlihat sebagai
  `Module OFF`, tetapi tombol dan link action disembunyikan.

## Rollback

Rollback aplikasi dilakukan dengan `git revert` commit Sprint 15 dalam urutan
terbalik. Jangan drop tabel secara otomatis karena rollback harus tetap
non-destructive. Dua tabel additive boleh dibiarkan tidak terpakai; data lama
dan seluruh engine baseline tetap valid. Jika removal schema benar-benar
diperlukan, lakukan hanya melalui migration terpisah setelah backup dan audit.

## Production Safety

Database production tidak disentuh. Seluruh migration, test, UAT, visual render,
integrity check, dan foreign-key check menggunakan database SQLite sementara.
