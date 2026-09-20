"""Legacy audit envelopes must preserve literal observations across fragments."""
from __future__ import annotations

import json
import unittest

from shortdrama_mcp.audit_coverage import (
    CONTRACT, LEGACY_PREFIX, PAGE_PREFIX, normalize_coverage_transport,
)
from shortdrama_mcp.models import SemanticAuditLayerInput


class AuditTransportFragmentTests(unittest.TestCase):
    def layer(self, evidence_ids):
        return SemanticAuditLayerInput.model_validate({
            "layer": "表演链", "status": "pass", "summary": "Synthetic transport check",
            "evidence_ids": evidence_ids,
        }).model_dump()

    def test_model_and_decoder_preserve_quote_whitespace_in_both_transports(self):
        quote = "原文 alpha  beta  "
        check = {
            "obligation_id": "EP90-001/grounded_performance",
            "dependency_sha256": "d" * 64, "status": "pass",
            "judgement": "Synthetic judgement for transport preservation only.",
            "observations": [{"reference": "field:EP90-001.原文支撑句", "quote": quote}],
            "issue_ids": [],
        }
        for prefix, key in ((LEGACY_PREFIX, "coverage"), (PAGE_PREFIX, "coverage_page")):
            with self.subTest(prefix=prefix):
                envelope = {"contract": CONTRACT, "manifest_sha256": "f" * 64, "checks": [check]}
                if prefix == PAGE_PREFIX:
                    envelope.update(contract="semantic-obligation-pages-v1", action="append",
                                    collection_key="synthetic-observations")
                encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
                # Deliberately split inside the literal's two consecutive spaces.
                boundary = encoded.index("alpha  beta") + len("alpha ")
                pieces = [encoded[:boundary], encoded[boundary:]]
                chunks = [part[start:start + 200] for part in pieces
                          for start in range(0, len(part), 200)]
                fragments = [f"{prefix}{index:05d}:{chunk}" for index, chunk in enumerate(chunks)]
                self.assertTrue(any(fragment.endswith(" ") for fragment in fragments))
                for leading in ("", "  \t"):
                    with self.subTest(leading=leading):
                        layer = self.layer(["  shot:EP90-001  ",
                                            *(leading + fragment for fragment in fragments)])
                        self.assertEqual(layer["evidence_ids"], ["shot:EP90-001", *fragments])
                        result = normalize_coverage_transport({"layers": [layer]})
                        self.assertEqual(result[key]["checks"], [check])
                        self.assertEqual(result["layers"][0]["evidence_ids"], ["shot:EP90-001"])
                        self.assertEqual(layer["evidence_ids"][1:], fragments)

    def test_ordinary_references_and_fragment_limits_remain_validated(self):
        self.assertEqual(self.layer(["  shot:one  "])["evidence_ids"], ["shot:one"])
        invalid = [[" "], ["missing-kind"], ["shot:one", " shot:one "], ["shot:" + "x" * 252]]
        for prefix in (LEGACY_PREFIX, PAGE_PREFIX):
            fragment = prefix + "00000:payload "
            invalid.extend([[fragment, fragment], [fragment, "  \t" + fragment],
                            [prefix + "00000:" + "x" * 256]])
        for refs in invalid:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                self.layer(refs)

    def test_preserving_fragments_does_not_bypass_sequence_or_json_validation(self):
        for prefix in (LEGACY_PREFIX, PAGE_PREFIX):
            for fragment, error in (("00001:{}", "fragment_sequence_invalid"),
                                    ("00000:{ broken ", "fragment_json_invalid")):
                with self.subTest(prefix=prefix, fragment=fragment):
                    layer = self.layer(["shot:EP90-001", prefix + fragment])
                    with self.assertRaisesRegex(ValueError, error):
                        normalize_coverage_transport({"layers": [layer]})


if __name__ == "__main__":
    unittest.main()
