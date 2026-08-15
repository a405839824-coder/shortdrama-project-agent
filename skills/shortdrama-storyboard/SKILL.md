---
name: shortdrama-storyboard
description: "Operate a full-lifecycle Chinese short-drama project Agent with the R10 stable runtime and R10-PG1 gates: analyze novels and stories, design hooks and adaptation plans, plan episodes and character arcs, create or rewrite scripts, execute additions/modifications/deletions/replacements/merges/splits/moves, propagate script changes into formal storyboards and independent Seedance/i2v batches, manage character/scene/prop anchors, keyframe prompts or images, BGM and voice design, file conversion, production batches, versions, audits, repairs, cross-window issue learning, guarded rule improvement, sign-off, and explicit capability-gap proposals. Use for 分镜、短剧分镜、分镜项目Agent、全流程Agent、剧情策划、改写剧本方案、剧本新增修改删除替换、生成或重建分镜、Seedance提示词、关键帧、人物场景锚点、BGM配音、文件交付、审计修复、问题统计、规则升级或能力补全。"
---

# Short-drama storyboard R10

## Execute through the plugin MCP

1. In ChatGPT plugin mode, read `references/plugin_execution.md` and call `get_agent_status` before any project answer in a new conversation. This MCP check replaces a direct shell bootstrap in ChatGPT.
2. Do not say “已执行、已修改、已生成、已审计、已同步” unless the corresponding MCP tool succeeded and returned a `project_id`, `work_order_id`, or `artifact_id` as applicable. A prose-only answer is a plan, not execution.
3. Create or resume the project, call `begin_project_task`, import/read all required sources, then use the matching commit and validation tools. Keep the same `project_id` across chats so the project hub and issue ledger remain continuous.
4. For `audit_and_repair`, fix every reported issue internally and recommit the complete affected storyboard before replying. Do not require the user to trigger repeated audits. Never create a new audit round for an unchanged artifact fingerprint.
5. If the MCP tools are unavailable, say `插件未连接，当前未执行` and give one connection action. Do not silently fall back to claiming model-only execution.

## Lock the runtime first

1. In ChatGPT plugin mode, call `get_agent_status`; set `full_self_test=true` after a server version change. Outside plugin mode, run `python scripts/r10_bootstrap.py --self-test` from this skill directory at the start of every new conversation or after a version change.
2. If it reports a project root, read that project's `R10_BOOTSTRAP.md`, runtime, schema, conflict crosswalk, and task cards in their declared load order.
3. Otherwise, use the bundled references in this skill. Require `workflow_revision=R10-PG1`; state that the bundled reference runtime is active and do not claim that an external project passed integration.
4. Stop with `E-BOOTSTRAP-001` if the bootstrap or self-test fails. Never replace a machine failure with model self-assessment.

The only active version is `V16.3-148-R10-StableRuntime`. Treat R1–R9 rules as history and migrate them through `references/deprecated_rule_map.json`.

## Run the full-project Agent

1. Read `references/full_agent_runtime.md` and `references/project_agent_capabilities.json` for any project-Agent, planning, script, change, storyboard, production, or system-improvement task. Use profile `R10-PROJECT-AGENT-1`.
2. Resolve and validate both exact shared Library files: `分镜项目Agent_项目中枢.json` for work orders, changes, deliverables, decisions, versions, and capability gaps; `分镜项目Agent_问题台账.json` for audit events, recurrence, statistics, and rule candidates.
3. Classify the request as `plan_only`, `execute`, `audit_only`, or `audit_and_repair`. Never edit an artifact when the user asks only for a plan, analysis, or recommendation.
4. Create one project work order, then lock the current authoritative source, latest artifact, target boundary, must-keep, must-remove, and protected scope before acting.
5. Treat explicit “生成、改写、修改、新增、删除、替换、合并、拆分、移动、修正后输出” language as execution authorization only for the named scope. Preserve a prior recoverable version and never silently overwrite unrelated content.
6. For any script or story change, create an impact matrix and propagate the approved change through episode beats, source mapping, cognition, scene state, formal storyboard, Seedance, anchors, keyframes, audio, timing, numbering, statistics, and production batches as applicable.
7. Regenerate only the affected range plus boundaries when upstream structure remains valid; rebuild the whole affected scene or episode when cognition, episode boundary, scene topology, or core shot plan changes.
8. Complete required validation and impact regression, write both shared files, and wait for human sign-off before archive.
9. If a requested function or dependency is unavailable, record a capability gap with a safe fallback, implementation proposal, risks, and tests. Never pretend an unavailable function succeeded.

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
2. Lock the source range and episode boundary. Build Source Lock, Beat list, Source Ledger, Borrow/Delete/Merge/Compress ledger, and a character cognition matrix. Map every formal shot and every key line to source evidence.
3. Build the seven-field scene-state ledger: character position, body direction, gaze target, held prop, door/window state, action progress, and light direction. Require each same-scene end state to equal the next start state unless an on-screen transition explains the change.
4. Plan one core narrative/emotional stage and one viewpoint per shot. Require a story/information/emotion function for every detail close-up.
5. Select a matching task package from `references/task_package_cards.json` or the project's authoritative copy.
6. Set camera and visibility before performance. For dialogue and important emotion shots, build the complete stimulus → listen/observe reaction → gaze path → micro-expression → ecological action → body/breath → end-state chain.
7. Generate formal shots in the exact canonical field order from `references/storyboard_schema.json` and apply the four learned modules only when triggered.
8. Keep formal shots and i2v batches separate. Every batch must contain the ten independent execution fields and pass a blank-context test. Reject phrases such as “承接上一镜”, “保持关键帧”, and “同上”. Keep BGM out of Seedance prompts; design music cues only after picture lock.
9. Audit in the fixed order: source/dialogue → camera/visibility → spatial continuity → performance chain → shot rhythm → Seedance independence → prohibitions/audio → statistics.
10. Validate JSON with `python scripts/validate_storyboard.py INPUT.json --mode audit`; use `--mode repair` after corrections. Regress the defined impact scope. A second consecutive recurrence requires root-cause repair; a third requires upstream rebuild.
11. After any rule or validator change, run `python scripts/run_golden_tests.py`; require every bundled functional test to pass.
12. In Agent mode, also run `python scripts/run_agent_tests.py`; require every safety and lifecycle test to pass.
13. In full-project Agent mode, run `python scripts/run_project_agent_tests.py`; require every work-order, change-propagation, and sign-off test to pass.
14. Archive only after statistics close, residual and introduced issues equal zero, all machine checks pass, both shared files are synchronized, and a real human signs off.

## Close internally and report briefly

- When the user requests audit plus repair, complete audit → repair → impact regression → final verification inside the same task. Do not hand back an intermediate file or require the user to request “再次审计”. Multiple internal passes are allowed; multiple user-triggered passes are not the default.
- When the user requests audit only, finish all eight layers before reporting and do not repair. If they later request repair, perform the repair and the complete internal regression before delivery.
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
- Read `references/plugin_execution.md` whenever the ChatGPT plugin MCP is available or plugin execution is being diagnosed.

Keep evidence concrete: include shot ID, field, source sentence, computed duration, batch ID, or file/version status with every warning or failure.
