# Generate through the existing MCP preflight tool

0.5.35 新生产必须先把 [production_plan_contract.md](production_plan_contract.md) 的计划随来源图冻结；依据 [production_prevention_contract.md](production_prevention_contract.md) 核对事实前提、决定归属、知情事件及整镜交付。以下 envelope 的来源图必须含该计划，服务器不自动补计划，缺失时先留存原始请求再拒绝生成。

0.5.32 字段内容约束以 [field_content_contract.md](field_content_contract.md) 为准；该文件由唯一规则注册表生成。正式母版、GPT 和 R5 阅读页必须由同一份字段数据生成。遇到错列返回路径与原片段，不能静默删句或补造依据。

`preflight_storyboard_draft` retains its existing tool name and parameters. Its
`storyboard` parameter accepts either the existing compact canonical or this
explicit compiler envelope. Do not mix the two representations.

```json
{
  "contract": "production-authoring-request-v1",
  "decisions": {
    "contract": "production-decisions-v1",
    "project": {},
    "initial_world": {},
    "shots": []
  },
  "source_graph_artifact_id": "ART-example",
  "source_graph_sha256": "replace_with_the_actual_64_character_lowercase_sha256"
}
```

This example shows the envelope shape only. The inner decisions must satisfy
`production_decisions.md` and `source_decisions.md`; empty project/world/shot
values are not a valid production input. The envelope has exactly four keys.

Register the frozen source graph using an existing authorized project route:

- Import a JSON file with `import_source_file(source_role="reference")` under
  a production intake/source-lock/chapter-intake work order; use its original
  artifact ID and original file SHA256.
- Alternatively, save the graph JSON with `commit_project_artifact` as a
  `storyboard_plan` under its permitted planning work order, then use that
  artifact's actual saved bytes fingerprint.

The generation work order then calls preflight with the envelope. The source
graph must be a published artifact of type `source:reference` or
`storyboard_plan` in the same project and namespace. There is no arbitrary path,
remote URL, inline graph, or caller-supplied approval escape hatch. Artifact and
SHA checks establish byte identity, not truth or an independent source review.

The service first applies its existing work-order/session/role fence and runtime
requirements. Each admitted compiler request receives a unique append-only
private attempt directory containing the submitted JSON representation, context,
available graph bytes, original decisions and compiler record. Concurrent calls
remain subject to the existing authority fence; requests cannot overwrite an
earlier attempt. MCP has already parsed the JSON when this function runs, so the
saved request is a faithful JSON serialization, not a claim to retain HTTP wire
formatting. Source graph file bytes are retained exactly.

On compilation rejection, the tool returns `status: authoring_rejected`,
`data.authoring_attempt` with findings, `compiled_storyboard: null`, and
`quality: null`. No canonical exists for a shot-level denominator. This is a
recorded generation attempt, not a zero-percent audited storyboard.

On compilation success, `data.compiled_storyboard` contains the actual finalized
canonical for the next call, and `data.authoring_attempt` identifies the recorded
input, graph and canonical. The same canonical proceeds through the existing
scene/episode/full preflight, source authority, history and quality gates. A
successful compilation may therefore return a failed preflight with real old
or new issues. Private attempt files are not published artifacts, XLSX files,
versions or handoffs.

Use the returned canonical unchanged for later preflight/commit. A successful
full preflight still requires its existing signed `draft_preflight_id` at commit;
source-decision obligations still require the existing independent audit. The
bridge does not fill missing evidence, certify semantic accuracy, or publish.
The local `author_storyboard.py --input ... --source-graph ...` entry remains
available. Tool registration alone does not prove the code is deployed remotely.
