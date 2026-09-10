# Production state binding

Use with `first_draft_quality_v2.md`, `strict_prompt_contract.md` and the existing chapter intake. This is an authoring procedure for the existing source, visibility, performance, continuity, timing and static-projection gates. It adds no alternative pass threshold or release route.

## Bind decisions before rendering fields

For each shot, keep a small internal decision record before writing the 27-field canonical. Record the source fact or conversation beat, the actual start state, the one core change, and the end state. For every phase, resolve these together:

| Decision | Required relationship |
|---|---|
| Person position, body and head direction | The head can reach the intended gaze target from this pose; a later turn is not already complete in the start frame. |
| Camera and frame bounds | The current pose exposes the face, hands, paper or feet actually required by the phase. A frontal face after turning cannot justify a frontal face before turning. |
| Visible people and eye intervals | Only actual people and visible parts belong in `可见主体`. Props and the offscreen roster belong in space/start state. Eye movement and blinks occur only while that person's eyes can be seen. |
| Prop holder, orientation and open/closed state | A paper changes holder only after receipt is established; a closed book reveals its inside page only after the visible opening. The focus and lighting must describe that same state. |
| Speech, thought and movement time | Place each event once. A turn done in the opening phase cannot happen again in the core phase. Serial speech and thought have separate intervals; movement may share speech time only when physically plausible. |
| Start-frame projection | Every positive clause in the actual GPT projection describes the same initial instant. Remove future completion, procedure notes and audit metadata; preserve the scope of prohibitions. |

Named source facts cannot be replaced by generic placeholders. A report that identifies a particular child retains that identity; a duty book cannot inherit a receipt's stamp or focus target. References to a protocol's prior owner or to a character's change of knowledge require actual authored presentation or an explicit source-treatment decision, not just a `覆盖原文` label.

Camera directions, lens choice and eye decisions are shot decisions, not shared prose defaults. Shared code may serialize validated decisions and calculate times/hashes; it must not invent gaze targets from “起始落点”, speech emphasis from the first two characters, a universal chest-up frame, a generic emotion, or fixed silent padding at both ends.

Keep one timestamped decision as the source of each state. `world_scene` contains fixed geometry, openings and light-source positions; current people, offscreen attendance and body directions belong to that shot's state. Derive the camera wording, image light side, framing, focus and state ledger from the coordinates actually used in the canonical. Do not validate one plan camera and then serialize its opposite, or retain a second `START_VISUAL` override. A necessary decision change updates all dependent fields before serialization and remains visible in the author record.

Distinguish frame zero from an event's before-state. An eye event at 0.3 seconds may start after an earlier gaze change; its source target does not overwrite the shot's initial target. Compare each event with the immediately preceding state at its own time, and each cut with the left end and right start. Static focus names something already present at frame zero: a blank writing region stays blank until the writing phase, and fingers cannot be described as relaxed before their release. Preparation is an actual authored action, not a universal “先看清” sentence that can contradict closed eyes.

Use a single explicit frame boundary. If feet or low hands are required, include them in that frame or change the action; a waist-up label and a full-body requirement cannot coexist. Test the final canonical camera and its actual projected static text, including any changed phase. Successful geometry on unused planning coordinates is not evidence for that output.

List each visible person as a separate top-level item in `可见主体`. Parentheses describe only that person's pose, body parts and visibility; they do not contain additional people. For example, `甲（与乙、丙、丁同框）` is an unresolved list syntax error, not proof that only one person appears. Rewrite all visible people as top-level items before applying the person-count gate. Ordinary annotations such as `仅后侧肩背与坐姿` or `左手与右手同框` remain valid body/pose descriptions.

For new author-decision packages, use the recorded typed rendering route in [typed_authoring.md](typed_authoring.md) before canonical preflight. Provide explicit source-unit arrays, three phases, event actors and structured prop states; call `scripts/author_storyboard.py` or its `record_attempt` library entry. Keep rejected and interrupted attempt records in the generation accounting. Structural rendering cannot prove that a phase posture and a clear-face camera assertion agree: author both from the same decision, retain unresolved source facts, and send the frozen canonical to independent semantic audit.

For staging with world directions, use [staging_geometry.md](staging_geometry.md) and `scripts/staging_geometry.py` on explicit camera, person, body, head and gaze coordinates. Derive world directions and image left/right from those same decisions before writing the prose. Check every changed phase, including the end of a turn, and carry positions forward in time: a moving group does not remain the same distance from a stationary messenger. The helper can disprove a back-facing clear-face claim or a reversed screen order. A clean result does not prove framing, occlusion, eye detail, comfortable anatomy, correct source interpretation or executable action. Do not turn it into an audit certificate.

For new coordinate-based authoring, use the v2 initial text bindings in [typed_authoring.md](typed_authoring.md#bind-camera-and-screen-wording-from-the-actual-initial-geometry). Bind actual camera and screen-side clauses into the canonical at serialization; do not merely run a geometry report and then retype different directions. This is a bounded dependency fix, not an automatic audit of remaining free text. Keep world light-source state free of shot-specific screen directions and receiving surfaces; those belong to the current camera/light fields. Track a sound event once on the scene timeline: a later continuous cut cannot reuse a footstep that already ended unless an explicit overlap or another source event is authored.

Place a blink, handover, body turn or focus change in its actual phase, once. An event described as happening near the end cannot sit in the opening phase. Light on a hand or paper must agree with that phase's actual hand height and holder. A dialogue that must finish before departure leaves separate time for the departure; time reserved for simultaneous speech cannot also certify a serial action.

The start-frame fields describe one instant. Put a standing-to-crouching range, a future camera allowance, an approach, travelling, writing or an offscreen hearing condition in its appropriate video/evidence field, not in a static field. Inspect actual projected text: quoted paper headings must retain their wording and punctuation; video eye policies must remain in the formal master and stay out of the still. Unknown mixed clauses must be resolved in the source decision, not silently deleted by a converter.

## Preserve the source's level of specificity

Keep known conversation content, conditions, answers and knowledge changes complete. When the source says only that a conversation or complete answer occurred but supplies no wording or specific answer, record that limitation and preserve the stated event through a grounded objective narration or an explicitly motivated sound omission. Do not invent medical details, a yes/no answer, an unidentified speaker, a precise interval or a claimed outcome merely to fill a dialogue template. This narrow treatment does not permit replacing supplied dialogue with a summary.

A source ambiguity that the storyboard does not resolve or contradict is a recorded source constraint, not automatically a generated error. For example, two local paper views need not declare an unsupported total paper count. If the authored shot actually requires a handover, simultaneous ownership, a concrete transfer route or a spoken answer, that decision must be established and audited; calling it source ambiguity does not close it. Freeze the distinction before the next evaluation, retain prior scores and audit records, and report any scope adjudication separately rather than retroactively improving a first-draft score.

## Close the complete cut, not only the current subject

Track the last observed state of offscreen people and props separately from unknown state. Do not turn “unknown” into a verified pose merely to make adjacent hashes match. Check the left end and right start for the full scene: a person shown seated cannot simply start at a distant desk; a delivered receipt must be visibly placed before it is flat on the next close-up.

### Immutable snapshots and named gaze dependencies

Use `scripts/production_state.py` when building a coordinate-based state chain. A `StateSnapshot` stores only immutable JSON bytes; `to_dict()` returns a new container tree each time. `ProductionStateChain.advance(next_state, label=..., reason=...)` validates and commits one new snapshot atomically. It never updates a previous snapshot or automatically follows a moved target. Keep the label and reason outside the seven physical fields. The chain records authored decisions, not proof that the action happened.

```python
from production_state import CONTRACT, ProductionStateChain, shot_states

chain = ProductionStateChain(initial_seven_fields, label="S001-frame0",
                             reason="Explicit authored initial state")
start = chain.current
next_state = start.to_dict()
next_state["人物位置"]["乙"]["position_xy"] = [2, 0]
# Only after the actual authored phase gives 甲 the corresponding gaze change:
next_state["视线落点"]["甲"]["position_xy"] = [2, 0]
end = chain.advance(next_state, label="S001-end", reason="乙 moves; 甲 explicitly follows")
shot_decision.update(shot_states(start, end))
project["prevention_evidence"]["authoring_evidence"]["production_state_contract"] = CONTRACT
```

Construct the following setup from `end.to_dict()`, not by reusing a mutable dictionary already stored as an earlier shot's `end_state`. Export each endpoint with `shot_states` or a separate `to_dict()` call. A returned dictionary is caller-owned and can still be edited; such an edit cannot rewrite the chain's stored snapshot. Do not treat exported hashes as independent continuity approval.

The explicit `production-state-chain-v1` contract uses the existing seven fields. `人物位置` contains actor identities and their physical `scene_id` and `position_xy` or `position_xyz`; props use those keys under `动作进度.物件位置与状态`. Every actor has an entry in `视线落点`, including a known actor outside the frame. Do not move offscreen people into the camera's current room. If there are no actors, both `人物位置` and `视线落点` may be the explicit string `无人物`.

For a named target, use `{"kind":"entity","target_type":"actor","target_id":"乙","status":"resolved","scene_id":"ROOM","position_xy":[2,0]}`; `target_type` may also be `prop`. A resolved reference must match the target's current declared scene and coordinates. When the entity declares xyz, the new reference must bind xyz too, including height. If both xy and xyz are supplied, they must agree. Coordinates are checked, not generated; this does not turn the head, prove eye reach, or certify a clear view.

When a target moves or its position becomes unknown, explicitly update the reference after a real authored gaze change, or use `{"kind":"entity","target_type":"actor","target_id":"乙","status":"unresolved","reason":"Target left this room; current gaze not yet authored"}`. An unresolved reference must omit scene and coordinates; it remains pending and provides no visible-gaze evidence. A declared `kind:"point"` with a distinct name, scene and coordinates can mean that the observer still looks at the former floor position. It must not reuse the entity's name to pretend the person remains there. Different physical scenes cannot support a resolved entity gaze through this bounded helper; do not invent visibility through a wall. Same-room offscreen targets remain valid known positions, subject to independent visibility review.

The typed v2 entry rechecks the actual submitted start/end fields and saves `production_state_dependencies.json` with successful attempts. With the explicit contract it requires per-actor dependency decisions. Without the opt-in, v2 still checks recognizable legacy `{name, position_xy}` references to declared actors/props; other legacy prose is listed as unexamined. Deleted legacy targets are recognized from the other endpoints. Typed v1 retains its historical compatibility and does not claim the new check. Neither mode proves that free-form phase prose occurred, that a wall permits sight, or that source facts are true; preserve full preflight and independent audit.

Separate posture from location in the author decision. A posture such as “站立，双手空着合在腹前” can remain unchanged after a permitted move; its old room name cannot. Serialize the current room from that person's current scene assignment, and inspect any retained free-text posture for a conflicting location. Apply this to the offscreen roster too: leaving the frame does not make a known location conflict harmless to the next shot.

Advance narrative progress at the actual trigger, not only at the final outcome. An interview may move through conditions explained, question asked, questions answered and willingness expressed; announcing the first part of a disposition is distinct from announcing all its terms or carrying it out. For each authored trigger, record the preceding state, the event and the resulting state, then carry that result to the next continuous start. Do not copy “尚未开始” across an event already visible or audible, or mark an apology completed merely because it was ordered. These are source-grounded decisions; a state label or matching hash alone is not evidence that the event happened.

The seven compared state fields describe the physical scene at that instant. Keep camera visibility, framing notes and bookkeeping labels such as `stage: end` or `stage: frame0` outside those field values. For example, a stationary person at `[0, 1]` remains at `[0, 1]` during a reverse angle even if their face leaves the frame; do not replace the person's world position with “当前画框无人”. `动作进度` records a real phase such as “递交尚未开始” or “接稳后已放手”, not the name of the ledger endpoint. A cut from one shot's end to another's start may describe the same instant, but matching endpoint labels cannot prove that its hands, gaze or props match. Preserve actual differences and source uncertainties for review; never copy the right start into the left end just to equalize hashes. An evidence explanation must describe that actual boundary, so continuous speech from one witness cannot inherit a generic “另一人单独取证” explanation.

Ordinary small offscreen movement may be physically possible, but possibility is not source evidence or a reviewed cut. Resolve meaningful changes in actual blocking, or identify an explicit permitted time transition. Cross-scene cuts state the location and whether the action is concurrent or later. The self-contained formal cell gives its own concrete state rather than “same as the previous shot”.

After choosing a clearer frame, revisit everything that depended on the former frame: facial visibility, listening reactions, gaze endpoints, background people, light in camera coordinates, static start and timing. Expanding the frame without this propagation is an incomplete repair.

## Batch repairs and keep evidence separate

Freeze the first candidate before its first preflight; never rewrite its hash or score after correction. Collect all findings from the applicable machine checks and complete independent semantic review before freezing the next repair candidate. While reviews run, proposed changes may remain a working plan. Mark them as proposals rather than another complete repair.

Group findings by their shared decision. For example, a reversed actor pose can cause camera, eye, light and static-frame failures. Rebuild that decision and all its dependent fields once, then inspect the entire affected scene and its boundaries for introduced faults. Follow the existing second-recurrence root-cause and third-recurrence upstream-rebuild policy; changing an action label does not prove such a rebuild happened.

Evidence assembly may copy source decisions, enumerate reviewed pairs and calculate hashes. It cannot manufacture independent entailment, continuity closure or zero residual counts. Keep unreviewed or unresolved rows pending. A regex false positive needs a minimal reproducer and a focused positive/negative code test; it is not permission to waive all findings in that category. Check the real exported strings as well as the canonical: a lost negation or a future-state leak can survive otherwise complete source and state ledgers.

Before assembling a complete candidate, check the exact ingredient contracts in [typed_authoring.md](typed_authoring.md#exact-authored-contracts): canonical shot-plan keys, per-utterance voice components and scene-grounded light wording. Keep creative decisions separate from deterministic label/quote/key serialization. Once a complete candidate exists, freeze it before full preflight; do not call a repaired complete candidate an unrecorded plan. Preserve every typed rejection and complete-candidate attempt.

Report source fact coverage, shot usability, contract completion and measured audiovisual results separately. Software regression totals and repaired-draft scores are not fresh-generation accuracy. Validate improved authoring on a genuinely new source sample with the intended production settings; preserve source ambiguities and label unmeasured timing or reference-image consistency.
