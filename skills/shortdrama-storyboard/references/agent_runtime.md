# R10 safe storyboard project Agent

Use this policy together with `V16.3-148-R10-StableRuntime` and `R10-PG1`. The Agent policy version is `R10-AGENT-SAFE-1`.

## Keep one authoritative cross-window ledger

- Use the exact Library title `分镜项目Agent_问题台账.json` as the authoritative cross-window issue ledger.
- At the start of Agent mode, resolve that exact title, load its latest version, and validate it with `python scripts/storyboard_agent.py validate --ledger LEDGER.json`.
- If the file does not exist, initialize it once. If Library access is unavailable, write a local pending event bundle and state that it is not yet synchronized; never claim cross-window collection without a successful ledger write.
- Treat the shared ledger as authoritative for exact counts. Use the current audit report and current file as primary evidence. Use prior chat context only as supplementary evidence and label any imported pattern as unquantified when exact event records are unavailable.
- Replace the existing Library file after each accepted audit event. Preserve the same file identity instead of creating date-stamped duplicates.

## Record every audit and repair

For every generation audit, audit-only run, repair, or repair regression, create one `audit_event` that satisfies `references/agent_ledger_schema.json`.

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
6. Seedance独立执行
7. 禁项与声音
8. 统计闭合

公共场景人物密度、地点角色、错峰动作、空间层次、具体环境声、对白避让、声画来源和Seedance复现均归入第7类“禁项与声音”；同场人流跳变同时在第3类“空间连续”记录。

The Agent computes the issue key from project, episode, logical file, error code, shot IDs, field, and issue type. Do not use mutable evidence text or content version as the only identity; the same defect must remain traceable after a repair changes the file.

## Close within two or three internal rounds

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
