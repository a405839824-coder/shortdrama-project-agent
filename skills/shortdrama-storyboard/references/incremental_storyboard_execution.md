# Incremental storyboard execution protocol 0.4.0

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

Performance work may reduce repeated reading, model-authored duplication, transferred bytes, or deterministic recomputation. It may not remove a required quality conclusion or let production certify itself.

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
| User-authorized change limited to exact existing episodes/scenes/shots with a valid base and protected scope | Production `commit_storyboard_delta` |
| Audit-confirmed non-structural field replacement on stable shots | `commit_storyboard_patch` with the exact certificate and repair package |
| Audit-confirmed boundary, cognition, source, shot identity/order/timing, scene-topology, or core-plan issue limited to an affected scene or episode | Repair full `commit_storyboard` for the complete affected scene/episode with the exact repair package |
| Stale base, unresolved boundary, insufficient authorization, fingerprint mismatch, or protected-scope conflict | Block; do not fall back silently |

An affected-scene or affected-episode repair rebuild may still be limited conceptually to that project scope, but the 0.4.0 repair authority submits it through the package-bound full commit route. “Full rebuild required” means rebuilding the complete affected semantic unit and its boundaries, not automatically regenerating unrelated episodes.

Fall back to a full commit when the server cannot prove a closed impact boundary, preserve outside-scope semantics, or deterministically rebuild all affected consumers.

## Use stable identities and compare-and-set

Treat the displayed `镜头编号` as a caller-supplied presentation label distinct from identity. The server validates it but does not silently derive or renumber it. Every canonical shot must have an internal immutable `shot_uid` that survives permitted full-route renumbering and version changes.

- Resolve delta operations by `shot_uid`, not by a mutable display number.
- Preserve existing UIDs for unchanged shots.
- Inserted shots require the full route, which mints new server-owned UIDs before persistence.
- Deletion, split, merge, movement or renumbering requires the full route; never recycle a removed UID.
- For a legacy 0.3.x artifact without UIDs, derive them deterministically from its episode/scene/display structural identity, persist them in the successor canonical, and leave the historical artifact unchanged.

Before merging, compare-and-set all required base identities:

- base artifact ID, expected canonical hash and raw-artifact fingerprint;
- the current server runtime-test certificate and work-order production authority;
- exact target scope, boundary neighbors, changed fields and protected scope.

After materialization, recompute and bind the complete source, boundary, parser, schema, runtime, rule, validator, project-anchor and target-model fingerprint bundle in the successor handoff. These final handoff bindings are not substitutes for the pre-merge base CAS.

Reject a stale or mismatched base. Never rebase a model-authored delta silently onto a newer canonical.

### Delta scope contract

A 0.4.0 delta manifest records the affected base shot IDs/UIDs, replacement IDs/UIDs, removed and replacement batch IDs, untouched-shot count and semantic-hash verification, evidence rows refreshed, derived fields recomputed, legacy UID migration count, payload sizes/reduction and internal merge/materialization duration. The request and persisted artifact metadata separately bind the exact base artifact/canonical, scope hash, target and left/right guards; the final result carries the server trace and successor handoff.

Outside the authorized semantic scope, every non-derived shot hash must remain identical. In 0.4.0 the delta route does not accept top-level source, cognition, coverage, chapter, scene-state, protected-dialogue, or other evidence replacement; if any such evidence must change, use the full route. Boundary neighbors are read-only guards and cannot be changed by delta.

For an eligible same-identity replacement, the 0.4.0 materializer may change only these declared deterministic values: replacement batch UID, cumulative duration, replacement batch duration, verified replacement membership, total duration, compiled independent Seedance prompt and directly redundant shot-plan bindings. Displayed shot/batch IDs remain caller-supplied and must already be unique and valid. Insert, delete, move, split, merge or renumber uses the full route. Any derived change does not authorize unrelated story, dialogue, cognition, performance, camera, sound, lighting or source-mapping content.

## Delay only mechanical materialization

Generate and merge the unique semantic information first. Delay repeated mechanical expansion until the semantic delta and its impact boundary are stable.

Semantic inputs include every creative or story-bearing decision: source mapping, episode/scene/beat placement, stable shot identity, duration, narrative function, visibility, camera, movement, blocking, visual content, performance, dialogue and voice, cognition, psychological audio, environmental people and sound ecology, lighting, transitions, anchors, generation restrictions, and start/end state.

The 0.4.0 mechanical derived outputs are limited to:

- cumulative time and total duration;
- server-owned stable batch UID, batch duration, membership, and shot-to-batch mapping verification;
- independent Seedance 2.5 batch text assembled in the declared field order with explicit anchors and no cross-context shorthand;

The materializer must not invent or reinterpret plot, dialogue, character knowledge, motivation, action, camera intent, performance, sound source, lighting, or continuity. If a required creative fact is absent, materialization fails and returns the task to semantic generation.

Run delta merge, mechanical materialization, final canonicalization, complete validation, JSON/XLSX export, and handoff creation under one leased successor transaction guarded by compare-and-set. Do not publish the base successor pointer until the candidate artifact exists and its project/base/canonical binding is rechecked. A validation failure creates no formal successor pointer; abort the transaction and do not release a partially materialized artifact as valid.

The semantic delta is an input package, not a competing canonical. Only the fully materialized final canonical is authoritative for export, audit, repair, cache keys, certificates and sign-off. Persist the delta manifest and materializer version with the successor metadata; bind the final handoff to the complete canonical and raw/semantic artifact fingerprint bundle.

## Validate the final materialized artifact without reduction

Production may run an early semantic preflight to catch source, causality, cognition, boundary, or continuity errors before long text is expanded. This early check is advisory and cannot replace final validation.

Before `production_to_audit` or `repair_to_audit`:

1. materialize every required formal shot, mother-template field, batch, formula, and statistic;
2. run the complete existing 49-validator R10 content suite on the full successor artifact;
3. run all applicable G0–G13 gates and required chapter coverage checks;
4. verify unchanged-unit semantic hashes and all authorized boundary effects;
5. verify numbering, cumulative time, formulas, total duration, and batch mapping globally;
6. save a new canonical JSON and XLSX version without overwriting the base;
7. issue only a production-preflight result and immutable handoff.

The independent auditor then verifies the handoff fingerprints and recomputes all eight layers on the final materialized artifact:

1. source and protected dialogue;
2. camera and visibility;
3. spatial and seven-field continuity;
4. performance chains;
5. shot rhythm, duration, and transitions;
6. Seedance blank-context independence and model contract;
7. prohibitions, public-scene people/audio ecology, and audiovisual sources;
8. numbering, formulas, statistics, residuals, and introduced issues.

Production preflight, semantic preflight, materializer success, full-table machine validation, repair regression, and cache hits are evidence. None is an independent-audit verdict.

## Record traces and make honest performance claims

Every formal production, delta, audit, and repair result must return a `trace_id` that resolves to an immutable persisted trace for the same project, work order, role binding, audit cycle, artifact, and fingerprints.

Every plugin trace declares `measurement_scope=server_only`: it measures MCP/service execution but not the model's reading, reasoning, or generation time. End-to-end time may be compared only in a separate caller/orchestrator replay record; it is not written into or presented as a plugin stage trace.

Record applicable stages separately:

- bootstrap/status and source bulk read;
- planning and semantic generation;
- delta compare-and-set and merge;
- mechanical materialization;
- full validation and impact regression;
- JSON/XLSX export;
- ledger, certificate, and handoff persistence.

For each stage, record only server-observed wall duration and the payload, affected/total shot/batch, preserved semantic-unit and cache metrics that the route actually measures. Never infer model tool-call count, source characters, generation duration or retries from service time. Do not double-count parallel child durations in parent wall time.

List any unmeasured stages. A `server_only` trace may support a claim about tool latency, payload reduction, or deterministic work avoided; it cannot support a claim about total ChatGPT turn time. Do not promise a percentage reduction until comparable traces exist for the same source, scope, rules, service tier, and final output contract.

Trace data must never contain source prose, dialogue, prompt bodies, private download URLs, credentials, OAuth subjects, or other user-identifying content.

## Normal flow

1. Production locks source, scope, base canonical, boundaries, protection, and runtime certificate.
2. It selects full or delta routing and generates only the required semantic content.
3. The service compare-and-sets the base, merges by stable UID, mechanically materializes all consumers, runs complete validation, exports a new version, and records the trace.
4. Production issues `production_preflight` plus `production_to_audit`; it reports `待独立审计`.
5. The resident audit window verifies every fingerprint and independently recomputes all eight layers.
6. If clean, the auditor issues the final certificate and waits for human sign-off. Repair is never activated.

For an unchanged-runtime ordinary task, keep execution on the fast-safe path: verify the runtime certificate once, reuse only fingerprint-matched objective records, transfer only the semantic delta, and make the server perform full materialization/validation/export. Do not inspect runtime source or rerun system regression suites. In the audit window, call the signed-handoff audit once and report its compact result; do not regenerate the storyboard or echo all rows into chat. Any extra reread, retry or rebuild must have a recorded cache miss, fingerprint mismatch, missing semantic input, validator failure or transport error.

## Failure and repair flow

1. The independent auditor records the immutable issue baseline and returns `fail_current`.
2. For `audit_only`, stop and wait for explicit repair authorization. For authorized `audit_and_repair`, issue one signed repair package and `audit_to_repair` handoff.
3. Create a distinct, single-use repair binding for that package.
4. Use certificate-bound patch only for eligible non-structural fields; otherwise rebuild the complete affected scene or episode through the package-bound full repair route. The production-only delta tool cannot be used by repair authority.
5. The service materializes and validates a new full artifact, records residual and introduced issues, consumes the package, closes repair, and creates `repair_to_audit`.
6. The original audit window verifies the successor and performs the independent final audit.
7. Prefer closure within two repair rounds. At the third recurrence, stop patching and require the defined upstream rebuild.

If CAS, authorization, fingerprint, materialization, outside-scope integrity, trace persistence, or final validation fails, do not publish a successor handoff and do not claim completion. Preserve the last valid artifact and name the exact blocker.

## Legacy and capability fallback

- Keep the full `commit_storyboard` route available for 0.3.x projects and any task that cannot prove a safe delta boundary.
- Migrate internal UIDs and new lifecycle metadata only into a new successor version; never rewrite historical artifacts in place.
- Let independent audit create `audit_intake` for a legitimate external or legacy artifact without a handoff. A present but mismatched handoff remains blocking.
- If `commit_storyboard_delta` or deterministic materialization is unavailable, record the capability gap and use a full commit when authorization and inputs permit. Never simulate a successful delta in chat text.
