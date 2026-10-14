# Deploy 0.5.65 while preserving the mounted Library runtime

This release uses one fresh transfer for the combined audit and generation
changes. It does not rewrite project history, administrator identities, producer
permissions, or the signed Library pointer registry. Run the commands below on
the authorized Railway service, from the application repository root. Do not
publish `main` until the freeze and publication steps have completed: the live
Railway service deploys pushes to `main` without waiting for check suites.

The transfer is uniquely bound to:

- Directory: `/data/.upgrade-production-grounding-0565`
- Contract: `library-runtime-transfer-production-grounding-0565-v1`
- Target: `0.5.65`
- Runtime: `/tmp/shortdrama-library-runtime`
- Maintenance trigger: `production_grounding_upgrade_gate_0565`

The new preparation and arming scripts use helpers already present in 0.5.64.
Copy only these reviewed scripts to a temporary directory in the old container
when its image does not contain them; keep both scripts in the same directory
and keep `PYTHONPATH` pointing to the old application's `server` directory.
Do not replace the running application's modules before the release.

## 1. Prepare a verified, unarmed snapshot

```bash
PYTHONPATH=server python scripts/prepare_upgrade_0565_backup.py
```

Expect `PREBACKUP_VERIFIED_NOT_ARMED`. This preserves the complete `projects`
and `exports` tree, verifies every archived file, and rechecks the live tree and
protected identity fingerprints. It does not block tools or arm startup restore.
An existing destination is refused, including previously consumed or aborted
transfers. A failed attempt removes only the partial files it created.

Limits remain finite: 2 GiB compressed, 8 GiB expanded, 100,000 files, and at
least 256 MiB free volume space after preparation. Inventory the actual tree and
space first. Never omit files, remove history, or replay an old archive to make
the release fit. If a verified draft becomes stale, stop and prepare a reviewed
fresh transfer; never edit its signed file hashes to force it through.

## 2. Freeze and drain the old container

```bash
PYTHONPATH=server python scripts/arm_upgrade_0565_backup.py freeze
```

Keep this process running in the old container. Expect
`ARMED_WRITER_FROZEN`. The script holds the retention lock, installs a persistent
SQLite gate before new tool dispatch can execute, waits for existing calls to
finish, and checks the exact prepared bytes and protected identities again.
Only then does it sign an active `manifest.json` with `old_writer_frozen=true`.

Before publication, an ordinary cancellation or a 15-minute publication timeout
disarms this manifest before reopening the old writer. A changed or unknown
manifest is not automatically cleared. A pre-existing maintenance trigger is
never replaced or removed. Once publication is marked, there is no automatic
timeout that reopens tools, even if deployment or restoration subsequently fails.

## 3. Mark publication and immediately deploy the reviewed commit

In another console on the same old container, while the freeze process remains
alive:

```bash
PYTHONPATH=server python scripts/arm_upgrade_0565_backup.py mark-publication
```

Expect `PUBLICATION_MARKED_GATE_REMAINS_CLOSED`. The marker binds the signed
manifest; publication and timeout cancellation are serialized by a lifecycle
lock. The signed freeze-process identity is checked before and after byte
verification; an exited process or reused PID cannot mark publication. Publish
the reviewed 0.5.65 commit only after this succeeds. Observe the
Railway deployment until terminal `SUCCESS`; a queued build is not a deployment.

Startup verifies the version binding, actual persistent maintenance trigger,
protected fingerprints, archive hash, every member, paths and finite limits.
It publishes only a complete tree, before creating the server or listening. It
will not merge into a different existing runtime. Success creates a signed
receipt and consumes the manifest once. Old consumed snapshots remain untouched.

## 4. Verify the new container, then explicitly reopen dispatch

On the new 0.5.65 container:

```bash
PYTHONPATH=server python scripts/arm_upgrade_0565_backup.py verify
```

Expect `RESTORE_VERIFIED_GATE_CLOSED`. Verification checks the running package
version, signed publication marker, consumed manifest and signed restore
receipt, complete current tree, compressed archive, protected identities and
the exact SQL maintenance gate. It does not reopen dispatch.

After confirming the expected deployment/commit and health, run:

```bash
PYTHONPATH=server python scripts/arm_upgrade_0565_backup.py verify-and-reopen
```

Expect `RESTORE_VERIFIED_DISPATCH_REOPENED`. All byte and identity checks run
again before the transaction removes only this release's exact trigger. Any
validation failure leaves the gate closed. Verify live plugin status, both
administrator read directions, and producer isolation after reopening. Do not
claim that these deployment checks measure storyboard content quality.

## Failure and rollback

Before publication is marked, allow the freeze process to disarm and resume the
unchanged old image. After publication starts, preserve the maintenance gate
and collect the failed step; never bypass verification to restore availability.
An interrupted restore keeps the active marker for safe retry. A consumed
manifest must never be re-armed, including after a cold restart.

Railway's ability to roll back an image does not restore this ephemeral runtime.
The previous 0.5.64 image does not understand the new contract. Do not directly
roll back/redeploy it or overwrite newer writes with an old snapshot. A rollback
requires preserving the latest complete state and a reviewed rollback build
with an explicitly compatible, fresh recovery contract. No destructive schema
migration is required by this transfer.

Retention continues to preserve the two newest verified restore archives and
all archives younger than seven days; pending restore archives are skipped.
No project or audit history is deleted by this release's deployment scripts.
