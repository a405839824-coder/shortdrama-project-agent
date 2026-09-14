# Native ChatGPT no-API performance diagnostics

Use this protocol only when the user explicitly asks to measure, compare, or diagnose storyboard duration in a native ChatGPT window and cannot instrument an API client. Ordinary production, audit, and repair runs do not add diagnostic markers.

## Evidence boundary

Plugin production/audit traces declare `measurement_scope=server_only`. They measure code running inside the MCP service and cannot measure ChatGPT reading, planning, hidden reasoning, text generation, client transport, or UI rendering.

A completed five-marker run declares `measurement_scope=tool_bracketed_chat`. Its timestamps are minted when the server receives each marker. It measures the gaps between tool calls in the active ChatGPT workflow, but it is not exact user-click-to-render end-to-end timing.

Never accept or invent a caller timestamp, queue duration, token rate, hidden-reasoning duration, model retry count, or causal percentage. Keep these unresolved without API/provider telemetry:

- time before the first marker;
- provider queue latency;
- hidden reasoning substeps;
- token count and token generation rate;
- client network/render subdivisions;
- final prose rendering after the last marker.

## Preconditions

1. Verify plugin version 0.5.3 or newer and the presence of `mark_performance_stage`.
2. Call `get_agent_status`, then create the role-specific `begin_project_task` work order normally.
3. Keep the production/audit/repair authority matrix unchanged. The diagnostic run binds to that work order, OAuth namespace, project, role, and MCP session.
4. Do not create a third window for timing. Production and independent audit remain separate; repair remains conditional on audit failure.
5. Record the thinking/reasoning mode only when the host or user explicitly exposes it. Otherwise use `unknown`; never infer it from response length.
6. Select the real route: `full_rebuild`, `episode_delta`, `first_independent_audit`, `repeat_audit`, or `audit_repair`.

## Five ordered markers

The server enforces this exact sequence. A retry of the same completed checkpoint is idempotent only when its IDs and numeric metrics are unchanged.

| Sequence | action | stage | Call immediately when |
|---|---|---|---|
| 0 | `start` | `task_started` | The work order is established and before reading/generating task content |
| 1 | `mark` | `inputs_ready` | All required unchanged/cached or newly read source, canonical, anchors, and boundary context are ready |
| 2 | `mark` | `plan_ready` | Chapter map, cognition, source treatment, beats, shot plan, and scope are locked, before detailed 27-field authorship |
| 3 | `mark` | `canonical_ready` | Every required shot has a complete 27-field canonical record, before the commit/audit tool call |
| 4 | `finish` | `delivery_ready` | Commit/export/handoff or audit certificate has returned and only the brief final user response remains |

Start returns `diagnostic_run_id`; pass it unchanged to all later markers. Attach only persisted project-scoped plugin `trace_id` values and the exact returned artifact ID. A short-lived read trace that is not persisted may remain unresolved and must not be counted as measured server time.

Do not put source prose, prompts, dialogue, shot fields, download URLs, credentials, OAuth identity, user names, or hidden reasoning in a marker. The tool schema accepts only IDs, enums, and bounded counts. Extra content is ignored and must not be persisted.

## Interval interpretation

The report maps marker gaps as follows:

- `source_and_context`: task start to inputs ready;
- `storyboard_planning`: inputs ready to plan ready;
- `canonical_authorship`: plan ready to complete 27-field canonical;
- `commit_export_and_delivery`: canonical ready to delivery ready.

Resolved plugin traces are summed separately as exact server time. The stage gap that contains a tool call also includes client/tool orchestration, so do not add stage gaps and plugin milliseconds as independent totals.

A single run can identify the dominant observed interval. It cannot prove whether hidden model reasoning, queueing, prompt size, rules, or client transport caused that interval. Use these conclusions:

- Small plugin share plus a large `canonical_authorship` gap supports “the plugin server is not the primary observed bottleneck”; it does not isolate provider internals.
- A dominant `storyboard_planning` gap makes workflow/context preparation the first A/B candidate; it does not justify deleting quality gates.
- A dominant `canonical_authorship` gap makes authored shot count, per-shot content volume, and declared reasoning mode the first A/B candidates.
- A large `commit_export_and_delivery` gap with a small server trace indicates unmeasured client/orchestration time around commit; do not label it server processing.

## Thinking-mode and workflow A/B

Do not rerun expensive production merely to create a comparison unless the user authorizes it. For a controlled pair, keep all of these fixed:

- exact source/canonical fingerprints and episode boundary;
- route, episode count, expected/actual shot count;
- R10-SPC-1 output contract and all validators;
- fresh-versus-continued window type;
- cache state, project role, service tier, and final deliverables.

Change one factor only, such as `medium` versus `extra_high` reasoning mode. Prefer at least two repeats per condition before making a causal claim. The plugin may show an observational difference after one matched pair, but it must keep `causal_claim_allowed=false`.

To test workflow cost separately, keep reasoning mode fixed and compare the standard full route with a deliberately optimized orchestration that reuses valid fingerprints and skips redundant bootstrap/source reads. Never compare a full rebuild against a delta and call the difference a thinking-mode effect.

## Quality invariants

Diagnostics never change the storyboard contract:

- 27 canonical semantic fields per shot remain complete.
- `空间站位描述`, `起始状态`, `音色`, `环境音效`, `环境动态`, `生成限制`, and all required performance/dialogue content remain per shot.
- `光影`, `光源来源`, `光源方向`, and `色温倾向` remain four separate per-shot fields.
- `画面内容` retains natural action order without numeric production clock labels and all required visible action, expression, dialogue, delivery, listener reaction, breath, and end state.
- Production, audit, repair, diagnostics, cache warm-up, and background jobs keep `legacy_generation_calls=0`, `seedance_batches_generated=0`, `model_authored_derived_chars=0`, and `duplicate_generated_bytes=0`.
- No validator, G0–G13 gate, independent audit layer, source coverage, or continuity rule may be skipped to improve timing.

## User-facing result

Return the diagnostic run ID, total tool-bracketed duration, four interval durations, dominant interval, resolved plugin trace count/server milliseconds/share, unresolved trace IDs, declared configuration, and the reasoning-mode comparison status.

State the conclusion at the evidence level actually supported. If no controlled pair exists, say that thinking-mode impact is still unproven and name the next controlled comparison. Do not promise a new minute target from one run.
