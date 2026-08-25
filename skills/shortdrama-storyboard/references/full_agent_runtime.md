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

## Keep two authoritative shared files

Resolve these exact Library titles at the start of every full Agent task:

- `分镜项目Agent_项目中枢.json`: current project state, work orders, change requests, deliverables, decisions, capability gaps, and version history.
- `分镜项目Agent_问题台账.json`: audit events, issue recurrence, statistics, rule candidates, and sign-offs.

Validate the project hub with `python scripts/project_agent.py validate --hub HUB.json` and the issue ledger with `python scripts/storyboard_agent.py validate --ledger LEDGER.json`. Replace the same Library identities after accepted updates. If either write fails, preserve a local pending record and explicitly report which shared state is not synchronized.

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

## Classify intent before acting

Map every request to one operation in `project_agent_capabilities.json` and one mode:

- `plan_only`: analyze or propose; do not change the source artifact.
- `execute`: create or modify the requested artifact.
- `audit_only`: complete all checks; do not repair.
- `audit_and_repair`: audit, repair, impact regression, and final verification in one task.

Treat “给方案、分析、建议、怎么改” as `plan_only`. Treat “生成、改写、修改、新增、删除、替换、合并、拆分、移动、修正后输出” as explicit execution authorization for the named scope. Do not extend authorization to unrelated episodes, files, characters, scenes, or rules.

If the request mixes planning and execution, lock the selected plan first. When the user has already specified the exact change, that instruction is the selected plan; do not ask for redundant confirmation.

## Run the universal task lifecycle

1. Load and validate both shared files.
2. Create one work order with request text, window key, operation, mode, targets, expected outputs, and authorization.
3. Lock the authoritative source, latest artifact version, episode/scene/shot boundary, must-keep list, must-remove list, and protected scope.
4. Resolve conflicts in this order: user’s current explicit instruction, current authoritative modified story, current source text, current approved project state, older reference versions.
5. Perform the selected planning or execution workflow.
6. Produce a new version by default. Preserve the original unless the user explicitly requests replacement of the same artifact.
7. Validate every required downstream consumer and impact boundary.
8. For storyboard output, complete the eight-layer audit and repair loop internally when repair is authorized.
9. Record deliverables, versions, decisions, issue events, unresolved blockers, and capability gaps.
10. Mark a clean task `awaiting_signoff`; archive only after human approval.

Never claim completion when the work order, deliverable, issue ledger, or Library writeback is incomplete.

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
8. Seedance/i2v start state, action process, end state, camera, sound, environment, and light.
9. Character, scene, costume, prop, position, and lighting anchors.
10. Keyframe prompts or generated frames.
11. Voice, psychological audio, ambience, BGM cues, and transitions.
12. Audit statistics, change notes, and production batches.

When the authoritative input is a novel chapter, also propagate and validate the complete-read proof, chapter event map, causal graph, character state changes, protected-dialogue mappings, source treatment ledger, adaptation budget, beat plan, and previous/next boundary context. Any upstream change that alters an event, causal link, cognition transition, or protected line invalidates the dependent beats and shots.

Regenerate only the affected units plus boundary neighbors when the upstream structure remains valid. Regenerate the whole episode or scene when episode boundaries, character cognition, scene topology, or the core shot plan changes. Never patch only visible wording when the causal source is upstream.

### Addition

Insert the new beat into the script, establish start/end state, generate the required shots and independent Seedance batches, update numbering or stable insertion IDs, recalculate duration, and audit both boundaries.

### Modification

Replace the affected content, remove old residue, update cognition and state, regenerate affected shots and production prompts, and regress dependent neighbors.

### Deletion

Delete obsolete script and storyboard material, remove its source and batch mappings, bridge the preceding and following states, update timing and numbering policy, and verify no stale reference remains.

### Replacement

Treat as one atomic change: lock the old target, insert the approved new chain, delete all old residues, rebuild downstream outputs, and compare against must-keep/must-remove lists.

### Merge, split, or move

Recalculate dialogue capacity, shot load, rhythm, source coverage, continuity, numbering, and batch independence. Do not preserve an invalid shot structure merely to reduce editing.

## Produce visual, video, audio, and delivery assets

- Build character, scene, costume, prop, position, and light anchors before image or video generation.
- Build a separate visual-texture lock before keyframe, scene-frame, or i2v prompting. For every ancient-costume live-action prompt, load `songyun_humanist_cinematography.md` as the mandatory visual master and apply its real-camera texture, sourced-light method, restrained color logic, lens/depth selection, humanist composition, prompt order, and negative restrictions. For every night or low-light ancient-costume prompt, additionally load `ancient_night_cinematography.md` and apply its motivated practical/ambient sources, distance falloff, half-lit readable faces, transparent shadows, restrained warm/cool color, night-specific lens/depth selection, low-light film texture, continuity, and negative restrictions. Adapt concrete values to the era, time, weather, place, mood, and real light sources. Project aspect ratio, sourced light, visibility, continuity, and reference anchors always override any example value.
- Generate keyframe prompts with the established real short-drama screenshot style and negative restrictions. Use an image-generation tool when the user requests images and the required references are available.
- Keep each Seedance 2.5 batch independent from GPT history and other batches. Export the 10-column mother-template field as `Seedance 2.5独立正式执行分镜`. Keep the stable production range at 4–15 seconds; an explicit long-scene mode may use the officially supported upper limit only after a separate load, timestamp, and continuity review.
- Design BGM after picture lock from the whole-scene emotional key, narrative function, time motion, frequency space, and audible scene image. Keep BGM out of Seedance prompts.
- Design dialogue delivery with sound strength, rhythm, breath, pause, emphasis, and ending tone.
- Convert or export files through the appropriate document or spreadsheet workflow while preserving the canonical field order and formulas.
- Split production into traceable episode, scene, shot, keyframe, i2v, voice, ambience, and BGM packages.

## Audit, repair, and learn

Use `agent_runtime.md` for issue event structure, recurrence escalation, concise reporting, and guarded rule candidates.

- Audit-only tasks never modify artifacts.
- Audit-and-repair tasks close internally within two preferred and three maximum rounds.
- Record every repair result as original issue resolved, residual, or introduced.
- A second consecutive recurrence requires root-cause repair; a third requires upstream rebuild.
- Never activate a formal rule or validator automatically.

## Handle missing capabilities honestly

When a request is not in the capability registry or required tools, files, permissions, or source evidence are unavailable:

1. Do not simulate a completed capability.
2. Record a `capability_gap` with requested input, desired output, missing dependency, risk, safe fallback, proposed implementation, and tests.
3. Continue only with a safe in-scope fallback that does not misrepresent completion.
4. Require user approval before adding external access, new credentials, destructive behavior, or a formal rule.

The capability registry is open-ended: new functions enter as candidates, pass implementation and tests, then become supported. “All functions” means every request is either routed to a supported workflow or recorded as an explicit gap; it does not mean pretending unavailable tools exist.

## Keep responses simple

Default task reports:

- Plan: `方案完成｜推荐方案X｜影响N处｜待执行/无需执行`
- Execution: `处理完成｜新增N｜修改N｜删除N｜替换N｜回归通过`
- Storyboard: `生成完成｜N镜｜总时长Xs｜审计通过`
- Audit: use the concise formats in `agent_runtime.md`.
- Blocked: `任务阻塞｜原因一句话｜下一步一句话`

Keep detailed evidence, change matrices, and capability records in shared files unless the user asks for the full report.
