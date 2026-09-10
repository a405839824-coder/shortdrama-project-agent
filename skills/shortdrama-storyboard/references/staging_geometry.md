# Explicit staging geometry

Use `skills/shortdrama-storyboard/scripts/staging_geometry.py` as a library (`analyze_scene`) or a local CLI before rendering posture, gaze and camera descriptions. This is the single implementation, included in the skill package and runtime script fingerprint. It derives facts from explicit decisions; it does not parse or repair storyboard prose.

Input contract `staging-geometry-v1` has `camera: [xEast, yNorth]`, `camera_target: [xEast, yNorth]`, and a nonempty `actors` array. Every actor supplies `actor_id`, `actor_position`, `body_yaw`, `head_yaw`, and `gaze_target`. Yaw is clockwise from north: 0 north, 90 east, 180 south, 270 west, in `[0,360)`. Use one common coordinate unit. Optional `clear_face: true` asks for a necessary-condition check. Optional `screen_left_of: [{left: actor_id, right: actor_id}]` checks declared image order. Unknown fields are rejected rather than ignored.

Camera forward is the normalized vector from camera to target. Screen right is `(forward_y, -forward_x)`. Camera depth is the actor-camera vector dotted with forward. The tool reports lateral world projection and normalized perspective screen x = lateral/depth, only for positive depth. Compare normalized screen x for image left/right; comparing world x or unnormalized lateral position can give the wrong order at different depths. No camera roll or mirrored image is assumed.

The head unit vector is `(sin(yaw), cos(yaw))`; its angle with the actor-to-camera vector measures planar front-facing geometry. A clear-face claim is contradicted if depth is not positive or the head front points away from the camera. At 90 degrees the tool leaves a profile unproven. It never certifies clear eyes, actual face visibility or an in-frame subject just because those checks found no contradiction. It also reports body/head/gaze world octants and signed head-body/gaze-head differences. Octant halfway ties select the clockwise octant. These numbers do not define comfortable human neck angles; any comfort criterion must be an explicitly justified author parameter outside this helper.

R3 EP32-004's northeast camera reverses the asserted accountant-hand side. R3 EP32-031's southwest camera puts the north rear row on the left. Frozen EP33-038's ending and EP33-040's start put an east-facing head against a northwest camera, approximately 135 degrees, while requesting a clear face. The focused tests preserve these three actual error patterns using explicitly labeled minimal coordinates; those camera distances were not measured from a generated image.

For each meaningful phase, compute these facts from that phase's shared coordinates and pose. Then render direction and screen-side statements from the returned facts, keeping actor IDs bound to each person's own visibility and actions. Do not merely paste a reference to a shared decision next to independently hand-written contradictory directions. A sampled phase does not prove the continuous path between samples. Static focus, light targets and eye state must still come from the initial phase, and event timing must still place each action exactly once.

CLI from the repository root:

```powershell
.\.venv\Scripts\python.exe skills/shortdrama-storyboard/scripts/staging_geometry.py decisions.json --output geometry-diagnostic.json
```

Exit 0 means only that the provided assertions have no detected 2D contradiction; exit 2 means invalid input, a contradiction, or an I/O failure. A diagnostic includes the exact raw input SHA256 and always `commit_eligible=false`. It creates an output only at a new path; it does not overwrite input or existing diagnostics. The caller must preserve raw author decisions and rejected production attempts through the existing recorded authoring route. This helper is not that attempt ledger and cannot improve a first-draft score by excluding rejected plans.

Finite numeric types are required; booleans, strings, NaN, infinity, duplicate IDs/JSON keys, undefined coincident directions and unknown assertion IDs are rejected. There is no inferred actor, target, head turn, missing state, source judgment or generated PASS. No canonical fields, quality thresholds or release gates change. Framing, occlusion, actual lens/filmback, depth of field, vertical geometry, source fidelity and real action/audio execution remain independent checks.

Normalization is performed after scaling the vector to avoid subnormal-length rounding. Nonzero subnormal projections and detected projection underflow to zero are rejected with a request to rescale the common coordinate unit; they are not accepted as reliable camera depths, angles or image order. Exact orthogonal zero projections remain valid. This is a floating-point diagnostic with bounded precision, not arbitrary-precision geometry.

Focused tests: `tests/test_v0527_staging_geometry.py`, discovered by the existing `test_*.py` test entry.
