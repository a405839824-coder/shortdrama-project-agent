---
name: shortdrama-storyboard
description: "Operate a full-lifecycle Chinese short-drama project Agent with the R10 stable runtime and R10-PG1 gates: analyze novels and stories, design hooks and adaptation plans, plan episodes and character arcs, create or rewrite scripts, execute additions/modifications/deletions/replacements/merges/splits/moves, propagate script changes into formal storyboards and independent Seedance/i2v batches, manage character/scene/prop anchors, keyframe prompts or images, BGM and voice design, file conversion, production batches, versions, audits, repairs, cross-window issue learning, guarded rule improvement, sign-off, and explicit capability-gap proposals. Use for 分镜、短剧分镜、分镜项目Agent、全流程Agent、剧情策划、改写剧本方案、剧本新增修改删除替换、生成或重建分镜、Seedance提示词、关键帧、人物场景锚点、BGM配音、文件交付、审计修复、问题统计、规则升级或能力补全。"
---

# Short-drama storyboard R10

## Lock the runtime first

1. When the R10 MCP plugin is available, call `get_agent_status(full_self_test=false)` once at the start of a new chat. If its fingerprint-bound runtime-test certificate is valid, use it and do not inspect plugin source, run local bootstrap, or rerun system suites for an ordinary content task.
2. Call `get_agent_status(full_self_test=true)` only after a runtime/rule/schema/script/dependency change, or when the certificate is missing or invalid. Stop on failure; never replace a machine failure with model self-assessment.
3. When the plugin is unavailable and this skill runs standalone, run `python scripts/r10_bootstrap.py --self-test`. If it reports a project root, read that project's `R10_BOOTSTRAP.md`, runtime, schema, conflict crosswalk, and task cards in their declared load order; otherwise use the bundled references.
4. Always require `workflow_revision=R10-PG1`. Stop with `E-BOOTSTRAP-001` when the applicable status/bootstrap check fails.

The only active version is `V16.3-148-R10-StableRuntime`. Treat R1–R9 rules as history and migrate them through `references/deprecated_rule_map.json`.

## Run the full-project Agent

1. Read `references/full_agent_runtime.md` and `references/project_agent_capabilities.json` for project intake, planning, script/story changes, cross-artifact propagation, system improvement, standalone operation, or lifecycle diagnosis. For an ordinary plugin-backed storyboard create/change/audit/repair with a valid runtime certificate, load `references/plugin_execution.md` and `references/incremental_storyboard_execution.md` instead; the service enforces the work-order lifecycle.
2. In plugin mode, use its authenticated `project_id` hub and issue ledger as the operational source of truth; do not fetch or rewrite the two global Library ledgers during an ordinary content task. Resolve and update `分镜项目Agent_项目中枢.json` and `分镜项目Agent_问题台账.json` only for standalone operation, explicit shared-ledger work, system/rule architecture updates, or final archival synchronization.
3. Classify the request as `plan_only`, `execute`, `audit_only`, or `audit_and_repair`. Never edit an artifact when the user asks only for a plan, analysis, or recommendation.
4. Create one role-specific project work order, then lock the current authoritative source, latest artifact, target boundary, must-keep, must-remove, and protected scope before acting. Keep an `audit_and_repair` request in one workflow chain, but use a read-only audit work order first and a distinct repair work order only after audit failure and explicit authorization.
5. Treat explicit “生成、改写、修改、新增、删除、替换、合并、拆分、移动、修正后输出” language as execution authorization only for the named scope. Preserve a prior recoverable version and never silently overwrite unrelated content.
6. For any script or story change, create an impact matrix and propagate the approved change through episode beats, source mapping, cognition, scene state, formal storyboard, Seedance, anchors, keyframes, audio, timing, numbering, statistics, and production batches as applicable.
7. Load `references/incremental_storyboard_execution.md` for every storyboard generation, regeneration, change, audit, or repair. For an existing canonical, regenerate only the affected stable-UID range plus exact boundaries when upstream structure remains valid; rebuild the whole affected scene or episode when cognition, episode boundary, scene topology, or core shot plan changes. A first full draft still uses the full route.
8. Complete required validation and impact regression, write both shared files, and wait for human sign-off before archive.
9. If a requested function or dependency is unavailable, record a capability gap with a safe fallback, implementation proposal, risks, and tests. Never pretend an unavailable function succeeded.

## Keep production and independent audit separated

1. Keep two independent windows/authorities for every formal workflow: `production` and `independent_audit`. Create the third `repair` window only after the independent audit fails and repair is explicitly authorized. Production may issue only `production_preflight`; only the original read-only audit authority may issue the final pass/fail conclusion. A producer or repair claim is never an audit result.
2. Create one canonical representation for each artifact version and treat it as the only structured truth for generation, export, repair, and audit. Bind it to the source, episode boundary, raw artifact, normalized workbook semantics, parser, schema, runtime, rule, validator, and project-anchor fingerprints. The canonical must reproduce normalized cells/formulas/order, not ZIP metadata or raw XLSX bytes. Reject competing or stale canonical copies.
3. At every production-to-audit, audit-to-repair, or repair-to-audit boundary, create a signed versioned handoff manifest. Atomically claim it once, verify its type, workflow chain, original audit authority, exact fingerprints, authorized scope, objective evidence, cache manifest, and stage-timing trace, then mark it consumed. A repair result must return to the original audit work order/window/session and audit cycle.
4. Reuse objective facts only after fingerprint verification: source bytes or normalized text, chunk/read proof, workbook cells and formulas, deterministic canonical parsing, approved project anchors, and an unchanged-runtime test certificate. Treat chapter maps, source mappings, protected-dialogue lists, cognition matrices, scene states, coverage figures, and production preflight findings as claims to test independently.
5. The independent audit must recompute source and dialogue coverage, causality, character cognition, seven-field continuity, visibility, performance, rhythm, public-scene people/audio ecology, Seedance independence, prohibitions, statistics, residuals, introduced issues, and the final verdict. Never copy the production preflight conclusion into the audit ledger.
6. For ordinary content generation, audit, repair, or archive tasks, verify an existing full-test certificate whose runtime, rules, schema, validator, scripts, dependencies, and environment fingerprints all match; do not invoke `ALL_RUNTIME_MODULES` or rerun the full functional suites. Rerun every required suite after any rule, validator, schema, script, dependency, or runtime change, or when the certificate is absent or mismatched.
7. Cache only content-addressed evidence. Invalidate affected cache entries when source, boundary, artifact, canonical parser, schema, runtime, rules, validator, project anchors, cognition, scene topology, core shot plan, or target-model contract changes. A cache mismatch is a miss, never a warning-only fallback.
8. Record server-observable stage durations and the counts/bytes actually measured for bootstrap/status, source read, canonicalization, delta scope/merge, materialization, validation, repair, export, and ledger/handoff. Never invent call counts, source characters, payload sizes, retries, or model-stage timing: list ChatGPT planning/generation and every other unobservable stage in `unmeasured_stages`. Mark plugin traces `measurement_scope=server_only`; do not claim an end-to-end efficiency gain without a separate comparable caller replay.
9. If an older or external artifact has no handoff, let the audit window create a read-only `audit_intake` manifest and canonical before auditing. Absence alone is not failure; an existing handoff whose fingerprints disagree is blocking.
10. Use `commit_storyboard_patch` only for an `independent_audit` certificate's confirmed non-structural shot-field issues. Require the exact base canonical and raw-artifact fingerprints, stable shot IDs, certificate issue codes, authorized impact scope, and an unchanged work-order protected scope. Reject episode/scene boundaries, character cognition, source evidence, shot identity/order/timing, scene topology, or core-plan changes with `full_rebuild_required`. A successful patch creates a new JSON/XLSX version, runs the complete R10 validator, and returns to the original independent audit authority before sign-off.
11. Use production-authorized `commit_storyboard_delta` for user-directed changes to an existing storyboard range. Send only the changed complete atomic shot records, closed batches, stable UIDs, target scope and exact left/right boundaries. The server must compare-and-set the parent hashes, reject protected or cross-boundary edits, preserve untouched semantic hashes, deterministically materialize only declared derived fields, export a full canonical JSON/XLSX, and run the same complete 49 validators and all project gates. Delta reduces transfer/regeneration work; it never reduces quality gates and cannot be called by audit or repair authority.

## Start the safe project Agent

1. Read `references/agent_runtime.md` and use Agent policy `R10-AGENT-SAFE-1` when the user requests a project Agent, cross-window collection, recurring-problem statistics, or rule improvement.
2. Resolve the exact shared Library title `分镜项目Agent_问题台账.json`. Create it only if no exact match exists. Validate the latest ledger before use and replace that same file after every accepted event.
3. Treat exact ledger events as the only source for exact occurrence counts. Import older chat summaries only as unquantified historical patterns; never invent shot-level counts or claim that inaccessible windows were collected.
4. Record one structured event for every audit, repair, and regression with `scripts/storyboard_agent.py`. Keep the full evidence in the ledger and the user-facing report brief.
5. Prefer two internal repair rounds and enforce a hard maximum of three. Keep `can_advance=false` after `fail_current`; after a clean pass, wait for explicit human sign-off.
6. On a two-round recurrence, require root-cause repair and create a rule candidate. On a three-round recurrence, require upstream rebuild.
7. Never let the ledger mutate formal rules. Require a deterministic validator change, positive/negative/regression tests, conflict validation, all test suites passing, and explicit human approval before a separate intentional skill update can promote a candidate.
8. If the shared ledger cannot be read or written, preserve a local pending event and report `跨窗口台账未同步`; do not silently continue as if synchronization succeeded.

## Execute the gated workflow

1. Lock one authoritative input, task scope, protected scope, and `R10-PG1`. Build must-include and must-exclude lists before editing.
2. For any novel, chapter, or continuous prose input that will become a storyboard, first load `references/novel_to_storyboard_intake.md`. Finish the complete authorized chapter read before planning: chunked reading is allowed only for extraction, and generation must wait until every chunk is synthesized into one chapter map. Build boundary context, event/causal map, character start-change-end states, cognition limits, multi-axis source treatment ledger, protected-dialogue list, adaptation budget, and beat plan. Require 100% coverage in the chapter preflight; stop on unresolved ambiguity or incomplete reading.
3. Lock the source range and episode boundary. Build Source Lock, Beat list, Source Ledger, Borrow/Delete/Merge/Compress ledger, and a character cognition matrix. Map every formal shot and every key line to source evidence. Never treat C-grade prose as automatically deletable; deletion requires explicit authorization or redundancy evidence, and any time/space/mood/foreshadowing function must be retained elsewhere.
4. Build the seven-field scene-state ledger: character position, body direction, gaze target, held prop, door/window state, action progress, and light direction. Require each same-scene end state to equal the next start state unless an on-screen transition explains the change.
5. Plan one core narrative/emotional stage and one viewpoint per shot. Allow a causal continuous action chain inside that stage; split only when a second independent turn, viewpoint, location, or time jump appears. Require a story/information/emotion function for every detail close-up.
6. Select a matching task package from `references/task_package_cards.json` or the project's authoritative copy.
7. Set camera and visibility before performance. For dialogue and important emotion shots, build the complete stimulus → listen/observe reaction → gaze path → micro-expression → ecological action → body/breath → end-state chain.
8. Before every ancient-costume live-action keyframe, scene-frame, or i2v prompt, load `references/songyun_humanist_cinematography.md` as the mandatory visual master and lock visual texture separately from story content. Apply its real-camera texture, sourced-light decision, restrained color, lens/depth selection, humanist composition, prompt order, and negative restrictions to every ancient-costume prompt. For night, post-sunset, pre-dawn, candlelit, moonlit, firelit, rainy-night, snowy-night, or otherwise low-light scenes, also load and apply `references/ancient_night_cinematography.md`. Adapt the exact palette and exposure to the scene's era, time, weather, location, mood, and real light sources. Reject letterboxing, direct camera gaze, fixed warm daylight, fixed red-candle lighting, and fixed lens parameters when they conflict with the project.
9. For every street, market, commercial street, alley, gate, dock, station, public restaurant/teahouse, temple-fair, shop public area, or other public-flow scene, load `references/public_scene_ecology.md`. Require location-appropriate ordinary people with explicit density, roles, staggered actions, spatial layers and same-scene continuity. Also require source-matched near/mid/far ambience, dialogue ducking when applicable, and the same people/sound ecology in the independent Seedance batch. An empty public scene passes only with both a visible empty-state description and a plot-grounded reason.
10. Generate formal shots in the exact canonical field order from `references/storyboard_schema.json` and apply the learned modules only when triggered. For a delta route, author only the affected atomic semantic records and let the deterministic materializer rebuild declared redundant execution text, timing and batch mappings. The caller must still provide valid displayed shot numbers; the service validates their uniqueness and order but does not silently renumber them. Fingerprint one fully materialized canonical object before exporting; the workbook, formal mother template, and Seedance batches must all derive from that same object.
11. Keep formal shots and i2v batches separate. Name the 10-column mother-template execution field exactly `Seedance 2.5独立正式执行分镜`. Every batch must contain the ten independent execution fields and pass a blank-context test. Reject phrases such as “承接上一镜”, “保持关键帧”, and “同上”. Keep BGM out of Seedance prompts; design music cues only after picture lock. For 即梦/Dreamina/Seedance output, load `references/jimeng_timing_policy.md`: keep numeric production timing in internal `动作与表演`, remove hard timecodes from formal `画面内容` and the merged 10-column D field, and use Seedance 2.5 timestamp syntax only inside the model-specific independent prompt. Keep the stable production batch range at 4–15 seconds; use the officially supported 30-second ability only in an explicitly requested long-scene mode with its own continuity and load review.
12. Run the chapter coverage audit before the shot-level audit when `source_ingestion.mode=chapter_to_storyboard`. Recompute exactly eight audit layers: source/dialogue → camera/visibility → spatial continuity → performance chain → shot rhythm → Seedance independence → prohibitions/public-scene people/audio ecology → statistics.
13. Validate JSON with `python scripts/validate_storyboard.py INPUT.json --mode audit`; use `--mode repair` after corrections. Regress the defined impact scope. A second consecutive recurrence requires root-cause repair; a third requires upstream rebuild.
14. After any rule or validator change, run `python scripts/run_golden_tests.py` and `python scripts/test_public_scene_ecology.py`; require every bundled functional test to pass.
15. In Agent mode, also run `python scripts/run_agent_tests.py`; require every safety and lifecycle test to pass.
16. In full-project Agent mode, run `python scripts/run_project_agent_tests.py`; require every work-order, change-propagation, and sign-off test to pass.
17. Archive only after statistics close, residual and introduced issues equal zero, all machine checks pass, both shared files are synchronized, and a real human signs off.

Steps 14–16 are change-triggered regression suites, not per-content-task work. For an unchanged runtime in an ordinary content task, verify the matching full-test certificate and continue with all content gates and eight audit layers; never use a certificate to reuse a content-quality conclusion.

## Close internally and report briefly

- When the user requests audit plus repair, keep one workflow chain but separate role-specific work orders and transport sessions: original independent audit → immutable repair package → conditional repair window creates a new version → impact regression → return to the original independent audit work order for final verification. Never let production, repair, or a replacement auditor sign the final verdict.
- When the user requests audit only, finish all eight layers before reporting and do not repair. If they later request repair, perform the repair and the complete internal regression before delivery.
- A production response may say `生产预检通过｜待独立审计`; it must not say `审计通过`. Only an `independent_audit` event bound to the exact canonical and artifact fingerprints may use the clean-audit wording below.
- Keep the full evidence ledger internally, but default the user-facing audit response to concise Chinese: conclusion, issue count, affected shot IDs with one-sentence problems, and next action. Do not expose long rule explanations unless the user asks for a detailed report.
- Use `审计通过｜阻塞错误0｜原问题残留0｜修改新增问题0` for a clean result. For a failed result, use `审计未通过｜共N项` followed by a short numbered list grouped by shot or issue type.
- Never label work complete while blocking, residual, or introduced issues remain. If closure is blocked, state the exact blocker in one short sentence.

## Enforce the single conflict order

Use this priority without reordering:

`安全合规 > 源文忠实 > 分集边界 > 台词容量 > 统计闭合 > 可见性 > 图生视频可执行性 > 节奏留存 > 美学细节`

Always enforce these resolved rules:

- Calculate dialogue at 2.8 Chinese characters/second plus pause budget. Treat 3.5 only as a non-boundary reference.
- Use 3–5 seconds = 0–1 blink and >5–10 seconds = 1–2 blinks; allow a 1–2 second no-blink hold for shock/recognition; never synchronize two people; omit blinking when eyes are invisible.
- Specify camera movement as type + subject + path/trigger + narrative effect. Allow one main movement per generation batch.
- Describe important emotional shots with at least four visible actor-detail categories; never use an abstract emotion word as the full performance direction.
- Design core dialogue with sound strength, speech rhythm, breath, pause, emphasis, and ending tone; connect punctuation to delivery.
- Keep lighting sourced and continuous across a scene. Do not use bottom light for ordinary romance.
- Mark psychological audio as no mouth movement, no lip-sync, no subtitles, and excluded from role-dialogue counts.
- Fail batches shorter than 4 seconds or longer than 15 seconds; warn above 12 seconds.

## Load references selectively

- Read `references/four_source_methods.md` for expression, 16 movements, shot-size/light design, punctuation, and voice templates.
- Read `references/songyun_humanist_cinematography.md` before every ancient-costume live-action visual prompt. Treat it as the mandatory visual master for real-camera texture, sourced-light decisions, restrained color, optical depth, lens selection, candid humanist composition, prompt structure, and negative restrictions. Adapt its concrete palette to the scene facts; do not omit the master because the scene is night, interior, dangerous, rainy, or non-Song.
- Read `references/ancient_night_cinematography.md` in addition to the global master for every ancient-costume night or low-light prompt. Use it to lock motivated practical and ambient sources, distance falloff, half-lit readable faces, transparent shadows, restrained warm/cool color, night-specific lens/depth choices, low-light film texture, continuity, and night prohibitions.
- Read `references/jimeng_timing_policy.md` for every 即梦、Dreamina or Seedance prompt, any request about seconds/timecodes in `画面内容`, and every 10-column mother-template export that may be copied into a generation model. Keep formal visual description, internal production timing, and model-specific execution syntax separate.
- Read `references/novel_to_storyboard_intake.md` before converting any novel chapter or continuous prose into a script, beat plan, formal storyboard, or production batch. Require complete-read proof, lossless source treatment, protected dialogue, state/cognition transitions, adaptation budget, beat planning, and pre/post coverage audits.
- Read `references/public_scene_ecology.md` for every public-flow location. Apply its ordinary-person density/role/action/layer requirements, concrete environmental sound layers, audiovisual source matching, dialogue ducking, Seedance propagation, empty-scene exception and continuity gates.
- Read `references/storyboard_runtime_rules.json` for executable policy, catalogs, priorities, and error definitions.
- Read `references/storyboard_schema.json` before creating or converting storyboard JSON.
- Read `references/conflict_crosswalk.json` when old and new rules disagree.
- Read `references/error_catalog.json` for evidence-backed repairs.
- Read `references/task_package_cards.json` to bind task type to required modules and outputs.
- Read `references/validator_ruleset.json` and `references/atomic_rule_registry.jsonl` for handler-level or atomic-rule audits.
- Read `references/template_library.md` when producing formal ledgers, shot tables, audit reports, or archive forms.
- Read `references/golden_tests.json` only when changing or diagnosing the validator.
- Read `references/agent_runtime.md` and `references/agent_ledger_schema.json` when operating or diagnosing the project Agent.
- Read `references/full_agent_runtime.md`, `references/project_agent_capabilities.json`, and `references/project_agent_schema.json` for full-lifecycle routing, script/storyboard changes, project state, production tasks, or missing-capability handling.
- Read `references/incremental_storyboard_execution.md` for the two-permanent-plus-conditional-repair window protocol, full-versus-delta routing, stable-UID/CAS scope protection, delayed deterministic materialization, and honest performance measurement.
- In the bundled MCP plugin, also read `references/plugin_execution.md` before any tool-backed task so the tool route, persistence proof and window authority match the current server contract.

Keep evidence concrete: include shot ID, field, source sentence, computed duration, batch ID, or file/version status with every warning or failure.
