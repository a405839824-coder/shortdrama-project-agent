# ChatGPT plugin execution contract

## Non-negotiable proof of execution

- In every new chat, call `get_agent_status` before acting. Resume with the user's `project_id`; if none is known, inspect the returned project list and ask only when more than one plausible project exists.
- Any mutating task must have a successful `begin_project_task` result. Content shown only in chat is not a persisted project change.
- A generated or changed deliverable exists only after a commit tool returns an `artifact_id`. State the returned ID and download link briefly.
- `commit_storyboard` returns a production preflight, not an independent audit verdict. A storyboard may be archived only after `audit_storyboard` issues a clean `independent_audit` certificate for the exact canonical and raw artifact fingerprints, followed by the user's explicit `signoff_task` approval. Non-storyboard artifacts still use `complete_task_validation`.
- If a tool call fails or tools are absent, report the exact blocker. Never replace an execution result with a confident narrative.
- For novel/chapter input, no formal shot may be generated until the full authorized chapter has been synthesized into one chapter map with source treatment, protected dialogue, character state/cognition, adaptation budget, beat plan, and 100% preflight coverage.
- The formal 10-column mother-template execution field must be named exactly `Seedance 2.5独立正式执行分镜`; legacy Seedance 2.0 labels are compatibility input only.
- For any public-flow scene, formal generation must load `public_scene_ecology.md` and include ordinary-person density, location roles, staggered actions, spatial layers, concrete near/mid/far ambience, dialogue ducking, audiovisual source evidence, and the same ecology in the independent Seedance batch. An empty public scene requires both a visible empty-state description and a plot-grounded reason.

## Standard route

1. `get_agent_status(project_id?)`
2. `create_project(project_name)` only when no matching project exists
3. `begin_project_task(...)` for every request
4. `import_source_file(...)` once per unchanged source fingerprint and `read_project_artifact(...)` for required content; use bounded `bulk_ranges` for long chapters and `if_none_match` when resuming
5. For novel/chapter conversion, finish complete reading and build the conditional `source_ingestion` evidence before planning shots
6. `plan_change_request(...)` before script/storyboard add, modify, delete, replace, merge, split, or move
7. Generate or rewrite within the locked scope
8. `commit_project_artifact(...)` for plans, scripts, dialogue, anchors, keyframes, Seedance 2.5 batches, BGM, voice, statistics, conversion, or production packages
9. `complete_task_validation(...)` for every non-storyboard deliverable
10. `commit_storyboard(...)` for a formal storyboard; it saves the unique canonical JSON, XLSX, production preflight certificate, handoff, stage telemetry and cross-chat evidence without claiming independent audit pass
11. In the separate audit window call `audit_storyboard(...)`; reuse objective handoff facts but independently recompute all eight quality layers
12. `signoff_task(...)` only after the exact clean independent-audit certificate exists and the user explicitly approves or asks to reopen

## Audit and repair route

- `audit_only`: audit the whole artifact once and report only the independent result; do not modify it.
- `audit_and_repair`: the audit window issues an immutable certificate and problem package. For certificate-confirmed non-structural shot fields, use `commit_storyboard_patch` with the exact base canonical hash, issue codes and impact scope; the tool still creates a full new JSON/XLSX and runs all R10 machine gates. For boundary, cognition, source, shot identity/order/timing, scene topology or core-plan changes, use the full `commit_storyboard` rebuild path.
- A repaired version always returns to `audit_storyboard` for a separate final verdict; repair/preflight cannot sign itself off.
- The result is clean only when blocking errors, residual issues, and introduced issues are all zero.
- Reuse a prior independent result only when canonical, raw artifact, source, boundary, ruleset, validator and audit-class fingerprints all match. A production preflight never satisfies the first independent audit.
- At round three, stop local patching and return the upstream-rebuild blocker.

## Cross-chat continuity

Continuity comes from the MCP data store, authenticated user namespace, and stable `project_id`; it does not come from reading inaccessible chat history. The MCP stores project state, versions, deliverables, audit events, recurrence, and capability gaps. In cloud mode, use persistent storage and OAuth so different users cannot share a namespace.

## Brief user-facing audit format

- Clean: `审计通过｜阻塞错误0｜原问题残留0｜修改新增问题0`
- Failed: `审计未通过｜共N项` followed by short numbered items with shot IDs and one-sentence fixes.
- Blocked: one sentence naming the blocker and next action.
