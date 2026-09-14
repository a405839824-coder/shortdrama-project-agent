# Production decisions v1

0.5.33 新生产先冻结 [production_plan_contract.md](production_plan_contract.md)；实际决定须匹配计划，编译器重算状态和输出。完整规则见 [production_prevention_contract.md](production_prevention_contract.md)。纯函数兼容旧输入不代表执行过新生产计划检查。

0.5.32 字段内容约束以 [field_content_contract.md](field_content_contract.md) 为准；该文件由唯一规则注册表生成。正式母版、GPT 和 R5 阅读页必须由同一份字段数据生成。遇到错列返回路径与原片段，不能静默删句或补造依据。

This contract is a producer for the existing typed-authoring v2 format. It is not a source, performance or release certificate. Production callers must freeze the original complete input through `record_attempt`; calling a pure lowering function is not attempt accounting.

`lower_production_decisions(payload, *, source_compilation=None)` returns `typed_decisions` and `derivation`. `ProductionDecisionError` exposes `path` and `reason`. This reference fixes the initial acceptance cases before implementation.

The sole initial world owns every actor, prop and named point. Shots do not accept authored endpoint states, phase text, event basis, staging or spatial canonical fields. Actions own their duration and effects. Three macro phases are generated from four integer ticks, measured in tenths of a second. No rounding is permitted. Snapshot, prose, event basis and later-shot state are derived together.

Initial implementation scope: explicit 3D landmarks, fixed perspective camera, body/head yaw, hand locations, an explicit reach origin, referenced gaze, known vertical opaque barriers, object possession, current written text, and actions hold/move_actor/move_prop/turn/look/point/blink/transfer/speak/narrate/write. It does not infer unspecified actors, anatomy, walls, source facts, speech content or lamps. Unsupported dependencies fail explicitly. Landmark visibility is not a full mesh-occlusion or cinematography certificate.

## Frozen acceptance cases

All cases use public synthetic identities only. Positive controls must reach the real typed renderer where structurally applicable; negative controls must fail for the named reason, not an unrelated missing field.

1. Moving a free paper changes its later state, later initial frame and text together; the earlier exported snapshot stays unchanged.
2. Deleting the paper move preserves the original location; supplying a different manual endpoint or phase text is rejected.
3. Changing only camera position reverses the independently expected screen relation; neither static prose nor the staging record can retain the old relation.
4. An actor facing away cannot be rendered with visible frontal eyes. A turn creates appropriate sampled visibility intervals, not another macro phase.
5. A known opaque barrier blocks an asserted gaze or reach. An actor in another physical scene cannot be a resolved current gaze target.
6. Moving a named target does not silently track it. A deliberate tracking look is allowed; an unchanged stale gaze is rejected.
7. An action outside the shot is rejected with its actual action path. Continuous hold/move/turn/look/point actions spanning macro boundaries are intersected with those phases automatically; their effects occur once and dependencies resolve to the final fragment. An atomic blink entirely inside one phase has exactly matching prose and timing basis. The original preimplementation proposal rejected every cross-macro action; that proposal was explicitly corrected before the continuous-action tests were added. Initial test logs remain separate.
8. Two simultaneous writes to the same hand/location/channel are rejected. Independent actor actions may overlap.
9. A held paper cannot be moved as a free prop; a real transfer requires the actual holder, empty destination hand and modelled reach.
10. Current paper text is present at frame zero. Future writing appears only after the write action finishes and is absent from the earlier static frame.
11. A speech action uses compiled source text and actor; no second text copy is accepted. Its character range and phase placement are generated together.
12. A cut changes editorial continuity only; it cannot replace world state. An offscreen actor keeps its own physical scene.
13. Unknown action types, missing targets, booleans/nonfinite coordinates and duplicate identities/events are rejected explicitly.
14. The lowering result does not claim `PASS`, source entailment, completed independent audit or a quality percentage. Legacy typed inputs remain owned by their existing entry.

## Exact input shape

All objects below reject unknown keys. Registry keys are stable plain identities (Chinese or ASCII letters/digits/underscore/hyphen); there is no separately authored display name. Coordinates are `[east, north, height]` in metres, and yaw is clockwise from north in `[0,360)`. Booleans and nonfinite numbers are invalid. Source references are IDs from a real immutable `SourceCompilation`, never a caller-built output dictionary.

```text
payload = {contract: "production-decisions-v1", project, initial_world, shots}
initial_world = {scenes: {sceneID: scene}, actors: {actorID: actor},
                 props: {propID: prop}, points: {pointID: point}}
scene = {label, door_window_state, light, barriers}
light = {period, kind, name, state, position_xyz, main_tone, shadow_tone, materials}
barrier = {id, a: [x,y,0], b: [x,y,0], height_m}
point = {scene_id, position_xyz}
actor = {scene_id, position_xyz, body_yaw, head_yaw, eye_height,
         eye_state, posture, hands: {left: localXYZ, right: localXYZ},
         gaze: null | {kind: "actor"|"prop"|"point", id},
         reach_origin_xyz, reach_m}
prop = {anchor: {point: pointID} | {actor: actorID, hand: "left"|"right"},
        written_ref: null | compiledWrittenObjectID}
shot = {shot_id, shot_uid, episode_id, scene_id, continuity_id,
        ticks: [0, firstBoundary, secondBoundary, duration], camera,
        actions, editorial, source_unit_ids, transition}
camera = {position_xyz, target: pointID, hfov_deg, vfov_deg, frame}
transition = {relation, basis}
```

`position_xyz` on actors is the body/root location. `eye_height` is the upward distance from that root. Hand offsets are relative to the body: local east/right, local north/front, height. Body yaw rotates them. Gaze toward an actor aims at its eye landmark. The existing seven-field continuity registry retains the actor's base coordinate; derived `aim_part: eyes` and `aim_position_xyz` explicitly distinguish the real aim from that entity anchor.

An actor may additionally declare `performance_state: {eyelid, breath}`. Eyelid values are `unknown/relaxed/slightly_tense`; breath values are `unknown/resting/inhale/exhale`. Omission means both are unknown. It never supplies default performance actions. Derived state lives inside the existing `动作进度.表演状态`, leaving the outer seven-field contract unchanged. An explicit eyelid effect from an unknown state is described as that action, never as a fabricated known before-state.

`posture` is one of `站立/坐姿/蹲姿/躺卧`; actual eye state is `natural_open/closed`. This version has no posture-change action: it refuses an unsupported action instead of silently adding a stand/sit. Reach uses required **absolute world coordinates `reach_origin_xyz`**, never an eye-height proportion. For a root at `[-0.3,-0.45,0]` and a declared local vertical offset `[0,0,0.8]`, supply `[-0.3,-0.45,0.8]`. Move translates this origin with the actor; turn rotates its existing root-relative offset with the body. The resulting current point is also exported in the seven-field body state. `reach_m` is explicit in `(0,2]`. A handover needs two actual hand landmarks within 0.2 m. A free-prop move needs the acting hand already within 0.2 m of the prop; write needs a real held tool and the hand at the carrier. These are declared modelling limits, not measured anatomical claims.

`frame` is `wide/medium/hands/object`; horizontal and vertical FOV are explicit values from 1 to 150 degrees. Eye/front, hand and foot landmarks inside the frustum determine the visible roster. Head yaw and known opaque barriers determine whether the eye landmark can be described as visible. Close-up labels are rejected if their declared frame also includes head/feet; object-only rejects visible actor landmarks. More than three visible actors is rejected. Fixed-camera projection consumes the same coordinates through the existing typed-v2 staging binder. Exact vertical camera/target coincidence is unsupported because its planar binding direction is undefined. No hidden full-body geometry is inferred from a point remaining outside the frustum.

The scene light uses the existing `interior_lighting_blocks` enums: ordinary day paper-window or explicitly lit night oil-lamp/candle/lantern, plus actual position and material/tone inputs. Night never creates a lamp. World bearing uses the common scene origin; screen side uses this camera's right vector. Scene physical locations are distinct from continuity IDs; offscreen actors retain their own physical scenes.

`door_window_state` accepts a registry such as `{"东纸窗":"closed","房门":"open"}`. Its paper-window entry must match that same named light's state. The limited old form `东纸窗关闭。房门开启。` is normalized to the same opening states; arbitrary prose, duplicate openings and contradictory light/window states fail. State snapshots derive the normalized registry rather than retaining a second unchecked story. Footstep sound cues require an explicitly standing actor; sitting/lying motion is not certified as walking.

`editorial` has six fields. `功能类型`, `参考图锚定`, `原文依据类型`, `原文支撑句` remain descriptive/source metadata. `环境动态` must be `{"mode":"shared_world"}`. `环境音效` must explicitly be `{"mode":"none"}` or `{"mode":"action_bound","cues":[{"action_id":"slide","sound":"prop_contact","volume":"soft"}]}`. Use `production_environment.environment_editorial(metadata=..., sound_cues=...)` to construct this input without writing a second environmental narrative. The constructor refuses old environmental prose instead of silently discarding it.

After simulation, environment fields are derived from the same scene light/clear-air model and completed actions. `prop_contact` binds a move_prop/transfer, `writing` binds write, and `footsteps` binds move_actor; volume is soft/normal. Actor, object and exact clock come from the real action. Missing/wrong-kind/duplicate cues fail; no sound is produced for a failed action. Free fog, autonomous paper movement, explosions or other unsupported effects require a real scene/action extension. This compiler does not simulate weather, fluid, flame dynamics or acoustics and does not prove source entailment. Each shot retains `environment_binding` alongside its actual world hashes. Legacy typed inputs remain a separate compatibility path and do not acquire this guarantee.

`project` is the existing typed project metadata/evidence. `scene_state_ledger` and prewritten timing events are forbidden; `audio_workflow.timing.utterances` must be absent or empty and is generated. The caller's source ledger, pending audits and old repair history are preserved. No formal evidence is synthesized by this compiler.

## Action schemas

Common keys are `id, kind, start, end, after, source_use_id`, plus `actor` on physical actions. Audio actions (`speak/narrate`) **do not accept actor or text**: they consume the compiled use's actor/text. `start/end` are integer ticks, not seconds. `after` is an explicit list of earlier completed original action IDs. `source_use_id` is an actual compiled use ID, or null only for declared staging actions that do not claim a source event.

The schemas below are the expanded executable actions. A physical source event is authored in the separately frozen source graph's optional `action_plans` object: `{contract: "source-action-plans-v1", initial_world_sha256, events: [{source_use_id, steps: [{step_id, kind, parameters, after}]}]}`. Each raw shot schedules `{id, kind: "source_step", source_use_id, step_id, start, end, after}` only. The actor comes from the compiled source use, parameters come from its frozen step, and step dependencies are mapped to their actual action IDs. A raw reference cannot repeat kind/prop/recipient/end-state parameters. All planned steps must be scheduled and completed exactly once. Use `source_action_plans.construct_source_action_plans` and `source_step` for construction; `migrate_source_actions` returns an explicit migration proposal that must be reviewed and frozen with the source graph before execution. It never approves old actions itself.

| kind | Additional keys | Actual effect |
|---|---|---|
| hold | none | Keeps position/body/hands; conflicts with concurrent changes to those channels. |
| move_actor | to: pointID | Interpolates root to the fixed named point; same physical scene only. |
| move_prop | prop, hand, to: pointID | Acquires reachable free prop, moves it with the hand, places it at the destination at completion. |
| turn | body_yaw, head_yaw | Interpolates the two yaw values and rotates hand offsets; never invents gaze tracking. |
| look | target: reference or null, tracking: bool, head_mode: still/follow | Null explicitly clears gaze. Tracking explicitly binds each sampled moving target; ordinary look binds the endpoint at completion. |
| point | hand, target: reference, finger_to: pointID | Moves that actual hand toward a reachable pointing endpoint; rejects a direction inconsistent with the target. |
| blink | none | One world blink in its declared interval; internal eyelid state returns open once. Only its actually visible intersections enter picture/event projection. A hidden blink is retained in world derivation, never described as visible. |
| transfer | prop, to_actor, to_hand | Changes the actual held prop to the meeting recipient hand once. |
| write | paper_ref, hand, tool | Uses a real held writing tool and a compiled planned paper; actual text becomes current only after completion. `source_use_id` must equal that paper's use ID. |
| speak | voice | Source mode must be `spoken`; compiled actor/text become one on-scene utterance. `voice` is the exact existing `voice_block` argument object, excluding mode/dialogue_rows. |
| narrate | none | Source mode is `objective_narration`, compiled actor is null. Text enters psychological/objective audio and actual timing, never static paper or mouth movement. |
| eyelid | to: relaxed/slightly_tense | Explicit eyelid tension effect; actual value changes at completion. Same-value effects and concurrent competing tension actions reject. Closed/hidden eyes retain the world effect without visible eye choreography. |
| breath | phase: inhale/exhale | Explicit breath phase during the action, resting after completion. Inhale conflicts with same-actor speech; exhale may accompany speech. Competing breath phases conflict. Offscreen effects remain in the world trace. |
| focus_adjust | none | Reads the actor's resolved current gaze and slightly adjusts focus on that same target. Missing/closed-eye/stale targets reject; no second target or implicit tracking is added. |
| blink_restraint | reason, utterance_id, char_range | An explicit bounded no-blink interval. It conflicts with actual blink intervals, retains its binding in world/action trace, and expires at its original end. Reason is emphasis/decisive_tail/high_pressure/shock/recognition/vigilance. |

Continuous `hold/move_actor/move_prop/turn/look/point/eyelid/breath/focus_adjust/blink_restraint` can cross macro boundaries. One action produces `id-part1`, `id-part2`, ... with exact intersected timing and generated start/continue/completion basis. Effects are simulated once; `after` is rebound to the final generated fragment. Derived-ID collisions are rejected. This version restricts atomic blink/transfer/write to one macro phase. Dynamic camera and simultaneous differing on-scene voice specifications still require explicit extensions. These restrictions are compiler limitations, not storyboard defects or semantic scores.

Pure authored staging/performance actions use `source_use_id: null`; do not invent source facts for eyelids, breath or a focus adjustment. Audio-related `blink_restraint` also uses null unless a separate real physical source use exists: `utterance_id` must identify the same actor's actual on-scene speak action. Its nonempty `char_range` must match the actual voice emphasis or actual terminal words. The declared interval covers that range's predicted timing inside the actual utterance; the binding retains source hash, profile, text/range and `is_measured: false`. A reason based on plot (high_pressure/shock/recognition/vigilance) instead requires a real `source_use_id`, with utterance_id/char_range both null. Its reason's semantic status remains pending independent review; the compiler does not declare it entailed.

`look` at the same target preserves the real gaze through the whole action; it does not clear gaze midway or implicitly follow moved targets. Stable gaze and unchanged head direction can be projected as a named hold and fixed head, without a fictitious second endpoint. Different-target look text derives its known origin and actual target. `turn` describes head rotation only when actual head_yaw changes. Timed physical predicates keep their actor/clock together. Visible successors of a hidden blink retain their real spoken ancestor in after-text; objective narration is never represented as heard speech.

Audio may cross macro phases: the complete source quote appears once, and later picture phases refer to that same utterance. Timing keeps one original utterance ID with contiguous `char_range` segments. Partitioning uses the registered profile's predicted capacity; it is never labelled measured alignment. If a very short macro cut requires dividing one character, the compiler reports `not-supported audio alignment` rather than inventing fractional characters or shifting the declared time. Ordered narration and dialogue can share a shot; narration has its own exact psychological quote range, and dialogue has its own zero-based `dialogue_index`. They may not overlap on the primary audio bus. Multiple sequential on-scene utterances require the same actual actor/voice specification; narration does not consume that specification.

A blink always belongs to the world timeline, irrespective of camera direction. The world event and its end effect are retained in `derivation.world_events`. Only real visible intersections generate ordinary timing events and picture basis. A partial interval says the blink is partly visible rather than claiming that a full blink was seen. When hidden projection nodes are removed, their visible/audio ancestors remain transitively in `projected_after`. Original `after` timing is first validated against the full world event, so this projection cannot make a too-early later action legal. Entirely hidden source uses are not credited as visible source presentation merely because a world event exists.

Visible blink basis keeps one exact clock with its actor, such as `甲在3.2秒自然眨眼一次。`; splitting the clock into a separate comma clause would lose the existing performance consumer's binding. If an explicit direct dependency is another actor's on-scene utterance in this shot, the basis may say `于乙本句发言结束后`. This states the declared order, not an invented emotional reaction; objective narration never becomes something the actor heard. Ordinary event start/end and original dependency IDs remain unchanged.

For on-scene speech, the compiler intersects its declared time with the same `clear_face` landmark visibility used by camera binding. Only those sampled intervals receive the canonical `口型与对白一致` instruction, expressed with explicit beginning and ending clocks; fully hidden speech gets person-bound `口部不入画` instead. The exact intersections are exported as `derivation.shots[*].actions[*].mouth_sync_intervals`. This is the fixed landmark model's face-view declaration, not a separate mouth mesh/occlusion certificate. Narration never gains lip-sync instructions. Audio IDs remain in timing and derivation instead of being read as production prose.

The action schema expresses these explicit performance intentions but does not supply missing ones. A `look` does not automatically add an eyelid effect, breath, blink or restraint reason. Original inputs missing those decisions still receive consumer findings. A stable named gaze is a state projection, not an invented eye movement; same-target focus adjustment does not authorize silently substituting another target to satisfy a path checker.

For a future writing plan that is only narrated, use `narrate` with an objective-narration source use. Do not declare a current paper or execute `write`. A compiled write use that is never actually written is unconsumed and fails the global consumption check. A current paper's exact complete text is rendered from the source compilation; handwritten action/field copies are not accepted.

## Output and integration

`lower_production_decisions(payload, source_compilation=compiled)` returns fresh `typed_decisions` for the existing v2 renderer and `derivation`. Derivation contains actual input hash, per-shot initial/final world hashes, seven-field endpoint hashes, camera values, eye visibility ticks, original-to-fragment event IDs, actual `rendered_sentences`, separate world action descriptions, consumed source IDs and predicted audio alignments. Each shot's `visible_current_paper_refs` lists only compiled paper IDs on props actually visible in that shot's first view; world presence alone is not evidence of visible paper. `world_events` preserves full action intervals, world dependencies, actual completed-effect counts and their projected dependency/visibility traces. Hashes prove identity, not entailment. No result signs an independent audit or claims release eligibility.

Source consumption verifies fact/use identities and whether required uses actually appear. Physical source actions also bind the raw initial-world digest, exact ordered plan and each observed execution effect. The same source event may expand into move plus transfer across shots, but a step cannot be re-executed under another ID, and one event cannot hand over the same object twice. Writing keeps its real paper/carrier binding and one execution per write use. `derivation.source_action_execution` retains expected and observed effects; `source_entailment_proved` remains false. A frozen plan can still misunderstand prose, and staging choices can still affect dramatic meaning; independent review must assess those facts rather than treating successful execution as semantic proof.

Objects referenced by source-plan prop parameters/prop targets and compiled written carriers are source-protected throughout the complete schedule. A null-source staging action cannot move, transfer or rewrite them before or after a planned event. The actual staging-effect check also preserves their anchor, ownership and inscription. Carrying an object while walking can change its coordinates while preserving those relations; unrelated staging props and ordinary blink/performance remain supported. This closes a source step followed by an unbound undo action without treating every actor movement as a new plot event.

`inspect_production_decisions` first collects independent structure/camera faults for all shots. If these are clear it simulates the state chain. A failed action marks the dependent state suffix as not checked; later independent structural checks are not hidden. The report has `findings`, `checked`, `not_checked`, and `lowered` only when lowering actually completed. Full preflight/semantic review remains outside this compiler.

When an upstream source compilation failed, the caller passes `source_unavailable_reason="the actual failure reason"` to `inspect_production_decisions`. This explicit inspection-only branch still checks world shape/numbers, prop anchors, independent cameras and action shapes. It checks the shape of written-reference strings but defers their source existence/current-state/carrier binding. `source_references` and `state_effect_simulation_and_lowering` are reported as `not_checked` with the original reason; no lowered output is returned. Omitting this parameter retains normal strict source checks, including rejection of an unknown paper. A failed compilation is never represented as an empty successful source registry.

Production integration freezes original bytes before lowering, freezes the source graph separately, obtains `SourceCompilation`, and then calls this function. Store the lowered bytes/derivation as derived evidence without replacing the original attempt. Existing legacy typed-v1/v2 inputs remain accepted only by their old paths; their compatibility is not a claim that they gained this action model.
