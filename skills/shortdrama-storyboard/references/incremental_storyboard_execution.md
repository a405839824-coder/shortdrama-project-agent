# Incremental storyboard execution protocol 0.5.5 compact master

Use this reference for an existing storyboard version when the requested work can be limited to named episodes, scenes, shots, or audit-confirmed repair targets. It defines execution authority and incremental assembly; it does not weaken any R10 content rule.

## Preserve the fixed quality contract

All paths in this protocol preserve these invariants:

- one final canonical object per artifact version;
- the existing 50 content validators;
- the ordered G0–G13 preventive gates;
- the THG-01–THG-17 template hard gates, complete N-1 adjacent-pair proof, dialogue-shot voice-field proof, source-fact dialogue proof, and actual multiline XLSX round-trip;
- the complete eight-layer independent audit;
- the mandatory `get_agent_status` → `migrate_project_state` when required → `query_project_issues` → per-scene `preflight_storyboard_draft` → signed full `preflight_storyboard_draft` → certificate-bound `commit_storyboard` → `audit_storyboard` sequence;
- a first-draft grade of overall correctness and direct usability at least 98%, each episode at least 96%, new local issues at most 2%, and zero hard errors, major issues, known recurrences, or systemic duplicate patterns;
- 100% coverage for core events, causal links, character-state changes, protected dialogue, and source treatment when chapter intake applies;
- zero blocking, residual, or introduced issues before a clean final verdict;
- an exact clean independent-audit certificate and human sign-off before archive.

Performance work removes repeated semantic authoring, duplicate Seedance text, batch packaging, background pre-generation, and redundant persistence. It may not remove a required quality conclusion or let production certify itself.

## Keep independent server authorities; any user window may execute

Any trusted window in the same authenticated namespace may create or continue every in-scope stage, including production, independent audit, repair and export from one window. Preserve existing authorization within its original scope without asking again on window change. Maintain two separate server authorities for a project audit cycle:

1. `production` creates or changes artifacts and issues production preflight evidence.
2. `independent_audit` consumes an immutable handoff, keeps the artifact read-only, independently recomputes every quality conclusion, and alone issues final pass/fail.

Do not pre-create a repair authority at task start. Create server `repair` only when all of the following are true:

- the independent audit returned `fail_current`;
- repair is explicitly authorized by the current work order or by a later user instruction;
- the auditor issued a signed, unexpired, immutable repair package for the exact artifact and audit cycle;
- that package belongs to the newest completed failed event in the immutable root/successor re-audit lineage; no later completed audit supersedes it;
- the repair package identifies the authorized targets, issue codes, protected scope, fingerprints, and required regression scope.

An `audit_only` task may produce findings and a repair package, but it must not activate repair without execution authorization. A clean audit never creates a repair authority; no user window switch is required.

### Exact role matrix

| Role | Activation | Artifact mutation | Permitted commit route | Audit verdict | Exit condition |
|---|---|---:|---|---:|---|
| `production` | Authorized server work order and authority root | Yes, within the user-authorized scope | `commit_storyboard` or production-authorized `commit_storyboard_delta` | No | Production preflight and `production_to_audit` handoff created |
| `independent_audit` | Distinct read-only server work order and authority root | No | None; it may persist audit evidence, certificates, and handoffs only | Yes | Clean certificate, or `audit_to_repair` package when repair is authorized |
| `repair` | Conditional server authority bound to one immutable repair package | Yes, only within package scope | `commit_storyboard_patch` for eligible non-structural fields, or package-bound full `commit_storyboard` for an affected scene/episode rebuild | No | New version and `repair_to_audit` handoff created; package consumed and repair authority completed |

Role authority must come from server-minted authority-root, window, actor, and work-order bindings. Never trust a caller-supplied role label. Production, audit, and repair server identities must be distinct within the same audit cycle, while the same authenticated user window may request each identity. The transport session is request provenance and namespace access, not role permission.

A user-requested new change is production work even if an older audit exists. Do not infer `repair` merely from an operation name such as `storyboard_change`; repair authority exists only through the bound immutable repair package.

After repair, send the successor artifact to the original independent-audit authority root, using the same auditor binding and audit-cycle identity. The auditor verifies the new fingerprint, reruns the required independent checks, and issues the only final verdict. Repair cannot open a replacement auditor or sign its own result.

## Select full, delta, or certificate-bound patch execution

Use the smallest safe route, not the smallest payload at any cost.

| Situation | Required route |
|---|---|
| No valid base canonical, competing canonicals, legacy input that cannot be normalized, or project-wide structural rewrite | Full `commit_storyboard` |
| New full episode/project generation | Full `commit_storyboard` |
| User-authorized stable-UID/same-count non-dialogue visual or non-cognitive execution change, limited to exact existing shots with a valid base, unchanged speaker/dialogue evidence, and protected scope | Production `commit_storyboard_delta` |
| Any dialogue text, punctuation, order/segmentation, or speaker addition, deletion, rewrite, split/merge, reorder, or reassignment | Full `commit_storyboard` for the complete affected scene or episode |
| Audit-confirmed non-structural field replacement on stable shots | `commit_storyboard_patch` with the exact certificate and repair package |
| Audit-confirmed boundary, cognition, source, shot identity/order/timing, scene-topology, or core-plan issue limited to an affected scene or episode | Repair full `commit_storyboard` for the complete affected scene/episode with the exact repair package |
| Stale base, unresolved boundary, insufficient authorization, fingerprint mismatch, or protected-scope conflict | Block; do not fall back silently |

An affected-scene or affected-episode repair rebuild may still be limited conceptually to that project scope, but the 0.5.5 repair authority submits it through the package-bound full commit route. “Full rebuild required” means rebuilding the complete affected semantic unit and its boundaries, not automatically regenerating unrelated episodes.

Fall back to a full commit when the server cannot prove a closed impact boundary, preserve outside-scope semantics, or deterministically rebuild all affected consumers.

## Use stable identities and compare-and-set

Treat the displayed `镜头编号` as a caller-supplied presentation label distinct from identity. The server validates it but does not silently derive or renumber it. Every canonical shot must have an internal immutable `shot_uid` that survives permitted full-route renumbering and version changes.

- Resolve delta operations by `shot_uid`, not by a mutable display number.
- Preserve existing UIDs for unchanged shots.
- Inserted shots require the full route, which mints new server-owned UIDs before persistence.
- Deletion, split, merge, movement or renumbering requires the full route; never recycle a removed UID.
- For a legacy 0.3.x/0.4.x artifact without UIDs, derive them deterministically from its episode/scene/display structural identity, migrate legacy fields and batches read-only into one compact successor, and leave the historical artifact unchanged. Never write retired fields into the successor.

Before merging, compare-and-set all required base identities:

- base artifact ID, expected canonical hash and raw-artifact fingerprint;
- the current server runtime-test certificate and work-order production authority;
- exact target scope, boundary neighbors, changed fields and protected scope.

After compact canonicalization, validation and export projection, recompute and bind the complete source, boundary, parser, schema, runtime, rule, validator, project-anchor and target-model fingerprint bundle in the successor handoff. These final handoff bindings are not substitutes for the pre-merge base CAS.

Reject a stale or mismatched base. Never rebase a model-authored delta silently onto a newer canonical.

### Delta scope contract

A 0.5.5 delta manifest records affected base shot IDs/UIDs, replacement IDs/UIDs, untouched-shot count and semantic-hash verification, evidence rows refreshed, computed timing/statistics, export projection version, legacy migration count, payload sizes/reduction, and internal merge/export duration. It contains no batch IDs or batch metrics. The request and persisted artifact metadata separately bind the exact base artifact/canonical, scope hash, target and left/right guards; the final result carries the server trace and successor handoff.

Outside the authorized semantic scope, every atomic shot hash must remain identical. The delta route does not accept top-level source, cognition, coverage, chapter, scene-state, protected-dialogue, or other evidence replacement; if any such evidence must change, use the full route. Because exact dialogue is merged into `画面内容` while these upstream evidence sets cannot be replaced by delta, the service must conservatively reject every dialogue or speaker change, even when the caller claims that source mapping or cognition is unchanged. Boundary neighbors are read-only guards and cannot be changed by delta.

For every delta replacement, exact dialogue text, punctuation, order/segmentation, and speaker identity must match the base. Any addition, deletion, rewrite, reorder, split/merge, or speaker reassignment requires the full route for the complete affected scene or episode so source mapping, cognition, protected dialogue, performance chains, and coverage can be rebuilt together. Within an unchanged shot identity and shot count, visual action, expression, camera/movement, listener reaction, delivery that does not change dialogue or cognition, and other non-cognitive execution facts may use delta only when the caller supplies fresh complete shot-bound evidence for every affected atom. `音色` remains independent. Psychological audio remains on the full route because the delta contract has no shot-keyed cognition proof.

For an eligible same-identity replacement, the 0.5.5 service may deterministically change only episode-local cumulative duration, batch total duration, formula/statistics results, the exact R10-SPC-1 ten-column workbook projection, and directly redundant shot-plan bindings. The canonical stores neither `正式分镜母版` nor `GPT关键帧生成指令`; they are export-only projections. It also stores no `batches`, Seedance text, Seedance column, or generated-batch sheet. Dialogue/speaker changes and insert, delete, move, split, merge, or renumber operations use the full route. A computed change does not authorize unrelated story, dialogue, cognition, performance, camera, sound, lighting or source-mapping content.

## Store atoms once and project only at export

Generate and merge unique semantic information once. Do not start a delayed/background expansion job after commit or audit.

Each shot uses exactly 27 atoms in this order: `镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`.

`画面内容` is the sole atom for visible action, performance, exact dialogue, visible synchronized delivery cues, listener reaction, breath/end state and the three required R10-SPC-1 Seedance 2.5 stages. The full six-dimensional delivery specification remains only in `音色`, not duplicated in picture content. `空间站位描述`, `起始状态`, and `音色` remain separate. `光影`, `光源来源`, `光源方向`, and `色温倾向` are four mandatory independent atoms; color temperature is qualitative and contains no Kelvin/K value, while source and direction contain no alternative-choice wording. The retired standalone fields are forbidden on new writes.

The 0.5.5 deterministic outputs are limited to cumulative time, total duration, formulas/statistics, and two export-only projections:

- `正式分镜母版`, the only blank-context executable single-shot master, beginning with the exact EP ID/duration/cumulative line and paragraph order defined by `strict_prompt_contract.md`; every shot's `生成限制` explicitly contains `9:16竖屏` and uses `禁止额外配乐` rather than the literal `BGM`;
- `GPT关键帧生成指令`, beginning with `画幅：9:16竖屏` and projected only from the static whitelist, with timing, motion, transition, dialogue, lip-sync and audio instructions removed.

The workbook projection has exactly ten columns: `分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`. No third long-text projection exists. New display IDs use `EP##-###`; cumulative time resets at each episode boundary while batch total duration remains the sum of all shot durations. Export is blocked until THG-01–THG-17, the complete N-1 adjacent-pair proof, fixed voice-template validation, source-fact dialogue validation, T2I/lighting validation, and the actual multiline XLSX round-trip pass.

The exporter must not invent or reinterpret plot, dialogue, character knowledge, motivation, action, camera intent, performance, sound source, lighting, or continuity. If a required creative fact is absent, export fails and returns the task to semantic generation. Production, audit, repair, cache warm-up and background workers must not pre-generate or persist Seedance prompts or batches.

Run delta merge, final canonicalization, complete validation, deterministic XLSX projection, and handoff creation under one leased successor transaction guarded by compare-and-set. Do not publish the base successor pointer until the candidate artifact exists and its project/base/canonical binding is rechecked. A validation failure creates no formal successor pointer; abort the transaction and do not release a partially exported artifact as valid.

The semantic delta is an input package, not a competing canonical. Only the compact atomic canonical is authoritative for export, audit, repair, cache keys, certificates and sign-off. Persist the delta manifest and projection version with successor metadata; bind the final handoff to the canonical and raw/semantic artifact fingerprint bundle.

## Validate the compact artifact without reduction

Production must run the 0.5.5 draft preflight before export. First call `query_project_issues` and bind the active prevention profile. Then call `preflight_storyboard_draft(scope_kind="scene")` after every scene; any failure is written to the issue ledger, creates no artifact/version/XLSX/projection/handoff, and must be repaired before the next scene is accepted. After all scenes are clean, call `preflight_storyboard_draft(scope_kind="full")` on the exact final canonical and pass its signed commit-ready `draft_preflight_id` to `commit_storyboard`. The signed binding covers project, work-order production authority, complete canonical, rules, validator, prevention profile, and scope; another authenticated window may continue the same authority, while a mismatched or stale certificate blocks commit.

The first submitted full draft is graded immutably. Grade A requires overall correctness and direct usability of at least 98%, every episode at least 96%, new local issue rate at most 2%, and zero hard errors, major issues, known-issue recurrences, or systemic duplicate patterns. This grade measures upstream prevention quality; it is not permission to ship the remaining percentage. Full preflight is commit-ready only after all detected issues are gone, and final independent audit requires all eight layers at 100% with zero blocking, residual, or introduced issues.

Before `production_to_audit` or `repair_to_audit`:

1. validate every required 27-field formal shot, compile the two deterministic export projections, and compute formulas/statistics;
2. run the complete existing 50-validator R10 content suite on the full successor artifact;
3. run all applicable G0–G13 gates and required chapter coverage checks;
4. verify unchanged-unit semantic hashes and all authorized boundary effects;
5. verify numbering, cumulative time, formulas, total duration, exact ten-column order, removed-field absence, four lighting atoms, and internal single-shot timing closure and untimed picture display globally;
6. save a new canonical JSON and XLSX version without overwriting the base;
7. issue only a production-preflight result and immutable handoff.

The independent auditor then verifies the handoff fingerprints and recomputes all eight layers on the final compact artifact and its deterministic export projection:

1. source and protected dialogue;
2. camera and visibility;
3. spatial and seven-field continuity;
4. performance chains;
5. shot rhythm, duration, and transitions;
6. compact formal-master blank-context independence, single-shot timing and target-model contract;
7. prohibitions, public-scene people/audio ecology, and audiovisual sources;
8. numbering, formulas, statistics, residuals, and introduced issues.

On an uncached fingerprint set, this review is an explicit `semantic_audit` submission under `semantic-audit-v8-adjacent-continuity-structured-voice-conversation-t2i-indoor-lighting`, bound to the canonical hash, exact reviewed shot count, `reviewed_adjacent_pair_count=N-1`, and the SHA-256 of the canonical ordered pair list. Each of the eight unique layers must carry its own pass/fail state, concise summary, and concrete evidence IDs; the spatial layer lists every `pair:left->right` exactly once and in canonical order, the performance layer audits the timed eye/blink contract, the prohibitions/audio layer lists every dialogue shot's `field:shot_id.音色` exactly once in canonical order, and the source/dialogue layer first lists every `source-unit:unit_id` in Source Ledger order and then every source-fact dialogue target's `field:shot_id.画面内容` exactly once in canonical order. The camera-visibility layer lists all four lighting fields for every shot in exact canonical and field order and verifies ordinary-interior clarity/door-window state or a physically grounded atmospheric exception. Every failed layer must have at least one targeted issue with evidence and a repair action, and the conclusion must agree with the layers. The service combines that submission with a freshly recomputed machine report. It must not manufacture an eight-layer pass from zero machine issues or regenerate text during audit. Only a valid exact independent-audit cache hit under the same v8 contract may omit the submission; v1–v7 cache entries remain history but are invalid for active release.

Production preflight, semantic preflight, compact canonicalization/export success, full-table machine validation, repair regression, and cache hits are evidence. None is an independent-audit verdict.

## Record traces and make honest performance claims

Every formal production, delta, audit, and repair result must return a `trace_id` that resolves to an immutable persisted trace for the same project, work order, role binding, audit cycle, artifact, and fingerprints. A completed optimized workflow must expose `fast_path_evidence` that resolves both the production trace and the independent-audit trace against the same canonical and handoff/certificate chain. Missing either trace is `fast_path_incomplete`.

Every plugin trace declares `measurement_scope=server_only`: it measures MCP/service execution but not the model's reading, reasoning, or generation time. When the user explicitly requests a native ChatGPT no-API timing investigation, plugin 0.5.3 or newer may additionally record the five ordered `mark_performance_stage` checkpoints defined in `no_api_performance_diagnostics.md`. That separate signed record declares `measurement_scope=tool_bracketed_chat`; it brackets work between tool calls but is not exact user-click-to-render end-to-end telemetry and never changes a plugin stage trace.

Record applicable stages separately:

- bootstrap/status and source bulk read;
- model planning and semantic generation remain unmeasured in ordinary plugin traces; in an explicit diagnostic they appear only as the `storyboard_planning` and `canonical_authorship` gaps between server-received markers;
- delta compare-and-set and merge;
- compact merge and deterministic export projection;
- full validation and impact regression;
- JSON/XLSX export;
- ledger, certificate, and handoff persistence.

For each stage, record only server-observed wall duration and payload, affected/total shot, preserved semantic-unit, export-projection, legacy-generation-call and cache metrics actually measured. New routes must report `legacy_generation_calls=0`, `seedance_batches_generated=0`, and `model_authored_derived_chars=0`. Never infer model tool-call count, source characters, generation duration or retries from service time. Do not double-count parallel child durations in parent wall time.

List any unmeasured stages. A `server_only` trace may support a claim about tool latency, payload reduction, or deterministic work avoided; it cannot support a claim about total ChatGPT turn time. A `tool_bracketed_chat` diagnostic may identify the largest observed interval but cannot isolate provider queueing, hidden reasoning, token generation rate, client transport, or final rendering. Do not promise a percentage reduction until comparable traces exist for the same source, scope, rules, service tier, window type, reasoning mode declaration, cache state, and final output contract.

Trace data must never contain source prose, dialogue, prompt bodies, private download URLs, credentials, OAuth subjects, or other user-identifying content.

## Normal flow

1. Production calls status; if required, authorized migration runs first and status is revalidated.
2. Production locks source, scope, base canonical, boundaries, protection, runtime certificate, and the current issue-pattern prevention profile.
3. It selects full or delta routing, generates only the required semantic content, and runs mandatory scene draft preflight after each scene. The server first derives the source-dialogue v6 authority manifest only from the server-registered source file after payload SHA, Hub authority entry and artifact-metadata role cross-bind; candidate inventory, `source_lock`, ledger and anchors cannot authorize parsing. The manifest SHA directly binds source fingerprint, role, occurrences and grammar SHA, while grammar SHA transitively binds implementation source; any closed-grammar ambiguity fails the scene/full preflight without a formal artifact.
4. For the full route, the service signs a clean full-draft preflight; production passes that exact certificate into commit. Failed drafts remain ledger evidence only and never create formal artifacts.
5. The service compare-and-sets the base, merges by stable UID, validates the compact canonical, deterministically projects the ten-column workbook, exports a new version without Seedance/batch artifacts, and records the trace.
6. Production issues `production_preflight` plus `production_to_audit`; its immutable workbook reports `PENDING_AUDIT` / `待独立审计` with null issue counts and trace.
7. The independent audit authority verifies every fingerprint and independently recomputes all eight layers.
8. If clean, the auditor creates a new immutable signed-audit-overlay artifact/successor workbook with the final certificate and waits for human sign-off; it never edits the production XLSX in place. Repair is never activated.

Production owns semantic authorship and pre-commit correction. Deterministic service code owns schema/gate execution, projections, migration CAS, certificate bindings and issue-pattern learning. Independent audit owns read-only omission detection and the final semantic verdict; it must not regenerate or repair content, and it must not be used as the routine cleaner for known defects.

For an unchanged-runtime ordinary task, keep execution on the fast-safe path: verify the runtime certificate once, reuse only fingerprint-matched objective records, transfer only the semantic delta, and make the server perform complete validation/export. Do not inspect runtime source or rerun system regression suites. Under the read-only audit authority, call the signed-handoff audit once and report its compact result; do not regenerate the storyboard, Seedance text, export rows, or echo all rows into chat. Any extra reread, retry or rebuild must have a recorded cache miss, fingerprint mismatch, missing semantic input, validator failure or transport error.

## Failure and repair flow

1. The independent auditor records the immutable issue baseline and returns `fail_current`. Every blocking issue must resolve to a non-empty canonical shot set; explicit `shot_ids` are authoritative and an optional location must resolve to the same set.
2. For `audit_only`, stop and wait for explicit repair authorization. For authorized `audit_and_repair`, issue one signed repair package and `audit_to_repair` handoff.
3. Create a distinct, single-use repair binding for that package.
4. Use certificate-bound patch only for eligible non-structural fields; otherwise rebuild the complete affected scene or episode through the package-bound full repair route. The production-only delta tool cannot be used by repair authority.
5. The service validates and exports a new compact artifact, records residual and introduced issues, consumes the package, closes repair, and creates `repair_to_audit`.
6. The original audit authority verifies the successor and performs the independent final audit through a fresh server-minted single-use successor handoff. A fail cannot turn into PASS on the same file/source_version/artifact/canonical bindings.
7. Prefer closure within two repair rounds. At the third recurrence, stop patching and require the defined upstream rebuild.

On restart, resume only the exact signed lineage and normalized semantic submission. Normalize only a valid 64-hex caller `semantic_audit.canonical_sha256` to lowercase; a hex-case-only replay returns the same signed receipt without another successor, event, certificate or round, and no other semantic/evidence/identity field is case-normalized. An event-only half-write may deterministically recover its missing certificate after all event/report/handoff/trace/cache/fingerprint/result/issue/summary fields match. The certificate content itself must exactly match that deterministic binding: a mismatched certificate re-signed with a valid service HMAC, certificate-only, different-semantic, stale-package or cross-lineage state fails closed with zero authority/projection mutation. A completed signed receipt is a read-only idempotent no-op. Recover a missing repair-package or Hub projection only inside the same storage lock after revalidating the latest lineage, exact certificate HMAC and fenced single-finisher claim by CAS; if authority advances, the stale replay neither writes a projection nor returns the old verdict.

If CAS, authorization, fingerprint, canonicalization, export projection, outside-scope integrity, trace persistence, or final validation fails, do not publish a successor handoff and do not claim completion. Preserve the last valid artifact and name the exact blocker.

## Legacy and capability fallback

- Keep the full `commit_storyboard` route available for legacy projects and any task that cannot prove a safe delta boundary, but route legacy data through the read-only compact migrator before creating a successor.
- Migrate internal UIDs and retained semantics only into a new compact successor version; never rewrite historical artifacts in place and never write retired fields, batch objects, or Seedance columns into the successor.
- Let independent audit create `audit_intake` for a legitimate external or legacy artifact without a handoff. A present but mismatched handoff remains blocking.
- If `commit_storyboard_delta` or deterministic export projection is unavailable, record the capability gap and use a full commit when authorization and inputs permit. Never simulate a successful delta in chat text.
