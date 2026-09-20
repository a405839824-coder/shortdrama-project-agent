# Counterexample fixture alignment for 0.5.47

This migration updates two pinned test files against baseline commit
`bea255ab0095d175c577ff14b5f153339c53dcd3`. It repairs fixture conflicts with the
existing 0.5.47 contract; it does not introduce a new rule or release version.

- `test_episode_rules.py` still checks strict `<1` and `<2` second observations.
  It now verifies that these observations do not enforce the already retired
  minimum short-shot quotas.
- `test_v0535_production_contract.py` uses a complete synthetic episode of
  27 shots and 96 seconds, with every shot at most four seconds. Original
  source dialogue and identities remain; seven explicitly registered synthetic
  speech events provide the required complete-episode duration. The late-hook
  counterexample still fails on actual delivery after the opening window, even
  when its declared clock claims an earlier time. Initial written-content
  visibility remains checked against actual delivery.

`historical_rule_baseline.json` records both old and new hashes under
`counterexample_migrations`. Only the two current pins and that migration record
change. Original source hashes, all 149 historical sections, historical rule
definitions and thresholds remain intact. The exact-hash test remains unchanged
and rejects subsequent unrecorded counterexample edits.

The two-scene regression now checks the actual selected row count: the first
scene contains ten shots and the second contains seventeen. Both scenes retain
their complete derivation and every source-bound shot.

These fixtures verify software contracts; they do not establish real story
quality, independent production sign-off or deployment readiness.
