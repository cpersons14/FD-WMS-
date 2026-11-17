## Change

Explain the concrete warehouse behavior and why it changes.

## Validation

- [ ] PostgreSQL release checks passed with no skipped tests.
- [ ] Existing receipt, allocation, fulfillment, import and permission tests passed.
- [ ] Added a regression test for any corrected inventory or permission bug.
- [ ] Schema changes are reviewed; deployment and rollback effects are explained.
- [ ] No customer records, credentials, attachments or database dumps are in this PR.
- [ ] Tested on staging before production promotion.

## Review

Inventory arithmetic, lock ordering, permissions, and migrations need independent review.
Link the test result and reviewer decision. Do not treat author-written tests as independent approval.
