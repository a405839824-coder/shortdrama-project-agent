from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from shortdrama_mcp.canonical import canonical_json_bytes
from shortdrama_mcp.storage import ProjectStore, sha256_bytes


class V0511AuthorityChainCommitTests(unittest.TestCase):
    namespace = "local-single-user"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix="shortdrama-v0511-authority-chain-"
        )
        self.store = ProjectStore(
            Path(self.temp.name), "https://example.invalid"
        )
        self.project_index = 0

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _digest(label: str) -> str:
        return hashlib.sha256(label.encode("utf-8")).hexdigest()

    def _fixture(self, label: str) -> dict[str, Any]:
        self.project_index += 1
        project_id = f"SBP-CHAIN-{self.project_index:04d}"
        work_order_id = f"WO-CHAIN-{self.project_index:04d}"
        work_order = {
            "work_order_id": work_order_id,
            "operation": "storyboard_create",
            "mode": "execute",
            "authorization": "explicit_execute",
            "server_actor_role": "production",
            "server_window_binding_id": f"WIN-ORIGINAL-{self.project_index:04d}",
            "server_actor_binding_id": f"ACT-ORIGINAL-{self.project_index:04d}",
            "transport_session_fingerprint": self._digest(
                f"{label}-original-session"
            ),
            "targets": ["第一集"],
            "source_lock": {
                "authoritative_source": "已锁定项目证据链",
                "latest_version": "current",
                "target_boundary": "第一集",
                "must_keep": ["历史证据"],
                "must_remove": [],
            },
            "protected_scope": [],
            "expected_outputs": ["正式分镜"],
            "status": "in_progress",
        }
        hub = {
            "project_id": project_id,
            "project_name": label,
            "work_orders": [copy.deepcopy(work_order)],
            "active_authority_bindings": [],
        }
        self.store.create_project(
            self.namespace,
            hub,
            {"project_id": project_id, "events": []},
        )
        authority_contract = {
            "work_order_id": work_order_id,
            "operation": work_order["operation"],
            "mode": work_order["mode"],
            "authorization": work_order["authorization"],
            "server_actor_role": work_order["server_actor_role"],
            "targets": copy.deepcopy(work_order["targets"]),
            "source_lock": copy.deepcopy(work_order["source_lock"]),
            "protected_scope": copy.deepcopy(work_order["protected_scope"]),
            "expected_outputs": copy.deepcopy(work_order["expected_outputs"]),
        }
        return {
            "project_id": project_id,
            "work_order": work_order,
            "work_order_id": work_order_id,
            "authority_root_id": self.store._authority_root_id(
                project_id, work_order_id
            ),
            "authority_contract_sha256": sha256_bytes(
                canonical_json_bytes(authority_contract)
            ),
            "work_order_snapshot_sha256": self.store.project_state_sha256(
                work_order
            ),
        }

    def _candidate(
        self,
        fixture: dict[str, Any],
        generation: int,
        previous_continuation_id: str | None,
    ) -> dict[str, Any]:
        suffix = f"{fixture['project_id']}-{generation}"
        continuation_id = f"CNT-CHAIN-{fixture['project_id'][-4:]}-{generation}"
        plan_sha256 = self._digest(f"{suffix}-plan")
        binding = {
            "continuation_id": continuation_id,
            "authority_epoch_id": f"EPOCH-CHAIN-{fixture['project_id'][-4:]}-{generation}",
            "authority_root_id": fixture["authority_root_id"],
            "work_order_id": fixture["work_order_id"],
            "generation": generation,
            "window_binding_id": f"WIN-CHAIN-{fixture['project_id'][-4:]}-{generation}",
            "actor_binding_id": f"ACT-CHAIN-{fixture['project_id'][-4:]}-{generation}",
            "session_fingerprint": self._digest(f"{suffix}-session"),
            "role": "production",
            "operation": "storyboard_create",
            "mode": "execute",
            "authorization": "explicit_execute",
            "authority_contract_sha256": fixture[
                "authority_contract_sha256"
            ],
            "plan_sha256": plan_sha256,
            "status": "active",
        }
        return {
            "continuation_id": continuation_id,
            "authority_epoch_id": binding["authority_epoch_id"],
            "authority_root_id": fixture["authority_root_id"],
            "work_order_id": fixture["work_order_id"],
            "role": "production",
            "operation": "storyboard_create",
            "mode": "execute",
            "authorization": "explicit_execute",
            "authority_contract_sha256": fixture[
                "authority_contract_sha256"
            ],
            "work_order_snapshot_sha256": fixture[
                "work_order_snapshot_sha256"
            ],
            "pre_state_sha256": self._digest(f"{suffix}-pre-state"),
            "previous_continuation_id": previous_continuation_id,
            "previous_generation": generation - 1,
            "generation": generation,
            "plan_sha256": plan_sha256,
            "runtime_test_certificate_id": f"CERT-CHAIN-{generation}",
            "runtime_test_certificate_key": self._digest(
                f"{suffix}-certificate"
            ),
            "request_text_sha256": self._digest(f"{suffix}-request"),
            "confirmation": (
                "ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION"
            ),
            "active_binding": binding,
            "status": "active",
            "created_at": f"2026-09-01T00:00:0{generation}Z",
        }

    def _commit(
        self, fixture: dict[str, Any], candidate: dict[str, Any]
    ) -> dict[str, Any]:
        return self.store.commit_authority_continuation(
            self.namespace,
            fixture["project_id"],
            candidate,
            fixture["work_order_snapshot_sha256"],
        )

    def _tree(self, fixture: dict[str, Any]) -> dict[str, bytes]:
        directory = self.store.project_dir(
            self.namespace, fixture["project_id"]
        )
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    def _record_path(
        self, fixture: dict[str, Any], continuation_id: str
    ) -> Path:
        return (
            self.store.project_dir(self.namespace, fixture["project_id"])
            / "authority_continuations"
            / f"{continuation_id}.json"
        )

    def _rewrite_signed(self, path: Path, record: dict[str, Any]) -> None:
        record["integrity_hmac"] = self.store._record_integrity_hmac(record)
        self.store.atomic_write_json(path, record)

    def test_candidate_must_descend_from_current_head_and_gen1_zero(self) -> None:
        fixture = self._fixture("candidate-ancestry")
        bad_gen1 = self._candidate(fixture, 1, "CNT-FAKE-PREVIOUS")
        before = self._tree(fixture)
        with self.assertRaisesRegex(
            ValueError, "authority_continuation_lineage_invalid"
        ):
            self._commit(fixture, bad_gen1)
        self.assertEqual(self._tree(fixture), before)

        gen1 = self._candidate(fixture, 1, None)
        committed1 = self._commit(fixture, gen1)
        bad_gen2 = self._candidate(fixture, 2, "CNT-FAKE-PREVIOUS")
        before = self._tree(fixture)
        with self.assertRaisesRegex(
            ValueError, "authority_continuation_lineage_invalid"
        ):
            self._commit(fixture, bad_gen2)
        self.assertEqual(self._tree(fixture), before)
        self.assertEqual(committed1["generation"], 1)

    def test_corrupt_json_and_partial_signature_fail_zero_write(self) -> None:
        for corruption in ("json", "unsigned"):
            with self.subTest(corruption=corruption):
                fixture = self._fixture(f"corrupt-{corruption}")
                gen1 = self._commit(
                    fixture, self._candidate(fixture, 1, None)
                )
                gen2 = self._candidate(
                    fixture, 2, str(gen1["continuation_id"])
                )
                path = self._record_path(
                    fixture, str(gen1["continuation_id"])
                )
                if corruption == "json":
                    path.write_text('{"broken":', encoding="utf-8")
                else:
                    record = json.loads(path.read_text(encoding="utf-8"))
                    record.pop("integrity_hmac", None)
                    self.store.atomic_write_json(path, record)
                before = self._tree(fixture)
                with self.assertRaises(
                    (ValueError, json.JSONDecodeError)
                ):
                    self._commit(fixture, gen2)
                self.assertEqual(self._tree(fixture), before)

    def test_resigned_fake_ancestry_snapshot_contract_and_binding_fail(self) -> None:
        mutations = (
            "ancestry",
            "snapshot",
            "contract",
            "active_binding",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                fixture = self._fixture(f"resigned-{mutation}")
                gen1 = self._commit(
                    fixture, self._candidate(fixture, 1, None)
                )
                gen2 = self._candidate(
                    fixture, 2, str(gen1["continuation_id"])
                )
                path = self._record_path(
                    fixture, str(gen1["continuation_id"])
                )
                record = json.loads(path.read_text(encoding="utf-8"))
                hub_path = self.store.project_dir(
                    self.namespace, fixture["project_id"]
                ) / "hub.json"
                hub = json.loads(hub_path.read_text(encoding="utf-8"))
                if mutation == "ancestry":
                    record["previous_continuation_id"] = "CNT-FAKE-ANCESTOR"
                elif mutation == "snapshot":
                    record["work_order_snapshot_sha256"] = "f" * 64
                elif mutation == "contract":
                    record["authority_contract_sha256"] = "e" * 64
                    record["active_binding"][
                        "authority_contract_sha256"
                    ] = "e" * 64
                    hub["active_authority_bindings"][0][
                        "authority_contract_sha256"
                    ] = "e" * 64
                else:
                    record["role"] = "independent_audit"
                    record["active_binding"]["role"] = "independent_audit"
                    hub["active_authority_bindings"][0][
                        "role"
                    ] = "independent_audit"
                self._rewrite_signed(path, record)
                if mutation in {"contract", "active_binding"}:
                    self.store.atomic_write_json(hub_path, hub)
                before = self._tree(fixture)
                with self.assertRaisesRegex(
                    ValueError,
                    "authority_continuation_(?:lineage|head|record)",
                ):
                    self._commit(fixture, gen2)
                self.assertEqual(self._tree(fixture), before)

    def test_generation_gap_and_duplicate_fail_zero_write(self) -> None:
        gap_fixture = self._fixture("generation-gap")
        gap_gen1 = self._commit(
            gap_fixture, self._candidate(gap_fixture, 1, None)
        )
        gap_gen2 = self._commit(
            gap_fixture,
            self._candidate(
                gap_fixture, 2, str(gap_gen1["continuation_id"])
            ),
        )
        gap_gen3 = self._candidate(
            gap_fixture, 3, str(gap_gen2["continuation_id"])
        )
        self._record_path(
            gap_fixture, str(gap_gen1["continuation_id"])
        ).unlink()
        before = self._tree(gap_fixture)
        with self.assertRaisesRegex(
            ValueError, "authority_continuation_lineage_invalid"
        ):
            self._commit(gap_fixture, gap_gen3)
        self.assertEqual(self._tree(gap_fixture), before)

        duplicate_fixture = self._fixture("generation-duplicate")
        duplicate_gen1 = self._commit(
            duplicate_fixture,
            self._candidate(duplicate_fixture, 1, None),
        )
        duplicate_gen2 = self._candidate(
            duplicate_fixture,
            2,
            str(duplicate_gen1["continuation_id"]),
        )
        original_path = self._record_path(
            duplicate_fixture, str(duplicate_gen1["continuation_id"])
        )
        duplicate = json.loads(original_path.read_text(encoding="utf-8"))
        duplicate_id = "CNT-CHAIN-DUPLICATE-GENERATION"
        duplicate["record_id"] = duplicate_id
        duplicate["continuation_id"] = duplicate_id
        duplicate["active_binding"]["continuation_id"] = duplicate_id
        duplicate["authority_epoch_id"] = "EPOCH-CHAIN-DUPLICATE"
        duplicate["active_binding"][
            "authority_epoch_id"
        ] = "EPOCH-CHAIN-DUPLICATE"
        duplicate["plan_sha256"] = self._digest("duplicate-plan")
        duplicate["active_binding"]["plan_sha256"] = duplicate[
            "plan_sha256"
        ]
        duplicate_path = self._record_path(duplicate_fixture, duplicate_id)
        self._rewrite_signed(duplicate_path, duplicate)
        before = self._tree(duplicate_fixture)
        with self.assertRaisesRegex(
            ValueError, "authority_continuation_lineage_invalid"
        ):
            self._commit(duplicate_fixture, duplicate_gen2)
        self.assertEqual(self._tree(duplicate_fixture), before)

    def test_later_generation_accepts_legitimate_work_order_lifecycle_change(
        self,
    ) -> None:
        fixture = self._fixture("work-order-lifecycle-change")
        gen1 = self._commit(
            fixture, self._candidate(fixture, 1, None)
        )
        hub = self.store.load_hub(
            self.namespace, fixture["project_id"]
        )
        work_order = hub["work_orders"][0]
        work_order["status"] = "executing"
        work_order["internal_round"] = 2
        work_order["state_history"] = [
            {"from": "scope_locked", "to": "executing"}
        ]
        self.store.save_hub(
            self.namespace, fixture["project_id"], hub
        )
        fixture["work_order_snapshot_sha256"] = (
            self.store.project_state_sha256(work_order)
        )
        gen2 = self._candidate(
            fixture, 2, str(gen1["continuation_id"])
        )
        committed = self._commit(fixture, gen2)
        self.assertEqual(committed["generation"], 2)
        self.assertEqual(
            committed["work_order_snapshot_sha256"],
            fixture["work_order_snapshot_sha256"],
        )

    def test_exact_signed_record_hub_half_write_replay_succeeds(self) -> None:
        fixture = self._fixture("signed-record-half-write")
        candidate = self._candidate(fixture, 1, None)
        directory = self.store.project_dir(
            self.namespace, fixture["project_id"]
        )
        hub_path = directory / "hub.json"
        original_atomic_write = self.store.atomic_write_json

        def fail_hub_publication(path: Path, value: Any) -> None:
            if path == hub_path and value.get("active_authority_bindings"):
                raise RuntimeError("simulated Hub publication failure")
            original_atomic_write(path, value)

        with patch.object(
            self.store,
            "atomic_write_json",
            side_effect=fail_hub_publication,
        ):
            with self.assertRaisesRegex(RuntimeError, "Hub publication"):
                self._commit(fixture, candidate)

        record_path = self._record_path(
            fixture, str(candidate["continuation_id"])
        )
        signed_record_before = record_path.read_bytes()
        hub_before = json.loads(hub_path.read_text(encoding="utf-8"))
        self.assertEqual(hub_before["active_authority_bindings"], [])

        resumed = self._commit(fixture, candidate)
        self.assertTrue(resumed["resumed_from_signed_record"])
        self.assertFalse(resumed["idempotent_replay"])
        self.assertEqual(record_path.read_bytes(), signed_record_before)
        head = self.store.load_hub(
            self.namespace, fixture["project_id"]
        )["active_authority_bindings"]
        self.assertEqual(head, [resumed["active_binding"]])

        before_replay = self._tree(fixture)
        replayed = self._commit(fixture, candidate)
        self.assertTrue(replayed["idempotent_replay"])
        self.assertEqual(self._tree(fixture), before_replay)


if __name__ == "__main__":
    unittest.main()
