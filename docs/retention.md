# Verified-copy retention

HTTP production starts one housekeeping thread after the MCP lifespan starts.
It waits five minutes after startup, then runs every six hours. A filesystem
lock prevents overlapping runs. `SHORTDRAMA_RETENTION_ENABLED=false` disables
the worker. `/health.data_retention` exposes enabled/alive, last/next run,
actual deleted bytes, skips, errors and volume usage without file contents.
Errors and usage of at least 85% generate warning/error logs and health state.

## Eligibility and retained evidence

* Consumed upgrade archives: at least seven days old, with valid signed manifest
  and successful restore receipt, matching archive SHA256, no pending restore.
  Always keep the newest two verified recovery archives. Keep all manifests,
  restore receipts and a signed deletion receipt; delete only `runtime.tar.gz`.
* Export bundles: signed metadata, expired download token plus 24 hours, exact
  bundle hash, and either a matching signed Library pointer or an identical
  still-mounted project under its existing write lock. Unknown legacy exports,
  changed exports and the only saved copy are retained with reasons.
* Projects, original source, storyboards, audit/signoff history, accounts,
  tokens/keys, and Library registries are never deleted by this worker.
  Project idle time is not currently proven by a request-level lease. Use the
  existing explicitly requested unmount only after committing a verified Library
  snapshot; a clock or old mtime alone must not remove an active project.

`/data/retention/preview.json` is a dry-run plan; `last-run.json` is the latest
actual run. `status.json` contains the last/next run summary. Per-path signed
receipts are written before unlink and completed afterwards. If an unlink or
receipt write fails it is reported, never relabeled as success. These small
receipts remain available; full content is not duplicated into the logs.

The October 2026 remediation first copies all existing historical archives to
the operator's workspace and verifies every compressed and uncompressed hash.
That one-time backup is separate from the ongoing rolling recovery window.
The maintenance deployment keeps the plugin version and every storyboard
contract unchanged, and uses a separately bound one-time startup transfer so
existing unsynced `/tmp` projects survive the container replacement.
