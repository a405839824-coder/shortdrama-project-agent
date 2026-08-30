# ChatGPT plugin execution contract

## Non-negotiable proof of execution

- In every new chat, call `get_agent_status` before acting. Resume with the user's `project_id`; if none is known, inspect the returned project list and ask only when more than one plausible project exists.
- Verify that status explicitly advertises plugin `0.5.4` and `output_contract=R10-SPC-1` before accepting any service-authored storyboard workbook or formal/GPT projection. A valid older compact-master certificate may be used for project-state reads and work-order coordination only. If the canonical can be read, apply the bundled deterministic R10-SPC-1 validator/export adapter and commit a compliant new version; if it cannot, record a capability gap and stop rather than deliver the old header, old timing stages, old lighting text, or deleted fixed phrases.
- Any mutating task must have a successful `begin_project_task` result. Content shown only in chat is not a persisted project change.
- A generated or changed deliverable exists only after a commit tool returns an `artifact_id`. State the returned ID and download link briefly.
- `commit_storyboard` returns an immutable production workbook whose audit overlay is `PENDING_AUDIT`/`未签发结论` with null counts and trace, not an independent audit verdict. `audit_storyboard` creates a new immutable signed-audit-overlay artifact/successor workbook and never edits the production XLSX in place. A storyboard may be archived only after its clean `independent_audit` certificate matches the exact canonical and raw artifact fingerprints, followed by the user's explicit `signoff_task` approval. Non-storyboard artifacts still use `complete_task_validation`.
- If a tool call fails or tools are absent, report the exact blocker. Never replace an execution result with a confident narrative.
- For novel/chapter input, no formal shot may be generated until the full authorized chapter has been synthesized into one chapter map with source treatment, protected dialogue, character state/cognition, adaptation budget, beat plan, and 100% preflight coverage.
- The compact canonical has exactly 27 atomic shot fields. Every new write records `source_lock.output_contract=R10-SPC-1` and follows `strict_prompt_contract.md`: each shot's `生成限制` and both export projections explicitly contain `9:16竖屏`; GPT begins `画幅：9:16竖屏` and uses only the static whitelist; formal/GPT projections contain no literal `BGM` and use `禁止额外配乐`. The XLSX export has exactly these ten columns: `分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`. There is no Seedance execution column or batch sheet. Export is blocked until THG-01–THG-12 and the actual multiline XLSX round-trip pass.
- For any public-flow scene, formal generation must load `public_scene_ecology.md` and include ordinary-person density, location roles, staggered actions, spatial layers, concrete near/mid/far ambience, dialogue ducking, and audiovisual source evidence in the same formal-master record. An empty public scene requires both a visible empty-state description and a plot-grounded reason.
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
8. `commit_project_artifact(...)` for plans, scripts, dialogue, anchors, keyframes, BGM, voice, statistics, conversion, or production packages; do not use it to pre-generate a duplicate Seedance/batch narrative
9. `complete_task_validation(...)` for every non-storyboard deliverable
10. For a first full draft, invalidated complete scene/episode, or any dialogue/speaker change, call `commit_storyboard(...)` for the complete affected scene or episode. Use `commit_storyboard_delta(...)` only for an authorized same-identity/same-count visual or other non-cognitive execution change whose exact dialogue text, punctuation, order/segmentation, speaker identity, source mapping, cognition, and protected-dialogue evidence remain unchanged; supply exact base hashes, stable UIDs, target range, neighboring boundaries, refreshed shot-bound evidence, and only changed complete 27-field records. Psychological audio and any insertion/deletion/movement/split/merge/renumber or source/cognition/boundary/topology change also use the full route. Both routes save a compact canonical JSON/XLSX, production preflight, signed handoff, and honest server-only trace without claiming independent audit pass.
11. In the separate original audit window call `audit_storyboard(...)`; atomically consume the exact handoff, reuse only objective facts, and independently recompute all eight quality layers. When the exact independent-audit cache does not hit, submit `semantic_audit` under `semantic-audit-v2-compact-master`, bound to the canonical hash and reviewed shot count, with exactly eight unique layer records, concrete evidence IDs, targeted issues/actions, and a consistent conclusion. Audit must not regenerate any shot, projection, Seedance prompt, or batch. Omit semantic input only on a validated cache hit; a v1 certificate is never a cache hit for the active contract.
12. `signoff_task(...)` only after the exact clean independent-audit certificate exists and the user explicitly approves or asks to reopen.

## Audit and repair route

- `audit_only`: audit the whole artifact once and report only the independent result; do not modify it.
- `audit_and_repair`: the original audit window first issues an immutable certificate and problem package. Only then open the conditional repair window. For certificate-confirmed non-structural shot fields, use `commit_storyboard_patch`; structural/boundary/cognition/source/order/timing/topology/core-plan repair uses the package-bound full `commit_storyboard` rebuild of the complete affected scene or episode. `commit_storyboard_delta` is production-authorized for user-directed changes, not a repair-authority shortcut. Every route creates a full new JSON/XLSX and runs all 49 validators, G0–G13, THG-01–THG-12 and multiline round-trip checks.
- A repaired version always returns through the signed `repair_to_audit` handoff to the same original audit work order/window/session and audit cycle. Repair/preflight cannot sign itself off, and a replacement audit authority cannot consume the return handoff.
- The result is clean only when blocking errors, residual issues, and introduced issues are all zero.
- Reuse a prior independent result only when canonical, raw artifact, source, boundary, ruleset, validator and audit-class fingerprints all match. A production preflight never satisfies the first independent audit.
- At round three, stop local patching and return the upstream-rebuild blocker.

## Efficiency without lowering quality

- Delta changes reduce authored/transferred content, not validators: the model authors each semantic fact once in the 27-field compact record; actions, expressions, dialogue, delivery, listener reaction and the required three Seedance 2.5 stages exist only in `画面内容`, while `音色` and the four lighting fields remain explicit. The server computes episode-local cumulative timing/batch statistics and projects the R10-SPC-1 formal cell plus static GPT keyframe instruction only at export, runs all 49 validators, G0–G13, THG-01–THG-12 and the multiline XLSX round-trip, and hands the compact artifact to all eight audit layers.
- Production, audit, repair, cache warm-up, and background jobs must never pre-generate or persist Seedance text, `batches`, a Seedance column, or a generated-batch sheet. Legacy forms are read-only migration inputs; new versions never write retired fields back.
- Automatic 16,000-character bulk reads reduce long-source tool-call count while preserving exact-order, no-gap reconstruction.
- Tool traces cover MCP server time only. Report ChatGPT planning/generation as unmeasured unless the user explicitly requested the 0.5.3-or-newer no-API diagnostic and a complete five-checkpoint `mark_performance_stage` run exists. That diagnostic reports `tool_bracketed_chat`, not exact end-to-end time; never infer total chat duration from a fast server trace.

### Explicit no-API diagnostic exception

- Load `no_api_performance_diagnostics.md` only for a user-requested native ChatGPT timing investigation. Ordinary production, audit and repair must not pay for five extra marker calls.
- After the role-specific work order exists, record exactly `task_started → inputs_ready → plan_ready → canonical_ready → delivery_ready`. Use only server timestamps, IDs, enums and bounded counts; never submit source, prompts, dialogue, shot text, credentials, user identity or hidden reasoning.
- Link the persisted production/audit/import telemetry IDs actually returned by this run. Their duration remains `server_only`; the gaps between diagnostic markers are `tool_bracketed_chat` and may contain model work, client transport and orchestration.
- A single run locates the dominant observed interval but cannot prove a thinking-mode effect. Compare reasoning modes only with a user-authorized matched run that keeps source, scope, route, shot count, cache state, window type and R10-SPC-1 deliverables fixed.
- Diagnostics do not authorize removal of per-shot `空间站位描述`, environment, sound, performance/dialogue, `生成限制`, or any of the four lighting fields; all validators, gates and independent audit layers remain active.

### Fast-safe call discipline

- An ordinary content task with a matching runtime-test certificate must not inspect plugin source, rerun bootstrap/golden/Agent suites, or rebuild system rules. `get_agent_status` verifies the certificate once.
- Use the plugin's authenticated project hub and issue ledger for ordinary content tasks. Do not materialize or rewrite the global Library hub/ledger unless the request is a system/rule update, explicit shared-ledger task, or final archival synchronization.
- For an unchanged imported source, use its stored fingerprint and canonical evidence. Do not download or page through the same file again merely to restate production facts; a fingerprint mismatch or a genuinely missing semantic excerpt is the only reread trigger.
- A bounded production replacement normally needs `get_agent_status` → `begin_project_task` → `plan_change_request` → one `commit_storyboard_delta`. Add one automatic bulk read only when the changed semantic content is not already available. Full generation still uses the full route.
- A clean independent audit normally needs `get_agent_status` → one read-only audit work order → one `audit_storyboard` call against the signed handoff. On a cache miss, the audit chat reviews the canonical and supplies the eight-layer semantic evidence in that call while the service independently recomputes machine validators; on an exact cache hit the prior signed evidence is reused. The chat must not regenerate shots, repeat production preflight, or expand the complete workbook into prose.
- A failed audit may add the conditional repair chain only after authorization: one repair work order → one package-bound patch/full commit → one return audit in the original audit window. Do not keep an idle third window or pre-generate repair content.
- Keep intermediate chat updates brief and return IDs/hashes instead of echoing complete canonical rows. If the safe route needs extra reads or retries, record the concrete cache miss, fingerprint change, missing input, validator failure, or transport error that caused them.
- Do not call `mark_performance_stage` on this ordinary fast-safe route. It is an opt-in diagnostic wrapper, not a required production dependency.

## Cross-chat continuity

Continuity comes from the MCP data store, authenticated user namespace, and stable `project_id`; it does not come from reading inaccessible chat history. The MCP stores project state, versions, deliverables, audit events, recurrence, and capability gaps. In cloud mode, use persistent storage and OAuth so different users cannot share a namespace.

## Brief user-facing audit format

- Clean: `审计通过｜阻塞错误0｜原问题残留0｜修改新增问题0`
- Failed: `审计未通过｜共N项` followed by short numbered items with shot IDs and one-sentence fixes.
- Blocked: one sentence naming the blocker and next action.
