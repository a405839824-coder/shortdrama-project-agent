# 0.5.68 — gesture state and phase projection repairs

Counted head gestures, a surface stroke and a body shudder previously repeated
their completed sentence in every macro phase. They now retain one world event
and describe its beginning, continuation and completion separately.

An explicit `head_tilt` action binds a finite pitch transition (-30 to 30
degrees) to the existing head channel and action state. It preserves body/root
coordinates, affects the declared camera-facing normal, carries into the next
shot and rejects a zero change, invalid angle, short interval or concurrent head
owner. The pitch state is visible in the existing static/picture fields; no
template field or delivery gate is added or removed. This remains a landmark
model, not a claim about body meshes or measured motion.

A look that turns a visible back/side of a head before its eyes enter frame now
retains that head transition without inventing visible eye movement. Spatial
screen position uses the same visible landmark as the first-frame description;
ground-relative staging still uses ground coordinates.

The source, dialogue, timing, state, independent audit and signed delivery
requirements remain unchanged. Existing artifacts and failures are retained;
affected author decisions are recompiled and independently reviewed. An
immutable 0.5.68 startup transfer reuses the existing signed backup, freeze,
restore and explicit reopening implementation.

A failed audit's signed full-repair authorization can be verified after an
execution-software update. Source bytes, canonical, parser, schema, project
anchors and target contract must still match exactly. The old runtime, cache,
latest Ledger authority, report, traces, identities and single-use package are
verified as historical evidence; no old audit becomes a current PASS. A current
runtime certificate and full validation of the new candidate remain mandatory,
and the new signed handoff returns to the original independent auditor. Patch
repair, audit replay and cached PASS do not gain this compatibility path.
