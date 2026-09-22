"""Reconstruction authorization/lineage tests, not content-accuracy acceptance.

The gate receives the upstream production replay result; these tests exercise
real workbook reading, artifact bytes and signed records rather than claiming
that this small historical fixture is a valid new full episode.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from shortdrama_mcp.file_io import CANONICAL_SIDECAR_SHEET
from shortdrama_mcp.service import StoryboardService, canonical_storyboard_sha256
from shortdrama_mcp.storage import ProjectStore
from shortdrama_mcp.workbook_reconstruction import (
    source_backed_reconstruction, claim_reconstruction, release_reconstruction, CONTRACT,
)
from test_efficiency import valid_storyboard
from test_workbook_canonical_migration import legacy_bytes


class WorkbookReconstructionTests(unittest.TestCase):
    namespace = "local-single-user"
    project = "SBP-reconstruction"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workbook-reconstruction-")
        self.addCleanup(self.temp.cleanup)
        self.store = ProjectStore(Path(self.temp.name), "http://127.0.0.1:8765")
        self.hub = {"project_id":self.project, "work_orders":[],
                    "project_state":{"authoritative_sources":[], "latest_artifacts":{}}}
        self.store.create_project(self.namespace, self.hub, {"events":[{"original_failure":"preserved"}]})
        self.service = SimpleNamespace(store=self.store,
            _selected_dialogue_source_entry=StoryboardService._selected_dialogue_source_entry)
        self.payload = legacy_bytes()
        self.old = self.imported(self.payload, "storyboard", "old.xlsx")
        self.authority = self.imported("甲说道：“保留原台词。”".encode(), "script", "source.txt")
        self.order = {"work_order_id":"WO-rebuild", "operation":"storyboard_regenerate", "mode":"execute",
            "authorization":"explicit_execute", "server_actor_role":"production",
            "request_text":"Explicitly rebuild this source workbook from the locked script",
            "targets":[self.old["artifact_id"], "source_sha256:"+self.old["fingerprint"], "EP01"],
            "source_lock":{"authoritative_source":self.authority["artifact_id"]}}
        self.hub["work_orders"].append(self.order)
        self.candidate = valid_storyboard()
        self.preflight = self.sign_preflight()

    def imported(self, payload, role, name):
        intake_id = "WO-intake-"+role
        record = self.store.save_artifact_bytes(self.namespace, self.project, payload,
            file_name=name, artifact_type="source:"+role, version="test", work_order_id=intake_id,
            metadata={"source_role":role})
        entry = {key:record[key] for key in ("artifact_id", "work_order_id", "fingerprint")}
        entry["source_role"] = role
        self.hub["project_state"]["authoritative_sources"].append(entry)
        self.hub["project_state"]["latest_artifacts"]["source:"+role] = copy.deepcopy(entry)
        self.hub["work_orders"].append({"work_order_id":intake_id, "operation":"project_intake",
            "mode":"execute", "authorization":"explicit_execute", "server_actor_role":"production"})
        return record

    def sign_preflight(self, **overrides):
        return self.store.save_project_record(self.namespace, self.project, kind="draft_preflights", record_id="DPF-test",
            value={"draft_preflight_id":"DPF-test", "work_order_id":"WO-rebuild", "scope_kind":"full",
                   "commit_ready":True, "commit_claim":{"claim_token":"DPC-unit", "state":"claimed"},
                   "candidate_sha256":canonical_storyboard_sha256(self.candidate), **overrides})

    def run_gate(self, **overrides):
        args = {"hub":self.hub, "work_order":self.order, "source_artifact_id":self.old["artifact_id"],
                "candidate":self.candidate, "production_route":"replayed_current_production",
                "full_preflight":self.preflight, **overrides}
        return source_backed_reconstruction(self.service, self.namespace, self.project, **args)

    def tree(self):
        return {str(path.relative_to(self.store.root)):path.read_bytes()
                for path in self.store.root.rglob("*") if path.is_file()}

    def other_proof(self):
        order = {**self.order, "work_order_id":"WO-other"}
        preflight = self.store.save_project_record(self.namespace, self.project,
            kind="draft_preflights", record_id="DPF-other", value={
                "draft_preflight_id":"DPF-other", "work_order_id":"WO-other", "scope_kind":"full",
                "commit_claim":{"claim_token":"DPC-unit", "state":"claimed"},
                "commit_ready":True, "candidate_sha256":canonical_storyboard_sha256(self.candidate)})
        return self.run_gate(work_order=order, full_preflight=preflight)

    def test_same_source_two_prevalidated_orders_cannot_claim_in_parallel(self):
        first, second = self.run_gate(), self.other_proof()
        ready = threading.Barrier(2)
        def claim(proof):
            ready.wait(timeout=10)
            try:
                # Separate instances use the actual cross-process file lock,
                # not a shared in-memory guard.
                store = ProjectStore(self.store.root, "http://127.0.0.1:8765")
                return claim_reconstruction(store, self.namespace, self.project, proof, claim_token="DPC-unit")
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, (first, second)))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1)
        self.assertIn("workbook_reconstruction_source_claim_conflict", results)

    def test_persisted_reservation_resumes_exact_owner_after_restart(self):
        first, second = self.run_gate(), self.other_proof()
        identity = claim_reconstruction(self.store, self.namespace, self.project, first, claim_token="DPC-unit")
        restarted = ProjectStore(self.store.root, "http://127.0.0.1:8765")
        self.assertEqual(claim_reconstruction(restarted, self.namespace, self.project, first, claim_token="DPC-unit"), identity)
        with self.assertRaisesRegex(ValueError, "source_claim_conflict"):
            claim_reconstruction(restarted, self.namespace, self.project, second, claim_token="DPC-unit")
        saved = restarted.get_project_record(self.namespace, self.project, kind="draft_preflights", record_id="DPF-test")
        self.assertTrue(restarted.project_record_is_signed(saved))
        self.assertEqual(saved["workbook_reconstruction_claim"]["state"], "reserved")

    def test_handled_failure_without_children_releases_source_for_another_order(self):
        first, second = self.run_gate(), self.other_proof()
        identity = claim_reconstruction(self.store, self.namespace, self.project, first, claim_token="DPC-unit")
        release_reconstruction(self.store, self.namespace, self.project, identity)
        claimed = claim_reconstruction(self.store, self.namespace, self.project, second, claim_token="DPC-unit")
        self.assertEqual(claimed["work_order_id"], "WO-other")
        self.assertEqual(self.store.load_ledger(self.namespace, self.project)["events"], [{"original_failure":"preserved"}])

    def test_failure_after_child_write_keeps_claim_and_requires_exact_recovery(self):
        first, second = self.run_gate(), self.other_proof()
        identity = claim_reconstruction(self.store, self.namespace, self.project, first, claim_token="DPC-unit")
        child = self.store.save_artifact_json(self.namespace, self.project, self.candidate,
            file_name="pending.json", artifact_type="storyboard_json", version="test", work_order_id="WO-rebuild",
            source_artifact_id=self.old["artifact_id"],
            metadata={"draft_preflight_id":"DPF-test", "audit_status":"PENDING_AUDIT"})
        with self.store.mutate_project_record(self.namespace, self.project, kind="draft_preflights", record_id="DPF-test") as record:
            record["commit_progress"] = {"canonical_json_artifact_id":child["artifact_id"]}
        release_reconstruction(self.store, self.namespace, self.project, identity)
        self.assertEqual(claim_reconstruction(self.store, self.namespace, self.project, first, claim_token="DPC-unit"), identity)
        with self.assertRaisesRegex(ValueError, "generated_descendant_requires_repair"):
            claim_reconstruction(self.store, self.namespace, self.project, second, claim_token="DPC-unit")
        self.store.update_artifact_metadata(self.namespace, self.project, child["artifact_id"], {"audit_status":"FAIL"})
        release_reconstruction(self.store, self.namespace, self.project, identity)
        for proof in (first, second):
            with self.subTest(order=proof["work_order_id"]), self.assertRaisesRegex(ValueError, "generated_descendant_requires_repair"):
                claim_reconstruction(self.store, self.namespace, self.project, proof, claim_token="DPC-unit")

    def test_cas_rechecks_child_published_after_pure_authorization(self):
        proof = self.run_gate()
        self.store.save_artifact_json(self.namespace, self.project, self.candidate,
            file_name="concurrent.json", artifact_type="storyboard_json", version="test",
            source_artifact_id=self.old["artifact_id"], metadata={"audit_status":"FAIL"})
        with self.assertRaisesRegex(ValueError, "generated_descendant_requires_repair"):
            claim_reconstruction(self.store, self.namespace, self.project, proof, claim_token="DPC-unit")

    def test_late_same_owner_cleanup_cannot_release_new_token_for_third_order(self):
        first, third = self.run_gate(), self.other_proof()
        old = claim_reconstruction(self.store, self.namespace, self.project, first, claim_token="DPC-unit")
        with self.store.mutate_project_record(self.namespace, self.project, kind="draft_preflights", record_id="DPF-test") as record:
            record["commit_claim"]["claim_token"] = "DPC-retry"
        # The new native draft claim already owns the retry, even before its
        # source reservation is rebound. An old owner's cleanup must not clear it.
        release_reconstruction(self.store, self.namespace, self.project, old)
        with self.assertRaisesRegex(ValueError, "source_claim_conflict"):
            claim_reconstruction(self.store, self.namespace, self.project, third, claim_token="DPC-unit")
        newer = claim_reconstruction(self.store, self.namespace, self.project, first, claim_token="DPC-retry")
        release_reconstruction(self.store, self.namespace, self.project, old)
        with self.assertRaisesRegex(ValueError, "source_claim_conflict"):
            claim_reconstruction(self.store, self.namespace, self.project, third, claim_token="DPC-unit")
        release_reconstruction(self.store, self.namespace, self.project, newer)
        self.assertEqual(claim_reconstruction(self.store, self.namespace, self.project, third,
                         claim_token="DPC-unit")["work_order_id"], "WO-other")

    def test_exact_reconstruction_preserves_old_bytes_history_and_separate_quality(self):
        before = self.tree()
        proof = self.run_gate()
        self.assertEqual(before, self.tree())
        self.assertEqual(proof["contract"], CONTRACT)
        self.assertEqual(proof["source_raw_sha256"], self.old["fingerprint"])
        self.assertEqual(proof["dialogue_authority_sha256"], self.authority["fingerprint"])
        self.assertNotEqual(proof["source_raw_sha256"], proof["dialogue_authority_sha256"])
        self.assertEqual(proof["source_proof"]["episode_ids"], ["EP01"])
        self.assertFalse(proof["source_proof"]["sidecar_present"])
        self.assertEqual(proof["candidate_classification"], "new_generation_from_authoritative_source")
        self.assertIsNone(proof["legacy_first_draft_correctness_percent"])
        self.assertEqual(proof["independent_audit_status"], "PENDING_AUDIT")

    def test_repair_and_ordinary_generated_sources_do_not_enter_revision_route(self):
        self.assertIsNone(self.run_gate(work_order={**self.order, "operation":"storyboard_repair"}))
        self.assertIsNone(self.run_gate(source_artifact_id=None))
        generated = self.store.save_artifact_json(self.namespace, self.project, self.candidate,
            file_name="generated.json", artifact_type="storyboard_json", version="test")
        self.assertIsNone(self.run_gate(source_artifact_id=generated["artifact_id"]))

    def test_raw_candidate_or_migration_compatibility_cannot_claim_new_production(self):
        for route in ("existing_artifact_audit_or_repair", "signed_historical_workbook_migration", None):
            with self.subTest(route=route), self.assertRaisesRegex(ValueError, "current_production_required"):
                self.run_gate(production_route=route)
        for key, value in (("authorization", "plan_only"), ("mode", "plan_only"),
                           ("server_actor_role", "repair"), ("workbook_migration_id", "WMG-old")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "production_authority_required"):
                self.run_gate(work_order={**self.order, key:value})

    def test_full_preflight_must_be_signed_exact_and_commit_ready(self):
        for record in (None, {key:value for key,value in self.preflight.items() if key != "integrity_hmac"},
                       {**self.preflight, "candidate_sha256":"0"*64}):
            with self.subTest(record=record), self.assertRaisesRegex(ValueError, "signed_full_preflight_required"):
                self.run_gate(full_preflight=record)
        for overrides in ({"scope_kind":"scene"}, {"commit_ready":False}, {"work_order_id":"WO-other"},
                          {"candidate_sha256":"0"*64}):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "signed_full_preflight_required"):
                self.run_gate(full_preflight=self.sign_preflight(**overrides))

    def test_exact_old_sha_all_episodes_and_no_extra_or_duplicate_targets(self):
        targets = self.order["targets"]
        variants = [targets[:-1], targets+targets[-1:], targets+["EP02"], targets+["all"],
                    [targets[0], "source_sha256:"+"0"*64, "EP01"], [targets[0], targets[1], "EP01-02"]]
        for variant in variants:
            with self.subTest(targets=variant), self.assertRaisesRegex(ValueError, "exact_source_hash_and_episode_targets_required"):
                self.run_gate(work_order={**self.order, "targets":variant})

    def test_candidate_cannot_drop_or_relabel_old_episode_scope(self):
        for field, value in (("镜头编号", "EP02-001"), ("episode_id", "EP02")):
            candidate = copy.deepcopy(self.candidate)
            candidate["shots"][0][field] = value
            preflight = self.sign_preflight(candidate_sha256=canonical_storyboard_sha256(candidate))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "candidate_episode_scope_mismatch"):
                self.run_gate(candidate=candidate, full_preflight=preflight)

    def test_missing_or_wrong_authoritative_source_is_not_inferred_from_old_workbook(self):
        for authority_id in (None, self.old["artifact_id"], "a narrative description"):
            with self.subTest(authority_id=authority_id), self.assertRaisesRegex(ValueError, "locked_novel_or_script_required"):
                self.run_gate(work_order={**self.order, "source_lock":{"authoritative_source":authority_id}})
        self.hub["project_state"]["latest_artifacts"].pop("source:script")
        self.hub["project_state"]["authoritative_sources"] = [self.hub["project_state"]["authoritative_sources"][0]]
        with self.assertRaisesRegex(ValueError, "locked_novel_or_script_required"):
            self.run_gate()

    def test_authority_entry_or_intake_mismatch_rejected(self):
        self.hub["project_state"]["latest_artifacts"]["source:script"]["fingerprint"] = "0"*64
        with self.assertRaisesRegex(ValueError, "source_authority_mismatch"):
            self.run_gate()
        self.hub["project_state"]["latest_artifacts"]["source:script"]["fingerprint"] = self.authority["fingerprint"]
        self.hub["work_orders"][0]["authorization"] = "plan_only"
        with self.assertRaisesRegex(ValueError, "source_authority_mismatch"):
            self.run_gate()

    def test_true_sidecar_is_read_from_bytes_even_without_metadata(self):
        payload = legacy_bytes(edit=lambda book, sheet: book.create_sheet(CANONICAL_SIDECAR_SHEET))
        source = self.imported(payload, "storyboard", "has-sidecar.xlsx")
        with self.assertRaisesRegex(ValueError, "already containing a sidecar"):
            self.run_gate(source_artifact_id=source["artifact_id"])

    def test_metadata_cannot_disguise_prior_formal_binding(self):
        for field in ("canonical_json_artifact_id", "production_certificate_id", "handoff_id",
                      "canonical_sha256", "recovered_without_canonical_sidecar"):
            self.store.update_artifact_metadata(self.namespace, self.project, self.old["artifact_id"], {field:"prior"})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "source_already_bound"):
                self.run_gate()
            self.store.update_artifact_metadata(self.namespace, self.project, self.old["artifact_id"], {field:None})

    def test_failed_generated_descendant_cannot_restart_from_old_import(self):
        self.store.save_artifact_json(self.namespace, self.project, self.candidate,
            file_name="failed.json", artifact_type="storyboard_json", version="test",
            source_artifact_id=self.old["artifact_id"], metadata={"audit_status":"FAIL"})
        before = self.tree()
        with self.assertRaisesRegex(ValueError, "generated_descendant_requires_repair"):
            self.run_gate()
        self.assertEqual(before, self.tree())

    def test_same_signed_commit_can_resume_its_own_pending_artifact_only(self):
        record = self.store.save_artifact_json(self.namespace, self.project, self.candidate,
            file_name="pending.json", artifact_type="storyboard_json", version="test", work_order_id="WO-rebuild",
            source_artifact_id=self.old["artifact_id"],
            metadata={"draft_preflight_id":"DPF-test", "audit_status":"PENDING_AUDIT"})
        preflight = self.sign_preflight(commit_progress={"canonical_json_artifact_id":record["artifact_id"]})
        self.assertEqual(self.run_gate(full_preflight=preflight)["contract"], CONTRACT)
        for overrides in ({"work_order_id":"WO-other"}, {"commit_progress":{"canonical_json_artifact_id":"ART-other"}}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.run_gate(full_preflight=self.sign_preflight(**overrides))
        self.store.update_artifact_metadata(self.namespace, self.project, record["artifact_id"], {"audit_status":"FAIL"})
        with self.assertRaisesRegex(ValueError, "generated_descendant_requires_repair"):
            self.run_gate(full_preflight=preflight)

    def test_actual_handoff_persistence_signs_reconstruction_proof(self):
        proof = self.run_gate()
        schema = json.loads((Path(__file__).resolve().parents[1]/"skills/shortdrama-storyboard/references/project_agent_schema.json").read_text(encoding="utf-8"))
        fingerprint_names = schema["$defs"]["handoff_manifest"]["properties"]["fingerprints"]["required"]
        # Isolate persistence from unrelated runtime/actor lookups. The real
        # handoff builder, schema validation, serialization and HMAC all run.
        self.service._active_authority_binding = lambda *args, **kwargs: {
            "window_binding_id":"WIN-unit", "actor_binding_id":"ACT-unit", "session_fingerprint":"a"*64,
            "authority_root_id":"AUTH-unit", "authority_epoch_id":"EPOCH-unit", "generation":1}
        self.service._fingerprint_bundle = lambda *args, **kwargs: {key:"a"*64 for key in fingerprint_names}
        self.service._project_schema = schema
        self.service._validate_schema_record = StoryboardService._validate_schema_record
        certificate = {key:"a"*64 for key in ("canonical_sha256", "source_fingerprint", "boundary_hash", "cache_key")}
        certificate.update({"certificate_id":"CERT-unit", "ruleset_version":"unit", "validator_version":"unit",
            "cache_components":{}, "preflight_status":"production_preflight_pass", "preflight_issue_count":0, "checked_shot_count":1})
        handoff = StoryboardService._persist_production_handoff(self.service, self.namespace, self.project,
            work_order_id="WO-rebuild", storyboard_artifact_id="ART-rebuilt", canonical_artifact_id="ART-canonical",
            certificate=certificate, storyboard=self.candidate, artifact_record={"metadata":{"workbook_reconstruction":proof}},
            work_order=self.order, stage_trace_id="TRC-unit")
        saved = self.store.get_project_record(self.namespace, self.project, kind="handoffs", record_id=handoff["handoff_id"])
        self.assertEqual(saved["objective_evidence"]["source_manifest"]["workbook_reconstruction"], proof)
        self.assertTrue(self.store.project_record_is_signed(saved))
        saved["objective_evidence"]["source_manifest"]["workbook_reconstruction"]["source_raw_sha256"] = "0"*64
        self.assertFalse(self.store.project_record_is_signed(saved))

    def test_source_handoff_cannot_be_escaped_by_removing_metadata(self):
        self.store.save_project_record(self.namespace, self.project, kind="handoffs", record_id="HND-old",
            value={"handoff_id":"HND-old", "storyboard_artifact_id":self.old["artifact_id"], "status":"failed"})
        with self.assertRaisesRegex(ValueError, "source_already_bound"):
            self.run_gate()

    def test_payload_tampering_is_rejected_on_actual_read(self):
        _, path = self.store.get_artifact(self.namespace, self.project, self.old["artifact_id"])
        path.write_bytes(path.read_bytes()+b"changed")
        with self.assertRaisesRegex(ValueError, "artifact_raw_fingerprint_mismatch"):
            self.run_gate()


class PublicWorkbookReconstructionTests(unittest.TestCase):
    def test_public_source_import_plan_preflight_and_revision_commit(self):
        from test_v0535_production_contract import CurrentMcpContractTests, complete_episode, generate
        from shortdrama_mcp.models import OpenAIFile
        import shortdrama_mcp.service as runtime
        flow = CurrentMcpContractTests(); flow.setUp()
        self.addCleanup(flow.tearDown)
        f = flow.f
        data, graph = complete_episode()
        # Use a source-proved name; the standalone fixture's single-letter
        # actor label is intentionally not an authoritative source role.
        data = json.loads(json.dumps(data, ensure_ascii=False).replace("甲", "江氏"))
        graph = json.loads(json.dumps(graph, ensure_ascii=False).replace("甲", "江氏"))
        source_rows = data["project"]["prevention_evidence"]["source_ledger"]
        source_texts = {row["source_id"]:row["source_text"] for row in source_rows}
        graph["sources"] = {key:hashlib.sha256(text.encode("utf-8")).hexdigest() for key,text in source_texts.items()}
        for fact in graph["facts"]:
            for support in fact["supports"]:
                text = source_texts[support["source_id"]]
                self.assertEqual(text.count(support["text"]), 1)
                support["start"] = text.index(support["text"])
                support["end"] = support["start"] + len(support["text"])
        dialogue = [{"speaker":row["speaker"], "text":row["source_text"].split("说道：", 1)[1]}
                    for row in source_rows if row["source_type"] == "对白"]
        source_text = "\n".join(f'{row["speaker"]}说道：“{row["text"]}”' for row in dialogue)
        with tempfile.TemporaryDirectory(prefix="reconstruction-current-") as folder:
            legacy = generate(folder, copy.deepcopy(data), copy.deepcopy(graph))
        legacy["shots"][0]["机位"] = "甲忽然知道所有真相。"  # Source content needs reconstruction, not lossless recertification.
        payload = legacy_bytes(legacy)

        def import_source(raw, name, role):
            order = f.begin("project_intake")
            with patch.object(runtime, "download_openai_file", return_value=(raw, name, "application/octet-stream")):
                return f.service.import_source_file(f.namespace, project_id=f.project, work_order_id=order,
                    file=OpenAIFile(file_id="synthetic-"+role, download_url="https://example.invalid/synthetic"),
                    source_role=role, request_session_id=None).artifact_id

        old_id = import_source(payload, "synthetic-old.xlsx", "storyboard")
        authority_id = import_source(source_text.encode("utf-8"), "synthetic-authority.txt", "script")
        data["project"]["protected_dialogue"] = dialogue
        data["project"]["source_lock"].update(source_artifact_id=authority_id,
            source_fingerprint=hashlib.sha256(source_text.encode("utf-8")).hexdigest(), source_role="script")
        request = flow.request(data, graph)
        begin_args = {"project_id":f.project, "operation":"storyboard_regenerate",
            "mode":"execute", "authorization":"explicit_execute", "request_text":"Explicit synthetic reconstruction",
            "targets":[old_id, "source_sha256:"+hashlib.sha256(payload).hexdigest(), "EP90"],
            "authoritative_source":authority_id, "latest_version":"v1", "target_boundary":"EP90",
            "must_keep":["authoritative dialogue", "old workbook bytes"], "must_remove":[], "protected_scope":[],
            "expected_outputs":["new source-backed storyboard pending independent audit"], "window_key":"rebuild"}
        f.order = f.mcp("begin_project_task", begin_args)["work_order_id"]
        twin_order = f.mcp("begin_project_task", begin_args)["work_order_id"]
        args = {"project_id":f.project, "work_order_id":f.order, "storyboard":request,
                "scope_kind":"scene", "scope_id":"ROOM-C1"}
        scene = f.mcp("preflight_storyboard_draft", args)
        self.assertEqual(scene["data"]["report"]["issues"], [])
        full = f.mcp("preflight_storyboard_draft", {**args, "scope_kind":"full", "scope_id":"EP90"})
        self.assertTrue(full["data"]["commit_ready"], full["data"].get("report", {}).get("issues"))
        twin_args = {**args, "work_order_id":twin_order}
        f.mcp("preflight_storyboard_draft", twin_args)
        twin_full = f.mcp("preflight_storyboard_draft", {**twin_args, "scope_kind":"full", "scope_id":"EP90"})
        self.assertTrue(twin_full["data"]["commit_ready"])
        commit_args = {"project_id":f.project, "work_order_id":f.order,
            "storyboard":full["data"]["compiled_storyboard"], "source_artifact_id":old_id,
            "draft_preflight_id":full["trace_id"], "file_name":"synthetic-rebuilt.xlsx"}
        twin_commit_args = {**commit_args, "work_order_id":twin_order,
            "storyboard":twin_full["data"]["compiled_storyboard"], "draft_preflight_id":twin_full["trace_id"]}

        with patch.object(f.service.store, "save_artifact_json", side_effect=RuntimeError("before child write")):
            with self.assertRaisesRegex(RuntimeError, "before child write"):
                f.service.commit_storyboard(f.namespace, **commit_args)
        failed_preparation = f.service.store.get_project_record(f.namespace, f.project,
            kind="draft_preflights", record_id=full["trace_id"])
        self.assertEqual(failed_preparation["workbook_reconstruction_claim"]["state"], "released")

        # Pause the first public commit after reserving the source, before its
        # first artifact write. The second pre-created work order already has
        # genuine signed scene/full preflights; only the new source CAS stops it.
        import shortdrama_mcp.workbook_reconstruction as reconstruction
        reserved, resume = threading.Event(), threading.Event()
        def paused_claim(store, namespace, project_id, proof, *, claim_token):
            result = claim_reconstruction(store, namespace, project_id, proof, claim_token=claim_token)
            if proof["work_order_id"] == f.order:
                reserved.set()
                if not resume.wait(timeout=30):
                    raise TimeoutError("test did not resume the source owner")
            return result
        save_json = f.service.store.save_artifact_json
        def crash_after_child(*args, **kwargs):
            result = save_json(*args, **kwargs)
            if kwargs.get("artifact_type") == "storyboard_json":
                raise RuntimeError("after child write")
            return result
        with patch.object(reconstruction, "claim_reconstruction", side_effect=paused_claim), \
                patch.object(f.service.store, "save_artifact_json", side_effect=crash_after_child):
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(f.service.commit_storyboard, f.namespace, **commit_args)
                try:
                    self.assertTrue(reserved.wait(timeout=30))
                    # Exercise the inner native commit claim after the outer
                    # role fence, so this regression does not rely on that
                    # separate guard hiding a same-preflight losing retry.
                    commit_after_role_fence = runtime.StoryboardService.commit_storyboard.__wrapped__.__wrapped__
                    with self.assertRaisesRegex(ValueError, "draft_preflight_commit_in_progress"):
                        commit_after_role_fence(f.service, f.namespace, **commit_args)
                    still_owned = f.service.store.get_project_record(f.namespace, f.project,
                        kind="draft_preflights", record_id=full["trace_id"])
                    self.assertEqual(still_owned["workbook_reconstruction_claim"]["state"], "reserved")
                    self.assertEqual(still_owned["workbook_reconstruction_claim"]["claim_token"],
                                     still_owned["commit_claim"]["claim_token"])
                    with self.assertRaisesRegex(ValueError, "workbook_reconstruction_source_claim_conflict"):
                        f.service.commit_storyboard(f.namespace, **twin_commit_args)
                    self.assertFalse(reconstruction._descendants(f.service.store, f.namespace, f.project, old_id))
                finally:
                    resume.set()
                with self.assertRaisesRegex(RuntimeError, "after child write"):
                    first.result(timeout=30)
        interrupted = f.service.store.get_project_record(f.namespace, f.project,
            kind="draft_preflights", record_id=full["trace_id"])
        self.assertEqual(interrupted["workbook_reconstruction_claim"]["state"], "reserved")
        self.assertEqual(len(reconstruction._descendants(f.service.store, f.namespace, f.project, old_id)), 1)
        # Exact retry resumes the real public partial commit and keeps the
        # original child identity, rather than creating a fresh generation.
        committed = f.mcp("commit_storyboard", commit_args)
        replayed = f.mcp("commit_storyboard", commit_args)
        self.assertEqual(committed["artifact_id"], replayed["artifact_id"])
        with self.assertRaisesRegex(ValueError, "workbook_reconstruction_generated_descendant_requires_repair"):
            f.service.commit_storyboard(f.namespace, **twin_commit_args)
        old_record, old_path = f.service.store.get_artifact(f.namespace, f.project, old_id)
        self.assertEqual(old_path.read_bytes(), payload)
        self.assertFalse(old_record["metadata"].get("canonical_json_artifact_id"))
        record = committed["data"]["artifact"]
        self.assertEqual(record["source_artifact_id"], old_id)
        proof = record["metadata"]["workbook_reconstruction"]
        self.assertEqual(proof["dialogue_authority_artifact_id"], authority_id)
        self.assertIsNone(proof["legacy_first_draft_correctness_percent"])
        handoff = f.service.store.get_project_record(f.namespace, f.project, kind="handoffs",
            record_id=record["metadata"]["handoff_id"])
        self.assertEqual(handoff["objective_evidence"]["source_manifest"]["workbook_reconstruction"], proof)
        self.assertTrue(f.service.store.project_record_is_signed(handoff))
        self.assertEqual(handoff["independent_audit_status"], "PENDING_AUDIT")


if __name__ == "__main__":
    unittest.main()
