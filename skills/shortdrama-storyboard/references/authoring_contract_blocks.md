# Explicit author contract fragments

0.5.32 字段内容约束以 [field_content_contract.md](field_content_contract.md) 为准；该文件由唯一规则注册表生成。正式母版、GPT 和 R5 阅读页必须由同一份字段数据生成。遇到错列返回路径与原片段，不能静默删句或补造依据。

`scripts/authoring_contract_blocks.py` renders a small set of explicit author
decisions into the existing formal/GPT contract vocabulary. It returns fragments,
never an entire shot, state ledger, source adaptation, timing plan, or PASS record.
Retain the actual decisions and complete candidate through `record_attempt` in
the authoring entry. A successful fragment call is not a storyboard approval.

## Explicit voice without repeated label copying

```python
from authoring_contract_blocks import voice_block

fields["音色"] = voice_block(
    mode="spoken", dialogue_rows=[("甲", "还能来吗？")],
    baseline="年龄感为成年，中音区，厚薄适中，明暗中性，常态清楚。",
    speech_act="询问来访安排。", current_expression="温和，意在确认安排。",
    volume="正常交谈。", rhythm="短句自然说出。", breath="开口前自然吸气。",
    pauses="句末短停。", emphasis='“还能”。', tail_direction="上抬",
    tail_execution="末字温和而克制。", prohibited_tones=["喊叫", "嘲讽"],
)
silent_voice = voice_block(mode="silent", dialogue_rows=[])
```

This is a synthetic example, not a character default. Supply every spoken value
from the actual author decisions and bind emphasis to the real speaker/text
rows. The helper owns the eleven ordered labels and the empty `发声方式` heading;
it reuses the existing validator and accepts exactly one registered tail choice:
`上抬`, `下压`, `截断`, `发虚`, `落稳`, `短收`. Individual tail execution remains
separate, explicit text. Missing values, label/newline injection, and invalid
choices raise `ContractBlockError`; none is silently normalized or invented.
Silent mode accepts neither dialogue rows nor spoken parameters and returns
`无对白，不适用。`. Unsupported genuine prose can use the full authoring fields,
which remain subject to content preflight.

The helper only returns `音色`. Author punctuation execution in the actual
utterance phase, including its timing; do not copy the voice subtemplate there.
v2 `record_attempt` rechecks actual assembled VOICE/PUNCT after saving the raw
input, so a later string replacement can cause a recorded rejection. v1 is
unchanged. A successful helper or typed call is not a semantic audit or release.
Synthetic public entry coverage is in `tests/test_v0527_voice_author_contract.py`.

## Eye visibility and the actual initial instant

```python
from authoring_contract_blocks import eye_blocks

eyes = eye_blocks(subject="甲", visibility="visible", state="natural_open")
fields["起始状态"] += eyes["initial_state_append"]
fields["生成限制"] += eyes["generation_limits_append"]
```

`visibility` is required: `visible`, `hidden`, or `no_people`. For `visible`,
`state` must be `natural_open` or `source_closed`. A source-closed eye decision
requires a nonempty `source_basis`; this basis remains an author assertion to
check against the retained source, and is not rendered as visible scenery.

```python
closed = eye_blocks(subject="甲", visibility="visible", state="source_closed",
                    source_basis="原文明确甲已经睡着。")
hidden = eye_blocks(subject="乙", visibility="hidden", state=None)
objects = eye_blocks(subject=None, visibility="no_people", state=None)
```

`hidden` does not infer whether the face is visible, and does not assert an eye
state. `no_people` returns empty fragments. Both require `state=None` and no
source basis; `no_people` also requires `subject=None`. Half-closed, opening,
unknown, or other unsupported states are rejected instead of changed to open.
Write unsupported genuine states through the full authoring contract.

Apply each actor's static fragment only to that actor's actual initial state.
Open-eye focus is named, so a second actor's source-closed eyes remain closed.
For `visible/natural_open`, current generation preserves the declared natural
state when no eye event is authored. It does not require a repeated focus phrase,
an eye movement, an eyelid change or a blink solely to fill a checklist.
`legacy_components=True` explicitly reproduces the historical policy text and
its separate focus-field check; it is not selected by a missing event plan.
Closed, hidden, and no-person branches emit no generation eye fragment. Compose
actors separately; do not replace their names with a blanket open-eye rule.
The exact registered video policy belongs only in `生成限制`; formal output keeps
it and GPT projection removes its dynamic clauses. The helper supplies no blink
time or movement. Actual visible eye actions belong in `画面内容`; numeric event
timing is kept in the internal execution data, not inserted into that field.
It does not infer a later visible face from a hidden initial eye state.

## Psychological audio and transitions

```python
from authoring_contract_blocks import psychological_safety_suffix, transition_block

fields["心理音轨"] = actual_unchanged_audio_payload + psychological_safety_suffix()
fields.update(transition_block(method="hard_cut", destination_place="书房",
                               destination_subject="木牌"))
```

The suffix is only for the psychological/narration channel. It forbids mouth,
lip-sync and subtitles for that channel, excludes it from role-dialogue duration,
and includes it in actual audio occupancy. Preserve every payload character and
register its real audio interval. It does not authorize converting given dialogue
to narration or overlapping a visible speaker's mouth incorrectly.

`transition_block` supports `hard_cut` and `dissolve`, with optional concrete
destination place/subject labels. With no destination, it states only the cut
method. It does not borrow a previous shot's sound/state or defer staging to an
unspecified next shot. Prose, alternatives and registered cross-shot labels are
rejected. A plain label is not an entity lookup or source certificate.

## Explicit ordinary indoor light

```python
from authoring_contract_blocks import interior_lighting_blocks

light = interior_lighting_blocks(
    source={"period": "day", "kind": "paper_window", "name": "纸窗甲",
            "state": "closed", "world_side": "东", "height_m": 2.2},
    screen_side="右", receiving_surfaces=["甲的面颊", "木案"],
    weak_reflector="西侧木墙", main_tone="slightly_warm", shadow_tone="neutral",
    materials=["衣料", "木纹"], air="clear", exposure="normal",
)
fields["空间站位描述"] += light["spatial_state_append"]
fields.update(light["lighting_fields"])
fields["生成限制"] += light["generation_limits_append"]
```

Only call this after the actual source state, visible receivers, camera-derived
screen side, exposure and tones are decided together. Append the physical-source
fragment to the actual spatial state; do not overwrite actor positions or replay
an old shot's state. Replace the four lighting fields with this result only when
its inputs are that shot's actual decisions. The helper cannot verify geometry,
visibility, source height, existence of a reflector, or color fidelity.

Supported narrow cases are day diffuse sky light through an open/closed paper
window, or night light from an explicitly present and `lit` `oil_lamp`, `candle`,
or `lantern`. `night` alone cannot generate a lamp. Other pairings and unlit or
unknown sources are rejected. No direct-sun, weather, smoke, dust, fog, additional
lamps or people are inferred. Use the full contract for other real scenes.

The source object requires exactly the six example keys. World directions use
the eight compass points; screen side is `左`, `右`, or `正前`. Height must be finite
and positive. Receiver/material lists must be nonempty explicit labels; a missing
reflector is explicitly `None`. Tone choices are `neutral`, `warm`, `cool`,
`slightly_warm`, `slightly_cool`; the supported air/exposure choices are explicitly
`clear`/`normal`. Unsupported or malformed inputs raise `ContractBlockError`.

Synthetic regression coverage is in `tests/test_v0527_authoring_contract_blocks.py`.
It calls the actual standalone and server formal/GPT entrypoints, including mixed
open/closed eyes, cropped subjects, objects, speech/narration, day/night lighting,
retained paper text and rejected unregistered video actions. These checks do not
certify source fidelity, action feasibility, continuity, all preflight gates, or
any accuracy percentage.

## Explicit ordinary outdoor daylight

Use `scene_lighting_blocks` for an established outdoor day scene. It accepts the
same receiver, tone, material, clear-air and normal-exposure arguments as the
indoor helper, with this exact source shape:

```python
source = {"period": "day", "kind": "outdoor_daylight", "name": "院中天光",
          "state": "present", "world_side": "东", "elevation_deg": 45}
```

The elevation is finite and strictly between 0 and 90 degrees. The production
compiler supplies bearing, elevation and screen side from the single authored
`direction_xyz` world vector; the standalone fragment does not certify them.
The result explicitly says `室外日间`, supplies ordinary natural daylight and
never adds a window, lamp, weather, visible sun or metre-based source height.
It owns the same existing four lighting fields and generation-limit fragment.
Calling the old indoor-only entry with this new kind is an error.

Source/setting support and a permitted ordinary director lighting choice remain
distinct. An author assertion is not source evidence or a semantic approval.
No night daylight, below-horizon direction, exact zenith, unestablished source,
smoke, volumetric beam or autonomous weather effect is supported by this branch.
Bundled certificate regressions are in `scripts/test_outdoor_daylight.py`; full
frozen-plan replay controls are in `tests/test_v0553_outdoor_daylight.py`.
