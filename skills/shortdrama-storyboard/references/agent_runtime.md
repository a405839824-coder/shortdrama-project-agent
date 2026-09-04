# R10 safe storyboard project Agent

Use this policy together with `V16.3-148-R10-StableRuntime` and `R10-PG1`. The Agent policy version is `R10-AGENT-SAFE-1`.

## Keep one authoritative cross-window ledger

- Use the exact Library title `分镜项目Agent_问题台账.json` as the authoritative cross-window issue ledger.
- At the start of Agent mode, resolve that exact title, load its latest version, and validate it with `python scripts/storyboard_agent.py validate --ledger LEDGER.json`.
- If the file does not exist, initialize it once. If Library access is unavailable, write a local pending event bundle and state that it is not yet synchronized; never claim cross-window collection without a successful ledger write.
- Treat the shared ledger as authoritative for exact counts. Use the current audit report and current file as primary evidence. Use prior chat context only as supplementary evidence and label any imported pattern as unquantified when exact event records are unavailable.
- Replace the existing Library file after each accepted audit event. Preserve the same file identity instead of creating date-stamped duplicates.

## Record every audit and repair

For every draft preflight, generation audit, audit-only run, repair, or repair regression, create one `audit_event` that satisfies `references/agent_ledger_schema.json`.

Distinguish the event authority explicitly:

- `production_draft_preflight` records scene/full draft findings before persistence. A failed draft updates occurrence history and the active prevention profile but creates no artifact, version, XLSX, projection, successor, handoff, or audit verdict.
- `production_preflight` may be stored as a separately classified production-evidence event and in the handoff's `producer_claims`, but it must use a distinct scope and audit class from `independent_audit`. It cannot close prior independent issues, increment an independent audit round, set release readiness, or issue the final audit verdict.
- `audit_class=independent_audit` uses the backward-compatible `task_type=audit`, is read-only, and must bind its event and certificate to the verified handoff, source, boundary, artifact, canonical, runtime, rule, and validator fingerprints. Only this audit class may issue the final pass/fail conclusion.
- Repair checks are stored in the immutable repair handoff and must return the new artifact fingerprint to `independent_audit` for final verification. Until the ledger writer enforces role-aware transitions, do not let a repair `result=pass` event close an audit cycle.

Reuse cross-window source extraction, deterministic canonical parsing, approved anchors, and an unchanged-runtime test certificate only after fingerprint verification. Never reuse a production coverage figure, cognition/state conclusion, issue resolution, or pass/fail verdict. Attach the stage-timing trace and cache hit/miss evidence to the event when available.

The source-dialogue occurrence inventory is deterministic evidence, not a candidate claim. Compute it only from the server-registered source file after cross-binding its payload SHA, Hub authority entry and artifact-metadata novel/script role with `source-dialogue-authority-v6` / `source-dialogue-closed-grammar-v6.3`. The authority manifest SHA in preflight and the signed draft-preflight record directly binds the source fingerprint, role, complete occurrences and grammar SHA; the grammar SHA transitively binds the implementation-source SHA, and artifact metadata references that signed record by `draft_preflight_id`. Storyboard inventory, `source_lock`, ledger records, cognition/anchor data and candidate speakers must not influence parsing. Any ambiguous occurrence/speaker/read-aloud object or unsupported syntax fails the complete preflight and creates no artifact, version, XLSX/projection, successor or handoff.

Required event identity and scope:

- `event_id`, ISO timestamp, `episode_id`, logical `file_name`, content `file_fingerprint`, `source_version`, and `audit_cycle_id`.
- `task_type`, `audit_round`, `result`, checked-shot count, blockers, and the complete issue list.
- For each issue: error code, one of the eight fixed audit categories, severity, affected shot IDs, field, concrete evidence, source sentence or an explicit not-applicable reason, repair action, and state (`initial`, `residual`, or `introduced`).

Use this fixed audit category order:

1. 源文对白
2. 机位可见性
3. 空间连续
4. 表演链
5. 镜头节奏
6. 正式母版单镜执行完整性
7. 禁项与声音
8. 统计闭合

公共场景人物密度、地点角色、错峰动作、空间层次、具体环境声、对白避让、声画来源和正式母版复现均归入第7类“禁项与声音”；同场人流跳变同时在第3类“空间连续”记录。

`Seedance独立执行` is a legacy input alias only. Normalize it immediately to `正式母版单镜执行完整性`, record the applied alias in the event, and never persist or report the legacy label as a current audit category.

## Keep compact generation single-source

- Pin the project hub to `compact_master_version=0.5.5` and `background_seedance_generation=false`.
- Pin every new formal storyboard write/export to `source_lock.output_contract=R10-SPC-1`; keep the v0.5 canonical architecture under the active rule patch, using strict EP numbering, episode-local cumulative time, exact three-stage Seedance 2.5 picture content, the fixed nested `音色` template, direct/source-fact dialogue separation, `正式分镜母版` fifth-column label, qualitative light fields, per-shot `9:16竖屏`, static GPT projection, complete eye-performance and N-1 adjacent-pair evidence, and THG-01–THG-16 in `strict_prompt_contract.md`.
- Pin newly issued independent-audit certificates and cache identities to `semantic-audit-v6-adjacent-continuity-structured-voice-conversation-scenes`; bind exact canonical, shot count, N-1 pair count and ordered-pair SHA-256, require the spatial layer's complete ordered `pair:left->right` set, the prohibitions/audio layer's complete ordered `field:shot_id.音色` set for all dialogue shots, and the source/dialogue layer's Source Ledger ordered `source-unit:unit_id` set plus canonical ordered `field:shot_id.画面内容` set for all fact-conversion dialogue targets. v1/v2/v3/v4/v5 remain readable history only and cannot authorize current release or cache reuse.
- Store only the R10.1 compact canonical. Compile the formal ten-column master and GPT keyframe projection deterministically when explicitly requested; do not persist generated copies in canonical.
- Do not create `seedance_batches`, `独立Seedance批次`, a standalone Seedance execution column, or any background video prompt job.
- Keep explicit on-demand video-model execution available through an authorized production request. It consumes the verified formal single-shot master and must not write a duplicate prompt or batch back into canonical.
- Script and storyboard changes propagate to `compact_canonical` and `formal_master_export`, then invalidate dependent anchors, keyframes, audio cues, and explicit on-demand production packages as required.
- Production writes and performs only `production_preflight`; its immutable workbook keeps `PENDING_AUDIT`/`未签发结论` with null counts and trace. Independent audit remains read-only and is the only authority that may create a new immutable signed-audit-overlay artifact/successor workbook with a final audit conclusion; it never edits the production XLSX in place. Repair activates only after an independent-audit failure plus explicit authorization, and its new artifact returns to the original independent-audit authority.
- Plugin 0.5.14 keeps exactly 19 tools and gives every authenticated window in one user namespace equal orchestration access while preserving distinct server production/audit/repair identities. `recover_storyboard_handoff` still requires two different server-production work orders, each exactly `rule_improvement + execute + explicit_execute`, and a current full-test certificate; the user may request both from the same window. Targets must exactly equal the authorized gap, ordered handoff IDs and corresponding `controlled_phase`; omitted or `null` phase is inferred only after namespace/authority validation from one unique exact token. Missing, duplicate, multiple, unknown, drifted, mixed or extra targets fail before plan or mutation. Plan before execute and preserve the identical list/`plan_sha256` plus the exact confirmation literal. The ordinary branch requires a zero-issue current validator report and appends ordinary `production_preflight` evidence. The only historical branch is `GAP-1bb91d83751b422f` with the exact ordered six IDs in `UPGRADE_0.5.12.md`: its frozen report must exactly equal the original signed PASS/zero-issue report. Each real current FAIL must exactly match its own sealed canonical SHA, complete current-report SHA-256, shot/error/warning counts, complete error histogram and current validator fingerprint; profiles are non-interchangeable and any drift fails before mutation. It appends a distinct `historical_production_preflight_attestation`, preserves the complete current FAIL diagnosis, never claims current pass, uses non-reusable `HISTCACHE`, and forces a full current independent audit. After the six attestations exist, the separate recovery work order may recover only the exact ordered 11 IDs under `GAP-0e5d48969c4c494e`; preserve every old signed record as `superseded` and issue one `prepared + PENDING_AUDIT` successor. Neither phase rewrites the old handoff/certificate, storyboard, canonical, XLSX or history, and historical Hub reads remain limited to the exact stripped or exact full signed record.

- Plugin 0.5.20 keeps those 19 tools and routes content storage through `migrate_project_state.storage_action`. In Library mode, a signed pointer plus exact bundle/tree hashes authorizes an ephemeral mount; all project bytes, including complete handoff and recovery history, live only in Library or the temporary runtime. Any mutation requires export, Library save/readback, and monotonic pointer commit before unmount. Rules, restart-stable secrets, and signed Library ID/hash/status/generation metadata are the only persistent-server allowlist.

The Agent computes one stable pattern key from project, error code, normalized field class, and normalized root-cause/issue type. Episode, logical filename, version, event ID, and shot IDs are occurrence locations and must not split one recurring defect into false singletons. Mutable evidence text is never the identity. Preserve every distinct event/scope/location and report both pattern count and occurrence count so a template defect recurring across batches is escalated instead of hidden by location-specific deduplication.

- Plugin 0.5.15 additionally permits a signed recovery successor to be recovered again after a later deployment invalidates only its complete dependency fingerprint. Use a newly registered exact gap and a new exact recovery work order. Verify the immediate committed recovery journal, signed old/new/certificate/trace after-images, Hub projections, recovery authority, immutable content fingerprints and current validator result before plan. Historical successors must retain the exact `HISTCACHE`, frozen PASS/zero report, complete current FAIL identity and non-reusable/full-reaudit flags. Any drift fails before writes; execute remains append-only and idempotent.

## Prevent defects before formal output

Use this mandatory production sequence: `get_agent_status` → authorized `migrate_project_state` when status requires it → `query_project_issues` → per-scene `preflight_storyboard_draft` → full `preflight_storyboard_draft` → certificate-bound `commit_storyboard` → separate `independent_audit`.

- Bind the active prevention-profile hash returned by `query_project_issues` to the work order and draft certificate. Any change invalidates the certificate.
- Record the first full-draft quality result immutably. Overall correctness and direct usability must each be at least 98%, every episode at least 96%, and new local issues at most 2%; hard errors, major issues, known recurrences, and systemic duplicates must all be zero.
- A threshold grade measures whether generation prevention is working. It does not hide findings or waive a validator: full preflight is commit-ready only with no remaining issue.
- Final independent audit is a read-only omission defense and requires 100% pass across all eight layers, with blocking, residual, and introduced issues all zero. It does not regenerate or repair a producer's known errors.
- A B/C first-draft result creates an active production quality block. Resume new storyboard production only after the root cause, rule/validator/template fix, negative regression fixture, and prevention profile are closed under the authorized improvement workflow.

## Close within two or three internal rounds

- Treat every ledger mutation as an atomic transaction. Validate the current ledger, the complete incoming event, scope ownership, the next continuous round, and all cross-event evidence identities on a candidate copy; commit it only after the resulting ledger revalidates. A rejected event, sign-off, or reopen must leave counters, scopes, events, issues, timestamps, and serialized ledger bytes unchanged.
- A `pass` event has both `issues=[]` and `blockers=[]`. An independent-audit pass also has `checked_shot_count>=1`, `task_type=audit`, `validation_role=independent_audit`, every required fingerprint/evidence binding, and all four independence assertions set to true. A production, repair, legacy, zero-shot, unbound, or self-asserted pass cannot authorize human sign-off.
- Compare event, handoff, trace, certificate and reopen identifiers with trimmed case-folded identity, while retaining their original display values; reject leading/trailing whitespace. Compare SHA-256 evidence case-insensitively after exact 64-hex validation. Across the whole project ledger, every independent-audit `handoff_id` and `stage_trace_id` is single-use even if the caller changes the cycle, episode or logical filename.
- After an independent `fail_current` or `blocked`, a later pass must bind a new handoff, raw file fingerprint, source version, artifact-semantic fingerprint, canonical fingerprint and stage trace. Renaming the file, changing the cycle, changing identifier case, or adding whitespace never turns failed evidence into a fresh repair result.
- A forced independent re-audit preserves the consumed root and creates one fresh server-derived successor handoff for the next round. Its root/parent lineage, original audit authority root/cycle, active signed authority epoch, route trace, event trace and complete fingerprint bundle are immutable. Every blocking issue must resolve to a non-empty canonical shot set; explicit `shot_ids` control, and any optional location must resolve to the same set. Only the latest completed failed lineage's repair package remains usable.
- Every modern completed independent-audit certificate seals its exact durable event with `event_sha256`; each successor seals the immediate parent certificate HMAC and parent event SHA, and storage repeats that check under the project lock. A pre-upgrade certificate missing `event_sha256` cannot replay or sign off. It can only be the explicitly forced next audit's historical parent after deterministic report/event validation, and only a completed modern successor may bridge it into non-authorizing history.
- Crash recovery may finish only an exact normalized semantic replay. Normalize only a valid 64-hex caller `semantic_audit.canonical_sha256` to lowercase; do not case-normalize any other semantic, evidence or identity field. Replaying that SHA with different hex case returns the same signed receipt and does not append a successor, event, certificate, handoff, round, telemetry or overlay. If the immutable event was written before its certificate, reconstruct the one deterministic missing certificate only after report/event/handoff/trace/cache/fingerprint/result/issue/summary identity closes. A stored certificate must exactly match the deterministic certificate content and all authority bindings; a content mismatch that has been re-signed with a valid service HMAC still fails with zero authority/projection mutation. A certificate-only state, any other semantic variation, stale package or cross-lineage retry also fails without mutation. A completed signed receipt is a read-only idempotent response. Repair-package replay accepts `issued` only with zero downstream evidence and `consumed` only with the complete repair work order, successor artifact/canonical, committed base CAS, metadata, return handoff and Hub projection; `consuming`, fabricated terminal states and deletion-after-consumption fail closed. Missing initial projections may be recovered only while holding the same storage lock and after rechecking zero downstream evidence, latest lineage, exact certificate HMAC and fenced single-finisher claim by CAS; a stale completed replay may neither resurrect a package, repair a projection nor return an obsolete verdict after authority advances.
- Reopening a clean independent scope invalidates the old sign-off eligibility immediately. The reopen binds one not-yet-recorded independent-audit event ID and the next continuous round; only that new clean event may restore `awaiting_human_signoff`. Never sign the stale pre-reopen pass, reuse a reopen trigger, or reopen beyond round three.
- Bind every human sign-off and authority reopen to an exact `basis_event_id`; timestamps are descriptive evidence, never event-order authority. Two actions and two audit rounds may share one second without sharing authority. A legacy action without an exact basis may be migrated only when the old cached scope `last_event_id` or one unique event proves the binding; downgrade it to `authority_class=legacy`, keep `can_advance=false`, and require a new independent audit. Fail migration when multiple candidates remain.
- On every ledger load, replay events in append sequence and verify continuous rounds, canonical scope derivation, last-event ownership, open issues, closure/sign-off state, global evidence freshness and counters. A scope, issue history/location, sign-off or authority reopen may reference only an event belonging to that same derived scope. Local JSON Schema constraints and Python runtime rejection rules must share the same negative matrix; cross-record uniqueness and replay remain mandatory runtime checks.
- Never rewrite a historical event, issue history, or occurrence-location byte to make an old ledger satisfy the new replay contract. The controlled migration path requires the signed migration journal's explicit `migration_id` and creates `replay_checkpoint` over the immutable event prefix, its canonical SHA-256, the old whole-ledger canonical SHA-256, immutable issue-history/location prefixes, and the canonical/legacy scope alias plus first/last-round boundaries derived from events. Hash field names refer to canonical JSON, not the raw file-byte SHA.
- A persisted checkpoint is a request for external authority, not proof of authority. Every load and mutation must receive a `checkpoint_authorizer(checkpoint) -> bool` backed by a valid signed migration journal and CAS: the journal HMAC is valid, its migration ID and before-ledger canonical SHA match, and its committed after-ledger contains the identical checkpoint. Plan-only validation may use a scoped provisional authorizer; ordinary CLI/load/ingest/sign-off/reopen without verified journal authority fails closed. No mutation API may create or alter the checkpoint.
- A checkpoint may seal legacy scope aliases, a non-one first round, structured evidence, or legacy derived counts only inside its exact immutable prefix. After the checkpoint, a new scope starts at round one, an existing canonical scope continues at the checkpoint's last round plus one, modern event validation applies, and summaries always recompute counts from issues instead of trusting sealed caches.
- A non-independent legacy pass becomes `legacy_resolution_requires_reaudit`; a migrated legacy approval becomes `legacy_signoff_requires_reaudit`. Neither can advance or authorize release. Legacy issue conclusions remain blocked until a new audit cycle performs a complete independent audit. A failure retains the block; only a later clean pass with new handoff, trace, file/source_version/artifact/canonical bindings may append the clearance evidence and reach `awaiting_human_signoff`.
- A clean production preflight may be superseded only by another `production_draft_preflight` revision in the same scope; it never opens a general audit continuation.
- An independent-audit certificate with `result=pass` must review at least one shot, carry `semantic_conclusion.status=pass`, pass every required gate/layer, and report zero blocking, residual, and introduced findings. Certificate text cannot override its structured result.
- Prefer closure within two internal audit-repair rounds. Allow a third round only for upstream reconstruction.
- Never accept `audit_round > 3`.
- `fail_current` always blocks the next episode.
- A clean pass still waits for real human sign-off before `can_advance=true` or archive.
- An issue present in two consecutive rounds requires root-cause repair, not another local wording edit.
- An issue present in three consecutive rounds requires upstream rebuild of the source ledger, state ledger, shot plan, or generation package that produced it.
- Resolve an open issue only when a later event in the same cycle no longer contains it. Preserve its history after resolution.

## Evolve rules through a guarded candidate gate

Do not edit or activate formal runtime rules automatically.

1. Generate a rule candidate only after the same issue survives two consecutive rounds.
2. Link the candidate to its triggering issue records and evidence.
3. Specify the proposed preventive change, deterministic validator change, positive and negative Golden Tests, impact-regression scope, and conflict-order check.
4. Run the validator tests, all bundled Golden Tests, the Agent tests, and skill validation.
5. Mark the candidate `awaiting_human` only when all machine checks pass.
6. Require an explicit human approval. Approval alone means `approved_pending_validation` if evidence is incomplete, or `ready_for_manual_promotion` when every gate is complete.
7. Activate the rule only through an intentional skill update. Record the promotion afterward; never let the ledger script rewrite runtime, validator, or test files.

Reject a candidate that conflicts with the fixed priority:

`安全合规 > 源文忠实 > 分集边界 > 台词容量 > 统计闭合 > 可见性 > 图生视频可执行性 > 节奏留存 > 美学细节`

## Keep the user-facing report short

Keep full evidence in the ledger. Default to one of these concise reports:

- `审计通过｜阻塞错误0｜原问题残留0｜修改新增问题0｜待人工确认归档`
- `审计未通过｜共N项｜第R/3轮` followed by affected shot IDs and one short sentence per issue.
- If blocked: state one exact blocker and the next action.

Do not ask the user to trigger repeated audits when the task includes repair. Complete audit, repair, impact regression, and final verification internally, then write the final event and report once.

## Operate the ledger script

Initialize:

```bash
python scripts/storyboard_agent.py init --output LEDGER.json --project-name PROJECT
```

Ingest one event:

```bash
python scripts/storyboard_agent.py ingest --ledger LEDGER.json --event EVENT.json
```

Validate and summarize:

```bash
python scripts/storyboard_agent.py validate --ledger LEDGER.json
python scripts/storyboard_agent.py summary --ledger LEDGER.json
```

Record candidate evidence and a human decision without modifying formal rules:

```bash
python scripts/storyboard_agent.py candidate-check --ledger LEDGER.json --candidate-id ID --validator-test-id TEST --golden-test-id TEST --conflict-check pass --all-tests-pass
python scripts/storyboard_agent.py approve --ledger LEDGER.json --candidate-id ID --decision approve
```

After a clean pass and explicit human confirmation:

```bash
python scripts/storyboard_agent.py signoff --ledger LEDGER.json --scope-key SCOPE --decision approve
```
