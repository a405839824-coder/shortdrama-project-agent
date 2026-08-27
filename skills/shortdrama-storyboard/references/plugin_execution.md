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
- Use two permanent independent ChatGPT/MCP authorities for formal work: production and audit. Start a third repair authority only after audit fails and repair is authorized. Cloud authority-sensitive tools require distinct trusted MCP sessions; repair must return to the original audit work order/session.
- Read `incremental_storyboard_execution.md` before choosing full, delta, patch, audit, or repair routes.

## Standard route

1. `get_agent_status(project_id?)`
2. `create_project(project_name)` only when no matching project exists
3. `begin_project_task(...)` for every request
4. `import_source_file(...)` once per unchanged source fingerprint and `read_project_artifact(...)` for required content; automatic bulk is the default for long content, and `if_none_match` avoids re-reading unchanged content
5. For novel/chapter conversion, finish complete reading and build the conditional `source_ingestion` evidence before planning shots
6. `plan_change_request(...)` before script/storyboard add, modify, delete, replace, merge, split, or move
7. Generate or rewrite within the locked scope
8. `commit_project_artifact(...)` for plans, scripts, dialogue, anchors, keyframes, Seedance 2.5 batches, BGM, voice, statistics, conversion, or production packages
9. `complete_task_validation(...)` for every non-storyboard deliverable
10. For a first full draft or invalidated complete scene/episode, call `commit_storyboard(...)`. For an authorized bounded change to an existing canonical, call `commit_storyboard_delta(...)` with the exact base hashes, stable UIDs, target range, neighboring boundaries and closed replacement batches. Both save a full canonical JSON/XLSX, production preflight, signed handoff and honest server-only trace without claiming independent audit pass.
11. In the separate original audit window call `audit_storyboard(...)`; atomically consume the exact handoff, reuse only objective facts, and independently recompute all eight quality layers.
12. `signoff_task(...)` only after the exact clean independent-audit certificate exists and the user explicitly approves or asks to reopen.

## Audit and repair route

- `audit_only`: audit the whole artifact once and report only the independent result; do not modify it.
- `audit_and_repair`: the original audit window first issues an immutable certificate and problem package. Only then open the conditional repair window. For certificate-confirmed non-structural shot fields, use `commit_storyboard_patch`; structural/boundary/cognition/source/order/timing/topology/core-plan repair uses the package-bound full `commit_storyboard` rebuild of the complete affected scene or episode. `commit_storyboard_delta` is production-authorized for user-directed changes, not a repair-authority shortcut. Every route creates a full new JSON/XLSX and runs all R10 machine gates.
- A repaired version always returns through the signed `repair_to_audit` handoff to the same original audit work order/window/session and audit cycle. Repair/preflight cannot sign itself off, and a replacement audit authority cannot consume the return handoff.
- The result is clean only when blocking errors, residual issues, and introduced issues are all zero.
- Reuse a prior independent result only when canonical, raw artifact, source, boundary, ruleset, validator and audit-class fingerprints all match. A production preflight never satisfies the first independent audit.
- At round three, stop local patching and return the upstream-rebuild blocker.

## Efficiency without lowering quality

- Delta changes reduce authored/transferred content, not validators: the server still materializes the complete canonical, exports the complete workbook, runs all 49 validators and hands the full artifact to all eight independent audit layers.
- Automatic 16,000-character bulk reads reduce long-source tool-call count while preserving exact-order, no-gap reconstruction.
- Tool traces cover MCP server time only. Report ChatGPT planning/generation as unmeasured unless a real end-to-end replay measured it; never infer total chat duration from a fast server trace.

### Fast-safe call discipline

- An ordinary content task with a matching runtime-test certificate must not inspect plugin source, rerun bootstrap/golden/Agent suites, or rebuild system rules. `get_agent_status` verifies the certificate once.
- Use the plugin's authenticated project hub and issue ledger for ordinary content tasks. Do not materialize or rewrite the global Library hub/ledger unless the request is a system/rule update, explicit shared-ledger task, or final archival synchronization.
- For an unchanged imported source, use its stored fingerprint and canonical evidence. Do not download or page through the same file again merely to restate production facts; a fingerprint mismatch or a genuinely missing semantic excerpt is the only reread trigger.
- A bounded production replacement normally needs `get_agent_status` → `begin_project_task` → `plan_change_request` → one `commit_storyboard_delta`. Add one automatic bulk read only when the changed semantic content is not already available. Full generation still uses the full route.
- A clean independent audit normally needs `get_agent_status` → one read-only audit work order → one `audit_storyboard` call against the signed handoff. The service rereads the canonical and recomputes all validators/eight-layer evidence; the chat must not regenerate shots, repeat production preflight, or expand the complete workbook into prose.
- A failed audit may add the conditional repair chain only after authorization: one repair work order → one package-bound patch/full commit → one return audit in the original audit window. Do not keep an idle third window or pre-generate repair content.
- Keep intermediate chat updates brief and return IDs/hashes instead of echoing complete canonical rows. If the safe route needs extra reads or retries, record the concrete cache miss, fingerprint change, missing input, validator failure, or transport error that caused them.

## Cross-chat continuity

Continuity comes from the MCP data store, authenticated user namespace, and stable `project_id`; it does not come from reading inaccessible chat history. The MCP stores project state, versions, deliverables, audit events, recurrence, and capability gaps. In cloud mode, use persistent storage and OAuth so different users cannot share a namespace.

## Brief user-facing audit format

- Clean: `审计通过｜阻塞错误0｜原问题残留0｜修改新增问题0`
- Failed: `审计未通过｜共N项` followed by short numbered items with shot IDs and one-sentence fixes.
- Blocked: one sentence naming the blocker and next action.
