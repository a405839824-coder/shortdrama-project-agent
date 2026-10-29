# Project master lighting

The default eleven-column template is unchanged. An administrator may enable
`master-lighting-v1` for one project after the user explicitly requests it.
This adds the four existing canonical lighting fields to E after environment
motion. K and GPT retain exactly the same source values. No quality threshold,
source rule, or audit requirement is changed.

Use `begin_project_task` with `rule_improvement`, `execute`, and
`explicit_execute`. The existing confirmation transport accepts:

```text
project-template-v1:{"contract":"project-template-v1","variant":"master-lighting-v1"}
```

The server records the authorizing work order in that project's hub. New
production decisions must explicitly contain
`project.canonical_extension.project_template_variant = "master-lighting-v1"`.
The compiled canonical retains this choice. Preflight and commit reject a
candidate whose choice differs from the authorized project setting. An employee
cannot create the rule-improvement work order.

The effective export-template fingerprint includes the base template and the
variant. Production certificates, artifact metadata and current formal delivery
receipts bind that fingerprint. Actual workbook cells and the hidden canonical
must still agree. Changing the active choice blocks a mismatched old receipt;
editing a downloaded workbook invalidates its byte binding. Historical files
and certificates are never rewritten. Source-graph production plans continue
to bind the unchanged base 27-field contract.

To restore the default, explicitly select `standard-v1` through the same admin
route. This affects subsequent eligible output and current receipt checks only.
The variant's existence is not a content audit or a measured quality claim.

Release preservation uses `scripts/upgrade_master_lighting.py` and the existing
tested freeze, signed-transfer, byte verification and explicit-reopen workflow
with new 0.5.69 release IDs. Do not reuse an older consumed transfer.
