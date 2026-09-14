# Frozen source facts and decided written objects

0.5.33 新生产来源图必须包含 [production_plan_contract.md](production_plan_contract.md) 的 `production_plan`，并遵守 [production_prevention_contract.md](production_prevention_contract.md)。计划核对真实来源、规则指纹、决定归属及知情事件；其独立审查问题绑定既有来源审计证据。允许读取旧图用于历史核验，但不能据此声称新生产计划已验证。

`source_decisions.py` is a pure structural compiler. It does not read files,
call a model, grant semantic approval, or change the existing 27 storyboard
fields and independent source/dialogue gates. All successful output has
`semantic_status: pending_independent_review`, a null accuracy, and no release
eligibility. Source authority and the completeness/truth of the frozen fact
graph still need independent review.

## Public API

```python
from source_decisions import (
    compile_source_decisions, validate_source_decisions,
    validate_source_consumption, graph_sha256, SourceDecisionError,
    construct_source_spoken_use,
)
compiled = compile_source_decisions(
    frozen_graph, decisions, source_texts={"DOC": actual_source_text},
    expected_graph_sha256=externally_frozen_graph_digest,
)
output = compiled.to_dict()
spoken = output["uses_by_id"]["U1"]  # lowerer takes actor/text from here once
# Output has source_constraints, event_ids and exact pending review obligations.
# During real lowering collect the IDs actually consumed, then check:
issues = validate_source_consumption(
    compiled, use_ids=actually_consumed_use_ids,
    written_object_ids=actually_consumed_paper_ids,
)
```

`validate_source_decisions` has the same arguments and returns a tuple of
`SourceIssue`, never a PASS signature. Compilation raises `SourceDecisionError`
with `.issues` and `.global_missing_fact_ids`. Missing required facts and
support dependencies remain global issues even without any shot ID.

Pass frozen graph **bytes** when the digest identifies an exact UTF-8 file;
for a dict, `graph_sha256` uses sorted compact JSON. These two identities are
intentionally distinct. The production entry retains raw graph/input bytes
and supplies the externally locked digest; an author-supplied matching hash is
not an independent approval. Duplicate keys in graph JSON bytes are rejected.

## Input shape

The graph has `contract: source-facts-v1`, `sources` (source ID to UTF-8 text
SHA256), and `facts`, plus optional `action_plans`. No other root keys are
accepted. The optional plan is a `source-action-plans-v1` mapping with an
`events` array; its detailed action contract is checked by the action compiler.
It is included in the existing graph hash, and is returned as `action_plans`
(null for an old graph). It is not an independent semantic approval.
Each fact has these explicit fields:

| Field | Contract |
|---|---|
| `id`, `event_id` | Stable fact and shared event identities; different paragraphs may support the same event. |
| `subject` | Source actor identity, or null if unknown. Unknown speakers cannot be assigned new spoken words. |
| `polarity` | `affirmative` or `negative`. |
| `conditions`, `depends_on` | Existing fact IDs. No missing/cyclic references; every use retains its complete referenced dependency closure. |
| `modality` | `asserted` or `possible`. |
| `occurrence` | Proposition/event content: `occurred`, `ongoing`, `future`, `conditional`. |
| `expression_occurrence` | Actual saying/inscription: `occurred`, `current`, `future`, `conditional`, `not_applicable`. This is separate from what the words describe. |
| `expression_status` | `asserted`, `negated`, `uncertain`, `not_applicable`; existence/certainty of the expression, distinct from its internal negative/possible proposition. |
| `channel` | `oral`, `written`, `action`, `narration`. |
| `content_availability` | `given`, `unstated`, `not_applicable`. |
| `content_policy` | Given: `verbatim` or `adaptable`; unstated: `event_only`; otherwise `not_applicable`. |
| `content_text` | Complete given words/content. Empty for unstated/not-applicable. Given oral content must locate one exact occurrence inside its actual frozen support. Extraction scope and meaning still require independent review. |
| `supports` | Nonempty `{source_id,start,end,text}` spans; Python Unicode offsets, start inclusive/end exclusive, exact locked source characters. Cross-paragraph supports are allowed. |
| `required` | Explicit boolean fixed in the frozen graph. A production decision cannot override it. |

Decisions have exactly `contract: source-decisions-v1`, `uses`, and
`written_objects`. A use is:

```json
{"id":"U1","fact_ids":["F1"],"mode":"spoken","wording":"adapted",
 "actor":"甲","text":"若下雨,明日再取。"}
```

`physical_action` and `objective_narration` require `wording: authored`.
`spoken` requires `verbatim` or `adapted`. `current_written` and `write`
require `verbatim`. Action/speech actors are explicit; narration/paper may use
null. Supplied fact qualifiers and full source spans are carried into the
compiled use unchanged, rather than copied into a second author claim.

`fact_ids[0]` identifies the event being presented. Other events may appear
only as retained causal/condition context, with their own source actors;
these do not turn into a second enacted actor or add their words to this
utterance/paper. For verbatim text, concatenate matching-channel given facts
of the primary event in the declared order. Whether that actual text truly
presents all claimed context remains an independent semantic obligation.

An affirmative asserted occurred/ongoing action may be physically enacted.
Negative, possible, future, or still-conditional acts cannot become an
unconditional occurred action. A conditional proposition may be **said now**;
only the actual oral expression must have occurred/current status. This v1
compiler does not infer that a conditional antecedent has become true.

One physical event supported by multiple paragraphs is merged into one use.
Two uses cannot separately enact the same event. One use can later be split
into shot phases by the production lowerer, without claiming two source events.
Repeated static/video references are not automatically additional events;
the lowerer remains responsible for tracking actual action occurrences.

## Constructible source-preserving speech

```python
use = construct_source_spoken_use(
    frozen_graph, source_texts=actual_source_texts,
    expected_graph_sha256=externally_frozen_graph_digest,
    use_id="U1", fact_ids=["SAY", "RAIN"],
    wording="verbatim", typography="source",
)
```

The constructor takes no replacement actor or text. It constructs one use from
the primary event's given oral units and validates source identity and spans.
Other required facts may await other uses in this partial constructor; complete
compilation still rejects globally missing facts. This is not a production
attempt, independent semantic approval, or a source-graph authoring function.

For every spoken use, given oral `content_text` must occur exactly inside one
actual frozen support location. Ambiguous repeated occurrences require a
narrower support. Ordered units cannot overlap or reverse their source order.
Each condition of this spoken proposition, including its nested conditions,
must have a precise support span contained in the preserved oral units. Merely listing its fact ID,
or pointing to a broad paragraph outside the spoken words, is insufficient.
If a condition was expressed elsewhere or needs a new paraphrase, this limited
constructor refuses to invent or repeat it: that case needs genuine semantic
planning, not an automatic concatenation of another speaker's words.
Conditions reached only through `depends_on` belong to background events and
remain in the source obligation; they are not forcibly repeated by the current
speaker. For example, a reply `走吧。` to someone else's conditional arrangement
does not have to repeat that arrangement's condition. The compiler still does
not certify that the background was fully presented elsewhere.

For `spoken/verbatim`, actual text must equal the ordered oral facts'
`content_text`. For `spoken/adapted`, the source facts must permit `adaptable`
and the same complete source wording must be preserved. The only currently
supported changes are leading/trailing ASCII whitespace and the width of
commas, question marks, or exclamation marks adjacent to Chinese characters,
outside quoted literal content. No word, negation, condition, time, internal
space, punctuation position, or sentence mood is removed or replaced. The
constructor can request `wording="adapted", typography="chinese_width"` for
this limited transformation. Protected wording always remains exact.

Thus `若下雨，明日可能不去。` can use an ASCII comma; it cannot become
`明日一定去。` merely by retaining the condition ID. Unverified synonyms also
return `spoken_realization_requires_review`; the module does not falsely claim
those synonyms are wrong, only that the current production path has no genuine
pre-production semantic approval mechanism. No author flag or matching hash
can provide that approval. This explicit support boundary replaces the old
behavior in which arbitrary adapted words compiled with only a pending label.

Successful spoken uses expose `spoken_realization` with the actual absolute
source locations, preserved condition spans, formatting operation and source /
actual text hashes. The existing review obligation binds this proof along with
the source graph, actual speaker and actual text. It remains pending: literal
preservation does not prove that the graph extracted the right speaker/event,
included every condition, or interpreted polarity and modality correctly. If
the graph itself omits a condition or falsely labels a substring as the complete
utterance, independent review of the full original source is still necessary.
The existing final dialogue-adaptation and independent audit gates remain.

A protected source cannot opt into adapted wording. An event whose words were
not supplied cannot become spoken content; unquoted objective narration may
retain the occurrence, but does not gain permission to invent its substance.
Given answers remain separate required facts. The exact quote prohibition on
unknown-event narration is narrow; unquoted semantic invention still requires
independent review and is never certified by structural success.

## Decided paper, not text recovered from a converter

A paper object has exactly:

```json
{"id":"P1","use_id":"PAPER1","carrier_id":"登记纸",
 "at":"frame0","state":"current"}
```

There is **no second text field**. Its full text comes from the referenced use,
which must equal the ordered written facts' complete given content. Each source
review obligation also binds the actual written objects using it, including
carrier ID, instant, state and full derived text. Changing only the carrier
invalidates that local obligation identity without changing source-use words.
One carrier
at one instant has one complete object. Lowering reads
`written_objects_by_id[P1].text` for both body and static projection expectations;
it never computes expected text from the converter's recognition output.
`compiled.written_objects[0].render_static()` renders the complete carrier text
without removing quotes or interpreting its internal future/negative clauses.

Current paper requires `current_written`, `expression_occurrence: current`,
`expression_status: asserted`, and `state: current, at: frame0`. A paper already bearing **“若条件满足，明日交付。”**
is legal even though the proposition is conditional/future. Oral speech or a
future, negated, or uncertain inscription cannot be relabelled as current paper.
A current inscription containing “不得交付” is distinct from a source saying
that those words were not written. Actual speech likewise requires asserted
expression status; a source saying that words were never spoken cannot supply
an occurred speech action.

A planned inscription uses `mode: write` and a paper object
`state: planned, at: after:<use_id>`. It carries exact intended written text,
but `render_static()` rejects it. The production lowerer may reference it in a
write action and update the carrier only when that actual action completes;
it must reject this object in initial-world paper state. A plan is not evidence
that the inscription already exists or will unconditionally occur.

## Integration responsibilities and limits

The lowerer consumes compiled source uses directly: speech supplies only a
use ID plus performance/voice/time decisions, and initial paper supplies only
an object reference. Collect actual consumed use/object IDs from that operation
and run `validate_source_consumption`; metadata-only coverage is insufficient.
The function checks collected IDs, not a caller's honesty. Preserve the exact
actual field bindings for the existing independent source/dialogue gates.

Structural reference completeness is not factual accuracy or a shot score.
The required fact inventory remains independent of the number of shots; a
missing global fact cannot vanish from the denominator. Source correctness
also does not prove timing, geometry, actor visibility, or deliverability.
No graph/decision flag can stand in for an independent review or publish gate.

### Existing independent audit consumer

The service revalidates a recorded production derivation with
`production_derivation.inspect_derivation(storyboard)` before accepting any
`source-decision:<digest>` evidence. The independent `semantic_audit` submission
must include every current obligation in its returned order in the existing
`源文对白` layer, together with all corresponding actual field references,
such as `field:EP90-001.起始状态`. One use may require fields from several shots.
Field references may also satisfy existing source checks; their presence does
not replace source-unit, protected-dialogue, adapted-dialogue, or source-limit
evidence. Existing machine findings remain in the report.

Only the enriched independent report records `source_decision_coverage`, bound
to the canonical, source graph, derivation and semantic submission hashes.
The canonical's `pending_independent_review` descriptions are not rewritten.
An invalid derivation, stale/missing obligation, missing actual field, or a
source-decision reference in another layer rejects the audit submission.
The existing audit work order, handoff, signed certificate and final publication
gates still apply; structural derivation metadata creates none of them.
Review of an unspecified speech event never certifies invented missing words.

Focused synthetic cases are in `tests/test_v0527_spoken_source_preservation.py`,
`tests/test_v0527_source_decisions.py` and
`tests/test_v0527_source_decision_independent_audit.py`. The latter exercises
the real derivation/consumer and recording entry, while isolating the machine
report; it does not issue a complete service audit certificate.
