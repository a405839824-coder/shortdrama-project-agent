from __future__ import annotations

import copy
import time
import unittest
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

import test_v0511_authority_fencing as authority_fixture
from test_efficiency import valid_storyboard


class V0511ProductionCommitContinuationRecoveryTests(unittest.TestCase):
    """Hard-crash recovery for a production commit's final WAL tail."""

    def setUp(self) -> None:
        self.harness = authority_fixture.V0511AuthorityFencingTests(
            methodName="runTest"
        )
        self.harness.setUp()
        self.service = self.harness.service
        self.namespace = self.harness.namespace

    def tearDown(self) -> None:
        self.harness.tearDown()

    def _tree(self, project_id: str) -> dict[str, bytes]:
        return self.harness._project_tree_bytes(project_id)

    def _expire_claim(self, project_id: str, preflight_id: str) -> str:
        record = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="draft_preflights",
            record_id=preflight_id,
        )
        token = str(record["commit_claim"]["claim_token"])
        with self.service.store.mutate_project_record(
            self.namespace,
            project_id,
            kind="draft_preflights",
            record_id=preflight_id,
        ) as current:
            current["commit_claim"]["lease_expires_epoch_seconds"] = (
                time.time() - 1
            )
            current["commit_claim"]["lease_expires_at"] = (
                "2000-01-01T00:00:00Z"
            )
        return token

    def _continue(
        self, project_id: str, work_order_id: str, new_session: str
    ) -> None:
        plan = self.harness._plan_continuation(
            project_id, work_order_id, new_session
        )
        result = self.harness._execute_continuation(
            project_id,
            work_order_id,
            new_session,
            str(plan.data["plan_sha256"]),
        )
        self.assertEqual(result.status, "continued")

    @contextmanager
    def _hard_crash_patches(self) -> Iterator[None]:
        # A real process death runs neither compensation callback.  Make the
        # in-process fault injection preserve the signed claim/WAL similarly.
        with patch.object(
            self.service.store,
            "release_draft_preflight_commit",
            side_effect=OSError("simulated process death before compensation"),
        ):
            yield

    def _leave_crashed_commit(
        self,
        label: str,
        *,
        crash_point: str,
    ) -> dict[str, Any]:
        project_id = self.harness._project(label)
        old_session = f"openai:{label}-old"
        new_session = f"openai:{label}-new"
        work_order_id = self.harness._begin_production(
            project_id, old_session, f"{label}-root"
        )
        storyboard = valid_storyboard()
        preflight = self.harness._preflight_full(
            project_id=project_id,
            work_order_id=work_order_id,
            session_id=old_session,
            storyboard=storyboard,
        )
        preflight_id = str(preflight.trace_id)
        file_name = f"{label}.xlsx"

        def commit(session: str, candidate: dict[str, Any] | None = None) -> Any:
            return self.service.commit_storyboard(
                self.namespace,
                project_id=project_id,
                work_order_id=work_order_id,
                storyboard=copy.deepcopy(candidate or storyboard),
                file_name=file_name,
                source_artifact_id=None,
                request_session_id=session,
                draft_preflight_id=preflight_id,
            )

        with self._hard_crash_patches():
            if crash_point == "after_hub_before_telemetry":
                with patch.object(
                    self.service,
                    "_save_telemetry",
                    side_effect=KeyboardInterrupt(
                        "hard crash after Hub before telemetry"
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        commit(old_session)
            elif crash_point == "after_telemetry_before_completion":
                with patch.object(
                    self.service.store,
                    "complete_draft_preflight_commit",
                    side_effect=KeyboardInterrupt(
                        "hard crash after telemetry before DPF completion"
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        commit(old_session)
            elif crash_point == "after_descriptor_before_hub":
                with patch.object(
                    self.service.store,
                    "mutate_project",
                    side_effect=KeyboardInterrupt(
                        "hard crash after recovery_ready before Hub"
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        commit(old_session)
            else:
                self.fail(f"unknown crash point: {crash_point}")

        record = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="draft_preflights",
            record_id=preflight_id,
        )
        descriptor = record["commit_progress"]["recovery_descriptor"]
        return {
            "project_id": project_id,
            "work_order_id": work_order_id,
            "old_session": old_session,
            "new_session": new_session,
            "storyboard": storyboard,
            "preflight_id": preflight_id,
            "file_name": file_name,
            "descriptor": descriptor,
            "commit": commit,
        }

    def test_hub_published_crash_recovers_across_generation_and_replays(
        self,
    ) -> None:
        state = self._leave_crashed_commit(
            "hub-before-telemetry",
            crash_point="after_hub_before_telemetry",
        )
        project_id = state["project_id"]
        descriptor = state["descriptor"]
        hub = self.service.store.load_hub(self.namespace, project_id)
        work_order = next(
            row
            for row in hub["work_orders"]
            if row["work_order_id"] == state["work_order_id"]
        )
        self.assertEqual(work_order["status"], "validating")
        with self.assertRaisesRegex(ValueError, "unknown telemetry"):
            self.service.store.get_project_record(
                self.namespace,
                project_id,
                kind="telemetry",
                record_id=descriptor["telemetry"]["telemetry_id"],
            )
        handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=descriptor["handoff_id"],
        )
        self.assertFalse(
            self.service.store.handoff_is_published(
                self.namespace, project_id, handoff
            )
        )

        self._expire_claim(project_id, state["preflight_id"])
        self._continue(
            project_id, state["work_order_id"], state["new_session"]
        )
        recovered = state["commit"](state["new_session"])
        self.assertEqual(recovered.status, "production_preflight_pass")
        self.assertTrue(recovered.data["authority_continuation_recovery"])
        self.assertEqual(
            recovered.trace_id,
            descriptor["telemetry"]["telemetry_id"],
        )
        handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=descriptor["handoff_id"],
        )
        self.assertTrue(
            self.service.store.handoff_is_published(
                self.namespace, project_id, handoff
            )
        )
        replay = state["commit"](state["new_session"])
        self.assertTrue(replay.data["idempotent_replay"])
        self.assertTrue(replay.data["authority_continuation_recovery"])
        self.assertEqual(replay.artifact_id, recovered.artifact_id)

    def test_telemetry_crash_reuses_exact_trace_and_fences_old_authority(
        self,
    ) -> None:
        state = self._leave_crashed_commit(
            "telemetry-before-completion",
            crash_point="after_telemetry_before_completion",
        )
        project_id = state["project_id"]
        descriptor = state["descriptor"]
        telemetry_id = descriptor["telemetry"]["telemetry_id"]
        telemetry_path = (
            self.service.store.project_dir(self.namespace, project_id)
            / "telemetry"
            / f"{telemetry_id}.json"
        )
        telemetry_before = telemetry_path.read_bytes()
        old_token = self._expire_claim(project_id, state["preflight_id"])
        self._continue(
            project_id, state["work_order_id"], state["new_session"]
        )

        before_old_session = self._tree(project_id)
        with self.assertRaisesRegex(
            ValueError,
            "window_session_mismatch|authority_generation_revoked",
        ):
            state["commit"](state["old_session"])
        self.assertEqual(self._tree(project_id), before_old_session)
        with self.assertRaisesRegex(
            ValueError, "draft_preflight_commit_claim_revoked"
        ):
            self.service.store.complete_draft_preflight_commit(
                self.namespace,
                project_id,
                state["preflight_id"],
                claim_token=old_token,
                consumed_by_commit={"artifact_id": descriptor["artifact_id"]},
            )
        self.assertEqual(self._tree(project_id), before_old_session)

        recovered = state["commit"](state["new_session"])
        self.assertEqual(recovered.trace_id, telemetry_id)
        self.assertEqual(telemetry_path.read_bytes(), telemetry_before)
        traces = self.service.store.load_hub(
            self.namespace, project_id
        )["performance_traces"]
        self.assertEqual(
            len([row for row in traces if row.get("telemetry_id") == telemetry_id]),
            1,
        )

    def test_pre_hub_descriptor_is_unpublished_and_new_full_dpf_is_safe(
        self,
    ) -> None:
        state = self._leave_crashed_commit(
            "descriptor-before-hub",
            crash_point="after_descriptor_before_hub",
        )
        project_id = state["project_id"]
        old_descriptor = state["descriptor"]
        old_handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=old_descriptor["handoff_id"],
        )
        self.assertFalse(
            self.service.store.handoff_is_published(
                self.namespace, project_id, old_handoff
            )
        )
        self._expire_claim(project_id, state["preflight_id"])
        self._continue(
            project_id, state["work_order_id"], state["new_session"]
        )
        new_preflight = self.harness._preflight_full(
            project_id=project_id,
            work_order_id=state["work_order_id"],
            session_id=state["new_session"],
            storyboard=state["storyboard"],
        )
        committed = self.service.commit_storyboard(
            self.namespace,
            project_id=project_id,
            work_order_id=state["work_order_id"],
            storyboard=copy.deepcopy(state["storyboard"]),
            file_name=state["file_name"],
            source_artifact_id=None,
            request_session_id=state["new_session"],
            draft_preflight_id=str(new_preflight.trace_id),
        )
        self.assertNotEqual(committed.artifact_id, old_descriptor["artifact_id"])
        self.assertNotEqual(
            committed.data["handoff"]["handoff_id"],
            old_descriptor["handoff_id"],
        )
        old_handoff = self.service.store.get_project_record(
            self.namespace,
            project_id,
            kind="handoffs",
            record_id=old_descriptor["handoff_id"],
        )
        self.assertFalse(
            self.service.store.handoff_is_published(
                self.namespace, project_id, old_handoff
            )
        )

    def test_payload_and_publication_tamper_fail_before_project_write(
        self,
    ) -> None:
        for tamper in ("payload", "artifact", "handoff", "hub", "claim"):
            with self.subTest(tamper=tamper):
                state = self._leave_crashed_commit(
                    f"recovery-tamper-{tamper}",
                    crash_point="after_hub_before_telemetry",
                )
                project_id = state["project_id"]
                self._expire_claim(project_id, state["preflight_id"])
                self._continue(
                    project_id,
                    state["work_order_id"],
                    state["new_session"],
                )
                candidate = copy.deepcopy(state["storyboard"])
                if tamper == "payload":
                    candidate["project_title"] = str(
                        candidate.get("project_title") or ""
                    ) + "-changed"
                elif tamper == "artifact":
                    artifact, path = self.service.store.get_artifact(
                        self.namespace,
                        project_id,
                        state["descriptor"]["artifact_id"],
                    )
                    path.write_bytes(path.read_bytes() + b"tamper")
                    self.assertEqual(
                        artifact["fingerprint"],
                        state["descriptor"]["artifact_fingerprint"],
                    )
                elif tamper == "handoff":
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        project_id,
                        kind="handoffs",
                        record_id=state["descriptor"]["handoff_id"],
                    ) as handoff:
                        handoff["checked_shot_count"] = int(
                            handoff["checked_shot_count"]
                        ) + 1
                elif tamper == "hub":
                    hub = self.service.store.load_hub(
                        self.namespace, project_id
                    )
                    deliverable = next(
                        row
                        for row in hub["deliverables"]
                        if row["deliverable_id"]
                        == state["descriptor"]["artifact_id"]
                    )
                    deliverable["fingerprint"] = "0" * 64
                    self.service.store.save_hub(
                        self.namespace, project_id, hub
                    )
                elif tamper == "claim":
                    with self.service.store.mutate_project_record(
                        self.namespace,
                        project_id,
                        kind="draft_preflights",
                        record_id=state["preflight_id"],
                    ) as preflight:
                        preflight["commit_claim"]["authority_root_id"] = (
                            "AUTH-other-root"
                        )
                before = self._tree(project_id)
                with self.assertRaises(ValueError):
                    state["commit"](state["new_session"], candidate)
                self.assertEqual(self._tree(project_id), before)


if __name__ == "__main__":
    unittest.main()
