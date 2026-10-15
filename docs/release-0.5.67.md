# 0.5.67 — complete-script episode authority and large candidate transport

An imported complete pure-dialogue play previously failed source authority on
its title, cast roster, bracketed actions and unlabelled continuation lines.
Auditing one episode also compared its dialogue against the entire play.

The source parser now supports an explicitly declared pure-dialogue-play profile.
It derives roles and turns from the source alone, preserves literal punctuation
and occurrence identities, keeps cast/staging silent, and treats an ellipsis-only
turn as silence. Other unlabelled prose retains the existing closed parser.

An exact source-bound `script-episode-partition-v1` plan must be saved under an
episode-plan work order and pass its required checks. The service verifies the
whole partition covers every body paragraph once on whole-act boundaries before
selecting the candidate's episode IDs. It rejects altered hashes, unvalidated
plans, gaps, overlaps, partial acts and unknown/reordered episodes. This narrows
source scope; it does not approve the meaning of a storyboard.

Compiled candidates above 256 KiB use a signed private candidate reference for
subsequent preflight/commit. The reference binds project, work order, preflight
and canonical hash. Full production gates, exact scene preflight, independent
audit and delivery receipts remain mandatory. Small candidates keep their
existing representation. No historical candidate or artifact is rewritten.

The 0.5.67 startup transfer uses a new immutable directory, signed manifest and
SQLite dispatch gate. It reuses the established backup/arming implementation in
a dedicated operator process configured only for this release. A verified
restore keeps dispatch closed until explicit verified reopening.

Regression coverage adds source-format and source-scope counterexamples,
service plan validation, reference identity/tamper tests, and a real signed
backup/restore/reopen test. The prior parser version assertion changes from v6
to v7; its existing behavior counterexamples remain intact. Real R2 isolation
acceptance is recorded outside the repository. Software tests do not establish
independent storyboard correctness or actual media duration.
