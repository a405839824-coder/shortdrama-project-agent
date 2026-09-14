# Production prevention entry and evidence lifecycle

This is the production recording and evidence contract. A successful recording produces
a structurally rendered draft pending full checks and independent review. A recording
does not establish deployment, a manuscript quality benchmark, or publication authority.

0.5.33 新生产必须先冻结 [production_plan_contract.md](production_plan_contract.md)，完整规则由 [production_prevention_contract.md](production_prevention_contract.md) 给出。仅在实际重编译验证计划后，表演按已声明事件与真实状态检查；自然静止和无需回应的听者不能被旧文字模板强迫补动作。动作冲突、实际眼部状态及反应前置条件继续检查，表演自然度仍需独立核对。

## Invocation

For the existing MCP tool, use the registered-artifact envelope in
`production_authoring_request.md`. Its compiled canonical goes through the actual
service preparation and full validator; the local command below remains supported.

```powershell
python scripts/author_storyboard.py --input decisions.json --source-graph source-facts.json --source-graph-sha256 <frozen-raw-file-sha256> --attempts-root attempts --attempt-id first-001
```

Use the `production-decisions-v1` input shape in `production_decisions.md`. The source graph
is `source-facts-v1`, with `source-decisions-v1` under project prevention evidence. Supply
the actual frozen source text rows. If no separate expected graph digest is supplied,
the recorder pins the bytes read at invocation and reports that weaker identity basis;
neither digest authenticates an author's semantic interpretation.

Every invocation has a fresh immutable directory. Raw input and graph bytes precede
compilation. Successful lowerings preserve typed decisions, source compilation, physical
derivation, canonical JSON, and check coverage. Rejections preserve the actual available
artifacts and diagnostics and do not publish canonical JSON. A collision creates a
separate rejected record; it does not overwrite the previous attempt.

## Checks and source review

`check_coverage.json` separates completed passes, failed checks, and dependent checks
that could not run. Source declarations, independent voice configuration, all shot
shapes, and cameras can be checked before a failed earlier action is repaired. A failed
world transition blocks dependent later states, which remain unchecked rather than
passing. This is bounded comprehensive collection, not an assertion that all possible
semantic defects are mechanically detectable.

`production_derivation.py` binds the frozen action input, graph, compiled source uses,
actual generated fields, and current compiler/schema/rule bytes. Inspection recompiles
the input and compares owned content, rather than trusting a refreshed checksum alone.
All downstream shots are recompiled from the same initial world. Old source-decision
review IDs bind the exact generated content, so a changed endpoint, camera, source use,
or timeline invalidates them. Direct edits to generated output require recompilation.
The existing service signs the complete current artifact and independent submission.

An independent source auditor uses the existing `semantic_audit` interface and
`source-decision:<digest>` IDs supplied by `inspect_derivation`, plus each actual
`field:EPxx-nnn.字段` reference. Narration requires 心理音轨 and its action context; current
paper requires 起始状态. Existing source-unit, protected/adapted dialogue and all other
layers remain mandatory. Source closure is recorded in the enriched audit report and
existing certificate. The canonical generation metadata remains `not_performed`; it
must not be edited to PASS, which would change the signed artifact and claim authority
the compiler does not have.

## Model and test boundaries

The model still interprets source meaning and chooses dramatic actions, blocking,
performance and voice. A compiler can consistently implement an incorrect decision.
Therefore inspect source entailment and performance independently, preserve legal
counterexamples, and use new unseen complete manuscripts for accuracy measurement.
Count a manuscript's first recorded input once, include rejected attempts, and report
the denominator and unchecked categories. Do not substitute unit-test pass rate for
manuscript accuracy or combine changing audit scopes into one improving percentage.

The physical model's supported effects and limitations are listed in
`production_decisions.md`. Camera/landmark predictions are not rendered-video
measurements. Predicted audio character alignment is not observed lip-sync. An
unsupported legitimate case is a capability gap, not a source error or a passed test.
