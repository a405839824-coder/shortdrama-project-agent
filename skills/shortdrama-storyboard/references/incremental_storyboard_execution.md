# Incremental storyboard execution protocol 0.5.0 compact master

Use this reference for an existing storyboard version when the requested work can be limited to named episodes, scenes, shots, or audit-confirmed repair targets. It defines execution authority and incremental assembly; it does not weaken any R10 content rule.

## Preserve the fixed quality contract

All paths in this protocol preserve these invariants:

- one final canonical object per artifact version;
- the existing 49 content validators;
- the ordered G0–G13 preventive gates;
- the complete eight-layer independent audit;
- 100% coverage for core events, causal links, character-state changes, protected dialogue, and source treatment when chapter intake applies;
- zero blocking, residual, or introduced issues before a clean final verdict;
- an exact clean independent-audit certificate and human sign-off before archive.

Performance work removes repeated semantic authoring, duplicate Seedance text, batch packaging, background pre-generation, and redundant persistence. It may not remove a required quality conclusion or let production certify itself.

## Keep two resident windows and create repair only when needed

Maintain two resident logical windows for a project audit cycle:

1. `production` creates or changes artifacts and issues production preflight evidence.
2. `independent_audit` consumes an immutable handoff, keeps the artifact read-only, independently recomputes every quality conclusion, and alone issues final pass/fail.

Do not create a third resident window at task start. Create `repair` only when all of the following are true:

- the independent audit returned `fail_current`;
- repair is explicitly authorized by the current work order or by a later user instruction;
- the auditor issued a signed, unexpired, immutable repair package for the exact artifact and audit cycle;
- the repair package identifies the authorized targets, issue codes, protected scope, fingerprints, and required regression scope.

An `audit_only` task may produce findings and a repair package, but it must not activate repair without execution authorization. A clean audit never creates a repair window.

### Exact role matrix

| Role | Activation | Artifact mutation | Permitted commit route | Audit verdict | Exit condition |
|---|---|---:|---|---:|---|
| `production` | Resident project window | Yes, within the user-authorized scope | `commit_storyboard` or production-authorized `commit_storyboard_delta` | No | Production preflight and `production_to_audit` handoff created |
| `independent_audit` | Resident project window, distinct from production | No | None; it may persist audit evidence, certificates, and handoffs only | Yes | Clean certificate, or `audit_to_repair` package when repair is authorized |
| `repair` | Conditional, single-use window created from one immutable repair package | Yes, only within package scope | `commit_storyboard_patch` for eligible non-structural fields, or package-bound full `commit_storyboard` for an affected scene/episode rebuild | No | New version and `repair_to_audit` handoff created; package consumed and window closed |

Role authority must come from server-minted window, actor, work-order, and transport-session bindings. Never trust a caller-supplied role label. Production, audit, and repair bindings must be distinct within the same audit cycle.

A user-requested new change is production work even if an older audit exists. Do not infer `repair` merely from an operation name such as `storyboard_change`; repair authority exists only through the bound immutable repair package.

After repair, send the successor artifact to the original independent-audit window, using the same auditor binding and audit-cycle identity. The auditor verifies the new fingerprint, reruns the required independent checks, and issues the only final verdict. Repair cannot open a replacement auditor or sign its own result.

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

An affected-scene or affected-episode repair rebuild may still be limited conceptually to that project scope, but the 0.5.0 repair authority submits it through the package-bound full commit route. “Full rebuild required” means rebuilding the complete affected semantic unit and its boundaries, not automatically regenerating unrelated episodes.

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

A 0.5.0 delta manifest records affected base shot IDs/UIDs, replacement IDs/UIDs, untouched-shot count and semantic-hash verification, evidence rows refreshed, computed timing/statistics, export projection version, legacy migration count, payload sizes/reduction, and internal merge/export duration. It contains no batch IDs or batch metrics. The request and persisted artifact metadata separately bind the exact base artifact/canonical, scope hash, target and left/right guards; the final result carries the server trace and successor handoff.

Outside the authorized semantic scope, every atomic shot hash must remain identical. The delta route does not accept top-level source, cognition, coverage, chapter, scene-state, protected-dialogue, or other evidence replacement; if any such evidence must change, use the full route. Because exact dialogue is merged into `画面内容` while these upstream evidence sets cannot be replaced by delta, the service must conservatively reject every dialogue or speaker change, even when the caller claims that source mapping or cognition is unchanged. Boundary neighbors are read-only guards and cannot be changed by delta.

For every delta replacement, exact dialogue text, punctuation, order/segmentation, and speaker identity must match the base. Any addition, deletion, rewrite, reorder, split/merge, or speaker reassignment requires the full route for the complete affected scene or episode so source mapping, cognition, protected dialogue, performance chains, and coverage can be rebuilt together. Within an unchanged shot identity and shot count, visual action, expression, camera/movement, listener reaction, delivery that does not change dialogue or cognition, and other non-cognitive execution facts may use delta only when the caller supplies fresh complete shot-bound evidence for every affected atom. `音色` remains independent. Psychological audio remains on the full route because the delta contract has no shot-keyed cognition proof.

For an eligible same-identity replacement, the 0.5.0 service may deterministically change only cumulative duration, total duration, formula/statistics results, the exact ten-column workbook projection, and directly redundant shot-plan bindings. The canonical stores neither `正式分镜母版·字段合并` nor `GPT关键帧生成指令`; they are export-only projections. It also stores no `batches`, Seedance text, Seedance column, or generated-batch sheet. Dialogue/speaker changes and insert, delete, move, split, merge, or renumber operations use the full route. A computed change does not authorize unrelated story, dialogue, cognition, performance, camera, sound, lighting or source-mapping content.

## Store atoms once and project only at export

Generate and merge unique semantic information once. Do not start a delayed/background expansion job after commit or audit.

Each shot uses exactly 27 atoms in this order: `镜头编号｜时长｜累计时长｜功能类型｜场景连续性ID｜镜头类型｜可见主体｜空间站位描述｜起始状态｜机位｜运镜方式｜画面内容｜音色｜心理音轨｜环境音效｜环境动态｜光影｜光源来源｜光源方向｜色温倾向｜生成限制｜转场类型｜切镜/转场提示词｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`.

`画面内容` is the sole atom for visible action, performance, exact dialogue, six-dimensional delivery, punctuation effect, listener reaction, breath/end state and optional Seedance 2.5 single-shot timing stages. `空间站位描述`, `起始状态`, and `音色` remain separate. `光影`, `光源来源`, `光源方向`, and `色温倾向` are four mandatory independent atoms. The retired standalone fields are forbidden on new writes.

The 0.5.0 deterministic outputs are limited to cumulative time, total duration, formulas/statistics, and two export-only projections:

- `正式分镜母版·字段合并`, the only blank-context executable single-shot master;
- `GPT关键帧生成指令`, a visual-only projection with timing, dialogue, lip-sync and audio instructions removed.

The workbook projection has exactly ten columns: `分镜序号｜时长｜累计时长｜功能类型/场景连续性ID｜正式分镜母版·字段合并｜GPT关键帧生成指令｜参考图锚定｜覆盖原文｜原文依据类型｜原文支撑句`. No third long-text projection exists.

The exporter must not invent or reinterpret plot, dialogue, character knowledge, motivation, action, camera intent, performance, sound source, lighting, or continuity. If a required creative fact is absent, export fails and returns the task to semantic generation. Production, audit, repair, cache warm-up and background workers must not pre-generate or persist Seedance prompts or batches.

Run delta merge, final canonicalization, complete validation, deterministic XLSX projection, and handoff creation under one leased successor transaction guarded by compare-and-set. Do not publish the base successor pointer until the candidate artifact exists and its project/base/canonical binding is rechecked. A validation failure creates no formal successor pointer; abort the transaction and do not release a partially exported artifact as valid.

The semantic delta is an input package, not a competing canonical. Only the compact atomic canonical is authoritative for export, audit, repair, cache keys, certificates and sign-off. Persist the delta manifest and projection version with successor metadata; bind the final handoff to the canonical and raw/semantic artifact fingerprint bundle.

## Validate the compact artifact without reduction

Production may run an early semantic preflight to catch source, causality, cognition, boundary, or continuity errors before export. This early check is advisory and cannot replace final validation.

Before `production_to_audit` or `repair_to_audit`:

1. validate every required 27-field formal shot, compile the two deterministic export projections, and compute formulas/statistics;
2. run the complete existing 49-validator R10 content suite on the full successor artifact;
3. run all applicable G0–G13 gates and required chapter coverage checks;
4. verify unchanged-unit semantic hashes and all authorized boundary effects;
5. verify numbering, cumulative time, formulas, total duration, exact ten-column order, removed-field absence, four lighting atoms, and single-shot stage closure globally;
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

On an uncached fingerprint set, this review is an explicit `semantic_audit` submission under `semantic-audit-v2-compact-master`, bound to the canonical hash and exact reviewed shot count. Each of the eight unique layers must carry its own pass/fail state, concise summary, and concrete evidence IDs; every failed layer must have at least one targeted issue with evidence and a repair action, and the conclusion must agree with the layers. The service combines that submission with a freshly recomputed machine report. It must not manufacture an eight-layer pass from zero machine issues or regenerate text during audit. Only a valid exact independent-audit cache hit under the same contract may omit the submission; v1 cache entries are invalidated.

Production preflight, semantic preflight, compact canonicalization/export success, full-table machine validation, repair regression, and cache hits are evidence. None is an independent-audit verdict.

## Record traces and make honest performance claims

Every formal production, delta, audit, and repair result must return a `trace_id` that resolves to an immutable persisted trace for the same project, work order, role binding, audit cycle, artifact, and fingerprints. A completed optimized workflow must expose `fast_path_evidence` that resolves both the production trace and the independent-audit trace against the same canonical and handoff/certificate chain. Missing either trace is `fast_path_incomplete`.

Every plugin trace declares `measurement_scope=server_only`: it measures MCP/service execution but not the model's reading, reasoning, or generation time. End-to-end time may be compared only in a separate caller/orchestrator replay record; it is not written into or presented as a plugin stage trace.

Record applicable stages separately:

- bootstrap/status and source bulk read;
- planning and semantic generation;
- delta compare-and-set and merge;
- compact merge and deterministic export projection;
- full validation and impact regression;
- JSON/XLSX export;
- ledger, certificate, and handoff persistence.

For each stage, record only server-observed wall duration and payload, affected/total shot, preserved semantic-unit, export-projection, legacy-generation-call and cache metrics actually measured. New routes must report `legacy_generation_calls=0`, `seedance_batches_generated=0`, and `model_authored_derived_chars=0`. Never infer model tool-call count, source characters, generation duration or retries from service time. Do not double-count parallel child durations in parent wall time.

List any unmeasured stages. A `server_only` trace may support a claim about tool latency, payload reduction, or deterministic work avoided; it cannot support a claim about total ChatGPT turn time. Do not promise a percentage reduction until comparable traces exist for the same source, scope, rules, service tier, and final output contract.

Trace data must never contain source prose, dialogue, prompt bodies, private download URLs, credentials, OAuth subjects, or other user-identifying content.

## Normal flow

1. Production locks source, scope, base canonical, boundaries, protection, and runtime certificate.
2. It selects full or delta routing and generates only the required semantic content.
3. The service compare-and-sets the base, merges by stable UID, validates the compact canonical, deterministically projects the ten-column workbook, exports a new version without Seedance/batch artifacts, and records the trace.
4. Production issues `production_preflight` plus `production_to_audit`; it reports `待独立审计`.
5. The resident audit window verifies every fingerprint and independently recomputes all eight layers.
6. If clean, the auditor issues the final certificate and waits for human sign-off. Repair is never activated.

For an unchanged-runtime ordinary task, keep execution on the fast-safe path: verify the runtime certificate once, reuse only fingerprint-matched objective records, transfer only the semantic delta, and make the server perform complete validation/export. Do not inspect runtime source or rerun system regression suites. In the audit window, call the signed-handoff audit once and report its compact result; do not regenerate the storyboard, Seedance text, export rows, or echo all rows into chat. Any extra reread, retry or rebuild must have a recorded cache miss, fingerprint mismatch, missing semantic input, validator failure or transport error.

## Failure and repair flow

1. The independent auditor records the immutable issue baseline and returns `fail_current`.
2. For `audit_only`, stop and wait for explicit repair authorization. For authorized `audit_and_repair`, issue one signed repair package and `audit_to_repair` handoff.
3. Create a distinct, single-use repair binding for that package.
4. Use certificate-bound patch only for eligible non-structural fields; otherwise rebuild the complete affected scene or episode through the package-bound full repair route. The production-only delta tool cannot be used by repair authority.
5. The service validates and exports a new compact artifact, records residual and introduced issues, consumes the package, closes repair, and creates `repair_to_audit`.
6. The original audit window verifies the successor and performs the independent final audit.
7. Prefer closure within two repair rounds. At the third recurrence, stop patching and require the defined upstream rebuild.

If CAS, authorization, fingerprint, canonicalization, export projection, outside-scope integrity, trace persistence, or final validation fails, do not publish a successor handoff and do not claim completion. Preserve the last valid artifact and name the exact blocker.

## Legacy and capability fallback

- Keep the full `commit_storyboard` route available for legacy projects and any task that cannot prove a safe delta boundary, but route legacy data through the read-only compact migrator before creating a successor.
- Migrate internal UIDs and retained semantics only into a new compact successor version; never rewrite historical artifacts in place and never write retired fields, batch objects, or Seedance columns into the successor.
- Let independent audit create `audit_intake` for a legitimate external or legacy artifact without a handoff. A present but mismatched handoff remains blocking.
- If `commit_storyboard_delta` or deterministic export projection is unavailable, record the capability gap and use a full commit when authorization and inputs permit. Never simulate a successful delta in chat text.
