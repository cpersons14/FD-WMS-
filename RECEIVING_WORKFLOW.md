# S3PL / Family Dollar receiving — agreed workflow

Confirmed November 4, 2026. This is the design implemented in this pilot; it is not a live deployment.

The floor receives, counts and puts away the delivery using its handwritten putaway sheet. It sends the putaway sheet, BOL and packing list to the office. Packing lists arrive with the truck. Office turnaround is acceptable; tablets and scanning are optional future improvements.

1. Office selects **Receiving → New Receipt**.
2. Enter supplier, received date and delivery/BOL/packing-list reference.
3. Key actual received quantities and locations **from the putaway sheet**. Enter multiple parts, split locations and loose quantities on separate rows as needed. Select eaches, cases or full standard pallets; the screen shows the verified case pack and resulting total eaches. Never infer case pack from the source balances.
4. Record shortages, damage and delivery notes. Put damaged quantities on a separate line into a held quarantine bin. An uncounted destination must be counted first. New part/location combinations are created in Inventory before receiving.
5. Drag and drop scanned BOLs, packing lists and photos, or use Choose files. Preview/remove files and choose which accompany the email. Accepted: PDF/JPG/PNG; maximum 6 MB per file, 12 files and 12 MB total per receipt.
6. Select the saved **FD Receiving** group. An administrator enters its approved distribution address (preferred) or approved individual addresses once. No real FD addresses were supplied during development.
7. **Submit Receipt** is the office approval. All valid lines post together, inventory updates, and one itemized customer email with selected documents is attempted after the inventory transaction commits. There is no second mandatory approval step. With receipt email disabled, the message stays pending for later sending.

**Save Draft** and **Needs clarification** retain work without changing inventory. The queue searches supplier, reference, receipt ID or posted part number and filters drafts, clarification and posted receipts. Posted receipts and their documents are locked. Inventory corrections must retain the receipt number and reason in the ledger; automatic receipt reversal and corrected receipt emails are not implemented.

The inventory post and email send have separate status records. Double submission cannot post the same receipt twice. A failed email cannot undo or repost inventory. Office checks the sender/mailbox before explicitly retrying failed/uncertain sends. The system records the intended recipients, selected documents, send attempts and provider acceptance time. An SMTP interruption can leave delivery uncertain; the system does not promise exactly-once inbox delivery. A changed or deactivated recipient group blocks unsent mail until reviewed; it never silently substitutes recipients.

This release uses a verified per-item case pack and an optional standard cases-per-pallet value. Orders and posted receipts retain their original factors. Changes never multiply existing balances. Variable packs on different pallets/deliveries, packaging-level stock tracking and pallet IDs are not implemented; receive nonstandard pallets in verified cases/eaches and resolve varying case packs before rollout.

Pallets are usually one SKU. No case-by-case labels, lot tracking, tablets or scannable locations are required for this office workflow. Optional future 4×6 pallet labels would show readable part number, quantity, case pack and a unique pallet barcode; distinguish printed receipt quantity from the remaining balance. Barcode label generation is not included yet.
