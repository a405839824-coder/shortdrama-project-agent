# Audit environment renewal

An authentic audit can become stale after deployment or runtime-test renewal.
This is `PENDING_AUDIT` with `audit_evidence_state=stale_environment`, not a
current PASS and not evidence corruption. Original bytes, canonical/source
identity, signed authority, cache identity, trace references and the saved
overlay remain mandatory. Tampering or missing required records still blocks.

New audit/intake work orders may proceed while historical evidence is stale.
Production/export gates do not inherit the old PASS. A new artifact receives
its own native intake and complete independent audit.

To re-audit the same consumed external intake when there is no pageable
collection, use the existing controlled recovery protocol with
`controlled_phase:audit_environment_recovery`. The plan verifies signed
historical lineage, coverage, source and artifact bytes; execute binds that
exact plan and the confirmation
`REVALIDATE_STALE_ENVIRONMENT_PRESERVE_HISTORY`. It adds a current intake and
trace. It does not rewrite history or copy old findings as current review.
Repeats resume the same signed journal; an unpublished successor cannot audit.

Deployment acceptance has separate gates:

1. Affected synthetic tests, including tampering, certificate-only renewal,
   legacy nonpaged intake, retry and interrupted publication.
2. A fresh verified snapshot of active runtime and protected identities before
   replacing the service. Never re-arm an already consumed transfer.
3. New deployed source/version and runtime self-test certificate read back.
4. The actual project creates a native audit/intake order, imports exact current
   workbook bytes and reads the full obligation manifest successfully.
5. Every requested episode is audited against its current bytes and source,
   including every required shot, adjacent pair and layer; every final native
   receipt is read back. Unknown evidence and failed checks stay explicit.

Steps 1–4 establish service recovery, not content accuracy or audit completion.
File counts, a healthy endpoint and software test totals are never substitutes
for step 5. Preserve the full inventory of known unresolved failures and bind
each claimed completion to its actual receipt.
