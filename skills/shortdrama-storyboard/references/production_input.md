# Unified complete author input

This is an input inspection boundary for the existing production-decisions-v1
contract. It does not add action types, a second field/enum registry, an automatic
story writer or an independent semantic reviewer.

Use construct_production_input(project=..., initial_world=..., shots=...) to
deep-copy explicitly authored decisions. It does not inspect, fill, retry or
persist anything. Its result is complete author input, not an append-only attempt
record. Freeze the first complete result through author_storyboard.record_attempt
before revising it using diagnostics. A pure local function cannot prevent a
full-access caller from bypassing that discipline; the controlled MCP production
request route always records before it inspects.

inspect_production_input(raw, graph_bytes=..., expected_graph_sha256=...) returns
a serializable report with checks, issues, not_checked and ready_for_canonical.
It never returns a canonical. The private recorder hook may retain actual source
compilation and lowering intermediates; these are not published output or a
semantic certificate.

The inspection reuses these current authorities:

- storyboard_schema.json supplies project and prevention-evidence required
  fields, nonempty constraints, enums and references. Only compiler-owned root
  constants, shots, total duration, scene-state snapshots and audio events/
  utterances are excluded from pre-lowering required fields. Second authored
  copies remain forbidden. No cognition, source fact or formal evidence is filled.
- source_decisions validates actual source bytes, fact references, complete
  condition/dependency closure, channel, wording mode and global required facts.
  Given oral words bind exact source spans and preserve the current spoken
  proposition's conditions; ordinary unverified synonym rewrites are not a
  supported production shortcut. Source interpretation remains independently
  reviewable, even when all literal words are preserved.
- inspect_production_decisions checks the real world, every independent camera
  and action shape, then actual effect prerequisites and source consumption.
  Physical source steps expand from the same frozen graph's action plan;
  environment fields and sound cues derive from the shared light/opening state
  and completed actions rather than free prose.
  If graph compilation fails, paper references and dependent simulation are
  not_checked; the missing graph does not turn a paper into a known-invalid
  prop. Independent camera/shape checks still run.
- inspect_typed_decisions checks the actual lowered typed input's existing
  state, phase, voice, punctuation and timing contracts.
- eye_contract_faults and listener_reaction_faults inspect the actual privately
  lowered shot fields and visible windows. The existing consumers decide which
  eye/performance requirements apply; this module has no new blink frequency,
  duration or emotion defaults. Real hidden eyes can be not_applicable.
  Unknown states are preserved; missing required decisions remain blocking.

The report distinguishes structural completeness from independent semantics.
ready_for_canonical means these input checks have no blocking findings and
lowering completed. It does not mean full preflight, all formal evidence, source
truth, photorealistic geometry, audio measurement or release approval succeeded.
Existing full validators and independent service sign-off remain required.
Source-semantic unknowns do not get invented content to complete the structure.

record_attempt invokes this boundary only for production-decisions-v1, after
input.original.json and available graph bytes are frozen. Blocked inputs retain
all independent findings and skipped dependencies without canonical.json. The
legacy typed-v1/v2 recording paths retain their original contract.

The synthetic regression's positive input combines the current-paper source
fixture with explicit performance decisions. The hold-only input remains a
missing-performance negative control. A successful test is a software result,
not proof of an independent content audit or real first-generation usability.
