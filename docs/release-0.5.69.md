# 0.5.69 — explicit Seedance 2.5 prompt libraries

New production explicitly selects one library and variant before freezing its
plan: animation (3d or 2d), or photorealistic (live_action_look). Missing, stale,
cross-library and incompatible visual-reference declarations fail validation.
Legacy artifacts remain readable without silently assigning a style.

`get_agent_status` accepts optional `prompt_library_id` and `prompt_variant`.
Together they return only that selected profile and shared Seedance guidance in
`selected_prompt_library`; reading it does not select or mutate a project.
The production compiler, formal mother template and GPT keyframe projection use
the same style field. The catalog carries fingerprints, not both libraries' prose.

The bounded C-010 migration replaces the old universal photorealism preference
with explicit selection and reference responsibility. The original 27 fields,
11-column template, source dialogue, timing, voice, permissions and independent
audit gates remain. Registered contradictory positive style phrases are blocked;
this is not complete semantic detection or a claim about generated media quality.

Official source summaries are versioned separately from project-authored style
templates. See `references/seedance25_prompt_libraries.md` in the storyboard skill.

The release adds an immutable signed transfer contract. Older contracts and
consumed snapshots are unchanged. `scripts/upgrade_prompt_library_0569.py` reuses
the existing prepare/freeze/publish/verify implementation; it is a maintenance
CLI, not an automatic recovery of data lost before this release.

## Deployment status and recovery prerequisites

This is a prepared candidate, not an online release. On 2026-10-29 Railway showed
an expired trial, an outstanding invoice, service offline and no active deployment.
The user chose to defer payment and finish release preparation. CLI login was
restored; no deployment, main-branch publication or production-data mutation was
performed for this candidate. Do not label the edge 404 fixed by this code.

Before resuming, verify the current main commit and deployment configuration,
then inventory the existing persistent volume, signed Library pointers, roles,
member connections and latest valid project snapshots. The old container is
offline: the existence of a volume alone does not prove its former temporary
runtime survived. Do not replay an old consumed upgrade snapshot or substitute
older project bytes. If the runtime is absent, recover the latest verified Library
snapshots through the normal authorized mount flow and account for any missing
newer work before proceeding.

Once a live, inventoried runtime exists, use the existing procedure in
`deploy-production-grounding-0565.md` with this release's adapter:

```bash
PYTHONPATH=server python scripts/upgrade_prompt_library_0569.py prepare
PYTHONPATH=server python scripts/upgrade_prompt_library_0569.py freeze
# Keep freeze running; in a second maintenance session:
PYTHONPATH=server python scripts/upgrade_prompt_library_0569.py mark-publication
# Publish the reviewed commit only after the marker succeeds.
# In the new verified 0.5.69 container:
PYTHONPATH=server python scripts/upgrade_prompt_library_0569.py verify
PYTHONPATH=server python scripts/upgrade_prompt_library_0569.py verify-and-reopen
```

Copy only the reviewed adapter into the old image's `scripts/` directory when
needed; its 0.5.66 helper modules already exist in 0.5.68. Freeze and publication
remain sequential, finite, signed and version-bound. Never run these commands
against an empty or uninventoried runtime. Railway auto-deploys `main`, so keep
the candidate off `main` until the data prerequisites are satisfied.

After deployment, verify the terminal success state, `/health` version,
unauthenticated `/mcp` OAuth challenge, authenticated administrator self-test,
actual MCP schema and separate animation/photorealistic reads. Check the protected
state and Library pointers against the pre-deploy inventory. Refresh a client's
tool schema if it has not acquired the optional selection arguments. Local tests
do not establish any of these online results.

Local Windows verification ran 256 selected regression tests: 253 passed and
three platform-specific cases were skipped (symlink privileges, POSIX backslash
filenames, and the Linux procfs/directory-fsync restore exercise). HTTP and STDIO
MCP smoke checks passed, including independent reads of all three variants and
rejection of a cross-library request. The runtime self-test and registry check
passed. Run the Linux restore exercise before deployment; it is not a verified
production-data recovery. This is not a full repository regression or media test.
