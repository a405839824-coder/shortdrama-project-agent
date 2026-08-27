from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from shortdrama_mcp.storage import ProjectStore


class SuccessorTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="shortdrama-successor-tx-")
        self.store = ProjectStore(Path(self.temp.name), "http://127.0.0.1:8765")
        self.namespace = "successor-transaction-tests"
        self.project_id = "PRJ-successor-transactions"
        self.store.create_project(
            self.namespace,
            {"project_id": self.project_id, "project_name": "successor transactions"},
            {"project_id": self.project_id},
        )
        self.base_hash = "a" * 64
        self.base = self.store.save_artifact_bytes(
            self.namespace,
            self.project_id,
            b"base-storyboard",
            file_name="base.xlsx",
            artifact_type="storyboard",
            version="v1",
            metadata={"canonical_sha256": self.base_hash},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def metadata(self) -> dict:
        record, _path = self.store.get_artifact(
            self.namespace, self.project_id, self.base["artifact_id"]
        )
        return record["metadata"]

    def successor(self, canonical_hash: str = "b" * 64) -> dict:
        return self.store.save_artifact_bytes(
            self.namespace,
            self.project_id,
            b"successor-storyboard-" + canonical_hash.encode("ascii")[:8],
            file_name="successor.xlsx",
            artifact_type="storyboard",
            version="v2",
            source_artifact_id=self.base["artifact_id"],
            metadata={"canonical_sha256": canonical_hash},
        )

    def prepare(
        self,
        transaction_id: str,
        *,
        successor_kind: str = "delta",
        lease_seconds: int = 30,
        scope_hash: str = "scope-ep02",
    ) -> dict:
        return self.store.prepare_storyboard_successor(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            expected_canonical_hash=self.base_hash,
            expected_raw_fingerprint=self.base["fingerprint"],
            transaction_id=transaction_id,
            successor_kind=successor_kind,
            scope_hash=scope_hash,
            lease_seconds=lease_seconds,
        )

    def commit(
        self, transaction_id: str, successor: dict, canonical_hash: str
    ) -> dict:
        return self.store.commit_storyboard_successor(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            transaction_id=transaction_id,
            successor_artifact_id=successor["artifact_id"],
            successor_canonical_hash=canonical_hash,
        )

    def test_prepare_is_pending_and_same_transaction_retry_is_idempotent(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=1_000.0):
            first = self.prepare("TX-idempotent", lease_seconds=30)
            second = self.prepare("TX-idempotent", lease_seconds=30)

        self.assertEqual(first, second)
        metadata = self.metadata()
        self.assertEqual(metadata["pending_successor"]["state"], "prepared")
        self.assertEqual(
            metadata["pending_successor"]["transaction_id"], "TX-idempotent"
        )
        self.assertIsNone(metadata.get("successor_artifact_id"))
        self.assertIsNone(metadata.get("delta_successor_artifact_id"))
        self.assertEqual(
            metadata["active_successor_claim"]["claim_id"], "TX-idempotent"
        )

    def test_unexpired_transaction_excludes_a_different_writer(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=2_000.0):
            self.prepare("TX-owner", lease_seconds=30)
        with patch("shortdrama_mcp.storage.time.time", return_value=2_010.0):
            with self.assertRaisesRegex(ValueError, "active successor lease"):
                self.prepare("TX-contender", lease_seconds=30)

        self.assertEqual(
            self.metadata()["pending_successor"]["transaction_id"], "TX-owner"
        )

    def test_same_transaction_id_cannot_change_immutable_arguments(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=2_500.0):
            self.prepare("TX-immutable", lease_seconds=30, scope_hash="scope-a")
            with self.assertRaisesRegex(ValueError, "reused with different parameters"):
                self.prepare(
                    "TX-immutable",
                    lease_seconds=30,
                    scope_hash="scope-b",
                )

        self.assertEqual(
            self.metadata()["pending_successor"]["scope_hash"], "scope-a"
        )

    def test_stale_transaction_is_recovered_by_a_new_writer(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=3_000.0):
            self.prepare("TX-crashed", lease_seconds=10)
        with patch("shortdrama_mcp.storage.time.time", return_value=3_011.0):
            self.prepare("TX-recovery", lease_seconds=20)

        metadata = self.metadata()
        self.assertEqual(metadata["pending_successor"]["transaction_id"], "TX-recovery")
        recovered = [
            row
            for row in metadata["successor_transaction_history"]
            if row.get("transaction_id") == "TX-crashed"
        ]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["state"], "recovered")
        self.assertEqual(
            recovered[0]["recovered_by_transaction_id"], "TX-recovery"
        )
        self.assertIsNone(metadata.get("successor_artifact_id"))

    def test_recovered_stale_writer_is_fenced_from_commit(self) -> None:
        old_successor = self.successor("4" * 64)
        new_successor = self.successor("5" * 64)
        with patch("shortdrama_mcp.storage.time.time", return_value=3_500.0):
            self.prepare("TX-stale-writer", lease_seconds=10)
        with patch("shortdrama_mcp.storage.time.time", return_value=3_511.0):
            self.prepare("TX-new-owner", lease_seconds=30)
            with self.assertRaisesRegex(ValueError, "no longer current"):
                self.commit("TX-stale-writer", old_successor, "4" * 64)
            committed = self.commit("TX-new-owner", new_successor, "5" * 64)

        self.assertEqual(
            committed["metadata"]["successor_artifact_id"],
            new_successor["artifact_id"],
        )

    def test_expired_same_transaction_can_renew_then_commit(self) -> None:
        canonical_hash = "c" * 64
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=4_000.0):
            self.prepare("TX-renew", lease_seconds=10)
        with patch("shortdrama_mcp.storage.time.time", return_value=4_011.0):
            self.prepare("TX-renew", lease_seconds=20)
        with patch("shortdrama_mcp.storage.time.time", return_value=4_012.0):
            committed = self.commit("TX-renew", successor, canonical_hash)

        metadata = committed["metadata"]
        self.assertEqual(metadata["successor_transaction"]["state"], "committed")
        self.assertEqual(metadata["successor_artifact_id"], successor["artifact_id"])
        self.assertTrue(
            any(
                row.get("transaction_id") == "TX-renew"
                and row.get("state") == "lease_expired"
                for row in metadata["successor_transaction_history"]
            )
        )

    def test_commit_is_the_only_transition_that_publishes_successor(self) -> None:
        canonical_hash = "d" * 64
        with patch("shortdrama_mcp.storage.time.time", return_value=5_000.0):
            self.prepare("TX-commit", lease_seconds=30)
            successor = self.successor(canonical_hash)

            before = self.metadata()
            self.assertIsNone(before.get("successor_artifact_id"))
            self.assertEqual(before["pending_successor"]["state"], "prepared")

            first = self.commit("TX-commit", successor, canonical_hash)
            second = self.commit("TX-commit", successor, canonical_hash)
            retried_prepare = self.prepare("TX-commit", lease_seconds=30)

        self.assertEqual(first, second)
        self.assertEqual(first, retried_prepare)
        metadata = first["metadata"]
        self.assertIsNone(metadata["pending_successor"])
        self.assertIsNone(metadata["active_successor_claim"])
        self.assertIsNone(metadata["active_delta_claim"])
        self.assertEqual(metadata["successor_artifact_id"], successor["artifact_id"])
        self.assertEqual(
            metadata["delta_successor_artifact_id"], successor["artifact_id"]
        )

    def test_repair_kind_prepare_commit_and_retries_keep_aliases_consistent(self) -> None:
        canonical_hash = "a1" * 32
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=5_500.0):
            prepared = self.prepare(
                "TX-repair",
                successor_kind="repair",
                lease_seconds=30,
                scope_hash="repair-scope",
            )
            retried_prepare = self.prepare(
                "TX-repair",
                successor_kind="repair",
                lease_seconds=30,
                scope_hash="repair-scope",
            )
            committed = self.commit("TX-repair", successor, canonical_hash)
            retried_commit = self.commit("TX-repair", successor, canonical_hash)
            committed_prepare_retry = self.prepare(
                "TX-repair",
                successor_kind="repair",
                lease_seconds=30,
                scope_hash="repair-scope",
            )

        self.assertEqual(prepared, retried_prepare)
        self.assertEqual(committed, retried_commit)
        self.assertEqual(committed, committed_prepare_retry)
        metadata = committed["metadata"]
        self.assertEqual(metadata["successor_kind"], "repair")
        self.assertEqual(
            metadata["successor_transaction"]["successor_kind"], "repair"
        )
        self.assertEqual(metadata["successor_artifact_id"], successor["artifact_id"])
        self.assertEqual(
            metadata["repair_successor_artifact_id"], successor["artifact_id"]
        )
        self.assertEqual(
            metadata["repair_successor_canonical_hash"], canonical_hash
        )
        self.assertIsNone(metadata["pending_successor"])
        self.assertIsNone(metadata["active_successor_claim"])
        self.assertIsNone(metadata["active_repair_claim"])
        self.assertIsNone(metadata.get("patch_successor_artifact_id"))
        self.assertIsNone(metadata.get("delta_successor_artifact_id"))

        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.prepare(
                "TX-after-repair",
                successor_kind="delta",
                scope_hash="different-scope",
            )

    def test_repair_abort_clears_both_active_aliases_without_publication(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=5_700.0):
            self.prepare(
                "TX-repair-abort",
                successor_kind="repair",
                lease_seconds=30,
                scope_hash="repair-abort-scope",
            )
            aborted = self.store.abort_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                transaction_id="TX-repair-abort",
                reason="repair failed before publication",
            )
            retried = self.store.abort_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                transaction_id="TX-repair-abort",
                reason="duplicate abort",
            )

        self.assertEqual(aborted, retried)
        metadata = aborted["metadata"]
        self.assertEqual(metadata["successor_transaction"]["state"], "aborted")
        self.assertEqual(metadata["successor_transaction"]["successor_kind"], "repair")
        self.assertIsNone(metadata["active_successor_claim"])
        self.assertIsNone(metadata["active_repair_claim"])
        self.assertIsNone(metadata.get("repair_successor_artifact_id"))
        self.assertIsNone(metadata.get("successor_artifact_id"))

    def test_commit_fault_does_not_publish_and_correct_retry_can_finish(self) -> None:
        actual_hash = "e" * 64
        successor = self.successor(actual_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=6_000.0):
            self.prepare("TX-fault", lease_seconds=30)
            with self.assertRaisesRegex(ValueError, "successor_canonical_hash_mismatch"):
                self.commit("TX-fault", successor, "f" * 64)

            after_fault = self.metadata()
            self.assertEqual(
                after_fault["pending_successor"]["transaction_id"], "TX-fault"
            )
            self.assertIsNone(after_fault.get("successor_artifact_id"))
            committed = self.commit("TX-fault", successor, actual_hash)

        self.assertEqual(
            committed["metadata"]["successor_artifact_id"], successor["artifact_id"]
        )

    def test_atomic_commit_write_failure_leaves_only_pending_state(self) -> None:
        canonical_hash = "6" * 64
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=6_500.0):
            self.prepare("TX-write-fault", lease_seconds=30)
            with patch.object(
                self.store,
                "atomic_write_json",
                side_effect=OSError("simulated fsync failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated fsync failure"):
                    self.commit("TX-write-fault", successor, canonical_hash)

            after_fault = self.metadata()
            self.assertEqual(after_fault["pending_successor"]["state"], "prepared")
            self.assertIsNone(after_fault.get("successor_artifact_id"))
            committed = self.commit("TX-write-fault", successor, canonical_hash)

        self.assertEqual(
            committed["metadata"]["successor_artifact_id"], successor["artifact_id"]
        )

    def test_base_cas_change_during_lease_blocks_commit_without_publishing(self) -> None:
        canonical_hash = "7" * 64
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=6_800.0):
            self.prepare("TX-base-changed", lease_seconds=30)
        self.store.update_artifact_metadata(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            {"canonical_sha256": "8" * 64},
        )
        with patch("shortdrama_mcp.storage.time.time", return_value=6_801.0):
            with self.assertRaisesRegex(ValueError, "base canonical changed"):
                self.commit("TX-base-changed", successor, canonical_hash)

        metadata = self.metadata()
        self.assertEqual(
            metadata["pending_successor"]["transaction_id"], "TX-base-changed"
        )
        self.assertIsNone(metadata.get("successor_artifact_id"))

    def test_missing_candidate_does_not_consume_pending_transaction(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=6_900.0):
            self.prepare("TX-missing-candidate", lease_seconds=30)
            with self.assertRaisesRegex(ValueError, "unknown successor_artifact_id"):
                self.store.commit_storyboard_successor(
                    self.namespace,
                    self.project_id,
                    self.base["artifact_id"],
                    transaction_id="TX-missing-candidate",
                    successor_artifact_id="ART-missing-candidate",
                    successor_canonical_hash="9" * 64,
                )

        metadata = self.metadata()
        self.assertEqual(
            metadata["pending_successor"]["transaction_id"],
            "TX-missing-candidate",
        )
        self.assertIsNone(metadata.get("successor_artifact_id"))

    def test_expired_commit_fails_closed_until_same_transaction_renews(self) -> None:
        canonical_hash = "1" * 64
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=7_000.0):
            self.prepare("TX-expired-commit", lease_seconds=10)
        with patch("shortdrama_mcp.storage.time.time", return_value=7_011.0):
            with self.assertRaisesRegex(ValueError, "successor_lease_expired"):
                self.commit("TX-expired-commit", successor, canonical_hash)
            self.assertIsNone(self.metadata().get("successor_artifact_id"))
            self.prepare("TX-expired-commit", lease_seconds=20)
        with patch("shortdrama_mcp.storage.time.time", return_value=7_012.0):
            committed = self.commit("TX-expired-commit", successor, canonical_hash)

        self.assertEqual(
            committed["metadata"]["successor_artifact_id"], successor["artifact_id"]
        )

    def test_abort_is_idempotent_and_never_publishes(self) -> None:
        with patch("shortdrama_mcp.storage.time.time", return_value=8_000.0):
            self.prepare("TX-abort", successor_kind="patch", lease_seconds=30)
            first = self.store.abort_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                transaction_id="TX-abort",
                reason="simulated persistence failure",
            )
            second = self.store.abort_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                transaction_id="TX-abort",
                reason="duplicate retry",
            )

        self.assertEqual(first, second)
        metadata = first["metadata"]
        self.assertEqual(metadata["successor_transaction"]["state"], "aborted")
        self.assertIsNone(metadata["pending_successor"])
        self.assertIsNone(metadata["active_patch_claim"])
        self.assertIsNone(metadata.get("successor_artifact_id"))

        with patch("shortdrama_mcp.storage.time.time", return_value=8_001.0):
            replacement = self.prepare("TX-after-abort", lease_seconds=30)
        self.assertEqual(
            replacement["metadata"]["pending_successor"]["transaction_id"],
            "TX-after-abort",
        )

    def test_legacy_stale_active_claim_is_recoverable(self) -> None:
        self.store.update_artifact_metadata(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            {
                "active_patch_claim": {
                    "claim_id": "LEGACY-stale",
                    "successor_kind": "patch",
                    "expected_canonical_hash": self.base_hash,
                    "scope_hash": "legacy-scope",
                    "claimed_at": "2000-01-01T00:00:00Z",
                }
            },
        )
        with patch("shortdrama_mcp.storage.time.time", return_value=2_000_000_000.0):
            recovered = self.prepare("TX-after-legacy", lease_seconds=30)

        metadata = recovered["metadata"]
        self.assertEqual(
            metadata["pending_successor"]["transaction_id"], "TX-after-legacy"
        )
        self.assertTrue(
            any(
                row.get("claim_id") == "LEGACY-stale"
                and row.get("state") == "recovered"
                for row in metadata["successor_transaction_history"]
            )
        )

    def test_legacy_repair_active_alias_is_recovered_and_fenced(self) -> None:
        self.store.update_artifact_metadata(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            {
                "active_repair_claim": {
                    "claim_id": "LEGACY-repair-stale",
                    "successor_kind": "repair",
                    "expected_canonical_hash": self.base_hash,
                    "scope_hash": "legacy-repair-scope",
                    "claimed_at": "2000-01-01T00:00:00Z",
                }
            },
        )
        with patch("shortdrama_mcp.storage.time.time", return_value=2_000_000_000.0):
            recovered = self.prepare(
                "TX-after-legacy-repair",
                successor_kind="repair",
                lease_seconds=30,
                scope_hash="new-repair-scope",
            )

        metadata = recovered["metadata"]
        self.assertEqual(
            metadata["active_repair_claim"]["transaction_id"],
            "TX-after-legacy-repair",
        )
        self.assertTrue(
            any(
                row.get("claim_id") == "LEGACY-repair-stale"
                and row.get("state") == "recovered"
                for row in metadata["successor_transaction_history"]
            )
        )

    def test_legacy_committed_alias_still_blocks_a_new_transaction(self) -> None:
        legacy = self.successor("2" * 64)
        self.store.update_artifact_metadata(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            {
                "patch_successor_artifact_id": legacy["artifact_id"],
                "patch_successor_canonical_hash": "2" * 64,
            },
        )
        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.prepare("TX-blocked-by-legacy")

    def test_repair_committed_alias_alone_is_terminal(self) -> None:
        legacy = self.successor("2a" * 32)
        self.store.update_artifact_metadata(
            self.namespace,
            self.project_id,
            self.base["artifact_id"],
            {
                "repair_successor_artifact_id": legacy["artifact_id"],
                "repair_successor_canonical_hash": "2a" * 32,
            },
        )
        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.prepare(
                "TX-blocked-by-repair-alias",
                successor_kind="repair",
                scope_hash="repair-terminal-scope",
            )

    def test_legacy_claim_finish_wrappers_use_transaction_semantics(self) -> None:
        canonical_hash = "3" * 64
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=9_000.0):
            claimed = self.store.claim_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                expected_canonical_hash=self.base_hash,
                expected_raw_fingerprint=self.base["fingerprint"],
                claim_id="TX-legacy-wrapper",
                successor_kind="delta",
                scope_hash="legacy-wrapper-scope",
                lease_seconds=30,
            )
            self.assertIsNone(claimed["metadata"].get("successor_artifact_id"))
            first = self.store.finish_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                claim_id="TX-legacy-wrapper",
                successor_artifact_id=successor["artifact_id"],
                successor_canonical_hash=canonical_hash,
            )
            second = self.store.finish_storyboard_successor(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                claim_id="TX-legacy-wrapper",
                successor_artifact_id=successor["artifact_id"],
                successor_canonical_hash=canonical_hash,
            )

        self.assertEqual(first, second)
        self.assertEqual(
            first["metadata"]["successor_transaction"]["state"], "committed"
        )

    def test_patch_wrappers_commit_generic_and_legacy_aliases_together(self) -> None:
        canonical_hash = "0" * 64
        successor = self.successor(canonical_hash)
        with patch("shortdrama_mcp.storage.time.time", return_value=9_500.0):
            self.store.claim_storyboard_patch(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                expected_canonical_hash=self.base_hash,
                claim_id="TX-patch-wrapper",
            )
            committed = self.store.finish_storyboard_patch(
                self.namespace,
                self.project_id,
                self.base["artifact_id"],
                claim_id="TX-patch-wrapper",
                successor_artifact_id=successor["artifact_id"],
                successor_canonical_hash=canonical_hash,
            )

        metadata = committed["metadata"]
        self.assertEqual(metadata["successor_artifact_id"], successor["artifact_id"])
        self.assertEqual(
            metadata["patch_successor_artifact_id"], successor["artifact_id"]
        )
        self.assertEqual(metadata["successor_transaction"]["state"], "committed")
        self.assertIsNone(metadata["active_successor_claim"])
        self.assertIsNone(metadata["active_patch_claim"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
