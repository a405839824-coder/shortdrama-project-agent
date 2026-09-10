# Typed author decisions before canonical rendering

Use this bounded structural route for new source-to-storyboard authoring after the complete source/scene/phase plan. It does not rewrite existing frozen drafts, repair prose, replace independent audit, or change a quality threshold. The EP33 failed first draft stays failed and immutable.

## Entry and immutable attempts

From the repository root:

```powershell
.\.venv\Scripts\python.exe skills/shortdrama-storyboard/scripts/author_storyboard.py --input decisions.json --attempts-root .analysis/authoring-attempts --attempt-id EP34-A1
```

Library callers use `record_attempt(input_path, attempts_root, attempt_id=...)`. The underscore-prefixed renderer is an internal unit-test helper, not an unlogged production route. Only installed `jsonschema` and existing local validators are used; no model API or new MCP tool is introduced.

Every invocation with an accessible, writable attempt root creates an exclusive attempt directory before parsing the input. `input.original.json` preserves the exact raw bytes; `started.json` records the invocation and interrupted attempts; `report.json` records the hash, structural findings and terminal status. Invalid JSON, duplicate JSON keys, missing actors, invalid props, and ordinary parser/validator exceptions also remain recorded. An existing attempt ID is never overwritten: the rejected repeat receives a separate `rejected-*` directory containing its input and failure report. Explicitly empty IDs, path separators, Windows device names such as `CON` and `COM1`, and other invalid IDs receive the same recorded rejection. Omitting the ID alone requests an automatically generated ID.

Raw input and canonical bytes are written and flushed to a private staging file, then published using an exclusive atomic hard link. A write or flush failure cannot publish a partial `canonical.json`; a collision cannot replace an existing artifact. This requires an attempt filesystem supporting hard links. Storage failures can still prevent a raw snapshot or terminal report from being saved; the existing `started.json` and attempt directory remain the accounting record when they were successfully written. Process interruption may leave a staging file, which is not a published canonical artifact.

Rejected decisions produce no `canonical.json`. Successful rendering creates that file once and reports `rendered_pending_preflight`, `commit_eligible=false`, and `semantic_accuracy_percent=null`. This means structural rendering completed, not that the draft is usable or audited. Run the existing full content preflight and independent audit on the frozen canonical next.

Keep all attempt directories, including rejected and interrupted attempts, when accounting for generation attempts. A failed plan followed by a rendered plan is not a perfect first attempt. This tool neither measures the model's hidden drafting attempts nor computes a first-draft quality percentage.

## Input contract

The root object has exactly three keys:

| Key | Required value |
|---|---|
| `contract` | `typed-authoring-decisions-v1`, or `typed-authoring-decisions-v2` for explicit initial geometry bindings described below |
| `project` | Authored canonical project/source/evidence metadata, excluding computed root fields |
| `shots` | Nonempty ordered array of complete shot decisions |

`project` provides `source_lock` and `prevention_evidence` plus any currently allowed optional root metadata. The current canonical JSON Schema still applies. The renderer reads the fixed version constants and 27-field order from that schema. Do not supply a second `shots`, `total_duration`, `field_order`, or version-constant copy in `project`.

The author's existing source ledger must have unique explicit `source_id` values. Other source, cognition, source-treatment, shot-plan, performance and audio decisions remain caller-authored. The renderer adds only `scene_state_ledger` from the shot decisions and `audio_workflow.timing.events` from their events; those two blocks must not also appear in `project`. The timing object itself and its explicit `utterances` list are still supplied by the author. No audit evidence is synthesized.

Each v1 shot decision has exactly these keys. Every v2 shot also requires `staging`:

| Key | Type and meaning |
|---|---|
| `shot_id`, `shot_uid`, `episode_id` | Nonempty strings; stable identities unique and episode matching the display ID |
| `duration` | Finite positive JSON number at exact 0.1-second precision |
| `source_unit_ids` | Nonempty array of distinct source-ID strings present in the source ledger; no semicolon/newline delimiter or surrounding whitespace |
| `fields` | Exactly the 22 canonical fields other than `镜头编号`, `时长`, `累计时长`, `画面内容`, `覆盖原文` |
| `phases` | Exactly three `{start, end, text}` objects with authored nonempty text and numeric contiguous positive intervals from zero through duration |
| `events` | Explicit array; each provided event supplies `id, kind, actor, start, end, action, basis`, plus allowed existing timing-schema optional fields; do not repeat `shot_id` |
| `start_state`, `end_state` | Exactly the existing seven continuity fields, with explicit author decisions |
| `transition_explanation` | Nonempty authored description of the actual cut or boundary |

The renderer joins `source_unit_ids` with `；` into the canonical string `覆盖原文`; it does not change source text, map unknown IDs, deduplicate silently, or accept a competing manual coverage value. Source, shot, stable UID and prop identities reject surrounding whitespace and control characters; source IDs also reject the output delimiter. It renders the three phase markers around the unchanged phase text. It calculates per-episode cumulative and batch total time in integer tenths, without silent rounding. Raw JSON numeric tokens are retained for time validation, so a token such as `4.0000000000000000001` cannot first round to `4.0` and bypass the precision check. The episode order cannot reopen a completed episode.

For the seven state fields, `手持道具` must be an explicit array of `{prop_id, holder, state}` objects whose three values are nonempty strings. `[]` explicitly declares no held props. A given prop has one identity and one holder entry per state. The current strict holder/state checker is reused; free text in place of the array, missing state, holder arrays, and explicit delimiter-separated holders are rejected. Natural-language holder descriptions still require semantic review. Other state fields accept nonempty authored strings or objects, rejecting whitespace-only text, nulls and nested empty placeholders while preserving numeric coordinates and boolean observations. Explicit `未知`/`待核` remains unresolved text; it is never replaced with a pose or made equal to a neighboring state.

The current `timing_model.validate_timing` validates the provided speech source/segment/character references, speakers, registered text timing budget, then the action actor/basis, `after`/trigger dependencies and mouth-action overlap using those real speech spans. A valid action after a spoken utterance is accepted; an unknown speech reference or wrong speaker is rejected. The program does not infer an action actor from a nearby name, copy calculated spans back into the authored evidence, create measured timing evidence, or hide a conflicting duplicate copy. Full content preflight and independent semantic checks still follow rendering; a provisional text budget is not an audio/video measurement.

## Shared decisions and the semantic boundary

### Bind camera and screen wording from the actual initial geometry

For new authoring with explicit staging, select `typed-authoring-decisions-v2`. Each shot adds a `staging` object with exactly `contract: "initial-staging-text-v1"`, `geometry`, `target_label`, and `bindings`. `geometry` is a `staging-geometry-v1` scene using the actual initial camera, target and actor decisions from [staging_geometry.md](staging_geometry.md). Object shots may use `actors: []`; they do not invent a person. `target_label` names that actual camera target, such as an authored table center.

Use exact standalone tokens in `机位` and/or `起始状态`:

```json
{
  "bindings": [
    {"id": "camera", "kind": "camera_pose"},
    {"id": "pair", "kind": "screen_relation", "actor": "甲", "relative_to": "乙"},
    {"id": "side", "kind": "axis_side", "actor": "甲"}
  ]
}
```

For example, `机位` can contain `[[staging:camera]]50mm，中景。[[staging:pair]]`. The recorded renderer substitutes the camera position around `target_label`, its direction, and the perspective horizontal relation between the actual actors. `axis_side` describes a person's side of the current optical axis; it does **not** assert that the person is inside or outside the frame. Changing the camera changes those actual output clauses from the same input geometry, including left/right reversal. Author lens, crop, visibility, surfaces, timing and other prose explicitly as before. Do not duplicate calculated relations with a second manually written opposite sentence. If planning evidence needs the rendered camera string, use `staging_text_bindings.bind_fields` on the same input rather than a separate camera copy; it is not a complete-candidate preflight route.

Every v2 shot must actually use a `camera_pose` binding in `机位`. Every declared binding must be used; duplicate/unknown names, unused bindings, malformed tokens, missing actors, undefined bearings and explicit geometry contradictions are recorded rejections. Bindings must start an affirmative clause, cannot occur in quoted text, source fields or dynamic phase text, and do not modify adjacent prohibitions or quotations. Direction names are the geometry helper's eight compass octants; coordinates remain the precise input. A successful attempt saves `initial_staging_bindings.json` alongside the exact input and canonical, and records `input_contract`; it is still pending preflight with no semantic score or release permission.

The v2 marker prefix `[[staging:` is reserved throughout the input, including source IDs, literal source text, ledger values/keys, event evidence and transitions. Only the designated camera/initial-state fields may contain bindings. A literal source collision is a recorded rejection with the raw source intact; use v1 to represent that literal syntax rather than deleting or rewriting source content. Single, double, nested and unclosed quote scopes all prohibit binding expansion. `target_label` remains an authored name for the target point, not a verified entity lookup; the author and independent reviewer must ensure that this name refers to the actual point used.

This binds only designated **initial** clauses, not arbitrary prose, end-state motion or the whole state ledger. It does not prove field-of-view, offscreen exclusion, occlusion, clear eye detail, hand height, a wall clearance, source correctness or continuity. A literal conflicting clause outside the tokens is preserved for independent review. Do not use the initial diagnostic to certify a later turn or moving camera. Continue checking full actual fields and all cuts; the v1 compatibility route remains available and is not retroactively treated as v2.

Use the timestamped single-source procedure in [production_state_binding.md](production_state_binding.md). The start state, event before-state and end state have different time roles. Do not populate the entire start ledger from a later event's `eye.start`, or generate static focus from the core action's result. Fixed scene descriptions contain no stale current-person orientation. Camera words and camera-relative light directions must come from the same coordinates used by the final shot, with one compatible frame boundary.

Phase posture, head direction, camera position and the assertion that a face is visible must refer to the same author decision. Do not independently write opposite directions in `起始状态`, `机位` and phase text. After a turn, explicitly resolve the final direction and the next start; a camera facing the back of that head cannot be made a clear face merely by adding “面部清楚”. These linked facts are authored together before serialization.

This entry does not use NLP to correct those directions. It cannot prove source entailment, complete physical blocking, correct visual visibility, source ambiguities, meaningful pauses, true holder transitions, lighting physics or all-pair continuity. Unequal adjacent states stay unequal. Provided evidence remains exactly caller-authored; missing independent `entailed_by_source`, `closed`, coverage percentages, or certificates are not filled in. Preserve unknown source items and route the actual canonical and projections to independent semantic review.

This is an enforced input contract for `record_attempt`, not an operating-system immutability boundary or an exclusive gate around every existing canonical producer. Python callers can access internal helpers, and users with filesystem access can edit files outside this recorded route. The route alone therefore cannot prove complete attempt accounting or prevent someone from bypassing it. The production caller must require this route and the downstream preflight/audit; `commit_eligible=false` remains mandatory in its report. The 22 authored canonical fields and phase prose remain independent text values: cross-field physical consistency is still an author and independent-review obligation, not a typed pose engine.

The focused tests live in `tests/test_v0527_authoring_serializer.py` and are included by the existing `test_*.py` discovery entry in `scripts/run_tests.ps1`, `scripts/run_tests.sh`, and CI shards.

## Exact authored contracts

These are existing content-preflight requirements. After preserving raw input, v2 also checks the actual assembled `音色` and dialogue phases with the existing VOICE/PUNCT lexical validators before emitting a canonical. It rejects manual post-helper edits that violate those contracts, including a spoken voice on a silent shot. v1 keeps its original rendering scope; full content preflight remains required for both routes. These narrow checks certify neither character intent nor source fidelity. Preserve the first complete candidate and every recorded attempt.

Use [authoring_contract_blocks.md](authoring_contract_blocks.md) for the supported voice, eye-state, ordinary indoor-light, psychological-audio safety and independent-transition fragments. The helper accepts the actual explicit decisions and returns designated fragments; it must not be used to overwrite a conflicting pose or choose an unprovided lamp, eye state or receiving surface. Test a new shared ingredient on synthetic shots through the real formal/GPT conversions before expanding it across a chapter. This ingredient check does not permit repeatedly preflighting an unfrozen complete draft or hiding complete rejected author packages as planning.

| Ingredient | Required author decision and serialization |
|---|---|
| `prevention_evidence.shot_plan` | Each `shot_id` has `core_function`, `viewpoint_count`, `camera_position`, `main_action_stage`, `transition_function`; strict single-shot counts `main_camera_count` and `main_action_stage_count` are both 1. A detail shot supplies its actual `detail_shot_function`. `single_main_viewpoint`, `single_core_action` and `end_change` do not replace these keys. A frame size is not a camera position, and an end state is not automatically the cut's function. |
| Scene and light facts | Supply an explicit supported `space_type` (`indoor`, `outdoor`, `transition`), actual time, openings and light sources. A transition scene resolves the current side. Relevant openings must exist in the physical fields or supported scene anchor read by the checker, not only in a private author dictionary. |
| `光源方向` | From the real camera and source, identify image side and height, the current receiving surface, fall path and depth/weak fill. Stable accepted wording includes `落向`, `衰减`, `纵深`, `暗侧`; for example, a real side source can “从画面右侧上方落向当前纸面，亮度随案面纵深衰减”. Choose actual surfaces and reflectors; do not add unsupported light, smoke, openings or generic keywords to pass. Preserve correct negations. |
| `音色` | Author the baseline age, register, thickness, brightness and stable vocal characteristic; per utterance choose speech act, surface emotion and intent, then volume, tempo/rhythm, breath, pauses, exact emphasis, tail and 2–4 prohibited tones. Intent and expression must belong to that utterance, not a generic filler. |
| Voice serialization | Keep the 11 fixed labels in order: `基础声线`, `言语行为`, `当前表达`, `发声方式`, `音量`, `语速与节奏`, `呼吸`, `停顿`, `重音`, `尾音`, `禁止语气`. Use fullwidth colons; `发声方式：` is an empty group heading. Put actual dialogue emphasis in quotes and check it against that same utterance. Tail direction explicitly selects `上抬`, `下压`, `截断`, `发虚`, `落稳` or `短收`, with any needed specific execution. Visible speaking includes the fixed phrase `口型与对白一致`. No-dialogue voice remains `无对白，不适用。`. |

Consult the registered voice vocabulary and `_voice_template_errors` in `validate_storyboard.py` for exact lexical requirements. Preserve real character facts when making components explicit: `年龄感：幼童` must not become “少年” merely to satisfy a word matcher; `明暗偏暗` can state an already chosen dark timbre. Select a fitting registered speech act and add the specific action, rather than replacing a meaningful thanks, admission or conditional answer with an unrelated act. If a legitimate expression still triggers a lexical false positive, record the exact input for focused review; do not weaken source fidelity or invent intention.

Use `voice_block` to serialize the fixed labels and the explicitly chosen registered tail. The helper writes only `音色`; it does not write the time-specific `画面内容` or choose a question intention. For a genuinely upward question, for example, the voice can retain `尾音：上抬；...` while its actual phase describes the particular `上扬` or `疑问` delivery. PUNCT reads the actual picture field, not the voice block. Do not replace the voice enum with an unregistered synonym or copy all voice labels into the picture.
