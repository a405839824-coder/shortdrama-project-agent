# Administrator project reading

`admin-mutual-project-read-v1` lets every enabled administrator read projects
owned by other enabled administrators. The owner remains unchanged. Producers
continue to see only their own projects; their namespaces are not included in
administrator mutual sharing. The policy applies to both host-managed members
and legacy subject-based administrator grants. Newly provisioned administrators
are included on their next request without a new code deployment.

The four read entries are `get_agent_status(full_self_test=false)`,
`read_project_artifact`, `query_project_issues`, and `read_narrative_record`.
Status lists all readable projects with `project_access` provenance, and project
detail includes published `artifact_versions` so historical files can be read
by their original IDs. Signed file downloads retain their original bytes,
integrity checks and existing short-lived bearer-link lifetime.

Each response identifies `owner_id`, `owner_member_id`, `owner_name`, `shared`,
`read_only`, and `can_write`. Normally the existing `project_id` suffices; if two
owners have the same project ID, pass the exact `owner_id` from the project list.
Ambiguity fails closed. Artifact/audit pagination keeps that owner selection.
Caller-supplied namespace strings and producer owner IDs confer no access.

Every request uses server-side membership/role configuration. Demoting or
disabling a reader stops new shared reads; demoting or disabling an owner removes
their projects from mutual sharing. Already issued bearer links retain the
existing expiry policy. Access audit logs still record the actual caller, never
the project owner as an impersonated actor.

Only read handlers resolve another owner's namespace. Production, edits,
repairs, full self-test certificate writes, migration, snapshot changes and
signoff continue to use the authenticated caller's own namespace. Shared status
exposes the owner's workflow but grants no execution/continuation capability.
An unmounted Library project is listed through its verified signed pointer;
the owner must mount its verified snapshot before shared content reads can run.
No project files, original storyboards, ledger history, templates, or rules are
rewritten, merged, regenerated or copied by a shared read.

## Deployment preservation

This maintenance change retains plugin version `0.5.62` and exposes its separate
read contract in status and health. Do not directly redeploy a Library-mode
container that has unsynced runtime projects. Freeze old writers, wait for active
calls, inventory the complete `/tmp/shortdrama-library-runtime` tree, and create a
fresh signed, byte-verified transfer under `/data/.upgrade-admin-read-20261006`.
The contract is `library-runtime-transfer-admin-read-20261006-v1`, target version
`0.5.62`. Bind `protected_fingerprints` for the existing registry, role database,
keys and signed Library pointers. Never re-arm a previously consumed transfer.

The live status on 2026-10-06 reported 5,381,214,008 bytes in runtime projects
alone, above the older 4 GiB recovery ceiling. This transfer alone accepts up to
8 GiB expanded; earlier transfer contracts retain their 4 GiB limit. The
compressed archive remains capped at 2 GiB and 100,000 regular files. Before
arming it, inventory projects plus exports and verify both ceilings and enough
free destination space. Do not omit files or delete history to fit a ceiling.

`PYTHONPATH=server python scripts/prepare_admin_read_backup.py` can prepare an
unarmed draft on the existing 0.5.62 server without a restart. It retains at
least 256 MiB of free volume space, caps compressed output while writing, and
checks every archived byte against a before/after source inventory and protected
identity fingerprints. Any existing destination is refused. A failure removes
only its own incomplete output. Success creates `runtime.tar.gz` and signed
`manifest.draft.json`, with `old_writer_frozen=false`; it never writes the active
`manifest.json`, stops a process, or deploys. This is a preparatory snapshot, not
the final deployment prerequisite: after draining and freezing the old writer,
recheck the exact complete tree and protected state before signing and arming.
If anything has changed, do not arm that draft.

`scripts/arm_admin_read_backup.py` can drain and freeze the live service without
changing member identities. It installs the named SQLite trigger
`admin_read_upgrade_gate_20261006`, blocking the mandatory `started` audit insert
before any new tool executes, while allowing terminal events to finish. It holds
the retention worker's lock and verifies the complete draft again before arming.
Keep the process running. Without a `deployment-started` marker in the transfer
directory, it disarms and reopens tools after 15 minutes. Write that marker only
immediately before the authorized publication; after that, it never automatically
reopens the old writer. The persistent trigger survives container replacement.
After verifying the new container's signed receipt, full restored tree and
protected fingerprints, explicitly remove only that exact maintenance trigger
to reopen tools. Do not remove it on an unverified or failed restore. Railway
volume-backed deployments cannot mount the old and new containers concurrently.

Only publish after this new transfer is verified and the old writer remains
frozen. Startup checks the entire archive and protected identities, restores
before listening, and consumes the marker once. Verify the restore receipt,
full runtime tree, exact source, unchanged owner/producer identities and both
administrator read directions after deployment. Software tests establish access
behavior; they do not certify storyboard content or episode error statistics.
