# R10 full-project storyboard Agent

Use this policy with `V16.3-148-R10-StableRuntime`, `R10-PG1`, and the issue policy in `agent_runtime.md`. The full-project profile is `R10-PROJECT-AGENT-1`.

## Contents

1. Shared project state
2. Intent and execution modes
3. Universal task lifecycle
4. Script and story change workflow
5. Storyboard generation and propagation
6. Production workflows
7. Audit, repair, and learning
8. Capability-gap handling
9. User-facing behavior

## Route authoritative project state

In MCP plugin mode, the authenticated plugin store for the exact `project_id` is the operational source of truth. Use its hub, work orders, deliverables, handoffs, audit events and issue ledger through the tools; do not separately fetch or rewrite the global Library copies for an ordinary content task. This avoids duplicate reads and conflicting state while preserving the same machine gates.

An older valid plugin certificate does not certify R10-SPC-1 unless status explicitly advertises that output contract. Such a service may still coordinate project state, but its workbook/projection is not deliverable as current output. When the canonical is available, run the bundled deterministic R10-SPC-1 validation/export adapter and persist a compliant successor; otherwise record the exact plugin capability gap and stop without claiming the Agent update reached the server exporter.

For plugin 0.5.14, `migration_required` is a recovery state, not an executable production state. Only an authorized `rule_improvement` or `version_management` work order may call `migrate_project_state`: plan first, verify schema compatibility plus before/after state hashes, then execute with compare-and-set. Preserve every historical artifact byte and canonical fingerprint as read-only evidence. Re-run status after migration; do not begin ordinary content work until the hub and issue ledger both validate under the current service.

Before any new full storyboard or complete scene/episode rebuild, execute the fixed generation-first chain: `get_agent_status` → required `migrate_project_state` → `query_project_issues` and prevention-profile binding → `preflight_storyboard_draft(scope_kind="scene")` after every scene → signed `preflight_storyboard_draft(scope_kind="full")` on the exact final canonical → `commit_storyboard` with that `draft_preflight_id` → immutable production handoff → separate `audit_storyboard`. A failed draft is learned as a ledger occurrence but produces no artifact, version, XLSX, deterministic projection, successor pointer, or handoff.

Grade the immutable first full draft before repair: overall correctness and direct usability must each be at least 98%, every episode at least 96%, and new local issues at most 2%; hard errors, major issues, known-issue recurrences, and systemic duplicate patterns must each equal zero. This threshold measures prevention quality and never suppresses an issue. A full preflight becomes commit-ready only after every detected issue is cleared. The independent audit remains a read-only omission defense and final release requires 100% pass across all eight layers with blocking, residual, and introduced issues all zero.

Resolve these exact Library titles only for standalone skill operation, explicit shared-ledger work, system/rule architecture updates, or final archival synchronization:

- `分镜项目Agent_项目中枢.json`: current project state, work orders, change requests, deliverables, decisions, capability gaps, and version history.
- `分镜项目Agent_问题台账.json`: audit events, issue recurrence, statistics, rule candidates, and sign-offs.

On those Library-backed routes, validate the project hub with `python scripts/project_agent.py validate --hub HUB.json` and the issue ledger with `python scripts/storyboard_agent.py validate --ledger LEDGER.json`. Replace the same Library identities after accepted updates. If either write fails, preserve a local pending record and explicitly report which shared state is not synchronized.

Operate the project hub through these deterministic commands after preparing the corresponding JSON request or record:

```bash
python scripts/project_agent.py init --output HUB.json --project-name PROJECT
python scripts/project_agent.py create-task --hub HUB.json --request WORK_ORDER.json
python scripts/project_agent.py create-change --hub HUB.json --request CHANGE_REQUEST.json
python scripts/project_agent.py record-deliverable --hub HUB.json --record DELIVERABLE.json
python scripts/project_agent.py record-validation --hub HUB.json --record VALIDATION.json
python scripts/project_agent.py transition --hub HUB.json --work-order-id ID --to STATE
python scripts/project_agent.py signoff --hub HUB.json --work-order-id ID --decision approve
python scripts/project_agent.py gap --hub HUB.json --record CAPABILITY_GAP.json
python scripts/project_agent.py summary --hub HUB.json
```

Do not use the hub script to edit the actual script, storyboard, image, or audio artifact. It controls scope, authorization, state, versions, deliverables, validation, and sign-off; perform the domain work through the appropriate file or media workflow.

Do not call `record-validation` or transition a storyboard work order to `passed` from production preflight or repair evidence. First verify an `independent_audit` certificate whose source, boundary, raw/semantic artifact, canonical, runtime, rules, validator, and scope fingerprints match. If the available hub/ledger writer cannot enforce this server-authority check, keep the work order awaiting audit, record a capability gap, and do not claim that evidence separation is machine-enforced.

## Preserve server-authority separation with window-neutral user access

Plugin 0.5.14 gives every trusted ChatGPT window in the same authenticated namespace the same user orchestration capability. Do not ask the user to declare a window role, move to another chat, find an old window, or restore permission. The transport session proves an authenticated request source; it is not the production/audit/repair role principal. Keep exactly 19 public tools. Public `begin_project_task(action="create")` creates a window-neutral work order, and another authenticated window in the same namespace may continue it.

Plugin 0.5.20 may place the complete project directory in ChatGPT Library and use only an ephemeral server mount. When status is `mount_required`, use the exact signed Library pointer through `migrate_project_state.storage_action`; verify both bundle SHA-256 and the complete directory-tree SHA-256 before any content route. After a mutation, export, save and read back the new Library object, then atomically advance the pointer generation before unmounting. The persistent server allowlist is limited to rules, restart-stable integrity/download secrets, and signed Library IDs/hashes/status/generation. Preserve every project and handoff byte in Library; legacy persistent deletion is a separately confirmed final step.

Use two permanent server logical authorities and one conditional authority. Each work order must receive a distinct server-minted authority root, window binding and actor binding; production and audit separation is proved by those identities, not by forcing separate user chats:

- `production`: creates a new immutable storyboard artifact, may issue only `production_preflight`, and leaves its audit overlay at `PENDING_AUDIT`/`未签发结论` with null counts and trace.
- `independent_audit`: reads the locked source and artifact, never mutates either, and alone may create a new immutable signed-audit-overlay artifact/successor workbook with the final audit verdict; it never edits the production XLSX in place.
- `repair`: created only after an audit failure and explicit repair authorization; applies one immutable structured repair package to a new artifact version, then returns to the original `independent_audit` authority root and audit cycle before release. Any authenticated window in the same namespace may invoke that exact root, but another audit root may not replace it.

The exact server-role matrix is `production + storyboard_create/regenerate/change + execute + explicit_execute`, `independent_audit + storyboard_audit + audit_only + read_only`, and `repair + storyboard_repair + audit_and_repair + explicit_execute`. Reject every other combination. The same user chat may request all three, but never merge their work orders, authority roots, actor bindings, claims, certificates, or conclusions. Do not start repair for a clean audit. Save time by reusing verified objective evidence, authoring each fact once in the compact master, and limiting generation to the authorized delta, while recomputing every quality conclusion.

Window-neutral access does not bypass workflow state. Pending audit, failed draft and quality blocks constrain the next allowed operation, not which user window can request it. Bind draft-preflight and diagnostic evidence to the server work-order authority so a different authenticated window can continue. Missing trusted cloud session, another namespace/project, invalid role matrix, unsigned repair package, stale claim/lease, or tampered evidence fails closed.

For stored 0.5.13-and-earlier window-bound work orders only, preserve the 0.5.11 continuation contract as compatibility. Do not use it for a 0.5.14 window-neutral work order. Legacy continuation is cloud-only and its eligible operations are exactly production `storyboard_create|storyboard_regenerate|storyboard_change`, independent-audit `storyboard_audit`, and repair `storyboard_repair`; reject `rule_improvement`, version management and every other production-classified operation. `action="plan_role_continuation"` performs a zero-write plan for one `resume_work_order_id`; `action="execute_role_continuation"` requires the identical request, returned `plan_sha256`, and confirmation `ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION`. Derive role from the existing legacy work order and require exact operation, mode, authorization, ordered targets, source lock, protected scope and expected outputs.

Before both plan and execute, reject without mutation if the current authority owns an active operation lease or an unexpired handoff, repair-package, or draft-preflight commit claim. Every authority-operation lease is strictly exclusive per logical authority root. A second same-root request returns `authority_operation_in_progress` even for the same work order, session and payload; it never joins or shares holders. Different authority roots may execute in parallel. Rotation may proceed after lease release or expiry. An expired draft-preflight commit claim stops blocking rotation, but its certificate remains bound to the old session; the continued session must obtain its own valid preflight instead of reclaiming that certificate. A repair-package claim is fenced by token, monotonically increasing claim generation and bounded lease; a legacy 0.5.10 `consuming` package that lacks complete lease fields fails closed and is not auto-migrated. Execute appends one signed continuation and advances the unique active-head projection to a new physical window/actor/session epoch with the next generation under the stable root. This logically revokes the preceding generation without rewriting its immutable signed record. Every later authority write rechecks the active generation at its next persistence boundary, so an old or in-flight context cannot commit after the head advances. Only newly issued handoffs, repair packages, and other records whose current Schema explicitly defines authority fields persist root/epoch/generation; consumption claims use their separate token/generation/lease contract, and certificates or traces are not retrofitted. Exact replay is idempotent only after the first exclusive operation has ended and while that continuation remains the active head, or while completing its signed-record-to-Hub half-write; it is never same-root concurrent coalescing. After a later head advances, the old continuation is superseded. Completed claims, handoffs or certificates remain governed by their domain gates but are not all universal inputs to `plan_sha256`.

Every ordinary-handoff, repair-package and independent re-audit-successor claim binds the claimant's current authority root, active generation and server-observed session together with a fresh claim token, monotonically increasing claim generation and live bounded lease. Finish and release revalidate the complete binding under the project lock; an old generation, replaced token/generation, expired lease or prior-session call fails with zero signed-storage and Hub mutation. Expiry permits only a new claim under the current active epoch. Ordinary-handoff completion additionally loads the actual signed `independent_audit` certificate from certificate storage and verifies its exact certificate ID, handoff, audit work order and artifact bindings. A caller string, production preflight, unsigned certificate or mismatched certificate cannot consume the handoff.

A delayed successor-candidate publication may advance only the candidate artifact transaction. Once the associated signed handoff is `consuming` or `consumed`, preserve its exact bytes and Hub projection, including lifecycle, token/generation/history and audit-certificate binding; never overwrite it with the earlier `prepared` candidate. A passed audit work order is normally terminal. Continue it only when exactly one service-signed `prepared independent_audit_successor` is already bound to that same audit authority, and then only to let the new active epoch claim that existing route. Zero, multiple, unsigned, non-prepared or differently bound routes fail without reopening PASS, minting a replacement route or changing the verdict.

For legacy continuation, reject every cross-role rotation, caller-authored role, changed contract, conflicting legacy session history, stale work-order snapshot/head/runtime/target session, other project/namespace, live-lease/live-claim request, or concurrent loser before authority mutation. This does not prohibit one 0.5.14 authenticated user window from requesting distinct new production, audit and repair work orders. `repair_to_audit` remains bound to the original audit root and cycle; another audit root is not. Pending production/repair handoff blocks a new production batch while allowing the required audit from the current window.

For the current project, the eleven previously identified invalid legacy `prepared production_to_audit` handoffs are not recovered merely by this window-neutral change. They still require the separate two-work-order controlled route under a current runtime certificate, and only the independent-audit server authority—not production—may consume and decide the replacements. The user may request all phases from one window; server identities and conclusions remain separated.

### Keep one canonical truth

For each artifact version, canonicalize one 27-field atomic object before spreadsheet export or audit. Export projections are not written back into it. Bind it to these fingerprints:

- authoritative source bytes and normalized source text;
- authorized chapter/episode boundary and approved episode plan;
- raw artifact bytes plus a separate normalized semantic workbook hash covering sheet order, cells, formulas, rows, and columns;
- canonical parser, schema, runtime, rules, validator, and target-model contract;
- approved character, scene, prop, costume, position, light, and project-state anchors.

The workbook, formal shots, GPT keyframe projection, coverage map, and statistics must derive from the same compact atomic canonical. Each shot's `生成限制` and both export projections explicitly include `9:16竖屏`; GPT begins `画幅：9:16竖屏` and reads only the static whitelist; formal/GPT output contains no literal `BGM` and expresses the negative constraint as `禁止额外配乐`. The canonical stores neither export projections nor Seedance/batch artifacts. It must reproduce the normalized semantic workbook hash; it is not required to reproduce raw XLSX ZIP bytes whose metadata or timestamps may differ. Reject an audit when two candidate canonicals exist, canonical semantics do not reproduce the normalized workbook hash, a retired field/batch is written into a successor, or any required fingerprint is missing.

### Derive source dialogue authority before trusting the candidate

For an imported novel or script, compute `source-dialogue-authority-v6` only from the server-registered source file after cross-binding its payload SHA, Hub authority entry and artifact-metadata source role. The closed grammar `source-dialogue-closed-grammar-v6.3` is the sole authority for audible occurrence boundaries, exact text/punctuation and uniquely resolvable speakers. Candidate storyboard dialogue inventory, `source_lock`, issue/history ledgers, cognition matrices, character anchors and candidate names remain untrusted assertions and must never feed back into source parsing.

The grammar covers speech cues with action/adverb modifiers, role-colon and role-newline scripts, multiple speakers on one line, quoted/unquoted speech, written-object/read-aloud transitions and non-dialogue visual labels. Occurrence offsets start at the first audible content character after authorized wrapper/delivery stripping; after a cue is consumed, every remaining role/visual marker run is parsed in source-offset order. Screen/UI/subtitle/note/sound/letter text is excluded unless the source proves audible reading. A marker fused without a legal boundary, a candidate name containing stage/visual structural terms, unknown action residue, conflicting separators/quotes, an unbound reader/object, or any other non-unique parse fails the whole production preflight. Persist the authority manifest SHA in the report and signed draft-preflight record: it directly binds the source fingerprint, role, exact occurrences and grammar SHA; the grammar SHA transitively binds the implementation-source SHA. Artifact metadata references that signed record through `draft_preflight_id` rather than duplicating independent parser or occurrence hashes. A failure writes no formal artifact, version, workbook/projection, successor or handoff.

### Pass a versioned handoff manifest

Create a handoff manifest for `production_to_audit`, `audit_to_repair`, and `repair_to_audit`. If an external or legacy artifact has no handoff, the read-only audit authority creates `audit_intake` from that exact source and artifact before auditing; absence alone is not a failure, while an existing mismatched handoff is blocking. Every manifest must include:

- handoff ID, source and destination window roles, authorized scope, and previous/current/next boundaries;
- source, raw-artifact, normalized-semantic-workbook, canonical, parser, schema, runtime, rule, validator, dependency/environment, project-anchor, and target-model fingerprints;
- exact objective evidence: normalized source/chunk proof, workbook cells/formulas, canonical parse, approved locks, and deterministic statistics;
- producer claims that remain subject to audit: chapter map, causal map, source treatment, protected dialogue, cognition, scene state, shot plan, coverage, and preflight findings;
- cache entries used or rejected, stage-timing trace ID, and repair/audit cycle IDs.

The receiving server authority atomically claims one exact handoff, verifies its type, status, workflow chain, artifact, fingerprints, destination authority and claim binding, then marks it consumed. The authenticated user window may vary. Missing, replayed, concurrently consumed or mismatched evidence is a blocker; never silently select another handoff or fall back to a stale project copy. A `repair_to_audit` handoff is valid only for the original audit authority and original signed audit cycle.

Plugin 0.5.14 keeps exactly 19 public tools and retains the two guarded `recover_storyboard_handoff` phases. They require two different server-production work orders, each exactly `rule_improvement + execute + explicit_execute`, plus a current full-regression certificate. The user may request both from any authenticated window. Targets must exactly equal the registered gap, ordered handoff IDs and the corresponding `controlled_phase` token; omission or `null` phase may be inferred only after namespace/authority validation from one unique exact token. Missing, duplicate, multiple, unknown, drifted, mixed or extra targets fail before plan or mutation, and omission never defaults to recovery.

The ordinary backfill route still requires current validation to pass with zero issues. The sole historical-contract exception is `GAP-1bb91d83751b422f` and the exact ordered six IDs in `UPGRADE_0.5.12.md`. Its frozen report must be PASS/zero issue and exactly equal the signed original production report. For each real current FAIL, the service reruns the current validator and requires exact equality with that handoff's sealed canonical SHA, complete normalized current-report SHA-256, shot/error/warning counts, full error-code histogram, and current validator composite/full SHA-256. No category-level aspect-only predicate remains, profiles cannot cross handoffs, and any drift fails before mutation. The service preserves both reports and appends only a `historical_production_preflight_attestation` with `historical_contract_pass`; it never claims current production pass. Its `HISTCACHE` identity is not reusable as current-preflight or independent-audit evidence, and every recovered successor requires a full current independent audit. The internal workbook proof remains limited to exact signed 0.5.4/current projection or the exact frozen-0.5.3 baseline; it seals raw XLSX/canonical, old handoff/certificate, original production trace, projector and provenance, while general `extract_xlsx` stays strict.

If a signed prepared production handoff is then blocked solely by a complete-fingerprint change, use the separate recovery work order with `phase="handoff_recovery"` rather than editing or deleting it. For `GAP-0e5d48969c4c494e`, the ordered list must be the exact 11 IDs in `UPGRADE_0.5.12.md`; plan first and execute the identical list/hash with `SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT`. Each signed recovery journal first marks the old handoff `superseded` so it cannot be claimed, then creates exactly one current-fingerprint `prepared + PENDING_AUDIT` successor and updates the Hub projection by CAS. The old handoff remains immutable history, authored artifacts stay byte-identical, historical attestation conclusions remain non-reusable, and production cannot issue or reuse the independent verdict.

Plugin 0.5.15 permits the resulting signed `prepared + PENDING_AUDIT` recovery successor to enter a later controlled recovery when another deployment changes its complete runtime dependency fingerprint. This requires a newly registered gap for the exact successor list and a new server-production `rule_improvement + execute + explicit_execute` work order. Before plan, verify the immediate committed recovery journal and exact signed old/new/certificate/trace after-images, unique Hub projections, recovery authority, immutable authored-content fingerprints and current validation. For historical successors, preserve and recheck the exact `HISTCACHE`, frozen PASS/zero report, complete current FAIL report SHA/count/histogram, current validator identity, false current-pass/reuse claims and mandatory full independent audit. Never treat the old exact-list gap as a wildcard. Any lineage or evidence drift fails before mutation; execute only supersedes the direct predecessor and emits one new `prepared + PENDING_AUDIT` successor, with exact retries returning the same identity.

An explicit independent re-audit similarly preserves the consumed root and every prior successor. The server mints exactly one fresh single-use successor for the next round, with the same original audit authority root/cycle, that root's active signed epoch, and immutable root/parent lineage but new handoff, route trace and event trace identities. Signed storage and the Hub projection must agree under a fenced claim and CAS before the route is usable. Every blocking audit issue resolves to one or more canonical shot IDs; explicit `shot_ids` are authoritative, a supplied location must resolve to the same set, and empty, unknown, conflicting or out-of-range targets reject before any authority write.

Every modern completed certificate binds the exact durable ledger event SHA. The next successor binds both its immediate parent's certificate HMAC and that event SHA in its signed lineage; the storage transaction verifies the same bridge under the project lock. A pre-upgrade certificate without `event_sha256` is not replay/sign-off authority. It may only parent an explicit forced re-audit after deterministic report/event validation, and only the completed modern successor converts it into non-authorizing historical ancestry.

The handoff lifecycle Schema mirrors the complete successor authority and fenced-claim state. Any successor-specific marker requires the full root/parent, sequence, route/event/production trace, parent certificate HMAC/event SHA, audit work-order/window/actor/session, server-minted and lineage-SHA structural set. Runtime and storage separately require route and event traces to be distinct and globally fresh. The successor inherits the root's legitimate route and source role (`production_to_audit/production`, `repair_to_audit/repair`, or `audit_intake/external_input`) and remains addressed to `independent_audit`; Schema must not rewrite every successor as production-authored. `prepared`, `consuming` and `consumed` admit only their exact claim/finalization fields. A consumed successor's `audit_certificate_id` and `latest_audit_certificate_id` resolve to the same certificate, and its top-level finalized claim fields equal the nested `consumption_claim` snapshot; runtime and storage each verify these cross-field invariants. Runtime full-regression certificates remain signed records in the certificate store; the Hub must not carry a second certificate projection. Certificates for one runtime key use a project-lock monotonic signed sequence; same-second PASS/FAIL order is never filename-derived, and an ambiguous legacy head fails closed. If a legacy Hub contains the retired duplicate projection, current-state validation requires a migration that deletes only that projection and preserves the signed certificate store. Every `prepared`, `replaying`, or `rolling_back` migration journal globally fences ordinary writes; only the same migration ID may continue or roll back with a scoped permit. Execute caller CAS equals signed `from_version`, rollback caller CAS equals signed `target_version`, and explicit IDs never fall back. A signed WAL and a current PASS runtime certificate rechecked inside the project lock precede all after-image writes; rolled-back re-execute persists signed `replaying` intent first. Exact cancellation can return prepared/replaying work to terminal `rolled_back`; exact re-execute of `rolled_back` additionally requires the same unique key, certificate/rules/validator, before CAS, after payloads and changed paths. Rejection leaves every project byte unchanged.

Only the latest completed failed lineage may issue the repair package used by a repair authority; a later failed successor supersedes the root package. The user may request that authority from any authenticated window. A fail-to-PASS transition requires a package-bound repaired artifact plus new file, source-version, artifact-semantic, canonical, handoff and trace bindings. Re-submitting a clean semantic matrix against the failed bytes is not repair. Restart recovery accepts only the exact normalized semantic submission and exact server-minted lineage. Normalization lowercases only a valid 64-hex caller `semantic_audit.canonical_sha256`; the same SHA in another hex case replays the same signed receipt without creating a successor, event, certificate or round, while all other semantic, evidence and identity fields remain exact. When the event exists but the certificate does not, the service may deterministically reconstruct that certificate only after all event/report/handoff/cache/fingerprint/result/issue/summary bindings match. A stored certificate must equal the complete deterministic certificate binding; a mismatched body remains invalid even when re-signed with a valid service HMAC. That mismatch, a certificate without its earlier ledger event, any other semantic variant, or any stale package fails closed with zero authority/projection mutation. An already completed signed receipt is a read-only no-op. Repair-package recovery accepts only an `issued` package with no downstream evidence or a `consumed` package whose repair work order, successor artifact/canonical, committed base CAS, metadata, signed return handoff and Hub projection all close exactly. Compensating a consumed package back to `issued` additionally requires the exact original repair claim token and generation, the fixed base successor transaction already `aborted`, the signed handoff and Hub projection both `rejected + aborted`, and no handoff claim, consumption snapshot, audit certificate or any other independent-audit downstream reference. A prepared/committed base transaction, different claim, live/published handoff or audit downstream permanently forbids that release. A `consuming` package, fabricated terminal state, or missing package with downstream evidence fails closed. Initial missing projections may be recovered only inside the same storage lock after revalidating zero downstream evidence, latest lineage, exact certificate HMAC and fenced single-finisher claim by CAS; if authority advances, stale replay cannot resurrect a package, repair a projection or return the old verdict.

Before any release transition, resolve referential integrity in both directions: every certificate `event_id`, `handoff_id`, and `stage_trace_id` must exist and match the same work order, audit cycle, logical artifact, scope, and fingerprints; every handoff certificate or immutable repair-package reference must resolve to exactly one record. If the installed hub/ledger writer does not initialize, persist, and validate these structures deterministically, preserve them as versioned deliverable attachments, record the missing writer as a capability gap, and do not claim that the new lifecycle is machine-enforced.

### Reuse facts, never conclusions

After successful fingerprint verification, these objective facts may cross windows without semantic regeneration:

- exact source text and complete-read/chunk manifest;
- deterministic workbook extraction, formulas, sheet/row/field inventory, and canonical serialization;
- approved human decisions, authoritative boundaries, and project anchors;
- deterministic file statistics;
- a full-test certificate for an unchanged runtime package.

Production-created chapter maps, source mappings, protected-dialogue lists, cognition matrices, scene-state ledgers, shot plans, performance chains, audio workflow, coverage figures, and preflight findings may cross only as claims to inspect. The independent audit must recompute:

1. core-event, causal-link, character-state, protected-dialogue, and source-treatment coverage;
2. source fidelity, deletion authority, new-plot prohibition, and episode boundaries;
3. cognition, seven-field scene continuity, camera visibility, performance, rhythm, transitions, and timing;
4. public-scene ordinary people, sound ecology, audiovisual matching, and same-scene continuity;
5. every formal single-shot master's blank-context independence, field completeness, R10-SPC-1 exact three-stage closure, and target-model contract;
6. prohibitions, formulas, statistics, residual issues, introduced issues, and final pass/fail.

An audit verdict may be reused only as an auditor-issued certificate for an exactly unchanged artifact and exactly unchanged dependency fingerprints. If the user explicitly requests a fresh independent semantic audit, reuse extraction and parsing only and recompute the semantic verdict. Never accept a producer-issued certificate as an audit certificate.

For an uncached fingerprint set, the independent authority must submit a `semantic_audit` under `semantic-audit-v6-adjacent-continuity-structured-voice-conversation-scenes`, bound to the exact canonical hash, reviewed shot count, reviewed adjacent-pair count N-1, and SHA-256 of the canonical ordered pair list. It contains exactly eight unique layer records, each with its own status, summary and concrete evidence IDs. The spatial-continuity layer must list every `pair:left->right` exactly once and in canonical order; failed pair issues target exactly those two shots in left-to-right order. The performance layer audits the timed gaze/focus/eyelid/blink chain, emphasis no-blink, staggered people, anti-stiff limits, and static-eye state. The prohibitions/audio layer must list `field:shot_id.音色` for every dialogue shot exactly once and in canonical order. The source/dialogue layer must list every `source-unit:unit_id` in Source Ledger order to independently verify conversation-scene classification, then list `field:shot_id.画面内容` for every fact-conversion dialogue target in canonical order. Failed layers include targeted issues, evidence and repair actions, and the conclusion agrees with every layer. The service combines this submission with a freshly computed machine report. It may not regenerate any shot, projection, Seedance prompt, or batch during audit. A zero-issue machine report cannot synthesize or replace the semantic submission. Only an exact signed independent-audit cache hit under the same v6 contract may omit it; v1/v2/v3/v4/v5 certificates remain readable history and cannot satisfy an active audit.

### Use content-addressed caches safely

Key each cache entry by the smallest complete dependency set:

- source extraction: source bytes + normalization/parser fingerprint;
- canonical parse: artifact bytes + parser + schema fingerprints;
- project anchors: approved anchor version + project-state fingerprint;
- runtime test certificate: runtime + rules + schema + validator + scripts + dependency/environment fingerprints;
- independent audit scope certificate: source + boundary + artifact/canonical + rules/validator + anchor + target-model fingerprints and exact scope.

Invalidate by impact, while preserving the R10 regression rules:

- dialogue or story change invalidates the current beat, source mapping, protected dialogue, and cognition dependents;
- action or gaze change invalidates the current shot, neighbors, and visibility chain;
- position, direction, prop, door/window, action-progress, or light-direction change invalidates the continuous scene;
- shot insertion/deletion invalidates downstream numbering, cumulative time, transitions, formulas, statistics, and export projections;
- time or lighting change invalidates the continuous scene;
- single-shot timing/model-contract change invalidates that formal master and its deterministic GPT projection;
- episode boundary, cognition transition, scene topology, or core shot-plan change invalidates the affected scene or episode;
- runtime, rule, schema, validator, script, dependency/environment, parser, or target-model change invalidates every dependent certificate and triggers the full defined regression suites.

Unchanged auditor-issued shot/scene certificates may support impact regression only when their sub-fingerprints and upstream dependencies are unchanged. Always rerun global source coverage, episode entry/exit, cognition, total duration, numbering, formulas, statistics, exact ten-column order, and retired-field absence before a final verdict.

### Separate content checks from system regression

Ordinary content generation, audit, repair, and archive tasks must run all applicable R10 preventive gates, THG-01–THG-16, the complete N-1 adjacent-pair proof, dialogue-shot voice-field evidence, source-fact dialogue evidence, the actual multiline XLSX round-trip, chapter coverage checks, eight audit layers, and impact regression. They must verify, but not regenerate, a matching full-test certificate. Do not load or diagnose every runtime module, inspect source code, or rerun functional/golden/Agent/project-Agent suites during an ordinary content audit.

Run the full suites when the runtime, rule, schema, validator, script, dependency/environment, parser contract, or target-model contract changes; when the certificate is absent or mismatched; or when a deterministic validator behaves inconsistently with its certificate. Keep `TP-PATCH-ABSORB` and other explicit system-change tasks on the full-regression path.

### Record comparable stage timing

For production, independent audit, and repair, record one trace ID and the server-observable stages that actually ran: canonicalization, delta base/scope/merge, validation, deterministic export projection, repair, and ledger/handoff as applicable. Persist measured start/end or duration, payload and affected/preserved/final counts when the service truly observes them. New compact routes must expose `legacy_generation_calls=0`, `seedance_batches_generated=0`, and `model_authored_derived_chars=0`. A completed optimized flow exposes `fast_path_evidence` binding both production and independent-audit traces to the same canonical, handoff and certificate; if either trace is absent or mismatched, report `fast_path_incomplete`. Do not infer model tool-call count, source characters, retries, or planning/generation duration from server time. A parallel parent stage must not double-count child durations as wall-clock time. Server records must say `measurement_scope=server_only`; ChatGPT planning/generation and every unavailable metric must be listed in `unmeasured_stages`, not reported as measured zero.

Use traces from the same locked source, boundary, rule version, hardware/service tier, and output contract when claiming an efficiency change. Performance is never allowed to override a failed quality gate.

## Classify intent before acting

Map every request to one operation in `project_agent_capabilities.json` and one mode:

- `plan_only`: analyze or propose; do not change the source artifact.
- `execute`: create or modify the requested artifact.
- `audit_only`: complete all checks; do not repair.
- `audit_and_repair`: one workflow chain whose initial `audit_only` work order is read-only; after a failed audit and explicit repair authorization, create a separate repair work order and return its successor to the original audit work order for impact regression and final verification.

Treat “给方案、分析、建议、怎么改” as `plan_only`. Treat “生成、改写、修改、新增、删除、替换、合并、拆分、移动、修正后输出” as explicit execution authorization for the named scope. Do not extend authorization to unrelated episodes, files, characters, scenes, or rules.

If the request mixes planning and execution, lock the selected plan first. When the user has already specified the exact change, that instruction is the selected plan; do not ask for redundant confirmation.

## Run the universal task lifecycle

1. Load the exact plugin project state, or validate both shared Library files only on a Library-backed/standalone route.
2. Create one server-role-specific, window-neutral work order with request text, operation, mode, targets, expected outputs, and authorization. An `audit_and_repair` request remains one workflow chain but uses an initial `audit_only` work order, then—only after failure and explicit authorization—a distinct repair work order; the user may request both from one window, but never place `storyboard_audit` and `storyboard_repair` under one server binding.
3. Lock the authoritative source, latest artifact version, episode/scene/shot boundary, must-keep list, must-remove list, and protected scope. Resolve or create the single canonical fingerprint and verify any incoming handoff before content work.
4. Resolve conflicts in this order: user’s current explicit instruction, current authoritative modified story, current source text, current approved project state, older reference versions.
5. Perform the selected planning or execution workflow.
6. Produce a new version by default. Preserve the original unless the user explicitly requests replacement of the same artifact.
7. Validate every required downstream consumer and impact boundary.
8. For storyboard output, choose the full route for a first draft, an invalidated scene/episode, or any dialogue/speaker change. Use the stable-UID delta route only for a same-count bounded visual or other non-cognitive execution change whose exact dialogue text, punctuation, order/segmentation, speaker identity, source mapping, cognition, and protected-dialogue evidence remain unchanged. Both routes run the same full production gates and issue only `production_preflight`. A separate `independent_audit` must complete the eight-layer audit and issue the release verdict. Only after a failed audit and explicit authorization may a third repair authority consume the structured repair package; it must return the new fingerprinted version to the original audit authority for verification.
9. Record deliverables, versions, decisions, issue events, unresolved blockers, and capability gaps.
10. Mark a clean task `awaiting_signoff`; archive only after human approval.

Never claim completion when the required work order, deliverable, or issue-ledger update is incomplete. Require Library writeback only on a route that actually uses the shared Library files.

## Plan, create, and rewrite story content

### Analysis and planning

Support novel analysis, story diagnosis, hooks, adaptation strategy, episode planning, character arcs, scene plans, rewrite plans, and alternative comparisons. A plan must state:

- Objective and audience effect.
- Current problem and evidence.
- Proposed story change.
- Character knowledge and motivation changes.
- Episode, scene, dialogue, time, and production impact.
- Must-keep and must-remove content.
- Risks and recommended option.

Do not edit the source artifact in `plan_only` mode.

### Novel or chapter intake

Before a novel chapter becomes a script, beat plan, or storyboard, load `novel_to_storyboard_intake.md` and complete its conditional `source_ingestion` gate. Read the full authorized chapter before generation; long chapters may be extracted in chunks, but no beat or shot may be produced until all chunks are synthesized into one chapter map. Lock previous/current/next boundaries, event topology, causal links, character start-change-end states, cognition limits, protected dialogue, source treatment, adaptation budget, and beat plan.

Use S/A/B/C only as the final value tier after multi-axis judgment. C-grade prose is a deletion candidate, not an automatic deletion: time, space, mood, foreshadowing, transition, and character ecology are valid cinematic functions. Require explicit authorization or redundancy evidence for deletion, preserve any cinematic function elsewhere, and stop on unresolved source ambiguity.

Transform through `source unit → event/beat → shot`; never use one sentence as an automatic one-shot unit. Require a 100% chapter preflight for core events, causal links, character state changes, protected dialogue, and source treatment before formal shot generation, then repeat the coverage audit after generation.

### Script creation

Create a complete short-drama script from the locked source and episode boundary. For novel input, use the approved chapter map and beat plan rather than converting paragraphs directly. Preserve key events and dialogue unless compression, adaptation, or deletion is explicitly authorized. Make motivations visible, track cognition, build emotional progression, and close each episode with a hook or deliberate resolution.

### Script rewrite

Build a source-to-rewrite ledger before rewriting. Every original event and key line must be retained, merged, compressed, moved, replaced, or explicitly deleted. Return a change summary with affected scenes, characters, dialogue, and downstream storyboard scope.

## Execute add, modify, delete, and other changes

Create a structured change request before mutating an existing script or storyboard. Use only these atomic operations:

- `add`: insert new material without silently replacing existing content.
- `modify`: change fields or content while keeping the same logical unit.
- `delete`: remove the authorized unit and every stale downstream reference.
- `replace`: atomically remove the named old unit and add its approved replacement.
- `merge`: combine named units while preserving source coverage and timing feasibility.
- `split`: divide one unit and preserve action, dialogue, timing, and continuity.
- `move`: relocate a unit and rebuild causal, temporal, and spatial links.

For every operation:

1. Identify exact source and target IDs.
2. Record the user instruction as authoritative basis.
3. Lock protected content.
4. Compute the impact matrix before editing.
5. Apply the change to the authoritative script or storyboard layer.
6. Remove obsolete content; do not leave old dialogue, actions, prompts, numbering, or statistics behind.
7. Update all affected downstream layers.
8. Regress the changed range plus entry and exit boundaries.

Deletion, replacement, merge, and move are destructive within the selected scope. Require explicit execution language and preserve a recoverable prior version. If scope is ambiguous, stop and ask one narrow question.

## Propagate script changes into storyboards

Any approved story or script change must evaluate these downstream consumers:

1. Episode boundary and beat list.
2. Source ledger and coverage map.
3. Character cognition and relationship stage.
4. Must-include and must-exclude locks.
5. Scene-state ledger and spatial continuity.
6. Shot plan, viewpoint, camera visibility, shot load, and rhythm.
7. Formal storyboard content, dialogue, timing, numbering, and cumulative time.
8. R10-SPC-1 formal-master start state, three mandatory Seedance 2.5 stages, action/dialogue process, end state, camera, sound, environment, and four-part qualitative light contract.
9. Character, scene, costume, prop, position, and lighting anchors.
10. Keyframe prompts or generated frames.
11. Voice, psychological audio, ambience, BGM cues, and transitions.
12. Audit statistics, change notes, and exact ten-column export projection.

When the authoritative input is a novel chapter, also propagate and validate the complete-read proof, chapter event map, causal graph, character state changes, protected-dialogue mappings, source treatment ledger, adaptation budget, beat plan, and previous/next boundary context. Any upstream change that alters an event, causal link, cognition transition, or protected line invalidates the dependent beats and shots.

Regenerate only the affected units plus boundary neighbors when the upstream structure remains valid. Regenerate the whole episode or scene when episode boundaries, character cognition, scene topology, or the core shot plan changes. Never patch only visible wording when the causal source is upstream.

### Addition

Insert the new beat into the script, establish start/end state, generate only the required 27-field shots, update numbering or stable insertion IDs, recalculate duration, project the ten-column export, and audit both boundaries.

### Modification

Replace the affected content, remove old residue, update cognition and state, regenerate affected compact shots only, deterministically refresh the export projection, and regress dependent neighbors.

### Deletion

Delete obsolete script and storyboard material, remove its source mapping, bridge the preceding and following states, update timing, numbering and export projection, and verify no stale reference remains.

### Replacement

Treat as one atomic change: lock the old target, insert the approved new chain, delete all old residues, rebuild downstream outputs, and compare against must-keep/must-remove lists.

### Merge, split, or move

Recalculate dialogue capacity, shot load, rhythm, source coverage, continuity, numbering, and compact-master blank-context executability. Do not preserve an invalid shot structure merely to reduce editing.

## Produce visual, video, audio, and delivery assets

- Build character, scene, costume, prop, position, and light anchors before image or video generation.
- For every public-flow scene, load `public_scene_ecology.md` before formal shot or keyframe projection. Write ordinary-person density, location-appropriate roles, staggered actions, spatial placement, concrete near/mid/far environmental sound, dialogue ducking, and audiovisual source matching once in the formal-master atoms; deterministically preserve keyframe-visible sound sources. An empty public scene requires a visible empty-state description plus a plot-grounded reason.
- Build a separate visual-texture lock before formal-master video execution or keyframe/scene-frame projection. For every ancient-costume live-action formal master or visual projection, load `songyun_humanist_cinematography.md` as the mandatory visual master and apply its real-camera texture, sourced-light method, restrained color logic, lens/depth selection, humanist composition, projection order, and negative restrictions. For every night or low-light ancient-costume formal master or visual projection, additionally load `ancient_night_cinematography.md` and apply its motivated practical/ambient sources, distance falloff, half-lit readable faces, transparent shadows, restrained warm/cool color, night-specific lens/depth selection, low-light film texture, continuity, and negative restrictions. Adapt concrete values to the era, time, weather, place, mood, and real light sources. Project aspect ratio, sourced light, visibility, continuity, and reference anchors always override any example value.
- Generate keyframe prompts with the established real short-drama screenshot style and negative restrictions. Use an image-generation tool when the user requests images and the required references are available.
- Treat `正式分镜母版` as the only blank-context executable single-shot master. `画面内容` carries action, expression, exact dialogue, lip-sync/timing, visible delivery cues, listener reaction and exactly three `【0.0—x.x秒】` Seedance 2.5 stages; `音色` alone carries the complete fixed voice specification. New display IDs use `EP##-###`, cumulative time resets per episode, and the formal cell follows `strict_prompt_contract.md`. Every shot's `生成限制` includes `9:16竖屏`; GPT starts `画幅：9:16竖屏` and contains only static whitelist fields. Export no Seedance column, no batch sheet, and no background-generated video prompt.
- Design music cues after picture lock from the whole-scene emotional key, narrative function, time motion, frequency space, and audible scene image. In formal/GPT single-shot text, the literal `BGM` is forbidden and the negative constraint is `禁止额外配乐`; music cues remain a separate downstream workflow.
- Design dialogue delivery with sound strength, rhythm, breath, pause, emphasis, and ending tone.
- Treat public-scene ambience as a mandatory production layer rather than optional decoration. Reject generic-only “环境声/街道嘈杂声”, anonymous extras with clear full dialogue, modern or era-incompatible sound sources, and same-scene crowd/audio continuity jumps.
- Convert or export files through the appropriate document or spreadsheet workflow while preserving the 27-field atomic order, exact ten-column projection and formulas; verify canonical→projection→XLSX→reverse parse under UTF-8 NFC+LF and preserve every multiline dialogue row.
- Split production into traceable episode, scene, shot, keyframe, voice, ambience, and BGM packages. For an explicit downstream i2v delivery, pass the committed `正式分镜母版` directly and create only the required transport metadata or adapter on demand; never create or persist a competing video narrative, Seedance text, or batch artifact.

## Audit, repair, and learn

Use `agent_runtime.md` for issue event structure, recurrence escalation, concise reporting, and guarded rule candidates.

- Audit-only tasks never modify artifacts.
- Audit-and-repair work orders close within two preferred and three maximum rounds, but keep authorities distinct: independent audit establishes the immutable issue baseline, repair creates a new artifact version, and independent audit verifies that new version.
- Prefer the certificate-bound `commit_storyboard_patch` path only when every requested repair is a non-structural field replacement on a stable shot UID and the exact independent-audit certificate names that issue. Use production-authorized `commit_storyboard_delta` only for a user-directed, same-identity/same-count visual or other non-cognitive execution change with refreshed shot-bound evidence. Delta must preserve exact dialogue text, punctuation, order/segmentation, and speaker identity; any dialogue or speaker change requires full `commit_storyboard` for the complete affected scene or episode because delta cannot replace source mapping, cognition, or protected-dialogue evidence. Psychological audio, insertion/deletion/movement/split/merge/renumbering, source mapping, cognition, boundary or topology changes also require the full affected scene/episode route. Structural audit repair must consume its immutable package and use full `commit_storyboard`. The service must compare-and-set base hashes, enforce stable UID boundaries and protected scope where applicable, save a new full artifact, and run the complete validator. Production, patch, delta, and repair preflight are never the independent final verdict.
- Record every repair result as original issue resolved, residual, or introduced.
- A second consecutive recurrence requires root-cause repair; a third requires upstream rebuild.
- Never activate a formal rule or validator automatically.

Production is responsible for authoring one canonical and correcting scene/full preflight failures before commit. Deterministic service code is responsible for migration CAS, pattern queries, schema and semantic gates, output projection, quality metrics, certificate/hash binding, and failed-draft ledger writes. Independent audit is responsible only for independently searching the immutable committed artifact for omissions and signing the final verdict; it must not regenerate or repair content and cannot be used as the normal cleanup stage for preventable generation defects.

## Handle missing capabilities honestly

When a request is not in the capability registry or required tools, files, permissions, or source evidence are unavailable:

1. Do not simulate a completed capability.
2. Open a `capability_gap` work order in `plan_only/plan_only` mode, then record the gap from any authenticated window using that exact work order with requested input, desired output, missing dependency, risk, safe fallback, proposed implementation, and tests. Never append a project gap without that work-order authority.
3. Continue only with a safe in-scope fallback that does not misrepresent completion.
4. Require user approval before adding external access, new credentials, destructive behavior, or a formal rule.

The capability registry is open-ended: new functions enter as candidates, pass implementation and tests, then become supported. “All functions” means every request is either routed to a supported workflow or recorded as an explicit gap; it does not mean pretending unavailable tools exist.

## Keep responses simple

Default task reports:

- Plan: `方案完成｜推荐方案X｜影响N处｜待执行/无需执行`
- Execution: `处理完成｜新增N｜修改N｜删除N｜替换N｜回归通过`
- Storyboard production: `生成完成｜N镜｜总时长Xs｜生产预检通过｜待独立审计`
- Independent audit: `审计通过｜阻塞错误0｜原问题残留0｜修改新增问题0｜待人工确认归档`
- Audit: use the concise formats in `agent_runtime.md`.
- Blocked: `任务阻塞｜原因一句话｜下一步一句话`

Keep detailed evidence, change matrices, and capability records in shared files unless the user asks for the full report.
