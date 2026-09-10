# Current repair observations

Keep every historical repair row from the immutable source artifact. A failed
old round is an observation, not a permanent current failure. Never delete old
counts, turn unknown attribution into zero, or append a claimed clean row to
make the gate pass.

For a repair, the service compares the source and candidate under the current
checker, computes the real remaining/newly detected issue sets, then calls
`validate_storyboard.bind_current_repair_check(candidate)`. This runs the entire
candidate again and returns a copy whose latest history row has `current_check`.
The ordinary validator always performs its actual checks. It verifies the
observation against the full candidate, retained history prefix, implementation,
rules and fresh diagnostics; stale or missing bindings fail. Current content,
contract, workflow and warning findings remain present. History must still be
structurally valid. An uncertain side-effect attribution stays `null` with
`introduced_count_state=attribution_pending`, including its displayed rate.

These hashes detect stale or altered data; they are not signatures. Only the
service's existing signed source/artifact lineage establishes which history is
authoritative. Standalone repair observations prove the local machine run only.
They never issue an independent audit, close unknown source facts or authorize
publication. After implementation/rule changes, rerun and bind a new observation
through the repair workflow; preserve old artifact bytes and old findings.

Use `boundary_decisions.BoundaryDecision` for explicitly authored time omission,
prerequisite reordering, or one-event reframing. Its explanation retains the
actual basis and names the editorial relation. It does not change either endpoint
state or waive same-scene continuity; physical source fidelity remains reviewed.
