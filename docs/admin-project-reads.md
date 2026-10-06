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

Only publish after this new transfer is verified and the old writer remains
frozen. Startup checks the entire archive and protected identities, restores
before listening, and consumes the marker once. Verify the restore receipt,
full runtime tree, exact source, unchanged owner/producer identities and both
administrator read directions after deployment. Software tests establish access
behavior; they do not certify storyboard content or episode error statistics.
