from __future__ import annotations

import contextlib
import contextvars
import copy
import functools
import hashlib
import inspect
import json
import re
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from . import __version__
from .canonical import (
    COMPACT_MATERIALIZER_CONTRACT,
    COMPACT_SCHEMA_PROFILE,
    COMPACT_SCHEMA_VERSION,
    LEGACY_DERIVED_SHOT_FIELDS,
    NO_DUPLICATE_GENERATION_EVIDENCE,
    assert_compact_storyboard_contract,
    canonical_json_bytes,
    canonical_sha256 as _canonical_sha256,
    compact_contract_evidence,
    compact_storyboard_canonical,
)
from .config import Settings
from .delta import (
    changed_shot_fields,
    ensure_shot_uids,
    merge_storyboard_delta,
    storyboard_identity_manifest,
)
from .file_io import (
    _VerifiedAuditOverlay,
    _seal_service_verified_audit_overlay,
    download_openai_file,
    extract_file,
    extract_xlsx_for_controlled_handoff_revalidation,
    preview_extracted,
    storyboard_to_xlsx,
)
from .models import (
    OpenAIFile,
    SEMANTIC_AUDIT_LAYER_NAMES,
    SemanticAuditInput,
    StoryboardDeltaRequestInput,
    ToolResponse,
)
from .storage import ProjectStore, sha256_bytes, slug_filename, utc_stamp
from .frozen_v053 import validate_storyboard as frozen_v053_validator
from .source_dialogue import (
    SOURCE_ACTION_LEXICON_SHA256,
    SOURCE_ACTION_LEXICON_VERSION,
    SOURCE_DIALOGUE_GRAMMAR_SHA256,
    SOURCE_DIALOGUE_GRAMMAR_VERSION,
    SOURCE_DIALOGUE_PARSER_VERSION,
    SOURCE_SPEECH_LEXICON_SHA256,
    SOURCE_SPEECH_LEXICON_VERSION,
    SourceDialogueAuthorityError,
    authority_claims_match_exactly,
    candidate_protected_dialogue_claims,
    extracted_source_text,
    missing_authoritative_occurrences,
    parse_source_dialogue_authority,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "shortdrama-storyboard"
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import project_agent  # noqa: E402
import r10_bootstrap  # noqa: E402
import storyboard_agent  # noqa: E402
import validate_storyboard  # noqa: E402


STORYBOARD_OPERATIONS = {
    "storyboard_create",
    "storyboard_regenerate",
    "storyboard_change",
    "storyboard_repair",
}

PLAN_ARTIFACT_TYPES = {
    "chapter_intake",
    "novel_analysis",
    "story_diagnosis",
    "hook_plan",
    "adaptation_plan",
    "episode_plan",
    "character_arc",
    "scene_plan",
    "rewrite_plan",
    "storyboard_plan",
    "change_impact",
}

PRODUCTION_ARTIFACT_TYPES = {
    "script",
    "dialogue",
    "anchors",
    "keyframe_prompt",
    "keyframe_image",
    "bgm_plan",
    "voice_plan",
    "production_batch",
    "statistics",
    "change_notes",
    "conversion",
}

# Generic artifact persistence is deliberately narrower than the capability
# registry.  A work order may only write the artifact family its operation was
# created for; storyboard canonical/audit/repair writes use their dedicated
# CAS-aware tools instead.
ARTIFACT_OPERATION_BINDINGS: dict[str, set[str]] = {
    "chapter_intake": {"chapter_intake"},
    "novel_analysis": {"novel_analysis"},
    "story_diagnosis": {"story_diagnosis"},
    "hook_plan": {"hook_design"},
    "adaptation_plan": {"adaptation_plan"},
    "episode_plan": {"episode_plan"},
    "character_arc": {"character_arc"},
    "scene_plan": {"scene_plan"},
    "rewrite_plan": {"rewrite_plan"},
    "storyboard_plan": {"storyboard_plan"},
    "change_impact": {"change_impact"},
    "script": {"script_create", "script_rewrite", "script_change"},
    "dialogue": {"dialogue_rewrite"},
    "anchors": {"anchor_management"},
    "keyframe_prompt": {"keyframe_prompt"},
    "keyframe_image": {"keyframe_image"},
    "bgm_plan": {"audio_bgm"},
    "voice_plan": {"voice_design"},
    "production_batch": {"production_batch"},
    "statistics": {"statistics"},
    "change_notes": {"version_management", "archive"},
    "conversion": {"format_convert"},
}

SOURCE_INTAKE_OPERATIONS = {"project_intake", "source_lock", "chapter_intake"}
WRITE_READY_WORK_ORDER_STATUSES = {"approved", "scope_locked", "executing", "failed_current"}
HANDOFF_RECOVERY_CONFIRMATION = "SUPERSEDE_AND_REISSUE_PRESERVE_AUDIT"
ROLE_CONTINUATION_CONFIRMATION = (
    "ROTATE_WINDOW_AUTHORITY_REVOKE_PREVIOUS_SESSION"
)
WINDOW_NEUTRAL_ACCESS_CONTRACT = "authenticated-namespace-window-neutral-v1"
PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION = (
    "REVALIDATE_AND_APPEND_PRODUCTION_PREFLIGHT_EVIDENCE"
)
MAX_HANDOFF_RECOVERY_ITEMS = 32
HANDOFF_IMMUTABLE_CONTENT_FINGERPRINTS = frozenset(
    {
        "source_bytes",
        "normalized_source",
        "episode_boundary",
        "artifact_raw",
        "artifact_semantic",
        "canonical",
        "project_anchors",
    }
)

AUDIT_CLASS_PRODUCTION_PREFLIGHT = "production_preflight"
AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT = (
    "historical_production_preflight_attestation"
)
AUDIT_CLASS_INDEPENDENT = "independent_audit"
AUDIT_SOURCE_HANDOFF_TYPES = frozenset(
    {"production_to_audit", "repair_to_audit", "audit_intake"}
)
PRODUCTION_PREFLIGHT_PASS = "production_preflight_pass"
PRODUCTION_PREFLIGHT_FAIL = "production_preflight_fail"
HISTORICAL_PRODUCTION_PREFLIGHT_PASS = "historical_contract_pass"
CURRENT_VALIDATION_FAIL = "fail_current"
CONTROLLED_V053_PREFLIGHT_GAP_ID = "GAP-1bb91d83751b422f"
CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS = (
    "HND-f6fd29730a074d2c",
    "HND-95e3b1c141a64783",
    "HND-c88ee199d5fb4676",
    "HND-e8ca409208134104",
    "HND-0dd8fa51a9e24cd0",
    "HND-9029110f35d74270",
)
CONTROLLED_HANDOFF_RECOVERY_GAP_ID = "GAP-0e5d48969c4c494e"
CONTROLLED_HANDOFF_RECOVERY_IDS = (
    "HND-5394638be5fe4a55",
    "HND-ea122cf3048e482a",
    "HND-3a090b4f44c74929",
    "HND-c765ba78aab840ff",
    "HND-7738fa25ce4044c9",
    *CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS,
)
FROZEN_V053_SOURCE_COMMIT = "9deb528db3f9eb27e0f406634036e98b8a3d1f80"
FROZEN_V053_SOURCE_TREE = "3d8837d26f1dbc6c5bacb0953e1a51927fc6ab39"
FROZEN_V053_INIT_BLOB = "9f6be08c26620b351d23046b8aaa6e29156bd119"
FROZEN_V053_INIT_SHA256 = (
    "a0593ff9a660ba95db23093d55ca4db4ac38c9f6ac48715084ff2d13629d6590"
)
FROZEN_V053_VALIDATOR_BLOB = "b2a2edede374912d7fb134fecd3e344552a6c01a"
FROZEN_V053_VALIDATOR_SHA256 = (
    "7142f4fc89a4cd1d537eb5c0b102d4162f40c237ead483c3067631ad71607866"
)
FROZEN_V053_RULES_BLOB = "09bd3f5f5e33f2c03000b29dd3ad2676a39da98c"
FROZEN_V053_RULES_SHA256 = (
    "395e4ca65f342e5c884187bb6e4058e697b6b74e478337c99bf8976ab98511f5"
)
FROZEN_D884_RULES_BLOB = "d29984c5005317de9c42b3b7b0b2503bbd84184b"
FROZEN_D884_RULES_SHA256 = (
    "774a71e242f20e99da3d403e8fd43fdd337d18d0218c331eb4378f5577e3c7b2"
)
FROZEN_D884_MANIFEST_BLOB = "0aced1b7520f8a7e5bce90314b9bc157eb370cbd"
FROZEN_D884_MANIFEST_SHA256 = (
    "5901712e30a33ae88d861eabb6d04ef7eea23e86e489fe977be430503f033320"
)
FROZEN_V053_ERROR_CATALOG_BLOB = (
    "8b1914d2a30679f4d1c915f1bfa2d38d455234d7"
)
FROZEN_V053_ERROR_CATALOG_SHA256 = (
    "996933db8905239ace156903f872f1a177ff495841f9559ee9926ba23ac6b1e4"
)
FROZEN_V053_VALIDATOR_COMPOSITE = (
    "V16.3-148-R10-StableRuntime:R10.1:R10-PG1:7142f4fc89a4cd1d"
)
FROZEN_V053_HANDOFF_RULESET_PROFILES = {
    "HND-f6fd29730a074d2c": (
        "V16.3-148-R10-StableRuntime:R10-PG1:c0867f81158ffca0",
        "c0867f81158ffca02cd5f2b3d53e527e0dcddf5a7ad02341758a54954636f5b9",
    ),
    "HND-95e3b1c141a64783": (
        "V16.3-148-R10-StableRuntime:R10-PG1:c0867f81158ffca0",
        "c0867f81158ffca02cd5f2b3d53e527e0dcddf5a7ad02341758a54954636f5b9",
    ),
    "HND-c88ee199d5fb4676": (
        "V16.3-148-R10-StableRuntime:R10-PG1:d8841a2f52d86bed",
        "d8841a2f52d86bed8554b21c3543e85f753b4b6f01a1b0a94964a9da4bcf90dc",
    ),
    "HND-e8ca409208134104": (
        "V16.3-148-R10-StableRuntime:R10-PG1:d8841a2f52d86bed",
        "d8841a2f52d86bed8554b21c3543e85f753b4b6f01a1b0a94964a9da4bcf90dc",
    ),
    "HND-0dd8fa51a9e24cd0": (
        "V16.3-148-R10-StableRuntime:R10-PG1:d8841a2f52d86bed",
        "d8841a2f52d86bed8554b21c3543e85f753b4b6f01a1b0a94964a9da4bcf90dc",
    ),
    "HND-9029110f35d74270": (
        "V16.3-148-R10-StableRuntime:R10-PG1:d8841a2f52d86bed",
        "d8841a2f52d86bed8554b21c3543e85f753b4b6f01a1b0a94964a9da4bcf90dc",
    ),
}
CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE = (
    "V16.3-148-R10-StableRuntime:R10.1:R10-PG1:af1fccfe2ab8c3bc"
)
CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT = (
    "af1fccfe2ab8c3bc946b0537cf9fe23241f51afc776a9d33275c133599570140"
)
CONTROLLED_V053_CURRENT_VALIDATION_PROFILES = {
    "HND-f6fd29730a074d2c": {
        "handoff_id": "HND-f6fd29730a074d2c",
        "canonical_sha256": (
            "35cf1227e70e4ced71df80b3db92a68586c121e821f87de05c0b46371ab5131f"
        ),
        "current_report_sha256": (
            "8b85adf678601819044974fb42510034568a57a77f3cbaea733052a1c2a0225a"
        ),
        "shot_count": 56,
        "error_count": 334,
        "warning_count": 0,
        "error_histogram": {
            "E-AUDIO-STAGE-001": 56,
            "E-BATCH-004": 56,
            "E-CONTINUITY-001": 163,
            "E-I2V-INDEP-001": 1,
            "E-I2V-INDEP-002": 56,
            "E-INTEGRATION-001": 1,
            "E-JIMENG-TIMING-001": 1,
        },
        "current_validator_composite": (
            CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE
        ),
        "current_validator_fingerprint": (
            CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT
        ),
    },
    "HND-95e3b1c141a64783": {
        "handoff_id": "HND-95e3b1c141a64783",
        "canonical_sha256": (
            "e4fcc1f6e4eab3a357ae8db96ec988a5bd661bf476d8c3af8c7f8fc4febe4423"
        ),
        "current_report_sha256": (
            "a6c26b492db69c8aae0b53eb10bf10f07618914a2ba6c2e7ff1755e2c4d89ed7"
        ),
        "shot_count": 61,
        "error_count": 373,
        "warning_count": 0,
        "error_histogram": {
            "E-AUDIO-STAGE-001": 61,
            "E-BATCH-003": 19,
            "E-BATCH-004": 61,
            "E-CONTINUITY-001": 156,
            "E-I2V-INDEP-001": 1,
            "E-I2V-INDEP-002": 61,
            "E-INTEGRATION-001": 1,
            "E-JIMENG-TIMING-001": 10,
            "E-SOURCE-SELECT-001": 3,
        },
        "current_validator_composite": (
            CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE
        ),
        "current_validator_fingerprint": (
            CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT
        ),
    },
    "HND-c88ee199d5fb4676": {
        "handoff_id": "HND-c88ee199d5fb4676",
        "canonical_sha256": (
            "8e2470c4e3e31eb53675d0c1a65c64f3fca6f40e720089faf32661721e04ffe6"
        ),
        "current_report_sha256": (
            "f6c231d4d1388bc24b550e6fdaf6f0621482edca57b5d6c853dc98816e9f2473"
        ),
        "shot_count": 55,
        "error_count": 380,
        "warning_count": 0,
        "error_histogram": {
            "E-AUDIO-STAGE-001": 55,
            "E-BATCH-003": 29,
            "E-BATCH-004": 55,
            "E-CONTINUITY-001": 158,
            "E-I2V-INDEP-002": 55,
            "E-JIMENG-TIMING-001": 3,
            "E-VIS-001": 25,
        },
        "current_validator_composite": (
            CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE
        ),
        "current_validator_fingerprint": (
            CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT
        ),
    },
    "HND-e8ca409208134104": {
        "handoff_id": "HND-e8ca409208134104",
        "canonical_sha256": (
            "c550aa4e9f4c8f1d7ffa4d598a2344d4c1a34091eed675d0cb39c05226b53e12"
        ),
        "current_report_sha256": (
            "7b5b09ee117190694b47f730ce52cf53db6657a8f74b995a09ff77964c48371e"
        ),
        "shot_count": 60,
        "error_count": 401,
        "warning_count": 0,
        "error_histogram": {
            "E-AUDIO-STAGE-001": 60,
            "E-BATCH-003": 20,
            "E-BATCH-004": 60,
            "E-CONTINUITY-001": 175,
            "E-I2V-INDEP-002": 60,
            "E-JIMENG-TIMING-001": 11,
            "E-VIS-001": 15,
        },
        "current_validator_composite": (
            CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE
        ),
        "current_validator_fingerprint": (
            CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT
        ),
    },
    "HND-0dd8fa51a9e24cd0": {
        "handoff_id": "HND-0dd8fa51a9e24cd0",
        "canonical_sha256": (
            "8c6f7a1946fa52b3df40e2f169f24b22c05c923f7e1d70446732a5c088207bd6"
        ),
        "current_report_sha256": (
            "997b93a683703d121c0a0b99b4ee9fb5a40289b8e75dd7b8073d85aa454d5a3c"
        ),
        "shot_count": 57,
        "error_count": 375,
        "warning_count": 0,
        "error_histogram": {
            "E-AUDIO-STAGE-001": 57,
            "E-BATCH-003": 18,
            "E-BATCH-004": 57,
            "E-CONTINUITY-001": 162,
            "E-I2V-INDEP-001": 1,
            "E-I2V-INDEP-002": 57,
            "E-INTEGRATION-001": 1,
            "E-JIMENG-TIMING-001": 10,
            "E-SOURCE-SELECT-001": 1,
            "E-VIS-001": 11,
        },
        "current_validator_composite": (
            CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE
        ),
        "current_validator_fingerprint": (
            CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT
        ),
    },
    "HND-9029110f35d74270": {
        "handoff_id": "HND-9029110f35d74270",
        "canonical_sha256": (
            "472f1e6457fcbc1ba9cf220b9bee4953a8fa93cbd9de4baffe0c782cbf644480"
        ),
        "current_report_sha256": (
            "bbb6549df6ea9e403664237ed95edde78d075d2f5fad48e582b09ee3ec4b38ee"
        ),
        "shot_count": 59,
        "error_count": 378,
        "warning_count": 0,
        "error_histogram": {
            "E-AUDIO-STAGE-001": 59,
            "E-BATCH-003": 17,
            "E-BATCH-004": 59,
            "E-CONTINUITY-001": 174,
            "E-I2V-INDEP-002": 59,
            "E-JIMENG-TIMING-001": 1,
            "E-VIS-001": 9,
        },
        "current_validator_composite": (
            CONTROLLED_V053_CURRENT_VALIDATOR_COMPOSITE
        ),
        "current_validator_fingerprint": (
            CONTROLLED_V053_CURRENT_VALIDATOR_FINGERPRINT
        ),
    },
}
DRAFT_PREFLIGHT_PASS = "draft_preflight_pass"
DRAFT_PREFLIGHT_FAIL = "draft_preflight_fail"

PERFORMANCE_DIAGNOSTIC_STAGES = (
    "task_started",
    "inputs_ready",
    "plan_ready",
    "canonical_ready",
    "delivery_ready",
)
PERFORMANCE_DIAGNOSTIC_GAPS = {
    "inputs_ready": "source_and_context",
    "plan_ready": "storyboard_planning",
    "canonical_ready": "canonical_authorship",
    "delivery_ready": "commit_export_and_delivery",
}
PERFORMANCE_DIAGNOSTIC_ROUTES = {
    "production": {"full_rebuild", "episode_delta", "unknown"},
    "independent_audit": {"first_independent_audit", "repeat_audit", "unknown"},
    "repair": {"audit_repair", "unknown"},
}
PERFORMANCE_DIAGNOSTIC_REASONING_MODES = {
    "light",
    "medium",
    "high",
    "extra_high",
    "max",
    "unknown",
}
PERFORMANCE_DIAGNOSTIC_WINDOW_TYPES = {"fresh", "continued", "unknown"}

# A local patch may repair presentation/execution detail, but it must never be
# used to smuggle a story, boundary, timing, identity, cognition, or source
# change past the full-rebuild workflow.  Matching is deliberately bilingual
# because imported canonical storyboards can contain either Chinese or English
# extension fields.
PATCH_FORBIDDEN_FIELDS = {
    "é•œå¤´ç¼–å·",
    "åŠŸèƒ½ç±»åž‹",
    "é•œå¤´ç±»åž‹",
    "åœºæ™¯è¿žç»­æ€§id",
    "ç´¯è®¡æ—¶é•¿",
    "æ—¶é•¿",
    "å¯è§ä¸»ä½“",
    "ç©ºé—´ç«™ä½æè¿°",
    "èµ·å§‹çŠ¶æ€",
    "äººç‰©è°ƒåº¦",
    "ç”»é¢å†…å®¹",
    "åŠ¨ä½œä¸Žè¡¨æ¼”",
    "å°è¯",
    "å¿ƒç†éŸ³è½¨",
    "å¿ƒç†éŸ³è½¨å±žæ€§",
    "å‚è€ƒå›¾é”šå®š",
    "è¦†ç›–åŽŸæ–‡",
    "åŽŸæ–‡æ”¯æ’‘å¥",
    "shot_id",
    "shotid",
    "scene_continuity_id",
    "duration",
    "cumulative_duration",
    "source_sentence",
    "source_support",
    "ç”Ÿæˆæ‰¹æ¬¡",
    "ç”Ÿæˆæ‰¹æ¬¡å¼•ç”¨",
    "seedanceå¼•ç”¨",
    "seedanceæ‰¹æ¬¡å¼•ç”¨",
    "seedance_batch_ref",
    "batch_id",
    "batch_mapping",
    "å…³é”®å¸§å¼•ç”¨",
    "keyframe_reference",
    # Stable CAS coordinates and every known server-owned identity/evidence
    # field are never caller-patchable, even when they happen to coexist with
    # the 27 authored fields in a persisted compact shot.
    "shot_uid",
    "episode_id",
    "scene_uid",
    "artifact_id",
    "work_order_id",
    "handoff_id",
    "trace_id",
    "certificate_id",
    "canonical_sha256",
    "source_fingerprint",
    "boundary_hash",
    "fingerprint",
    "schema_version",
    "schema_profile",
    "field_order",
    "total_duration",
    "materializer_contract",
    "materialization_passes",
    "model_authored_derived_chars",
    "legacy_generation_calls",
    "seedance_batches_generated",
    "duplicate_generated_bytes",
    *{field.casefold() for field in LEGACY_DERIVED_SHOT_FIELDS},
}
PATCH_FORBIDDEN_FIELD_TERMS = (
    "åŽŸæ–‡",
    "ç« èŠ‚è¾¹ç•Œ",
    "è®¤çŸ¥",
    "ä¿¡æ¯æƒé™",
    "äººç‰©èº«ä»½",
    "source_",
    "source.",
    "boundary",
    "cognition",
    "knowledge_state",
    "åœºæ™¯æ‹“æ‰‘",
    "é•œå¤´æ‹“æ‰‘",
    "ç”Ÿæˆæ‰¹æ¬¡",
    "æ‰¹æ¬¡æ˜ å°„",
    "æ‰¹æ¬¡å¼•ç”¨",
    "seedanceå¼•ç”¨",
    "seedance_reference",
    "batch_ref",
    "batch_mapping",
    "keyframe_reference",
    "shot_uid",
    "episode_id",
    "scene_uid",
    "artifact_id",
    "work_order_id",
    "handoff_id",
    "trace_id",
    "certificate_id",
    "canonical_sha",
    "fingerprint",
    "identity_manifest",
    "server_",
    "schema_version",
    "schema_profile",
    "materializer_contract",
    "materialization_pass",
    "model_authored_derived",
    "legacy_generation_call",
    "seedance_batches_generated",
    "duplicate_generated_byte",
)

AUDIT_GATE_IDS = tuple(f"G{index}" for index in range(14))
AUDIT_LAYER_NAMES = SEMANTIC_AUDIT_LAYER_NAMES
SEMANTIC_AUDIT_CONTRACT_VERSION = (
    "semantic-audit-v6-adjacent-continuity-structured-voice-conversation-scenes"
)
STRICT_OUTPUT_CONTRACT = "R10-SPC-1"
SEMANTIC_AUDIT_CONTRACT_FINGERPRINT = sha256_bytes(
    SEMANTIC_AUDIT_CONTRACT_VERSION.encode("utf-8")
)

_ACTIVE_CONSUMPTION_CLAIMS: contextvars.ContextVar[tuple[dict[str, str], ...]] = (
    contextvars.ContextVar("shortdrama_active_consumption_claims", default=())
)


def _release_consumption_claims_on_error(method: Any) -> Any:
    """Compensate ordinary failures after a durable single-consumer claim.

    The durable journal and deterministic ids make a later retry resumable;
    releasing here prevents a handled exception from needlessly waiting for
    lease expiry.  A hard process crash is recovered by the bounded lease.
    """

    @functools.wraps(method)
    def wrapped(self: "StoryboardService", *args: Any, **kwargs: Any) -> Any:
        context_token = _ACTIVE_CONSUMPTION_CLAIMS.set(())
        try:
            return method(self, *args, **kwargs)
        except BaseException as exc:
            for claim in reversed(_ACTIVE_CONSUMPTION_CLAIMS.get()):
                try:
                    if claim.get("kind") == "draft_preflight":
                        self.store.update_draft_preflight_commit_progress(
                            claim["namespace"],
                            claim["project_id"],
                            claim["record_id"],
                            claim_token=claim["claim_token"],
                            stage="failed_recoverable",
                            progress={
                                "last_error_type": type(exc).__name__,
                                "last_error_at": utc_stamp(),
                            },
                        )
                        self.store.release_draft_preflight_commit(
                            claim["namespace"],
                            claim["project_id"],
                            claim["record_id"],
                            claim_token=claim["claim_token"],
                            reason=f"handled {type(exc).__name__}",
                        )
                    elif claim.get("kind") == "handoff":
                        self.store.release_handoff_claim(
                            claim["namespace"],
                            claim["project_id"],
                            claim["record_id"],
                            consumer_work_order_id=claim["consumer_work_order_id"],
                            claim_token=claim["claim_token"],
                            reason=f"handled {type(exc).__name__}",
                        )
                    elif claim.get("kind") == "reaudit_handoff":
                        self.store.release_reaudit_successor_claim(
                            claim["namespace"],
                            claim["project_id"],
                            claim["record_id"],
                            consumer_work_order_id=claim[
                                "consumer_work_order_id"
                            ],
                            claim_token=claim["claim_token"],
                            reason=f"handled {type(exc).__name__}",
                        )
                    elif claim.get("kind") == "reaudit_successor":
                        self.store.rollback_reaudit_successor(
                            claim["namespace"],
                            claim["project_id"],
                            claim["record_id"],
                            route_trace_id=claim["route_trace_id"],
                        )
                except Exception:
                    # Preserve the original failure.  The bounded lease is the
                    # fallback if compensation itself cannot be persisted.
                    pass
            raise
        finally:
            _ACTIVE_CONSUMPTION_CLAIMS.reset(context_token)

    return wrapped


def _fence_role_operation(method: Any) -> Any:
    """Keep one storyboard role generation active for the whole tool call.

    The short-lived storage lease prevents a continuation from rotating the
    authority between validation and persistence.  Every storage write made
    inside the call also rechecks and renews the same signed lease, so a stale
    request that wakes after a crash/expiry cannot cross the new authority
    head.  Nested repair/delta calls reuse the outer lease.
    """

    signature = inspect.signature(method)

    @functools.wraps(method)
    def wrapped(self: "StoryboardService", *args: Any, **kwargs: Any) -> Any:
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        namespace = bound.arguments.get("namespace")
        project_id = bound.arguments.get("project_id")
        work_order_id = bound.arguments.get("work_order_id")
        request_session_id = bound.arguments.get("request_session_id")
        if not all(
            isinstance(value, str) and value
            for value in (namespace, project_id, work_order_id)
        ):
            # Local compatibility audit may mint its audit work order inside
            # the method.  Role continuation is cloud-only, so there is no
            # rotatable authority to fence on this path.
            return method(self, *args, **kwargs)

        existing = self.store.current_authority_operation()
        if existing is not None:
            if (
                existing.get("namespace") != namespace
                or existing.get("project_id") != project_id
                or existing.get("work_order_id") != work_order_id
            ):
                raise ValueError("authority_operation_scope_conflict")
            return method(self, *args, **kwargs)

        hub = self.store.load_hub(namespace, project_id)
        work_order = _find_work_order(hub, work_order_id)
        operation = str(work_order.get("operation") or "")
        if operation not in STORYBOARD_OPERATIONS | {"storyboard_audit"}:
            return method(self, *args, **kwargs)
        binding = self._assert_work_order_session(
            namespace, work_order, request_session_id
        )
        lease = self.store.claim_authority_operation(
            namespace,
            project_id,
            work_order_id=work_order_id,
            expected_authority_root_id=str(binding["authority_root_id"]),
            expected_authority_generation=int(binding.get("generation") or 0),
            expected_session_fingerprint=binding.get("session_fingerprint"),
        )
        failed = False
        try:
            with self.store.bind_authority_operation(lease):
                return method(self, *args, **kwargs)
        except BaseException:
            failed = True
            raise
        finally:
            try:
                self.store.release_authority_operation(
                    namespace,
                    project_id,
                    authority_root_id=str(lease["authority_root_id"]),
                    claim_token=str(lease["claim_token"]),
                )
            except Exception:
                if not failed:
                    raise

    return wrapped

# These columns are deterministic projections of the atomic shot fields.  A
# protected-scope comparison must ignore them or an older, unmaterialized
# canonical would look semantically different from the same current shot.
FULL_COMMIT_DERIVED_SHOT_FIELDS = {
    "ç´¯è®¡æ—¶é•¿",
    *LEGACY_DERIVED_SHOT_FIELDS,
}


def _stable_json_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value)


def canonical_storyboard_sha256(storyboard: dict[str, Any]) -> str:
    """Return the layout-independent identity of a canonical storyboard."""

    return _canonical_sha256(storyboard)


def _draft_commit_payload_sha256(
    *,
    project_id: str,
    work_order_id: str,
    draft_preflight_id: str,
    canonical_sha256: str,
    source_artifact_id: str | None,
    file_name: str,
) -> str:
    """Hash the logical commit independently from a physical window.

    ``commit_request_sha256`` remains the issuance identity used to derive the
    deterministic artifact/certificate/handoff ids.  This second identity is
    deliberately session-free: after a hard crash, a signed descendant of the
    same authority root can prove that it is finishing the exact logical
    payload without minting a second publication.
    """

    return sha256_bytes(
        _stable_json_bytes(
            {
                "contract": "draft-commit-logical-payload-v0511",
                "project_id": project_id,
                "work_order_id": work_order_id,
                "draft_preflight_id": draft_preflight_id,
                "canonical_sha256": canonical_sha256,
                "source_artifact_id": source_artifact_id,
                "file_name": file_name,
            }
        )
    )


def _prepare_compact_storyboard_for_commit(
    storyboard: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the one permitted semantic-compaction pass, then add CAS IDs."""

    compact = compact_storyboard_canonical(storyboard)
    return ensure_shot_uids(compact, in_place=True)


def _assert_r10_spc1_output_contract(storyboard: dict[str, Any]) -> None:
    """Reject every new storyboard write that still uses the retired prompt contract."""

    source_lock = storyboard.get("source_lock")
    expected = {
        "output_contract": STRICT_OUTPUT_CONTRACT,
        "target_model": "Seedance 2.5",
        "aspect_ratio": "9:16",
    }
    if not isinstance(source_lock, dict):
        raise ValueError(
            "r10_spc1_contract_required: source_lock must declare the active output contract"
        )
    mismatches = [
        f"{field}={source_lock.get(field)!r}, expected {value!r}"
        for field, value in expected.items()
        if source_lock.get(field) != value
    ]
    if mismatches:
        raise ValueError(
            "r10_spc1_contract_required: " + "; ".join(mismatches)
        )


def _export_compact_storyboard(
    storyboard: dict[str, Any],
    report: dict[str, Any],
    *,
    audit_overlay: _VerifiedAuditOverlay | None = None,
) -> bytes:
    """Project a prepared canonical without running semantic migration again."""

    return storyboard_to_xlsx(
        storyboard,
        report,
        assume_compact=True,
        audit_overlay=audit_overlay,
    )


def build_audit_cache_key(
    *,
    canonical_sha256: str,
    source_fingerprint: str,
    boundary_hash: str,
    ruleset_version: str,
    validator_version: str,
    audit_class: str,
    artifact_fingerprint: str = "",
    parser_fingerprint: str = "",
    schema_fingerprint: str = "",
    runtime_fingerprint: str = "",
    scripts_fingerprint: str = "",
    dependencies_fingerprint: str = "",
    anchors_fingerprint: str = "",
    target_model_fingerprint: str = "",
    scope_fingerprint: str = "",
    runtime_test_certificate_id: str = "",
    semantic_audit_contract_fingerprint: str = "",
) -> str:
    """Build an audit cache key whose safety-relevant inputs are explicit."""

    components = {
        "canonical": canonical_sha256,
        "source": source_fingerprint,
        "boundary": boundary_hash,
        "ruleset": ruleset_version,
        "validator": validator_version,
        "audit_class": audit_class,
        "artifact": artifact_fingerprint,
        "parser": parser_fingerprint,
        "schema": schema_fingerprint,
        "runtime": runtime_fingerprint,
        "scripts": scripts_fingerprint,
        "dependencies": dependencies_fingerprint,
        "anchors": anchors_fingerprint,
        "target_model": target_model_fingerprint,
        "scope": scope_fingerprint,
        "runtime_test_certificate_id": runtime_test_certificate_id,
        "semantic_audit_contract": semantic_audit_contract_fingerprint,
    }
    return f"AUDCACHE-{sha256_bytes(_stable_json_bytes(components))}"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _artifact_version(artifacts: list[dict[str, Any]], artifact_type: str) -> str:
    number = 1 + sum(item.get("artifact_type") == artifact_type for item in artifacts)
    return f"v{number}"


def _find_work_order(hub: dict[str, Any], work_order_id: str) -> dict[str, Any]:
    work_order = next((item for item in hub.get("work_orders", []) if item.get("work_order_id") == work_order_id), None)
    if work_order is None:
        raise ValueError(f"unknown work_order_id: {work_order_id}")
    return work_order


def _episode_range_from_target(value: Any) -> set[int] | None:
    """Parse the machine-readable episode target forms accepted by the API."""

    compact = re.sub(r"\s+", "", str(value or "")).casefold()
    match = re.fullmatch(
        r"(?:episode:)?ep(\d+)(?:[-â€”â€“~è‡³](?:ep)?(\d+))?", compact
    ) or re.fullmatch(r"ç¬¬?(\d+)(?:[-â€”â€“~è‡³](\d+))?é›†", compact)
    if match is None:
        # Also accept the common fully-qualified Chinese range
        # ``ç¬¬06é›†-ç¬¬07é›†`` without broadening arbitrary natural language.
        match = re.fullmatch(r"ç¬¬(\d+)é›†[-â€”â€“~è‡³]ç¬¬?(\d+)é›†", compact)
    if match is None:
        return None
    lower = int(match.group(1))
    upper = int(match.group(2) or lower)
    if lower < 1 or upper < 1:
        return None
    lower, upper = min(lower, upper), max(lower, upper)
    return set(range(lower, upper + 1))


def _episode_scope_from_targets(targets: Any) -> set[int] | None:
    """Return an episode allow-list, rejecting mixed ambiguous scope labels."""

    if not isinstance(targets, list):
        return None
    clean_targets = [value for value in targets if str(value or "").strip()]
    if not clean_targets:
        return None
    parsed = [_episode_range_from_target(value) for value in clean_targets]
    if all(value is None for value in parsed):
        return None
    if any(value is None for value in parsed):
        raise ValueError(
            "full_commit_episode_scope_ambiguous: episode targets cannot be mixed "
            "with unparseable scope labels"
        )
    result: set[int] = set()
    for value in parsed:
        result.update(value or set())
    return result


def _shot_episode_number(storyboard: dict[str, Any], shot: dict[str, Any]) -> int | None:
    """Resolve a shot's episode without guessing from its array position."""

    candidates = (
        shot.get("episode_id"),
        storyboard.get("episode_id"),
        shot.get("åœºæ™¯è¿žç»­æ€§ID"),
        shot.get("é•œå¤´ç¼–å·"),
    )
    for raw in candidates:
        compact = re.sub(r"\s+", "", str(raw or "")).casefold()
        if not compact:
            continue
        match = re.search(r"(?:^|[^a-z])ep[-_]?0*(\d+)", compact)
        if match is None:
            match = re.search(r"ç¬¬0*(\d+)é›†", compact)
        if match is not None:
            number = int(match.group(1))
            return number if number >= 1 else None
    return None


def _protected_shot_hash(shot: dict[str, Any]) -> str:
    """Hash all caller-owned shot semantics, including identity and sources."""

    return sha256_bytes(
        _stable_json_bytes(
            {
                key: copy.deepcopy(value)
                for key, value in shot.items()
                if key not in FULL_COMMIT_DERIVED_SHOT_FIELDS
            }
        )
    )


def _issue_category(error_code: str) -> str:
    code = error_code.upper()
    if "PUBLIC-CONT" in code:
        return "ç©ºé—´è¿žç»­"
    if "PUBLIC" in code:
        return "ç¦é¡¹ä¸Žå£°éŸ³"
    if "CHAPTER-BUDGET" in code:
        return "é•œå¤´èŠ‚å¥"
    if any(term in code for term in ("SOURCE", "DIALOGUE", "COGNITION", "CHANGELOCK", "CHAPTER-INTAKE")):
        return "æºæ–‡å¯¹ç™½"
    if any(term in code for term in ("CAMERA", "VISIBLE", "VISIBILITY")):
        return "æœºä½å¯è§æ€§"
    if any(term in code for term in ("CONTINUITY", "SPATIAL", "LIGHT")):
        return "ç©ºé—´è¿žç»­"
    if "VOICE" in code:
        return "ç¦é¡¹ä¸Žå£°éŸ³"
    if any(term in code for term in ("PERFORMANCE", "EXPRESSION", "BLINK", "PUNCT")):
        return "è¡¨æ¼”é“¾"
    if any(term in code for term in ("DURATION", "RHYTHM", "SHOTLOAD", "SHOT-")):
        return "é•œå¤´èŠ‚å¥"
    if any(term in code for term in ("BATCH", "MOTION", "SEEDANCE", "CROSSCONTEXT", "I2V")):
        return "æ­£å¼æ¯ç‰ˆå•é•œæ‰§è¡Œå®Œæ•´æ€§"
    if any(term in code for term in ("AUDIO", "BGM", "PSYCH", "PROHIBIT")):
        return "ç¦é¡¹ä¸Žå£°éŸ³"
    return "ç»Ÿè®¡é—­åˆ"


def _gate_for_issue(error_code: str) -> str:
    category = _issue_category(error_code)
    return {
        "æºæ–‡å¯¹ç™½": "source_coverage",
        "æœºä½å¯è§æ€§": "camera_visibility",
        "ç©ºé—´è¿žç»­": "spatial_continuity",
        "è¡¨æ¼”é“¾": "performance_chain",
        "é•œå¤´èŠ‚å¥": "shot_rhythm",
        "æ­£å¼æ¯ç‰ˆå•é•œæ‰§è¡Œå®Œæ•´æ€§": "compact_master_single_shot_integrity",
        "ç¦é¡¹ä¸Žå£°éŸ³": "prohibitions_audio",
        "ç»Ÿè®¡é—­åˆ": "statistics",
    }[category]


def _gate_results(required_gates: list[str], issues: list[dict[str, Any]]) -> dict[str, str]:
    results = {gate: "pass" for gate in required_gates}
    for issue in issues:
        gate = _gate_for_issue(str(issue.get("error_code") or ""))
        if (
            gate == "compact_master_single_shot_integrity"
            and gate not in results
            and "seedance_independence" in results
        ):
            # Read/repair an old work-order ledger without making its retired
            # identifier the contract for newly created work orders.
            gate = "seedance_independence"
        if gate in results:
            results[gate] = "fail"
        elif required_gates:
            results[required_gates[0]] = "fail"
    return results


def _issue_shot_ids(
    storyboard: dict[str, Any], issue: dict[str, Any]
) -> list[str]:
    supplied = issue.get("shot_ids")
    if isinstance(supplied, list):
        values = [str(value) for value in supplied if str(value)]
        if values:
            return values
    location = str(issue.get("location") or "")
    match = re.search(r"shots\[(\d+)\]", location)
    shots = storyboard.get("shots", [])
    if match:
        index = int(match.group(1)) - 1
        if 0 <= index < len(shots) and isinstance(shots[index], dict):
            row = shots[index]
            return [str(row.get("é•œå¤´ç¼–å·") or row.get("shot_uid") or index + 1)]
    return []


SCENE_ID_FIELDS = ("scene_uid", "scene_id", "åœºæ™¯ID", "åœºæ™¯è¿žç»­æ€§ID")


def _shot_reference_ids(shot: dict[str, Any]) -> set[str]:
    """Return every stable/display identifier that can target one shot."""

    return {
        str(value)
        for value in (shot.get("shot_uid"), shot.get("é•œå¤´ç¼–å·"))
        if value not in (None, "")
    }


def _shot_scene_aliases(shot: dict[str, Any]) -> tuple[str, ...]:
    """Resolve scene aliases without assuming which producer spelling was used."""

    return tuple(
        dict.fromkeys(
            str(shot.get(field)).strip()
            for field in SCENE_ID_FIELDS
            if shot.get(field) not in (None, "") and str(shot.get(field)).strip()
        )
    )


def _stable_scene_id(shot: dict[str, Any]) -> str:
    aliases = _shot_scene_aliases(shot)
    return aliases[0] if aliases else ""


def _scene_scope_groups(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Build deterministic logical scene groups from a compact candidate."""

    groups: dict[str, dict[str, Any]] = {}
    for shot in storyboard.get("shots", []):
        if not isinstance(shot, dict):
            continue
        scene_id = _stable_scene_id(shot)
        if not scene_id:
            continue
        group = groups.setdefault(
            scene_id,
            {"stable_scene_id": scene_id, "aliases": set(), "shots": []},
        )
        group["aliases"].update(_shot_scene_aliases(shot))
        group["shots"].append(shot)
    result: list[dict[str, Any]] = []
    for scene_id in sorted(groups):
        group = groups[scene_id]
        shots = list(group["shots"])
        result.append(
            {
                "stable_scene_id": scene_id,
                "scene_aliases": sorted(str(value) for value in group["aliases"]),
                "shots": shots,
                "scope_shot_ids": [
                    str(row.get("é•œå¤´ç¼–å·") or row.get("shot_uid") or "")
                    for row in shots
                ],
                "scope_sha256": sha256_bytes(_stable_json_bytes(shots)),
            }
        )
    return result


def _targeted_rows_for_scene(
    rows: Any,
    scoped_refs: set[str],
) -> list[dict[str, Any]]:
    """Keep evidence whose complete target set belongs to this scene."""

    if not isinstance(rows, list):
        return []
    selected: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        target_field = next(
            (
                field
                for field in ("mapped_shot_uids", "target_shots")
                if isinstance(raw.get(field), list) and raw.get(field)
            ),
            None,
        )
        if target_field is None:
            continue
        targets = {str(value) for value in raw.get(target_field, []) if str(value)}
        if targets and targets <= scoped_refs:
            selected.append(copy.deepcopy(raw))
    return selected


def _scene_candidate_for_validation(
    candidate: dict[str, Any], scoped_shots: list[dict[str, Any]]
) -> dict[str, Any]:
    """Project a validation-only scene canonical and remove global-only inputs."""

    scoped = copy.deepcopy(candidate)
    scoped["shots"] = copy.deepcopy(scoped_shots)
    scoped["total_duration"] = round(
        sum(float(row.get("æ—¶é•¿") or 0) for row in scoped_shots), 10
    )
    scoped_refs = (
        set().union(*(_shot_reference_ids(row) for row in scoped_shots))
        if scoped_shots
        else set()
    )
    scoped["protected_dialogue"] = _targeted_rows_for_scene(
        scoped.get("protected_dialogue"), scoped_refs
    )
    source_ingestion = scoped.get("source_ingestion")
    if isinstance(source_ingestion, dict):
        source_ingestion["protected_dialogue"] = _targeted_rows_for_scene(
            source_ingestion.get("protected_dialogue"), scoped_refs
        )
    evidence = scoped.get("prevention_evidence")
    if isinstance(evidence, dict):
        for field in ("scene_state_ledger", "shot_plan", "performance_chains"):
            rows = evidence.get(field)
            if isinstance(rows, list):
                evidence[field] = [
                    copy.deepcopy(row)
                    for row in rows
                    if isinstance(row, dict)
                    and str(row.get("shot_id") or "") in scoped_refs
                ]
        source_rows = evidence.get("source_ledger")
        if isinstance(source_rows, list):
            evidence["source_ledger"] = [
                copy.deepcopy(row)
                for row in source_rows
                if isinstance(row, dict)
                and isinstance(row.get("target_shots"), list)
                and {
                    str(value)
                    for value in row.get("target_shots", [])
                    if str(value)
                }
                & scoped_refs
            ]
        change_lock = evidence.get("change_lock")
        if isinstance(change_lock, dict):
            change_lock["must_include"] = []
    return scoped


def _scene_issue_is_applicable(issue: dict[str, Any]) -> bool:
    """Return whether a finding is decidable from a complete scene alone."""

    location = str(issue.get("location") or "")
    if location.endswith(".ç´¯è®¡æ—¶é•¿") or location == "total_duration":
        return False
    if location.startswith(
        (
            "shots[",
            "scene:",
            "scene_state:",
            "shot_plan:",
            "performance_chain:",
            "protected_dialogue[",
        )
    ):
        return True
    return location in {
        "prevention_evidence.scene_state_ledger",
        "prevention_evidence.shot_plan",
        "prevention_evidence.change_lock.must_exclude",
    }


def _scene_scoped_report(
    candidate: dict[str, Any], scoped_shots: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run local gates on a scene and explicitly mark global gates N/A."""

    scoped_candidate = _scene_candidate_for_validation(candidate, scoped_shots)
    raw_report = validate_storyboard.validate_project(scoped_candidate, mode="audit")
    raw_issues = [
        copy.deepcopy(row)
        for row in raw_report.get("issues", [])
        if isinstance(row, dict)
    ]
    applicable = [row for row in raw_issues if _scene_issue_is_applicable(row)]
    excluded = [row for row in raw_issues if not _scene_issue_is_applicable(row)]
    report = copy.deepcopy(raw_report)
    report["validation_scope"] = "scene"
    report["issues"] = applicable
    report["shot_count"] = len(scoped_shots)
    report["error_count"] = sum(
        str(row.get("severity") or "fail").casefold()
        not in {"warning", "warn", "info"}
        for row in applicable
    )
    report["warning_count"] = sum(
        str(row.get("severity") or "").casefold() in {"warning", "warn"}
        for row in applicable
    )
    report["status"] = "pass" if not applicable else "fail_current"
    report["scope_status"] = report["status"]
    reason = (
        "scene scope cannot decide chapter intake, whole-project coverage, "
        "episode boundaries, cumulative statistics, or export round-trip"
    )
    report["scope_gate_results"] = {
        gate: {
            "status": "not_applicable",
            "evidence_count": 0,
            "evidence_ids": [],
            "not_applicable_reason": reason,
        }
        for gate in (
            "G1",
            "G2",
            "G3",
            "statistics_closure",
            "source_coverage",
            "episode_boundary",
        )
    }
    report["not_applicable_gates"] = list(report["scope_gate_results"])
    report["not_applicable_findings"] = [
        {
            "error_code": str(row.get("error_code") or "E-UNKNOWN"),
            "location": str(row.get("location") or ""),
            "reason": reason,
        }
        for row in excluded
    ]
    report["raw_scene_projection_issue_count"] = len(raw_issues)
    report["applicable_scene_issue_count"] = len(applicable)
    return report, scoped_candidate


def _draft_quality_metrics(
    storyboard: dict[str, Any],
    issues: list[dict[str, Any]],
    *,
    project_id: str = "quality",
    known_pattern_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Measure first-draft usability by unique affected shots, never issue points."""

    shots = [row for row in storyboard.get("shots", []) if isinstance(row, dict)]
    shot_to_episode = {
        str(row.get("é•œå¤´ç¼–å·") or row.get("shot_uid") or index + 1): str(
            row.get("episode_id") or "å…¨é¡¹ç›®"
        )
        for index, row in enumerate(shots)
    }
    affected: set[str] = set()
    pattern_counts: dict[str, int] = {}
    hard_major_count = 0
    global_blocking_count = 0
    known_hits: set[str] = set()
    for issue in issues:
        ids = _issue_shot_ids(storyboard, issue)
        affected.update(ids)
        severity = str(issue.get("severity") or "fail").casefold()
        blocking = bool(
            issue.get("blocking", severity in {"fail", "error", "blocking"})
        )
        if blocking or severity in {"fail", "error", "blocking"}:
            hard_major_count += 1
            if not ids:
                global_blocking_count += 1
        normalized = {
            "error_code": issue.get("error_code"),
            "field": str(
                issue.get("field")
                or str(issue.get("location") or "å…¨è¡¨").rsplit(".", 1)[-1]
            ),
            "issue_type": issue.get("issue_type")
            or issue.get("template_gate_id")
            or _issue_category(str(issue.get("error_code") or "")),
            "root_cause": issue.get("root_cause"),
            "category": issue.get("semantic_layer")
            or _issue_category(str(issue.get("error_code") or "")),
        }
        pattern = storyboard_agent.issue_pattern_key(project_id, normalized)
        pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        if known_pattern_keys and pattern in known_pattern_keys:
            known_hits.add(pattern)

    total = len(shots)
    affected_count = len(affected)
    direct = (
        round((total - affected_count) * 100.0 / total, 2)
        if total
        else (100.0 if not issues else 0.0)
    )
    episode_totals: dict[str, int] = {}
    episode_affected: dict[str, set[str]] = {}
    for shot_id, episode_id in shot_to_episode.items():
        episode_totals[episode_id] = episode_totals.get(episode_id, 0) + 1
        if shot_id in affected:
            episode_affected.setdefault(episode_id, set()).add(shot_id)
    episode_rates = {
        episode_id: round(
            (count - len(episode_affected.get(episode_id, set()))) * 100.0 / count,
            2,
        )
        for episode_id, count in episode_totals.items()
        if count
    }
    minimum_episode = min(episode_rates.values(), default=direct)
    systemic_patterns = sorted(
        key for key, count in pattern_counts.items() if count >= 2
    )
    new_local_issue_rate = (
        round(affected_count * 100.0 / total, 2) if total else (0.0 if not issues else 100.0)
    )
    zero_tolerance_clean = (
        hard_major_count == 0
        and not known_hits
        and not systemic_patterns
        and global_blocking_count == 0
    )
    grade = "A"
    if not zero_tolerance_clean or direct < 95.0 or minimum_episode < 96.0:
        grade = "C"
    elif direct < 98.0 or new_local_issue_rate > 2.0:
        grade = "B"
    commit_eligible = bool(
        grade == "A"
        and direct >= 98.0
        and minimum_episode >= 96.0
        and new_local_issue_rate <= 2.0
        and zero_tolerance_clean
        and not issues
    )
    return {
        "grade": grade,
        "checked_shot_count": total,
        "issue_point_count": len(issues),
        "unique_affected_shot_count": affected_count,
        "affected_shot_ids": sorted(affected),
        "first_draft_correctness_percent": direct,
        "direct_usability_percent": direct,
        "new_local_issue_shot_rate_percent": new_local_issue_rate,
        "episode_correctness_percent": episode_rates,
        "minimum_episode_correctness_percent": minimum_episode,
        "hard_or_major_issue_count": hard_major_count,
        "global_blocking_issue_count": global_blocking_count,
        "known_recurrence_count": len(known_hits),
        "known_recurrence_pattern_keys": sorted(known_hits),
        "systemic_duplicate_count": len(systemic_patterns),
        "systemic_pattern_keys": systemic_patterns,
        "commit_eligible": commit_eligible,
        "final_independent_audit_required_percent": 100.0,
    }


class StoryboardService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = ProjectStore(
            settings.data_dir,
            settings.public_base_url,
            max_download_bytes=settings.max_file_bytes,
        )
        self.bootstrap = r10_bootstrap.verify_bundled(self_test=False)
        # Every behavior-bearing Skill resource must invalidate runtime and
        # preflight certificates when it changes. Restrict by controlled
        # textual rule formats, but include Markdown contracts and JSONL rule
        # registries rather than hashing only JSON schemas.
        ruleset_paths = [
            SKILL_ROOT / "SKILL.md",
            *sorted(
                path
                for path in (SKILL_ROOT / "references").rglob("*")
                if path.is_file() and path.suffix.casefold() in {".md", ".json", ".jsonl"}
            ),
        ]
        ruleset_fingerprints = [
            [str(path.relative_to(SKILL_ROOT)), sha256_bytes(path.read_bytes())]
            for path in ruleset_paths
            if path.is_file()
        ]
        validator_path = SCRIPTS_DIR / "validate_storyboard.py"
        self._ruleset_digest = sha256_bytes(_stable_json_bytes(ruleset_fingerprints))[:16]
        self._validator_digest = (
            sha256_bytes(validator_path.read_bytes())[:16] if validator_path.is_file() else "missing"
        )
        script_fingerprints = [
            [str(path.relative_to(SKILL_ROOT)), sha256_bytes(path.read_bytes())]
            for path in sorted(SCRIPTS_DIR.glob("*.py"))
            if path.is_file()
        ]
        requirements_path = PLUGIN_ROOT / "requirements.txt"
        dependency_payload = {
            "python": ".".join(str(value) for value in sys.version_info[:3]),
            "requirements_sha256": (
                sha256_bytes(requirements_path.read_bytes()) if requirements_path.is_file() else "missing"
            ),
        }
        self._runtime_scripts_digest = sha256_bytes(_stable_json_bytes(script_fingerprints))[:16]
        self._dependency_digest = sha256_bytes(_stable_json_bytes(dependency_payload))[:16]
        self._ruleset_fingerprint = sha256_bytes(_stable_json_bytes(ruleset_fingerprints))
        self._validator_fingerprint = (
            sha256_bytes(validator_path.read_bytes())
            if validator_path.is_file()
            else sha256_bytes(b"missing-validator")
        )
        self._scripts_fingerprint = sha256_bytes(_stable_json_bytes(script_fingerprints))
        self._dependencies_fingerprint = sha256_bytes(_stable_json_bytes(dependency_payload))
        service_root = Path(__file__).resolve().parent
        service_paths = sorted(
            path
            for path in service_root.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in {".py", ".json"}
            and "__pycache__" not in path.parts
        )
        self._service_code_fingerprint = sha256_bytes(
            _stable_json_bytes(
                [
                    [
                        path.relative_to(service_root).as_posix(),
                        sha256_bytes(path.read_bytes()),
                    ]
                    for path in service_paths
                ]
            )
        )
        parser_paths = [
            Path(__file__).with_name("canonical.py"),
            Path(__file__).with_name("file_io.py"),
            Path(__file__).with_name("source_dialogue.py"),
        ]
        self._parser_fingerprint = sha256_bytes(
            _stable_json_bytes(
                [
                    [path.name, sha256_bytes(path.read_bytes())]
                    for path in parser_paths
                    if path.is_file()
                ]
            )
        )
        schema_path = SKILL_ROOT / "references" / "storyboard_schema.json"
        self._schema_fingerprint = (
            sha256_bytes(schema_path.read_bytes())
            if schema_path.is_file()
            else sha256_bytes(b"missing-storyboard-schema")
        )
        self._runtime_fingerprint = sha256_bytes(
            _stable_json_bytes(
                {
                    "runtime": self.bootstrap.get("version"),
                    "workflow": self.bootstrap.get("workflow_revision"),
                    "service_code": self._service_code_fingerprint,
                }
            )
        )
        self._target_model_fingerprint = sha256_bytes(
            b"R10.1 compact formal-master single-shot execution contract"
        )
        self._project_schema = json.loads(
            (SKILL_ROOT / "references" / "project_agent_schema.json").read_text(encoding="utf-8")
        )
        self._ledger_schema = json.loads(
            (SKILL_ROOT / "references" / "agent_ledger_schema.json").read_text(encoding="utf-8")
        )
        try:
            Draft202012Validator.check_schema(self._project_schema)
            Draft202012Validator.check_schema(self._ledger_schema)
        except SchemaError as exc:
            raise RuntimeError(f"R10 lifecycle schema is invalid: {exc.message}") from exc

    def _require_runtime(self) -> None:
        if self.bootstrap.get("status") != "pass":
            raise RuntimeError("R10 runtime bootstrap failed; execution is blocked")

    @staticmethod
    def _validate_schema_record(
        schema: dict[str, Any], definition: str, value: dict[str, Any]
    ) -> None:
        record_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"#/$defs/{definition}",
            "$defs": schema.get("$defs", {}),
        }
        try:
            Draft202012Validator(record_schema).validate(value)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or definition
            raise ValueError(
                f"lifecycle_schema_mismatch: {definition}.{location}: {exc.message}"
            ) from exc

    @property
    def _ruleset_version(self) -> str:
        return ":".join(
            [
                str(self.bootstrap.get("version") or "unknown"),
                str(self.bootstrap.get("workflow_revision") or "unknown"),
                self._ruleset_digest,
            ]
        )

    def _validator_version(self) -> str:
        return ":".join(
            [
                str(getattr(validate_storyboard, "VERSION", "unknown")),
                str(getattr(validate_storyboard, "SCHEMA_VERSION", "unknown")),
                str(getattr(validate_storyboard, "WORKFLOW_PROFILE", "unknown")),
                self._validator_digest,
            ]
        )

    def _runtime_certificate_key(self) -> str:
        return "RUNTIMECERT-" + sha256_bytes(
            _stable_json_bytes(
                {
                    "runtime": self.bootstrap.get("version"),
                    "workflow": self.bootstrap.get("workflow_revision"),
                    "ruleset": self._ruleset_version,
                    "validator": self._validator_version(),
                    "scripts": self._runtime_scripts_digest,
                    "service_code": self._service_code_fingerprint,
                    "dependencies": self._dependency_digest,
                    "deployment_mode": self.settings.deployment_mode,
                    "output_contract": STRICT_OUTPUT_CONTRACT,
                }
            )
        )

    def _matching_runtime_certificate(
        self, namespace: str, project_id: str
    ) -> dict[str, Any] | None:
        runtime_key = self._runtime_certificate_key()
        try:
            certificate = self.store.latest_runtime_certificate(
                namespace,
                project_id,
                certificate_key=runtime_key,
            )
        except ValueError as exc:
            if str(exc).startswith("runtime_certificate_storage_invalid:"):
                raise
            record_id = str(exc).rsplit(": ", 1)[-1]
            record_path = (
                self.store.project_dir(namespace, project_id)
                / "certificates"
                / f"{record_id}.json"
            )
            try:
                damaged = self.store.read_json(record_path)
            except (OSError, ValueError):
                damaged = None
            if isinstance(damaged, dict) and (
                damaged.get("certificate_type")
                == "runtime_full_regression"
                or damaged.get("certificate_key") == runtime_key
            ):
                raise ValueError(
                    "runtime_certificate_storage_invalid: persisted runtime certificate is tampered"
                ) from exc
            raise
        if certificate is None:
            return None
        if (
            certificate.get("certificate_type") != "runtime_full_regression"
            or certificate.get("result") != "pass"
            or certificate.get("ruleset_version") != self._ruleset_version
            or certificate.get("validator_version") != self._validator_version()
            or certificate.get("scripts_digest") != self._runtime_scripts_digest
            or certificate.get("service_code_fingerprint") != self._service_code_fingerprint
            or certificate.get("dependency_digest") != self._dependency_digest
        ):
            return None
        return certificate

    def _require_runtime_test_certificate(
        self, namespace: str, project_id: str
    ) -> dict[str, Any]:
        try:
            certificate = self._matching_runtime_certificate(
                namespace, project_id
            )
        except ValueError as exc:
            if str(exc).startswith(
                "runtime_certificate_storage_invalid:"
            ):
                raise ValueError(
                    "runtime_test_certificate_required: persisted runtime certificate is missing, stale, or tampered"
                ) from exc
            raise
        if certificate is None:
            raise ValueError(
                "runtime_test_certificate_required: call get_agent_status with full_self_test=true after any runtime/rule/schema change"
            )
        if not self.store.project_record_is_signed(certificate):
            raise ValueError(
                "runtime_test_certificate_required: legacy unsigned runtime certificate must be regenerated"
            )
        return certificate

    def _persist_runtime_certificate(
        self,
        namespace: str,
        project_id: str,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        certificate_id = _new_id("CERT")
        tests = {}
        for key in (
            "golden_tests",
            "strict_prompt_tests",
            "public_scene_tests",
            "agent_tests",
            "project_agent_tests",
        ):
            value = report.get(key)
            if isinstance(value, dict):
                tests[key] = {
                    "status": value.get("status"),
                    "test_count": value.get("test_count"),
                    "test_pass_count": value.get("test_pass_count"),
                    "test_fail_count": value.get("test_fail_count"),
                }
        value = {
            "certificate_id": certificate_id,
            "certificate_type": "runtime_full_regression",
            "certificate_key": self._runtime_certificate_key(),
            "result": "pass" if report.get("status") == "pass" else "fail_current",
            "runtime_version": report.get("version"),
            "workflow_revision": report.get("workflow_revision"),
            "ruleset_version": self._ruleset_version,
            "validator_version": self._validator_version(),
            "scripts_digest": self._runtime_scripts_digest,
            "service_code_fingerprint": self._service_code_fingerprint,
            "dependency_digest": self._dependency_digest,
            "deployment_mode": self.settings.deployment_mode,
            "output_contract": STRICT_OUTPUT_CONTRACT,
            "tests": tests,
        }
        return self.store.save_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=certificate_id,
            value=value,
        )

    @staticmethod
    def _boundary_hash(storyboard: dict[str, Any]) -> str:
        chapter_intake = storyboard.get("chapter_intake")
        if not isinstance(chapter_intake, dict):
            chapter_intake = {}
        boundary_payload = {
            "source_lock": storyboard.get("source_lock") or {},
            "chapter_boundary": chapter_intake.get("chapter_boundary")
            or chapter_intake.get("target_boundary")
            or chapter_intake.get("boundary"),
            "previous_boundary": chapter_intake.get("previous_chapter_boundary"),
            "next_boundary": chapter_intake.get("next_chapter_boundary"),
        }
        return sha256_bytes(_stable_json_bytes(boundary_payload))

    def _source_fingerprint(
        self,
        namespace: str,
        project_id: str,
        storyboard: dict[str, Any],
        source_artifact_id: str | None,
        artifact_record: dict[str, Any] | None = None,
    ) -> str:
        if source_artifact_id:
            try:
                source_record, _path = self.store.get_artifact(namespace, project_id, source_artifact_id)
                source_metadata = source_record.get("metadata", {})
                # A repaired storyboard inherits the original source lineage.
                # Its own canonical hash identifies the derived storyboard and
                # must never replace the novel/script source fingerprint.
                inherited = source_metadata.get("source_fingerprint")
                return str(inherited or source_record.get("fingerprint"))
            except ValueError as exc:
                raise ValueError(
                    "source_artifact_missing_or_invalid: explicit source_artifact_id "
                    "must resolve inside the current namespace and project"
                ) from exc
        if artifact_record:
            cached = artifact_record.get("metadata", {}).get("source_fingerprint")
            if cached:
                return str(cached)
        source_lock = storyboard.get("source_lock") or {}
        explicit = source_lock.get("source_fingerprint") if isinstance(source_lock, dict) else None
        return str(explicit or sha256_bytes(_stable_json_bytes(source_lock)))

    @staticmethod
    def _source_dialogue_issue(
        *,
        location: str,
        evidence: str,
        repair_action: str,
    ) -> dict[str, Any]:
        return {
            "error_code": "E-SOURCE-SELECT-001",
            "location": location,
            "field": "protected_dialogue",
            "semantic_layer": "source_dialogue_authority",
            "template_gate_id": "THG-05",
            "severity": "blocking",
            "blocking": True,
            "evidence": evidence,
            "repair_action": repair_action,
        }

    @staticmethod
    def _selected_dialogue_source_entry(
        hub: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Select authority only from server-persisted project state.

        A derived script is preferred over the novel when both are current.
        Crucially, no candidate inventory, ledger, cognition matrix, anchor, or
        caller-authored speaker list participates in this selection.
        """

        state = hub.get("project_state") or {}
        latest = state.get("latest_artifacts") or {}
        if isinstance(latest, dict):
            for key in ("source:script", "source:novel"):
                entry = latest.get(key)
                if isinstance(entry, dict) and entry.get("artifact_id"):
                    return copy.deepcopy(entry)
        sources = [
            row
            for row in state.get("authoritative_sources", [])
            if isinstance(row, dict)
            and row.get("artifact_id")
            and str(row.get("source_role") or "").removeprefix("source:")
            in {"novel", "script"}
        ]
        for role in ("script", "novel"):
            matching = [
                row
                for row in sources
                if str(row.get("source_role") or "").removeprefix("source:")
                == role
            ]
            if matching:
                return copy.deepcopy(matching[-1])
        return None

    def _source_dialogue_authority_manifest(
        self,
        namespace: str,
        project_id: str,
        candidate: dict[str, Any],
        *,
        hub: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Recompute THG-05 speaker/text authority from the imported source."""

        active_hub = hub or self.store.load_hub(namespace, project_id)
        entry = self._selected_dialogue_source_entry(active_hub)
        base_manifest: dict[str, Any] = {
            "parser_version": SOURCE_DIALOGUE_PARSER_VERSION,
            "grammar_version": SOURCE_DIALOGUE_GRAMMAR_VERSION,
            "grammar_sha256": SOURCE_DIALOGUE_GRAMMAR_SHA256,
            "speech_lexicon_version": SOURCE_SPEECH_LEXICON_VERSION,
            "speech_lexicon_sha256": SOURCE_SPEECH_LEXICON_SHA256,
            "action_lexicon_version": SOURCE_ACTION_LEXICON_VERSION,
            "action_lexicon_sha256": SOURCE_ACTION_LEXICON_SHA256,
            "authority_source": "server_project_state_only",
            "candidate_authority_fields_used": [],
        }
        claim_error: str | None = None
        try:
            claims = candidate_protected_dialogue_claims(candidate)
        except SourceDialogueAuthorityError as exc:
            claims = []
            claim_error = str(exc)
        if entry is None:
            issues: list[dict[str, Any]] = []
            raw_source_lock = candidate.get("source_lock")
            authority_lock_groups = (
                (
                    ("source_artifact_id", "authoritative_source_artifact_id"),
                    lambda value: value.strip(),
                    "source_artifact_id",
                ),
                (
                    ("source_fingerprint", "authoritative_source_fingerprint"),
                    lambda value: value.strip().casefold(),
                    "source_fingerprint",
                ),
                (
                    ("source_role", "authoritative_source_role"),
                    lambda value: value.strip().casefold().removeprefix("source:"),
                    "source_role",
                ),
            )
            declared_authority_lock = False
            if "source_lock" in candidate and not isinstance(raw_source_lock, dict):
                issues.append(
                    self._source_dialogue_issue(
                        location="source_lock",
                        evidence=(
                            "å€™é€‰source_lockå¿…é¡»æ˜¯å¯¹è±¡ï¼›é¡¹ç›®åˆæ²¡æœ‰æœåŠ¡ç«¯æƒå¨æºï¼Œ"
                            "è¯¥ç•¸å½¢ç»‘å®šä¸èƒ½æŒ‰not_applicableå¿½ç•¥ã€‚"
                        ),
                        repair_action="å¯¼å…¥æœåŠ¡ç«¯æƒå¨æºå¹¶ç”±æœåŠ¡ç«¯é‡ç­¾source_lockã€‚",
                    )
                )
            elif isinstance(raw_source_lock, dict):
                for keys, normalize, label in authority_lock_groups:
                    parsed: list[tuple[str, str]] = []
                    for key in keys:
                        if key not in raw_source_lock:
                            continue
                        declared_authority_lock = True
                        raw_value = raw_source_lock.get(key)
                        if not isinstance(raw_value, str) or not raw_value.strip():
                            issues.append(
                                self._source_dialogue_issue(
                                    location=f"source_lock.{key}",
                                    evidence=(
                                        f"æ— æƒå¨æºæ—¶ä»å£°æ˜Žäº†ç•¸å½¢{label}ï¼š{raw_value!r}"
                                    ),
                                    repair_action="ç§»é™¤ä¼ªç»‘å®šï¼›å¯¼å…¥æƒå¨æºåŽç”±æœåŠ¡ç«¯é‡ç­¾ã€‚",
                                )
                            )
                            continue
                        parsed.append((key, normalize(raw_value)))
                    if len({value for _key, value in parsed}) > 1:
                        issues.append(
                            self._source_dialogue_issue(
                                location=f"source_lock.{label}_alias_conflict",
                                evidence=f"æ— æƒå¨æºæ—¶{label}åˆ«åä»äº’ç›¸å†²çªã€‚",
                                repair_action="ç§»é™¤å†²çªç»‘å®šï¼›å¯¼å…¥æƒå¨æºåŽç”±æœåŠ¡ç«¯é‡ç­¾ã€‚",
                            )
                        )
                if declared_authority_lock:
                    issues.append(
                        self._source_dialogue_issue(
                            location="source_dialogue_authority.source",
                            evidence=(
                                "å€™é€‰æ˜¾å¼å£°æ˜Žäº†authority-bearing source_lockå­—æ®µï¼Œ"
                                "ä½†é¡¹ç›®æ²¡æœ‰æœåŠ¡ç«¯æ³¨å†Œçš„æƒå¨æºï¼›å€™é€‰é”ä¸èƒ½è‡ªè¯ã€‚"
                            ),
                            repair_action="å…ˆå¯¼å…¥å¹¶é”å®šæœåŠ¡ç«¯æƒå¨æºï¼Œå†é‡å»ºå€™é€‰ã€‚",
                        )
                    )
            if claim_error:
                issues.append(
                    self._source_dialogue_issue(
                        location="source_dialogue_authority.claims",
                        evidence=claim_error,
                        repair_action=(
                            "ä¿®å¤æ¯æ¡å€™é€‰protected_dialogueçš„ç»“æž„ï¼Œå¹¶ç¡®ä¿é¡¶å±‚ä¸Ž"
                            "source_ingestioné•œåƒå®Œå…¨ä¸€è‡´åŽé‡æ–°é¢„æ£€ã€‚"
                        ),
                    )
                )
            if claims:
                issues.append(
                    self._source_dialogue_issue(
                        location="source_dialogue_authority.source",
                        evidence=(
                            "å€™é€‰å£°æ˜Žäº†protected_dialogueï¼Œä½†é¡¹ç›®æ²¡æœ‰æœåŠ¡ç«¯æ³¨å†Œçš„"
                            "source:novel/source:scriptæƒå¨æºï¼›å€™é€‰å­—æ®µä¸èƒ½è‡ªè¯å¯¹ç™½ã€‚"
                        ),
                        repair_action=(
                            "å…ˆå¯¼å…¥å¹¶é”å®šæƒå¨åŽŸæ–‡/å‰§æœ¬ï¼Œå†ç”±æœåŠ¡ç«¯authority parseré‡å»º"
                            "protected_dialogueï¼›ä¸å¾—ç”¨inventory/ledger/cognition/anchoræŽˆæƒã€‚"
                        ),
                    )
                )
            manifest = {
                **base_manifest,
                "status": "fail_current" if issues else "not_applicable",
                "reason": "no authoritative novel/script source is registered",
                "source_artifact_id": None,
                "source_role": None,
                "source_fingerprint": None,
                "source_extraction_sha256": None,
                "occurrence_count": 0,
                "claim_count": len(claims),
                "missing_occurrence_count": 0,
                "exact_claim_match": not claims,
                "claim_validation_error": claim_error,
                "occurrences": [],
            }
            manifest["manifest_sha256"] = sha256_bytes(
                _stable_json_bytes(manifest)
            )
            return manifest, issues

        source_artifact_id = str(entry.get("artifact_id") or "")
        source_role = str(entry.get("source_role") or "").removeprefix("source:")
        source_fingerprint = str(entry.get("fingerprint") or "")
        raw_source_lock = candidate.get("source_lock")
        source_lock = raw_source_lock if isinstance(raw_source_lock, dict) else {}
        issues: list[dict[str, Any]] = []
        if claim_error:
            issues.append(
                self._source_dialogue_issue(
                    location="source_dialogue_authority.claims",
                    evidence=claim_error,
                    repair_action=(
                        "ä¿®å¤æ¯æ¡å€™é€‰protected_dialogueçš„speaker/textç»“æž„ï¼Œå¹¶ç¡®ä¿"
                        "é¡¶å±‚ä¸Žsource_ingestioné•œåƒçš„æ•°é‡ã€é¡ºåºå’Œå†…å®¹å®Œå…¨ä¸€è‡´ã€‚"
                    ),
                )
            )
        if "source_lock" in candidate and not isinstance(raw_source_lock, dict):
            issues.append(
                self._source_dialogue_issue(
                    location="source_lock",
                    evidence="å€™é€‰source_lockå¿…é¡»æ˜¯å¯¹è±¡ï¼Œä¸èƒ½ä»¥å…¶ä»–ç±»åž‹ç»•è¿‡æƒå¨æºç»‘å®šã€‚",
                    repair_action="ä»ŽæœåŠ¡ç«¯å½“å‰æƒå¨æºé‡æ–°ç­¾å‘å®Œæ•´source_lockå¯¹è±¡ã€‚",
                )
            )

        def validate_lock_aliases(
            keys: tuple[str, ...],
            *,
            expected: str,
            normalize: Any,
            label: str,
        ) -> None:
            declared: list[tuple[str, str, Any]] = []
            for key in keys:
                if key not in source_lock:
                    continue
                raw_value = source_lock.get(key)
                if not isinstance(raw_value, str) or not raw_value.strip():
                    issues.append(
                        self._source_dialogue_issue(
                            location=f"source_lock.{key}",
                            evidence=(
                                f"å€™é€‰source_lockçš„{label}å£°æ˜Žå¿…é¡»æ˜¯éžç©ºå­—ç¬¦ä¸²ï¼›"
                                f"actual={raw_value!r}"
                            ),
                            repair_action="ä»ŽæœåŠ¡ç«¯å½“å‰æƒå¨æºé‡æ–°ç­¾å‘source lockã€‚",
                        )
                    )
                    continue
                declared.append((key, raw_value, normalize(raw_value)))
            normalized_values = {value for _key, _raw, value in declared}
            if len(normalized_values) > 1:
                issues.append(
                    self._source_dialogue_issue(
                        location=f"source_lock.{label}_alias_conflict",
                        evidence=(
                            f"å€™é€‰source_lockçš„{label}åˆ«åäº’ç›¸å†²çªï¼š"
                            + ", ".join(f"{key}={raw!r}" for key, raw, _ in declared)
                        ),
                        repair_action="åˆ é™¤å†²çªå£°æ˜Žå¹¶ç”±æœåŠ¡ç«¯é‡ç­¾å”¯ä¸€ä¸€è‡´çš„source lockã€‚",
                    )
                )
            expected_value = normalize(expected)
            for key, raw_value, normalized in declared:
                if normalized == expected_value:
                    continue
                issues.append(
                    self._source_dialogue_issue(
                        location=f"source_lock.{key}",
                        evidence=(
                            f"å€™é€‰source_lockçš„{label}ä¸ŽæœåŠ¡ç«¯æƒå¨æºä¸ä¸€è‡´ï¼š"
                            f"candidate={raw_value!r}, authoritative={expected!r}"
                        ),
                        repair_action="ä»ŽæœåŠ¡ç«¯å½“å‰æƒå¨æºé‡æ–°ç­¾å‘source lockã€‚",
                    )
                )

        validate_lock_aliases(
            ("source_artifact_id", "authoritative_source_artifact_id"),
            expected=source_artifact_id,
            normalize=lambda value: value.strip(),
            label="source_artifact_id",
        )
        validate_lock_aliases(
            ("source_fingerprint", "authoritative_source_fingerprint"),
            expected=source_fingerprint,
            normalize=lambda value: value.strip().casefold(),
            label="source_fingerprint",
        )
        validate_lock_aliases(
            ("source_role", "authoritative_source_role"),
            expected=source_role,
            normalize=lambda value: value.strip().casefold().removeprefix("source:"),
            label="source_role",
        )
        extraction_sha = ""
        occurrences: list[Any] = []
        try:
            extracted, source_record, _path = self._load_extracted_or_payload(
                namespace, project_id, source_artifact_id
            )
            actual_fingerprint = str(source_record.get("fingerprint") or "")
            if not source_fingerprint or actual_fingerprint != source_fingerprint:
                raise SourceDialogueAuthorityError(
                    "source_dialogue_authority_source_binding_mismatch: project source "
                    "entry does not match stored artifact bytes"
                )
            recorded_role = str(
                source_record.get("metadata", {}).get("source_role") or source_role
            ).removeprefix("source:")
            if recorded_role != source_role or source_role not in {"novel", "script"}:
                raise SourceDialogueAuthorityError(
                    "source_dialogue_authority_source_binding_mismatch: source role is "
                    "not stable across project entry and artifact metadata"
                )
            source_text = extracted_source_text(extracted)
            extraction_sha = sha256_bytes(_stable_json_bytes(extracted))
            occurrences = parse_source_dialogue_authority(
                source_text, source_role=source_role
            )
        except (OSError, TypeError, ValueError, SourceDialogueAuthorityError) as exc:
            # SourceDialogueAuthorityError is a ValueError subclass; spelling
            # it out documents that parser ambiguity is an expected gate, not
            # an internal error eligible for warning-only fallback.
            issues.append(
                self._source_dialogue_issue(
                    location="source_dialogue_authority.source",
                    evidence=str(exc)[:4000],
                    repair_action=(
                        "ä¿®æ­£æˆ–æ˜Žç¡®æ ‡æ³¨æƒå¨æºä¸­çš„è¯´è¯äºº/å‘å£°è¯­æ³•ï¼Œé‡æ–°å¯¼å…¥æºæ–‡ä»¶åŽè¿è¡Œé¢„æ£€ï¼›"
                        "ä¸å¾—ç”¨å€™é€‰äººç‰©è¡¨åå‘æŽˆæƒã€‚"
                    ),
                )
            )

        missing = missing_authoritative_occurrences(occurrences, claims)
        expected_claims = [(row.speaker, row.text) for row in occurrences]
        exact_claim_match = authority_claims_match_exactly(occurrences, claims)
        if not exact_claim_match:
            first_difference = next(
                (
                    index
                    for index in range(max(len(expected_claims), len(claims)))
                    if (
                        expected_claims[index]
                        if index < len(expected_claims)
                        else None
                    )
                    != (claims[index] if index < len(claims) else None)
                ),
                0,
            )
            expected_at_difference = (
                expected_claims[first_difference]
                if first_difference < len(expected_claims)
                else None
            )
            claim_at_difference = (
                claims[first_difference]
                if first_difference < len(claims)
                else None
            )
            issues.append(
                self._source_dialogue_issue(
                    location=(
                        "source_dialogue_authority.claims."
                        f"index-{first_difference}"
                    ),
                    evidence=(
                        "å€™é€‰protected_dialogueå¿…é¡»ä¸Žæºæ–‡authorityé€occurrenceä¸¥æ ¼ç­‰å€¼ï¼›"
                        f"expected_count={len(expected_claims)}, claim_count={len(claims)}, "
                        f"first_expected={expected_at_difference!r}, "
                        f"first_claim={claim_at_difference!r}ã€‚"
                        "å‰æ’ã€åŽæ’ã€ä¸­æ’æˆ–é‡å¤é¢å¤–å¯¹ç™½å‡ä¸è¢«æºæ–‡æŽˆæƒã€‚"
                    ),
                    repair_action=(
                        "å°†protected_dialogueæ¢å¤ä¸ºæºæ–‡authorityçš„ç²¾ç¡®è¯´è¯äººã€æ­£æ–‡ã€æ ‡ç‚¹ã€"
                        "é‡å¤æ¬¡æ•°å’Œé¡ºåºï¼›å™è¿°è½¬å°è¯é¡»èµ°å¦ä¸€ä¸ªæ˜Žç¡®æŽˆæƒä¸”å¯å®¡è®¡çš„å‰§æƒ…å˜æ›´æµç¨‹ã€‚"
                    ),
                )
            )
        for occurrence in missing:
            issues.append(
                self._source_dialogue_issue(
                    location=(
                        "source_dialogue_authority.occurrence."
                        f"line-{occurrence.line_number}.char-{occurrence.char_offset}"
                    ),
                    evidence=(
                        "æºæ–‡å¯¹ç™½æœªæŒ‰è¯´è¯äººã€æ­£æ–‡ã€æ ‡ç‚¹ã€é‡å¤æ¬¡æ•°å’Œé¡ºåºè¿›å…¥ä¿æŠ¤æ¸…å•ï¼š"
                        f"speaker={occurrence.speaker!r}, text={occurrence.text!r}, "
                        f"kind={occurrence.kind}"
                    ),
                    repair_action=(
                        "ä»ŽæœåŠ¡ç«¯æºæ–‡authorityé‡å»ºprotected_dialogueï¼Œå¹¶é€occurrenceä¿æŒ"
                        "è¯´è¯äººã€æ­£æ–‡ã€æ ‡ç‚¹ã€é‡å¤æ¬¡æ•°å’Œé¡ºåºã€‚"
                    ),
                )
            )
        manifest = {
            **base_manifest,
            "status": "pass" if not issues else "fail_current",
            "source_artifact_id": source_artifact_id,
            "source_role": source_role,
            "source_fingerprint": source_fingerprint,
            "source_extraction_sha256": extraction_sha or None,
            "occurrence_count": len(occurrences),
            "claim_count": len(claims),
            "missing_occurrence_count": len(missing),
            "exact_claim_match": exact_claim_match,
            "claim_validation_error": claim_error,
            "occurrences": [row.as_dict() for row in occurrences],
        }
        manifest["manifest_sha256"] = sha256_bytes(_stable_json_bytes(manifest))
        return manifest, issues

    @staticmethod
    def _sha256_identity(value: Any) -> str:
        text = str(value or "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", text):
            return text
        return sha256_bytes(str(value or "").encode("utf-8"))

    @staticmethod
    def _session_fingerprint(namespace: str, request_session_id: str | None) -> str | None:
        if not request_session_id:
            return None
        # Namespace binding prevents the same transport identifier from being
        # comparable across OAuth subjects without exposing either value.
        return sha256_bytes(f"{namespace}\x00{request_session_id}".encode("utf-8"))

    @staticmethod
    def _work_order_role(operation: str, mode: str) -> str:
        if operation == "storyboard_audit" and mode == "audit_only":
            return "independent_audit"
        if operation == "storyboard_repair":
            return "repair"
        return "production"

    @staticmethod
    def _assert_storyboard_authority(work_order: dict[str, Any], expected_role: str) -> None:
        matrices = {
            "production": {
                "operations": {"storyboard_create", "storyboard_regenerate", "storyboard_change"},
                "mode": "execute",
                "authorization": "explicit_execute",
            },
            "repair": {
                "operations": {"storyboard_repair"},
                "mode": "audit_and_repair",
                "authorization": "explicit_execute",
            },
            "independent_audit": {
                "operations": {"storyboard_audit"},
                "mode": "audit_only",
                "authorization": "read_only",
            },
        }
        expected = matrices[expected_role]
        actual_role = str(work_order.get("server_actor_role") or "")
        if (
            actual_role != expected_role
            or work_order.get("operation") not in expected["operations"]
            or work_order.get("mode") != expected["mode"]
            or work_order.get("authorization") != expected["authorization"]
        ):
            raise ValueError(
                "window_role_matrix_mismatch: operation, mode, authorization, and server role do not match"
            )

    @staticmethod
    def _assert_delta_scope_binding(
        scope_target: str,
        affected_rows: list[dict[str, Any]],
        requested_uids: list[str],
    ) -> None:
        """Prove that the selected rows belong to the machine-readable target.

        Merely finding the target text in a work order is not enough: without
        this check an EP06 order could submit the stable UIDs of EP20.  Legacy
        workbooks without episode IDs may still use an exact shot_uid target.
        Ambiguous natural-language targets must take the full commit route.
        """

        compact = re.sub(r"\s+", "", str(scope_target or "")).casefold()
        episode_match = re.fullmatch(
            r"(?:episode:)?ep?(\d+)(?:[-â€”â€“~è‡³](?:ep)?(\d+))?", compact
        ) or re.fullmatch(r"ç¬¬?(\d+)(?:[-â€”â€“~è‡³](\d+))?é›†", compact)
        if episode_match:
            lower = int(episode_match.group(1))
            upper = int(episode_match.group(2) or lower)
            lower, upper = min(lower, upper), max(lower, upper)
            row_episodes: list[int] = []
            for row in affected_rows:
                value = re.sub(r"\s+", "", str(row.get("episode_id") or "")).casefold()
                match = re.fullmatch(r"ep?(\d+)", value)
                if not match:
                    raise ValueError(
                        "delta_scope_binding_missing: episode target requires episode_id on every affected shot; "
                        "use an exact shot_uids target for a legacy artifact or the full commit route"
                    )
                row_episodes.append(int(match.group(1)))
            if not row_episodes or any(value < lower or value > upper for value in row_episodes):
                raise ValueError(
                    "delta_scope_binding_mismatch: affected shot_uids are outside the authorized episode target"
                )
            return

        for prefix in ("shot_uid:", "shot_uids:"):
            if compact.startswith(prefix):
                declared = [
                    item for item in re.split(r"[,ï¼Œã€]", compact[len(prefix) :]) if item
                ]
                if declared != [value.casefold() for value in requested_uids]:
                    raise ValueError(
                        "delta_scope_binding_mismatch: exact shot_uid target does not match the affected ordered range"
                    )
                return

        if compact.startswith("scene:"):
            declared = compact[len("scene:") :]
            if not declared:
                raise ValueError("delta_scope_unparseable: scene target is empty")
            for row in affected_rows:
                candidates = {
                    re.sub(r"\s+", "", str(row.get("scene_uid") or "")).casefold(),
                    re.sub(r"\s+", "", str(row.get("åœºæ™¯è¿žç»­æ€§ID") or "")).casefold(),
                }
                candidates.discard("")
                if declared not in candidates:
                    raise ValueError(
                        "delta_scope_binding_mismatch: affected shot_uids are outside the authorized scene target"
                    )
            return

        if len(affected_rows) == 1:
            single_candidates = {
                re.sub(r"\s+", "", str(affected_rows[0].get("é•œå¤´ç¼–å·") or "")).casefold(),
                requested_uids[0].casefold(),
            }
            if compact in single_candidates:
                return
        raise ValueError(
            "delta_scope_unparseable: use EPxx/EPxx-EPyy, scene:<id>, or shot_uids:<ordered uids>"
        )

    @staticmethod
    def _authority_root_id(project_id: str, work_order_id: str) -> str:
        return "AUTH-" + sha256_bytes(
            _stable_json_bytes(
                {
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "contract": "window-role-authority-root-v1",
                }
            )
        )[:24]

    @staticmethod
    def _work_order_authority_contract(work_order: dict[str, Any]) -> dict[str, Any]:
        contract = {
            "work_order_id": str(work_order.get("work_order_id") or ""),
            "operation": str(work_order.get("operation") or ""),
            "mode": str(work_order.get("mode") or ""),
            "authorization": str(work_order.get("authorization") or ""),
            "server_actor_role": str(work_order.get("server_actor_role") or ""),
            "targets": copy.deepcopy(work_order.get("targets") or []),
            "source_lock": copy.deepcopy(work_order.get("source_lock") or {}),
            "protected_scope": copy.deepcopy(
                work_order.get("protected_scope") or []
            ),
            "expected_outputs": copy.deepcopy(
                work_order.get("expected_outputs") or []
            ),
        }
        if "window_access_contract" in work_order:
            contract["window_access_contract"] = str(
                work_order.get("window_access_contract") or ""
            )
            contract["window_access_scope"] = str(
                work_order.get("window_access_scope") or ""
            )
        return contract

    def _project_and_hub_for_work_order(
        self, namespace: str, work_order_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        matches: list[tuple[str, dict[str, Any]]] = []
        for project in self.store.list_projects(namespace):
            project_id = str(project.get("project_id") or "")
            if not project_id:
                continue
            hub = self.store.load_hub(namespace, project_id)
            if any(
                isinstance(row, dict)
                and row.get("work_order_id") == work_order_id
                for row in hub.get("work_orders", [])
            ):
                matches.append((project_id, hub))
        if len(matches) > 1:
            raise ValueError(
                "authority_root_ambiguous: work order appears in more than one project"
            )
        return matches[0] if matches else None

    def _active_authority_binding(
        self,
        namespace: str,
        project_id: str,
        work_order: dict[str, Any],
        *,
        hub: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve one signed active physical binding for a stable work order.

        Generation zero is the immutable binding minted with the original work
        order.  Later generations live in append-only signed continuation
        records; Hub contains only the active-head projection.
        """

        work_order_id = str(work_order.get("work_order_id") or "")
        authority_root_id = self._authority_root_id(project_id, work_order_id)
        authority_contract = self._work_order_authority_contract(work_order)
        contract_sha256 = sha256_bytes(_stable_json_bytes(authority_contract))
        current_hub = hub or self.store.load_hub(namespace, project_id)
        raw_heads = current_hub.get("active_authority_bindings")
        if raw_heads is None:
            raw_heads = []
        if not isinstance(raw_heads, list):
            raise ValueError(
                "authority_head_invalid: active authority heads must be an array"
            )
        matching_heads = [
            row
            for row in raw_heads
            if isinstance(row, dict)
            and row.get("authority_root_id") == authority_root_id
        ]
        if len(matching_heads) > 1:
            raise ValueError(
                "authority_head_invalid: authority root has multiple active heads"
            )
        if not matching_heads:
            signed_generations = [
                row
                for row in self.store.list_project_records_strict(
                    namespace,
                    project_id,
                    kind="authority_continuations",
                )
                if isinstance(row, dict)
                and row.get("authority_root_id") == authority_root_id
                and self.store.project_record_is_signed(row)
                and row.get("status") == "active"
            ]
            if signed_generations:
                raise ValueError(
                    "authority_head_incomplete: signed continuation exists without its active Hub head"
                )
            return {
                "authority_root_id": authority_root_id,
                "continuation_id": None,
                "authority_epoch_id": f"EPOCH-{authority_root_id[5:]}-0",
                "work_order_id": work_order_id,
                "generation": 0,
                "window_binding_id": work_order.get("server_window_binding_id"),
                "actor_binding_id": work_order.get("server_actor_binding_id"),
                "session_fingerprint": work_order.get(
                    "transport_session_fingerprint"
                ),
                "role": work_order.get("server_actor_role"),
                "operation": work_order.get("operation"),
                "mode": work_order.get("mode"),
                "authorization": work_order.get("authorization"),
                "authority_contract_sha256": contract_sha256,
                "plan_sha256": None,
                "status": "active",
            }
        head = matching_heads[0]
        if not isinstance(head, dict):
            raise ValueError("authority_head_invalid: active head is not an object")
        continuation_id = str(head.get("continuation_id") or "")
        try:
            record = self.store.get_project_record(
                namespace,
                project_id,
                kind="authority_continuations",
                record_id=continuation_id,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError(
                "authority_head_invalid: signed continuation record is unavailable"
            ) from exc
        if (
            not self.store.project_record_is_signed(record)
            or record.get("status") != "active"
            or record.get("authority_root_id") != authority_root_id
            or record.get("work_order_id") != work_order_id
            or record.get("authority_contract_sha256") != contract_sha256
            or record.get("active_binding") != head
        ):
            raise ValueError(
                "authority_head_invalid: continuation signature or contract differs"
            )
        records = self.store.list_project_records_strict(
            namespace, project_id, kind="authority_continuations"
        )
        generations = [
            int(row.get("generation") or -1)
            for row in records
            if isinstance(row, dict)
            and row.get("authority_root_id") == authority_root_id
            and self.store.project_record_is_signed(row)
            and row.get("status") == "active"
        ]
        if generations and int(head.get("generation") or -1) != max(generations):
            raise ValueError(
                "authority_head_rollback: Hub does not reference the newest signed generation"
            )
        return copy.deepcopy(head)

    def _verified_authority_binding_at_generation(
        self,
        namespace: str,
        project_id: str,
        work_order: dict[str, Any],
        generation: int,
        *,
        hub: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve one historical physical binding from the signed chain.

        Certificates and re-audit routes outlive the window that issued them.
        Their physical binding must therefore be checked against the exact
        signed continuation generation, rather than either the immutable
        generation-zero work order or the current active head alone.
        """

        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise ValueError("audit_authority_generation_invalid")
        current_hub = hub or self.store.load_hub(namespace, project_id)
        work_order_id = str(work_order.get("work_order_id") or "")
        canonical_work_order = _find_work_order(current_hub, work_order_id)
        authority_root_id = self._authority_root_id(project_id, work_order_id)
        contract_sha256 = sha256_bytes(
            _stable_json_bytes(
                self._work_order_authority_contract(canonical_work_order)
            )
        )
        current = self._active_authority_binding(
            namespace,
            project_id,
            canonical_work_order,
            hub=current_hub,
        )
        current_generation = current.get("generation")
        if (
            isinstance(current_generation, bool)
            or not isinstance(current_generation, int)
            or generation > current_generation
        ):
            raise ValueError("audit_authority_generation_invalid")

        generation_zero = {
            "continuation_id": None,
            "authority_epoch_id": f"EPOCH-{authority_root_id[5:]}-0",
            "authority_root_id": authority_root_id,
            "work_order_id": work_order_id,
            "generation": 0,
            "window_binding_id": canonical_work_order.get(
                "server_window_binding_id"
            ),
            "actor_binding_id": canonical_work_order.get(
                "server_actor_binding_id"
            ),
            "session_fingerprint": canonical_work_order.get(
                "transport_session_fingerprint"
            ),
            "role": canonical_work_order.get("server_actor_role"),
            "operation": canonical_work_order.get("operation"),
            "mode": canonical_work_order.get("mode"),
            "authorization": canonical_work_order.get("authorization"),
            "authority_contract_sha256": contract_sha256,
            "plan_sha256": None,
            "status": "active",
        }
        records = [
            row
            for row in self.store.list_project_records_strict(
                namespace,
                project_id,
                kind="authority_continuations",
            )
            if row.get("authority_root_id") == authority_root_id
        ]
        by_generation: dict[int, dict[str, Any]] = {}
        previous_continuation_id: str | None = None
        expected_binding_fields = set(generation_zero)
        authority_epoch_ids: set[str] = set()
        plan_sha256s: set[str] = set()
        for expected_generation in range(1, current_generation + 1):
            matches = [
                row
                for row in records
                if row.get("generation") == expected_generation
            ]
            if len(matches) != 1:
                raise ValueError(
                    "audit_authority_lineage_invalid: generation gap or duplicate"
                )
            record = matches[0]
            binding = record.get("active_binding")
            if (
                not self.store.project_record_is_signed(record)
                or not isinstance(binding, dict)
                or set(binding) != expected_binding_fields
                or record.get("project_id") != project_id
                or record.get("work_order_id") != work_order_id
                or record.get("authority_root_id") != authority_root_id
                or record.get("previous_generation")
                != expected_generation - 1
                or record.get("previous_continuation_id")
                != previous_continuation_id
                or not isinstance(
                    record.get("work_order_snapshot_sha256"), str
                )
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(record.get("work_order_snapshot_sha256") or ""),
                )
                is None
                or record.get("expected_work_order_snapshot_sha256")
                != record.get("work_order_snapshot_sha256")
                or record.get("authority_contract_sha256")
                != contract_sha256
                or record.get("status") != "active"
                or isinstance(record.get("generation"), bool)
                or isinstance(binding.get("generation"), bool)
                or any(
                    record.get(field) != binding.get(field)
                    for field in expected_binding_fields
                )
                or binding.get("generation") != expected_generation
                or binding.get("work_order_id") != work_order_id
                or binding.get("authority_root_id") != authority_root_id
                or binding.get("role")
                != canonical_work_order.get("server_actor_role")
                or binding.get("operation")
                != canonical_work_order.get("operation")
                or binding.get("mode") != canonical_work_order.get("mode")
                or binding.get("authorization")
                != canonical_work_order.get("authorization")
                or binding.get("authority_contract_sha256")
                != contract_sha256
                or binding.get("continuation_id")
                != record.get("continuation_id")
                or binding.get("authority_epoch_id")
                != record.get("authority_epoch_id")
                or binding.get("plan_sha256")
                != record.get("plan_sha256")
                or binding.get("status") != "active"
            ):
                raise ValueError("audit_authority_lineage_invalid")
            epoch_id = str(binding.get("authority_epoch_id") or "")
            plan_sha256 = str(binding.get("plan_sha256") or "")
            if (
                not epoch_id
                or epoch_id in authority_epoch_ids
                or not plan_sha256
                or plan_sha256 in plan_sha256s
            ):
                raise ValueError(
                    "audit_authority_lineage_invalid: epoch or plan reused"
                )
            authority_epoch_ids.add(epoch_id)
            plan_sha256s.add(plan_sha256)
            previous_continuation_id = str(record["continuation_id"])
            by_generation[expected_generation] = copy.deepcopy(binding)
        if len(records) != current_generation:
            raise ValueError(
                "audit_authority_lineage_invalid: unexpected generation record"
            )
        expected_current = (
            generation_zero
            if current_generation == 0
            else by_generation[current_generation]
        )
        if current != expected_current:
            raise ValueError(
                "audit_authority_lineage_invalid: active head differs"
            )
        return copy.deepcopy(
            generation_zero if generation == 0 else by_generation[generation]
        )

    def _verified_audit_certificate_authority(
        self,
        namespace: str,
        project_id: str,
        *,
        certificate: dict[str, Any],
        audit_work_order: dict[str, Any],
        hub: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Verify a certificate's issuance snapshot against signed lineage."""

        authority_fields = {
            "audit_authority_root_id": "authority_root_id",
            "audit_authority_epoch_id": "authority_epoch_id",
            "audit_authority_generation": "generation",
            "audit_authority_continuation_id": "continuation_id",
            "audit_window_binding_id": "window_binding_id",
            "audit_actor_binding_id": "actor_binding_id",
            "audit_session_fingerprint": "session_fingerprint",
        }
        lineage_fields = tuple(authority_fields)[:4]
        present_lineage_fields = [
            field for field in lineage_fields if field in certificate
        ]
        if present_lineage_fields and len(present_lineage_fields) != len(
            lineage_fields
        ):
            raise ValueError(
                "audit_certificate_authority_invalid: partial authority snapshot"
            )
        if present_lineage_fields:
            generation = certificate.get("audit_authority_generation")
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 0
            ):
                raise ValueError(
                    "audit_certificate_authority_invalid: generation"
                )
        else:
            # Compatibility is deliberately limited to the complete legacy
            # shape, which could only represent the immutable generation-zero
            # work-order binding.  Partial/newer unsigned claims fail closed.
            generation = 0
            if "audit_window_binding_id" in certificate:
                raise ValueError(
                    "audit_certificate_authority_invalid: partial authority snapshot"
                )
        binding = self._verified_authority_binding_at_generation(
            namespace,
            project_id,
            audit_work_order,
            generation,
            hub=hub,
        )
        fields_to_compare = (
            authority_fields
            if present_lineage_fields
            else {
                "audit_actor_binding_id": "actor_binding_id",
                "audit_session_fingerprint": "session_fingerprint",
            }
        )
        mismatched = [
            certificate_field
            for certificate_field, binding_field in fields_to_compare.items()
            if certificate.get(certificate_field)
            != binding.get(binding_field)
        ]
        if mismatched:
            raise ValueError(
                "audit_certificate_authority_invalid: "
                + ",".join(mismatched)
            )
        return binding

    def _verified_reaudit_route_authority(
        self,
        namespace: str,
        project_id: str,
        *,
        route: dict[str, Any],
        audit_work_order: dict[str, Any],
        hub: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Verify the audit authority that minted one signed re-audit route."""

        authority_fields = {
            "reaudit_audit_authority_root_id": "authority_root_id",
            "reaudit_audit_authority_epoch_id": "authority_epoch_id",
            "reaudit_audit_authority_generation": "generation",
            "reaudit_audit_authority_continuation_id": "continuation_id",
            "reaudit_audit_window_binding_id": "window_binding_id",
            "reaudit_audit_actor_binding_id": "actor_binding_id",
            "reaudit_audit_session_fingerprint": "session_fingerprint",
        }
        lineage_fields = tuple(authority_fields)[:4]
        present = [field for field in lineage_fields if field in route]
        if present and len(present) != len(lineage_fields):
            raise ValueError(
                "reaudit_lineage_invalid: partial route authority snapshot"
            )
        if present:
            generation = route.get("reaudit_audit_authority_generation")
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 0
            ):
                raise ValueError(
                    "reaudit_lineage_invalid: route authority generation"
                )
        else:
            generation = 0
        binding = self._verified_authority_binding_at_generation(
            namespace,
            project_id,
            audit_work_order,
            generation,
            hub=hub,
        )
        compare_fields = (
            authority_fields
            if present
            else {
                "reaudit_audit_window_binding_id": "window_binding_id",
                "reaudit_audit_actor_binding_id": "actor_binding_id",
                "reaudit_audit_session_fingerprint": "session_fingerprint",
            }
        )
        if any(
            route.get(route_field) != binding.get(binding_field)
            for route_field, binding_field in compare_fields.items()
        ):
            raise ValueError(
                "reaudit_lineage_invalid: route authority differs from signed generation"
            )
        return binding

    def _assert_work_order_session(
        self,
        namespace: str,
        work_order: dict[str, Any],
        request_session_id: str | None,
    ) -> dict[str, Any]:
        located = self._project_and_hub_for_work_order(
            namespace, str(work_order.get("work_order_id") or "")
        )
        if located is None:
            binding = {
                "generation": 0,
                "session_fingerprint": work_order.get(
                    "transport_session_fingerprint"
                ),
            }
        else:
            project_id, hub = located
            canonical_work_order = _find_work_order(
                hub, str(work_order.get("work_order_id") or "")
            )
            binding = self._active_authority_binding(
                namespace, project_id, canonical_work_order, hub=hub
            )
        expected = binding.get("session_fingerprint")
        actual = self._session_fingerprint(namespace, request_session_id)
        if work_order.get("window_access_contract") == WINDOW_NEUTRAL_ACCESS_CONTRACT:
            # The authenticated namespace is the user-facing access boundary.
            # A ChatGPT window is request provenance, not a role principal:
            # any trusted window for the same OAuth subject may operate the
            # work order.  The server-minted authority root/window/actor
            # binding remains the execution identity used by claims,
            # certificates, handoffs and audit separation.
            if self.settings.deployment_mode == "cloud" and not actual:
                raise ValueError(
                    "authenticated_window_required: cloud execution requires "
                    "a server-observed MCP session"
                )
            if self.settings.deployment_mode == "cloud" and not expected:
                raise ValueError(
                    "execution_identity_invalid: window-neutral work order is "
                    "missing its server-minted authority binding"
                )
            return binding
        # Local/direct service callers have no trusted host session envelope.
        # Keep that development/test path compatible while still comparing a
        # session whenever one is supplied.  Cloud authority always requires
        # both sides of the server-minted binding below.
        if (
            self.settings.deployment_mode != "cloud"
            and actual is None
            and int(binding.get("generation") or 0) == 0
        ):
            return binding
        if self.settings.deployment_mode == "cloud" and (not expected or not actual):
            raise ValueError(
                "window_session_required: cloud production, audit, and repair authorities require a server-observed MCP session"
            )
        if expected and expected != actual:
            raise ValueError(
                "window_session_mismatch: tool call is not from the server-bound work-order session"
            )
        return binding

    @staticmethod
    def _assert_work_order_write_ready(work_order: dict[str, Any]) -> None:
        """Reject non-executable states before any artifact/source persistence."""

        StoryboardService._ensure_can_execute(work_order)
        status = str(work_order.get("status") or "")
        if status not in WRITE_READY_WORK_ORDER_STATUSES:
            raise ValueError(
                f"work_order_write_forbidden: work order cannot persist from {status or 'unknown'}"
            )

    def _assert_source_import_authority(
        self,
        namespace: str,
        work_order: dict[str, Any],
        request_session_id: str | None,
    ) -> None:
        """Require an explicit production intake authority for source writes."""

        if (
            work_order.get("server_actor_role") != "production"
            or work_order.get("operation") not in SOURCE_INTAKE_OPERATIONS
            or work_order.get("mode") != "execute"
            or work_order.get("authorization") != "explicit_execute"
        ):
            raise ValueError(
                "source_import_authority_required: use a production project_intake, "
                "source_lock, or chapter_intake work order with execute/explicit_execute"
            )
        self._assert_work_order_session(namespace, work_order, request_session_id)
        self._assert_work_order_write_ready(work_order)

    def _assert_generic_artifact_authority(
        self,
        namespace: str,
        work_order: dict[str, Any],
        artifact_type: str,
        request_session_id: str | None,
    ) -> None:
        """Bind generic output writes to one production work-order capability."""

        operation = str(work_order.get("operation") or "")
        expected_operations = ARTIFACT_OPERATION_BINDINGS.get(artifact_type, set())
        if (
            work_order.get("server_actor_role") != "production"
            or operation not in expected_operations
            or operation in STORYBOARD_OPERATIONS | {"storyboard_audit"}
        ):
            raise ValueError(
                "artifact_write_authority_mismatch: artifact type is not bound to this "
                "production work-order operation"
            )
        if artifact_type in PLAN_ARTIFACT_TYPES:
            authority_ok = (
                work_order.get("mode") == "plan_only"
                and work_order.get("authorization") == "plan_only"
            )
        else:
            authority_ok = (
                work_order.get("mode") == "execute"
                and work_order.get("authorization") == "explicit_execute"
            )
        if not authority_ok:
            raise ValueError(
                "artifact_write_authority_mismatch: read_only, audit, and repair "
                "authorities cannot persist generic project artifacts"
            )
        self._assert_work_order_session(namespace, work_order, request_session_id)
        self._assert_work_order_write_ready(work_order)

    def _assert_capability_gap_authority(
        self,
        namespace: str,
        work_order: dict[str, Any],
        request_session_id: str | None,
    ) -> None:
        """Authorize the additive proposal record without granting rule writes."""

        if (
            work_order.get("server_actor_role") != "production"
            or work_order.get("operation") != "capability_gap"
            or work_order.get("mode") != "plan_only"
            or work_order.get("authorization") != "plan_only"
        ):
            raise ValueError(
                "capability_gap_authority_required: use a capability_gap "
                "plan_only/plan_only production work order"
            )
        self._assert_work_order_session(namespace, work_order, request_session_id)
        self._assert_work_order_write_ready(work_order)

    def _fingerprint_bundle(
        self,
        storyboard: dict[str, Any],
        artifact_record: dict[str, Any],
        *,
        canonical_hash: str,
        source_fingerprint: str,
        boundary_hash: str,
    ) -> dict[str, str]:
        source_sha = self._sha256_identity(source_fingerprint)
        anchors = {
            key: storyboard.get(key)
            for key in (
                "character_anchors",
                "scene_anchors",
                "prop_anchors",
                "costume_anchors",
                "project_anchors",
            )
            if key in storyboard
        }
        raw = self._sha256_identity(artifact_record.get("fingerprint"))
        return {
            "source_bytes": source_sha,
            "normalized_source": source_sha,
            "episode_boundary": self._sha256_identity(boundary_hash),
            "artifact_raw": raw,
            "artifact_semantic": self._sha256_identity(canonical_hash),
            "canonical": self._sha256_identity(canonical_hash),
            "parser": self._parser_fingerprint,
            "schema": self._schema_fingerprint,
            "runtime": self._runtime_fingerprint,
            "rules": self._ruleset_fingerprint,
            "validator": self._validator_fingerprint,
            "scripts": self._scripts_fingerprint,
            "dependencies_environment": self._dependencies_fingerprint,
            "project_anchors": sha256_bytes(_stable_json_bytes(anchors)),
            "target_model_contract": self._target_model_fingerprint,
        }

    @staticmethod
    def _normalized_semantic_audit_submission(
        semantic_audit: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the canonical identity of one semantic submission.

        SHA-256 hexadecimal is case-insensitive at the artifact boundary, so
        normalize the canonical and ordered-pair machine identities before
        hashing. Human evidence, issue identifiers and free text remain
        byte-for-byte unchanged.
        """

        submission = SemanticAuditInput.model_validate(
            semantic_audit
        ).model_dump(mode="python")
        submission["canonical_sha256"] = str(
            submission["canonical_sha256"]
        ).lower()
        submission["adjacent_pair_order_sha256"] = str(
            submission["adjacent_pair_order_sha256"]
        ).lower()
        return submission

    def _attach_independent_evidence_matrix(
        self,
        report: dict[str, Any],
        storyboard: dict[str, Any],
        semantic_audit: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind an independent semantic submission to the machine report.

        The machine validator may add findings, but it can never manufacture a
        clean eight-layer semantic review.  An uncached caller must supply the
        complete review and concrete evidence references for this canonical.
        """

        submission = self._normalized_semantic_audit_submission(
            semantic_audit
        )
        if str(storyboard.get("schema_version") or "") != COMPACT_SCHEMA_VERSION:
            raise ValueError(
                "legacy_storyboard_requires_compact_migration: legacy raw artifacts and certificates "
                "remain readable, but a new independent audit requires an R10.1 production successor"
            )
        canonical_hash = canonical_storyboard_sha256(storyboard)
        if str(submission["canonical_sha256"]).casefold() != canonical_hash.casefold():
            raise ValueError(
                "semantic_audit_fingerprint_mismatch: canonical_sha256 does not match the audited artifact"
            )
        shots = [row for row in storyboard.get("shots", []) if isinstance(row, dict)]
        batches = [row for row in storyboard.get("batches", []) if isinstance(row, dict)]
        if batches:
            raise ValueError(
                "semantic_audit_batch_removed: R10.1 audits the formal-master single-shot contract and forbids batches"
            )
        if int(submission["reviewed_shot_count"]) != len(shots):
            raise ValueError(
                "semantic_audit_count_mismatch: reviewed_shot_count does not match the canonical"
            )
        if int(submission["reviewed_batch_count"]) != len(batches):
            raise ValueError(
                "semantic_audit_count_mismatch: reviewed_batch_count does not match the canonical"
            )
        ordered_shot_refs = [
            str(row.get("é•œå¤´ç¼–å·") or row.get("shot_uid") or "").strip()
            for row in shots
        ]
        if any(not value for value in ordered_shot_refs):
            raise ValueError(
                "semantic_audit_reference_invalid: every canonical shot requires an identity"
            )
        expected_pair_ids = [
            f"{ordered_shot_refs[index]}->{ordered_shot_refs[index + 1]}"
            for index in range(max(len(ordered_shot_refs) - 1, 0))
        ]
        expected_pair_set = set(expected_pair_ids)
        expected_pair_hash = sha256_bytes(
            _stable_json_bytes(expected_pair_ids)
        )
        if int(submission["reviewed_adjacent_pair_count"]) != len(
            expected_pair_ids
        ):
            raise ValueError(
                "semantic_audit_adjacent_pair_count_mismatch: reviewed_adjacent_pair_count must equal N-1"
            )
        if str(submission["adjacent_pair_order_sha256"]).casefold() != expected_pair_hash.casefold():
            raise ValueError(
                "semantic_audit_adjacent_pair_hash_mismatch: adjacent_pair_order_sha256 does not match canonical order"
            )

        layer_rows = submission["layers"]
        layer_by_name = {str(row["layer"]): row for row in layer_rows}
        if len(layer_by_name) != len(AUDIT_LAYER_NAMES) or set(layer_by_name) != set(
            AUDIT_LAYER_NAMES
        ):
            raise ValueError(
                "semantic_audit_layers_invalid: exactly eight unique R10 layers are required"
            )

        shot_by_ref: dict[str, dict[str, Any]] = {}
        for row in shots:
            for value in (row.get("é•œå¤´ç¼–å·"), row.get("shot_uid")):
                reference = str(value or "").strip()
                if reference:
                    shot_by_ref[reference] = row
        shot_refs = set(shot_by_ref)

        def validate_evidence_id(evidence_id: str) -> None:
            kind, value = evidence_id.split(":", 1)
            kind = kind.strip().casefold()
            value = value.strip()
            if not kind or not value:
                raise ValueError(
                    "semantic_audit_evidence_invalid: evidence references must use non-empty kind:id"
                )
            if kind not in {"shot", "field", "canonical", "pair", "source-unit"}:
                raise ValueError(
                    "semantic_audit_evidence_invalid: evidence kind must be one of "
                    "shot, field, canonical, pair, or source-unit"
                )
            if kind == "shot" and value not in shot_refs:
                raise ValueError(
                    f"semantic_audit_evidence_invalid: unknown shot evidence {evidence_id!r}"
                )
            if kind == "field":
                shot_id, separator, field_name = value.partition(".")
                shot = shot_by_ref.get(shot_id)
                if not separator or not field_name or shot is None or field_name not in shot:
                    raise ValueError(
                        f"semantic_audit_evidence_invalid: field evidence must bind an existing shot field: {evidence_id!r}"
                    )
            if kind == "canonical" and value.casefold() != canonical_hash.casefold():
                raise ValueError(
                    f"semantic_audit_evidence_invalid: canonical evidence does not match the audited artifact: {evidence_id!r}"
                )
            if kind == "pair" and value not in expected_pair_set:
                raise ValueError(
                    f"semantic_audit_evidence_invalid: pair evidence is not one canonical adjacent pair: {evidence_id!r}"
                )

        for row in layer_rows:
            for evidence_id in row["evidence_ids"]:
                validate_evidence_id(str(evidence_id))

        dialogue_shot_refs = [
            str(row.get("é•œå¤´ç¼–å·") or row.get("shot_uid") or "").strip()
            for row in shots
            if re.search(
                r"è¯´é“\s*[ï¼š:]?\s*[â€œ\"]",
                str(row.get("ç”»é¢å†…å®¹") or ""),
            )
        ]
        expected_voice_evidence_ids = [
            f"field:{shot_ref}.éŸ³è‰²" for shot_ref in dialogue_shot_refs
        ]
        submitted_voice_evidence_ids = [
            str(evidence_id)
            for evidence_id in layer_by_name["ç¦é¡¹ä¸Žå£°éŸ³"]["evidence_ids"]
            if str(evidence_id).startswith("field:")
            and str(evidence_id).endswith(".éŸ³è‰²")
        ]
        if submitted_voice_evidence_ids != expected_voice_evidence_ids:
            raise ValueError(
                "semantic_audit_voice_field_coverage_incomplete: ç¦é¡¹ä¸Žå£°éŸ³å±‚å¿…é¡»æŒ‰canonicalé¡ºåº"
                "é€é•œæäº¤å…¨éƒ¨å¸¦å¯¹ç™½é•œçš„field:é•œå¤´ç¼–å·.éŸ³è‰²è¯æ®ï¼›"
                f"expected={expected_voice_evidence_ids}, submitted={submitted_voice_evidence_ids}"
            )

        adaptation_audit = (
            storyboard.get("prevention_evidence", {}).get(
                "dialogue_adaptation_audit"
            )
            if isinstance(storyboard.get("prevention_evidence"), dict)
            else None
        )
        adapted_target_set: set[str] = set()
        adapted_dialogue_count = 0
        adaptation_entry_sha256 = ""
        expected_scene_classification_evidence_ids: list[str] = []
        conversation_scene_count = 0
        if isinstance(adaptation_audit, dict):
            adaptation_entry_sha256 = str(
                adaptation_audit.get("ordered_entry_sha256") or ""
            )
            entries = adaptation_audit.get("entries")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    source_unit_id = str(entry.get("source_unit_id") or "").strip()
                    if source_unit_id:
                        expected_scene_classification_evidence_ids.append(
                            f"source-unit:{source_unit_id}"
                        )
                    if entry.get("scene_semantics") in {
                        "å™è¿°æ€§ä¼šè¯åœºæ™¯", "æ··åˆä¼šè¯åœºæ™¯"
                    }:
                        conversation_scene_count += 1
                    if entry.get("treatment") != "åŽŸæ–‡äº‹å®žè½¬åŒ–å¯¹ç™½":
                        continue
                    adapted_dialogue_count += 1
                    targets = entry.get("target_shots")
                    if isinstance(targets, list):
                        adapted_target_set.update(
                            str(value).strip()
                            for value in targets
                            if str(value).strip()
                        )
        expected_adapted_dialogue_evidence_ids = [
            f"field:{shot_ref}.ç”»é¢å†…å®¹"
            for shot_ref in ordered_shot_refs
            if shot_ref in adapted_target_set
        ]
        submitted_adapted_dialogue_evidence_ids = [
            str(evidence_id)
            for evidence_id in layer_by_name["æºæ–‡å¯¹ç™½"]["evidence_ids"]
            if str(evidence_id) in set(expected_adapted_dialogue_evidence_ids)
        ]
        if (
            submitted_adapted_dialogue_evidence_ids
            != expected_adapted_dialogue_evidence_ids
        ):
            raise ValueError(
                "semantic_audit_adapted_dialogue_coverage_incomplete: æºæ–‡å¯¹ç™½å±‚å¿…é¡»æŒ‰canonicalé¡ºåº"
                "é€é•œæäº¤å…¨éƒ¨äº‹å®žè½¬åŒ–å¯¹ç™½ç›®æ ‡é•œçš„field:é•œå¤´ç¼–å·.ç”»é¢å†…å®¹è¯æ®ï¼›"
                f"expected={expected_adapted_dialogue_evidence_ids}, "
                f"submitted={submitted_adapted_dialogue_evidence_ids}"
            )

        submitted_scene_classification_evidence_ids = [
            str(evidence_id)
            for evidence_id in layer_by_name["æºæ–‡å¯¹ç™½"]["evidence_ids"]
            if str(evidence_id).startswith("source-unit:")
        ]
        if (
            submitted_scene_classification_evidence_ids
            != expected_scene_classification_evidence_ids
        ):
            raise ValueError(
                "semantic_audit_conversation_scene_coverage_incomplete: æºæ–‡å¯¹ç™½å±‚å¿…é¡»æŒ‰Source Ledgeré¡ºåº"
                "é€å•å…ƒæäº¤å…¨éƒ¨source-unit:å•å…ƒIDï¼Œç‹¬ç«‹å¤æ ¸å…¶ä¼šè¯/æ··åˆä¼šè¯/éžä¼šè¯åˆ¤å®šï¼›"
                f"expected={expected_scene_classification_evidence_ids}, "
                f"submitted={submitted_scene_classification_evidence_ids}"
            )

        spatial_pair_ids = [
            str(evidence_id).split(":", 1)[1]
            for evidence_id in layer_by_name["ç©ºé—´è¿žç»­"]["evidence_ids"]
            if str(evidence_id).casefold().startswith("pair:")
        ]
        if spatial_pair_ids != expected_pair_ids:
            missing = [value for value in expected_pair_ids if value not in spatial_pair_ids]
            extra = [value for value in spatial_pair_ids if value not in expected_pair_set]
            raise ValueError(
                "semantic_audit_adjacent_pair_coverage_incomplete: ç©ºé—´è¿žç»­å±‚å¿…é¡»æŒ‰canonicalé¡ºåºé€ç»„æäº¤å…¨éƒ¨N-1ä¸ªpairè¯æ®ï¼›"
                f"missing={missing}, extra={extra}, submitted_order={spatial_pair_ids}"
            )

        semantic_issues = submission["issues"]
        failed_layers = {
            str(row["layer"])
            for row in layer_rows
            if row.get("status") == "fail_current"
        }
        issue_layers = {str(row["layer"]) for row in semantic_issues}
        if failed_layers - issue_layers:
            raise ValueError(
                "semantic_audit_issue_required: every failed layer requires at least one semantic issue"
            )
        if issue_layers - failed_layers:
            raise ValueError(
                "semantic_audit_layer_status_mismatch: a semantic issue cannot belong to a passing layer"
            )
        conclusion = submission["conclusion"]
        if conclusion.get("status") == "pass" and (failed_layers or semantic_issues):
            raise ValueError(
                "semantic_audit_conclusion_mismatch: pass requires eight passing layers and zero semantic issues"
            )
        if conclusion.get("status") == "fail_current" and not semantic_issues:
            raise ValueError(
                "semantic_audit_issue_required: fail_current conclusion requires a semantic issue"
            )

        normalized_semantic_issues: list[dict[str, Any]] = []
        for issue in semantic_issues:
            submitted_shot_ids = list(issue.get("shot_ids", []))
            if not submitted_shot_ids:
                raise ValueError(
                    "semantic_audit_repair_target_required: every failing semantic issue "
                    "must authorize at least one audited shot"
                )
            unknown_shots = sorted(set(submitted_shot_ids) - shot_refs)
            if issue.get("batch_ids"):
                raise ValueError(
                    "semantic_audit_batch_removed: issue targets must bind formal-master shots/fields"
                )
            if unknown_shots:
                raise ValueError(
                    "semantic_audit_reference_invalid: unknown issue targets "
                    + ", ".join(unknown_shots)
                )
            canonical_shot_ids = [
                str(
                    shot_by_ref[reference].get("é•œå¤´ç¼–å·")
                    or shot_by_ref[reference].get("shot_uid")
                    or ""
                ).strip()
                for reference in submitted_shot_ids
            ]
            if any(not reference for reference in canonical_shot_ids):
                raise ValueError(
                    "semantic_audit_reference_invalid: issue target has no canonical shot identity"
                )
            if len(set(canonical_shot_ids)) != len(canonical_shot_ids):
                raise ValueError(
                    "semantic_audit_reference_invalid: aliases cannot repeat the same canonical shot"
                )
            layer_evidence = set(layer_by_name[str(issue["layer"])]["evidence_ids"])
            issue_evidence = {str(value) for value in issue.get("evidence_ids", [])}
            if not issue_evidence & layer_evidence:
                raise ValueError(
                    f"semantic_audit_evidence_mismatch: issue {issue['issue_id']} is not bound to its layer evidence"
                )
            for evidence_id in issue_evidence:
                validate_evidence_id(evidence_id)
            pair_issue_evidence = sorted(
                evidence_id
                for evidence_id in issue_evidence
                if evidence_id.casefold().startswith("pair:")
            )
            if pair_issue_evidence:
                if len(pair_issue_evidence) != 1:
                    raise ValueError(
                        "semantic_audit_adjacent_pair_issue_invalid: one continuity issue must bind exactly one adjacent pair"
                    )
                pair_value = pair_issue_evidence[0].split(":", 1)[1]
                pair_shot_ids = pair_value.split("->")
                if (
                    str(issue["layer"]) != "ç©ºé—´è¿žç»­"
                    or len(pair_shot_ids) != 2
                    or canonical_shot_ids != pair_shot_ids
                ):
                    raise ValueError(
                        "semantic_audit_adjacent_pair_issue_invalid: pair issue must target its two canonical shots in left-to-right order"
                    )
            normalized_semantic_issues.append(
                {
                    "error_code": str(issue["error_code"]),
                    "semantic_issue_id": str(issue["issue_id"]),
                    "semantic_layer": str(issue["layer"]),
                    "severity": str(issue.get("severity") or "fail"),
                    "blocking": bool(issue.get("blocking", True)),
                    "shot_ids": canonical_shot_ids,
                    "batch_ids": list(issue.get("batch_ids", [])),
                    "field": str(issue.get("field") or "è¯­ä¹‰å®¡è®¡"),
                    "location": f"semantic_audit.{issue['issue_id']}",
                    "evidence_ids": sorted(issue_evidence),
                    "evidence": str(issue["evidence"]),
                    "source_sentence": str(issue["evidence"]),
                    "repair_action": str(issue["repair_action"]),
                    "issue_source": "independent_semantic_audit",
                }
            )

        enriched = copy.deepcopy(report)
        machine_issues = [
            copy.deepcopy(item)
            for item in enriched.get("issues", [])
            if isinstance(item, dict)
        ]
        issues = machine_issues + normalized_semantic_issues
        enriched["issues"] = issues
        chapter_mode = isinstance(storyboard.get("chapter_intake"), dict)
        evidence_prefix = (
            f"machine:{self._validator_fingerprint[:12]}:"
            f"{canonical_hash[:12]}"
        )
        gate_matrix: dict[str, dict[str, Any]] = {}
        for gate in AUDIT_GATE_IDS:
            if gate in {"G1", "G2", "G3"} and not chapter_mode:
                gate_matrix[gate] = {
                    "status": "not_applicable",
                    "evidence_count": 0,
                    "evidence_ids": [],
                    "not_applicable_reason": "non_chapter storyboard has no chapter-intake gate",
                }
                continue
            relevant = [
                str(item.get("error_code") or "E-UNKNOWN")
                for item in issues
                if bool(
                    item.get(
                        "blocking",
                        item.get("severity") in {"fail", "error", "blocking"},
                    )
                )
            ]
            gate_matrix[gate] = {
                "status": "fail_current" if relevant else "pass",
                "evidence_count": len(relevant) or 1,
                "evidence_ids": (
                    [f"issue:{value}" for value in relevant]
                    if relevant
                    else [f"{evidence_prefix}:{gate}"]
                ),
            }

        layer_matrix: dict[str, dict[str, Any]] = {}
        for layer in AUDIT_LAYER_NAMES:
            submitted = layer_by_name[layer]
            machine_relevant = [
                str(item.get("error_code") or "E-UNKNOWN")
                for item in machine_issues
                if _issue_category(str(item.get("error_code") or "")) == layer
            ]
            evidence_ids = list(submitted["evidence_ids"])
            evidence_ids.extend(
                f"issue:{error_code}" for error_code in machine_relevant
            )
            evidence_ids = list(dict.fromkeys(evidence_ids))
            layer_matrix[layer] = {
                "status": (
                    "fail_current"
                    if submitted["status"] == "fail_current" or machine_relevant
                    else "pass"
                ),
                "summary": str(submitted["summary"])
                + (
                    f"ï½œæœºå™¨æ ¡éªŒæ–°å¢ž{len(machine_relevant)}é¡¹"
                    if machine_relevant
                    else ""
                ),
                "evidence_count": len(evidence_ids),
                "evidence_ids": evidence_ids,
            }
        machine_adjacent = (
            report.get("quality_metrics", {}).get("adjacent_continuity")
            if isinstance(report.get("quality_metrics"), dict)
            else None
        )
        if (
            not isinstance(machine_adjacent, dict)
            or int(machine_adjacent.get("expected_pair_count", -1))
            != len(expected_pair_ids)
            or list(machine_adjacent.get("pair_ids") or [])
            != expected_pair_ids
            or str(machine_adjacent.get("ordered_pair_sha256") or "")
            != expected_pair_hash
        ):
            raise ValueError(
                "semantic_audit_adjacent_pair_machine_proof_missing: current validator report lacks the exact N-1 pair manifest"
            )
        clean = (
            not issues
            and conclusion.get("status") == "pass"
            and all(
                row["status"] in {"pass", "not_applicable"}
                for row in gate_matrix.values()
            )
            and all(row["status"] == "pass" for row in layer_matrix.values())
        )
        submission_hash = sha256_bytes(_stable_json_bytes(submission))
        quality = _draft_quality_metrics(storyboard, issues)
        source_issue_count = sum(
            _issue_category(str(item.get("error_code") or "")) == "æºæ–‡å¯¹ç™½"
            for item in issues
        )
        enriched["semantic_audit"] = submission
        enriched["semantic_audit_sha256"] = submission_hash
        enriched["independent_evidence_matrix"] = {
            "preventive_gate_results": gate_matrix,
            "audit_layer_results": layer_matrix,
            "quality_thresholds": {
                "source_and_key_dialogue_mapping_percent": 100 if source_issue_count == 0 else 0,
                "blocking_error_count": sum(
                    bool(item.get("blocking", True)) for item in issues
                ),
                "old_content_hit_count": 0,
                "cross_context_term_count": 0,
                "shot_pass_percent": quality["first_draft_correctness_percent"],
                "unique_affected_shot_count": quality["unique_affected_shot_count"],
                "residual_issue_count": 0,
                "introduced_issue_count": 0,
                "adjacent_pair_coverage_percent": 100,
            },
            "result": "pass" if clean else "fail_current",
            "semantic_audit_contract": SEMANTIC_AUDIT_CONTRACT_VERSION,
            "semantic_audit_sha256": submission_hash,
            "reviewed_shot_count": int(submission["reviewed_shot_count"]),
            "reviewed_batch_count": int(submission["reviewed_batch_count"]),
            "reviewed_adjacent_pair_count": int(
                submission["reviewed_adjacent_pair_count"]
            ),
            "adjacent_pair_order_sha256": expected_pair_hash,
            "adjacent_continuity": {
                "contract": "adjacent-pair-continuity-v1",
                "expected_pair_count": len(expected_pair_ids),
                "reviewed_pair_count": len(spatial_pair_ids),
                "coverage_percent": 100.0,
                "ordered_pair_sha256": expected_pair_hash,
                "pair_ids": expected_pair_ids,
                "production_proof_verified": bool(
                    machine_adjacent.get("proof_verified")
                ),
                "production_failed_pair_count": int(
                    machine_adjacent.get("failed_pair_count", 0)
                ),
                "spatial_layer_status": layer_matrix["ç©ºé—´è¿žç»­"]["status"],
            },
            "voice_field_coverage": {
                "contract": "fixed-voice-template-v1",
                "expected_dialogue_shot_count": len(dialogue_shot_refs),
                "reviewed_voice_field_count": len(submitted_voice_evidence_ids),
                "coverage_percent": 100.0,
                "evidence_ids": expected_voice_evidence_ids,
                "audio_layer_status": layer_matrix["ç¦é¡¹ä¸Žå£°éŸ³"]["status"],
                "machine_voice_issue_count": sum(
                    str(item.get("error_code") or "") == "E-VOICE-001"
                    for item in machine_issues
                ),
            },
            "adapted_dialogue_coverage": {
                "contract": "source-fact-dialogue-v1",
                "expected_adapted_dialogue_count": adapted_dialogue_count,
                "expected_target_shot_count": len(
                    expected_adapted_dialogue_evidence_ids
                ),
                "reviewed_target_shot_count": len(
                    submitted_adapted_dialogue_evidence_ids
                ),
                "coverage_percent": 100.0,
                "ordered_entry_sha256": adaptation_entry_sha256,
                "evidence_ids": expected_adapted_dialogue_evidence_ids,
                "source_layer_status": layer_matrix["æºæ–‡å¯¹ç™½"]["status"],
                "machine_adaptation_issue_count": sum(
                    str(item.get("error_code") or "")
                    == "E-ADAPTED-DIALOGUE-001"
                    for item in machine_issues
                ),
            },
            "conversation_scene_classification_coverage": {
                "contract": "narrative-conversation-scene-v1",
                "expected_source_unit_count": len(
                    expected_scene_classification_evidence_ids
                ),
                "reviewed_source_unit_count": len(
                    submitted_scene_classification_evidence_ids
                ),
                "conversation_scene_count": conversation_scene_count,
                "coverage_percent": 100.0,
                "evidence_ids": expected_scene_classification_evidence_ids,
                "source_layer_status": layer_matrix["æºæ–‡å¯¹ç™½"]["status"],
            },
        }
        enriched["status"] = "pass" if clean else "fail_current"
        return enriched

    def _audit_cache_components(
        self,
        *,
        canonical_hash: str,
        source_fingerprint: str,
        boundary_hash: str,
        audit_class: str,
        artifact_fingerprint: str = "",
        storyboard: dict[str, Any] | None = None,
        scope: Any = None,
        runtime_test_certificate_id: str = "",
    ) -> dict[str, str]:
        anchors = {
            key: (storyboard or {}).get(key)
            for key in (
                "character_anchors",
                "scene_anchors",
                "prop_anchors",
                "costume_anchors",
                "project_anchors",
            )
            if key in (storyboard or {})
        }
        return {
            "canonical": canonical_hash,
            "source": source_fingerprint,
            "boundary": boundary_hash,
            "ruleset": self._ruleset_version,
            "validator": self._validator_version(),
            "audit_class": audit_class,
            "artifact": artifact_fingerprint,
            "parser": self._parser_fingerprint,
            "schema": self._schema_fingerprint,
            "runtime": self._runtime_fingerprint,
            "scripts": self._scripts_fingerprint,
            "dependencies": self._dependencies_fingerprint,
            "anchors": sha256_bytes(_stable_json_bytes(anchors)),
            "target_model": self._target_model_fingerprint,
            "scope": sha256_bytes(_stable_json_bytes(scope or {})),
            "runtime_test_certificate_id": runtime_test_certificate_id,
            "semantic_audit_contract": (
                SEMANTIC_AUDIT_CONTRACT_FINGERPRINT
                if audit_class == AUDIT_CLASS_INDEPENDENT
                else ""
            ),
        }

    @staticmethod
    def _cache_key_from_components(components: dict[str, str]) -> str:
        return build_audit_cache_key(
            canonical_sha256=components["canonical"],
            source_fingerprint=components["source"],
            boundary_hash=components["boundary"],
            ruleset_version=components["ruleset"],
            validator_version=components["validator"],
            audit_class=components["audit_class"],
            artifact_fingerprint=components.get("artifact", ""),
            parser_fingerprint=components.get("parser", ""),
            schema_fingerprint=components.get("schema", ""),
            runtime_fingerprint=components.get("runtime", ""),
            scripts_fingerprint=components.get("scripts", ""),
            dependencies_fingerprint=components.get("dependencies", ""),
            anchors_fingerprint=components.get("anchors", ""),
            target_model_fingerprint=components.get("target_model", ""),
            scope_fingerprint=components.get("scope", ""),
            runtime_test_certificate_id=components.get("runtime_test_certificate_id", ""),
            semantic_audit_contract_fingerprint=components.get(
                "semantic_audit_contract", ""
            ),
        )

    @staticmethod
    def _independent_audit_cache_scope(
        handoff: dict[str, Any], artifact_id: str
    ) -> dict[str, Any]:
        """Prevent a byte-identical sibling artifact reusing audit authority."""

        return {
            "artifact_id": str(artifact_id),
            "handoff_id": str(handoff.get("handoff_id") or ""),
            "audit_cycle_id": str(handoff.get("audit_cycle_id") or ""),
            "production_scope": copy.deepcopy(handoff.get("scope") or {}),
        }

    @classmethod
    def _valid_cache_identity(cls, record: dict[str, Any], audit_class: str) -> bool:
        components = record.get("cache_components")
        if not isinstance(components, dict) or components.get("audit_class") != audit_class:
            return False
        try:
            expected = cls._cache_key_from_components(components)
        except (KeyError, TypeError, ValueError):
            return False
        return record.get("cache_key") == expected

    @classmethod
    def _valid_historical_preflight_identity(
        cls, record: dict[str, Any]
    ) -> bool:
        """Validate the non-reusable historical-attestation cache domain."""

        components = record.get("cache_components")
        replay = record.get("production_validation_replay")
        replay_issue_count = (
            replay.get("current_validation_issue_count")
            if isinstance(replay, dict)
            else None
        )
        record_issue_count = record.get("current_validation_issue_count")
        preflight_status = record.get(
            "production_preflight_status",
            record.get("preflight_status"),
        )
        if isinstance(replay, dict):
            replay_without_proof = copy.deepcopy(replay)
            replay_proof = replay_without_proof.pop("proof_sha256", None)
        else:
            replay_without_proof = {}
            replay_proof = None
        if (
            not isinstance(components, dict)
            or components.get("audit_class")
            != AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT
            or not isinstance(replay, dict)
            or replay.get("historical_contract_status")
            != HISTORICAL_PRODUCTION_PREFLIGHT_PASS
            or replay.get("current_validation_status")
            != CURRENT_VALIDATION_FAIL
            or replay_proof
            != sha256_bytes(_stable_json_bytes(replay_without_proof))
            or not isinstance(replay_issue_count, int)
            or isinstance(replay_issue_count, bool)
            or replay_issue_count < 1
            or preflight_status != HISTORICAL_PRODUCTION_PREFLIGHT_PASS
            or record.get("current_validation_status")
            != CURRENT_VALIDATION_FAIL
            or not isinstance(record_issue_count, int)
            or isinstance(record_issue_count, bool)
            or record_issue_count < 1
            or record.get("current_production_pass_claimed") is not False
            or record.get("current_preflight_cache_reusable") is not False
            or record.get("conclusion_reusable_for_independent_audit")
            is not False
            or record.get("requires_full_current_independent_audit") is not True
            or record.get("independent_audit_status") != "PENDING_AUDIT"
        ):
            return False
        try:
            expected = cls._historical_preflight_cache_key(components)
        except (KeyError, TypeError, ValueError):
            return False
        return (
            record.get("cache_key") == expected
            and str(expected).startswith("HISTCACHE-")
        )

    def _save_telemetry(
        self,
        namespace: str,
        project_id: str,
        *,
        operation: str,
        started_ns: int,
        stages_ms: dict[str, float],
        cache_hit: bool = False,
        artifact_id: str | None = None,
        telemetry_id: str | None = None,
        window_role: str | None = None,
        task_type: str | None = None,
        input_fingerprints: dict[str, str] | None = None,
        shot_count: int = 0,
        batch_count: int = 0,
        affected_shot_count: int = 0,
        preserved_semantic_count: int = 0,
        source_character_count: int = 0,
        input_payload_bytes: int = 0,
        output_payload_bytes: int = 0,
        materialization_passes: int = 0,
        cache_reason: str | None = None,
        unmeasured_stages: list[str] | None = None,
        _persist: bool = True,
    ) -> dict[str, Any]:
        telemetry_id = telemetry_id or _new_id("TEL")
        rounded_stages = {key: round(float(value), 3) for key, value in stages_ms.items()}
        wall_duration_ms = max(
            0, int(round((time.perf_counter_ns() - started_ns) / 1_000_000))
        )
        stage_name_map = {
            "validate": "validation",
            "machine_validation": "validation",
            "serialize_and_persist": "export_projection",
            "canonical_load": "canonicalization",
            "base_load": "delta_base_load",
            "scope_verify": "delta_scope_check",
            "delta_merge": "delta_merge",
            "materialize": "canonicalization",
            "ledger": "ledger_handoff",
        }
        ended_dt = datetime.now(timezone.utc)
        started_dt = ended_dt - timedelta(milliseconds=wall_duration_ms)
        started_at = started_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        ended_at = ended_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        measured_source_chars = max(0, int(source_character_count))
        measured_input_bytes = max(0, int(input_payload_bytes))
        measured_output_bytes = max(0, int(output_payload_bytes))
        unavailable_metrics = [
            "chatgpt_model_generation",
            "stage_tool_call_count",
            "stage_retry_count",
        ]
        if measured_source_chars == 0:
            unavailable_metrics.append("source_character_count")
        if measured_input_bytes == 0:
            unavailable_metrics.append("input_payload_bytes")
        if measured_output_bytes == 0:
            unavailable_metrics.append("output_payload_bytes")
        unmeasured = list(dict.fromkeys(list(unmeasured_stages or []) + unavailable_metrics))
        schema_stages = []
        for index, (key, duration) in enumerate(rounded_stages.items()):
            stage_payload = 0
            if index == 0:
                stage_payload = max(0, int(input_payload_bytes))
            if stage_name_map.get(key, "validation") == "export_projection":
                stage_payload = max(stage_payload, int(output_payload_bytes))
            schema_stages.append(
                {
                "stage": stage_name_map.get(key, "validation"),
                "started_at": None,
                "ended_at": None,
                "duration_ms": max(0, int(round(duration))),
                "tool_call_count": None,
                "source_character_count": measured_source_chars if index == 0 and measured_source_chars else None,
                "payload_bytes": stage_payload if stage_payload else None,
                "shot_count": max(0, int(shot_count)),
                "batch_count": max(0, int(batch_count)),
                "cache_status": "hit" if cache_hit else "miss",
                "cache_reason": cache_reason or (
                    "exact fingerprint cache" if cache_hit else "fresh deterministic stage"
                ),
                "retry_count": None,
                "error_status": "none",
                }
            )
        value = {
            "telemetry_id": telemetry_id,
            "trace_id": telemetry_id,
            "operation": operation,
            "duration_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
            "stages_ms": rounded_stages,
            "stages": [
                {"stage": key, "duration_ms": duration, "status": "ok", "metrics": {}}
                for key, duration in rounded_stages.items()
            ],
            "cache_hit": bool(cache_hit),
            "artifact_id": artifact_id,
            "measurement_scope": "server_only",
            "unmeasured_stages": unmeasured,
            "input_payload_bytes": measured_input_bytes,
            "output_payload_bytes": measured_output_bytes,
            "affected_shot_count": max(0, int(affected_shot_count)),
            "preserved_semantic_count": max(0, int(preserved_semantic_count)),
            "schema_version": COMPACT_SCHEMA_VERSION,
            "schema_profile": COMPACT_SCHEMA_PROFILE,
            "materializer_contract": COMPACT_MATERIALIZER_CONTRACT,
            "materialization_passes": max(0, int(materialization_passes)),
            **NO_DUPLICATE_GENERATION_EVIDENCE,
        }
        if window_role and task_type:
            value.update(
                {
                    "window_role": window_role,
                    "task_type": task_type,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "wall_duration_ms": wall_duration_ms,
                    "input_fingerprints": input_fingerprints or {"artifact": sha256_bytes(b"none")},
                    "stages": schema_stages or [
                        {
                            "stage": "validation",
                            "started_at": None,
                            "ended_at": None,
                            "duration_ms": 0,
                            "tool_call_count": None,
                            "source_character_count": None,
                            "payload_bytes": None,
                            "shot_count": max(0, int(shot_count)),
                            "batch_count": max(0, int(batch_count)),
                            "cache_status": "not_applicable",
                            "cache_reason": "no timed child stage",
                            "retry_count": None,
                            "error_status": "none",
                        }
                    ],
                }
            )
            self._validate_schema_record(
                self._project_schema, "stage_timing_trace", value
            )
        if not _persist:
            return value
        return self.store.save_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=telemetry_id,
            value=value,
        )

    def _verify_authoritative_audit_overlay(
        self,
        namespace: str,
        project_id: str,
        *,
        overlay_record: dict[str, Any],
        source_record: dict[str, Any],
        certificate: dict[str, Any],
    ) -> None:
        """Bind every visible audit-page claim to the signed certificate.

        Artifact metadata is an index and its public SHA can be recomputed by a
        local writer.  It is therefore insufficient evidence for a PASS.  This
        verifier reparses the authoritative workbook (which also checks the
        hidden canonical sidecar and both visible projections), reconstructs
        the complete audit-page value matrix from the signed certificate, and
        requires the two to be identical before status may expose PASS.
        """

        overlay_id = str(overlay_record.get("artifact_id") or "")
        if not overlay_id:
            raise ValueError("authoritative_audit_overlay_identity_missing")
        current_overlay, overlay_path = self.store.get_artifact(
            namespace,
            project_id,
            overlay_id,
        )
        source_id = str(source_record.get("artifact_id") or "")
        if (
            current_overlay.get("artifact_type") != "storyboard_audit_overlay"
            or str(current_overlay.get("source_artifact_id") or "") != source_id
        ):
            raise ValueError("authoritative_audit_overlay_source_binding_invalid")

        overlay_metadata = current_overlay.get("metadata")
        source_metadata = source_record.get("metadata")
        if not isinstance(overlay_metadata, dict) or not isinstance(
            source_metadata, dict
        ):
            raise ValueError("authoritative_audit_overlay_metadata_invalid")
        report = certificate.get("report")
        certificate_summary = certificate.get("audit_summary")
        certificate_fingerprints = certificate.get("fingerprints")
        semantic_conclusion = certificate.get("semantic_conclusion")
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("issues"), list)
            or not isinstance(certificate_summary, dict)
            or not isinstance(certificate_fingerprints, dict)
            or not isinstance(semantic_conclusion, dict)
            or not str(semantic_conclusion.get("summary") or "").strip()
        ):
            raise ValueError("authoritative_audit_overlay_certificate_incomplete")

        overlay_status = str(certificate.get("result") or "fail_current").upper()
        if overlay_status not in {"PASS", "FAIL_CURRENT", "BLOCKED"}:
            raise ValueError("authoritative_audit_overlay_status_invalid")
        issue_count = int(certificate.get("issue_count") or 0)
        expected_counts = {
            "initial_issue_count": int(
                certificate_summary.get("initial_issue_count", issue_count)
            ),
            "residual_issue_count": int(
                certificate_summary.get("residual_issue_count", 0)
            ),
            "introduced_issue_count": int(
                certificate_summary.get("introduced_issue_count", 0)
            ),
            "blocking_issue_count": int(
                certificate_summary.get("blocking_issue_count", issue_count)
            ),
        }
        certificate_id = str(certificate.get("certificate_id") or "")
        certificate_hmac = str(certificate.get("integrity_hmac") or "")
        audit_trace_id = str(
            certificate.get("audit_trace_id")
            or certificate.get("stage_trace_id")
            or ""
        )
        canonical_hash = str(certificate.get("canonical_sha256") or "")
        source_raw_hash = str(certificate_fingerprints.get("artifact_raw") or "")
        expected_metadata = {
            "canonical_sha256": canonical_hash,
            "source_storyboard_artifact_id": source_id,
            "source_storyboard_raw_sha256": source_raw_hash,
            "independent_audit_status": overlay_status,
            "audit_status": overlay_status,
            "issue_count": issue_count,
            "independent_audit_certificate_id": certificate_id,
            "independent_audit_trace_id": audit_trace_id,
            "independent_audit_summary": certificate_summary,
            "signed_audit_overlay": True,
            "canonical_json_artifact_id": source_metadata.get(
                "canonical_json_artifact_id"
            ),
            "canonical_json_fingerprint": source_metadata.get(
                "canonical_json_fingerprint"
            ),
        }
        if any(
            overlay_metadata.get(key) != value
            for key, value in expected_metadata.items()
        ):
            raise ValueError("authoritative_audit_overlay_metadata_binding_invalid")
        source_metadata_canonical = str(
            source_metadata.get("canonical_sha256") or ""
        )
        if (
            str(source_record.get("fingerprint") or "") != source_raw_hash
            or (
                source_metadata_canonical
                and source_metadata_canonical != canonical_hash
            )
        ):
            raise ValueError("authoritative_audit_overlay_source_fingerprint_invalid")

        extracted = extract_file(
            overlay_path.read_bytes(),
            str(current_overlay.get("file_name") or "audit-overlay.xlsx"),
        )
        canonical = extracted.get("canonical_storyboard")
        sheets = extracted.get("sheets")
        if (
            extracted.get("kind") != "workbook"
            or extracted.get("canonical_sidecar_present") is not True
            or not isinstance(canonical, dict)
            or canonical_storyboard_sha256(canonical) != canonical_hash
            or not isinstance(sheets, list)
        ):
            raise ValueError("authoritative_audit_overlay_workbook_binding_invalid")
        scope_sheets = [
            sheet
            for sheet in sheets
            if isinstance(sheet, dict) and sheet.get("name") == "00_èŒƒå›´ä¸Žå®¡è®¡"
        ]
        if len(scope_sheets) != 1:
            raise ValueError("authoritative_audit_overlay_scope_sheet_invalid")

        def visible_cell(value: Any) -> Any:
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
                value = f"'{value}"
            if value == "":
                return None
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return value

        def row(*values: Any) -> list[Any]:
            padded = [*values, *([None] * max(0, 5 - len(values)))]
            return [visible_cell(value) for value in padded[:5]]

        scope_rows: list[tuple[str, Any]] = [
            ("independent_audit_status", overlay_status),
            (
                "independent_audit_conclusion",
                str(semantic_conclusion["summary"]),
            ),
            *[(key, expected_counts[key]) for key in expected_counts],
            ("audit_trace_id", audit_trace_id),
            ("certificate_id", certificate_id),
            ("certificate_integrity_hmac", certificate_hmac),
            ("canonical_sha256", canonical_hash),
            ("source_artifact_raw_sha256", source_raw_hash),
            ("project_version", canonical.get("project_version")),
            ("schema_version", canonical.get("schema_version")),
            ("workflow_profile", canonical.get("workflow_profile")),
            ("total_duration", canonical.get("total_duration")),
            (
                "production_evidence_location",
                "canonical sidecarï¼ˆç”Ÿäº§è¯æ®ä¸ä½œä¸ºç‹¬ç«‹å®¡è®¡ç»“è®ºï¼‰",
            ),
        ]
        expected_matrix: list[list[Any]] = [
            row(f"00_èŒƒå›´ä¸Žå®¡è®¡ï½œ{overlay_status}ï½œç‹¬ç«‹å®¡è®¡å·²ç­¾å‘"),
            row(),
            row("èŒƒå›´å­—æ®µ", "å€¼"),
            *[row(label, value) for label, value in scope_rows],
            row(),
            row("ç‹¬ç«‹å®¡è®¡é”™è¯¯ç ", "ä¸¥é‡åº¦", "ä½ç½®", "è¯æ®", "ä¿®å¤åŠ¨ä½œ"),
        ]
        report_issues = report["issues"]
        if report_issues:
            expected_matrix.extend(
                row(
                    issue.get("error_code"),
                    issue.get("severity"),
                    issue.get("location"),
                    issue.get("evidence"),
                    issue.get("repair_action"),
                )
                for issue in report_issues
                if isinstance(issue, dict)
            )
            if len(expected_matrix) != 21 + len(report_issues):
                raise ValueError("authoritative_audit_overlay_report_issue_invalid")
        else:
            sentinel = (
                "INDEPENDENT_AUDIT_PASS"
                if overlay_status == "PASS"
                else f"INDEPENDENT_AUDIT_{overlay_status}"
            )
            expected_matrix.append(
                row(
                    sentinel,
                    "info" if overlay_status == "PASS" else "fail",
                    "å…¨è¡¨",
                    (
                        "ç‹¬ç«‹å®¡è®¡è¯ä¹¦å·²ç­¾å‘ï¼›æ®‹ç•™0ã€æ–°å¢ž0ã€é˜»å¡ž0"
                        if overlay_status == "PASS"
                        else f"ç‹¬ç«‹å®¡è®¡è¯ä¹¦çŠ¶æ€ä¸º{overlay_status}"
                    ),
                    "æ— éœ€ä¿®å¤"
                    if overlay_status == "PASS"
                    else "æŒ‰å®¡è®¡é—®é¢˜ä¿®å¤åŽé‡å®¡",
                )
            )

        actual_matrix = [
            [visible_cell(value) for value in list(raw_row)]
            for raw_row in list(scope_sheets[0].get("matrix") or [])
        ]
        if actual_matrix != expected_matrix:
            raise ValueError("authoritative_audit_overlay_visible_claim_mismatch")

    def _project_reuse_summary(
        self,
        namespace: str,
        project_id: str,
        hub: dict[str, Any],
    ) -> dict[str, Any]:
        """Expose only the durable IDs a fresh role window needs to resume."""

        state = hub.get("project_state") if isinstance(hub.get("project_state"), dict) else {}
        source_keys = {
            "artifact_id",
            "source_role",
            "file_name",
            "version",
            "fingerprint",
            "imported_at",
        }
        authoritative_sources = [
            {
                key: copy.deepcopy(value)
                for key, value in row.items()
                if key in source_keys
            }
            for row in state.get("authoritative_sources", [])
            if isinstance(row, dict) and row.get("artifact_id")
        ]

        latest_artifacts: dict[str, dict[str, Any]] = {}
        raw_latest = state.get("latest_artifacts")
        if isinstance(raw_latest, dict):
            for artifact_type, raw in raw_latest.items():
                if not isinstance(raw, dict):
                    continue
                value = copy.deepcopy(raw)
                reusable_id = value.get("artifact_id") or value.get("deliverable_id")
                if reusable_id:
                    value["artifact_id"] = str(reusable_id)
                latest_artifacts[str(artifact_type)] = value

        pending_handoffs: list[dict[str, Any]] = []
        for handoff in self.store.list_project_records(
            namespace, project_id, kind="handoffs"
        ):
            if handoff.get("status") not in {"prepared", "consuming"}:
                continue
            pending_handoffs.append(
                {
                    key: copy.deepcopy(handoff.get(key))
                    for key in (
                        "handoff_id",
                        "work_order_id",
                        "audit_cycle_id",
                        "handoff_type",
                        "from_window_role",
                        "to_window_role",
                        "status",
                        "publication_state",
                        "storyboard_artifact_id",
                        "canonical_artifact_id",
                        "canonical_sha256",
                        "stage_trace_id",
                        "certificate_id",
                        "production_preflight_status",
                        "production_preflight_issue_count",
                        "independent_audit_status",
                        "created_at",
                    )
                    if handoff.get(key) is not None
                }
            )

        artifacts = self.store.list_artifacts(namespace, project_id)
        storyboard_artifacts = [
            row
            for row in artifacts
            if row.get("artifact_type") in {"storyboard", "storyboard_audit_overlay"}
        ]
        overlay_source_artifacts = [
            row
            for row in artifacts
            if row.get("artifact_type")
            in {
                "storyboard",
                "storyboard_json",
                "storyboard_json_recovered",
                "source:storyboard",
            }
        ]
        authoritative_storyboard_id = str(
            latest_artifacts.get("storyboard", {}).get("artifact_id") or ""
        )
        reuse_state_errors: list[str] = []
        if authoritative_storyboard_id:
            authoritative = next(
                (
                    row
                    for row in storyboard_artifacts
                    if row.get("artifact_id") == authoritative_storyboard_id
                ),
                None,
            )
            if authoritative is None:
                reuse_state_errors.append(
                    "AUTHORITATIVE_ARTIFACT_MISSING: latest storyboard pointer cannot be resolved"
                )
        else:
            authoritative = storyboard_artifacts[0] if storyboard_artifacts else None
        selected = authoritative
        authoritative_is_overlay = bool(
            isinstance(authoritative, dict)
            and authoritative.get("artifact_type") == "storyboard_audit_overlay"
        )
        if authoritative_is_overlay:
            source_artifact_id = str(
                authoritative.get("source_artifact_id") or ""
            ).strip()
            if not source_artifact_id:
                reuse_state_errors.append(
                    "AUTHORITATIVE_OVERLAY_SOURCE_MISSING: audit overlay has no source_artifact_id"
                )
                selected = None
            else:
                selected = next(
                    (
                        row
                        for row in overlay_source_artifacts
                        if row.get("artifact_id") == source_artifact_id
                    ),
                    None,
                )
                if selected is None:
                    reuse_state_errors.append(
                        "AUTHORITATIVE_OVERLAY_SOURCE_MISSING: audit overlay source artifact cannot be resolved"
                    )
        latest_storyboard: dict[str, Any] | None = None
        if isinstance(selected, dict):
            metadata = selected.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            authoritative_metadata = (
                authoritative.get("metadata")
                if isinstance(authoritative, dict)
                and isinstance(authoritative.get("metadata"), dict)
                else metadata
            )
            matching_handoff = next(
                (
                    row
                    for row in pending_handoffs
                    if row.get("storyboard_artifact_id") == selected.get("artifact_id")
                ),
                None,
            )
            # Audit authority belongs to the immutable overlay, while the
            # certificate itself remains bound to the source production bytes.
            raw_audit_status = authoritative_metadata.get("audit_status")
            raw_issue_count = authoritative_metadata.get("issue_count")
            certificate_id = authoritative_metadata.get(
                "independent_audit_certificate_id"
            )
            effective_audit_status = "PENDING_AUDIT"
            effective_issue_count: int | None = None
            audit_evidence_state = "missing_certificate"
            verified_certificate: dict[str, Any] | None = None
            if certificate_id:
                try:
                    if (
                        isinstance(authoritative, dict)
                        and authoritative.get("artifact_type")
                        == "storyboard_audit_overlay"
                    ):
                        self.store.get_artifact(
                            namespace,
                            project_id,
                            str(authoritative.get("artifact_id")),
                        )
                    (
                        verified_certificate,
                        verified_artifact_record,
                    ) = self._load_current_independent_audit_certificate(
                        namespace,
                        project_id,
                        certificate_id=str(certificate_id),
                        artifact_id=str(selected.get("artifact_id")),
                    )
                    selected = verified_artifact_record
                    metadata = selected.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    if authoritative_is_overlay:
                        self._verify_authoritative_audit_overlay(
                            namespace,
                            project_id,
                            overlay_record=authoritative,
                            source_record=selected,
                            certificate=verified_certificate,
                        )
                    report = verified_certificate.get("report")
                    if not isinstance(report, dict):
                        raise ValueError("independent audit report is unavailable")
                    effective_audit_status = str(
                        verified_certificate.get("result") or "fail_current"
                    ).upper()
                    effective_issue_count = int(
                        verified_certificate.get("issue_count") or 0
                    )
                    if effective_audit_status == "PASS" and (
                        effective_issue_count != 0 or report.get("issues")
                    ):
                        raise ValueError("clean certificate is inconsistent")
                    audit_evidence_state = "verified_independent_certificate"
                except (AttributeError, KeyError, OSError, TypeError, ValueError):
                    effective_audit_status = "AUDIT_EVIDENCE_INVALID"
                    effective_issue_count = None
                    audit_evidence_state = "invalid_certificate_or_binding"

            if authoritative_is_overlay:
                if audit_evidence_state == "missing_certificate":
                    reuse_state_errors.append(
                        "AUTHORITATIVE_OVERLAY_CERTIFICATE_MISSING: audit overlay has no independent audit certificate"
                    )
                elif audit_evidence_state != "verified_independent_certificate":
                    reuse_state_errors.append(
                        "AUTHORITATIVE_OVERLAY_AUDIT_EVIDENCE_INVALID: audit overlay certificate, binding, or artifact bytes are invalid"
                    )

            latest_storyboard = {
                "artifact_id": selected.get("artifact_id"),
                "authoritative_artifact_id": (
                    authoritative.get("artifact_id")
                    if isinstance(authoritative, dict)
                    else selected.get("artifact_id")
                ),
                "canonical_artifact_id": metadata.get("canonical_json_artifact_id"),
                "canonical_sha256": metadata.get("canonical_sha256"),
                "handoff_id": (
                    matching_handoff.get("handoff_id")
                    if isinstance(matching_handoff, dict)
                    else metadata.get("handoff_id")
                ),
                "trace_id": (
                    matching_handoff.get("stage_trace_id")
                    if isinstance(matching_handoff, dict)
                    else None
                ),
                "production_certificate_id": metadata.get("production_certificate_id"),
                "work_order_id": selected.get("work_order_id"),
                "source_artifact_id": selected.get("source_artifact_id"),
                "file_name": selected.get("file_name"),
                "version": selected.get("version"),
                "fingerprint": selected.get("fingerprint"),
                "created_at": selected.get("created_at"),
                "audit_status": effective_audit_status,
                "effective_audit_status": effective_audit_status,
                "issue_count": effective_issue_count,
                "legacy_audit_status": raw_audit_status,
                "legacy_issue_count": raw_issue_count,
                "legacy_claim_accepted": False,
                "audit_evidence_state": audit_evidence_state,
                "production_preflight_status": metadata.get(
                    "production_preflight_status"
                ),
                "production_preflight_issue_count": metadata.get(
                    "production_preflight_issue_count"
                ),
                "independent_audit_status": effective_audit_status,
                "independent_audit_certificate_id": (
                    certificate_id
                    if audit_evidence_state == "verified_independent_certificate"
                    else None
                ),
                "independent_audit_trace_id": (
                    verified_certificate.get("audit_trace_id")
                    if isinstance(verified_certificate, dict)
                    and audit_evidence_state == "verified_independent_certificate"
                    else None
                ),
                "authoritative_latest": bool(
                    authoritative_storyboard_id
                    and isinstance(authoritative, dict)
                    and authoritative.get("artifact_id") == authoritative_storyboard_id
                ),
            }

        return {
            "authoritative_sources": authoritative_sources,
            "latest_artifacts": latest_artifacts,
            "pending_handoffs": pending_handoffs,
            "latest_storyboard": latest_storyboard,
            "reuse_state_errors": reuse_state_errors,
        }

    @staticmethod
    def _independent_audit_pending_state(
        hub: dict[str, Any], reuse_summary: dict[str, Any]
    ) -> dict[str, Any]:
        """Return one fail-closed pending-audit decision for status and writes."""

        reasons: list[str] = []
        handoff_ids: list[str] = []
        for row in reuse_summary.get("pending_handoffs", []):
            if not isinstance(row, dict):
                continue
            if row.get("handoff_type") not in {
                "production_to_audit",
                "repair_to_audit",
            }:
                continue
            if row.get("status") not in {"prepared", "consuming"}:
                continue
            handoff_ids.append(str(row.get("handoff_id") or ""))
        if handoff_ids:
            reasons.append("production_or_repair_handoff_pending")

        work_order_ids: list[str] = []
        for row in hub.get("work_orders", []):
            if not isinstance(row, dict):
                continue
            if row.get("operation") not in STORYBOARD_OPERATIONS:
                continue
            if (
                row.get("status") == "validating"
                and row.get("awaiting_independent_audit") is True
            ):
                work_order_ids.append(str(row.get("work_order_id") or ""))
        if work_order_ids:
            reasons.append("production_work_order_awaiting_independent_audit")

        latest_storyboard = reuse_summary.get("latest_storyboard")
        latest_audit_status = ""
        if isinstance(latest_storyboard, dict):
            latest_audit_status = str(
                latest_storyboard.get("effective_audit_status")
                or latest_storyboard.get("audit_status")
                or ""
            ).upper()
        if latest_audit_status in {"PENDING_AUDIT", "AUDIT_EVIDENCE_INVALID"}:
            reasons.append(f"latest_storyboard_{latest_audit_status.casefold()}")

        return {
            "pending": bool(reasons),
            "reasons": reasons,
            "handoff_ids": [value for value in handoff_ids if value],
            "work_order_ids": [value for value in work_order_ids if value],
            "latest_audit_status": latest_audit_status or None,
        }

    @staticmethod
    def _draft_preflight_failure_state(
        ledger: dict[str, Any]
    ) -> dict[str, Any]:
        """Return unresolved failed draft scopes governed by fail-current policy."""

        policy_enabled = bool(
            ledger.get("policy", {}).get("fail_current_blocks_next_episode")
            is True
        )
        if not policy_enabled:
            return {
                "blocked": False,
                "policy_enabled": False,
                "scope_keys": [],
                "open_issue_ids": [],
            }

        failed_scope_keys = {
            str(event.get("scope_key") or "")
            for event in ledger.get("events", [])
            if isinstance(event, dict)
            and event.get("task_type") == "production_draft_preflight"
            and event.get("result") == "fail_current"
            and event.get("scope_key")
        }
        unresolved_scopes: list[str] = []
        open_issue_ids: set[str] = set()
        for scope in ledger.get("scope_states", []):
            if not isinstance(scope, dict):
                continue
            scope_key = str(scope.get("scope_key") or "")
            if scope_key not in failed_scope_keys:
                continue
            scope_open_issue_ids = {
                str(value)
                for value in scope.get("open_issue_ids", [])
                if str(value)
            }
            unresolved = bool(
                scope.get("last_result") != "pass"
                or scope_open_issue_ids
                or scope.get("closure_state") != "preflight_clean"
            )
            if unresolved:
                unresolved_scopes.append(scope_key)
                open_issue_ids.update(scope_open_issue_ids)

        # The scope is authoritative for closure.  A same-scope clean
        # preflight sets preflight_clean and empties open_issue_ids, so old
        # failed events never keep a repaired draft blocked indefinitely.
        return {
            "blocked": bool(unresolved_scopes),
            "policy_enabled": True,
            "scope_keys": sorted(unresolved_scopes),
            "open_issue_ids": sorted(open_issue_ids),
        }

    def agent_status(
        self,
        namespace: str,
        project_id: str | None = None,
        full_self_test: bool = False,
        *,
        request_session_id: str | None = None,
    ) -> ToolResponse:
        report = r10_bootstrap.verify_bundled(self_test=full_self_test) if full_self_test else self.bootstrap
        projects = self.store.list_projects(namespace)
        data: dict[str, Any] = {
            "plugin_version": __version__,
            "runtime_status": report.get("status"),
            "runtime": report,
            "output_contract": STRICT_OUTPUT_CONTRACT,
            "target_model": "Seedance 2.5",
            "aspect_ratio": "9:16",
            "project_count": len(projects),
            "projects": projects[:50],
            "execution_available": report.get("status") == "pass",
            "deployment_mode": self.settings.deployment_mode,
            "auth_mode": self.settings.auth_mode,
            "window_neutral_orchestration": True,
            "window_role_restrictions": False,
            "window_access_scope": "authenticated_namespace",
            "window_access_capabilities": [
                "production",
                "independent_audit",
                "repair",
            ],
            "performance_diagnostics": {
                "available": True,
                "tool": "mark_performance_stage",
                "measurement_scope": "tool_bracketed_chat",
                "ordinary_runs_add_markers": False,
            },
        }
        response_status = str(report.get("status"))
        summary = "è¿è¡Œæ—¶PASS" if report.get("status") == "pass" else "è¿è¡Œæ—¶é˜»å¡ž"
        if project_id:
            hub = self.store.load_hub(namespace, project_id)
            ledger = self.store.load_ledger(namespace, project_id)
            try:
                runtime_certificate = self._matching_runtime_certificate(
                    namespace, project_id
                )
            except ValueError:
                runtime_certificate = None
            runtime_certificate_valid = bool(
                runtime_certificate is not None
                and runtime_certificate.get("result") == "pass"
                and self.store.project_record_is_signed(runtime_certificate)
            )
            if full_self_test:
                if report.get("status") == "pass":
                    if not runtime_certificate_valid:
                        runtime_certificate = self._persist_runtime_certificate(
                            namespace, project_id, report
                        )
                else:
                    # A failed current full regression must not be masked by a
                    # certificate issued by an earlier successful invocation.
                    runtime_certificate = self._persist_runtime_certificate(
                        namespace, project_id, report
                    )
                runtime_certificate_valid = bool(
                    runtime_certificate is not None
                    and runtime_certificate.get("result") == "pass"
                    and self.store.project_record_is_signed(runtime_certificate)
                )
            state_errors = self._project_state_errors(
                namespace, project_id, hub, ledger
            )
            data["project"] = project_agent.summarize(hub)
            data["issues"] = storyboard_agent.summarize(ledger)
            reuse_summary = self._project_reuse_summary(namespace, project_id, hub)
            data.update(reuse_summary)
            state_errors.extend(reuse_summary.get("reuse_state_errors", []))
            data["state_valid"] = not state_errors
            data["state_errors"] = state_errors
            data["project_state_status"] = (
                "valid" if data["state_valid"] else "migration_required"
            )
            data["recent_telemetry"] = self.store.list_project_records(
                namespace, project_id, kind="telemetry"
            )[:20]
            data["recent_performance_diagnostics"] = self.store.list_project_records(
                namespace, project_id, kind="diagnostics"
            )[:10]
            data["runtime_test_certificate_valid"] = runtime_certificate_valid
            data["runtime_test_certificate"] = runtime_certificate
            quality_block = hub.get("project_state", {}).get(
                "production_quality_block"
            )
            data["production_quality_block"] = copy.deepcopy(quality_block)
            draft_failure_state = self._draft_preflight_failure_state(ledger)
            data["draft_preflight_repair_pending"] = bool(
                draft_failure_state["blocked"]
            )
            data["draft_preflight_failure_details"] = draft_failure_state
            pending_audit_state = self._independent_audit_pending_state(
                hub, reuse_summary
            )
            pending_independent_audit = bool(pending_audit_state["pending"])
            data["independent_audit_pending"] = pending_independent_audit
            data["independent_audit_pending_details"] = pending_audit_state
            data["role_continuation_supported"] = True
            data["continuation_supported"] = True
            data["role_continuation_required"] = False
            data["role_continuation_operations"] = sorted(
                STORYBOARD_OPERATIONS | {"storyboard_audit"}
            )
            current_session_fingerprint = self._session_fingerprint(
                namespace, request_session_id
            )
            resumable_authorities: list[dict[str, Any]] = []
            current_window_roles: set[str] = set()
            resumable_states = {
                "approved",
                "scope_locked",
                "proposal_ready",
                "executing",
                "validating",
                "failed_current",
                "blocked",
            }
            for work_order in hub.get("work_orders", []):
                if not isinstance(work_order, dict):
                    continue
                role = str(work_order.get("server_actor_role") or "")
                if role not in {
                    "production",
                    "independent_audit",
                    "repair",
                }:
                    continue
                try:
                    binding = self._active_authority_binding(
                        namespace,
                        project_id,
                        work_order,
                        hub=hub,
                    )
                    window_neutral = bool(
                        work_order.get("window_access_contract")
                        == WINDOW_NEUTRAL_ACCESS_CONTRACT
                    )
                    is_current = bool(
                        not window_neutral
                        and current_session_fingerprint
                        and binding.get("session_fingerprint")
                        == current_session_fingerprint
                    )
                    if is_current:
                        current_window_roles.add(role)
                    state = str(work_order.get("status") or "")
                    operation = str(work_order.get("operation") or "")
                    operation_supported = operation in STORYBOARD_OPERATIONS | {
                        "storyboard_audit"
                    }
                    resumable = bool(
                        not window_neutral
                        and state in resumable_states
                        and not is_current
                        and operation_supported
                    )
                    reason = (
                        "window_neutral_no_resume_required"
                        if window_neutral
                        else "already_active_in_current_window"
                        if is_current
                        else (
                            "eligible"
                            if resumable
                            else (
                                "operation_not_supported"
                                if not operation_supported
                                else f"work_order_state_{state or 'unknown'}"
                            )
                        )
                    )
                    resumable_authorities.append(
                        {
                            "work_order_id": work_order.get("work_order_id"),
                            "authority_root_id": binding.get(
                                "authority_root_id"
                            ),
                            "role": role,
                            "operation": work_order.get("operation"),
                            "work_order_status": state,
                            "active_generation": binding.get("generation"),
                            "current_window_status": (
                                "accessible"
                                if window_neutral
                                else ("active" if is_current else "other_window")
                            ),
                            "resumable": resumable,
                            "reason": reason,
                        }
                    )
                except ValueError as exc:
                    resumable_authorities.append(
                        {
                            "work_order_id": work_order.get("work_order_id"),
                            "role": role,
                            "operation": work_order.get("operation"),
                            "work_order_status": work_order.get("status"),
                            "resumable": False,
                            "reason": str(exc).split(":", 1)[0],
                        }
                    )
            data["resumable_authorities"] = resumable_authorities
            data["current_window_status"] = (
                "active"
                if current_window_roles
                else (
                    "window_neutral"
                    if request_session_id
                    else "session_unavailable"
                )
            )
            data["current_window_roles"] = sorted(current_window_roles)
            data["window_access_status"] = (
                "ready" if request_session_id else "session_unavailable"
            )
            data["required_next_role"] = (
                "independent_audit"
                if pending_independent_audit
                else (
                    "repair"
                    if (
                        draft_failure_state["blocked"]
                        or (
                            isinstance(quality_block, dict)
                            and quality_block.get("active")
                        )
                    )
                    else "production"
                )
            )
            data["required_next_action"] = data["required_next_role"]
            data["open_issue_pattern_count"] = data["issues"].get(
                "open_issue_count", 0
            )
            if not data["state_valid"]:
                response_status = "migration_required"
                data["execution_available"] = False
                data["execution_mode"] = "recovery_only"
                data["recovery_available"] = True
                data["recovery_operations"] = [
                    "get_agent_status",
                    "query_project_issues",
                    "migrate_project_state",
                ]
                summary += (
                    f"ï½œé¡¹ç›®çŠ¶æ€INVALIDï½œéœ€è¿ç§»ï½œé”™è¯¯{len(data['state_errors'])}é¡¹"
                )
            elif report.get("status") != "pass" or not runtime_certificate_valid:
                response_status = "action_required"
                data["execution_available"] = False
                data["execution_mode"] = "self_test_only"
                data["action_required"] = "run_full_self_test"
                data["self_test_required"] = True
                summary += "ï½œå½“å‰è¿è¡Œæ—¶æœªèŽ·æœ‰æ•ˆå…¨é‡å›žå½’è¯ä¹¦ï½œä»…å…è®¸è‡ªæ£€"
            elif isinstance(quality_block, dict) and quality_block.get("active"):
                response_status = "action_required"
                data["execution_available"] = True
                data["execution_mode"] = "root_cause_repair_only"
                data["allowed_storyboard_operations"] = ["storyboard_repair"]
                summary += "ï½œåˆç‰ˆè´¨é‡é—¨é˜»æ–­ä¸‹ä¸€æ‰¹ï½œéœ€æ ¹å› ä¿®å¤"
            elif pending_independent_audit:
                response_status = "action_required"
                data["execution_available"] = True
                data["execution_mode"] = "audit_resolution"
                data["allowed_storyboard_operations"] = ["storyboard_audit"]
                summary += "ï½œä¸Šä¸€æ‰¹å¾…ç‹¬ç«‹å®¡è®¡ï½œå¯åœ¨å½“å‰çª—å£ç›´æŽ¥å®¡è®¡"
            elif draft_failure_state["blocked"]:
                response_status = "action_required"
                data["execution_available"] = True
                data["execution_mode"] = "draft_repair_only"
                data["allowed_storyboard_operations"] = [
                    "storyboard_repair",
                    "storyboard_change",
                ]
                data["action_required"] = (
                    "correct_failed_draft_scope_or_start_root_cause_repair"
                )
                summary += "ï½œåˆç¨¿é¢„æ£€å¤±è´¥scopeæœªé—­åˆï½œä»…å…è®¸åŽŸå·¥å•ä¿®æ­£æˆ–æ ¹å› ä¿®å¤"
            else:
                data["execution_mode"] = "normal"
                data["allowed_storyboard_operations"] = sorted(
                    STORYBOARD_OPERATIONS | {"storyboard_audit"}
                )
                summary += "ï½œé¡¹ç›®çŠ¶æ€VALIDï½œAgentå¯æ‰§è¡Œ"
            summary += (
                f"ï½œé¡¹ç›®{hub.get('project_name')}"
                f"ï½œå¼€æ”¾é—®é¢˜æ¨¡å¼{data['issues'].get('open_issue_count', 0)}"
            )
        return ToolResponse(status=response_status, summary=summary, project_id=project_id, data=data)

    def _resolve_performance_diagnostic_links(
        self,
        namespace: str,
        project_id: str,
        *,
        window_role: str,
        related_trace_ids: list[str],
        related_artifact_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
        """Resolve only project-scoped IDs; never accept prompt or source text."""

        artifact_summary: dict[str, Any] | None = None
        if related_artifact_id:
            artifact, _path = self.store.get_artifact(
                namespace, project_id, related_artifact_id
            )
            artifact_summary = {
                "artifact_id": artifact.get("artifact_id"),
                "artifact_type": artifact.get("artifact_type"),
                "fingerprint": artifact.get("fingerprint"),
            }

        resolved: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for trace_id in related_trace_ids:
            try:
                trace = self.store.get_project_record(
                    namespace,
                    project_id,
                    kind="telemetry",
                    record_id=trace_id,
                )
            except ValueError:
                unresolved.append(trace_id)
                continue
            trace_role = str(trace.get("window_role") or "")
            if trace_role and trace_role != window_role:
                raise ValueError(
                    "diagnostic_trace_role_mismatch: linked trace belongs to another authority"
                )
            trace_artifact_id = str(trace.get("artifact_id") or "")
            if (
                related_artifact_id
                and trace_artifact_id
                and trace_artifact_id != related_artifact_id
            ):
                raise ValueError(
                    "diagnostic_trace_artifact_mismatch: linked trace and artifact differ"
                )
            resolved.append(
                {
                    "trace_id": str(trace.get("trace_id") or trace_id),
                    "operation": str(trace.get("operation") or "unknown"),
                    "duration_ms": round(float(trace.get("duration_ms") or 0), 3),
                    "measurement_scope": str(
                        trace.get("measurement_scope") or "server_only"
                    ),
                    "artifact_id": trace.get("artifact_id"),
                    "cache_hit": bool(trace.get("cache_hit", False)),
                }
            )
        return resolved, unresolved, artifact_summary

    def _performance_diagnostic_comparison(
        self,
        namespace: str,
        project_id: str,
        current: dict[str, Any],
    ) -> dict[str, Any]:
        config = current.get("configuration") or {}
        current_mode = str(config.get("reasoning_mode") or "unknown")
        if current_mode == "unknown":
            return {
                "status": "unavailable",
                "reason": "reasoning_mode_not_declared",
                "causal_claim_allowed": False,
            }
        candidates = []
        for record in self.store.list_project_records(
            namespace, project_id, kind="diagnostics"
        ):
            if (
                record.get("diagnostic_run_id") == current.get("diagnostic_run_id")
                or record.get("status") != "complete"
            ):
                continue
            other = record.get("configuration") or {}
            if (
                str(other.get("reasoning_mode") or "unknown") == current_mode
                or str(other.get("reasoning_mode") or "unknown") == "unknown"
            ):
                continue
            matching_keys = (
                "route",
                "window_type",
                "episode_count",
                "expected_shot_count",
            )
            if any(other.get(key) != config.get(key) for key in matching_keys):
                continue
            candidates.append(record)
        if not candidates:
            return {
                "status": "needs_controlled_pair",
                "reason": "no_completed_run_with_matching_scope_and_different_reasoning_mode",
                "causal_claim_allowed": False,
                "next_test": (
                    "åœ¨ç›¸åŒæºæ–‡ä»¶ã€é›†æ•°ã€é•œå¤´æ•°ã€çª—å£ç±»åž‹ã€ç¼“å­˜çŠ¶æ€å’Œè¾“å‡ºåˆåŒä¸‹ï¼Œ"
                    "åªåˆ‡æ¢æ€è€ƒæ¨¡å¼å†è¿è¡Œä¸€æ¬¡"
                ),
            }
        previous = candidates[0]
        previous_report = previous.get("report") or {}
        current_total = int(
            (current.get("checkpoints") or [{}])[-1].get("elapsed_from_start_ms") or 0
        )
        previous_total = int(previous_report.get("total_tool_bracketed_ms") or 0)
        return {
            "status": "observational_pair_available",
            "causal_claim_allowed": False,
            "current_run_id": current.get("diagnostic_run_id"),
            "current_reasoning_mode": current_mode,
            "current_total_ms": current_total,
            "comparison_run_id": previous.get("diagnostic_run_id"),
            "comparison_reasoning_mode": (previous.get("configuration") or {}).get(
                "reasoning_mode"
            ),
            "comparison_total_ms": previous_total,
            "observed_difference_ms": current_total - previous_total,
            "interpretation": (
                "è¿™æ˜¯åŒèŒƒå›´è§‚å¯Ÿå·®å€¼ï¼›åªæœ‰é‡å¤å¤šæ¬¡å¹¶æŽ§åˆ¶ç¼“å­˜ã€çª—å£å’Œè´Ÿè½½åŽï¼Œ"
                "æ‰èƒ½æŠŠå·®å¼‚ä¸»è¦å½’å› äºŽæ€è€ƒæ¨¡å¼"
            ),
        }

    def _build_performance_diagnostic_report(
        self,
        namespace: str,
        project_id: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        checkpoints = list(record.get("checkpoints") or [])
        stage_gaps: dict[str, int] = {}
        for checkpoint in checkpoints[1:]:
            gap_name = PERFORMANCE_DIAGNOSTIC_GAPS.get(str(checkpoint.get("stage")))
            if gap_name:
                stage_gaps[gap_name] = int(checkpoint.get("gap_from_previous_ms") or 0)
        total_ms = int(checkpoints[-1].get("elapsed_from_start_ms") or 0) if checkpoints else 0
        resolved_by_id: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []
        for checkpoint in checkpoints:
            for trace in checkpoint.get("resolved_plugin_traces") or []:
                resolved_by_id[str(trace.get("trace_id") or "")] = trace
            unresolved.extend(str(value) for value in checkpoint.get("unresolved_trace_ids") or [])
        resolved_by_id.pop("", None)
        plugin_server_ms = round(
            sum(float(trace.get("duration_ms") or 0) for trace in resolved_by_id.values()),
            3,
        )
        plugin_share = (
            round(plugin_server_ms * 100.0 / total_ms, 3) if total_ms > 0 else None
        )
        dominant_stage = max(stage_gaps, key=stage_gaps.get) if stage_gaps else None
        dominant_ms = stage_gaps.get(dominant_stage, 0) if dominant_stage else 0
        if plugin_share is None:
            plugin_finding = "æ²¡æœ‰å¯è§£æžçš„æœåŠ¡ç«¯traceï¼Œä¸èƒ½åˆ¤æ–­æ’ä»¶å æ¯”"
            plugin_evidence = "unmeasured"
        elif plugin_share < 20:
            plugin_finding = "å·²è§£æžçš„æ’ä»¶æœåŠ¡ç«¯æ—¶é—´ä¸æ˜¯æœ¬æ¬¡ä¸»è¦è§‚å¯Ÿç“¶é¢ˆ"
            plugin_evidence = "measured"
        elif plugin_share >= 50:
            plugin_finding = "æ’ä»¶æœåŠ¡ç«¯æ—¶é—´å æ¯”è¾ƒé«˜ï¼Œéœ€è¦ç»§ç»­ä¸‹é’»å…·ä½“æœåŠ¡ç«¯é˜¶æ®µ"
            plugin_evidence = "measured"
        else:
            plugin_finding = "æ’ä»¶æœåŠ¡ç«¯æ—¶é—´æœ‰æ˜¾è‘—å æ¯”ï¼Œä½†ä¸æ˜¯å”¯ä¸€è€—æ—¶æ¥æº"
            plugin_evidence = "measured"
        planning_ms = stage_gaps.get("storyboard_planning", 0)
        authorship_ms = stage_gaps.get("canonical_authorship", 0)
        comparison = self._performance_diagnostic_comparison(
            namespace, project_id, record
        )
        warnings = []
        if plugin_server_ms > total_ms and total_ms >= 0:
            warnings.append(
                "linked_plugin_trace_duration_exceeds_bracket: verify that only traces from this run were linked"
            )
        if unresolved:
            warnings.append(
                "some_related_trace_ids_are_not_persisted_server_telemetry"
            )
        return {
            "diagnostic_run_id": record.get("diagnostic_run_id"),
            "measurement_scope": "tool_bracketed_chat",
            "clock_source": "server_receive_time",
            "exact_end_to_end": False,
            "total_tool_bracketed_ms": total_ms,
            "stage_gaps_ms": stage_gaps,
            "dominant_observed_stage": dominant_stage,
            "dominant_observed_stage_ms": dominant_ms,
            "resolved_plugin_trace_count": len(resolved_by_id),
            "resolved_plugin_server_ms": plugin_server_ms,
            "plugin_share_of_bracketed_percent": plugin_share,
            "unresolved_trace_ids": list(dict.fromkeys(unresolved)),
            "attribution": [
                {
                    "factor": "plugin_server",
                    "evidence": plugin_evidence,
                    "finding": plugin_finding,
                },
                {
                    "factor": "chatgpt_model_orchestration_interval",
                    "evidence": "tool_bracketed_gap",
                    "finding": (
                        f"æœ€å¤§å¯è§‚å¯ŸåŒºé—´ä¸º{dominant_stage or 'unknown'}ï¼Œ{dominant_ms}æ¯«ç§’ï¼›"
                        "è¯¥åŒºé—´å¯èƒ½åŒ…å«è§„åˆ’ã€éšè—æ€è€ƒã€æ–‡æœ¬ç”Ÿæˆã€å®¢æˆ·ç«¯ä¼ è¾“å’Œå·¥å…·ç¼–æŽ’ï¼Œ"
                        "æ— APIæ—¶ä¸èƒ½å†ç²¾ç¡®æ‹†åˆ†"
                    ),
                },
                {
                    "factor": "storyboard_workflow_rules",
                    "evidence": "tool_bracketed_gap",
                    "planning_interval_ms": planning_ms,
                    "authorship_interval_ms": authorship_ms,
                    "finding": (
                        "è§„åˆ’åŒºé—´å¤§äºŽæˆç¨¿åŒºé—´ï¼Œæµç¨‹/è§„åˆ™å‡†å¤‡å€¼å¾—ä¼˜å…ˆåšA/B"
                        if planning_ms > authorship_ms
                        else "æˆç¨¿åŒºé—´ä¸å°äºŽè§„åˆ’åŒºé—´ï¼Œä¼˜å…ˆæ£€æŸ¥æ¨¡åž‹ç”Ÿæˆä½“é‡å’Œæ€è€ƒæ¨¡å¼"
                    ),
                },
                {
                    "factor": "reasoning_mode",
                    "evidence": comparison.get("status"),
                    "finding": comparison,
                },
            ],
            "unmeasured_stages": [
                "before_task_started_marker",
                "provider_queue_latency",
                "hidden_reasoning_substeps",
                "token_generation_count_and_rate",
                "client_network_and_render_substages",
                "final_answer_after_delivery_ready_marker",
            ],
            "quality_contract_unchanged": {
                "output_contract": "R10-SPC-1",
                "canonical_atomic_field_count": 27,
                "per_shot_spatial_environment_lighting_and_generation_limits": True,
                "lighting_fields": ["å…‰å½±", "å…‰æºæ¥æº", "å…‰æºæ–¹å‘", "è‰²æ¸©å€¾å‘"],
                "background_seedance_generation": False,
                "seedance_batches_generated": 0,
                "model_authored_derived_chars": 0,
            },
            "warnings": warnings,
        }

    @_fence_role_operation
    def mark_performance_stage(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        action: str,
        stage: str,
        diagnostic_run_id: str | None = None,
        reasoning_mode: str = "unknown",
        window_type: str = "unknown",
        route: str = "unknown",
        episode_count: int = 0,
        expected_shot_count: int = 0,
        observed_shot_count: int = 0,
        observed_character_count: int = 0,
        related_trace_ids: list[str] | None = None,
        related_artifact_id: str | None = None,
        request_session_id: str | None = None,
    ) -> ToolResponse:
        """Persist content-free checkpoints around a native ChatGPT run.

        The server timestamps receipt of each marker.  It does not accept
        caller-authored timestamps, prompt text, dialogue, source excerpts, or
        hidden-reasoning claims, so the resulting report remains useful even
        when the caller cannot use the OpenAI API.
        """

        self._require_runtime()
        if action not in {"start", "mark", "finish"}:
            raise ValueError("diagnostic_action_invalid")
        if stage not in PERFORMANCE_DIAGNOSTIC_STAGES:
            raise ValueError("diagnostic_stage_invalid")
        expected_action = (
            "start"
            if stage == PERFORMANCE_DIAGNOSTIC_STAGES[0]
            else "finish"
            if stage == PERFORMANCE_DIAGNOSTIC_STAGES[-1]
            else "mark"
        )
        if action != expected_action:
            raise ValueError(
                f"diagnostic_action_stage_mismatch: {stage} requires {expected_action}"
            )
        if reasoning_mode not in PERFORMANCE_DIAGNOSTIC_REASONING_MODES:
            raise ValueError("diagnostic_reasoning_mode_invalid")
        if window_type not in PERFORMANCE_DIAGNOSTIC_WINDOW_TYPES:
            raise ValueError("diagnostic_window_type_invalid")
        numeric_values = {
            "episode_count": (episode_count, 1_000),
            "expected_shot_count": (expected_shot_count, 100_000),
            "observed_shot_count": (observed_shot_count, 100_000),
            "observed_character_count": (observed_character_count, 100_000_000),
        }
        for field, (value, maximum) in numeric_values.items():
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                raise ValueError(f"diagnostic_metric_invalid: {field}")
        trace_ids = [str(value).strip() for value in (related_trace_ids or [])]
        if (
            len(trace_ids) > 16
            or any(not value or len(value) > 128 for value in trace_ids)
            or len(set(trace_ids)) != len(trace_ids)
        ):
            raise ValueError("diagnostic_trace_ids_invalid")

        hub = self.store.load_hub(namespace, project_id)
        work_order = _find_work_order(hub, work_order_id)
        role = str(work_order.get("server_actor_role") or "")
        if work_order.get("operation") not in STORYBOARD_OPERATIONS | {"storyboard_audit"}:
            raise ValueError("diagnostic_storyboard_work_order_required")
        if role not in PERFORMANCE_DIAGNOSTIC_ROUTES:
            raise ValueError("diagnostic_window_role_invalid")
        self._assert_storyboard_authority(work_order, role)
        authority_binding = self._assert_work_order_session(
            namespace, work_order, request_session_id
        )
        if route not in PERFORMANCE_DIAGNOSTIC_ROUTES.get(role, set()):
            raise ValueError("diagnostic_route_role_mismatch")
        resolved, unresolved, artifact_summary = self._resolve_performance_diagnostic_links(
            namespace,
            project_id,
            window_role=role,
            related_trace_ids=trace_ids,
            related_artifact_id=related_artifact_id,
        )
        now_ms = time.time_ns() // 1_000_000
        observed_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )
        request_signature = {
            "related_trace_ids": trace_ids,
            "related_artifact_id": related_artifact_id,
            "observed_shot_count": observed_shot_count,
            "observed_character_count": observed_character_count,
        }

        if action == "start":
            if diagnostic_run_id:
                raise ValueError("diagnostic_run_id_must_be_omitted_on_start")
            run_id = _new_id("DGN")
            checkpoint = {
                "sequence": 0,
                "stage": stage,
                "observed_at": observed_at,
                "observed_epoch_ms": now_ms,
                "elapsed_from_start_ms": 0,
                "gap_from_previous_ms": 0,
                "metrics": {
                    "observed_shot_count": observed_shot_count,
                    "observed_character_count": observed_character_count,
                },
                "request_signature": request_signature,
                "resolved_plugin_traces": resolved,
                "unresolved_trace_ids": unresolved,
                "artifact": artifact_summary,
            }
            record = {
                "diagnostic_run_id": run_id,
                "status": "running",
                "work_order_id": work_order_id,
                "window_role": role,
                "transport_session_fingerprint": authority_binding.get(
                    "session_fingerprint"
                ),
                "measurement_scope": "tool_bracketed_chat",
                "clock_source": "server_receive_time",
                "configuration": {
                    "reasoning_mode": reasoning_mode,
                    "window_type": window_type,
                    "route": route,
                    "episode_count": episode_count,
                    "expected_shot_count": expected_shot_count,
                },
                "started_at": observed_at,
                "started_epoch_ms": now_ms,
                "checkpoints": [checkpoint],
                "content_retention": "ids_enums_counts_only",
                **NO_DUPLICATE_GENERATION_EVIDENCE,
            }
            persisted = self.store.save_project_record(
                namespace,
                project_id,
                kind="diagnostics",
                record_id=run_id,
                value=record,
            )
            return ToolResponse(
                status="diagnostic_started",
                summary="æ— APIæ€§èƒ½è¯Šæ–­å·²å¼€å§‹ï½œä¸‹ä¸€é˜¶æ®µ inputs_ready",
                project_id=project_id,
                work_order_id=work_order_id,
                trace_id=run_id,
                data={
                    "diagnostic_run_id": run_id,
                    "measurement_scope": "tool_bracketed_chat",
                    "checkpoint": checkpoint,
                    "next_stage": "inputs_ready",
                    "record_integrity_signed": self.store.project_record_is_signed(
                        persisted
                    ),
                },
            )

        if not diagnostic_run_id:
            raise ValueError("diagnostic_run_id_required")
        duplicate = False
        completed: dict[str, Any] | None = None
        checkpoint: dict[str, Any] | None = None
        with self.store.mutate_project_record(
            namespace,
            project_id,
            kind="diagnostics",
            record_id=diagnostic_run_id,
        ) as record:
            if record.get("work_order_id") != work_order_id or record.get("window_role") != role:
                raise ValueError("diagnostic_work_order_binding_mismatch")
            expected_session = record.get("transport_session_fingerprint")
            actual_session = authority_binding.get("session_fingerprint")
            if expected_session and expected_session != actual_session:
                raise ValueError("diagnostic_window_session_mismatch")
            checkpoints = record.get("checkpoints")
            if not isinstance(checkpoints, list) or not checkpoints:
                raise ValueError("diagnostic_record_invalid")
            last = checkpoints[-1]
            if last.get("stage") == stage:
                if last.get("request_signature") != request_signature:
                    raise ValueError("diagnostic_stage_replay_conflict")
                duplicate = True
                checkpoint = copy.deepcopy(last)
                if record.get("status") == "complete":
                    completed = copy.deepcopy(record)
            else:
                if record.get("status") != "running":
                    raise ValueError("diagnostic_run_already_complete")
                expected_index = len(checkpoints)
                if (
                    expected_index >= len(PERFORMANCE_DIAGNOSTIC_STAGES)
                    or PERFORMANCE_DIAGNOSTIC_STAGES[expected_index] != stage
                ):
                    next_stage = (
                        PERFORMANCE_DIAGNOSTIC_STAGES[expected_index]
                        if expected_index < len(PERFORMANCE_DIAGNOSTIC_STAGES)
                        else "none"
                    )
                    raise ValueError(
                        f"diagnostic_stage_out_of_order: expected {next_stage}, got {stage}"
                    )
                started_ms = int(record.get("started_epoch_ms") or now_ms)
                previous_ms = int(last.get("observed_epoch_ms") or started_ms)
                checkpoint = {
                    "sequence": expected_index,
                    "stage": stage,
                    "observed_at": observed_at,
                    "observed_epoch_ms": now_ms,
                    "elapsed_from_start_ms": max(0, now_ms - started_ms),
                    "gap_from_previous_ms": max(0, now_ms - previous_ms),
                    "metrics": {
                        "observed_shot_count": observed_shot_count,
                        "observed_character_count": observed_character_count,
                    },
                    "request_signature": request_signature,
                    "resolved_plugin_traces": resolved,
                    "unresolved_trace_ids": unresolved,
                    "artifact": artifact_summary,
                }
                checkpoints.append(checkpoint)
                record["updated_at"] = observed_at
                if action == "finish":
                    record["status"] = "complete"
                    record["finished_at"] = observed_at
                    record["report"] = self._build_performance_diagnostic_report(
                        namespace, project_id, record
                    )
                    completed = copy.deepcopy(record)

        assert checkpoint is not None
        if completed is not None:
            report = completed.get("report") or self._build_performance_diagnostic_report(
                namespace, project_id, completed
            )
            return ToolResponse(
                status="diagnostic_complete",
                summary=(
                    f"æ— APIæ€§èƒ½è¯Šæ–­å®Œæˆï½œæ€»è®¡{report.get('total_tool_bracketed_ms', 0)}æ¯«ç§’ï½œ"
                    f"æœ€å¤§åŒºé—´{report.get('dominant_observed_stage') or 'unknown'}"
                ),
                project_id=project_id,
                work_order_id=work_order_id,
                artifact_id=related_artifact_id,
                trace_id=diagnostic_run_id,
                data={
                    "diagnostic_run_id": diagnostic_run_id,
                    "idempotent_replay": duplicate,
                    "report": report,
                },
            )
        next_index = int(checkpoint.get("sequence") or 0) + 1
        next_stage = PERFORMANCE_DIAGNOSTIC_STAGES[next_index]
        return ToolResponse(
            status="diagnostic_checkpointed",
            summary=f"è¯Šæ–­é˜¶æ®µå·²è®°å½•ï½œ{stage}ï½œä¸‹ä¸€é˜¶æ®µ {next_stage}",
            project_id=project_id,
            work_order_id=work_order_id,
            trace_id=diagnostic_run_id,
            data={
                "diagnostic_run_id": diagnostic_run_id,
                "measurement_scope": "tool_bracketed_chat",
                "idempotent_replay": duplicate,
                "checkpoint": checkpoint,
                "next_stage": next_stage,
            },
        )


    def create_project(self, namespace: str, project_name: str) -> ToolResponse:
        self._require_runtime()
        project_name = project_name.strip()
        if not project_name:
            raise ValueError("project_name is required")
        hub = project_agent.new_hub(project_name)
        project_id = str(hub["project_id"])
        if self.store.project_exists(namespace, project_id):
            existing = self.store.load_hub(namespace, project_id)
            return ToolResponse(
                status="existing",
                summary=f"é¡¹ç›®å·²å­˜åœ¨ï½œ{existing.get('project_name')}",
                project_id=project_id,
                data={"project": project_agent.summarize(existing)},
            )
        ledger = storyboard_agent.new_ledger(project_name, project_id=project_id)
        self.store.create_project(namespace, hub, ledger)
        return ToolResponse(
            status="created",
            summary=f"é¡¹ç›®å·²å»ºç«‹ï½œ{project_name}",
            project_id=project_id,
            data={"project": project_agent.summarize(hub)},
        )

    def _assert_project_migration_authority(
        self,
        namespace: str,
        work_order: dict[str, Any],
        request_session_id: str | None,
    ) -> None:
        if (
            work_order.get("operation") not in {"rule_improvement", "version_management"}
            or work_order.get("mode") != "execute"
            or work_order.get("authorization") != "explicit_execute"
            or work_order.get("server_actor_role") != "production"
        ):
            raise ValueError(
                "project_migration_authority_required: use an explicit rule_improvement "
                "or version_management production work order"
            )
        self._assert_work_order_session(namespace, work_order, request_session_id)
        self._assert_work_order_write_ready(work_order)

    @staticmethod
    def _whole_schema_errors(schema: dict[str, Any], value: dict[str, Any]) -> list[str]:
        errors = []
        for error in Draft202012Validator(schema).iter_errors(value):
            location = ".".join(str(item) for item in error.absolute_path) or "$"
            errors.append(f"{location}: {error.message}")
        return errors

    def _ledger_checkpoint_authorizer(
        self, namespace: str, project_id: str
    ) -> Any:
        """Return the project-bound verifier for a persisted replay checkpoint."""

        def authorize(checkpoint: dict[str, Any]) -> bool:
            return self.store.ledger_replay_checkpoint_is_authorized(
                namespace, project_id, checkpoint
            )

        return authorize

    def _project_state_errors(
        self,
        namespace: str,
        project_id: str,
        hub: dict[str, Any],
        ledger: dict[str, Any],
    ) -> list[str]:
        return (
            project_agent.validate_hub(hub)
            + storyboard_agent.validate_ledger(
                ledger,
                checkpoint_authorizer=self._ledger_checkpoint_authorizer(
                    namespace, project_id
                ),
            )
            + [
                f"hub_schema.{value}"
                for value in self._whole_schema_errors(self._project_schema, hub)
            ]
            + [
                f"ledger_schema.{value}"
                for value in self._whole_schema_errors(self._ledger_schema, ledger)
            ]
        )

    def _require_current_project_state(
        self, namespace: str, project_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        hub = self.store.load_hub(namespace, project_id)
        ledger = self.store.load_ledger(namespace, project_id)
        errors = self._project_state_errors(
            namespace, project_id, hub, ledger
        )
        if errors:
            raise ValueError(
                "project_state_migration_required: " + "; ".join(errors[:20])
            )
        return hub, ledger

    @staticmethod
    def _project_migration_key(
        *,
        project_id: str,
        work_order_id: str,
        from_version: str,
        target_version: str,
    ) -> str:
        return sha256_bytes(
            _stable_json_bytes(
                {
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "from_version": from_version,
                    "target_version": target_version,
                    "spec": "project-state-migration-v055",
                }
            )
        )

    def _assert_project_migration_record_images(
        self,
        record: dict[str, Any],
        *,
        project_id: str,
        migration_id: str,
    ) -> None:
        """Verify every signed journal image before any state-file write."""

        image_bindings = (
            ("hub_before", "hub_before_sha256"),
            ("ledger_before", "ledger_before_sha256"),
            ("hub_after", "hub_after_sha256"),
            ("ledger_after", "ledger_after_sha256"),
        )
        if (
            record.get("record_id") != migration_id
            or record.get("migration_id") != migration_id
            or record.get("project_id") != project_id
            or record.get("preserves_artifacts") is not True
            or not isinstance(record.get("changed_paths"), list)
        ):
            raise ValueError("migration_record_authority_conflict")
        for image_field, sha_field in image_bindings:
            image = record.get(image_field)
            declared_sha = record.get(sha_field)
            if (
                not isinstance(image, dict)
                or not isinstance(declared_sha, str)
                or self.store.project_state_sha256(image) != declared_sha
            ):
                raise ValueError("migration_record_authority_conflict")

    def _project_migration_candidate_errors(
        self,
        namespace: str,
        project_id: str,
        *,
        hub_before: dict[str, Any],
        ledger_before: dict[str, Any],
        candidate_hub: dict[str, Any],
        candidate_ledger: dict[str, Any],
        migration_id: str,
        signed_journal: dict[str, Any] | None = None,
    ) -> list[str]:
        """Validate one migration after-image without authorizing a write."""

        candidate_checkpoint = candidate_ledger.get("replay_checkpoint")
        if (
            isinstance(candidate_checkpoint, dict)
            and candidate_checkpoint != ledger_before.get("replay_checkpoint")
        ):
            expected_old_ledger_sha = self.store.project_state_sha256(
                ledger_before
            )

            def candidate_checkpoint_authorizer(
                checkpoint: dict[str, Any],
            ) -> bool:
                journal_binding_valid = signed_journal is None or bool(
                    self.store.project_record_is_signed(signed_journal)
                    and signed_journal.get("record_id") == migration_id
                    and signed_journal.get("migration_id") == migration_id
                    and signed_journal.get("project_id") == project_id
                    and signed_journal.get("ledger_before_sha256")
                    == expected_old_ledger_sha
                    and signed_journal.get("ledger_after")
                    == candidate_ledger
                    and signed_journal.get("ledger_after_sha256")
                    == self.store.project_state_sha256(candidate_ledger)
                )
                return bool(
                    journal_binding_valid
                    and checkpoint == candidate_checkpoint
                    and checkpoint.get("migration_id") == migration_id
                    and checkpoint.get("old_ledger_canonical_sha256")
                    == expected_old_ledger_sha
                )

        else:
            candidate_checkpoint_authorizer = (
                self._ledger_checkpoint_authorizer(namespace, project_id)
            )
        return (
            project_agent.validate_hub(candidate_hub)
            + storyboard_agent.validate_ledger(
                candidate_ledger,
                checkpoint_authorizer=candidate_checkpoint_authorizer,
            )
            + [
                f"hub_schema.{value}"
                for value in self._whole_schema_errors(
                    self._project_schema, candidate_hub
                )
            ]
            + [
                f"ledger_schema.{value}"
                for value in self._whole_schema_errors(
                    self._ledger_schema, candidate_ledger
                )
            ]
        )

    def _assert_rolled_back_migration_candidate_matches(
        self,
        record: dict[str, Any],
        *,
        migration_id: str,
    ) -> None:
        """Rebuild a rolled-back candidate with its sealed timestamps."""

        hub_after = record["hub_after"]
        ledger_after = record["ledger_after"]
        migration_history = next(
            (
                row
                for row in reversed(hub_after.get("version_history", []))
                if isinstance(row, dict)
                and row.get("migration_id") == migration_id
            ),
            {},
        )
        hub_timestamp = str(
            migration_history.get("timestamp")
            or hub_after.get("updated_at")
            or ""
        )
        ledger_timestamp = str(ledger_after.get("updated_at") or "")
        if not hub_timestamp or not ledger_timestamp:
            raise ValueError("migration_record_authority_conflict")
        expected_hub, expected_hub_changes = (
            project_agent.migrate_hub_to_current(
                record["hub_before"],
                timestamp=hub_timestamp,
                migration_id=migration_id,
            )
        )
        expected_ledger, expected_ledger_changes = (
            storyboard_agent.migrate_ledger_to_current(
                record["ledger_before"],
                timestamp=ledger_timestamp,
                migration_id=migration_id,
            )
        )
        if (
            expected_hub != hub_after
            or expected_ledger != ledger_after
            or expected_hub_changes + expected_ledger_changes
            != record.get("changed_paths")
        ):
            raise ValueError("migration_record_authority_conflict")

    def migrate_project_state(
        self,
        namespace: str,
        *,
        project_id: str,
        work_order_id: str,
        action: str,
        target_version: str,
        expected_current_version: str | None,
        migration_id: str | None,
        request_session_id: str | None,
    ) -> ToolResponse:
        """Plan, commit or CAS-rollback one control-state-only migration."""

        self._require_runtime()
        if action not in {"plan", "execute", "rollback"}:
            raise ValueError("migration action must be plan, execute, or rollback")
        if action in {"execute", "rollback"} and not expected_current_version:
            raise ValueError(
                "expected_current_version is required for migration execute or rollback"
            )
        if target_version != project_agent.COMPACT_MASTER_VERSION:
            raise ValueError(
                f"unsupported migration target: expected {project_agent.COMPACT_MASTER_VERSION}"
            )
        hub_before = self.store.load_hub(namespace, project_id)
        ledger_before = self.store.load_ledger(namespace, project_id)
        work_order = _find_work_order(hub_before, work_order_id)
        self._assert_project_migration_authority(
            namespace, work_order, request_session_id
        )
        current_version = str(
            hub_before.get("policy", {}).get("compact_master_version") or "unknown"
        )
        if action == "rollback":
            if not migration_id:
                raise ValueError("migration_id is required for rollback")
            rollback_record = self.store.get_project_record(
                namespace,
                project_id,
                kind="migrations",
                record_id=migration_id,
            )
            self._assert_project_migration_record_images(
                rollback_record,
                project_id=project_id,
                migration_id=migration_id,
            )
            if (
                rollback_record.get("work_order_id") != work_order_id
                or rollback_record.get("target_version") != target_version
                or (
                    expected_current_version
                    and rollback_record.get("target_version")
                    != expected_current_version
                )
            ):
                raise ValueError("migration_record_authority_conflict")
            # Bind the caller's expected version to the signed journal target,
            # not the currently readable hub field. During an interrupted
            # two-file rollback the hub may already contain `from_version`
            # while the ledger still contains `target_version`; storage owns
            # the exact before/after CAS recovery decision.
            record = self.store.rollback_project_state_migration(
                namespace, project_id, migration_id
            )
            return ToolResponse(
                status="rolled_back",
                summary=f"é¡¹ç›®æŽ§åˆ¶çŠ¶æ€å·²å›žæ»šï½œ{migration_id}",
                project_id=project_id,
                work_order_id=work_order_id,
                data={"migration": record, "authored_artifacts_modified": False},
            )

        # Resolve every journal strictly before selecting one.  An execute may
        # never run beside a project rollback, and an explicit migration ID is
        # authoritative rather than a hint that may fall back to another row.
        migration_records: list[dict[str, Any]] = []
        requested_migration: dict[str, Any] | None = None
        prepared_migration: dict[str, Any] | None = None
        if action == "execute":
            migration_records = self.store.list_project_records_strict(
                namespace, project_id, kind="migrations"
            )
            if any(
                row.get("status") == "rolling_back"
                for row in migration_records
            ):
                raise ValueError(
                    "migration_rollback_in_progress: finish rollback before execute"
                )
            if migration_id:
                try:
                    requested_migration = self.store.get_project_record(
                        namespace,
                        project_id,
                        kind="migrations",
                        record_id=migration_id,
                    )
                except ValueError as exc:
                    if not self._is_unknown_project_record_error(
                        exc,
                        kind="migrations",
                        record_id=migration_id,
                    ):
                        raise
                    requested_migration = None
                if requested_migration is None and any(
                    row.get("status") in {"prepared", "replaying"}
                    for row in migration_records
                ):
                    raise ValueError("migration_record_authority_conflict")
                if (
                    requested_migration is not None
                    and requested_migration.get("status")
                    in {"prepared", "replaying"}
                ):
                    prepared_migration = requested_migration
            else:
                matching_prepared = [
                    row
                    for row in migration_records
                    if row.get("status") in {"prepared", "replaying"}
                    and row.get("work_order_id") == work_order_id
                    and row.get("target_version") == target_version
                ]
                if len(matching_prepared) > 1:
                    raise ValueError("migration_record_authority_conflict")
                prepared_migration = (
                    matching_prepared[0] if matching_prepared else None
                )
            if prepared_migration is None and any(
                row.get("status") in {"prepared", "replaying"}
                for row in migration_records
            ):
                raise ValueError("migration_record_authority_conflict")
            if prepared_migration is not None:
                prepared_id = str(prepared_migration.get("migration_id") or "")
                prepared_from_version = str(
                    prepared_migration.get("from_version") or ""
                )
                expected_prepared_key = self._project_migration_key(
                    project_id=project_id,
                    work_order_id=work_order_id,
                    from_version=prepared_from_version,
                    target_version=target_version,
                )
                if (
                    prepared_migration.get("project_id") != project_id
                    or prepared_migration.get("work_order_id") != work_order_id
                    or prepared_migration.get("target_version") != target_version
                    or prepared_migration.get("status")
                    not in {"prepared", "replaying"}
                    or (
                        migration_id is not None
                        and prepared_id != migration_id
                    )
                    or (
                        expected_current_version is not None
                        and prepared_from_version != expected_current_version
                    )
                    or prepared_migration.get("migration_key")
                    != expected_prepared_key
                    or prepared_migration.get("ruleset_version")
                    != self._ruleset_version
                    or prepared_migration.get("validator_version")
                    != self._validator_version()
                ):
                    raise ValueError("migration_record_authority_conflict")
                self._assert_project_migration_record_images(
                    prepared_migration,
                    project_id=project_id,
                    migration_id=prepared_id,
                )
                prepared_errors = self._project_migration_candidate_errors(
                    namespace,
                    project_id,
                    hub_before=prepared_migration["hub_before"],
                    ledger_before=prepared_migration["ledger_before"],
                    candidate_hub=prepared_migration["hub_after"],
                    candidate_ledger=prepared_migration["ledger_after"],
                    migration_id=prepared_id,
                    signed_journal=prepared_migration,
                )
                if prepared_errors:
                    raise ValueError(
                        "migration_candidate_invalid: "
                        + "; ".join(prepared_errors[:20])
                    )
                current_certificate = self._require_runtime_test_certificate(
                    namespace, project_id
                )
                if prepared_migration.get("runtime_test_certificate_id") != str(
                    current_certificate["certificate_id"]
                ):
                    raise ValueError("migration_runtime_certificate_conflict")
                recovered = self.store.commit_project_state_migration(
                    namespace,
                    project_id,
                    migration_id=prepared_id,
                    migration_key=expected_prepared_key,
                    work_order_id=work_order_id,
                    from_version=prepared_from_version,
                    target_version=target_version,
                    hub_before=prepared_migration["hub_before"],
                    ledger_before=prepared_migration["ledger_before"],
                    hub_after=prepared_migration["hub_after"],
                    ledger_after=prepared_migration["ledger_after"],
                    runtime_test_certificate_id=str(
                        current_certificate["certificate_id"]
                    ),
                    runtime_test_certificate_key=self._runtime_certificate_key(),
                    ruleset_version=self._ruleset_version,
                    validator_version=self._validator_version(),
                    changed_paths=list(prepared_migration["changed_paths"]),
                )
                recovered_hub = self.store.load_hub(namespace, project_id)
                recovered_ledger = self.store.load_ledger(namespace, project_id)
                recovered_errors = self._project_state_errors(
                    namespace,
                    project_id,
                    recovered_hub,
                    recovered_ledger,
                )
                if recovered_errors:
                    raise RuntimeError(
                        "migration_recovered_state_invalid: "
                        + "; ".join(recovered_errors[:20])
                    )
                return ToolResponse(
                    status="migration_recovered",
                    summary=(
                        f"é¡¹ç›®æŽ§åˆ¶çŠ¶æ€è¿ç§»å·²ä»Žç­¾åæ—¥å¿—æ¢å¤ï½œ"
                        f"{prepared_from_version}â†’{target_version}"
                    ),
                    project_id=project_id,
                    work_order_id=work_order_id,
                    data={
                        "migration": recovered,
                        "authored_artifacts_modified": False,
                    },
                )

        key_from_version = expected_current_version or current_version
        migration_key = self._project_migration_key(
            project_id=project_id,
            work_order_id=work_order_id,
            from_version=key_from_version,
            target_version=target_version,
        )
        resolved_migration_id = migration_id or f"MIG-{migration_key[:20]}"
        existing: dict[str, Any] | None = None
        if action == "execute":
            if requested_migration is not None and (
                requested_migration.get("migration_key") != migration_key
                or requested_migration.get("migration_id")
                != resolved_migration_id
            ):
                raise ValueError("migration_record_authority_conflict")
            same_key_records = [
                row
                for row in migration_records
                if row.get("migration_key") == migration_key
            ]
            if any(
                row.get("migration_id") != resolved_migration_id
                for row in same_key_records
            ):
                raise ValueError("migration_record_authority_conflict")
            if len(same_key_records) > 1:
                raise ValueError("migration_record_authority_conflict")
            existing = (
                requested_migration
                if requested_migration is not None
                else (same_key_records[0] if same_key_records else None)
            )
            if existing is not None and existing.get("status") not in {
                "committed",
                "rolled_back",
                "replaying",
            }:
                raise ValueError("migration_record_authority_conflict")
        if existing and existing.get("status") == "committed":
            existing_id = str(existing.get("migration_id") or "")
            self._assert_project_migration_record_images(
                existing,
                project_id=project_id,
                migration_id=existing_id,
            )
            current_certificate = self._require_runtime_test_certificate(
                namespace, project_id
            )
            if (
                not self.store.project_record_is_signed(existing)
                or existing.get("project_id") != project_id
                or existing.get("work_order_id") != work_order_id
                or existing.get("migration_key") != migration_key
                or existing.get("from_version") != key_from_version
                or existing.get("target_version") != target_version
                or existing_id != resolved_migration_id
                or existing.get("ruleset_version") != self._ruleset_version
                or existing.get("validator_version") != self._validator_version()
                or existing.get("runtime_test_certificate_id")
                != str(current_certificate["certificate_id"])
            ):
                raise ValueError("migration_record_authority_conflict")
            current_errors = self._project_state_errors(
                namespace, project_id, hub_before, ledger_before
            )
            if current_errors:
                raise ValueError(
                    "migration_committed_state_invalid: "
                    + "; ".join(current_errors[:20])
                )
            return ToolResponse(
                status="already_migrated",
                summary=f"é¡¹ç›®è¿ç§»å·²å®Œæˆä¸”å¹‚ç­‰ï½œ{resolved_migration_id}",
                project_id=project_id,
                work_order_id=work_order_id,
                data={"migration": existing, "authored_artifacts_modified": False},
            )
        if existing and existing.get("status") == "rolled_back":
            existing_id = str(existing.get("migration_id") or "")
            self._assert_project_migration_record_images(
                existing,
                project_id=project_id,
                migration_id=existing_id,
            )
            self._assert_rolled_back_migration_candidate_matches(
                existing,
                migration_id=existing_id,
            )
            current_certificate = self._require_runtime_test_certificate(
                namespace, project_id
            )
            current_hub_sha = self.store.project_state_sha256(hub_before)
            current_ledger_sha = self.store.project_state_sha256(ledger_before)
            if (
                existing.get("work_order_id") != work_order_id
                or existing.get("migration_key") != migration_key
                or existing.get("from_version") != key_from_version
                or existing.get("target_version") != target_version
                or existing_id != resolved_migration_id
                or existing.get("ruleset_version") != self._ruleset_version
                or existing.get("validator_version") != self._validator_version()
                or existing.get("runtime_test_certificate_id")
                != str(current_certificate["certificate_id"])
                or current_hub_sha
                not in {
                    existing.get("hub_before_sha256"),
                    existing.get("hub_after_sha256"),
                }
                or current_ledger_sha
                not in {
                    existing.get("ledger_before_sha256"),
                    existing.get("ledger_after_sha256"),
                }
            ):
                raise ValueError("migration_record_authority_conflict")
            replay_errors = self._project_migration_candidate_errors(
                namespace,
                project_id,
                hub_before=existing["hub_before"],
                ledger_before=existing["ledger_before"],
                candidate_hub=existing["hub_after"],
                candidate_ledger=existing["ledger_after"],
                migration_id=existing_id,
                signed_journal=existing,
            )
            if replay_errors:
                raise ValueError(
                    "migration_candidate_invalid: "
                    + "; ".join(replay_errors[:20])
                )
            replayed = self.store.commit_project_state_migration(
                namespace,
                project_id,
                migration_id=existing_id,
                migration_key=migration_key,
                work_order_id=work_order_id,
                from_version=key_from_version,
                target_version=target_version,
                hub_before=existing["hub_before"],
                ledger_before=existing["ledger_before"],
                hub_after=existing["hub_after"],
                ledger_after=existing["ledger_after"],
                runtime_test_certificate_id=str(
                    current_certificate["certificate_id"]
                ),
                runtime_test_certificate_key=self._runtime_certificate_key(),
                ruleset_version=self._ruleset_version,
                validator_version=self._validator_version(),
                changed_paths=list(existing["changed_paths"]),
            )
            replayed_hub = self.store.load_hub(namespace, project_id)
            replayed_ledger = self.store.load_ledger(namespace, project_id)
            replayed_errors = self._project_state_errors(
                namespace,
                project_id,
                replayed_hub,
                replayed_ledger,
            )
            if replayed_errors:
                raise RuntimeError(
                    "migration_committed_state_invalid: "
                    + "; ".join(replayed_errors[:20])
                )
            return ToolResponse(
                status="migrated",
                summary=(
                    f"é¡¹ç›®æŽ§åˆ¶çŠ¶æ€è¿ç§»å·²æŒ‰å›žæ»šæ—¥å¿—ç²¾ç¡®é‡æ‰§è¡Œï½œ"
                    f"{key_from_version}â†’{target_version}"
                ),
                project_id=project_id,
                work_order_id=work_order_id,
                data={
                    "migration": replayed,
                    "authored_artifacts_modified": False,
                },
            )
        if expected_current_version and current_version != expected_current_version:
            raise ValueError(
                "migration_current_version_mismatch: "
                f"expected {expected_current_version}, found {current_version}"
            )
        if current_version == target_version:
            current_errors = self._project_state_errors(
                namespace, project_id, hub_before, ledger_before
            )
            if not current_errors:
                return ToolResponse(
                    status="already_current",
                    summary=f"é¡¹ç›®æŽ§åˆ¶çŠ¶æ€å·²æ˜¯{target_version}ä¸”éªŒè¯é€šè¿‡",
                    project_id=project_id,
                    work_order_id=work_order_id,
                    data={
                        "target_version": target_version,
                        "authored_artifacts_modified": False,
                    },
                )

        migration_timestamp = utc_stamp()
        candidate_hub, hub_changes = project_agent.migrate_hub_to_current(
            hub_before,
            timestamp=migration_timestamp,
            migration_id=resolved_migration_id,
        )
        candidate_ledger, ledger_changes = storyboard_agent.migrate_ledger_to_current(
            ledger_before,
            timestamp=migration_timestamp,
            migration_id=resolved_migration_id,
        )
        validation_errors = self._project_migration_candidate_errors(
            namespace,
            project_id,
            hub_before=hub_before,
            ledger_before=ledger_before,
            candidate_hub=candidate_hub,
            candidate_ledger=candidate_ledger,
            migration_id=resolved_migration_id,
        )
        report = {
            "migration_id": resolved_migration_id,
            "migration_key": migration_key,
            "from_version": current_version,
            "target_version": target_version,
            "hub_changed_paths": hub_changes,
            "ledger_changed_paths": ledger_changes,
            "candidate_valid": not validation_errors,
            "candidate_errors": validation_errors,
            "before": {
                "hub_sha256": self.store.project_state_sha256(hub_before),
                "ledger_sha256": self.store.project_state_sha256(ledger_before),
            },
            "after": {
                "hub_sha256": self.store.project_state_sha256(candidate_hub),
                "ledger_sha256": self.store.project_state_sha256(candidate_ledger),
            },
            "preserved_counts": {
                "artifacts": len(self.store.list_artifacts(namespace, project_id)),
                "work_orders": len(hub_before.get("work_orders", [])),
                "issues": len(ledger_before.get("issues", [])),
                "events": len(ledger_before.get("events", [])),
            },
            "authored_artifacts_modified": False,
        }
        if action == "plan":
            return ToolResponse(
                status="migration_plan_ready" if not validation_errors else "blocked",
                summary=(
                    f"è¿ç§»é¢„æ¼”å®Œæˆï½œ{current_version}â†’{target_version}ï½œå€™é€‰æœ‰æ•ˆ"
                    if not validation_errors
                    else f"è¿ç§»é¢„æ¼”é˜»å¡žï½œå€™é€‰é”™è¯¯{len(validation_errors)}é¡¹"
                ),
                project_id=project_id,
                work_order_id=work_order_id,
                data={"migration_plan": report},
            )
        if validation_errors:
            raise ValueError(
                "migration_candidate_invalid: " + "; ".join(validation_errors[:20])
            )
        runtime_certificate = self._require_runtime_test_certificate(namespace, project_id)
        record = self.store.commit_project_state_migration(
            namespace,
            project_id,
            migration_id=resolved_migration_id,
            migration_key=migration_key,
            work_order_id=work_order_id,
            from_version=current_version,
            target_version=target_version,
            hub_before=hub_before,
            ledger_before=ledger_before,
            hub_after=candidate_hub,
            ledger_after=candidate_ledger,
            runtime_test_certificate_id=str(runtime_certificate["certificate_id"]),
            runtime_test_certificate_key=self._runtime_certificate_key(),
            ruleset_version=self._ruleset_version,
            validator_version=self._validator_version(),
            changed_paths=hub_changes + ledger_changes,
        )
        committed_hub = self.store.load_hub(namespace, project_id)
        committed_ledger = self.store.load_ledger(namespace, project_id)
        committed_errors = self._project_state_errors(
            namespace,
            project_id,
            committed_hub,
            committed_ledger,
        )
        if committed_errors:
            raise RuntimeError(
                "migration_committed_state_invalid: "
                + "; ".join(committed_errors[:20])
            )
        return ToolResponse(
            status="migrated",
            summary=f"é¡¹ç›®æŽ§åˆ¶çŠ¶æ€è¿ç§»å®Œæˆï½œ{current_version}â†’{target_version}ï½œåŽ†å²åˆ†é•œæœªæ”¹å†™",
            project_id=project_id,
            work_order_id=work_order_id,
            data={"migration": record, "migration_report": report},
        )

    @staticmethod
    def _handoff_record_projection(value: dict[str, Any]) -> dict[str, Any]:
        """Drop project-record envelope fields before embedding in the hub."""

        return {
            key: copy.deepcopy(item)
            for key, item in value.items()
            if key not in {"record_id", "project_id", "integrity_hmac"}
        }

    @classmethod
    def _hub_handoff_matches_signed_record(
        cls,
        hub_handoff: dict[str, Any],
        signed_handoff: dict[str, Any],
    ) -> bool:
        """Accept only the two historically emitted exact Hub shapes.

        Current writers embed the envelope-free projection.  A legacy
        production finalizer instead copied the complete signed handoff into
        the Hub, including ``record_id``, ``project_id`` and
        ``integrity_hmac``.  Those rows are safe to consume only when every
        byte represented by the parsed JSON still equals the signed record;
        no other missing, additional or changed field is tolerated.
        """

        expected_projection = cls._handoff_record_projection(signed_handoff)
        return hub_handoff == expected_projection or hub_handoff == signed_handoff

    def _assert_handoff_recovery_work_order(
        self,
        namespace: str,
        work_order: dict[str, Any],
        *,
        request_session_id: str | None,
    ) -> None:
        self._assert_work_order_session(
            namespace, work_order, request_session_id
        )
        if work_order.get("operation") != "rule_improvement":
            raise ValueError(
                "handoff_recovery_authority_required: use an explicit "
                "rule_improvement production work order"
            )
        if (
            work_order.get("mode") != "execute"
            or work_order.get("authorization") != "explicit_execute"
            or work_order.get("server_actor_role") != "production"
        ):
            raise ValueError(
                "project_migration_authority_required: use an explicit rule_improvement "
                "or version_management production work order"
            )
        self._assert_work_order_write_ready(work_order)

    def _assert_handoff_recovery_authority(
        self,
        namespace: str,
        work_order: dict[str, Any],
        *,
        gap_id: str,
        handoff_ids: list[str],
        phase: str,
        request_session_id: str | None,
    ) -> None:
        self._assert_handoff_recovery_work_order(
            namespace,
            work_order,
            request_session_id=request_session_id,
        )
        raw_targets = [str(value) for value in work_order.get("targets", [])]
        if len(raw_targets) != len(set(raw_targets)):
            raise ValueError(
                "handoff_recovery_scope_mismatch: work order targets must not contain duplicates"
            )
        targets = set(raw_targets)
        required = {
            gap_id,
            *handoff_ids,
            f"controlled_phase:{phase}",
        }
        if targets != required:
            missing = sorted(required - targets)
            extra = sorted(targets - required)
            raise ValueError(
                "handoff_recovery_scope_mismatch: work order must target the "
                f"exact gap and handoffs; missing {missing}; extra {extra}"
            )

    @staticmethod
    def _require_capability_gap(hub: dict[str, Any], gap_id: str) -> dict[str, Any]:
        gap = next(
            (
                row
                for row in hub.get("capability_gaps", [])
                if isinstance(row, dict) and row.get("gap_id") == gap_id
            ),
            None,
        )
        if gap is None:
            raise ValueError(
                "handoff_recovery_gap_required: gap_id is not registered in this project"
            )
        return gap

    def _production_preflight_certificate_value(
        self,
        *,
        certificate_id: str,
        artifact_id: str,
        canonical_artifact_id: str,
        cache_components: dict[str, str],
        report: dict[str, Any],
        work_order_id: str,
        production_work_order_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build, but do not persist, one production-only certificate."""

        cache_key = self._cache_key_from_components(cache_components)
        clean = not report.get("issues")
        value: dict[str, Any] = {
            "certificate_id": certificate_id,
            "certificate_type": AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            "work_order_id": work_order_id,
            "production_work_order_id": production_work_order_id,
            "artifact_id": artifact_id,
            "canonical_artifact_id": canonical_artifact_id,
            "canonical_sha256": cache_components["canonical"],
            "source_fingerprint": cache_components["source"],
            "boundary_hash": cache_components["boundary"],
            "ruleset_version": cache_components["ruleset"],
            "validator_version": cache_components["validator"],
            "audit_class": AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            "cache_key": cache_key,
            "cache_components": copy.deepcopy(cache_components),
            "checked_shot_count": int(report.get("shot_count", 0)),
            "report_sha256": sha256_bytes(_stable_json_bytes(report)),
            "report": copy.deepcopy(report),
            "preflight_status": (
                PRODUCTION_PREFLIGHT_PASS
                if clean
                else PRODUCTION_PREFLIGHT_FAIL
            ),
            "preflight_issue_count": len(report.get("issues", [])),
            "independent_audit_status": "PENDING_AUDIT",
        }
        if extra:
            value.update(copy.deepcopy(extra))
        return value

    @staticmethod
    def _git_blob_sha1(payload: bytes) -> str:
        header = f"blob {len(payload)}\0".encode("ascii")
        return hashlib.sha1(header + payload).hexdigest()

    @classmethod
    def _frozen_v053_validator_provenance(
        cls, *, ruleset_version: str
    ) -> tuple[dict[str, Any], Path]:
        frozen_root = Path(frozen_v053_validator.__file__).resolve().parent
        d884_profile = ruleset_version.endswith(":d8841a2f52d86bed")
        paths = {
            "init": frozen_root / "__init__.py",
            "validator": frozen_root / "validate_storyboard.py",
            "rules": frozen_root
            / (
                "storyboard_runtime_rules_d884.json"
                if d884_profile
                else "storyboard_runtime_rules.json"
            ),
            "error_catalog": frozen_root / "error_catalog.json",
        }
        if d884_profile:
            paths["manifest"] = frozen_root / "plugin_manifest_d884.json"
        payloads = {name: path.read_bytes() for name, path in paths.items()}
        expected = {
            "init": (
                FROZEN_V053_INIT_BLOB,
                FROZEN_V053_INIT_SHA256,
            ),
            "validator": (
                FROZEN_V053_VALIDATOR_BLOB,
                FROZEN_V053_VALIDATOR_SHA256,
            ),
            "rules": (FROZEN_V053_RULES_BLOB, FROZEN_V053_RULES_SHA256),
            "error_catalog": (
                FROZEN_V053_ERROR_CATALOG_BLOB,
                FROZEN_V053_ERROR_CATALOG_SHA256,
            ),
        }
        if d884_profile:
            expected["rules"] = (
                FROZEN_D884_RULES_BLOB,
                FROZEN_D884_RULES_SHA256,
            )
            expected["manifest"] = (
                FROZEN_D884_MANIFEST_BLOB,
                FROZEN_D884_MANIFEST_SHA256,
            )
        for name, payload in payloads.items():
            expected_blob, expected_sha256 = expected[name]
            if (
                cls._git_blob_sha1(payload) != expected_blob
                or sha256_bytes(payload) != expected_sha256
            ):
                raise RuntimeError(
                    f"frozen_v053_validator_integrity_failed: {name}"
                )
        provenance = {
            "plugin_version": "0.5.2" if d884_profile else "0.5.3",
            "source_snapshot_kind": (
                "sealed_local_package"
                if d884_profile
                else "sealed_git_snapshot_export"
            ),
            "source_lineage_runtime_verified": False,
            "source_commit": None if d884_profile else FROZEN_V053_SOURCE_COMMIT,
            "source_tree": None if d884_profile else FROZEN_V053_SOURCE_TREE,
            "init_git_blob": FROZEN_V053_INIT_BLOB,
            "init_sha256": FROZEN_V053_INIT_SHA256,
            "validator_git_blob": FROZEN_V053_VALIDATOR_BLOB,
            "validator_sha256": FROZEN_V053_VALIDATOR_SHA256,
            "rules_profile": ruleset_version,
            "rules_git_blob": (
                FROZEN_D884_RULES_BLOB
                if d884_profile
                else FROZEN_V053_RULES_BLOB
            ),
            "rules_sha256": (
                FROZEN_D884_RULES_SHA256
                if d884_profile
                else FROZEN_V053_RULES_SHA256
            ),
            "error_catalog_git_blob": FROZEN_V053_ERROR_CATALOG_BLOB,
            "error_catalog_sha256": FROZEN_V053_ERROR_CATALOG_SHA256,
            "manifest_git_blob": (
                FROZEN_D884_MANIFEST_BLOB if d884_profile else None
            ),
            "manifest_sha256": (
                FROZEN_D884_MANIFEST_SHA256 if d884_profile else None
            ),
            "composite_validator_version": (
                FROZEN_V053_VALIDATOR_COMPOSITE
            ),
        }
        return provenance, paths["rules"]

    @classmethod
    def _frozen_v053_production_validation(
        cls, storyboard: dict[str, Any], *, ruleset_version: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        provenance, rules_path = cls._frozen_v053_validator_provenance(
            ruleset_version=ruleset_version
        )
        report = frozen_v053_validator.validate_project(
            copy.deepcopy(storyboard),
            mode="audit",
            rules_path=str(rules_path),
        )
        if (
            report.get("status") != "pass"
            or report.get("issues") != []
            or int(report.get("error_count") or 0) != 0
        ):
            raise ValueError(
                "historical_preflight_validation_failed: frozen v0.5.3 "
                f"validator reported {len(report.get('issues', []))} issue(s)"
            )
        if report.get("validator_version") != (
            "V16.3-148-R10-StableRuntime"
        ):
            raise RuntimeError(
                "frozen_v053_validator_integrity_failed: report version"
            )
        return report, provenance

    @staticmethod
    def _validation_error_histogram(
        report: dict[str, Any],
    ) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    str(item.get("error_code") or "UNKNOWN")
                    for item in report.get("issues", [])
                    if isinstance(item, dict)
                ).items()
            )
        )

    def _sealed_historical_current_validation_profile(
        self,
        *,
        handoff_id: str,
        storyboard: dict[str, Any],
        report: dict[str, Any],
    ) -> bool:
        """Match one immutable handoff-specific current-validation seal."""

        profile = CONTROLLED_V053_CURRENT_VALIDATION_PROFILES.get(
            handoff_id
        )
        if not isinstance(profile, dict):
            return False
        issues = report.get("issues")
        error_count = report.get("error_count")
        warning_count = report.get("warning_count")
        report_shot_count = report.get("shot_count")
        profile_error_count = profile.get("error_count")
        profile_warning_count = profile.get("warning_count")
        profile_shot_count = profile.get("shot_count")
        profile_histogram = profile.get("error_histogram")
        strict_report_counts = all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (error_count, warning_count, report_shot_count)
        )
        strict_profile_counts = all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (
                profile_error_count,
                profile_warning_count,
                profile_shot_count,
            )
        )
        if (
            profile.get("handoff_id") != handoff_id
            or report.get("status") != "fail"
            or not isinstance(issues, list)
            or not issues
            or not all(isinstance(issue, dict) for issue in issues)
            or not strict_report_counts
            or not strict_profile_counts
            or not isinstance(profile_histogram, dict)
            or not profile_histogram
            or not all(
                isinstance(code, str)
                and bool(code)
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count > 0
                for code, count in profile_histogram.items()
            )
            or error_count != len(issues)
            or error_count != profile_error_count
            or warning_count != 0
            or warning_count != profile_warning_count
            or report_shot_count != profile_shot_count
            or len(storyboard.get("shots", [])) != profile_shot_count
            or sum(profile_histogram.values()) != profile_error_count
            or self._validation_error_histogram(report)
            != profile_histogram
            or canonical_storyboard_sha256(storyboard)
            != profile.get("canonical_sha256")
            or sha256_bytes(_stable_json_bytes(report))
            != profile.get("current_report_sha256")
            or self._validator_version()
            != profile.get("current_validator_composite")
            or self._validator_fingerprint
            != profile.get("current_validator_fingerprint")
        ):
            return False
        return True

    def _historical_production_validation_replay(
        self,
        *,
        handoff_id: str,
        handoff: dict[str, Any],
        storyboard: dict[str, Any],
        current_report: dict[str, Any],
        old_certificate: dict[str, Any],
        old_fingerprints: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not current_report.get("issues"):
            raise ValueError(
                "historical_preflight_replay_not_required: current "
                "validation already passes"
            )
        if not self._sealed_historical_current_validation_profile(
            handoff_id=handoff_id,
            storyboard=storyboard,
            report=current_report,
        ):
            raise ValueError(
                "historical_preflight_current_issue_scope_mismatch: "
                "current validation does not exactly match the sealed "
                "handoff profile"
            )
        expected_profile = FROZEN_V053_HANDOFF_RULESET_PROFILES.get(
            handoff_id
        )
        if expected_profile is None:
            raise ValueError(
                "historical_preflight_scope_mismatch: handoff has no "
                "sealed rules profile"
            )
        expected_ruleset, expected_rules_fingerprint = expected_profile
        original_ruleset = str(
            old_certificate.get("ruleset_version") or ""
        )
        cache_components = old_certificate.get("cache_components")
        handoff_cache_components = handoff.get("cache_components")
        if (
            not isinstance(cache_components, dict)
            or cache_components != handoff_cache_components
            or original_ruleset != expected_ruleset
            or handoff.get("ruleset_version") != expected_ruleset
            or cache_components.get("ruleset") != expected_ruleset
            or old_fingerprints.get("rules")
            != expected_rules_fingerprint
            or handoff.get("fingerprints", {}).get("rules")
            != expected_rules_fingerprint
            or handoff.get("validator_version")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or old_certificate.get("validator_version")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or cache_components.get("validator")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or old_fingerprints.get("validator")
            != FROZEN_V053_VALIDATOR_SHA256
            or handoff.get("fingerprints", {}).get("validator")
            != FROZEN_V053_VALIDATOR_SHA256
        ):
            raise ValueError(
                "historical_preflight_origin_mismatch: sealed handoff "
                "rules/validator profile differs"
            )
        historical_report, provenance = (
            self._frozen_v053_production_validation(
                storyboard, ruleset_version=expected_ruleset
            )
        )
        historical_report_sha256 = sha256_bytes(
            _stable_json_bytes(historical_report)
        )
        original_report = old_certificate.get("report")
        if (
            not isinstance(original_report, dict)
            or original_report != historical_report
            or old_certificate.get("report_sha256")
            != historical_report_sha256
        ):
            raise ValueError(
                "historical_preflight_origin_mismatch: frozen replay does "
                "not exactly match the signed original production evidence"
            )
        current_report_sha256 = sha256_bytes(
            _stable_json_bytes(current_report)
        )
        issue_count = len(current_report.get("issues", []))
        replay: dict[str, Any] = {
            "spec": "controlled-historical-production-preflight-replay-v1",
            "basis": "legacy_frozen_validator_replay",
            "historical_contract_status": (
                HISTORICAL_PRODUCTION_PREFLIGHT_PASS
            ),
            "historical_contract_issue_count": 0,
            "historical_report_sha256": historical_report_sha256,
            "current_validation_status": CURRENT_VALIDATION_FAIL,
            "current_validation_issue_count": issue_count,
            "current_report_sha256": current_report_sha256,
            "current_validation_error_histogram": (
                self._validation_error_histogram(current_report)
            ),
            "frozen_validator_provenance": provenance,
            "current_validator_version": self._validator_version(),
            "current_production_pass_claimed": False,
            "current_preflight_cache_reusable": False,
            "conclusion_reusable_for_independent_audit": False,
            "requires_full_current_independent_audit": True,
        }
        replay["proof_sha256"] = sha256_bytes(
            _stable_json_bytes(replay)
        )
        return replay, historical_report

    def _historical_preflight_cache_components(
        self,
        *,
        context: dict[str, Any],
        replay: dict[str, Any],
        runtime_test_certificate_id: str,
    ) -> dict[str, str]:
        components = copy.deepcopy(context["cache_components"])
        components.update(
            {
                "ruleset": str(
                    context["old_certificate"].get("ruleset_version") or ""
                ),
                "validator": FROZEN_V053_VALIDATOR_COMPOSITE,
                "audit_class": AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT,
                "runtime_test_certificate_id": runtime_test_certificate_id,
                "scope": sha256_bytes(
                    _stable_json_bytes(
                        {
                            "old_handoff_id": context["handoff"][
                                "handoff_id"
                            ],
                            "old_handoff_sha256": context[
                                "old_record_sha256"
                            ],
                            "production_validation_replay_sha256": replay[
                                "proof_sha256"
                            ],
                        }
                    )
                ),
            }
        )
        return components

    @classmethod
    def _historical_preflight_cache_key(
        cls, components: dict[str, str]
    ) -> str:
        current_domain_key = cls._cache_key_from_components(components)
        _prefix, digest = current_domain_key.split("-", 1)
        return f"HISTCACHE-{digest}"

    def _historical_preflight_attestation_value(
        self,
        *,
        certificate_id: str,
        context: dict[str, Any],
        replay: dict[str, Any],
        historical_report: dict[str, Any],
        work_order_id: str,
        runtime_test_certificate_id: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_components = self._historical_preflight_cache_components(
            context=context,
            replay=replay,
            runtime_test_certificate_id=runtime_test_certificate_id,
        )
        value: dict[str, Any] = {
            "certificate_id": certificate_id,
            "certificate_type": (
                AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT
            ),
            "evidence_type": (
                "controlled_historical_production_preflight_attestation"
            ),
            "work_order_id": work_order_id,
            "production_work_order_id": context["handoff"][
                "work_order_id"
            ],
            "artifact_id": context["artifact_id"],
            "canonical_artifact_id": context["canonical_artifact_id"],
            "canonical_sha256": context["canonical_hash"],
            "source_fingerprint": context["source_fingerprint"],
            "boundary_hash": context["boundary_hash"],
            "ruleset_version": cache_components["ruleset"],
            "validator_version": cache_components["validator"],
            "audit_class": (
                AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT
            ),
            "cache_key": self._historical_preflight_cache_key(
                cache_components
            ),
            "cache_components": cache_components,
            "checked_shot_count": int(
                historical_report.get("shot_count", 0)
            ),
            "report_sha256": sha256_bytes(
                _stable_json_bytes(historical_report)
            ),
            "report": copy.deepcopy(historical_report),
            "preflight_status": HISTORICAL_PRODUCTION_PREFLIGHT_PASS,
            "preflight_issue_count": 0,
            "historical_validation_report": copy.deepcopy(
                historical_report
            ),
            "current_validation_report": copy.deepcopy(
                context["current_validation_report"]
            ),
            "production_validation_replay": copy.deepcopy(replay),
            "current_validation_status": CURRENT_VALIDATION_FAIL,
            "current_validation_issue_count": replay[
                "current_validation_issue_count"
            ],
            "current_production_pass_claimed": False,
            "current_preflight_cache_reusable": False,
            "conclusion_reusable_for_independent_audit": False,
            "requires_full_current_independent_audit": True,
            "independent_audit_status": "PENDING_AUDIT",
        }
        if extra:
            value.update(copy.deepcopy(extra))
        return value

    def _controlled_production_revalidation_trace_value(
        self,
        *,
        trace_id: str,
        phase: str,
        work_order: dict[str, Any],
        production_work_order_id: str,
        gap_id: str,
        plan_sha256: str,
        artifact_id: str,
        artifact_size_bytes: int,
        fingerprints: dict[str, str],
        shot_count: int,
        authority_record_id: str,
        revalidation_measurement: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = str(revalidation_measurement.get("started_at") or "")
        ended_at = str(revalidation_measurement.get("ended_at") or "")
        duration_ms = int(revalidation_measurement.get("duration_ms") or 0)
        if not started_at or not ended_at or duration_ms <= 0:
            raise ValueError(
                "controlled_production_revalidation_measurement_required"
            )
        value = {
            "telemetry_id": trace_id,
            "trace_id": trace_id,
            "operation": "recover_storyboard_handoff",
            "phase": phase,
            "window_role": "production",
            "task_type": "production_preflight",
            "measurement_scope": "server_only",
            "unmeasured_stages": [],
            "started_at": started_at,
            "ended_at": ended_at,
            "wall_duration_ms": duration_ms,
            "work_order_id": str(work_order.get("work_order_id") or ""),
            "production_work_order_id": production_work_order_id,
            "gap_id": gap_id,
            "plan_sha256": plan_sha256,
            "authority_record_id": authority_record_id,
            "artifact_id": artifact_id,
            "input_fingerprints": copy.deepcopy(fingerprints),
            "input_payload_bytes": max(0, int(artifact_size_bytes)),
            "output_payload_bytes": 0,
            "affected_shot_count": 0,
            "preserved_semantic_count": max(0, int(shot_count)),
            "schema_version": COMPACT_SCHEMA_VERSION,
            "schema_profile": COMPACT_SCHEMA_PROFILE,
            "materializer_contract": COMPACT_MATERIALIZER_CONTRACT,
            "materialization_passes": 0,
            **NO_DUPLICATE_GENERATION_EVIDENCE,
            "stages": [
                {
                    "stage": "validation",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "duration_ms": duration_ms,
                    "tool_call_count": 0,
                    "source_character_count": None,
                    "payload_bytes": max(0, int(artifact_size_bytes)),
                    "shot_count": max(0, int(shot_count)),
                    "batch_count": 0,
                    "cache_status": "miss",
                    "cache_reason": (
                        "current controlled production revalidation; no generation"
                    ),
                    "retry_count": 0,
                    "error_status": "none",
                }
            ],
        }
        self._validate_schema_record(
            self._project_schema, "stage_timing_trace", value
        )
        return value

    @staticmethod
    def _legacy_handoff_preflight_fields_missing(
        handoff: dict[str, Any],
    ) -> bool:
        return all(
            handoff.get(field) is None or handoff.get(field) == ""
            for field in (
                "production_preflight_status",
                "production_preflight_issue_count",
                "independent_audit_status",
            )
        )

    @staticmethod
    def _clean_pending_preflight_fields(value: dict[str, Any]) -> bool:
        issue_count = value.get("production_preflight_issue_count")
        return (
            value.get("production_preflight_status")
            == PRODUCTION_PREFLIGHT_PASS
            and isinstance(issue_count, int)
            and not isinstance(issue_count, bool)
            and issue_count == 0
            and value.get("independent_audit_status") == "PENDING_AUDIT"
        )

    @classmethod
    def _legacy_certificate_evidence_invalid(
        cls,
        certificate: dict[str, Any],
        handoff: dict[str, Any],
        *,
        recovery_successor: bool = False,
    ) -> bool:
        """Reject failed, ambiguous, or misbound legacy preflight evidence."""

        report = certificate.get("report")
        if not isinstance(report, dict):
            return True
        issues = report.get("issues")
        if not isinstance(issues, list) or issues:
            return True
        if certificate.get("report_sha256") != sha256_bytes(
            _stable_json_bytes(report)
        ):
            return True
        if not cls._valid_cache_identity(
            certificate, AUDIT_CLASS_PRODUCTION_PREFLIGHT
        ):
            return True
        if certificate.get("cache_components") != handoff.get(
            "cache_components"
        ):
            return True
        certificate_projection_proof = certificate.get(
            "workbook_projection_revalidation"
        )
        handoff_projection_proof = handoff.get(
            "workbook_projection_revalidation"
        )
        if (
            certificate_projection_proof is not None
            or handoff_projection_proof is not None
        ) and certificate_projection_proof != handoff_projection_proof:
            return True
        for field in (
            "certificate_id",
            "work_order_id",
            "artifact_id",
            "canonical_artifact_id",
            "canonical_sha256",
            "source_fingerprint",
            "boundary_hash",
            "ruleset_version",
            "validator_version",
            "audit_class",
            "cache_key",
        ):
            if field == "artifact_id":
                expected = handoff.get("storyboard_artifact_id")
            elif field == "work_order_id" and recovery_successor:
                expected = handoff.get("recovery_work_order_id")
            else:
                expected = handoff.get(field)
            if certificate.get(field) != expected:
                return True
        if recovery_successor and certificate.get(
            "production_work_order_id"
        ) != handoff.get("work_order_id"):
            return True
        if (
            certificate.get("certificate_type")
            != AUDIT_CLASS_PRODUCTION_PREFLIGHT
        ):
            return True
        status = certificate.get("preflight_status")
        if status is not None and status != "" and status != PRODUCTION_PREFLIGHT_PASS:
            return True
        issue_count = certificate.get("preflight_issue_count")
        if issue_count is not None and issue_count != "":
            if (
                not isinstance(issue_count, int)
                or isinstance(issue_count, bool)
                or issue_count != 0
            ):
                return True
        independent_status = certificate.get("independent_audit_status")
        return (
            independent_status is not None
            and independent_status != ""
            and independent_status != "PENDING_AUDIT"
        )

    @classmethod
    def _historical_recovery_certificate_evidence_invalid(
        cls,
        certificate: dict[str, Any],
        handoff: dict[str, Any],
    ) -> bool:
        """Validate one non-reusable historical certificate on a successor."""

        report = certificate.get("report")
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("issues"), list)
            or report.get("issues")
            or certificate.get("report_sha256")
            != sha256_bytes(_stable_json_bytes(report))
            or not cls._valid_historical_preflight_identity(certificate)
            or not cls._valid_historical_preflight_identity(handoff)
            or certificate.get("cache_components")
            != handoff.get("cache_components")
            or certificate.get("production_validation_replay")
            != handoff.get("production_validation_replay")
            or certificate.get("work_order_id")
            != handoff.get("recovery_work_order_id")
            or certificate.get("production_work_order_id")
            != handoff.get("work_order_id")
        ):
            return True
        for field in (
            "certificate_id",
            "canonical_artifact_id",
            "canonical_sha256",
            "source_fingerprint",
            "boundary_hash",
            "ruleset_version",
            "validator_version",
            "audit_class",
            "cache_key",
            "current_validation_status",
            "current_validation_issue_count",
            "current_production_pass_claimed",
            "current_preflight_cache_reusable",
            "conclusion_reusable_for_independent_audit",
            "requires_full_current_independent_audit",
            "independent_audit_status",
        ):
            if certificate.get(field) != handoff.get(field):
                return True
        if certificate.get("artifact_id") != handoff.get(
            "storyboard_artifact_id"
        ):
            return True
        certificate_projection = certificate.get(
            "workbook_projection_revalidation"
        )
        handoff_projection = handoff.get(
            "workbook_projection_revalidation"
        )
        return (
            certificate_projection is not None
            or handoff_projection is not None
        ) and certificate_projection != handoff_projection

    def _verified_recovery_successor(
        self,
        namespace: str,
        project_id: str,
        *,
        hub: dict[str, Any],
        handoff: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the signed immediate recovery journal for a prepared successor.

        A successor may be recovered again after a later deployment changes
        only runtime dependencies. The immediate committed journal seals the
        predecessor, certificate and production trace, so no mutable label is
        accepted as recovery provenance.
        """

        recovery_id = str(handoff.get("recovery_id") or "")
        if not recovery_id:
            return None
        old_handoff_id = str(handoff.get("supersedes_handoff_id") or "")
        handoff_id = str(handoff.get("handoff_id") or "")
        if not old_handoff_id or not handoff_id:
            raise ValueError(
                f"handoff_recovery_lineage_invalid: {handoff_id or '<unknown>'} "
                "has incomplete successor identity"
            )
        journal = self.store.completed_handoff_recovery(
            namespace,
            project_id,
            recovery_id=recovery_id,
            old_handoff_id=old_handoff_id,
            new_handoff_id=handoff_id,
        )
        if journal is None:
            raise ValueError(
                f"handoff_recovery_lineage_invalid: {handoff_id} recovery is not committed"
            )
        journal_new = journal.get("new_after")
        if not isinstance(journal_new, dict):
            raise ValueError(
                f"handoff_recovery_lineage_invalid: {handoff_id} successor after-image is missing"
            )
        if (
            journal.get("work_order_id")
            != handoff.get("recovery_work_order_id")
            or journal.get("gap_id") != handoff.get("recovery_gap_id")
            or journal.get("trace_id") != handoff.get("recovery_trace_id")
            or handoff.get("recovery_plan_sha256")
            != journal_new.get("recovery_plan_sha256")
            or handoff.get("status") != "prepared"
            or handoff.get("independent_audit_status") != "PENDING_AUDIT"
        ):
            raise ValueError(
                f"handoff_recovery_lineage_invalid: {handoff_id} signed recovery bindings differ"
            )
        recovery_order = _find_work_order(
            hub, str(handoff.get("recovery_work_order_id") or "")
        )
        if (
            recovery_order.get("server_actor_role") != "production"
            or recovery_order.get("operation") != "rule_improvement"
            or recovery_order.get("mode") != "execute"
            or recovery_order.get("authorization") != "explicit_execute"
        ):
            raise ValueError(
                f"handoff_recovery_lineage_invalid: {handoff_id} recovery authority differs"
            )
        return journal

    def _assert_historical_origin_evidence(
        self,
        namespace: str,
        project_id: str,
        *,
        handoff_id: str,
        handoff: dict[str, Any],
        certificate: dict[str, Any],
        storyboard: dict[str, Any],
    ) -> dict[str, Any]:
        """Fail closed unless the original legacy PASS chain is complete."""

        report = certificate.get("report")
        fingerprints = handoff.get("fingerprints")
        components = certificate.get("cache_components")
        shot_count = len(storyboard.get("shots", []))
        def exact_int(value: Any, expected: int) -> bool:
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value == expected
            )

        profile = FROZEN_V053_HANDOFF_RULESET_PROFILES.get(handoff_id)
        if (
            profile is None
            or not isinstance(report, dict)
            or not isinstance(fingerprints, dict)
            or not isinstance(components, dict)
            or components != handoff.get("cache_components")
            or certificate.get("result") != "pass"
            or not exact_int(certificate.get("issue_count"), 0)
            or not exact_int(
                certificate.get("checked_shot_count"), shot_count
            )
            or handoff.get("production_result") != "pass"
            or not exact_int(handoff.get("issue_count"), 0)
            or not exact_int(handoff.get("checked_shot_count"), shot_count)
            or report.get("status") != "pass"
            or report.get("issues") != []
            or not exact_int(report.get("error_count"), 0)
            or not exact_int(report.get("warning_count"), 0)
            or not exact_int(report.get("shot_count"), shot_count)
            or handoff.get("ruleset_version") != profile[0]
            or certificate.get("ruleset_version") != profile[0]
            or components.get("ruleset") != profile[0]
            or fingerprints.get("rules") != profile[1]
            or handoff.get("validator_version")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or certificate.get("validator_version")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or components.get("validator")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or fingerprints.get("validator")
            != FROZEN_V053_VALIDATOR_SHA256
            or components.get("canonical")
            != handoff.get("canonical_sha256")
            or components.get("source")
            != handoff.get("source_fingerprint")
            or components.get("boundary")
            != handoff.get("boundary_hash")
            or components.get("artifact")
            != fingerprints.get("artifact_raw")
            or components.get("parser") != fingerprints.get("parser")
            or components.get("schema") != fingerprints.get("schema")
            or components.get("runtime") != fingerprints.get("runtime")
            or components.get("scripts") != fingerprints.get("scripts")
            or components.get("dependencies")
            != fingerprints.get("dependencies_environment")
            or components.get("anchors")
            != fingerprints.get("project_anchors")
            or components.get("target_model")
            != fingerprints.get("target_model_contract")
            or fingerprints.get("canonical")
            != handoff.get("canonical_sha256")
            or fingerprints.get("artifact_semantic")
            != handoff.get("canonical_sha256")
            or fingerprints.get("source_bytes")
            != self._sha256_identity(handoff.get("source_fingerprint"))
            or fingerprints.get("episode_boundary")
            != self._sha256_identity(handoff.get("boundary_hash"))
        ):
            raise ValueError(
                f"historical_preflight_origin_mismatch: {handoff_id} "
                "original PASS certificate or sealed profile differs"
            )

        producer_claims = handoff.get("producer_claims")
        objective = handoff.get("objective_evidence")
        if (
            not isinstance(producer_claims, dict)
            or producer_claims.get("preflight_findings") != []
            or not isinstance(objective, dict)
            or objective.get("canonical_ref")
            != handoff.get("canonical_artifact_id")
            or objective.get("source_manifest", {}).get("fingerprint")
            != fingerprints.get("source_bytes")
            or objective.get("workbook_manifest", {}).get("artifact_id")
            != handoff.get("storyboard_artifact_id")
            or objective.get("workbook_manifest", {}).get("raw_sha256")
            != fingerprints.get("artifact_raw")
            or not exact_int(
                objective.get("deterministic_statistics", {}).get(
                    "shot_count"
                ),
                shot_count,
            )
            or not exact_int(
                objective.get("deterministic_statistics", {}).get(
                    "batch_count"
                ),
                0,
            )
        ):
            raise ValueError(
                f"historical_preflight_origin_mismatch: {handoff_id} "
                "objective evidence is incomplete"
            )

        runtime_certificate_id = str(
            components.get("runtime_test_certificate_id") or ""
        )
        if not runtime_certificate_id:
            raise ValueError(
                f"historical_preflight_origin_mismatch: {handoff_id} "
                "has no original runtime certificate"
            )
        runtime_certificate = self.store.get_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=runtime_certificate_id,
        )
        tests = runtime_certificate.get("tests")
        required_tests = {
            "golden_tests",
            "strict_prompt_tests",
            "public_scene_tests",
            "agent_tests",
            "project_agent_tests",
        }
        expected_test_counts = {
            "golden_tests": 73,
            "strict_prompt_tests": 10,
            "public_scene_tests": 11,
            "agent_tests": 16,
            "project_agent_tests": 21,
        }
        if (
            not self.store.project_record_is_signed(runtime_certificate)
            or runtime_certificate.get("certificate_id")
            != runtime_certificate_id
            or runtime_certificate.get("certificate_type")
            != "runtime_full_regression"
            or runtime_certificate.get("result") != "pass"
            or runtime_certificate.get("runtime_version")
            != "V16.3-148-R10-StableRuntime"
            or runtime_certificate.get("workflow_revision") != "R10-PG1"
            or runtime_certificate.get("output_contract") != "R10-SPC-1"
            or not isinstance(tests, dict)
            or set(tests) != required_tests
            or any(
                not isinstance(value, dict)
                or value.get("status") != "pass"
                or not exact_int(value.get("test_fail_count"), 0)
                or not exact_int(
                    value.get("test_count"), expected_test_counts[name]
                )
                or not exact_int(
                    value.get("test_pass_count"), expected_test_counts[name]
                )
                for name, value in tests.items()
            )
        ):
            raise ValueError(
                f"historical_preflight_origin_mismatch: {handoff_id} "
                "original runtime certificate is invalid"
            )
        expected_runtime_key = "RUNTIMECERT-" + sha256_bytes(
            _stable_json_bytes(
                {
                    "runtime": runtime_certificate.get("runtime_version"),
                    "workflow": runtime_certificate.get(
                        "workflow_revision"
                    ),
                    "ruleset": runtime_certificate.get("ruleset_version"),
                    "validator": runtime_certificate.get(
                        "validator_version"
                    ),
                    "scripts": runtime_certificate.get("scripts_digest"),
                    "service_code": runtime_certificate.get(
                        "service_code_fingerprint"
                    ),
                    "dependencies": runtime_certificate.get(
                        "dependency_digest"
                    ),
                    "deployment_mode": runtime_certificate.get(
                        "deployment_mode"
                    ),
                    "output_contract": runtime_certificate.get(
                        "output_contract"
                    ),
                }
            )
        )
        expected_runtime_fingerprint = sha256_bytes(
            _stable_json_bytes(
                {
                    "runtime": runtime_certificate.get("runtime_version"),
                    "workflow": runtime_certificate.get(
                        "workflow_revision"
                    ),
                    "service_code": runtime_certificate.get(
                        "service_code_fingerprint"
                    ),
                }
            )
        )
        scripts = str(components.get("scripts") or "")
        dependencies = str(components.get("dependencies") or "")
        if (
            runtime_certificate.get("certificate_key")
            != expected_runtime_key
            or runtime_certificate.get("ruleset_version") != profile[0]
            or runtime_certificate.get("validator_version")
            != FROZEN_V053_VALIDATOR_COMPOSITE
            or not scripts.startswith(
                str(runtime_certificate.get("scripts_digest") or "")
            )
            or not dependencies.startswith(
                str(runtime_certificate.get("dependency_digest") or "")
            )
            or fingerprints.get("runtime")
            != expected_runtime_fingerprint
            or fingerprints.get("scripts") != scripts
            or fingerprints.get("dependencies_environment")
            != dependencies
        ):
            raise ValueError(
                f"historical_preflight_origin_mismatch: {handoff_id} "
                "original runtime bindings differ"
            )
        return runtime_certificate

    def _load_handoff_revalidation_context(
        self,
        namespace: str,
        project_id: str,
        *,
        hub: dict[str, Any],
        handoff_id: str,
        runtime_certificate: dict[str, Any],
        allow_historical_contract: bool = False,
    ) -> dict[str, Any]:
        """Load immutable production evidence and recompute current validity."""

        revalidation_started_ns = time.perf_counter_ns()
        revalidation_started_at = utc_stamp()

        handoff = self.store.get_project_record(
            namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        if not self.store.project_record_is_signed(handoff):
            raise ValueError(f"handoff_reissue_required: {handoff_id} is unsigned")
        self._validate_schema_record(
            self._project_schema, "handoff_manifest", handoff
        )
        projections = [
            row
            for row in hub.get("handoffs", [])
            if isinstance(row, dict) and row.get("handoff_id") == handoff_id
        ]
        if (
            len(projections) != 1
            or not self._hub_handoff_matches_signed_record(
                projections[0], handoff
            )
        ):
            raise ValueError(
                f"handoff_recovery_projection_invalid: {handoff_id} Hub projection differs from signed record"
            )
        if handoff.get("status") != "prepared":
            raise ValueError(
                f"handoff_recovery_state_conflict: {handoff_id} status is "
                f"{handoff.get('status')!r}, expected 'prepared'"
            )
        if (
            handoff.get("handoff_type") != "production_to_audit"
            or handoff.get("from_window_role") != "production"
            or handoff.get("to_window_role") != "independent_audit"
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} is not a production_to_audit handoff"
            )
        if handoff.get("claim_authority_work_order_id") or handoff.get(
            "claimed_by_work_order_id"
        ):
            raise ValueError(
                f"handoff_recovery_state_conflict: {handoff_id} has audit claim authority"
            )
        if not self.store.handoff_is_published(namespace, project_id, handoff):
            raise ValueError(
                f"handoff_recovery_state_conflict: {handoff_id} is not committed"
            )
        recovery_journal = self._verified_recovery_successor(
            namespace,
            project_id,
            hub=hub,
            handoff=handoff,
        )
        production_cache_valid = self._valid_cache_identity(
            handoff, AUDIT_CLASS_PRODUCTION_PREFLIGHT
        )
        historical_recovery_successor = bool(
            recovery_journal is not None
            and self._valid_historical_preflight_identity(handoff)
        )
        if not production_cache_valid and not historical_recovery_successor:
            raise ValueError(
                f"handoff_recovery_identity_invalid: {handoff_id} has an invalid original cache identity"
            )

        original_work_order = _find_work_order(
            hub, str(handoff.get("work_order_id") or "")
        )
        if original_work_order.get("server_actor_role") != "production":
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} producer authority is invalid"
            )
        artifact_id = str(handoff.get("storyboard_artifact_id") or "")
        canonical_artifact_id = str(
            handoff.get("canonical_artifact_id") or ""
        )
        storyboard, artifact_record = self._storyboard_from_artifact(
            namespace,
            project_id,
            artifact_id,
            persist_recovered_canonical=False,
            require_bound_projection=True,
        )
        _verified_artifact_record, artifact_path = self.store.get_artifact(
            namespace, project_id, artifact_id
        )
        artifact_bytes = artifact_path.read_bytes()
        if sha256_bytes(artifact_bytes) != artifact_record.get(
            "fingerprint"
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} workbook payload fingerprint differs"
            )
        if artifact_path.suffix.casefold() not in {".xlsx", ".xlsm"}:
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} production artifact is not a workbook"
            )
        extracted_workbook = (
            extract_xlsx_for_controlled_handoff_revalidation(
                artifact_bytes
            )
        )
        if (
            extracted_workbook.get("kind") != "workbook"
            or extracted_workbook.get("canonical_sidecar_present") is not True
            or extracted_workbook.get("canonical_storyboard") != storyboard
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} visible workbook, embedded sidecar, and canonical artifact differ"
            )
        projection_revalidation = copy.deepcopy(
            extracted_workbook.get("controlled_projection_revalidation")
        )
        if (
            not isinstance(projection_revalidation, dict)
            or projection_revalidation.get("spec")
            != "controlled-workbook-projection-revalidation-v1"
            or projection_revalidation.get(
                "visible_rows_match_selected_projector"
            )
            is not True
            or projection_revalidation.get("visible_header_contract")
            != "current"
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} workbook projection proof is incomplete"
            )
        projection_mode = str(
            projection_revalidation.get("mode") or ""
        )
        if projection_mode == "strict_projection_contract":
            if (
                projection_revalidation.get(
                    "visible_projection_matches_sidecar"
                )
                is not True
                or projection_revalidation.get(
                    "visible_audit_page_matches_sidecar"
                )
                is not True
            ):
                raise ValueError(
                    f"handoff_recovery_binding_mismatch: {handoff_id} signed workbook projection proof differs"
                )
        elif projection_mode == "legacy_envelope_short_header_v053_rows":
            if (
                projection_revalidation.get("compatibility_applied")
                is not True
                or projection_revalidation.get("selected_projector")
                != "shortdrama-projector/0.5.3-frozen"
                or projection_revalidation.get(
                    "sidecar_projection_sha256"
                )
                is not None
                or projection_revalidation.get(
                    "sidecar_audit_page_sha256"
                )
                is not None
            ):
                raise ValueError(
                    f"handoff_recovery_binding_mismatch: {handoff_id} transitional workbook proof differs"
                )
        else:
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} workbook projection mode is unsupported"
            )
        artifact_metadata = artifact_record.get("metadata")
        if not isinstance(artifact_metadata, dict):
            artifact_metadata = {}
        if str(artifact_metadata.get("canonical_json_artifact_id") or "") != (
            canonical_artifact_id
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} canonical sidecar differs"
            )
        canonical_record, _canonical_path = self.store.get_artifact(
            namespace, project_id, canonical_artifact_id
        )
        canonical_hash = canonical_storyboard_sha256(storyboard)
        if (
            str(handoff.get("canonical_sha256") or "") != canonical_hash
            or str(
                canonical_record.get("metadata", {}).get(
                    "canonical_sha256"
                )
                or canonical_hash
            )
            != canonical_hash
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} canonical hash differs"
            )
        projection_revalidation.update(
            {
                "artifact_raw_sha256": str(
                    artifact_record.get("fingerprint") or ""
                ),
                "canonical_sha256": canonical_hash,
            }
        )

        source_fingerprint = str(
            artifact_metadata.get("source_fingerprint")
            or handoff.get("source_fingerprint")
            or ""
        )
        boundary_hash = self._boundary_hash(storyboard)
        if (
            not source_fingerprint
            or str(handoff.get("source_fingerprint") or "")
            != source_fingerprint
            or str(handoff.get("boundary_hash") or "") != boundary_hash
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} source or boundary differs"
            )

        old_certificate = self.store.get_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=str(handoff.get("certificate_id") or ""),
        )
        if historical_recovery_successor:
            certificate_evidence_invalid = (
                self._historical_recovery_certificate_evidence_invalid(
                    old_certificate, handoff
                )
            )
        else:
            certificate_evidence_invalid = (
                self._legacy_certificate_evidence_invalid(
                    old_certificate,
                    handoff,
                    recovery_successor=recovery_journal is not None,
                )
            )
        if (
            not self.store.project_record_is_signed(old_certificate)
            or old_certificate.get("certificate_id")
            != handoff.get("certificate_id")
            or old_certificate.get("artifact_id") != artifact_id
            or old_certificate.get("canonical_artifact_id")
            != canonical_artifact_id
            or old_certificate.get("canonical_sha256") != canonical_hash
            or certificate_evidence_invalid
        ):
            raise ValueError(
                f"handoff_recovery_certificate_invalid: {handoff_id} production preflight certificate is invalid"
            )
        artifact_raw_sha256 = str(
            artifact_record.get("fingerprint") or ""
        )
        if (
            handoff.get("fingerprints", {}).get("artifact_raw")
            != artifact_raw_sha256
            or old_certificate.get("cache_components", {}).get(
                "artifact"
            )
            != artifact_raw_sha256
        ):
            raise ValueError(
                f"handoff_recovery_certificate_invalid: {handoff_id} original certificate does not bind the workbook bytes"
            )
        for field, expected in (
            ("production_preflight_status", PRODUCTION_PREFLIGHT_PASS),
            ("production_preflight_issue_count", 0),
            ("independent_audit_status", "PENDING_AUDIT"),
        ):
            value = artifact_metadata.get(field)
            if value is None or value == "":
                continue
            if field == "production_preflight_issue_count":
                matches = (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value == 0
                )
            else:
                matches = value == expected
            if not matches:
                raise ValueError(
                    f"handoff_recovery_evidence_invalid: {handoff_id} artifact metadata contains an explicit preflight conflict"
                )

        current_report = validate_storyboard.validate_project(
            storyboard, mode="audit"
        )
        report = current_report
        current_fingerprints = self._fingerprint_bundle(
            storyboard,
            artifact_record,
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
        )
        old_fingerprints = handoff.get("fingerprints")
        if not isinstance(old_fingerprints, dict):
            raise ValueError(
                f"handoff_recovery_identity_invalid: {handoff_id} has no complete fingerprints"
            )
        production_validation_replay: dict[str, Any] | None = None
        if current_report.get("issues"):
            if historical_recovery_successor:
                sealed_replay = copy.deepcopy(
                    handoff.get("production_validation_replay")
                )
                replay_without_proof = copy.deepcopy(sealed_replay)
                replay_proof = (
                    replay_without_proof.pop("proof_sha256", None)
                    if isinstance(replay_without_proof, dict)
                    else None
                )
                if (
                    not isinstance(sealed_replay, dict)
                    or replay_proof
                    != sha256_bytes(
                        _stable_json_bytes(replay_without_proof)
                    )
                    or sealed_replay.get("current_validator_version")
                    != self._validator_version()
                    or sealed_replay.get("current_report_sha256")
                    != sha256_bytes(_stable_json_bytes(current_report))
                    or sealed_replay.get(
                        "current_validation_issue_count"
                    )
                    != len(current_report.get("issues", []))
                    or sealed_replay.get(
                        "current_validation_error_histogram"
                    )
                    != self._validation_error_histogram(current_report)
                    or old_certificate.get(
                        "production_validation_replay"
                    )
                    != sealed_replay
                ):
                    raise ValueError(
                        f"handoff_recovery_lineage_invalid: {handoff_id} "
                        "historical current-failure profile differs"
                    )
                historical_report = old_certificate.get("report")
                if not isinstance(historical_report, dict):
                    raise ValueError(
                        f"handoff_recovery_lineage_invalid: {handoff_id} "
                        "historical report is missing"
                    )
                production_validation_replay = sealed_replay
                report = historical_report
            elif (
                not allow_historical_contract
                or handoff_id not in CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS
                or projection_mode
                != "legacy_envelope_short_header_v053_rows"
            ):
                raise ValueError(
                    f"handoff_recovery_validation_failed: {handoff_id} has "
                    f"{len(current_report.get('issues', []))} current validation issue(s)"
                )
            else:
                self._assert_historical_origin_evidence(
                    namespace,
                    project_id,
                    handoff_id=handoff_id,
                    handoff=handoff,
                    certificate=old_certificate,
                    storyboard=storyboard,
                )
                production_validation_replay, report = (
                    self._historical_production_validation_replay(
                        handoff_id=handoff_id,
                        handoff=handoff,
                        storyboard=storyboard,
                        current_report=current_report,
                        old_certificate=old_certificate,
                        old_fingerprints=old_fingerprints,
                    )
                )
            if any(
                artifact_metadata.get(field) not in {None, ""}
                for field in (
                    "production_preflight_status",
                    "production_preflight_issue_count",
                    "independent_audit_status",
                )
            ):
                raise ValueError(
                    f"historical_preflight_origin_mismatch: {handoff_id} "
                    "artifact metadata contains an explicit current claim"
                )
        original_trace_id = str(handoff.get("stage_trace_id") or "")
        original_trace = self.store.get_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=original_trace_id,
        )
        self._validate_schema_record(
            self._project_schema, "stage_timing_trace", original_trace
        )
        if recovery_journal is not None:
            trace_origin_valid = (
                original_trace.get("operation")
                == "recover_storyboard_handoff"
                and original_trace.get("phase") == "handoff_recovery"
                and original_trace.get("work_order_id")
                == handoff.get("recovery_work_order_id")
                and original_trace.get("production_work_order_id")
                == handoff.get("work_order_id")
                and original_trace.get("gap_id")
                == handoff.get("recovery_gap_id")
                and original_trace.get("plan_sha256")
                == handoff.get("recovery_plan_sha256")
            )
        else:
            trace_origin_valid = (
                original_trace.get("operation") == "commit_storyboard"
                and original_trace.get("work_order_id")
                in {None, handoff.get("work_order_id")}
            )
        if (
            not self.store.project_record_is_signed(original_trace)
            or original_trace.get("trace_id") != original_trace_id
            or original_trace.get("telemetry_id") != original_trace_id
            or not trace_origin_valid
            or original_trace.get("window_role") != "production"
            or original_trace.get("task_type") != "production_preflight"
            or original_trace.get("artifact_id") != artifact_id
            or original_trace.get("input_fingerprints")
            != old_fingerprints
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} original production trace does not bind the workbook"
            )
        if production_validation_replay is not None:
            trace_stages = original_trace.get("stages")
            expected_shot_count = len(storyboard.get("shots", []))
            if (
                not isinstance(trace_stages, list)
                or not trace_stages
                or any(
                    not isinstance(stage, dict)
                    or stage.get("shot_count") != expected_shot_count
                    or stage.get("batch_count") != 0
                    or stage.get("error_status") != "none"
                    for stage in trace_stages
                )
            ):
                raise ValueError(
                    f"historical_preflight_origin_mismatch: {handoff_id} "
                    "original production trace stages differ"
                )
        projection_revalidation.update(
            {
                "old_handoff_sha256": self.store.project_state_sha256(
                    handoff
                ),
                "old_certificate_id": old_certificate.get(
                    "certificate_id"
                ),
                "old_certificate_sha256": (
                    self.store.project_state_sha256(old_certificate)
                ),
                "original_production_trace_id": original_trace_id,
                "original_production_trace_sha256": (
                    self.store.project_state_sha256(original_trace)
                ),
                "artifact_raw_bound_by_original_certificate": True,
                "audit_page_integrity_source": (
                    "signed_sidecar_sha256"
                    if projection_revalidation.get(
                        "visible_audit_page_matches_sidecar"
                    )
                    is True
                    else "signed_original_artifact_raw_sha256"
                ),
            }
        )
        projection_revalidation["proof_sha256"] = sha256_bytes(
            _stable_json_bytes(projection_revalidation)
        )
        content_changes = sorted(
            key
            for key in HANDOFF_IMMUTABLE_CONTENT_FINGERPRINTS
            if old_fingerprints.get(key) != current_fingerprints.get(key)
        )
        if content_changes:
            raise ValueError(
                f"handoff_recovery_content_changed: {handoff_id} immutable content fingerprints differ: {content_changes}"
            )
        stale_components = sorted(
            key
            for key, value in current_fingerprints.items()
            if old_fingerprints.get(key) != value
        )
        cache_components = self._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            artifact_fingerprint=str(artifact_record.get("fingerprint") or ""),
            storyboard=storyboard,
            scope={
                "targets": original_work_order.get("targets", []),
                "protected_scope": original_work_order.get(
                    "protected_scope", []
                ),
            },
            runtime_test_certificate_id=str(
                runtime_certificate["certificate_id"]
            ),
        )
        revalidation_ended_at = utc_stamp()
        elapsed_ns = time.perf_counter_ns() - revalidation_started_ns
        revalidation_duration_ms = max(1, (elapsed_ns + 999_999) // 1_000_000)
        return {
            "handoff": handoff,
            "storyboard": storyboard,
            "artifact_record": artifact_record,
            "artifact_metadata": artifact_metadata,
            "old_certificate": old_certificate,
            "report": report,
            "current_validation_report": current_report,
            "production_validation_replay": (
                production_validation_replay
            ),
            "recovery_successor_journal": recovery_journal,
            "historical_recovery_successor": (
                historical_recovery_successor
            ),
            "preflight_evidence_source": (
                "controlled_historical_contract_replay"
                if production_validation_replay is not None
                else "controlled_current_revalidation"
            ),
            "production_preflight_status": (
                HISTORICAL_PRODUCTION_PREFLIGHT_PASS
                if production_validation_replay is not None
                else PRODUCTION_PREFLIGHT_PASS
            ),
            "current_fingerprints": current_fingerprints,
            "old_fingerprints": old_fingerprints,
            "stale_components": stale_components,
            "cache_components": cache_components,
            "original_work_order": original_work_order,
            "artifact_id": artifact_id,
            "canonical_artifact_id": canonical_artifact_id,
            "canonical_hash": canonical_hash,
            "source_fingerprint": source_fingerprint,
            "boundary_hash": boundary_hash,
            "workbook_projection_revalidation": (
                projection_revalidation
            ),
            "old_record_sha256": self.store.project_state_sha256(handoff),
            "current_fingerprints_sha256": sha256_bytes(
                _stable_json_bytes(current_fingerprints)
            ),
            "revalidation_measurement": {
                "started_at": revalidation_started_at,
                "ended_at": revalidation_ended_at,
                "duration_ms": revalidation_duration_ms,
            },
        }

    @staticmethod
    def _preflight_evidence_identity(
        *,
        project_id: str,
        work_order_id: str,
        gap_id: str,
        handoff_id: str,
        old_record_sha256: str,
        current_fingerprints_sha256: str,
        runtime_test_certificate_id: str,
        workbook_projection_proof_sha256: str,
        production_validation_replay_sha256: str,
    ) -> tuple[str, str, str]:
        evidence_key = sha256_bytes(
            _stable_json_bytes(
                {
                    "spec": "controlled-preflight-evidence-reissue-v1",
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "gap_id": gap_id,
                    "old_handoff_id": handoff_id,
                    "old_record_sha256": old_record_sha256,
                    "current_fingerprints_sha256": current_fingerprints_sha256,
                    "runtime_test_certificate_id": runtime_test_certificate_id,
                    "workbook_projection_proof_sha256": (
                        workbook_projection_proof_sha256
                    ),
                    "production_validation_replay_sha256": (
                        production_validation_replay_sha256
                    ),
                }
            )
        )
        return (
            evidence_key,
            f"PFE-{sha256_bytes((evidence_key + ':evidence').encode())[:20]}",
            f"CERT-{sha256_bytes((evidence_key + ':certificate').encode())[:20]}",
        )

    def _prepare_handoff_recovery_item_v056(
        self,
        namespace: str,
        project_id: str,
        *,
        hub: dict[str, Any],
        work_order_id: str,
        gap_id: str,
        handoff_id: str,
        runtime_certificate: dict[str, Any],
    ) -> dict[str, Any]:
        handoff = self.store.get_project_record(
            namespace,
            project_id,
            kind="handoffs",
            record_id=handoff_id,
        )
        if not self.store.project_record_is_signed(handoff):
            raise ValueError(
                f"handoff_reissue_required: {handoff_id} is unsigned"
            )
        self._validate_schema_record(
            self._project_schema, "handoff_manifest", handoff
        )
        if handoff.get("status") != "prepared":
            raise ValueError(
                f"handoff_recovery_state_conflict: {handoff_id} status is "
                f"{handoff.get('status')!r}, expected 'prepared'"
            )
        if (
            handoff.get("handoff_type") != "production_to_audit"
            or handoff.get("from_window_role") != "production"
            or handoff.get("to_window_role") != "independent_audit"
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} is not a production_to_audit handoff"
            )
        if handoff.get("claim_authority_work_order_id") or handoff.get(
            "claimed_by_work_order_id"
        ):
            raise ValueError(
                f"handoff_recovery_state_conflict: {handoff_id} has audit claim authority"
            )
        if not self.store.handoff_is_published(
            namespace, project_id, handoff
        ):
            raise ValueError(
                f"handoff_recovery_state_conflict: {handoff_id} is not committed"
            )
        if not self._valid_cache_identity(
            handoff, AUDIT_CLASS_PRODUCTION_PREFLIGHT
        ):
            raise ValueError(
                f"handoff_recovery_identity_invalid: {handoff_id} has an invalid original cache identity"
            )
        if (
            handoff.get("production_preflight_status")
            != PRODUCTION_PREFLIGHT_PASS
            or int(handoff.get("production_preflight_issue_count") or 0) != 0
            or handoff.get("independent_audit_status") != "PENDING_AUDIT"
        ):
            raise ValueError(
                f"handoff_recovery_evidence_invalid: {handoff_id} is not a clean pending production preflight"
            )

        original_work_order = _find_work_order(
            hub, str(handoff.get("work_order_id") or "")
        )
        if original_work_order.get("server_actor_role") != "production":
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} producer authority is invalid"
            )
        artifact_id = str(handoff.get("storyboard_artifact_id") or "")
        canonical_artifact_id = str(
            handoff.get("canonical_artifact_id") or ""
        )
        storyboard, artifact_record = self._storyboard_from_artifact(
            namespace,
            project_id,
            artifact_id,
            persist_recovered_canonical=False,
            require_bound_projection=True,
        )
        artifact_metadata = artifact_record.get("metadata")
        if not isinstance(artifact_metadata, dict):
            artifact_metadata = {}
        if str(artifact_metadata.get("canonical_json_artifact_id") or "") != (
            canonical_artifact_id
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} canonical sidecar differs"
            )
        canonical_record, _canonical_path = self.store.get_artifact(
            namespace, project_id, canonical_artifact_id
        )
        canonical_hash = canonical_storyboard_sha256(storyboard)
        if (
            str(handoff.get("canonical_sha256") or "") != canonical_hash
            or str(canonical_record.get("metadata", {}).get("canonical_sha256") or canonical_hash)
            != canonical_hash
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} canonical hash differs"
            )

        source_fingerprint = str(
            artifact_metadata.get("source_fingerprint")
            or handoff.get("source_fingerprint")
            or ""
        )
        boundary_hash = self._boundary_hash(storyboard)
        if (
            not source_fingerprint
            or str(handoff.get("source_fingerprint") or "")
            != source_fingerprint
            or str(handoff.get("boundary_hash") or "") != boundary_hash
        ):
            raise ValueError(
                f"handoff_recovery_binding_mismatch: {handoff_id} source or boundary differs"
            )

        old_certificate = self.store.get_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=str(handoff.get("certificate_id") or ""),
        )
        if (
            not self.store.project_record_is_signed(old_certificate)
            or old_certificate.get("certificate_type")
            != AUDIT_CLASS_PRODUCTION_PREFLIGHT
            or old_certificate.get("artifact_id") != artifact_id
            or old_certificate.get("canonical_artifact_id")
            != canonical_artifact_id
            or old_certificate.get("canonical_sha256") != canonical_hash
            or old_certificate.get("preflight_status")
            != PRODUCTION_PREFLIGHT_PASS
            or int(old_certificate.get("preflight_issue_count") or 0) != 0
            or old_certificate.get("independent_audit_status")
            != "PENDING_AUDIT"
        ):
            raise ValueError(
                f"handoff_recovery_certificate_invalid: {handoff_id} production preflight certificate is invalid"
            )

        report = validate_storyboard.validate_project(storyboard, mode="audit")
        if report.get("issues"):
            raise ValueError(
                f"handoff_recovery_validation_failed: {handoff_id} has "
                f"{len(report.get('issues', []))} current validation issue(s)"
            )
        current_fingerprints = self._fingerprint_bundle(
            storyboard,
            artifact_record,
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
        )
        old_fingerprints = handoff.get("fingerprints")
        if not isinstance(old_fingerprints, dict):
            raise ValueError(
                f"handoff_recovery_identity_invalid: {handoff_id} has no complete fingerprints"
            )
        stale_components = sorted(
            key
            for key, value in current_fingerprints.items()
            if old_fingerprints.get(key) != value
        )
        if not stale_components:
            raise ValueError(
                f"handoff_recovery_not_required: {handoff_id} already matches the current complete fingerprint"
            )
        cache_components = self._audit_cache_components(
            canonical_hash=canonical_hash,
            source_fingerprint=source_fingerprint,
            boundary_hash=boundary_hash,
            audit_class=AUDIT_CLASS_PRODUCTION_PREFLIGHT,
            artifact_fingerprint=str(artifact_record.get("fingerprint") or ""),
            storyboard=storyboard,
            scope={
                "targets": original_work_order.get("targets", []),
                "protected_scope": original_work_order.get(
                    "protected_scope", []
                ),
            },
            runtime_test_certificate_id=str(
                runtime_certificate["certificate_id"]
            ),
        )
        old_record_sha256 = self.store.project_state_sha256(handoff)
        current_fingerprints_sha256 = sha256_bytes(
            _stable_json_bytes(current_fingerprints)
        )
        recovery_key = sha256_bytes(
            _stable_json_bytes(
                {
                    "spec": "controlled-storyboard-handoff-recovery-v1",
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "gap_id": gap_id,
                    "old_handoff_id": handoff_id,
                    "old_record_sha256": old_record_sha256,
                    "current_fingerprints_sha256": current_fingerprints_sha256,
                    "runtime_test_certificate_id": runtime_certificate[
                        "certificate_id"
                    ],
                }
            )
        )
        public = {
            "old_handoff_id": handoff_id,
            "new_handoff_id": f"HND-{sha256_bytes((recovery_key + ':handoff').encode())[:20]}",
            "new_certificate_id": f"CERT-{sha256_bytes((recovery_key + ':certificate').encode())[:20]}",
            "recovery_id": f"REC-{recovery_key[:20]}",
            "recovery_key": recovery_key,
            "artifact_id": artifact_id,
            "canonical_artifact_id": canonical_artifact_id,
            "canonical_sha256": canonical_hash,
            "old_record_sha256": old_record_sha256,
            "old_fingerprints_sha256": sha256_bytes(
                _stable_json_bytes(old_fingerprints)
            ),
            "current_fingerprints_sha256": current_fingerprints_sha256,
            "stale_components": stale_components,
            "cache_status": "miss",
            "cache_reason": "complete fingerprint changed; prior handoff conclusion is not reusable",
            "independent_audit_status": "PENDING_AUDIT",
        }
        return {
            "public": public,
            "handoff": handoff,
            "storyboard": storyboard,
            "artifact_record": artifact_record,
            "report": report,
            "current_fingerprints": current_fingerprints,
            "cache_components": cache_components,
            "original_work_order": original_work_order,
        }

    def _verified_preflight_evidence_reissue(
        self,
        namespace: str,
        project_id: str,
        *,
        context: dict[str, Any],
        runtime_certificate: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        handoff = context["handoff"]
        matching_evidence = [
            row
            for row in self.store.list_project_records_strict(
                namespace,
                project_id,
                kind="preflight_evidence_reissues",
            )
            if row.get("old_handoff_id") == handoff.get("handoff_id")
            and row.get("old_handoff_sha256")
            == context["old_record_sha256"]
            and row.get("current_fingerprints_sha256")
            == context["current_fingerprints_sha256"]
            and row.get("runtime_test_certificate_id")
            == runtime_certificate["certificate_id"]
            and row.get("status") == "committed"
        ]
        if not matching_evidence:
            raise ValueError(
                f"handoff_preflight_evidence_reissue_required: {handoff['handoff_id']} has no committed current evidence successor"
            )
        if len(matching_evidence) != 1:
            raise ValueError(
                f"handoff_preflight_evidence_reissue_conflict: {handoff['handoff_id']} has non-unique current evidence"
            )
        evidence = matching_evidence[0]
        self._validate_schema_record(
            self._project_schema,
            "preflight_evidence_reissue",
            evidence,
        )
        evidence_key, evidence_id, certificate_id = (
            self._preflight_evidence_identity(
                project_id=project_id,
                work_order_id=str(evidence.get("work_order_id") or ""),
                gap_id=str(evidence.get("gap_id") or ""),
                handoff_id=str(handoff["handoff_id"]),
                old_record_sha256=str(context["old_record_sha256"]),
                current_fingerprints_sha256=str(
                    context["current_fingerprints_sha256"]
                ),
                runtime_test_certificate_id=str(
                    runtime_certificate["certificate_id"]
                ),
                workbook_projection_proof_sha256=str(
                    context["workbook_projection_revalidation"][
                        "proof_sha256"
                    ]
                ),
                production_validation_replay_sha256=str(
                    (
                        context.get("production_validation_replay")
                        or {}
                    ).get("proof_sha256")
                    or ""
                ),
            )
        )
        expected_report_sha256 = sha256_bytes(
            _stable_json_bytes(context["report"])
        )
        replay = context.get("production_validation_replay")
        historical = isinstance(replay, dict)
        if historical:
            replay_without_proof = copy.deepcopy(replay)
            replay_proof = replay_without_proof.pop("proof_sha256", None)
            if replay_proof != sha256_bytes(
                _stable_json_bytes(replay_without_proof)
            ):
                raise ValueError(
                    f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} replay proof is invalid"
                )
        expected_evidence_type = (
            "controlled_historical_production_preflight_attestation"
            if historical
            else "controlled_current_production_preflight_revalidation"
        )
        expected_preflight_status = context[
            "production_preflight_status"
        ]
        expected_certificate_class = (
            AUDIT_CLASS_HISTORICAL_PRODUCTION_PREFLIGHT
            if historical
            else AUDIT_CLASS_PRODUCTION_PREFLIGHT
        )
        expected_evidence_origin = (
            "controlled_historical_contract_replay"
            if historical
            else "controlled_current_revalidation"
        )
        expected_certificate_cache_components = (
            self._historical_preflight_cache_components(
                context=context,
                replay=replay,
                runtime_test_certificate_id=str(
                    runtime_certificate["certificate_id"]
                ),
            )
            if historical
            else context["cache_components"]
        )
        expected_certificate_cache_key = (
            self._historical_preflight_cache_key(
                expected_certificate_cache_components
            )
            if historical
            else self._cache_key_from_components(
                expected_certificate_cache_components
            )
        )
        expected_trace_id = (
            f"TRC-{sha256_bytes((evidence_key + ':evidence-trace').encode())[:20]}"
        )
        evidence_work_order = _find_work_order(
            self.store.load_hub(namespace, project_id),
            str(evidence.get("work_order_id") or ""),
        )
        authorized_handoff_ids = evidence.get("authorized_handoff_ids")
        authorized_ids_valid = (
            isinstance(authorized_handoff_ids, list)
            and bool(authorized_handoff_ids)
            and all(
                isinstance(value, str) and bool(value)
                for value in authorized_handoff_ids
            )
            and len(set(authorized_handoff_ids))
            == len(authorized_handoff_ids)
            and handoff.get("handoff_id") in authorized_handoff_ids
        )
        if historical and (
            evidence.get("gap_id") != CONTROLLED_V053_PREFLIGHT_GAP_ID
            or authorized_handoff_ids
            != list(CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS)
        ):
            raise ValueError(
                f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} historical batch authority differs"
            )
        expected_targets = {
            str(evidence.get("gap_id") or ""),
            *(authorized_handoff_ids if authorized_ids_valid else []),
            "controlled_phase:preflight_evidence_backfill",
        }
        expected_batch_trace_id = (
            "TRC-"
            + sha256_bytes(
                (
                    str(evidence.get("plan_sha256") or "")
                    + ":preflight-evidence-trace"
                ).encode()
            )[:20]
        )
        expected_shot_count = int(context["report"].get("shot_count", 0))
        if (
            not self.store.project_record_is_signed(evidence)
            or evidence.get("status") != "committed"
            or evidence.get("evidence_type")
            != expected_evidence_type
            or evidence.get("preflight_evidence_id") != evidence_id
            or evidence.get("evidence_key") != evidence_key
            or evidence.get("old_handoff_id") != handoff.get("handoff_id")
            or evidence.get("old_handoff_sha256")
            != context["old_record_sha256"]
            or evidence.get("original_certificate_id")
            != context["old_certificate"].get("certificate_id")
            or evidence.get("artifact_id") != context["artifact_id"]
            or evidence.get("canonical_artifact_id")
            != context["canonical_artifact_id"]
            or evidence.get("canonical_sha256") != context["canonical_hash"]
            or evidence.get("current_fingerprints_sha256")
            != context["current_fingerprints_sha256"]
            or evidence.get("current_fingerprints")
            != context["current_fingerprints"]
            or evidence.get("runtime_test_certificate_id")
            != runtime_certificate["certificate_id"]
            or evidence.get("workbook_projection_revalidation")
            != context["workbook_projection_revalidation"]
            or evidence.get("validation_report_sha256")
            != expected_report_sha256
            or evidence.get("checked_shot_count") != expected_shot_count
            or evidence.get("certificate_id") != certificate_id
            or evidence.get("production_preflight_status")
            != expected_preflight_status
            or evidence.get("production_preflight_issue_count") != 0
            or evidence.get("independent_audit_status") != "PENDING_AUDIT"
            or evidence.get("first_draft_grade_reconstructed") is not False
            or evidence.get("authored_artifacts_modified") is not False
            or evidence.get("history_records_modified") != 0
            or evidence.get("independent_audit_verdict_issued") is not False
            or evidence.get("original_production_trace_id")
            != handoff.get("stage_trace_id")
            or evidence.get("confirmation")
            != PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION
            or evidence.get("trace_id") != expected_trace_id
            or evidence.get("batch_trace_id") != expected_batch_trace_id
            or not authorized_ids_valid
            or evidence_work_order.get("operation") != "rule_improvement"
            or evidence_work_order.get("mode") != "execute"
            or evidence_work_order.get("authorization")
            != "explicit_execute"
            or evidence_work_order.get("server_actor_role") != "production"
            or {
                str(value)
                for value in evidence_work_order.get("targets", [])
            }
            != expected_targets
            or (
                historical
                and (
                    evidence.get("production_validation_replay")
                    != replay
                    or evidence.get("historical_validation_report")
                    != context["report"]
                    or evidence.get(
                        "historical_validation_report_sha256"
                    )
                    != replay["historical_report_sha256"]
                    or evidence.get("current_validation_report")
                    != context["current_validation_report"]
                    or evidence.get(
                        "current_validation_report_sha256"
                    )
                    != replay["current_report_sha256"]
                    or evidence.get(
                        "current_validation_error_histogram"
                    )
                    != replay["current_validation_error_histogram"]
                    or evidence.get("current_validation_status")
                    != CURRENT_VALIDATION_FAIL
                    or evidence.get("current_validation_issue_count")
                    != replay["current_validation_issue_count"]
                    or evidence.get("current_production_pass_claimed")
                    is not False
                    or evidence.get("current_preflight_cache_reusable")
                    is not False
                    or evidence.get(
                        "conclusion_reusable_for_independent_audit"
                    )
                    is not False
                    or evidence.get(
                        "requires_full_current_independent_audit"
                    )
                    is not True
                )
            )
        ):
            raise ValueError(
                f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} evidence successor is invalid"
            )
        batch_trace = self.store.get_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=expected_batch_trace_id,
        )
        batch_mappings = batch_trace.get("mappings")
        current_mapping = [
            row
            for row in batch_mappings
            if isinstance(row, dict)
            and row.get("old_handoff_id") == handoff.get("handoff_id")
        ] if isinstance(batch_mappings, list) else []
        if (
            not self.store.project_record_is_signed(batch_trace)
            or batch_trace.get("operation") != "recover_storyboard_handoff"
            or batch_trace.get("phase") != "preflight_evidence_backfill"
            or batch_trace.get("trace_id") != expected_batch_trace_id
            or batch_trace.get("telemetry_id") != expected_batch_trace_id
            or batch_trace.get("work_order_id") != evidence.get("work_order_id")
            or batch_trace.get("gap_id") != evidence.get("gap_id")
            or batch_trace.get("plan_sha256") != evidence.get("plan_sha256")
            or batch_trace.get("authorized_handoff_ids")
            != authorized_handoff_ids
            or batch_trace.get("evidence_count") != len(authorized_handoff_ids)
            or not isinstance(batch_mappings, list)
            or len(batch_mappings) != len(authorized_handoff_ids)
            or [
                row.get("old_handoff_id")
                for row in batch_mappings
                if isinstance(row, dict)
            ] != authorized_handoff_ids
            or len(current_mapping) != 1
            or current_mapping[0].get("preflight_evidence_id") != evidence_id
            or current_mapping[0].get("certificate_id") != certificate_id
            or current_mapping[0].get("trace_id") != expected_trace_id
            or current_mapping[0].get("workbook_projection_mode")
            != context["workbook_projection_revalidation"]["mode"]
            or current_mapping[0].get(
                "workbook_projection_proof_sha256"
            )
            != context["workbook_projection_revalidation"][
                "proof_sha256"
            ]
            or current_mapping[0].get("production_preflight_status")
            != expected_preflight_status
            or current_mapping[0].get("production_preflight_issue_count") != 0
            or current_mapping[0].get("independent_audit_status")
            != "PENDING_AUDIT"
            or (
                historical
                and (
                    current_mapping[0].get(
                        "production_validation_replay_sha256"
                    )
                    != replay["proof_sha256"]
                    or current_mapping[0].get(
                        "current_validation_status"
                    )
                    != CURRENT_VALIDATION_FAIL
                    or current_mapping[0].get(
                        "current_validation_issue_count"
                    )
                    != replay["current_validation_issue_count"]
                    or current_mapping[0].get(
                        "current_production_pass_claimed"
                    )
                    is not False
                    or current_mapping[0].get(
                        "current_preflight_cache_reusable"
                    )
                    is not False
                    or current_mapping[0].get(
                        "conclusion_reusable_for_independent_audit"
                    )
                    is not False
                    or current_mapping[0].get(
                        "requires_full_current_independent_audit"
                    )
                    is not True
                )
            )
        ):
            raise ValueError(
                f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} batch trace is invalid"
            )
        original_trace_id = str(
            evidence.get("original_production_trace_id") or ""
        )
        original_trace = self.store.get_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=original_trace_id,
        )
        if (
            not self.store.project_record_is_signed(original_trace)
            or original_trace.get("trace_id") != original_trace_id
            or original_trace.get("telemetry_id") != original_trace_id
            or original_trace.get("operation") != "commit_storyboard"
            or original_trace.get("window_role") != "production"
            or original_trace.get("task_type") != "production_preflight"
            or original_trace.get("artifact_id") != context["artifact_id"]
            or original_trace.get("input_fingerprints")
            != context["old_fingerprints"]
        ):
            raise ValueError(
                f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} original production trace is invalid"
            )
        self._validate_schema_record(
            self._project_schema, "stage_timing_trace", original_trace
        )
        certificate = self.store.get_project_record(
            namespace,
            project_id,
            kind="certificates",
            record_id=certificate_id,
        )
        self._validate_schema_record(
            self._project_schema,
            "production_preflight_certificate",
            certificate,
        )
        certificate_report = certificate.get("report")
        if (
            not self.store.project_record_is_signed(certificate)
            or self.store.project_state_sha256(certificate)
            != evidence.get("certificate_after_sha256")
            or certificate.get("certificate_type")
            != expected_certificate_class
            or certificate.get("work_order_id")
            != evidence.get("work_order_id")
            or certificate.get("evidence_reissue_work_order_id")
            != evidence.get("work_order_id")
            or certificate.get("evidence_reissue_gap_id")
            != evidence.get("gap_id")
            or certificate.get("production_work_order_id")
            != handoff.get("work_order_id")
            or certificate.get("artifact_id") != context["artifact_id"]
            or certificate.get("canonical_artifact_id")
            != context["canonical_artifact_id"]
            or certificate.get("canonical_sha256")
            != context["canonical_hash"]
            or certificate.get("source_fingerprint")
            != context["source_fingerprint"]
            or certificate.get("boundary_hash")
            != context["boundary_hash"]
            or certificate.get("ruleset_version")
            != expected_certificate_cache_components["ruleset"]
            or certificate.get("validator_version")
            != expected_certificate_cache_components["validator"]
            or certificate.get("audit_class")
            != expected_certificate_class
            or certificate.get("cache_components")
            != expected_certificate_cache_components
            or certificate.get("cache_key")
            != expected_certificate_cache_key
            or (
                not historical
                and not self._valid_cache_identity(
                    certificate, AUDIT_CLASS_PRODUCTION_PREFLIGHT
                )
            )
            or (
                historical
                and not self._valid_historical_preflight_identity(
                    certificate
                )
            )
            or certificate.get("preflight_status")
            != expected_preflight_status
            or certificate.get("preflight_issue_count") != 0
            or certificate.get("independent_audit_status")
            != "PENDING_AUDIT"
            or certificate.get("evidence_origin")
            != expected_evidence_origin
            or certificate.get("preflight_evidence_id") != evidence_id
            or certificate.get("backfill_for_handoff_id")
            != handoff.get("handoff_id")
            or certificate.get("original_certificate_id")
            != context["old_certificate"].get("certificate_id")
            or certificate.get("original_handoff_sha256")
            != context["old_record_sha256"]
            or certificate.get("authorized_handoff_ids")
            != authorized_handoff_ids
            or certificate.get("runtime_test_certificate_id")
            != runtime_certificate["certificate_id"]
            or certificate.get("workbook_projection_revalidation")
            != context["workbook_projection_revalidation"]
            or certificate.get("report_sha256") != expected_report_sha256
            or not isinstance(certificate_report, dict)
            or certificate_report != context["report"]
            or sha256_bytes(_stable_json_bytes(certificate_report))
            != expected_report_sha256
            or certificate.get("current_fingerprints")
            != context["current_fingerprints"]
            or certificate.get("current_fingerprints_sha256")
            != context["current_fingerprints_sha256"]
            or certificate.get("evidence_trace_id") != expected_trace_id
            or certificate.get("checked_shot_count") != expected_shot_count
            or certificate.get("first_draft_grade_reconstructed") is not False
            or certificate.get("authored_artifacts_modified") is not False
            or certificate.get("independent_audit_verdict_issued") is not False
            or evidence.get("certificate_after") != certificate
            or (
                historical
                and (
                    certificate.get("evidence_type")
                    != expected_evidence_type
                    or certificate.get("production_validation_replay")
                    != replay
                    or certificate.get("historical_validation_report")
                    != context["report"]
                    or certificate.get("current_validation_report")
                    != context["current_validation_report"]
                    or certificate.get("current_validation_status")
                    != CURRENT_VALIDATION_FAIL
                    or certificate.get("current_validation_issue_count")
                    != replay["current_validation_issue_count"]
                    or certificate.get("current_production_pass_claimed")
                    is not False
                    or certificate.get("current_preflight_cache_reusable")
                    is not False
                    or certificate.get(
                        "conclusion_reusable_for_independent_audit"
                    )
                    is not False
                    or certificate.get(
                        "requires_full_current_independent_audit"
                    )
                    is not True
                )
            )
        ):
            raise ValueError(
                f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} evidence certificate is invalid"
            )
        stage_trace = self.store.get_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=expected_trace_id,
        )
        if (
            not self.store.project_record_is_signed(stage_trace)
            or self.store.project_state_sha256(stage_trace)
            != evidence.get("stage_trace_after_sha256")
            or stage_trace.get("operation")
            != "recover_storyboard_handoff"
            or stage_trace.get("phase")
            != "preflight_evidence_backfill"
            or stage_trace.get("trace_id") != expected_trace_id
            or stage_trace.get("telemetry_id") != expected_trace_id
            or stage_trace.get("window_role") != "production"
            or stage_trace.get("task_type") != "production_preflight"
            or stage_trace.get("work_order_id")
            != evidence.get("work_order_id")
            or stage_trace.get("production_work_order_id")
            != handoff.get("work_order_id")
            or stage_trace.get("gap_id") != evidence.get("gap_id")
            or stage_trace.get("plan_sha256")
            != evidence.get("plan_sha256")
            or stage_trace.get("authority_record_id") != evidence_id
            or stage_trace.get("artifact_id") != context["artifact_id"]
            or stage_trace.get("input_fingerprints")
            != context["current_fingerprints"]
            or stage_trace.get("authorized_handoff_ids")
            != authorized_handoff_ids
            or evidence.get("stage_trace_after") != stage_trace
            or (
                historical
                and (
                    stage_trace.get("production_validation_replay")
                    != replay
                    or stage_trace.get("production_preflight_status")
                    != HISTORICAL_PRODUCTION_PREFLIGHT_PASS
                    or stage_trace.get("current_validation_status")
                    != CURRENT_VALIDATION_FAIL
                    or stage_trace.get("current_validation_issue_count")
                    != replay["current_validation_issue_count"]
                    or stage_trace.get("current_production_pass_claimed")
                    is not False
                    or stage_trace.get("current_preflight_cache_reusable")
                    is not False
                    or stage_trace.get(
                        "conclusion_reusable_for_independent_audit"
                    )
                    is not False
                    or stage_trace.get(
                        "requires_full_current_independent_audit"
                    )
                    is not True
                )
            )
        ):
            raise ValueError(
                f"handoff_preflight_evidence_reissue_invalid: {handoff['handoff_id']} evidence trace is invalid"
            )
        self._validate_schema_record(
            self._project_schema, "stage_timing_trace", stage_trace
        )
        return evidence, certificate

    def _prepare_handoff_recovery_item(
        self,
        namespace: str,
        project_id: str,
        *,
        hub: dict[str, Any],
        work_order_id: str,
        gap_id: str,
        handoff_id: str,
        runtime_certificate: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._load_handoff_revalidation_context(
            namespace,
            project_id,
            hub=hub,
            handoff_id=handoff_id,
            runtime_certificate=runtime_certificate,
            allow_historical_contract=(
                gap_id == CONTROLLED_HANDOFF_RECOVERY_GAP_ID
                and handoff_id in CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS
            ),
        )
        handoff = context["handoff"]
        old_certificate = context["old_certificate"]
        evidence_id: str | None = None
        evidence_source = "original_handoff"
        effective_certificate_id = str(old_certificate["certificate_id"])
        historical_replay = context.get("production_validation_replay")
        if isinstance(historical_replay, dict):
            if context.get("historical_recovery_successor") is True:
                if (
                    handoff.get("production_preflight_status")
                    != HISTORICAL_PRODUCTION_PREFLIGHT_PASS
                    or handoff.get("production_preflight_issue_count") != 0
                    or handoff.get("current_validation_status")
                    != CURRENT_VALIDATION_FAIL
                    or handoff.get("independent_audit_status")
                    != "PENDING_AUDIT"
                    or handoff.get("preflight_evidence_source")
                    != "controlled_historical_contract_replay"
                ):
                    raise ValueError(
                        f"handoff_recovery_evidence_invalid: {handoff_id} "
                        "historical successor evidence differs"
                    )
                raw_evidence_id = handoff.get(
                    "preflight_evidence_reissue_id"
                )
                evidence_id = (
                    str(raw_evidence_id) if raw_evidence_id else None
                )
                evidence_source = (
                    "controlled_historical_contract_replay"
                )
            else:
                if not self._legacy_handoff_preflight_fields_missing(
                    handoff
                ):
                    raise ValueError(
                        f"handoff_recovery_evidence_invalid: {handoff_id} historical origin must retain the exact missing-field legacy shape"
                    )
                evidence, evidence_certificate = (
                    self._verified_preflight_evidence_reissue(
                        namespace,
                        project_id,
                        context=context,
                        runtime_certificate=runtime_certificate,
                    )
                )
                evidence_id = str(evidence["preflight_evidence_id"])
                evidence_source = (
                    "controlled_historical_contract_replay"
                )
                effective_certificate_id = str(
                    evidence_certificate["certificate_id"]
                )
        elif self._clean_pending_preflight_fields(handoff):
            if (
                old_certificate.get("preflight_status")
                != PRODUCTION_PREFLIGHT_PASS
                or old_certificate.get("preflight_issue_count") != 0
                or old_certificate.get("independent_audit_status")
                != "PENDING_AUDIT"
            ):
                raise ValueError(
                    f"handoff_recovery_certificate_invalid: {handoff_id} clean handoff lacks exact original evidence"
                )
        elif self._legacy_handoff_preflight_fields_missing(handoff):
            evidence, evidence_certificate = (
                self._verified_preflight_evidence_reissue(
                    namespace,
                    project_id,
                    context=context,
                    runtime_certificate=runtime_certificate,
                )
            )
            evidence_id = str(evidence["preflight_evidence_id"])
            evidence_source = context["preflight_evidence_source"]
            effective_certificate_id = str(
                evidence_certificate["certificate_id"]
            )
        else:
            raise ValueError(
                f"handoff_recovery_evidence_invalid: {handoff_id} has ambiguous or failed preflight fields"
            )
        stale_components = context["stale_components"]
        if not stale_components:
            raise ValueError(
                f"handoff_recovery_not_required: {handoff_id} already matches the current complete fingerprint"
            )
        recovery_key = sha256_bytes(
            _stable_json_bytes(
                {
                    "spec": "controlled-storyboard-handoff-recovery-v2",
                    "project_id": project_id,
                    "work_order_id": work_order_id,
                    "gap_id": gap_id,
                    "old_handoff_id": handoff_id,
                    "old_record_sha256": context["old_record_sha256"],
                    "current_fingerprints_sha256": context[
                        "current_fingerprints_sha256"
                    ],
                    "runtime_test_certificate_id": runtime_certificate[
                        "certificate_id"
                    ],
                    "preflight_evidence_source": evidence_source,
                    "preflight_evidence_id": evidence_id,
                    "effective_preflight_certificate_id": (
                        effective_certificate_id
                    ),
                    "workbook_projection_revalidation_sha256": (
                        sha256_bytes(
                            _stable_json_bytes(
                                context[
                                    "workbook_projection_revalidation"
                                ]
                            )
                        )
                    ),
                    "production_validation_replay_sha256": str(
                        (
                            context.get("production_validation_replay")
                            or {}
                        ).get("proof_sha256")
                        or ""
                    ),
                }
            )
        )
        public = {
            "old_handoff_id": handoff_id,
            "new_handoff_id": (
                f"HND-{sha256_bytes((recovery_key + ':handoff').encode())[:20]}"
            ),
            "new_certificate_id": (
                f"CERT-{sha256_bytes((recovery_key + ':certificate').encode())[:20]}"
            ),
            "recovery_id": f"REC-{recovery_key[:20]}",
            "recovery_key": recovery_key,
            "production_revalidation_trace_id": (
                f"TRC-{sha256_bytes((recovery_key + ':production-revalidation-trace').encode())[:20]}"
            ),
            "artifact_id": context["artifact_id"],
            "canonical_artifact_id": context["canonical_artifact_id"],
            "canonical_sha256": context["canonical_hash"],
            "old_record_sha256": context["old_record_sha256"],
            "old_fingerprints_sha256": sha256_bytes(
                _stable_json_bytes(context["old_fingerprints"])
            ),
            "current_fingerprints_sha256": context[
                "current_fingerprints_sha256"
            ],
            "current_fingerprints": copy.deepcopy(
                context["current_fingerprints"]
            ),
            "stale_components": copy.deepcopy(stale_components),
            "preflight_evidence_source": evidence_source,
            "preflight_evidence_id": evidence_id,
            "effective_preflight_certificate_id": effective_certificate_id,
            "workbook_projection_revalidation": copy.deepcopy(
                context["workbook_projection_revalidation"]
            ),
            "cache_status": "miss",
            "cache_reason": (
                "sealed historical contract replay; current validator failed; full independent audit required"
                if isinstance(historical_replay, dict)
                else "complete fingerprint changed; current production preflight was revalidated"
            ),
            "independent_audit_status": "PENDING_AUDIT",
        }
        replay = context.get("production_validation_replay")
        if isinstance(replay, dict):
            public.update(
                {
                    "production_preflight_status": (
                        HISTORICAL_PRODUCTION_PREFLIGHT_PASS
                    ),
                    "production_validation_replay": copy.deepcopy(
                        replay
                    ),
                    "current_validation_status": (
                        CURRENT_VALIDATION_FAIL
                    ),
                    "current_validation_issue_count": replay[
                        "current_validation_issue_count"
                    ],
                    "current_validation_error_histogram": copy.deepcopy(
                        replay["current_validation_error_histogram"]
                    ),
                    "current_production_pass_claimed": False,
                    "current_preflight_cache_reusable": False,
                    "conclusion_reusable_for_independent_audit": False,
                    "requires_full_current_independent_audit": True,
                }
            )
        else:
            public["production_preflight_status"] = (
                PRODUCTION_PREFLIGHT_PASS
            )
        return {
            "public": public,
            "context": context,
            "handoff": handoff,
            "storyboard": context["storyboard"],
            "artifact_record": context["artifact_record"],
            "report": context["report"],
            "current_validation_report": context[
                "current_validation_report"
            ],
            "production_validation_replay": replay,
            "preflight_evidence_source": evidence_source,
            "current_fingerprints": context["current_fingerprints"],
            "cache_components": context["cache_components"],
            "original_work_order": context["original_work_order"],
            "revalidation_measurement": context[
                "revalidation_measurement"
            ],
            "workbook_projection_revalidation": copy.deepcopy(
                context["workbook_projection_revalidation"]
            ),
        }

    def _prepare_handoff_preflight_evidence_item(
        self,
        namespace: str,
        project_id: str,
        *,
        hub: dict[str, Any],
        work_order_id: str,
        gap_id: str,
        handoff_id: str,
        runtime_certificate: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._load_handoff_revalidation_context(
            namespace,
            project_id,
            hub=hub,
            handoff_id=handoff_id,
            runtime_certificate=runtime_certificate,
            allow_historical_contract=(
                gap_id == CONTROLLED_V053_PREFLIGHT_GAP_ID
                and handoff_id in CONTROLLED_V053_PREFLIGHT_HANDOFF_IDS
            ),
        )
        handoff = context["handoff"]
        if not self._legacy_handoff_preflight_fields_missing(handoff):
            if self._clean_pending_preflight_fields(handoff):
                raise ValueError(
                    f"preflight_evidence_reissue_not_required: {handoff_id} already carries clean production evidence"
                )
            raise ValueError(
                f"preflight_evidence_reissue_forbidden: {handoff_id} contains explicit or ambiguous preflight fields"
            )
        existing_evidence = [
            row
            for row in self.store.list_project_records_strict(
                namespace,
                project_id,
                kind="preflight_evidence_reissues",
            )
            if row.get("old_handoff_id") == handoff_id
            and row.get("old_handoff_sha256")
            == context["old_record_sha256"]
            and row.get("current_fingerprints_sha256")
            == context["current_fingerprints_sha256"]
            and row.get("runtime_test_certificate_id")
            == runtime_certificate["certificate_id"]
            and row.get("status") in {"prepared", "committed"}
        ]
        if len(existing_evidence) > 1:
            raise ValueError(
                f"preflight_evidence_reissue_conflict: {handoff_id} has non-unique current evidence"
            )
        if existing_evidence and (
            existing_evidence[0].get("work_order_id") != work_order_id
            or existing_evidence[0].get("gap_id") != gap_id
        ):
            if existing_evidence[0].get("status") == "committed":
                self._verified_preflight_evidence_reissue(
                    namespace,
                    project_id,
                    context=context,
                    runtime_certificate=runtime_certificate,
                )
            raise ValueError(
                f"preflight_evidence_reissue_not_required: {handoff_id} already has current controlled evidence from another authorized work order"
            )
        original_trace_id = str(handoff.get("stage_trace_id") or "")
        original_trace = self.store.get_project_record(
            namespace,
            project_id,
            kind="telemetry",
            record_id=original_trace_id,
        )
        self._validate_schema_record(
            self._project_schema, "stage_timing_trace", original_trace
        )
        if (
            not self.store.project_record_is_signed(original_trace)
            or original_trace.get("trace_id") != original_trace_id
            or original_trace.get("telemetry_id") != original_trace_id
            or original_trace.get("operation") != "commit_storyboard"
            or original_trace.get("window_role") != "production"
            or original_trace.get("task_type") != "production_preflight"
            or original_trace.get("artifact_id") != context["artifact_id"]
            or original_trace.get("input_fingerprints")
            != context["old_fingerprints"]
            or original_trace.get("work_order_id")
            not in {None, handoff.get("work_order_id")}
        ):
            raise ValueError(
                f"preflight_evidence_reissue_origin_invalid: {handoff_id} lacks an exact production commit trace"
            )
        evidence_key, evidence_id, certificate_id = (
            self._preflight_evidence_identity(
                project_id=project_id,
                work_order_id=work_order_id,
                gap_id=gap_id,
                handoff_id=handoff_id,
                old_record_sha256=str(context["old_record_sha256"]),
                current_fingerprints_sha256=str(
                    context["current_fingerprints_sha256"]
                ),
                runtime_test_certificate_id=str(
                    runtime_certificate["certificate_id"]
                ),
                workbook_projection_proof_sha256=str(
                    context["workbook_projection_revalidation"][
                        "proof_sha256"
                    ]
                ),
                production_validation_replay_sha256=str(
                    (
                        context.get("production_validation_replay")
                        or {}
                    ).get("proof_sha256")
                    or ""
                ),
            )
        )
        report_sha256 = sha256_bytes(_stable_json_bytes(context["report"]))
        public = {
            "old_handoff_id": handoff_id,
            "work_order_id": work_order_id,
            "gap_id": gap_id,
            "preflight_evidence_id": evidence_id,
            "evidence_trace_id": (
                f"TRC-{sha256_bytes((evidence_key + ':evidence-trace').encode())[:20]}"
            ),
            "new_certificate_id": certificate_id,
            "original_certificate_id": handoff.get("certificate_id"),
            "artifact_id": context["artifact_id"],
            "canonical_artifact_id": context["canonical_artifact_id"],
            "canonical_sha256": context["canonical_hash"],
            "old_handoff_sha256": context["old_record_sha256"],
            "current_fingerprints_sha256": context[
                "current_fingerprints_sha256"
            ],
            "current_fingerprints": copy.deepcopy(
                context["current_fingerprints"]
            ),
            "validation_report_sha256": report_sha256,
            "checked_shot_count": int(context["report"].get("shot_count", 0)),
            "stale_components": copy.deepcopy(context["stale_components"]),
            "workbook_projection_revalidation": copy.deepcopy(
                context["workbook_projection_revalidation"]
            ),
            "production_preflight_status": context[
                "production_preflight_status"
            ],
            "production_preflight_issue_count": 0,
            "independent_audit_status": "PENDING_AUDIT",
            "first_draft_grade_reconstructed": False,
            "authored_artifacts_modified": False,
            "history_records_modified": 0,
        }
        replay = context.get("production_validation_replay")
        if isinstance(replay, dict):
            public.update(
                {
                    "evidence_type": (
                        "controlled_historical_production_preflight_attestation"
                    ),
                    "production_validation_replay": copy.deepcopy(replay),
                    "historical_validation_report_sha256": (
                        report_sha256
                    ),
                    "current_validation_status": (
                        CURRENT_VALIDATION_FAIL
                    ),
                    "current_validation_issue_count": replay[
                        "current_validation_issue_count"
                    ],
                    "current_validation_report_sha256": replay[
                        "current_report_sha256"
                    ],
                    "current_validation_error_histogram": copy.deepcopy(
                        replay["current_validation_error_histogram"]
                    ),
                    "current_production_pass_claimed": False,
                    "current_preflight_cache_reusable": False,
                    "conclusion_reusable_for_independent_audit": False,
                    "requires_full_current_independent_audit": True,
                }
            )
        else:
            public["evidence_type"] = (
                "controlled_current_production_preflight_revalidation"
            )
        return {
            "public": public,
            "context": context,
            "evidence_key": evidence_key,
            "original_trace_id": original_trace_id,
            "runtime_test_certificate_id": runtime_certificate[
                "certificate_id"
            ],
        }

    @staticmethod
    def _preflight_evidence_reissue_plan_payload(
        *,
        project_id: str,
        work_order_id: str,
        gap_id: str,
        runtime_test_certificate_id: str,
        authorized_handoff_ids: list[str],
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "spec": "controlled-preflight-evidence-reissue-v1",
            "phase": "preflight_evidence_backfill",
            "project_id": project_id,
            "work_order_id": work_order_id,
            "gap_id": gap_id,
            "runtime_test_certificate_id": runtime_test_certificate_id,
            "authorized_handoff_ids": copy.deepcopy(authorized_handoff_ids),
            "items": copy.deepcopy(items),
        }

    def _execute_handoff_preflight_evidence_item(
        self,
        namespace: str,
        project_id: str,
        *,
        work_order_id: str,
        gap_id: str,
        plan_sha256: str,
        trace_id: str,
        prepared: dict[str, Any],
        authorized_handoff_ids: list[str],
        request_session_id: str | None,
    ) -> dict[str, Any]:
        context = prepared["context"]
        public = prepared["public"]
        live_hub = self.store.load_hub(namespace, project_id)
        live_work_order = _find_work_order(live_hub, work_order_id)
        self._assert_handoff_recovery_authority(
            namespace,
            live_work_order,
            gap_id=gap_id,
            handoff_ids=authorized_handoff_ids,
            phase="preflight_evidence_backfill",
            request_session_id=request_session_id,
        )
        self._require_capability_gap(live_hub, gap_id)
        live_runtime_certificate = self._require_runtime_test_certificate(
            namespace, project_id
        )
        if (
            live_runtime_certificate.get("certificate_id")
            != prepared["runtime_test_certificate_id"]
        ):
            raise ValueError(
                "preflight_evidence_reissue_plan_stale: runtime certificate changed after plan"
            )
        replay = context.get("production_validation_replay")
        certificate_extra = {
            "created_at": str(live_work_order.get("created_at") or ""),
            "evidence_origin": (
                "controlled_historical_contract_replay"
                if isinstance(replay, dict)
                else "controlled_current_revalidation"
            ),
            "evidence_reissue_work_order_id": work_order_id,
            "evidence_reissue_gap_id": gap_id,
            "authorized_handoff_ids": copy.deepcopy(
                authorized_handoff_ids
            ),
            "evidence_trace_id": public["evidence_trace_id"],
            "preflight_evidence_id": public["preflight_evidence_id"],
            "backfill_for_handoff_id": public["old_handoff_id"],
            "original_certificate_id": public[
                "original_certificate_id"
            ],
            "original_handoff_sha256": public["old_handoff_sha256"],
            "current_fingerprints": copy.deepcopy(
                context["current_fingerprints"]
            ),
            "current_fingerprints_sha256": public[
                "current_fingerprints_sha256"
            ],
            "runtime_test_certificate_id": prepared[
                "runtime_test_certificate_id"
            ],
            "workbook_projection_revalidation": copy.deepcopy(
                context["workbook_projection_revalidation"]
            ),
            "first_draft_grade_reconstructed": False,
            "authored_artifacts_modified": False,
            "independent_audit_verdict_issued": False,
        }
        if isinstance(replay, dict):
            certificate = self._historical_preflight_attestation_value(
                certificate_id=str(public["new_certificate_id"]),
                context=context,
                replay=replay,
                historical_report=context["report"],
                work_order_id=work_order_id,
                runtime_test_certificate_id=str(
                    prepared["runtime_test_certificate_id"]
                ),
                extra=certificate_extra,
            )
        else:
            certificate = self._production_preflight_certificate_value(
                certificate_id=str(public["new_certificate_id"]),
                artifact_id=str(public["artifact_id"]),
                canonical_artifact_id=str(
                    public["canonical_artifact_id"]
                ),
                cache_components=context["cache_components"],
                report=context["report"],
                work_order_id=work_order_id,
                production_work_order_id=str(
                    context["handoff"]["work_order_id"]
                ),
                extra=certificate_extra,
            )
        self._validate_schema_record(
            self._project_schema,
            "production_preflight_certificate",
            certificate,
        )
        evidence_value = {
            "preflight_evidence_id": public["preflight_evidence_id"],
            "evidence_type": public["evidence_type"],
            "work_order_id": work_order_id,
            "gap_id": gap_id,
            "authorized_handoff_ids": copy.deepcopy(
                authorized_handoff_ids
            ),
            "trace_id": public["evidence_trace_id"],
            "batch_trace_id": trace_id,
            "plan_sha256": plan_sha256,
            "confirmation": PREFLIGHT_EVIDENCE_REISSUE_CONFIRMATION,
            "old_handoff_id": public["old_handoff_id"],
            "old_handoff_sha256": public["old_handoff_sha256"],
            "original_certificate_id": public["original_certificate_id"],
            "original_production_trace_id": prepared[
                "original_trace_id"
            ],
            "certificate_id": public["new_certificate_id"],
            "artifact_id": public["artifact_id"],
            "canonical_artifact_id": public["canonical_artifact_id"],
            "canonical_sha256": public["canonical_sha256"],
            "current_fingerprints": copy.deepcopy(
                context["current_fingerprints"]
            ),
            "current_fingerprints_sha256": public[
                "current_fingerprints_sha256"
            ],
            "runtime_test_certificate_id": prepared[
                "runtime_test_certificate_id"
            ],
            "workbook_projection_revalidation": copy.deepcopy(
                context["workbook_projection_revalidation"]
            ),
            "validation_report_sha256": public[
                "validation_report_sha256"
            ],
            "checked_shot_count": public["checked_shot_count"],
            "production_preflight_status": public[
                "production_preflight_status"
            ],
            "production_preflight_issue_count": 0,
            "independent_audit_status": "PENDING_AUDIT",
            "first_draft_grade_reconstructed": False,
            "authored_artifacts_modified": False,
            "history_records_modified": 0,
            "independent_audit_verdict_issued": False,
        }
        if isinstance(replay, dict):
            evidence_value.update(
                {
                    "production_validation_replay": copy.deepcopy(
                        replay
                    ),
                    "historical_validation_report": copy.deepcopy(
                        context["report"]
                    ),
                    "historical_validation_report_sha256": replay[
                        "historical_report_sha256"
                    ],
                    "current_validation_report": copy.deepcopy(
                        context["current_validation_report"]
                    ),
                    "current_validation_report_sha256": replay[
                        "current_report_sha256"
                    ],
                    "current_validation_error_histogram": copy.deepcopy(
                        replay["current_validation_error_histogram"]
                    ),
                    "current_validation_status": (
                        CURRENT_VALIDATION_FAIL
                    ),
                    "current_validation_issue_count": replay[
                        "current_validation_issue_count"
                    ],
                    "current_production_pass_claimed": False,
                    "current_preflight_cache_reusable": False,
                    "conclusion_reusable_for_independent_audit": False,
                    "requires_full_current_independent_audit": True,
                }
            )
        self._validate_schema_record(
            self._project_schema,
            "preflight_evidence_reissue",
            evidence_value,
        )
        stage_trace = self._controlled_production_revalidation_trace_ßžwß¦òµë(š+myÖç•ÒÀ¢†æFöfc¢F–7E·7G"Âç•ÒÀ¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢WfVçC¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢’ÓâGWÆU¶&ööÂÂF–7E·7G"Âç•ÒÂæöæRÂF–7E·7G"Âç•ÒÂæöæUÓ ¢""%&VBöæRFWFW&Ö–æ—7F–2f–ÆVBÖVF—B&W—"&ö¦V7F–öâà ¢Ö—76–ær6¶vRö†æFöfbô‡V"&÷w2&RF†RöæÇ’&V6÷fW&&ÆR7FFR†W&S°¢&W6VçBÖ'WBÖF–ffW&VçB6–væVB&V6÷&B&VÖ–ç26÷''WF–öâæBf–Ç0¢6Æ÷6VBâF†R6ÆÆW"Ö’&W—"Ö—76–ær&ö¦V7F–öâöæÇ’F‡&÷Vv‚F†P¢7F÷&vRÖÆ–W"ÆFW7BÖÆ–æVvR42à¢""  ¢–b–çB†6W'F–f–6FRævWB‚&—77VUö6÷VçB"’÷"’ÓÒ ¢&WGW&âG'VRÂæöæRÂæöæP¢W‡V7FVE÷6¶vRÂW‡V7FVEö†æFöfbÒ€¢6VÆbåöFWFW&Ö–æ—7F–5÷&W—%÷6¶vUöæEö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢&öGV7F–öåö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢¢–bW‡V7FVE÷6¶vR—2æöæR÷"W‡V7FVEö†æFöfb—2æöæS ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢f–ÆVBVF—B6ææ÷B&V'V–ÆB—G2FWFW&Ö–æ—7F–2&W—"&÷WFR ¢¢–bW‡V7FVE÷6¶vRævWB‚&VF—EöWfVçEö–B"’ÒWfVçBævWB‚&WfVçEö–B"“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢&W—"&÷WFRæBGW&&ÆRWfVçBF–ffW" ¢¢6W'F–f–6FUö–BÒ7G"†6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"’÷"""¢W‡V7FVE÷6¶vUö–BÒ7G"†W‡V7FVE÷6¶vU²'&W—%÷6¶vUö–B%Ò¢W‡V7FVEö†æFöfeö–BÒ7G"†W‡V7FVEö†æFöfe²&†æFöfeö–B%Ò¢F÷vç7G&VÕöWf–FVæ6RÒ6VÆbå÷&W—%÷6¶vUöF÷vç7G&VÕöWf–FVæ6R€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vUö–CÖW‡V7FVE÷6¶vUö–BÀ¢&6Uö'F–f7Eö–C×7G"†W‡V7FVE÷6¶vU²&&6Uö'F–f7Eö–B%Ò’À¢¢ÆÅ÷6¶vW2Ò6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G5÷7G&–7B€¢æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ'&W—%÷6¶vW2 ¢¢–bç’€¢&÷rævWB‚'&W—%÷6¶vUö–B"’ÓÒW‡V7FVE÷6¶vUö–@¢æB&÷rævWB‚&VF—Eö6W'F–f–6FUö–B"’Ò6W'F–f–6FUö–@¢f÷"&÷r–âÆÅ÷6¶vW0¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢FWFW&Ö–æ—7F–2&W—"6¶vR–FVçF—G’&VÆöæw2Fòæ÷F†W"6W'F–f–6FR ¢¢6¶vW2Ò°¢&÷p¢f÷"&÷r–âÆÅ÷6¶vW0¢–b&÷rævWB‚&VF—Eö6W'F–f–6FUö–B"’ÓÒ6W'F–f–6FUö–@¢Ð¢–bæ÷B6¶vW3 ¢–bF÷vç7G&VÕöWf–FVæ6S ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6öç7VÖVB&W—"WF†÷&—¦F–öâ6ææ÷B&R&W7W'&V7FVBg&öÒÖ—76–ær6¶vR7FFR ¢¢&WGW&âfÇ6RÂæöæRÂæöæP¢–bÆVâ‡6¶vW2’Ò ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVBf–ÆVBVF—B†2æòVæ—VR&W—"6¶vR ¢¢&W—%÷6¶vRÒ6¶vW5³Ð¢6¶vU÷7F÷&vUöf–VÆG2Ò²'&V6÷&Eö–B"Â'&ö¦V7Eö–B"Â&–çFVw&—G•ö†Ö2'Ð¢6¶vUö×WF&ÆUöf–VÆG2Ò°¢'7FGW2"À¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"À¢'7V66W76÷%ö'F–f7Eö–B"À¢'7V66W76÷%ö6æöæ–6Å÷6†#Sb"À¢'&W—%÷FõöVF—Eö†æFöfeö–B"À¢&6Æ–ÕövVæW&F–öâ"À¢&6Æ–Õö†—7F÷'’"À¢&6öç7V×F–öåö6Æ–Ò"À¢Ð¢6¶vUöÆÆ÷vVEöf–VÆG2Ò€¢6WB†W‡V7FVE÷6¶vR¢Â6¶vU÷7F÷&vUöf–VÆG0¢Â6¶vUö×WF&ÆUöf–VÆG0¢¢6¶vU÷7FGW2Ò&W—%÷6¶vRævWB‚'7FGW2"¢6¶vU÷G&ç6—F–öåöf–VÆG2Ò°¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"À¢'7V66W76÷%ö'F–f7Eö–B"À¢'7V66W76÷%ö6æöæ–6Å÷6†#Sb"À¢'&W—%÷FõöVF—Eö†æFöfeö–B"À¢Ð¢&W6VçE÷G&ç6—F–öåöf–VÆG2Ò°¢¶W’f÷"¶W’–â6¶vU÷G&ç6—F–öåöf–VÆG2–b¶W’–â&W—%÷6¶vP¢Ð¢fÆ–E÷6¶vU÷G&ç6—F–öâÒ€¢6¶vU÷7FGW2ÓÒ&—77VVB ¢æBæ÷B&W6VçE÷G&ç6—F–öåöf–VÆG0¢æBæ÷BF÷vç7G&VÕöWf–FVæ6P¢’÷"€¢6¶vU÷7FGW2ÓÒ&6öç7VÖVB ¢æB&W6VçE÷G&ç6—F–öåöf–VÆG2ÓÒ6¶vU÷G&ç6—F–öåöf–VÆG0¢æBÆÂ€¢&W—%÷6¶vRævWB†¶W’¢f÷"¶W’–â6¶vU÷G&ç6—F–öåöf–VÆG0¢¢æB—6–ç7Fæ6R‡&W—%÷6¶vRævWB‚&6öç7V×F–öåö6Æ–Ò"’ÂF–7B¢æB&W—%÷6¶vRævWB‚&6Æ–ÕövVæW&F–öâ"¢ÓÒ&W—%÷6¶vRævWB‚&6öç7V×F–öåö6Æ–Ò"Â·Ò’ævWB€¢&6Æ–ÕövVæW&F–öâ ¢¢¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&W—%÷6¶vR¢÷"&W—%÷6¶vRævWB‚'&V6÷&Eö–B"’ÒW‡V7FVE÷6¶vUö–@¢÷"&W—%÷6¶vRævWB‚'&ö¦V7Eö–B"’Ò&ö¦V7Eö–@¢÷"6WB‡&W—%÷6¶vR’Ò6¶vUöÆÆ÷vVEöf–VÆG0¢÷"ç’€¢&W—%÷6¶vRævWB†¶W’’ÒfÇVP¢f÷"¶W’ÂfÇVR–âW‡V7FVE÷6¶vRæ—FV×2‚¢–b¶W’Ò'7FGW2 ¢¢÷"æ÷BfÆ–E÷6¶vU÷G&ç6—F–öà¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVB&W—"6¶vRF–ffW'2g&öÒ—G2FWFW&Ö–æ—7F–2WF†÷&—G’ ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&–Ö×WF&ÆU÷&W—%÷6¶vR"Â&W—%÷6¶vP¢¢G'“ ¢VF—E÷Fõ÷&W—%ö†æFöfbÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖW‡V7FVEö†æFöfeö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2’ç7F'G7v—F‚‚'Væ¶æ÷vâ†æFöfg2&V6÷&Eö–C¢"“ ¢–b6¶vU÷7FGW2Ò&—77VVB# ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6öç7VÖVB&W—"&÷WFRÆ÷7B—G2VF—B×Fò×&W—"†æFöfb ¢’g&öÒW†0¢&WGW&âfÇ6RÂ&W—%÷6¶vRÂæöæP¢&—6P¢W‡V7FVE÷6VÆVEö†æFöfbÒ°¢¢¦6÷’æFVW6÷’†W‡V7FVEö†æFöfb’À¢'&V6÷&Eö–B#¢W‡V7FVEö†æFöfeö–BÀ¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢Ð¢7GVÅö†æFöfe÷v—F†÷WEö†Ö2Ò°¢¶W“¢6÷’æFVW6÷’‡fÇVR¢f÷"¶W’ÂfÇVR–âVF—E÷Fõ÷&W—%ö†æFöfbæ—FV×2‚¢–b¶W’Ò&–çFVw&—G•ö†Ö2 ¢Ð¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†VF—E÷Fõ÷&W—%ö†æFöfb¢÷"7GVÅö†æFöfe÷v—F†÷WEö†Ö2ÒW‡V7FVE÷6VÆVEö†æFöf`¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVB&W—"†æFöfbF–ffW'2g&öÒ—G2FWFW&Ö–æ—7F–2WF†÷&—G’ ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"ÂVF—E÷Fõ÷&W—%ö†æFöf`¢¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢6¶vU÷&÷w2Ò°¢&÷p¢f÷"&÷r–â‡V"ævWB‚'&W—%÷6¶vW2"ÂµÒ¢–b&÷rævWB‚'&W—%÷6¶vUö–B"’ÓÒW‡V7FVE÷6¶vUö–@¢Ð¢†æFöfe÷&÷w2Ò°¢&÷p¢f÷"&÷r–â‡V"ævWB‚&†æFöfg2"ÂµÒ¢–b&÷rævWB‚&†æFöfeö–B"’ÓÒW‡V7FVEö†æFöfeö–@¢Ð¢–bæ÷B6¶vU÷&÷w2÷"æ÷B†æFöfe÷&÷w3 ¢–b6¶vU÷7FGW2Ò&—77VVB# ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6öç7VÖVB&W—"&÷WFRÆ÷7B‡V"&ö¦V7F–öâ ¢¢&WGW&âfÇ6RÂ&W—%÷6¶vRÂVF—E÷Fõ÷&W—%ö†æFöf`¢–b€¢ÆVâ‡6¶vU÷&÷w2’Ò¢÷"ÆVâ††æFöfe÷&÷w2’Ò¢÷"6¶vU÷&÷w5³ÒÒ&W—%÷6¶vP¢÷"†æFöfe÷&÷w5³ÒÒVF—E÷Fõ÷&W—%ö†æFöf`¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVB&W—"‡V"&ö¦V7F–öâ6†ævVB–FVçF—G’ ¢¢–b6¶vU÷7FGW2ÓÒ&6öç7VÖVB# ¢6VÆbå÷fÆ–FFUö6öç7VÖVE÷&W—%÷6¶vU÷7FFR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vS×&W—%÷6¶vRÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢¢&WGW&âG'VRÂ&W—%÷6¶vRÂVF—E÷Fõ÷&W—%ö†æFöf` ¢FVbö6ö×ÆWFVEöVF—E÷&W7öç6R€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢'F–f7Eö–C¢7G"À¢6æöæ–6Åö†6ƒ¢7G"À¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢†æFöfc¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢WfVçC¢F–7E·7G"Âç•ÒÀ¢f–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢’ÓâFööÅ&W7öç6S ¢""%&WGW&â6ö×ÆWFVBVF—Bg&öÒ–Ö×WF&ÆR&V6÷&G2v—F†÷WBw&—FW2â""  ¢WF†÷&—G•öÖæ–fW7C¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢&ö÷BÂÆFW7BÒ6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÔfÇ6RÀ¢&W—%÷&ö¦V7F–öãÔfÇ6RÀ¢WF†÷&—G•öÖæ–fW7Eö÷WCÖWF†÷&—G•öÖæ–fW7BÀ¢¢–b€¢ÆFW7BævWB‚&†æFöfeö–B"’Ò†æFöfbævWB‚&†æFöfeö–B"¢÷"ÆFW7BævWB‚'7FGW2"’Ò&6öç7VÖVB ¢÷"ÆFW7BævWB‚&VF—Eö6W'F–f–6FUö–B"¢Ò6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢÷"6W'F–f–6FRævWB‚&WfVçEö–B"’ÒWfVçBævWB‚&WfVçEö–B"¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&WÆ•÷7WW'6VFVC¢æWvW"6ö×ÆWFVBVF—B—2WF†÷&—FF—fR ¢¢FVÆVÖWG'•ö–BÒ7G"€¢6W'F–f–6FRævWB‚&VF—E÷G&6Uö–B"¢÷"6W'F–f–6FRævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢¢FVÆVÖWG'’Ò6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–C×FVÆVÖWG'•ö–BÀ¢¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡FVÆVÖWG'’¢÷"FVÆVÖWG'’ævWB‚&÷W&F–öâ"’Ò&VF—E÷7F÷'–&ö&B ¢÷"FVÆVÖWG'’ævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"FVÆVÖWG'’ævWB‚&–çWEöf–ævW'&–çG2"’Òf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVBVF—BFVÆVÖWG'’6†ævVB–FVçF—G’ ¢¢÷fW&Æ—2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆ—7Eö'F–f7G2†æÖW76RÂ&ö¦V7Eö–B¢–b&÷rævWB‚&'F–f7E÷G—R"’ÓÒ'7F÷'–&ö&EöVF—Eö÷fW&Æ’ ¢æB&÷rævWB‚&ÖWFFF"Â·Ò’ævWB€¢&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B ¢¢ÓÒ6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢Ð¢–bÆVâ†÷fW&Æ—2’Ò ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVBVF—B†2æòVæ—VR÷fW&Æ’ ¢¢VF—FVE÷&V6÷&BÒ÷fW&Æ—5³Ð¢6÷W&6U÷&V6÷&BÂ÷6÷W&6U÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ'F–f7Eö–@¢¢6VÆbå÷fW&–g•öWF†÷&—FF—fUöVF—Eö÷fW&Æ’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷fW&Æ•÷&V6÷&CÖVF—FVE÷&V6÷&BÀ¢6÷W&6U÷&V6÷&C×6÷W&6U÷&V6÷&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢¢€¢&W—%÷&ö¦V7F–öåö6ö×ÆWFRÀ¢&W—%÷6¶vRÀ¢VF—E÷Fõ÷&W—%ö†æFöfbÀ¢’Ò6VÆbåö6ö×ÆWFVE÷&W—%÷&ö¦V7F–öå÷7FFR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢†æFöfcÖ†æFöfbÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢WfVçCÖWfVçBÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢–bæ÷B&W—%÷&ö¦V7F–öåö6ö×ÆWFS ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–æ6ö×ÆWFS¢6ö×ÆWFVBf–ÆVBVF—B&W—"&ö¦V7F–öâ—2–æ6ö×ÆWFR ¢¢&÷WFU÷G&6RÒæöæP¢–b†æFöfbævWB‚'&VVF—Eö¶–æB"’ÓÒ&–æFWVæFVçEöVF—E÷7V66W76÷"# ¢&÷WFU÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–C×7G"††æFöfbævWB‚'&VVF—E÷&÷WFU÷G&6Uö–B"’÷"""’À¢¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&÷WFU÷G&6R¢÷"&÷WFU÷G&6RævWB‚&÷W&F–öâ"¢Ò&VF—E÷7F÷'–&ö&E÷&VVF—E÷&÷WFR ¢÷"&÷WFU÷G&6RævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"&÷WFU÷G&6RævWB‚&–çWEöf–ævW'&–çG2"’Òf–ævW'&–çG0¢÷"&÷WFU÷G&6RævWB‚'v–æF÷u÷&öÆR"¢Ò&–æFWVæFVçEöVF—B ¢÷"&÷WFU÷G&6RævWB‚'F6µ÷G—R"¢Ò&–æFWVæFVçEöVF—B ¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6ö×ÆWFVB&÷WFRG&6R6†ævVB–FVçF—G’ ¢¢—77VUö6÷VçBÒÆVâ†WfVçBævWB‚&—77VW2"ÂµÒ’¢7FGW2Ò€¢'72 ¢–b6W'F–f–6FRævWB‚'&W7VÇB"’ÓÒ'72 ¢VÇ6R&&Æö6¶VB ¢–b6W'F–f–6FRævWB‚'&W7VÇB"’ÓÒ&&Æö6¶VB ¢VÇ6R&f–Åö7W'&VçB ¢¢7VÖÖ'’Òb.ZêŠê{¹>iéÎiÊ®XùŽûÙÎ™zîš)‡¶—77VUö6÷VçGÞšžûÙÎiÊ®ikZ)î‹ÚîjÊ ¢&öGV7F–öå÷G&6Uö–BÒ7G"††æFöfbævWB‚'7FvU÷G&6Uö–B"’÷"""¢÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–BÒ7G"€¢†æFöfbævWB‚'&öGV7F–öå÷7FvU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢¢&W÷'BÒ6÷’æFVW6÷’†6W'F–f–6FRævWB‚'&W÷'B"’÷"·Ò¢f7E÷F…öWf–FVæ6RÒ°¢&66†Uö†—B#¢G'VRÀ¢&66†U÷&V6öâ#¢&6ö×ÆWFVB6–væVBVF—B&V6V—B&WW6VB&VBÖöæÇ’"À¢'6VÖçF–5÷7V&Ö—76–öå÷&WV—&VB#¢fÇ6RÀ¢'6VÖçF–5÷7V&Ö—76–öå÷W6VB#¢fÇ6RÀ¢&Ö6†–æU÷fÆ–FF÷%öW†V7WFVB#¢fÇ6RÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'&Wf–WvVE÷6†÷Eö6÷VçB#¢6W'F–f–6FRævWB€¢'&Wf–WvVE÷6†÷Eö6÷VçB ¢’À¢'&Wf–WvVEö&F6…ö6÷VçB#¢6W'F–f–6FRævWB€¢'&Wf–WvVEö&F6…ö6÷VçB ¢’À¢'&Wf–WvVEöF¦6VçE÷—%ö6÷VçB#¢6W'F–f–6FRævWB€¢'&Wf–WvVEöF¦6VçE÷—%ö6÷VçB ¢’À¢&F¦6VçE÷—%ö÷&FW%÷6†#Sb#¢6W'F–f–6FRævWB€¢&F¦6VçE÷—%ö÷&FW%÷6†#Sb ¢’À¢'6VÖçF–5öVF—E÷6†#Sb#¢6W'F–f–6FRævWB€¢'6VÖçF–5öVF—E÷6†#Sb ¢’À¢'&öGV7F–öå÷G&6Uö–B#¢&öGV7F–öå÷G&6Uö–BÀ¢&÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–B#¢÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–BÀ¢'&VVF—E÷&÷WFU÷G&6Uö–B#¢†æFöfbævWB€¢'&VVF—E÷&÷WFU÷G&6Uö–B ¢’À¢&VF—E÷G&6Uö–B#¢FVÆVÖWG'•ö–BÀ¢&6W'F–f–6FUöVF—E÷G&6Uö–B#¢6W'F–f–6FRævWB€¢&VF—E÷G&6Uö–B ¢’À¢&ÖV7W&VÖVçE÷66÷R#¢'6W'fW%ööæÇ’"À¢Ð¢F÷væÆöE÷W&ÂÒ6VÆbç7F÷&Rç6–væVEöF÷væÆöE÷W&Â€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†VF—FVE÷&V6÷&E²&'F–f7Eö–B%Ò’À¢7G"†VF—FVE÷&V6÷&E²&f–ÆUöæÖR%Ò’À¢¢&W7öç6RÒFööÅ&W7öç6R€¢7FGW3×7FGW2À¢7VÖÖ'“×7VÖÖ'’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢G&6Uö–C×FVÆVÖWG'•ö–BÀ¢FF×°¢'&W÷'B#¢&W÷'BÀ¢&VF—B#¢°¢'7FGW2#¢&GWÆ–6FR"À¢&WfVçB#¢6÷’æFVW6÷’†WfVçB’À¢&–ævW7B#¢æöæRÀ¢ÒÀ¢&VF—Eö6Æ72#¢TD•Eô4Ä55ô”äDUTäDTåBÀ¢&VF—E÷v÷&µö÷&FW%ö–B#¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢&66†Uö†—B#¢G'VRÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'&öGV7F–öå÷G&6Uö–B#¢&öGV7F–öå÷G&6Uö–BÀ¢&÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–B#¢€¢÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–@¢’À¢'&VVF—E÷&÷WFU÷G&6R#¢&÷WFU÷G&6RÀ¢&VF—E÷G&6Uö–B#¢FVÆVÖWG'•ö–BÀ¢&f7E÷F…öWf–FVæ6R#¢f7E÷F…öWf–FVæ6RÀ¢&†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’††æFöfb’À¢&6W'F–f–6FR#¢6VÆbåö6W'F–f–6FU÷7VÖÖ'’†6W'F–f–6FR’À¢'&W—%÷6¶vR#¢&W—%÷6¶vRÀ¢&VF—E÷Fõ÷&W—%ö†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’€¢VF—E÷Fõ÷&W—%ö†æFöf`¢’À¢&VF—FVE÷v÷&¶&öö²#¢°¢&'F–f7Eö–B#¢VF—FVE÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&f–ÆUöæÖR#¢VF—FVE÷&V6÷&E²&f–ÆUöæÖR%ÒÀ¢&f–ævW'&–çB#¢VF—FVE÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢'7FGW2#¢7G"€¢6W'F–f–6FRævWB‚'&W7VÇB"’÷"&f–Åö7W'&VçB ¢’çWW"‚’À¢'6÷W&6Uö'F–f7Eö–B#¢'F–f7Eö–BÀ¢ÒÀ¢&VF—FVE÷v÷&¶&ööµöF÷væÆöE÷W&Â#¢F÷væÆöE÷W&ÂÀ¢'FVÆVÖWG'•ö–B#¢FVÆVÖWG'•ö–BÀ¢'FVÆVÖWG'’#¢FVÆVÖWG'’À¢ÒÀ¢¢6VÆbç7F÷&Ræ76W'EöÆFW7E÷&VVF—E÷&V6V—B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&ö÷Eö†æFöfeö–C×7G"€¢†æFöfbævWB‚'&VVF—E÷&ö÷Eö†æFöfeö–B"¢÷"†æFöfbævWB‚&†æFöfeö–B"¢÷"" ¢’À¢&ö÷Eö†æFöfeö†Ö3×7G"‡&ö÷BævWB‚&–çFVw&—G•ö†Ö2"’÷"""’À¢ÆFW7Eö†æFöfeö–C×7G"††æFöfbævWB‚&†æFöfeö–B"’÷"""’À¢ÆFW7Eö†æFöfeö†Ö3×7G"€¢†æFöfbævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢6W'F–f–6FUö–C×7G"†6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"’÷"""’À¢6W'F–f–6FUö†Ö3×7G"€¢6W'F–f–6FRævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢Æ–æVvUöWF†÷&—G•öÖæ–fW7CÖWF†÷&—G•öÖæ–fW7BÀ¢¢&WGW&â&W7öç6P ¢FVb÷fÆ–FFUöW†7E÷&VVF—Eö6W'F–f–6FUö&–æF–ær€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢WfVçC¢F–7E·7G"Âç•ÒÀ¢†æFöfc¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢f–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢66†Uö6ö×öæVçG3¢F–7E·7G"Â7G%ÒÀ¢'F–f7E÷&V6÷&C¢F–7E·7G"Âç•ÒÀ¢ÆÆ÷uöÆVv7•öÖ—76–æuöWfVçE÷6†¢&ööÂÒfÇ6RÀ¢’ÓâæöæS ¢""$&–æB6–væVB6W'F–f–6FRFò—G2W†7B&W÷'BÂWfVçBæB&÷WFRà ¢F†—2fÆ–FF–öâ'Vç2&Vf÷&Rç’&V6÷fW'’6Æ–Ò÷"&ö¦V7F–öâw&—FRà¢&V6÷&BF†B—26÷'&V7FÇ’„Ô2×6–væVB'WB–çFW&æÆÇ’–æ6öç6—7FVçB—0¢7F–ÆÂ–çfÆ–BWF†÷&—G’æB×W7BæWfW"&R6ö×ÆWFVB÷"&WÆ–VBà¢""  ¢&W÷'BÒ6W'F–f–6FRævWB‚'&W÷'B"¢6VÖçF–5÷7V&Ö—76–öâÒ€¢&W÷'BævWB‚'6VÖçF–5öVF—B"¢–b—6–ç7Fæ6R‡&W÷'BÂF–7B¢VÇ6RæöæP¢¢Wf–FVæ6UöÖG&—‚Ò€¢&W÷'BævWB‚&–æFWVæFVçEöWf–FVæ6UöÖG&—‚"¢–b—6–ç7Fæ6R‡&W÷'BÂF–7B¢VÇ6RæöæP¢¢&W÷'Eö—77VW2Ò€¢&W÷'BævWB‚&—77VW2"’–b—6–ç7Fæ6R‡&W÷'BÂF–7B’VÇ6RæöæP¢¢WfVçEö—77VW2ÒWfVçBævWB‚&—77VW2"¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†6W'F–f–6FR¢÷"æ÷B—6–ç7Fæ6R‡&W÷'BÂF–7B¢÷"æ÷B—6–ç7Fæ6R‡&W÷'Eö—77VW2ÂÆ—7B¢÷"æ÷B—6–ç7Fæ6R†WfVçEö—77VW2ÂÆ—7B¢÷"æ÷B—6–ç7Fæ6R‡6VÖçF–5÷7V&Ö—76–öâÂF–7B¢÷"æ÷B—6–ç7Fæ6R†Wf–FVæ6UöÖG&—‚ÂF–7B¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6W'F–f–6FRWF†÷&—G’—2Vç6–væVB÷"–æ6ö×ÆWFR ¢¢6W'F–f–6FUöWF†÷&—G’Ò6VÆbå÷fW&–f–VEöVF—Eö6W'F–f–6FUöWF†÷&—G’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢6VÖçF–5÷6†#SbÒ6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2‡6VÖçF–5÷7V&Ö—76–öâ¢¢W‡V7FVE÷&W7VÇBÒ€¢'72 ¢–bWfVçBævWB‚'&W7VÇB"’ÓÒ'72 ¢VÇ6R&&Æö6¶VB ¢–bWfVçBævWB‚'&W7VÇB"’ÓÒ&&Æö6¶VB ¢VÇ6R&f–Åö7W'&VçB ¢¢WfVçE÷G&6Uö–BÒ7G"†WfVçBævWB‚'7FvU÷G&6Uö–B"’÷"""¢W‡V7FVE÷&öGV7F–öå÷G&6Uö–BÒ7G"€¢†æFöfbævWB‚'&VVF—E÷&÷WFU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢¢W‡V7FVE÷&öGV7F–öå÷7FvU÷G&6Uö–BÒ7G"€¢†æFöfbævWB‚'&öGV7F–öå÷7FvU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢¢W‡V7FVEöf–ævW'&–çG2Ò°¢'6÷W&6R#¢f–ævW'&–çG5²'6÷W&6Uö'—FW2%ÒÀ¢&&÷VæF'’#¢f–ævW'&–çG5²&W—6öFUö&÷VæF'’%ÒÀ¢&'F–f7E÷&r#¢f–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢&'F–f7E÷6VÖçF–2#¢f–ævW'&–çG5²&'F–f7E÷6VÖçF–2%ÒÀ¢&6æöæ–6Â#¢f–ævW'&–çG5²&6æöæ–6Â%ÒÀ¢''6W"#¢f–ævW'&–çG5²''6W"%ÒÀ¢'66†VÖ#¢f–ævW'&–çG5²'66†VÖ%ÒÀ¢''VçF–ÖR#¢f–ævW'&–çG5²''VçF–ÖR%ÒÀ¢''VÆW2#¢f–ævW'&–çG5²''VÆW2%ÒÀ¢'fÆ–FF÷"#¢f–ævW'&–çG5²'fÆ–FF÷"%ÒÀ¢'&ö¦V7Eöæ6†÷'2#¢f–ævW'&–çG5²'&ö¦V7Eöæ6†÷'2%ÒÀ¢'F&vWEöÖöFVÅö6öçG&7B#¢f–ævW'&–çG5°¢'F&vWEöÖöFVÅö6öçG&7B ¢ÒÀ¢Ð¢&W÷'E÷7VÖÖ'’Ò6÷’æFVW6÷’‡&W÷'BævWB‚&VF—E÷7VÖÖ'’"’÷"·Ò¢W‡V7FVEö6W'F–f–6FU÷7VÖÖ'’Ò°¢¢§&W÷'E÷7VÖÖ'’À¢&VF—E÷G&6Uö–B#¢WfVçE÷G&6Uö–BÀ¢&6æöæ–6Å÷6†#Sb#¢66†Uö6ö×öæVçG5²&6æöæ–6Â%ÒÀ¢&'F–f7E÷&u÷6†#Sb#¢f–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢Ð¢W‡V7FVE÷fÇVW2Ò°¢&6W'F–f–6FU÷G—R#¢TD•Eô4Ä55ô”äDUTäDTåBÀ¢&VF—Eö6Æ72#¢TD•Eô4Ä55ô”äDUTäDTåBÀ¢'v÷&µö÷&FW%ö–B#¢VF—E÷v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’À¢'&öGV7F–öå÷v÷&µö÷&FW%ö–B#¢†æFöfbævWB‚'v÷&µö÷&FW%ö–B"’À¢&'F–f7Eö–B#¢'F–f7E÷&V6÷&BævWB‚&'F–f7Eö–B"’À¢&6æöæ–6Åö'F–f7Eö–B#¢'F–f7E÷&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB€¢&6æöæ–6Åö§6öåö'F–f7Eö–B ¢’À¢&6æöæ–6Å÷6†#Sb#¢66†Uö6ö×öæVçG5²&6æöæ–6Â%ÒÀ¢'6÷W&6Uöf–ævW'&–çB#¢66†Uö6ö×öæVçG5²'6÷W&6R%ÒÀ¢&&÷VæF'•ö†6‚#¢66†Uö6ö×öæVçG5²&&÷VæF'’%ÒÀ¢''VÆW6WE÷fW'6–öâ#¢66†Uö6ö×öæVçG5²''VÆW6WB%ÒÀ¢'fÆ–FF÷%÷fW'6–öâ#¢66†Uö6ö×öæVçG5²'fÆ–FF÷"%ÒÀ¢&66†Uö¶W’#¢6VÆbåö66†Uö¶W•ög&öÕö6ö×öæVçG2€¢66†Uö6ö×öæVçG0¢’À¢&66†Uö6ö×öæVçG2#¢66†Uö6ö×öæVçG2À¢&6†V6¶VE÷6†÷Eö6÷VçB#¢–çB‡&W÷'BævWB‚'6†÷Eö6÷VçB"ÂÓ’’À¢'&W÷'E÷6†#Sb#¢6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2‡&W÷'B’’À¢'&W7VÇB#¢W‡V7FVE÷&W7VÇBÀ¢&—77VUö6÷VçB#¢ÆVâ‡&W÷'Eö—77VW2’À¢&WfVçEö–B#¢WfVçBævWB‚&WfVçEö–B"’À¢&†æFöfeö–B#¢†æFöfbævWB‚&†æFöfeö–B"’À¢'7FvU÷G&6Uö–B#¢WfVçE÷G&6Uö–BÀ¢&VF—E÷G&6Uö–B#¢WfVçE÷G&6Uö–BÀ¢'&öGV7F–öå÷G&6Uö–B#¢W‡V7FVE÷&öGV7F–öå÷G&6Uö–BÀ¢'&öGV7F–öå÷7FvU÷G&6Uö–B#¢€¢W‡V7FVE÷&öGV7F–öå÷7FvU÷G&6Uö–@¢’À¢&f–ævW'&–çG2#¢W‡V7FVEöf–ævW'&–çG2À¢&–æFWVæFVçEööe÷&öGV7F–öâ#¢G'VRÀ¢'&WfVçF—fUövFU÷&W7VÇG2#¢Wf–FVæ6UöÖG&—‚ævWB€¢'&WfVçF—fUövFU÷&W7VÇG2 ¢’À¢&VF—EöÆ–W%÷&W7VÇG2#¢Wf–FVæ6UöÖG&—‚ævWB€¢&VF—EöÆ–W%÷&W7VÇG2 ¢’À¢'VÆ—G•÷F‡&W6†öÆG2#¢Wf–FVæ6UöÖG&—‚ævWB€¢'VÆ—G•÷F‡&W6†öÆG2 ¢’À¢&VF—Eö7F÷%ö&–æF–æuö–B#¢6W'F–f–6FUöWF†÷&—G’ævWB€¢&7F÷%ö&–æF–æuö–B ¢’À¢&VF—E÷6W76–öåöf–ævW'&–çB#¢6W'F–f–6FUöWF†÷&—G’ævWB€¢'6W76–öåöf–ævW'&–çB ¢’À¢'6VÖçF–5öVF—Eö6öçG&7B#¢€¢4TÔåD”5ôTD•Eô4ôåE$5EõdU%4”ôà¢’À¢'6VÖçF–5öVF—E÷6†#Sb#¢6VÖçF–5÷6†#SbÀ¢'&Wf–WvVE÷6†÷Eö6÷VçB#¢–çB€¢6VÖçF–5÷7V&Ö—76–öâævWB‚'&Wf–WvVE÷6†÷Eö6÷VçB"ÂÓ¢’À¢'&Wf–WvVEö&F6…ö6÷VçB#¢–çB€¢6VÖçF–5÷7V&Ö—76–öâævWB‚'&Wf–WvVEö&F6…ö6÷VçB"ÂÓ¢’À¢'&Wf–WvVEöF¦6VçE÷—%ö6÷VçB#¢–çB€¢6VÖçF–5÷7V&Ö—76–öâævWB€¢'&Wf–WvVEöF¦6VçE÷—%ö6÷VçB"ÂÓ¢¢’À¢&F¦6VçE÷—%ö÷&FW%÷6†#Sb#¢7G"€¢6VÖçF–5÷7V&Ö—76–öâævWB€¢&F¦6VçE÷—%ö÷&FW%÷6†#Sb"Â" ¢¢’À¢&F¦6VçEö6öçF–çV—G’#¢Wf–FVæ6UöÖG&—‚ævWB€¢&F¦6VçEö6öçF–çV—G’ ¢’À¢'fö–6Uöf–VÆEö6÷fW&vR#¢Wf–FVæ6UöÖG&—‚ævWB€¢'fö–6Uöf–VÆEö6÷fW&vR ¢’À¢&FFVEöF–ÆöwVUö6÷fW&vR#¢Wf–FVæ6UöÖG&—‚ævWB€¢&FFVEöF–ÆöwVUö6÷fW&vR ¢’À¢&6öçfW'6F–öå÷66VæUö6Æ76–f–6F–öåö6÷fW&vR#¢Wf–FVæ6UöÖG&—‚ævWB€¢&6öçfW'6F–öå÷66VæUö6Æ76–f–6F–öåö6÷fW&vR ¢’À¢'6÷W&6Uö–ævW7F–öåöÖöFR#¢€¢&6†FW%÷Fõ÷7F÷'–&ö&B ¢–b—6–ç7Fæ6R‡&W÷'BævWB‚&6†FW%ö6÷fW&vR"’ÂF–7B¢VÇ6R&æöåö6†FW" ¢’À¢'6VÖçF–5ö6öæ6ÇW6–öâ#¢6VÖçF–5÷7V&Ö—76–öâævWB‚&6öæ6ÇW6–öâ"’À¢&VF—E÷7VÖÖ'’#¢W‡V7FVEö6W'F–f–6FU÷7VÖÖ'’À¢Ð¢–bæ÷B€¢ÆÆ÷uöÆVv7•öÖ—76–æuöWfVçE÷6†¢æB&WfVçE÷6†#Sb"æ÷B–â6W'F–f–6FP¢“ ¢W‡V7FVE÷fÇVW5²&WfVçE÷6†#Sb%ÒÒ6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2†WfVçB¢¢Ö—6ÖF6†VBÒ°¢¶W¢f÷"¶W’ÂfÇVR–âW‡V7FVE÷fÇVW2æ—FV×2‚¢–b÷7F&ÆUö§6öåö'—FW2†6W'F–f–6FRævWB†¶W’’¢Ò÷7F&ÆUö§6öåö'—FW2‡fÇVR¢Ð¢WfVçE÷6VÖçF–5÷6†#SbÒWfVçBævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢–b€¢Ö—6ÖF6†V@¢÷"&W÷'BævWB‚'6VÖçF–5öVF—E÷6†#Sb"’Ò6VÖçF–5÷6†#S`¢÷"Wf–FVæ6UöÖG&—‚ævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢Ò6VÖçF–5÷6†#S`¢÷"ÆVâ†WfVçEö—77VW2’ÒÆVâ‡&W÷'Eö—77VW2¢÷"÷7F&ÆUö§6öåö'—FW2†WfVçBævWB‚&6†V6¶VE÷6†÷Eö6÷VçB"’¢Ò÷7F&ÆUö§6öåö'—FW2†6W'F–f–6FRævWB‚&6†V6¶VE÷6†÷Eö6÷VçB"’¢÷"€¢WfVçE÷6VÖçF–5÷6†#Sbæ÷B–â„æöæRÂ""¢æBWfVçE÷6VÖçF–5÷6†#SbÒ6VÖçF–5÷6†#S`¢¢“ ¢FWF–Ç2Ò"Â"æ¦ö–â†Ö—6ÖF6†VB’÷"&WfVçB÷&W÷'B6÷VçG2 ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢6W'F–f–6FRÂ&W÷'BÂWfVçBÂ†æFöfbÂv÷&²÷&FW"Â÷"f–ævW'&–çG2F–ffW#¢ ¢²FWF–Ç0¢ ¢FVb÷fÆ–FFU÷&VVF—EöÆ–æVvUö6W'F–f–6FUöWF†÷&—G’€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢&ö÷C¢F–7E·7G"Âç•ÒÀ¢÷&FW&VE÷&÷WFW3¢Æ—7E¶F–7E·7G"Âç•ÕÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢f–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçC¢&ööÂÒfÇ6RÀ¢’ÓâÆ—7E¶F–7E·7G"Âç•ÕÓ ¢""%&WfÆ–FFRWfW'’6ö×ÆWFVB&÷WFRw26W'F–f–6FRæBÆVFvW"WfVçBâ""  ¢'F–f7Eö–BÒ7G"‡&ö÷BævWB‚&Æöv–6Åö'F–f7Eö–B"’÷"""¢7F÷'–&ö&BÂ'F–f7E÷&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ'F–f7Eö–@¢¢6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢6÷W&6Uöf–ævW'&–çBÒ6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢'F–f7E÷&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"’À¢'F–f7E÷&V6÷&BÀ¢¢&÷VæF'•ö†6‚Ò6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B¢&V6ö×WFVEöf–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢7F÷'–&ö&BÀ¢'F–f7E÷&V6÷&BÀ¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢¢–b&V6ö×WFVEöf–ævW'&–çG2Òf–ævW'&–çG3 ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢7W'&VçB'F–f7Bf–ævW'&–çG2F–ffW"g&öÒF†R6–væVB&÷WFR ¢¢'VçF–ÖUö6W'F–f–6FRÒ6VÆbå÷&WV—&U÷'VçF–ÖU÷FW7Eö6W'F–f–6FR€¢æÖW76RÂ&ö¦V7Eö–@¢¢ÆVFvW"Ò6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B¢Öæ–fW7C¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢6ö×ÆWFVE÷&÷WFW2Ò°¢&÷WFP¢f÷"&÷WFR–â÷&FW&VE÷&÷WFW0¢–b&÷WFRævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢Ð¢6ö×ÆWFVE÷F–Åö—5÷66÷UöWF†÷&—G’Ò&ööÂ€¢÷&FW&VE÷&÷WFW0¢æB÷&FW&VE÷&÷WFW5²ÓÒævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢¢&÷WFU÷÷6—F–öç2Ò°¢7G"‡&÷WFRævWB‚&†æFöfeö–B"’÷"""“¢–æFW€¢f÷"–æFW‚Â&÷WFR–âVçVÖW&FR†÷&FW&VE÷&÷WFW2¢Ð¢f÷"&÷WFUö–æFW‚Â&÷WFR–âVçVÖW&FR†6ö×ÆWFVE÷&÷WFW2“ ¢&÷WFUö–BÒ7G"‡&÷WFRævWB‚&†æFöfeö–B"’÷"""¢6W'F–f–6FUö–BÒ7G"€¢&÷WFRævWB‚&VF—Eö6W'F–f–6FUö–B"’÷"" ¢¢6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–CÖ6W'F–f–6FUö–BÀ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbåöÆVFvW%÷66†VÖÀ¢&–æFWVæFVçEöVF—Eö6W'F–f–6FR"À¢6W'F–f–6FRÀ¢¢6W'F–f–6FUöWF†÷&—G’Ò€¢6VÆbå÷fW&–f–VEöVF—Eö6W'F–f–6FUöWF†÷&—G’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢¢WF†÷&—G•ö6öçF–çVF–öåö†Ö2Ò" ¢–b6W'F–f–6FUöWF†÷&—G’ævWB‚&6öçF–çVF–öåö–B"“ ¢6öçF–çVF–öâÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&WF†÷&—G•ö6öçF–çVF–öç2"À¢&V6÷&Eö–C×7G"€¢6W'F–f–6FUöWF†÷&—G•²&6öçF–çVF–öåö–B%Ð¢’À¢¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†6öçF–çVF–öâ¢÷"6öçF–çVF–öâævWB‚&7F—fUö&–æF–ær"¢Ò6W'F–f–6FUöWF†÷&—G¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢6W'F–f–6FR6öçF–çVF–öâWF†÷&—G’F–ffW'2 ¢¢WF†÷&—G•ö6öçF–çVF–öåö†Ö2Ò7G"€¢6öçF–çVF–öâævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢¢WfVçEö–BÒ7G"†6W'F–f–6FRævWB‚&WfVçEö–B"’÷"""¢ÖF6†–æuöWfVçG2Ò°¢&÷p¢f÷"&÷r–âÆVFvW"ævWB‚&WfVçG2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB7G"‡&÷rævWB‚&WfVçEö–B"’÷"""’ÓÒWfVçEö–@¢Ð¢–bÆVâ†ÖF6†–æuöWfVçG2’Ò ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢6W'F–f–6FR†2æòVæ—VRÆVFvW"WfVçB ¢¢WfVçBÒÖF6†–æuöWfVçG5³Ð¢WfVçE÷6†#SbÒ6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2†WfVçB’¢&÷WFU÷÷6—F–öâÒ&÷WFU÷÷6—F–öç2ævWB‡&÷WFUö–BÂÓ¢6†–ÆBÒ€¢÷&FW&VE÷&÷WFW5·&÷WFU÷÷6—F–öâ²Ð¢–bÃÒ&÷WFU÷÷6—F–öâÂÆVâ†÷&FW&VE÷&÷WFW2’Ò¢VÇ6RæöæP¢¢–b6†–ÆB—2æ÷BæöæRæB€¢6†–ÆBævWB‚'&Wf–÷W5öVF—Eö6W'F–f–6FUö–B"¢Ò6W'F–f–6FUö–@¢÷"6†–ÆBævWB‚'&Wf–÷W5öVF—Eö6W'F–f–6FUö†Ö2"¢Ò6W'F–f–6FRævWB‚&–çFVw&—G•ö†Ö2"¢÷"6†–ÆBævWB‚'&Wf–÷W5öVF—EöWfVçE÷6†#Sb"¢ÒWfVçE÷6†#S`¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢7V66W76÷"FöW2æ÷B6VÂ—G2&VçB6W'F–f–6FRæBWfVçB ¢¢6W'F–f–6FUö†5öWfVçE÷6†Ò&WfVçE÷6†#Sb"–â6W'F–f–6FP¢ÆÆ÷uöÆVv7•öÖ—76–æuöWfVçE÷6†Ò&ööÂ€¢æ÷B6W'F–f–6FUö†5öWfVçE÷6†¢æB€¢6†–ÆB—2æ÷BæöæP¢÷"€¢&÷WFU÷÷6—F–öâÓÒÆVâ†÷&FW&VE÷&÷WFW2’Ò¢æBÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&Vç@¢¢¢¢–b€¢æ÷B6W'F–f–6FUö†5öWfVçE÷6†¢æBæ÷BÆÆ÷uöÆVv7•öÖ—76–æuöWfVçE÷6†¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢ÆVv7’6W'F–f–6FRæVVG2g&W6‚7V66W76÷"VF—B&Vf÷&R—B6âWF†÷&—¦R&WÆ’ ¢¢7W'&VçEö66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢'F–f7Eöf–ævW'&–çC×7G"€¢'F–f7E÷&V6÷&BævWB‚&f–ævW'&–çB"’÷"" ¢’À¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢66÷S×6VÆbåö–æFWVæFVçEöVF—Eö66†U÷66÷R€¢&÷WFRÂ'F–f7Eö–@¢’À¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢'VçF–ÖUö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"’÷"" ¢’À¢¢&÷WFUö—5ö†—7F÷&–6Å÷&VçBÒ&ööÂ€¢6†–ÆB—2æ÷BæöæP¢÷"€¢&÷WFU÷÷6—F–öâÓÒÆVâ†÷&FW&VE÷&÷WFW2’Ò¢æBÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&Vç@¢¢¢66†Uö6ö×öæVçG2Ò7W'&VçEö66†Uö6ö×öæVçG0¢–b&÷WFUö—5ö†—7F÷&–6Å÷&VçC ¢W'6—7FVEö66†Uö6ö×öæVçG2Ò6W'F–f–6FRævWB€¢&66†Uö6ö×öæVçG2 ¢¢–b€¢æ÷B—6–ç7Fæ6R‡W'6—7FVEö66†Uö6ö×öæVçG2ÂF–7B¢÷"6WB‡W'6—7FVEö66†Uö6ö×öæVçG2¢Ò6WB†7W'&VçEö66†Uö6ö×öæVçG2¢÷"ç’€¢W'6—7FVEö66†Uö6ö×öæVçG2ævWB†¶W’’ÒfÇVP¢f÷"¶W’ÂfÇVR–â7W'&VçEö66†Uö6ö×öæVçG2æ—FV×2‚¢–b¶W’Ò'6VÖçF–5öVF—Eö6öçG&7B ¢¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢†—7F÷&–6Â6W'F–f–6FR66†R6†ævVB÷WG6–FRF†R6VÖçF–2VF—B6öçG&7B ¢¢66†Uö6ö×öæVçG2Ò6÷’æFVW6÷’€¢W'6—7FVEö66†Uö6ö×öæVçG0¢¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçCÖWfVçBÀ¢†æFöfc×&÷WFRÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢&WV—&UöÆFW7E÷66÷SÒ€¢6ö×ÆWFVE÷F–Åö—5÷66÷UöWF†÷&—G¢æB&÷WFUö–æFW‚ÓÒÆVâ†6ö×ÆWFVE÷&÷WFW2’Ò¢’À¢¢&W÷'BÒ6W'F–f–6FRævWB‚'&W÷'B"¢–bæ÷B—6–ç7Fæ6R‡&W÷'BÂF–7B“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢6W'F–f–6FR&W÷'B—2Ö—76–ær ¢¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçEö6öçFVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçCÖWfVçBÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢&W÷'C×&W÷'BÀ¢&WV—&UöÆFW7E÷66÷SÒ€¢6ö×ÆWFVE÷F–Åö—5÷66÷UöWF†÷&—G¢æB&÷WFUö–æFW‚ÓÒÆVâ†6ö×ÆWFVE÷&÷WFW2’Ò¢’À¢¢6VÆbå÷fÆ–FFUöW†7E÷&VVF—Eö6W'F–f–6FUö&–æF–ær€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢WfVçCÖWfVçBÀ¢†æFöfc×&÷WFRÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢ÆÆ÷uöÆVv7•öÖ—76–æuöWfVçE÷6†Ò€¢ÆÆ÷uöÆVv7•öÖ—76–æuöWfVçE÷6†¢’À¢¢Öæ–fW7BæVæB€¢°¢&†æFöfeö–B#¢&÷WFUö–BÀ¢&†æFöfeö†Ö2#¢7G"€¢&÷WFRævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢&6W'F–f–6FUö–B#¢6W'F–f–6FUö–BÀ¢&6W'F–f–6FUö†Ö2#¢7G"€¢6W'F–f–6FRævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢&WfVçEö–B#¢WfVçEö–BÀ¢&WfVçE÷6†#Sb#¢WfVçE÷6†#SbÀ¢&ÆVv7•öWfVçE÷6†öÖ—76–ær#¢€¢æ÷B6W'F–f–6FUö†5öWfVçE÷6†¢’À¢&WF†÷&—G•÷&ö÷Eö–B#¢7G"€¢6W'F–f–6FUöWF†÷&—G’ævWB‚&WF†÷&—G•÷&ö÷Eö–B"’÷"" ¢’À¢&WF†÷&—G•öWö6…ö–B#¢7G"€¢6W'F–f–6FUöWF†÷&—G’ævWB‚&WF†÷&—G•öWö6…ö–B"’÷"" ¢’À¢&WF†÷&—G•övVæW&F–öâ#¢–çB€¢6W'F–f–6FUöWF†÷&—G’ævWB‚&vVæW&F–öâ"’÷" ¢’À¢&WF†÷&—G•ö6öçF–çVF–öåö–B#¢€¢6W'F–f–6FUöWF†÷&—G’ævWB‚&6öçF–çVF–öåö–B"¢’À¢&WF†÷&—G•ö6öçF–çVF–öåö†Ö2#¢€¢WF†÷&—G•ö6öçF–çVF–öåö†Ö0¢’À¢&WF†÷&—G•ö6öçG&7E÷6†#Sb#¢€¢7G"€¢6W'F–f–6FUöWF†÷&—G’ævWB€¢&WF†÷&—G•ö6öçG&7E÷6†#Sb ¢¢÷"" ¢¢’À¢Ð¢¢&WGW&âÖæ–fW7@ ¢FVb÷&V6÷fW&&ÆU÷&VVF—Eö6W'F–f–6FR€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢VæF–æuö†æFöfc¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢f–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢66†Uö6ö×öæVçG3¢F–7E·7G"Â7G%ÒÀ¢'F–f7E÷&V6÷&C¢F–7E·7G"Âç•ÒÀ¢’ÓâGWÆU¶F–7E·7G"Âç•ÒÂæöæRÂF–7E·7G"Âç•ÒÂæöæUÓ ¢""%&WGW&âF†RVæ—VR6W'B¶WfVçB—"g&öÒ&RÖf–æ—6‚7&6‚à ¢6ö×ÆWFR—"&W7VÖW2f–æÆ—¦F–öâââWfVçBÖöæÇ’†&BÖ7&6‚7FFP¢Ö’ÆFW"Ö–çBöæRFWFW&Ö–æ—7F–26W'F–f–6FRÂ'WB6W'F–f–6FRv—F€¢æò–Ö×WF&ÆRWfVçBÇv—2f–Ç26Æ÷6VBà¢""  ¢†æFöfeö–BÒ7G"‡VæF–æuö†æFöfbævWB‚&†æFöfeö–B"’÷"""¢WfVçE÷G&6Uö–BÒ7G"€¢VæF–æuö†æFöfbævWB‚'&VVF—EöWfVçE÷G&6Uö–B"’÷"" ¢¢W‡V7FVEö66†Uö¶W’Ò6VÆbåö66†Uö¶W•ög&öÕö6ö×öæVçG2€¢66†Uö6ö×öæVçG0¢¢6W'F–f–6FW2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G5÷7G&–7B€¢æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ&6W'F–f–6FW2 ¢¢–b&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfeö–@¢÷"&÷rævWB‚'7FvU÷G&6Uö–B"’ÓÒWfVçE÷G&6Uö–@¢÷"&÷rævWB‚&66†Uö¶W’"’ÓÒW‡V7FVEö66†Uö¶W¢Ð¢ÆVFvW"Ò6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B¢WfVçG2Ò°¢&÷p¢f÷"&÷r–âÆVFvW"ævWB‚&WfVçG2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB€¢&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfeö–@¢÷"&÷rævWB‚'7FvU÷G&6Uö–B"’ÓÒWfVçE÷G&6Uö–@¢÷"&÷rævWB‚&VF—Eö66†Uö¶W’"’ÓÒW‡V7FVEö66†Uö¶W¢¢Ð¢–bæ÷B6W'F–f–6FW2æBæ÷BWfVçG3 ¢&WGW&âæöæRÂæöæP¢–b€¢VæF–æuö†æFöfbævWB‚'7FGW2"’æ÷B–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢÷"ÆVâ†WfVçG2’Ò¢÷"ÆVâ†6W'F–f–6FW2’â¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–æ6ö×ÆWFS¢VæF–ær&÷WFR×W7B†fRW†7FÇ’öæRÆVFvW"WfVçBæBBÖ÷7BöæR6–væVB6W'F–f–6FR ¢¢WfVçBÒWfVçG5³Ð¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçCÖWfVçBÀ¢†æFöfc×VæF–æuö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢¢–bæ÷B6W'F–f–6FW3 ¢&WGW&âæöæRÂWfVç@¢6W'F–f–6FRÒ6W'F–f–6FW5³Ð¢6VÆbå÷fÆ–FFUöW†7E÷&VVF—Eö6W'F–f–6FUö&–æF–ær€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢WfVçCÖWfVçBÀ¢†æFöfc×VæF–æuö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢¢&WGW&â6W'F–f–6FRÂWfVç@ ¢FVb÷&V6÷fW&&ÆU÷&ö÷EöVF—Eö6W'F–f–6FR€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢VæF–æuö†æFöfc¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢f–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢66†Uö6ö×öæVçG3¢F–7E·7G"Â7G%ÒÀ¢'F–f7E÷&V6÷&C¢F–7E·7G"Âç•ÒÀ¢6æF–FFS¢F–7E·7G"Âç•ÒÀ¢’ÓâF–7E·7G"Âç•ÒÂæöæS ¢""%fW&–g’F†R–Ö×WF&ÆRWfVçB¶6W'F–f–6FR—"g&öÒ&ö÷BÖVF—B&WG'’à ¢6Vv‡Bf–ÇW&RgFW"F†÷6RGvò&V6÷&G2&RGW&&ÆR&VÆV6W2F†P¢&öGV7F–öâ†æFöfb&6²Fò&W&VFâöæÇ’F†BW†7BÂVæ—VR— ¢Ö’&W7VÖRF†R&VÖ–æ–ær6¶vRö÷fW&Æ’÷FVÆVÖWG'’&ö¦V7F–öç2à¢""  ¢–b€¢VæF–æuö†æFöfbævWB‚'&VVF—Eö¶–æB"¢÷"VæF–æuö†æFöfbævWB‚'7FGW2"¢æ÷B–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢÷"VæF–æuö†æFöfbævWB‚&VF—Eö6W'F–f–6FUö–B"¢“ ¢&WGW&âæöæP¢†æFöfeö–BÒ7G"‡VæF–æuö†æFöfbævWB‚&†æFöfeö–B"’÷"""¢6W'F–f–6FW2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G5÷7G&–7B€¢æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ&6W'F–f–6FW2 ¢¢–b&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfeö–@¢Ð¢WfVçG2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B’ævWB€¢&WfVçG2"ÂµÐ¢¢–b—6–ç7Fæ6R‡&÷rÂF–7B’æB&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfeö–@¢Ð¢–bæ÷B6W'F–f–6FW2æBæ÷BWfVçG3 ¢&WGW&âæöæP¢–bÆVâ†6W'F–f–6FW2’Ò÷"ÆVâ†WfVçG2’Ò ¢&—6RfÇVTW'&÷"€¢&VF—E÷&V6÷fW'•ö–æ6ö×ÆWFS¢VæF–ær&ö÷B&÷WFR×W7B†fRW†7FÇ’öæR6–væVB6W'F–f–6FRæBÆVFvW"WfVçB ¢¢6W'F–f–6FRÒ6W'F–f–6FW5³Ð¢WfVçBÒWfVçG5³Ð¢–b6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"’Ò6æF–FFRævWB€¢&6W'F–f–6FUö–B ¢“ ¢&—6RfÇVTW'&÷"€¢&VF—E÷&V6÷fW'•ö–çfÆ–C¢66†VB&ö÷B6W'F–f–6FR–FVçF—G’F–ffW'2 ¢¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçCÖWfVçBÀ¢†æFöfc×VæF–æuö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢¢6VÆbå÷fÆ–FFUöW†7E÷&VVF—Eö6W'F–f–6FUö&–æF–ær€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢WfVçCÖWfVçBÀ¢†æFöfc×VæF–æuö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&CÖ'F–f7E÷&V6÷&BÀ¢¢&WGW&â6W'F–f–6FP ¢FVb÷W'6—7E÷&VVF—E÷7V66W76÷"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢&ö÷C¢F–7E·7G"Âç•ÒÀ¢&VçC¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢VF—EöWfVçE÷G&6Uö–C¢7G"À¢f–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢'F–f7Eö–C¢7G"À¢6†÷Eö6÷VçC¢–çBÀ¢’ÓâGWÆU¶F–7E·7G"Âç•ÒÂF–7E·7G"Âç•ÕÓ ¢""$Ö–çBg&W6‚6–væVB&÷WFRöæÇ’gFW"6VÖçF–2fÆ–FF–öâ7V66VVG2â""  ¢&÷WFU÷7F'FVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢&VçEöWF†÷&—G•öÖæ–fW7C¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢fW&–f–VE÷&ö÷BÂfW&–f–VEöÆFW7BÒ6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfc×&VçBÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÕG'VRÀ¢&W—%÷&ö¦V7F–öãÔfÇ6RÀ¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçCÕG'VRÀ¢WF†÷&—G•öÖæ–fW7Eö÷WC×&VçEöWF†÷&—G•öÖæ–fW7BÀ¢¢–b€¢fW&–f–VE÷&ö÷BævWB‚&†æFöfeö–B"’Ò&ö÷BævWB‚&†æFöfeö–B"¢÷"fW&–f–VEöÆFW7BævWB‚&†æFöfeö–B"¢Ò&VçBævWB‚&†æFöfeö–B"¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢7V66W76÷"&VçB6†ævVB&Vf÷&R—77VR ¢¢7W'&VçEöVF—Eö&–æF–ærÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÂ&ö¦V7Eö–BÂVF—E÷v÷&µö÷&FW ¢¢VF—Eö&–æF–ærÒ6VÆbå÷fW&–f–VEöWF†÷&—G•ö&–æF–æuöEövVæW&F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢VF—E÷v÷&µö÷&FW"À¢–çB†7W'&VçEöVF—Eö&–æF–ærævWB‚&vVæW&F–öâ"’÷"’À¢ ¢FVb&VçEöWF†÷&—G’††æFöfeö–C¢7G"’ÓâF–7E·7G"Âç•Ó ¢ÖF6†W2Ò°¢&÷p¢f÷"&÷r–â&VçEöWF†÷&—G•öÖæ–fW7@¢–b&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfeö–@¢Ð¢–bÆVâ†ÖF6†W2’Ò ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢7V66W76÷"&VçBWF†÷&—G’—2–æ6ö×ÆWFR ¢¢&WGW&âÖF6†W5³Ð ¢FVb&÷WFU÷G&6Uöf÷"‡&÷WFU÷G&6Uö–C¢7G"’ÓâF–7E·7G"Âç•Ó ¢G'“ ¢W†—7F–æu÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–C×&÷WFU÷G&6Uö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B7G"†W†2’ç7F'G7v—F‚‚'Væ¶æ÷vâFVÆVÖWG'’&V6÷&Eö–C¢"“ ¢&—6P¢W†—7F–æu÷G&6RÒæöæP¢–bW†—7F–æu÷G&6R—2æ÷BæöæS ¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†W†—7F–æu÷G&6R¢÷"W†—7F–æu÷G&6RævWB‚&÷W&F–öâ"¢Ò&VF—E÷7F÷'–&ö&E÷&VVF—E÷&÷WFR ¢÷"W†—7F–æu÷G&6RævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"W†—7F–æu÷G&6RævWB‚&–çWEöf–ævW'&–çG2"¢Òf–ævW'&–çG0¢÷"W†—7F–æu÷G&6RævWB‚'v–æF÷u÷&öÆR"¢Ò&–æFWVæFVçEöVF—B ¢÷"W†—7F–æu÷G&6RævWB‚'F6µ÷G—R"¢Ò&–æFWVæFVçEöVF—B ¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷G&6Uö6öæfÆ–7C¢W'6—7FVB&÷WFRFVÆVÖWG'’6†ævVB–FVçF—G’ ¢¢&WGW&âW†—7F–æu÷G&6P¢&WGW&â6VÆbå÷6fU÷FVÆVÖWG'’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷W&F–öãÒ&VF—E÷7F÷'–&ö&E÷&VVF—E÷&÷WFR"À¢7F'FVEöç3×&÷WFU÷7F'FVEöç2À¢7FvW5ö×3×°¢&Æ–æVvU÷fW&–f–6F–öåöæE÷&÷WFUö—77VR#¢€¢F–ÖRçW&eö6÷VçFW%öç2‚’Ò&÷WFU÷7F'FVEöç0¢¢òóóÀ¢ÒÀ¢66†Uö†—CÔfÇ6RÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢FVÆVÖWG'•ö–C×&÷WFU÷G&6Uö–BÀ¢v–æF÷u÷&öÆSÒ&–æFWVæFVçEöVF—B"À¢F6µ÷G—SÒ&–æFWVæFVçEöVF—B"À¢–çWEöf–ævW'&–çG3Öf–ævW'&–çG2À¢6†÷Eö6÷VçC×6†÷Eö6÷VçBÀ¢&F6…ö6÷VçCÓÀ¢ ¢–b&VçBævWB‚'7FGW2"’–â²'&W&VB"Â&6öç7VÖ–ær'Ó ¢VæF–ærÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×7G"‡&VçBævWB‚&†æFöfeö–B"’÷"""’À¢¢–b€¢VæF–ærævWB‚'7FGW2"’æ÷B–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢÷ ¢VæF–ærævWB‚'&VVF—Eö¶–æB"¢Ò&–æFWVæFVçEöVF—E÷7V66W76÷" ¢÷"VæF–ærævWB‚'&VVF—E÷&ö÷Eö†æFöfeö–B"¢Ò&ö÷BævWB‚&†æFöfeö–B"¢÷"VæF–ærævWB‚&6Æ–ÕöWF†÷&—G•÷v÷&µö÷&FW%ö–B"¢ÒVF—E÷v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"¢÷"VæF–ærævWB‚&f–ævW'&–çG2"’Òf–ævW'&–çG0¢÷"VæF–ærævWB‚'&VVF—EöWfVçE÷G&6Uö–B"¢ÒVF—EöWfVçE÷G&6Uö–@¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢VæF–ær&÷WFR6†ævVB&WG'’–FVçF—G’ ¢¢7GVÅ÷&VçEö–BÒ7G"€¢VæF–ærævWB‚'&VVF—E÷&VçEö†æFöfeö–B"’÷"" ¢¢7GVÅ÷&VçBÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ7GVÅ÷&VçEö–BÀ¢¢–b€¢7GVÅ÷&VçBævWB‚'7FGW2"’Ò&6öç7VÖVB ¢÷"7GVÅ÷&VçBævWB‚&VF—Eö6W'F–f–6FUö–B"¢ÒVæF–ærævWB‚'&Wf–÷W5öVF—Eö6W'F–f–6FUö–B"¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢VæF–ær&÷WFR&VçB—2æòÆöævW"6ö×ÆWFR ¢¢7GVÅ÷&VçEöWF†÷&—G’Ò&VçEöWF†÷&—G’†7GVÅ÷&VçEö–B¢–b€¢VæF–ærævWB‚'&Wf–÷W5öVF—Eö6W'F–f–6FUö†Ö2"¢Ò7GVÅ÷&VçEöWF†÷&—G’ævWB‚&6W'F–f–6FUö†Ö2"¢÷"VæF–ærævWB‚'&Wf–÷W5öVF—EöWfVçE÷6†#Sb"¢Ò7GVÅ÷&VçEöWF†÷&—G’ævWB‚&WfVçE÷6†#Sb"¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢VæF–ær&÷WFRÆ÷7B—G2&VçBWF†÷&—G’'&–FvR ¢¢–bVæF–ærævWB‚'7FGW2"’ÓÒ'&W&VB# ¢G'“ ¢VæF–ærÒ6VÆbç7F÷&Ræ6öÖÖ—E÷&VVF—E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&ö÷Eö†æFöfeö–C×7G"‡&ö÷E²&†æFöfeö–B%Ò’À¢&VçEö†æFöfeö–CÖ7GVÅ÷&VçEö–BÀ¢7V66W76÷%ö†æFöfeö–C×7G"‡VæF–æu²&†æFöfeö–B%Ò’À¢6öç7VÖW%÷v÷&µö÷&FW%ö–C×7G"€¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ð¢’À¢7V66W76÷#×6VÆbåö†æFöfe÷&V6÷&E÷&ö¦V7F–öâ‡VæF–ær’À¢¢W†6WBfÇVTW'&÷"2W†3 ¢–b7G"†W†2’Ò'&VVF—E÷7V66W76÷%ö–FV×÷FVæ7•ö6öæfÆ–7B# ¢&—6P¢&Vg&W6†VBÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×7G"‡VæF–æu²&†æFöfeö–B%Ò’À¢¢–b€¢&Vg&W6†VBævWB‚'7FGW2"’Ò&6öç7VÖ–ær ¢÷"&Vg&W6†VBævWB‚'&VVF—EöÆ–æVvU÷6†#Sb"¢ÒVæF–ærævWB‚'&VVF—EöÆ–æVvU÷6†#Sb"¢÷"&Vg&W6†VBævWB‚&f–ævW'&–çG2"’Òf–ævW'&–çG0¢÷"&Vg&W6†VBævWB‚'&VVF—EöWfVçE÷G&6Uö–B"¢ÒVF—EöWfVçE÷G&6Uö–@¢“ ¢&—6P¢VæF–ærÒ&Vg&W6†V@¢&WGW&âVæF–ærÂ&÷WFU÷G&6Uöf÷"€¢7G"‡VæF–æu²'&VVF—E÷&÷WFU÷G&6Uö–B%Ò¢ ¢7V66W76÷%ö–BÒöæWuö–B‚$„äB"¢&÷WFU÷G&6Uö–BÒöæWuö–B‚%E$2"¢W6VE÷G&6Uö–G2Ò°¢7G"‡&÷rævWB‚'FVÆVÖWG'•ö–B"’÷"&÷rævWB‚'G&6Uö–B"’÷"""¢f÷"&÷r–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G5÷7G&–7B€¢æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ'FVÆVÖWG'’ ¢¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢Ð¢W6VE÷G&6Uö–G2çWFFR€¢7G"‡&÷rævWB‚'7FvU÷G&6Uö–B"’÷"""¢f÷"&÷r–â6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B’ævWB€¢&WfVçG2"ÂµÐ¢¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢¢–b€¢æ÷B&÷WFU÷G&6Uö–@¢÷"æ÷BVF—EöWfVçE÷G&6Uö–@¢÷"&÷WFU÷G&6Uö–BÓÒVF—EöWfVçE÷G&6Uö–@¢÷"&÷WFU÷G&6Uö–B–âW6VE÷G&6Uö–G0¢÷"VF—EöWfVçE÷G&6Uö–B–âW6VE÷G&6Uö–G0¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷G&6Uö6öæfÆ–7C¢&÷WFRæBVF—BÖWfVçBG&6W2×W7B&RvÆö&ÆÇ’g&W6‚ ¢¢7V66W76÷"Ò6VÆbåö†æFöfe÷&V6÷&E÷&ö¦V7F–öâ‡&ö÷B¢f÷"¶W’–â€¢&†æFöfeö–B"À¢&7&VFVEöB"À¢'7FGW2"À¢&6Æ–ÖVEö'•÷v÷&µö÷&FW%ö–B"À¢&6Æ–Õ÷Fö¶Vâ"À¢&6Æ–ÕövVæW&F–öâ"À¢&6Æ–ÕöWö6…÷6V6öæG2"À¢&6Æ–ÕöÆV6U÷6V6öæG2"À¢&6Æ–ÕöÆV6UöW‡—&W5öB"À¢&6Æ–ÕöÆV6UöW‡—&W5öWö6…÷6V6öæG2"À¢&6Æ–ÕöWF†÷&—G•ö6öçG&7B"À¢&6Æ–ÕöWF†÷&—G•÷&ö÷Eö–B"À¢&6Æ–ÕöWF†÷&—G•övVæW&F–öâ"À¢&6Æ–ÕöWF†÷&—G•÷6W76–öåöf–ævW'&–çB"À¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"À¢&6öç7VÖVEöB"À¢&6öç7V×F–öåö6Æ–Ò"À¢&VF—Eö6W'F–f–6FUö–B"À¢&VF—Eö6W'F–f–6FUö–G2"À¢'&VVF—Eö6÷VçB"À¢'&VVF—FVEöB"À¢&ÆFW7EöVF—Eö6W'F–f–6FUö–B"À¢“ ¢7V66W76÷"ç÷†¶W’ÂæöæR¢&VçEöWF†÷&—G•÷&V6÷&BÒ&VçEöWF†÷&—G’€¢7G"‡&VçBævWB‚&†æFöfeö–B"’÷"""¢¢7V66W76÷"çWFFR€¢°¢&†æFöfeö–B#¢7V66W76÷%ö–BÀ¢&7&VFVEöB#¢WF5÷7F×‚’À¢'7FGW2#¢'&W&VB"À¢&–æFWVæFVçEöVF—E÷7FGW2#¢%TäD”äuôTD•B"À¢'7FvU÷G&6Uö–B#¢&÷WFU÷G&6Uö–BÀ¢'&öGV7F–öå÷7FvU÷G&6Uö–B#¢7G"‡&ö÷E²'7FvU÷G&6Uö–B%Ò’À¢'&VVF—Eö¶–æB#¢&–æFWVæFVçEöVF—E÷7V66W76÷""À¢'&VVF—E÷&ö÷Eö†æFöfeö–B#¢7G"‡&ö÷E²&†æFöfeö–B%Ò’À¢'&VVF—E÷&VçEö†æFöfeö–B#¢7G"‡&VçE²&†æFöfeö–B%Ò’À¢'&VVF—E÷6WVVæ6R#¢–çB‡&VçBævWB‚'&VVF—E÷6WVVæ6R"’÷"¢²À¢'&VVF—E÷&÷WFU÷G&6Uö–B#¢&÷WFU÷G&6Uö–BÀ¢'&VVF—EöWfVçE÷G&6Uö–B#¢VF—EöWfVçE÷G&6Uö–BÀ¢'&Wf–÷W5öVF—Eö6W'F–f–6FUö–B#¢7G"€¢&VçE²&VF—Eö6W'F–f–6FUö–B%Ð¢’À¢'&Wf–÷W5öVF—Eö6W'F–f–6FUö†Ö2#¢7G"€¢&VçEöWF†÷&—G•÷&V6÷&E²&6W'F–f–6FUö†Ö2%Ð¢’À¢'&Wf–÷W5öVF—EöWfVçE÷6†#Sb#¢7G"€¢&VçEöWF†÷&—G•÷&V6÷&E²&WfVçE÷6†#Sb%Ð¢’À¢&6Æ–ÕöWF†÷&—G•÷v÷&µö÷&FW%ö–B#¢7G"€¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ð¢’À¢'&VVF—EöVF—E÷v–æF÷uö&–æF–æuö–B#¢VF—Eö&–æF–ærævWB€¢'v–æF÷uö&–æF–æuö–B ¢’À¢'&VVF—EöVF—Eö7F÷%ö&–æF–æuö–B#¢VF—Eö&–æF–ærævWB€¢&7F÷%ö&–æF–æuö–B ¢’À¢'&VVF—EöVF—E÷6W76–öåöf–ævW'&–çB#¢VF—Eö&–æF–ærævWB€¢'6W76–öåöf–ævW'&–çB ¢’À¢'&VVF—EöVF—EöWF†÷&—G•÷&ö÷Eö–B#¢VF—Eö&–æF–ærævWB€¢&WF†÷&—G•÷&ö÷Eö–B ¢’À¢'&VVF—EöVF—EöWF†÷&—G•öWö6…ö–B#¢VF—Eö&–æF–ærævWB€¢&WF†÷&—G•öWö6…ö–B ¢’À¢'&VVF—EöVF—EöWF†÷&—G•övVæW&F–öâ#¢VF—Eö&–æF–ærævWB€¢&vVæW&F–öâ ¢’À¢'&VVF—EöVF—EöWF†÷&—G•ö6öçF–çVF–öåö–B#¢VF—Eö&–æF–ærævWB€¢&6öçF–çVF–öåö–B ¢’À¢'6W'fW%öÖ–çFVEöÆ–æVvR#¢G'VRÀ¢Ð¢¢7V66W76÷%²'&VVF—EöÆ–æVvU÷6†#Sb%ÒÒ6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢6VÆbå÷&VVF—EöÆ–æVvU÷–ÆöB€¢&ö÷C×&ö÷BÂ&VçC×&VçBÂ7V66W76÷#×7V66W76÷ ¢¢¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â7V66W76÷ ¢¢W'6—7FVBÒ6VÆbç7F÷&Ræ6öÖÖ—E÷&VVF—E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&ö÷Eö†æFöfeö–C×7G"‡&ö÷E²&†æFöfeö–B%Ò’À¢&VçEö†æFöfeö–C×7G"‡&VçE²&†æFöfeö–B%Ò’À¢7V66W76÷%ö†æFöfeö–C×7V66W76÷%ö–BÀ¢6öç7VÖW%÷v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢7V66W76÷#×7V66W76÷"À¢¢ô5D•dUô4ôå5TÕD”ôåô4Ä”Õ2ç6WB€¢€¢¥ô5D•dUô4ôå5TÕD”ôåô4Ä”Õ2ævWB‚’À¢°¢&¶–æB#¢'&VVF—E÷7V66W76÷""À¢&æÖW76R#¢æÖW76RÀ¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢'&V6÷&Eö–B#¢7V66W76÷%ö–BÀ¢'&÷WFU÷G&6Uö–B#¢&÷WFU÷G&6Uö–BÀ¢ÒÀ¢¢¢&÷WFU÷G&6RÒ&÷WFU÷G&6Uöf÷"‡&÷WFU÷G&6Uö–B¢&WGW&âW'6—7FVBÂ&÷WFU÷G&6P ¢FVb÷W'6—7E÷&öGV7F–öåö†æFöfb€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW%ö–C¢7G"À¢7F÷'–&ö&Eö'F–f7Eö–C¢7G"À¢6æöæ–6Åö'F–f7Eö–C¢7G"À¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢'F–f7E÷&V6÷&C¢F–7E·7G"Âç•ÒÀ¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢7FvU÷G&6Uö–C¢7G"À¢&W—%÷6¶vUö–C¢7G"ÂæöæRÒæöæRÀ¢V&Æ–6F–öå÷7FFS¢7G"Ò&6öÖÖ—GFVB"À¢7V66W76÷%÷G&ç67F–öåö–C¢7G"ÂæöæRÒæöæRÀ¢7V66W76÷%ö&6Uö'F–f7Eö–C¢7G"ÂæöæRÒæöæRÀ¢7V66W76÷%ö¶–æC¢7G"ÂæöæRÒæöæRÀ¢7V66W76÷%÷66÷Uö†6ƒ¢7G"ÂæöæRÒæöæRÀ¢†æFöfeö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâF–7E·7G"Âç•Ó ¢FWFW&Ö–æ—7F–5ö†æFöfbÒ†æFöfeö–B—2æ÷BæöæP¢†æFöfeö–BÒ†æFöfeö–B÷"öæWuö–B‚$„äB"¢&÷VæF'’Ò7F÷'–&ö&BævWB‚&6†FW%ö–çF¶R"’÷"7F÷'–&ö&BævWB‚'6÷W&6UöÆö6²"’÷"·Ð¢–bæ÷B—6–ç7Fæ6R†&÷VæF'’ÂF–7B“ ¢&÷VæF'’Ò·Ð¢&öÆRÒ7G"‡v÷&µö÷&FW"ævWB‚'6W'fW%ö7F÷%÷&öÆR"’÷"'&öGV7F–öâ"¢WF†÷&—G•ö&–æF–ærÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÂ&ö¦V7Eö–BÂv÷&µö÷&FW ¢¢†æFöfe÷G—RÒ'&W—%÷FõöVF—B"–b&öÆRÓÒ'&W—""VÇ6R'&öGV7F–öå÷FõöVF—B ¢f–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢7F÷'–&ö&BÀ¢'F–f7E÷&V6÷&BÀ¢6æöæ–6Åö†6ƒ×7G"†6W'F–f–6FU²&6æöæ–6Å÷6†#Sb%Ò’À¢6÷W&6Uöf–ævW'&–çC×7G"†6W'F–f–6FU²'6÷W&6Uöf–ævW'&–çB%Ò’À¢&÷VæF'•ö†6ƒ×7G"†6W'F–f–6FU²&&÷VæF'•ö†6‚%Ò’À¢¢6÷W&6UöÆö6²Òv÷&µö÷&FW"ævWB‚'6÷W&6UöÆö6²"’÷"·Ð¢–bæ÷B—6–ç7Fæ6R‡6÷W&6UöÆö6²ÂF–7B“ ¢6÷W&6UöÆö6²Ò·Ð¢F&vWEö–G2Ò·7G"‡fÇVR’f÷"fÇVR–âv÷&µö÷&FW"ævWB‚'F&vWG2"ÂµÒ’–b7G"‡fÇVR•Ò÷"².XZŽšžyºâ%Ð¢†æFöfbÒ°¢&†æFöfeö–B#¢†æFöfeö–BÀ¢¢¦6ö×7Eö6öçG&7EöWf–FVæ6R‚’À¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢&VF—Eö7–6ÆUö–B#¢b$TB×·v÷&µö÷&FW%ö–GÒ"À¢&Æöv–6Åö'F–f7Eö–B#¢7F÷'–&ö&Eö'F–f7Eö–BÀ¢&†æFöfe÷G—R#¢†æFöfe÷G—RÀ¢&7&VFVEöB#¢WF5÷7F×‚’À¢&g&öÕ÷v–æF÷u÷&öÆR#¢&öÆRÀ¢'Fõ÷v–æF÷u÷&öÆR#¢&–æFWVæFVçEöVF—B"À¢'66÷R#¢°¢'F&vWEö–G2#¢F&vWEö–G2À¢'6÷W&6U÷&ævR#¢°¢&WF†÷&—FF—fU÷6÷W&6R#¢7G"‡6÷W&6UöÆö6²ævWB‚&WF†÷&—FF—fU÷6÷W&6R"’÷"&Æö6¶VB6÷W&6R"’À¢&ÆFW7E÷fW'6–öâ#¢7G"‡6÷W&6UöÆö6²ævWB‚&ÆFW7E÷fW'6–öâ"’÷"&7W'&VçB"’À¢ÒÀ¢&&÷VæF'•ö6öçFW‡B#¢°¢'&Wf–÷W5öVæB#¢7G"†&÷VæF'’ævWB‚'&Wf–÷W5öVæB"’÷"&÷VæF'’ævWB‚.Kˆ®™¸n{¸Žx+’"’÷".iÊ®hùKé²"’À¢&7W'&VçE÷7F'B#¢7G"†&÷VæF'’ævWB‚&7W'&VçE÷7F'B"’÷"&÷VæF'’ævWB‚.iÊÎ™¸n‹[~x+’"’÷".[{.™HZé¢"’À¢&7W'&VçEöVæB#¢7G"†&÷VæF'’ævWB‚&7W'&VçEöVæB"’÷"&÷VæF'’ævWB‚.iÊÎ™¸n{¸Žx+’"’÷".[{.™HZé¢"’À¢&æW‡E÷7F'B#¢7G"†&÷VæF'’ævWB‚&æW‡E÷7F'B"’÷"&÷VæF'’ævWB‚.Kˆ¾™¸n‹[~x+’"’÷".iÊ®hùKé²"’À¢ÒÀ¢'&÷FV7FVE÷66÷R#¢Æ—7B‡v÷&µö÷&FW"ævWB‚'&÷FV7FVE÷66÷R"’÷"µÒ’À¢ÒÀ¢&f–ævW'&–çG2#¢f–ævW'&–çG2À¢&ö&¦V7F—fUöWf–FVæ6R#¢°¢'6÷W&6UöÖæ–fW7B#¢²&f–ævW'&–çB#¢f–ævW'&–çG5²'6÷W&6Uö'—FW2%×ÒÀ¢'v÷&¶&ööµöÖæ–fW7B#¢°¢&'F–f7Eö–B#¢7F÷'–&ö&Eö'F–f7Eö–BÀ¢'&u÷6†#Sb#¢f–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢'6—¦Uö'—FW2#¢–çB†'F–f7E÷&V6÷&BævWB‚'6—¦Uö'—FW2"’÷"’À¢ÒÀ¢&6æöæ–6Å÷&Vb#¢6æöæ–6Åö'F–f7Eö–BÀ¢&&÷fVEöÆö6·2#¢°¢&×W7Eö¶VW#¢Æ—7B‡6÷W&6UöÆö6²ævWB‚&×W7Eö¶VW"’÷"µÒ’À¢&×W7E÷&VÖ÷fR#¢Æ—7B‡6÷W&6UöÆö6²ævWB‚&×W7E÷&VÖ÷fR"’÷"µÒ’À¢'&÷FV7FVE÷66÷R#¢Æ—7B‡v÷&µö÷&FW"ævWB‚'&÷FV7FVE÷66÷R"’÷"µÒ’À¢ÒÀ¢&FWFW&Ö–æ—7F–5÷7FF—7F–72#¢°¢'6†÷Eö6÷VçB#¢ÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢&&F6…ö6÷VçB#¢À¢'F÷FÅöGW&F–öâ#¢7F÷'–&ö&BævWB‚'F÷FÅöGW&F–öâ"Â’À¢ÒÀ¢ÒÀ¢'&öGV6W%ö6Æ–×2#¢°¢&6Æ–Õ÷G—R#¢'&W—%÷&W7VÇB"–b&öÆRÓÒ'&W—""VÇ6R'&öGV7F–öåö6Æ–Ò"À¢&6†FW%öÖ#¢7F÷'–&ö&BævWB‚&6†FW%öÖ"’÷"·ÒÀ¢'6÷W&6UöÖ–ær#¢7F÷'–&ö&BævWB‚'6÷W&6UöÖ–ær"’÷"·ÒÀ¢'&÷FV7FVEöF–ÆöwVR#¢7F÷'–&ö&BævWB‚'&÷FV7FVEöF–ÆöwVR"’÷"µÒÀ¢&6övæ—F–öåöÖG&—‚#¢7F÷'–&ö&BævWB‚&6övæ—F–öåöÖG&—‚"’÷"µÒÀ¢'66VæU÷7FFR#¢7F÷'–&ö&BævWB‚'66VæU÷7FFR"’÷"µÒÀ¢'6†÷E÷Æâ#¢€¢7F÷'–&ö&BævWB‚'&WfVçF–öåöWf–FVæ6R"Â·Ò’ævWB‚'6†÷E÷Æâ"ÂµÒ¢–b—6–ç7Fæ6R‡7F÷'–&ö&BævWB‚'&WfVçF–öåöWf–FVæ6R"’ÂF–7B¢VÇ6RµÐ¢’À¢&6÷fW&vR#¢7F÷'–&ö&BævWB‚&6÷fW&vR"’÷"·ÒÀ¢'&VfÆ–v‡Eöf–æF–æw2#¢6W'F–f–6FRævWB‚'&W÷'B"Â·Ò’ævWB‚&—77VW2"ÂµÒ’À¢ÒÀ¢&66†UöÖæ–fW7B#¢°¢°¢&66†Uö6Æ72#¢&6æöæ–6Å÷'6R"À¢&66†Uö¶W’#¢6W'F–f–6FU²&66†Uö¶W’%ÒÀ¢'7FGW2#¢&Ö—72"À¢'&V6öâ#¢&g&W6‚&öGV7F–öâ6æöæ–6ÂæB&VfÆ–v‡B"À¢&FWVæFVæ7•öf–ævW'&–çG2#¢f–ævW'&–çG2À¢Ð¢ÒÀ¢'7FvU÷G&6Uö–B#¢7FvU÷G&6Uö–BÀ¢'7FGW2#¢'&W&VB"À¢'V&Æ–6F–öå÷7FFR#¢V&Æ–6F–öå÷7FFRÀ¢'7F÷'–&ö&Eö'F–f7Eö–B#¢7F÷'–&ö&Eö'F–f7Eö–BÀ¢&6æöæ–6Åö'F–f7Eö–B#¢6æöæ–6Åö'F–f7Eö–BÀ¢&6æöæ–6Å÷6†#Sb#¢6W'F–f–6FU²&6æöæ–6Å÷6†#Sb%ÒÀ¢'6÷W&6Uöf–ævW'&–çB#¢6W'F–f–6FU²'6÷W&6Uöf–ævW'&–çB%ÒÀ¢&&÷VæF'•ö†6‚#¢6W'F–f–6FU²&&÷VæF'•ö†6‚%ÒÀ¢''VÆW6WE÷fW'6–öâ#¢6W'F–f–6FU²''VÆW6WE÷fW'6–öâ%ÒÀ¢'fÆ–FF÷%÷fW'6–öâ#¢6W'F–f–6FU²'fÆ–FF÷%÷fW'6–öâ%ÒÀ¢&VF—Eö6Æ72#¢TD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…BÀ¢&6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&66†Uö¶W’#¢6W'F–f–6FU²&66†Uö¶W’%ÒÀ¢&66†Uö6ö×öæVçG2#¢6W'F–f–6FU²&66†Uö6ö×öæVçG2%ÒÀ¢'&öGV7F–öå÷&VfÆ–v‡E÷7FGW2#¢6W'F–f–6FU²'&VfÆ–v‡E÷7FGW2%ÒÀ¢'&öGV7F–öå÷&VfÆ–v‡Eö—77VUö6÷VçB#¢6W'F–f–6FU°¢'&VfÆ–v‡Eö—77VUö6÷VçB ¢ÒÀ¢&–æFWVæFVçEöVF—E÷7FGW2#¢%TäD”äuôTD•B"À¢&6†V6¶VE÷6†÷Eö6÷VçB#¢6W'F–f–6FU²&6†V6¶VE÷6†÷Eö6÷VçB%ÒÀ¢&f7G5÷&WW6&ÆR#¢G'VRÀ¢&6öæ6ÇW6–öå÷&WW6&ÆUöf÷%ö–æFWVæFVçEöVF—B#¢fÇ6RÀ¢&g&öÕ÷v–æF÷uö&–æF–æuö–B#¢WF†÷&—G•ö&–æF–ærævWB€¢'v–æF÷uö&–æF–æuö–B ¢’À¢&g&öÕö7F÷%ö&–æF–æuö–B#¢WF†÷&—G•ö&–æF–ærævWB€¢&7F÷%ö&–æF–æuö–B ¢’À¢&g&öÕ÷6W76–öåöf–ævW'&–çB#¢WF†÷&—G•ö&–æF–ærævWB€¢'6W76–öåöf–ævW'&–çB ¢’À¢&g&öÕöWF†÷&—G•÷&ö÷Eö–B#¢WF†÷&—G•ö&–æF–ærævWB€¢&WF†÷&—G•÷&ö÷Eö–B ¢’À¢&g&öÕöWF†÷&—G•öWö6…ö–B#¢WF†÷&—G•ö&–æF–ærævWB€¢&WF†÷&—G•öWö6…ö–B ¢’À¢&g&öÕöWF†÷&—G•övVæW&F–öâ#¢WF†÷&—G•ö&–æF–ærævWB€¢&vVæW&F–öâ ¢’À¢Ð¢–b7V66W76÷%÷G&ç67F–öåö–C ¢†æFöfbçWFFR€¢°¢'7V66W76÷%÷G&ç67F–öåö–B#¢7V66W76÷%÷G&ç67F–öåö–BÀ¢'7V66W76÷%ö&6Uö'F–f7Eö–B#¢7V66W76÷%ö&6Uö'F–f7Eö–BÀ¢'7V66W76÷%ö¶–æB#¢7V66W76÷%ö¶–æBÀ¢'7V66W76÷%÷66÷Uö†6‚#¢7V66W76÷%÷66÷Uö†6‚À¢Ð¢¢–b†æFöfe÷G—RÓÒ'&W—%÷FõöVF—B# ¢–bæ÷B&W—%÷6¶vUö–C ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷&WV—&VC¢&W—%÷FõöVF—B†æFöfb×W7B&VfW&Væ6RF†R6öç7VÖVB–Ö×WF&ÆR6¶vR ¢¢†æFöfe²&–Ö×WF&ÆU÷&W—%÷6¶vUö–B%ÒÒ&W—%÷6¶vUö–@¢&W—%÷6¶vRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×&W—%÷6¶vUö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&W—%÷6¶vR“ ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6¶vR—2Vç6–væVB"¢†æFöfbçWFFR€¢°¢'&WGW&å÷FõöVF—E÷v÷&µö÷&FW%ö–B#¢&W—%÷6¶vRævWB‚&VF—E÷v÷&µö÷&FW%ö–B"’À¢'&WGW&å÷FõöVF—E÷v–æF÷uö&–æF–æuö–B#¢&W—%÷6¶vRævWB‚&VF—E÷v–æF÷uö&–æF–æuö–B"’À¢'&WGW&å÷FõöVF—Eö7F÷%ö&–æF–æuö–B#¢&W—%÷6¶vRævWB‚&VF—Eö7F÷%ö&–æF–æuö–B"’À¢'&WGW&å÷FõöVF—E÷6W76–öåöf–ævW'&–çB#¢&W—%÷6¶vRævWB€¢&VF—E÷6W76–öåöf–ævW'&–çB ¢’À¢'&WGW&å÷FõöVF—EöWF†÷&—G•÷&ö÷Eö–B#¢€¢&W—%÷6¶vRævWB‚&VF—EöWF†÷&—G•÷&ö÷Eö–B"¢÷"6VÆbåöWF†÷&—G•÷&ö÷Eö–B€¢&ö¦V7Eö–BÀ¢7G"‡&W—%÷6¶vRævWB‚&VF—E÷v÷&µö÷&FW%ö–B"’÷"""’À¢¢’À¢'&WGW&å÷FõöVF—EöWF†÷&—G•öWö6…ö–B#¢€¢&W—%÷6¶vRævWB‚&VF—EöWF†÷&—G•öWö6…ö–B"¢’À¢'&WGW&å÷FõöVF—EöWF†÷&—G•övVæW&F–öâ#¢€¢&W—%÷6¶vRævWB‚&VF—EöWF†÷&—G•övVæW&F–öâ"¢’À¢'&WGW&å÷FõöVF—EöWF†÷&—G•ö6öçF–çVF–öåö–B#¢€¢&W—%÷6¶vRævWB€¢&VF—EöWF†÷&—G•ö6öçF–çVF–öåö–B ¢¢’À¢Ð¢¢VF—E÷Fõ÷&W—%ö–BÒ&W—%÷6¶vRævWB‚&VF—E÷Fõ÷&W—%ö†æFöfeö–B"¢–bVF—E÷Fõ÷&W—%ö–C ¢VF—E÷Fõ÷&W—"Ò6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×7G"†VF—E÷Fõ÷&W—%ö–B’À¢¢†æFöfe²&VF—Eö7–6ÆUö–B%ÒÒVF—E÷Fõ÷&W—"ævWB€¢&VF—Eö7–6ÆUö–B"Â†æFöfe²&VF—Eö7–6ÆUö–B%Ð¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöf`¢¢–bFWFW&Ö–æ—7F–5ö†æFöfc ¢G'“ ¢W†—7F–ærÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B7G"†W†2’ç7F'G7v—F‚‚'Væ¶æ÷vâ†æFöfg2&V6÷&Eö–C¢"“ ¢&—6P¢W†—7F–ærÒæöæP¢–bW†—7F–ær—2æ÷BæöæS ¢–bæ÷B€¢6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†W†—7F–ær¢æBW†—7F–ærævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"¢ÓÒ7F÷'–&ö&Eö'F–f7Eö–@¢æBW†—7F–ærævWB‚&6æöæ–6Åö'F–f7Eö–B"¢ÓÒ6æöæ–6Åö'F–f7Eö–@¢æBW†—7F–ærævWB‚&6W'F–f–6FUö–B"¢ÓÒ6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢“ ¢&—6RfÇVTW'&÷"‚'&öGV7F–öåö†æFöfeö–FV×÷FVæ7•ö6öæfÆ–7B"¢&WGW&âW†—7F–æp¢&WGW&â6VÆbç7F÷&Rç6fU÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢fÇVSÖ†æFöfbÀ¢ ¢FVb÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW%ö–C¢7G"À¢'F–f7Eö–C¢7G"À¢6æöæ–6Åö'F–f7Eö–C¢7G"À¢†æFöfeö–C¢7G"À¢G&ç67F–öåö–C¢7G"À¢7FFS¢7G"À¢&V6öã¢7G"ÂæöæRÒæöæRÀ¢’ÓâF–7E·7G"Âç•Ó ¢""%V&Æ—6‚÷"&V¦V7BWfW'’&ö¦V7F–öâöböæR7V66W76÷"6æF–FFRà ¢6æF–FFR'—FW2&RW'6—7FVB&Vf÷&RF†R&6R6ö×&RÖæB×6WB6òF†P¢fÆ–FF÷"6â&–æBW†7B†6†W2âF†W’&VÖ–âf–ÂÖ6Æ÷6VBVçF–ÂF†P¢6ö×&RÖæB×6WB7V66VVG2âF†—2ÖWF†öBWFFW2F†R'F–f7B—"À¢6–væVB†æFöfbÂæB‡V"&ö¦V7F–öç2FövWF†W"VæFW"F†R&ö¦V7BÆö6²w0¢æ÷&ÖÂ6W&–Æ—¦F–öâ&÷VæF'’à¢""  ¢&WGW&â6VÆbç7F÷&Rç6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æöæ–6Åö'F–f7Eö–BÀ¢†æFöfeö–CÖ†æFöfeö–BÀ¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢7FFS×7FFRÀ¢&V6öã×&V6öâÀ¢ ¢FVböÖF6†–æuö†æFöfb€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢'F–f7Eö–C¢7G"À¢6æöæ–6Åö†6ƒ¢7G"À¢†æFöfeö–C¢7G"ÂæöæRÀ¢’ÓâF–7E·7G"Âç•ÒÂæöæS ¢FVbfÆ–Eö–FVçF—G’‡fÇVS¢F–7E·7G"Âç•Ò’Óâ&ööÃ ¢–bfÇVRævWB‚&†æFöfe÷G—R"’ÓÒ&VF—Eö–çF¶R# ¢&WGW&â&ööÂ€¢fÇVRævWB‚&VF—Eö6Æ72"’ÓÒ&VF—Eö–çF¶R ¢æBfÇVRævWB‚&g&öÕ÷v–æF÷u÷&öÆR"’ÓÒ&W‡FW&æÅö–çWB ¢æBfÇVRævWB‚'Fõ÷v–æF÷u÷&öÆR"’ÓÒ&–æFWVæFVçEöVF—B ¢¢–bfÇVRævWB‚'&VfÆ–v‡EöWf–FVæ6U÷6÷W&6R"’ÓÒ€¢&6öçG&öÆÆVEö†—7F÷&–6Åö6öçG&7E÷&WÆ’ ¢“ ¢&WGW&â6VÆbå÷fÆ–Eö†—7F÷&–6Å÷&VfÆ–v‡Eö–FVçF—G’‡fÇVR¢&WGW&â6VÆbå÷fÆ–Eö66†Uö–FVçF—G’€¢fÇVRÂTD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…@¢ ¢–b†æFöfeö–C ¢†æFöfbÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB††æFöfb“ ¢&—6RfÇVTW'&÷"‚&†æFöfe÷&V—77VU÷&WV—&VC¢Vç6–væVBÆVv7’†æFöfb6ææ÷B&R6öç7VÖVB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B‡6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöfb¢–b†æFöfbævWB‚'7FGW2"’ÓÒ'7WW'6VFVB# ¢&—6RfÇVTW'&÷"€¢&†æFöfe÷7WW'6VFVC¢W6R7V66W76÷" ¢b'¶†æFöfbævWB‚w7WW'6VFVEö'•ö†æFöfeö–Br’÷"wVæ¶æ÷vâwÒ ¢¢V&Æ–6F–öå÷7FFRÒ7G"††æFöfbævWB‚'V&Æ–6F–öå÷7FFR"’÷"&6öÖÖ—GFVB"¢–bæ÷B6VÆbç7F÷&Ræ†æFöfeö—5÷V&Æ—6†VB€¢æÖW76RÂ&ö¦V7Eö–BÂ†æFöf`¢“ ¢&—6RfÇVTW'&÷"€¢&†æFöfeöæ÷Eö6öÖÖ—GFVC¢7V66W76÷"V&Æ–6F–öâ7FFR—2 ¢b'·V&Æ–6F–öå÷7FFR'Ò ¢¢–b†æFöfbævWB‚&6æöæ–6Å÷6†#Sb"’Ò6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"‚&†æFöfb6æöæ–6Â†6‚FöW2æ÷BÖF6‚F†RVF—FVB7F÷'–&ö&B"¢–b'F–f7Eö–Bæ÷B–â°¢†æFöfbævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"’À¢†æFöfbævWB‚&6æöæ–6Åö'F–f7Eö–B"’À¢Ó ¢&—6RfÇVTW'&÷"‚&†æFöfbFöW2æ÷B&VÆöærFòF†RVF—FVB'F–f7B"¢–bæ÷BfÆ–Eö–FVçF—G’††æFöfb“ ¢&—6RfÇVTW'&÷"‚&†æFöfb66†R–FVçF—G’—2–çfÆ–B"¢&WGW&â†æFöf`¢f÷"†æFöfb–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G2†æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ&†æFöfg2"“ ¢–b†æFöfbævWB‚'7FGW2"’–â²'&V¦V7FVB"Â'7WW'6VFVB'Ó ¢6öçF–çVP¢–bæ÷B6VÆbç7F÷&Ræ†æFöfeö—5÷V&Æ—6†VB€¢æÖW76RÂ&ö¦V7Eö–BÂ†æFöf`¢“ ¢6öçF–çVP¢–b†æFöfbævWB‚&6æöæ–6Å÷6†#Sb"’Ò6æöæ–6Åö†6ƒ ¢6öçF–çVP¢–b'F–f7Eö–B–â°¢†æFöfbævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"’À¢†æFöfbævWB‚&6æöæ–6Åö'F–f7Eö–B"’À¢Ó ¢–bfÆ–Eö–FVçF—G’††æFöfb“ ¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB††æFöfb“ ¢&—6RfÇVTW'&÷"€¢&†æFöfe÷&V—77VU÷&WV—&VC¢Vç6–væVBÆVv7’†æFöfb6ææ÷B&R6öç7VÖVB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöf`¢¢&WGW&â†æFöf`¢&WGW&âæöæP ¢FVbö76W'Eö†—7F÷&–6Åö†æFöfeöVF—Eö–çF¶R€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢†æFöfc¢F–7E·7G"Âç•ÒÀ¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢'F–f7Eö–C¢7G"À¢’ÓâæöæS ¢""%&V6†V6²F†Ræ'&÷rÆVv7’v÷&¶&öö²&÷WFRv—F†÷WBw&çF–ærfW&F–7Bâ""  ¢–b†æFöfbævWB‚'&VfÆ–v‡EöWf–FVæ6U÷6÷W&6R"’Ò€¢&6öçG&öÆÆVEö†—7F÷&–6Åö6öçG&7E÷&WÆ’ ¢“ ¢&WGW&à¢–b€¢æ÷B6VÆbå÷fÆ–Eö†—7F÷&–6Å÷&VfÆ–v‡Eö–FVçF—G’††æFöfb¢÷"æ÷B6VÆbç7F÷&Ræ†æFöfeö—5÷V&Æ—6†VB€¢æÖW76RÂ&ö¦V7Eö–BÂ†æFöf`¢¢÷"'F–f7Eö–@¢æ÷B–â°¢†æFöfbævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"’À¢†æFöfbævWB‚&6æöæ–6Åö'F–f7Eö–B"’À¢Ð¢“ ¢&—6RfÇVTW'&÷"€¢&†—7F÷&–6Åö†æFöfeöVF—Eö–çF¶Uö–çfÆ–C¢6–væVB&V6÷fW'’ ¢'&÷WFR—2–æ6ö×ÆWFR ¢¢v÷&¶&ööµö–BÒ7G"††æFöfbævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"’÷"""¢'F–f7E÷&V6÷&BÂ'F–f7E÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂv÷&¶&ööµö–@¢¢'F–f7Eö'—FW2Ò'F–f7E÷F‚ç&VEö'—FW2‚¢W‡G&7FVBÒW‡G&7E÷†Ç7…öf÷%ö6öçG&öÆÆVEö†æFöfe÷&WfÆ–FF–öâ€¢'F–f7Eö'—FW0¢¢&ööbÒ†æFöfbævWB‚'v÷&¶&ööµ÷&ö¦V7F–öå÷&WfÆ–FF–öâ"¢&WÆ’Ò†æFöfbævWB‚'&öGV7F–öå÷fÆ–FF–öå÷&WÆ’"¢–b€¢'F–f7E÷F‚ç7Vff—‚æ66VföÆB‚’æ÷B–â²"ç†Ç7‚"Â"ç†Ç6Ò'Ð¢÷"6†#Seö'—FW2†'F–f7Eö'—FW2¢Ò'F–f7E÷&V6÷&BævWB‚&f–ævW'&–çB"¢÷"'F–f7E÷&V6÷&BævWB‚&f–ævW'&–çB"¢Ò†æFöfbævWB‚&f–ævW'&–çG2"Â·Ò’ævWB‚&'F–f7E÷&r"¢÷"æ÷B—6–ç7Fæ6R‡&ööbÂF–7B¢÷"&ööbævWB‚&'F–f7E÷&u÷6†#Sb"¢Ò'F–f7E÷&V6÷&BævWB‚&f–ævW'&–çB"¢÷"&ööbævWB‚&6æöæ–6Å÷6†#Sb"¢Ò†æFöfbævWB‚&6æöæ–6Å÷6†#Sb"¢÷"&ööbævWB‚&ÖöFR"¢Ò&ÆVv7•öVçfVÆ÷U÷6†÷'Eö†VFW%÷cS5÷&÷w2 ¢÷"&ööbævWB‚'6VÆV7FVE÷&ö¦V7F÷""¢Ò'6†÷'FG&Ö×&ö¦V7F÷"óãRã2Ög&÷¦Vâ ¢÷"&ööbævWB‚'f—6–&ÆU÷&÷w5öÖF6…÷6VÆV7FVE÷&ö¦V7F÷""’—2æ÷BG'VP¢÷"W‡G&7FVBævWB‚&6æöæ–6Å÷7F÷'–&ö&B"’Ò7F÷'–&ö&@¢÷"W‡G&7FVBævWB‚&6æöæ–6Å÷6–FV6%÷&W6VçB"’—2æ÷BG'VP¢÷"W‡G&7FVBævWB€¢&6öçG&öÆÆVE÷&ö¦V7F–öå÷&WfÆ–FF–öâ"Â·Ð¢’ævWB‚'f—6–&ÆU÷&÷w5öÖF6…÷6VÆV7FVE÷&ö¦V7F÷""¢—2æ÷BG'VP¢÷"æ÷B—6–ç7Fæ6R‡&WÆ’ÂF–7B¢÷"&WÆ’ævWB‚&7W'&VçE÷fÆ–FF–öå÷7FGW2"¢Ò5U%$TåEõdÄ”DD”ôåôd”À¢÷"†æFöfbævWB‚'&WV—&W5ögVÆÅö7W'&VçEö–æFWVæFVçEöVF—B"¢—2æ÷BG'VP¢“ ¢&—6RfÇVTW'&÷"€¢&†—7F÷&–6Åö†æFöfeöVF—Eö–çF¶Uö–çfÆ–C¢v÷&¶&öö²Â ¢&6æöæ–6ÂÂ&WÆ’Â÷"&ö¦V7F–öâ&ööbF–ffW'2 ¢ ¢FVböVç7W&UöVF—E÷v÷&µö÷&FW"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢'F–f7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"ÂæöæRÀ¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÀ¢’ÓâF–7E·7G"Âç•Ó ¢–bv÷&µö÷&FW%ö–C ¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢VÇ6S ¢2Æ—fR…EE&WVW7G2†fRâö'6W'f&ÆRG&ç7÷'B6W76–öâæB×W7@¢2W‡Æ–6—FÇ’7&VFR6W&FRVF—Bv÷&²÷&FW"âF—&V7B÷7FF–ð¢2ÆVv7’6ÆÆW'2†fRæòG&ç7÷'B–BÂ6òF†R6W'fW"7&VFW2¢2F—7F–æ7B6ö×F–&–Æ—G’VF—BWF†÷&—G’v—F‚—G2÷vâVæf÷&vV&ÆP¢2&–æF–æw2–ç7FVBöbG'W7F–ær&öÆRFW‡B7WÆ–VB'’F†R6ÆÆW"à¢–b&WVW7E÷6W76–öåö–C ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷v÷&µö÷&FW%÷&WV—&VC¢7&VFR7F÷'–&ö&EöVF—BöVF—EööæÇ’v÷&²÷&FW"–âF†RVF—Bv–æF÷r ¢¢W†—7F–æuö‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢W†—7F–ærÒæW‡B€¢€¢&÷p¢f÷"&÷r–â&WfW'6VB†W†—7F–æuö‡V"ævWB‚'v÷&µö÷&FW'2"ÂµÒ’¢–b&÷rævWB‚&÷W&F–öâ"’ÓÒ'7F÷'–&ö&EöVF—B ¢æB&÷rævWB‚&ÖöFR"’ÓÒ&VF—EööæÇ’ ¢æB'F–f7Eö–B–â&÷rævWB‚'F&vWG2"ÂµÒ¢’À¢æöæRÀ¢¢–bW†—7F–ær—2æ÷BæöæS ¢v÷&µö÷&FW"ÒW†—7F–æp¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ†æÖW76RÂv÷&µö÷&FW"Â&WVW7E÷6W76–öåö–B¢&WGW&âv÷&µö÷&FW ¢&W7öç6RÒ6VÆbæ&Vv–å÷F6²€¢æÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢÷W&F–öãÒ'7F÷'–&ö&EöVF—B"À¢ÖöFSÒ&VF—EööæÇ’"À¢WF†÷&—¦F–öãÒ'&VEööæÇ’"À¢&WVW7E÷FW‡CÒ.X[ÎZëžiz~Zê.h‹~zºþy¨NiÈÞXªYšŽxºÎz¸¾ZêŠêXZ^Xú2"À¢F&vWG3Õ¶'F–f7Eö–EÒÀ¢WF†÷&—FF—fU÷6÷W&6SÒ.[{.™HZé®Xˆn™YÎXø®X[f†æFöfb"À¢ÆFW7E÷fW'6–öãÒ&7W'&VçB"À¢F&vWEö&÷VæF'“Ò&†æFöfbÖ&÷VæF'’"À¢×W7Eö¶VWÕ².Xú®Šû¾xºÎz¸¾ZêŠê%ÒÀ¢×W7E÷&VÖ÷fSÕµÒÀ¢&÷FV7FVE÷66÷SÕ².XZŽ˜:Žk©Xh^Zë’%ÒÀ¢W‡V7FVEö÷WGWG3Õ²&–æFWVæFVçEöVF—Eö6W'F–f–6FR%ÒÀ¢v–æF÷uö¶W“Öb'6W'fW"ÖVF—B×µöæWuö–B‚u%Târ—Ò"À¢&WVW7E÷6W76–öåö–CÔæöæRÀ¢¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Â7G"‡&W7öç6Rçv÷&µö÷&FW%ö–B’¢–b€¢v÷&µö÷&FW"ævWB‚&÷W&F–öâ"’Ò'7F÷'–&ö&EöVF—B ¢÷"v÷&µö÷&FW"ævWB‚&ÖöFR"’Ò&VF—EööæÇ’ ¢÷"v÷&µö÷&FW"ævWB‚&WF†÷&—¦F–öâ"’Ò'&VEööæÇ’ ¢÷"v÷&µö÷&FW"ævWB‚'6W'fW%ö7F÷%÷&öÆR"’æ÷B–â´æöæRÂ&–æFWVæFVçEöVF—B'Ð¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—EöWF†÷&—G•÷&WV—&VC¢VF—BÖöæÇ’6W'fW"Ö&÷VæBv÷&²÷&FW"—2&WV—&VB ¢¢6VÆbåö76W'E÷7F÷'–&ö&EöWF†÷&—G’‡v÷&µö÷&FW"Â&–æFWVæFVçEöVF—B"¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ†æÖW76RÂv÷&µö÷&FW"Â&WVW7E÷6W76–öåö–B¢&WGW&âv÷&µö÷&FW  ¢FVbö76W'EöVF—E÷6W&F–öâ€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢†æFöfc¢F–7E·7G"Âç•ÒÀ¢’ÓâæöæS ¢VF—Eö&–æF–ærÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÂ&ö¦V7Eö–BÂVF—E÷v÷&µö÷&FW ¢¢–bVF—E÷v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’ÓÒ†æFöfbævWB‚'v÷&µö÷&FW%ö–B"“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷6W&F–öå÷&WV—&VC¢v÷&²÷&FW'2×W7BF–ffW""¢–bVF—Eö&–æF–ærævWB‚'v–æF÷uö&–æF–æuö–B"’ÓÒ†æFöfbævWB‚&g&öÕ÷v–æF÷uö&–æF–æuö–B"“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷6W&F–öå÷&WV—&VC¢v–æF÷r&–æF–æw2×W7BF–ffW""¢–bVF—Eö&–æF–ærævWB‚&7F÷%ö&–æF–æuö–B"’ÓÒ†æFöfbævWB‚&g&öÕö7F÷%ö&–æF–æuö–B"“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷6W&F–öå÷&WV—&VC¢7F÷"&–æF–æw2×W7BF–ffW""¢&öGV6W%÷6W76–öâÒ†æFöfbævWB‚&g&öÕ÷6W76–öåöf–ævW'&–çB"¢VF—F÷%÷6W76–öâÒVF—Eö&–æF–ærævWB‚'6W76–öåöf–ævW'&–çB"¢–b€¢VF—E÷v÷&µö÷&FW"ævWB‚'v–æF÷uö66W75ö6öçG&7B"¢Òt”äDõuôäUUE$Åô44U55ô4ôåE$5@¢æB&öGV6W%÷6W76–öà¢æBVF—F÷%÷6W76–öà¢æB&öGV6W%÷6W76–öâÓÒVF—F÷%÷6W76–öà¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷6W&F–öå÷&WV—&VC¢&öGV7F–öâæBVF—BG&ç7÷'B6W76–öç2×W7BF–ffW" ¢¢–b†æFöfbævWB‚&†æFöfe÷G—R"’ÓÒ'&W—%÷FõöVF—B# ¢W‡V7FVE÷v÷&µö÷&FW%ö–BÒ†æFöfbævWB€¢'&WGW&å÷FõöVF—E÷v÷&µö÷&FW%ö–B ¢¢–bVF—E÷v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’ÒW‡V7FVE÷v÷&µö÷&FW%ö–C ¢&—6RfÇVTW'&÷"€¢'&W—%÷&WGW&åöVF—EöÖ—6ÖF6ƒ¢&W—&VB'F–f7B×W7B&WGW&âFòF†R÷&–v–æÂ–æFWVæFVçBVF—BWF†÷&—G’ ¢¢&WGW&åöÆ–æVvUöf–VÆG2Ò€¢'&WGW&å÷FõöVF—EöWF†÷&—G•÷&ö÷Eö–B"À¢'&WGW&å÷FõöVF—EöWF†÷&—G•öWö6…ö–B"À¢'&WGW&å÷FõöVF—EöWF†÷&—G•övVæW&F–öâ"À¢'&WGW&å÷FõöVF—EöWF†÷&—G•ö6öçF–çVF–öåö–B"À¢¢&W6VçE÷&WGW&åöÆ–æVvRÒ°¢f–VÆBf÷"f–VÆB–â&WGW&åöÆ–æVvUöf–VÆG2–bf–VÆB–â†æFöf`¢Ð¢–bÆVâ‡&W6VçE÷&WGW&åöÆ–æVvR’ÓÒÆVâ‡&WGW&åöÆ–æVvUöf–VÆG2“ ¢&WGW&åövVæW&F–öâÒ†æFöfbævWB€¢'&WGW&å÷FõöVF—EöWF†÷&—G•övVæW&F–öâ ¢¢–b€¢—6–ç7Fæ6R‡&WGW&åövVæW&F–öâÂ&ööÂ¢÷"æ÷B—6–ç7Fæ6R‡&WGW&åövVæW&F–öâÂ–çB¢÷"&WGW&åövVæW&F–öâÂ ¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷&WGW&åöVF—EöÖ—6ÖF6ƒ¢WF†÷&—G’vVæW&F–öâ—2–çfÆ–B ¢¢VÆ–b&W6VçE÷&WGW&åöÆ–æVvRÓÒ°¢'&WGW&å÷FõöVF—EöWF†÷&—G•÷&ö÷Eö–B ¢Ó ¢2F†R&RÖ6öçF–çVF–öâ6†R6'&–VBöæÇ’F†RFWFW&Ö–æ—7F–0¢2WF†÷&—G’&ö÷BæB6â&W&W6VçBvVæW&F–öâ¦W&òöæÇ’à¢&WGW&åövVæW&F–öâÒ ¢VÆ–b&W6VçE÷&WGW&åöÆ–æVvS ¢&—6RfÇVTW'&÷"€¢'&W—%÷&WGW&åöVF—EöÖ—6ÖF6ƒ¢WF†÷&—G’6æ6†÷B—2'F–Â ¢¢VÇ6S ¢&WGW&åövVæW&F–öâÒ ¢&WGW&åö&–æF–ærÒ6VÆbå÷fW&–f–VEöWF†÷&—G•ö&–æF–æuöEövVæW&F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢VF—E÷v÷&µö÷&FW"À¢&WGW&åövVæW&F–öâÀ¢¢W‡V7FVBÒ°¢'v–æF÷uö&–æF–æuö–B#¢†æFöfbævWB€¢'&WGW&å÷FõöVF—E÷v–æF÷uö&–æF–æuö–B ¢’À¢&7F÷%ö&–æF–æuö–B#¢†æFöfbævWB€¢'&WGW&å÷FõöVF—Eö7F÷%ö&–æF–æuö–B ¢’À¢'6W76–öåöf–ævW'&–çB#¢†æFöfbævWB€¢'&WGW&å÷FõöVF—E÷6W76–öåöf–ævW'&–çB ¢’À¢Ð¢f÷"f–VÆBÂfÇVR–âW‡V7FVBæ—FV×2‚“ ¢2Æö6Â÷7FF–òG&ç7÷'B†2æòWF†VçF–6FVB&WVW7B6W76–öâFò&–æBà¢2F†R&W—"6¶vR7F–ÆÂ6'&–W26W'fW"ÖÖ–çFVB6ö×F–&–Æ—G¢2f–ævW'&–çBÂv†–ÆRF†R÷&–v–æÂVF—Bv÷&²÷&FW"6÷'&V7FÇ’¶VW0¢2—G2G&ç7÷'Bf–ævW'&–çBV×G’â6Æ÷VBÖöFR×W7B&VÖ–âW†7Bà¢–b€¢f–VÆBÓÒ'6W76–öåöf–ævW'&–çB ¢æB6VÆbç6WGF–æw2æFWÆ÷–ÖVçEöÖöFRÒ&6Æ÷VB ¢æBVF—Eö&–æF–ærævWB†f–VÆB’—2æöæP¢“ ¢6öçF–çVP¢–bfÇVR—2æ÷BæöæRæB&WGW&åö&–æF–ærævWB†f–VÆB’ÒfÇVS ¢&—6RfÇVTW'&÷"€¢'&W—%÷&WGW&åöVF—EöÖ—6ÖF6ƒ¢&W—&VB'F–f7B×W7B&WGW&âFòF†R6–væVB†—7F÷&–6ÂVF—BWF†÷&—G’ ¢¢–b&W6VçE÷&WGW&åöÆ–æVvS ¢Æ–æVvUöW‡V7FVBÒ°¢'&WGW&å÷FõöVF—EöWF†÷&—G•÷&ö÷Eö–B#¢&WF†÷&—G•÷&ö÷Eö–B"À¢'&WGW&å÷FõöVF—EöWF†÷&—G•öWö6…ö–B#¢&WF†÷&—G•öWö6…ö–B"À¢'&WGW&å÷FõöVF—EöWF†÷&—G•övVæW&F–öâ#¢&vVæW&F–öâ"À¢'&WGW&å÷FõöVF—EöWF†÷&—G•ö6öçF–çVF–öåö–B#¢&6öçF–çVF–öåö–B"À¢Ð¢6ö×&VBÒ€¢Æ–æVvUöW‡V7FV@¢–bÆVâ‡&W6VçE÷&WGW&åöÆ–æVvR¢ÓÒÆVâ‡&WGW&åöÆ–æVvUöf–VÆG2¢VÇ6R°¢'&WGW&å÷FõöVF—EöWF†÷&—G•÷&ö÷Eö–B#¢&WF†÷&—G•÷&ö÷Eö–B ¢Ð¢¢–bç’€¢†æFöfbævWB††æFöfeöf–VÆB¢Ò&WGW&åö&–æF–ærævWB†&–æF–æuöf–VÆB¢f÷"†æFöfeöf–VÆBÂ&–æF–æuöf–VÆB–â6ö×&VBæ—FV×2‚¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷&WGW&åöVF—EöÖ—6ÖF6ƒ¢&WGW&âWF†÷&—G’—2æ÷B–âF†R6–væVBÆ–æVvR ¢¢–b€¢VF—Eö&–æF–ærævWB‚&WF†÷&—G•÷&ö÷Eö–B"¢Ò&WGW&åö&–æF–ærævWB‚&WF†÷&—G•÷&ö÷Eö–B"¢÷"–çB†VF—Eö&–æF–ærævWB‚&vVæW&F–öâ"’÷"¢Â–çB‡&WGW&åö&–æF–ærævWB‚&vVæW&F–öâ"’÷"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷&WGW&åöVF—EöÖ—6ÖF6ƒ¢7W'&VçBVF—BWF†÷&—G’—2æ÷BFW66VæFçBöbF†R&WGW&âWF†÷&—G’ ¢ ¢FVb÷W'6—7EöVF—Eö–çF¶R€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢&V6÷&C¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢7FvU÷G&6Uö–C¢7G"À¢’ÓâF–7E·7G"Âç•Ó ¢6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢6÷W&6Uöf–ævW'&–çBÒ6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"’À¢&V6÷&BÀ¢¢&÷VæF'•ö†6‚Ò6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B¢f–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢7F÷'–&ö&BÀ¢&V6÷&BÀ¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢¢†æFöfeö–BÒöæWuö–B‚$„äB"¢VF—E÷v÷&µö÷&FW%ö–BÒ7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò¢&öGV6W%÷v÷&µö÷&FW%ö–BÒ7G"‡&V6÷&BævWB‚'v÷&µö÷&FW%ö–B"’÷"""¢–bæ÷B&öGV6W%÷v÷&µö÷&FW%ö–B÷"&öGV6W%÷v÷&µö÷&FW%ö–BÓÒVF—E÷v÷&µö÷&FW%ö–C ¢&öGV6W%÷v÷&µö÷&FW%ö–BÒ$U…EtòÒ"²6†#Seö'—FW2€¢7G"‡&V6÷&E²&'F–f7Eö–B%Ò’æVæ6öFR‚'WFbÓ‚"¢•³£#Ð¢†æFöfbÒ°¢&†æFöfeö–B#¢†æFöfeö–BÀ¢'66†VÖ÷fW'6–öâ#¢7G"‡7F÷'–&ö&BævWB‚'66†VÖ÷fW'6–öâ"’÷"&ÆVv7’×&VBÖöæÇ’"’À¢'66†VÖ÷&öf–ÆR#¢7G"‡7F÷'–&ö&BævWB‚'66†VÖ÷&öf–ÆR"’÷"&ÆVv7’×&VBÖöæÇ’"’À¢&ÖFW&–Æ—¦W%ö6öçG&7B#¢'&VBÖöæÇ’ÖVF—BÖ–çF¶S¦æòÖÖFW&–Æ—¦F–öâ"À¢&ÖFW&–Æ—¦F–öå÷76W2#¢À¢¢¤äõôEUÄ”4DUôtTäU$D”ôåôUd”DTä4RÀ¢2W‡FW&æÂ–çF¶R†2æò&öGV6W"v÷&²÷&FW"–âF†—2&ö¦V7Bâ¢2FWFW&Ö–æ—7F–27–çF†WF–2&öGV6W"–FVçF—G’&W6W'fW2F†P¢2&öGV7F–öâöVF—B6W&F–öâ–çf&–çBv—F†÷WB&WFVæF–ærF†@¢2F†RVF—BWF†÷&—G’WF†÷&VBF†R–çWB'F–f7Bà¢'v÷&µö÷&FW%ö–B#¢&öGV6W%÷v÷&µö÷&FW%ö–BÀ¢&VF—Eö7–6ÆUö–B#¢b$TB×¶VF—E÷v÷&µö÷&FW%²wv÷&µö÷&FW%ö–Bu×Ò"À¢&Æöv–6Åö'F–f7Eö–B#¢7G"‡&V6÷&E²&'F–f7Eö–B%Ò’À¢&†æFöfe÷G—R#¢&VF—Eö–çF¶R"À¢&7&VFVEöB#¢WF5÷7F×‚’À¢&g&öÕ÷v–æF÷u÷&öÆR#¢&W‡FW&æÅö–çWB"À¢'Fõ÷v–æF÷u÷&öÆR#¢&–æFWVæFVçEöVF—B"À¢'66÷R#¢°¢'F&vWEö–G2#¢·7G"‡&V6÷&E²&'F–f7Eö–B%Ò•ÒÀ¢'6÷W&6U÷&ævR#¢²'6÷W&6R#¢&W‡FW&æÂ7F÷'–&ö&B–çF¶R'ÒÀ¢&&÷VæF'•ö6öçFW‡B#¢°¢'&Wf–÷W5öVæB#¢.iÊ®hùKé²"À¢&7W'&VçE÷7F'B#¢.ZIn˜:ŽXˆn™YÎ‹[~x+’"À¢&7W'&VçEöVæB#¢.ZIn˜:ŽXˆn™YÎ{¸Žx+’"À¢&æW‡E÷7F'B#¢.iÊ®hùKé²"À¢ÒÀ¢'&÷FV7FVE÷66÷R#¢Æ—7B†VF—E÷v÷&µö÷&FW"ævWB‚'&÷FV7FVE÷66÷R"’÷"µÒ’À¢ÒÀ¢&f–ævW'&–çG2#¢f–ævW'&–çG2À¢&ö&¦V7F—fUöWf–FVæ6R#¢°¢'6÷W&6UöÖæ–fW7B#¢²&f–ævW'&–çB#¢f–ævW'&–çG5²'6÷W&6Uö'—FW2%×ÒÀ¢'v÷&¶&ööµöÖæ–fW7B#¢°¢&'F–f7Eö–B#¢&V6÷&E²&'F–f7Eö–B%ÒÀ¢'&u÷6†#Sb#¢f–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢ÒÀ¢&6æöæ–6Å÷&Vb#¢7G"€¢&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"¢÷"&V6÷&E²&'F–f7Eö–B%Ð¢’À¢&&÷fVEöÆö6·2#¢²&VF—E÷&VEööæÇ’#¢G'VWÒÀ¢&FWFW&Ö–æ—7F–5÷7FF—7F–72#¢²'6†÷Eö6÷VçB#¢ÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’—ÒÀ¢ÒÀ¢'&öGV6W%ö6Æ–×2#¢°¢&6Æ–Õ÷G—R#¢&W‡FW&æÅö–çWEö6Æ–Ò"À¢&6†FW%öÖ#¢·ÒÀ¢'6÷W&6UöÖ–ær#¢·ÒÀ¢'&÷FV7FVEöF–ÆöwVR#¢µÒÀ¢&6övæ—F–öåöÖG&—‚#¢µÒÀ¢'66VæU÷7FFR#¢µÒÀ¢'6†÷E÷Æâ#¢7F÷'–&ö&BævWB‚'6†÷G2"’÷"µÒÀ¢&6÷fW&vR#¢·ÒÀ¢'&VfÆ–v‡Eöf–æF–æw2#¢µÒÀ¢ÒÀ¢&66†UöÖæ–fW7B#¢°¢°¢&66†Uö6Æ72#¢&6æöæ–6Å÷'6R"À¢&66†Uö¶W’#¢b$”åD´R×¶6æöæ–6Åö†6‡Ò"À¢'7FGW2#¢&Ö—72"À¢'&V6öâ#¢&W‡FW&æÂ'F–f7Bæ÷&ÖÆ—¦VB'’&VBÖöæÇ’VF—B–çF¶R"À¢&FWVæFVæ7•öf–ævW'&–çG2#¢f–ævW'&–çG2À¢Ð¢ÒÀ¢'7FvU÷G&6Uö–B#¢7FvU÷G&6Uö–BÀ¢'7FGW2#¢'&W&VB"À¢'7F÷'–&ö&Eö'F–f7Eö–B#¢7G"‡&V6÷&E²&'F–f7Eö–B%Ò’À¢&6æöæ–6Åö'F–f7Eö–B#¢7G"€¢&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"¢÷"&V6÷&E²&'F–f7Eö–B%Ð¢’À¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uöf–ævW'&–çB#¢6÷W&6Uöf–ævW'&–çBÀ¢&&÷VæF'•ö†6‚#¢&÷VæF'•ö†6‚À¢&VF—Eö6Æ72#¢&VF—Eö–çF¶R"À¢&g&öÕ÷v–æF÷uö&–æF–æuö–B#¢$U…Et”âÒ ¢²6†#Seö'—FW2€¢b'v–æF÷s§·&V6÷&E²v'F–f7Eö–Bu×Ò"æVæ6öFR‚'WFbÓ‚"¢•³£#ÒÀ¢&g&öÕö7F÷%ö&–æF–æuö–B#¢$U…D5BÒ ¢²6†#Seö'—FW2€¢b&7F÷#§·&V6÷&E²v'F–f7Eö–Bu×Ò"æVæ6öFR‚'WFbÓ‚"¢•³£#ÒÀ¢&g&öÕ÷6W76–öåöf–ævW'&–çB#¢æöæRÀ¢Ð¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B‡6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöfb¢W'6—7FVBÒ6VÆbç7F÷&Rç6fU÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢fÇVSÖ†æFöfbÀ¢¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢‡V"ç6WFFVfVÇB‚&†æFöfg2"ÂµÒ’æVæB†6÷’æFVW6÷’‡W'6—7FVB’¢&WGW&âW'6—7FV@ ¢7FF–6ÖWF†ö@¢FVb÷F÷öÆöw•öf–ævW'&–çB‡7F÷'–&ö&C¢F–7E·7G"Âç•Ò’Óâ7G# ¢–ÆöBÒ°¢'6†÷G2#¢°¢°¢'6†÷Eö–B#¢&÷rævWB‚.™YÎZKN{ÉnXûr"’À¢'66VæR#¢&÷rævWB‚.YË®išþ‹ùî{ºÞh
t”B"’÷"&÷rævWB‚.YË®išþ‹ùî{ºÞh
v–B"’À¢&GW&F–öâ#¢&÷rævWB‚.i{n™[ò"’À¢&7V×VÆF—fR#¢&÷rævWB‚.{JþŠêi{n™[ò"’À¢'7F–Å÷÷6—F–öâ#¢&÷rævWB‚.z›®™{Nz¹žKØÞhøþ‹û"’À¢'7F'F–æu÷7FFR#¢&÷rævWB‚.‹[~Zx¾x«nh"’À¢Ð¢f÷"&÷r–â7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢ÒÀ¢'66†VÖ÷&öf–ÆR#¢7F÷'–&ö&BævWB‚'66†VÖ÷&öf–ÆR"’À¢Ð¢&WGW&â6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2‡–ÆöB’ ¢FVb÷&W—%ö—77VU÷F&vWG2€¢6VÆbÀ¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢—77VW3¢Æ—7E´ç•ÒÀ¢’ÓâÆ—7E¶F–7E·7G"Â7G%ÕÓ ¢""$æ÷&ÖÆ—¦RWfW'’&W—&&ÆR—77VRFòW†7B6æöæ–6Â6†÷BF&vWG2â""  ¢6†÷E÷&VfW&Væ6U÷Fõö–C¢F–7E·7G"Â7G%ÒÒ·Ð¢f÷"6†÷B–â7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ“ ¢–bæ÷B—6–ç7Fæ6R‡6†÷BÂF–7B“ ¢6öçF–çVP¢6æöæ–6Å÷6†÷Eö–BÒ7G"€¢6†÷BævWB‚.™YÎZKN{ÉnXûr"’÷"6†÷BævWB‚'6†÷E÷V–B"’÷"" ¢’ç7G&—‚¢–bæ÷B6æöæ–6Å÷6†÷Eö–C ¢6öçF–çVP¢f÷"&VfW&Væ6R–â‡6†÷BævWB‚.™YÎZKN{ÉnXûr"’Â6†÷BævWB‚'6†÷E÷V–B"’“ ¢6ÆVå÷&VfW&Væ6RÒ7G"‡&VfW&Væ6R÷"""’ç7G&—‚¢–b6ÆVå÷&VfW&Væ6S ¢6†÷E÷&VfW&Væ6U÷Fõö–E¶6ÆVå÷&VfW&Væ6UÒÒ6æöæ–6Å÷6†÷Eö–@ ¢—77VU÷F&vWG3¢Æ—7E¶F–7E·7G"Â7G%ÕÒÒµÐ¢6öç6–FW&VEö—77VUö6÷VçBÒ ¢f÷"—77VR–â—77VW3 ¢–bæ÷B—6–ç7Fæ6R†—77VRÂF–7B“ ¢6öçF–çVP¢6öç6–FW&VEö—77VUö6÷VçB³Ò¢—77VUö–BÒ7G"€¢—77VRævWB‚'6VÖçF–5ö—77VUö–B"¢÷"—77VRævWB‚&—77VUö–B"¢÷"—77VRævWB‚&W'&÷%ö6öFR"¢÷"$RÕTä´äõtâ ¢¢Æö6F–öâÒ7G"€¢—77VRævWB‚&Æö6F–öâ"’÷"b'6VÖçF–5öVF—Bç¶—77VUö–GÒ ¢¢f–VÆBÒ7G"€¢—77VRævWB‚&f–VÆB"’÷"Æö6F–öâç'7Æ—B‚"â"Â•²ÓÐ¢’ç7G&—‚¢W‡Æ–6—E÷&VfW&Væ6W2Ò—77VRævWB‚'6†÷Eö–G2"’÷"µÐ¢–bæ÷B—6–ç7Fæ6R†W‡Æ–6—E÷&VfW&Væ6W2ÂÆ—7B“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWEö–çfÆ–C¢—77VR6†÷Eö–G2×W7B&RÆ—7B ¢¢Væ¶æ÷vå÷&VfW&Væ6W2Ò6÷'FVB€¢°¢7G"‡&VfW&Væ6R’ç7G&—‚¢f÷"&VfW&Væ6R–âW‡Æ–6—E÷&VfW&Væ6W0¢–b7G"‡&VfW&Væ6R’ç7G&—‚’æ÷B–â6†÷E÷&VfW&Væ6U÷Fõö–@¢Ð¢¢–bVæ¶æ÷vå÷&VfW&Væ6W3 ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWEö–çfÆ–C¢6W'F–f–6FR&VfW&Væ6W2Væ¶æ÷vâ6†÷G2 ¢²"Â"æ¦ö–â‡Væ¶æ÷vå÷&VfW&Væ6W2¢¢6æöæ–6Å÷&VfW&Væ6W2Ò°¢6†÷E÷&VfW&Væ6U÷Fõö–E·7G"‡&VfW&Væ6R’ç7G&—‚•Ð¢f÷"&VfW&Væ6R–âW‡Æ–6—E÷&VfW&Væ6W0¢–b7G"‡&VfW&Væ6R’ç7G&—‚¢Ð¢–bÆVâ‡6WB†6æöæ–6Å÷&VfW&Væ6W2’’ÒÆVâ†6æöæ–6Å÷&VfW&Væ6W2“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWEö–çfÆ–C¢6†÷BÆ–6W2&WVBöæR6æöæ–6ÂF&vWB ¢¢W‡Æ–6—E÷6†÷Eö–G2Ò6WB†6æöæ–6Å÷&VfW&Væ6W2¢Æö6F–öåöÖF6‚Ò&Rç6V&6‚‡"'6†÷G5Å²…ÆB²•ÅÒ"ÂÆö6F–öâ¢Æö6F–öå÷6†÷Eö–G3¢6WE·7G%ÒÒ6WB‚¢f÷"FW&—fVB–â6VÆbå÷6†÷Eö–G5öf÷%öÆö6F–öâ‡7F÷'–&ö&BÂÆö6F–öâ“ ¢–bFW&—fVBæ÷B–â6†÷E÷&VfW&Væ6U÷Fõö–C ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWEö–çfÆ–C¢Æö6F–öâ&W6öÇfVB÷WG6–FRF†RVF—FVB'F–f7B ¢¢Æö6F–öå÷6†÷Eö–G2æFB‡6†÷E÷&VfW&Væ6U÷Fõö–E¶FW&—fVEÒ¢–bÆö6F–öåöÖF6‚æBæ÷BÆö6F–öå÷6†÷Eö–G3 ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWEö–çfÆ–C¢–æFW†VBÆö6F–öâ—2÷WG6–FRF†RVF—FVB'F–f7B ¢¢–b€¢W‡Æ–6—E÷6†÷Eö–G0¢æBÆö6F–öå÷6†÷Eö–G0¢æBW‡Æ–6—E÷6†÷Eö–G2ÒÆö6F–öå÷6†÷Eö–G0¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWEö6öæfÆ–7C¢W‡Æ–6—B6†÷Eö–G2æBÆö6F–öâF—6w&VR ¢¢6†÷Eö–G2ÒW‡Æ–6—E÷6†÷Eö–G2÷"Æö6F–öå÷6†÷Eö–G0¢–b&ööÂ†—77VRævWB‚&&Æö6¶–ær"ÂG'VR’’æBæ÷B6†÷Eö–G3 ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWE÷&WV—&VC¢WfW'’&Æö6¶–ærVF—B—77VR×W7BWF†÷&—¦R6öæ7&WFR6†÷B ¢¢f÷"6†÷Eö–B–â6÷'FVB‡6†÷Eö–G2“ ¢—77VU÷F&vWG2æVæB€¢°¢&—77VUö–B#¢—77VUö–BÀ¢&—77VUö6öFR#¢7G"€¢—77VRævWB‚&W'&÷%ö6öFR"’÷"$RÕTä´äõtâ ¢’À¢'6†÷Eö–B#¢6†÷Eö–BÀ¢&f–VÆB#¢f–VÆBÀ¢&Æö6F–öâ#¢Æö6F–öâÀ¢Ð¢¢Væ—VRÒ°¢€¢&÷u²&—77VUö–B%ÒÀ¢&÷u²&—77VUö6öFR%ÒÀ¢&÷u²'6†÷Eö–B%ÒÀ¢&÷u²&f–VÆB%ÒÀ¢&÷u²&Æö6F–öâ%ÒÀ¢“¢&÷p¢f÷"&÷r–â—77VU÷F&vWG0¢Ð¢æ÷&ÖÆ—¦VBÒÆ—7B‡Væ—VRçfÇVW2‚’¢–b6öç6–FW&VEö—77VUö6÷VçBæBæ÷Bæ÷&ÖÆ—¦VC ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷F&vWE÷&WV—&VC¢VF—B—77VW26ææ÷B&öGV6RâV×G’&W—"&÷WFR ¢¢&WGW&âæ÷&ÖÆ—¦V@ ¢FVböFWFW&Ö–æ—7F–5÷&W—%÷6¶vUöæEö†æFöfb€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢&öGV7F–öåö†æFöfc¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢’ÓâGWÆU¶F–7E·7G"Âç•ÒÂæöæRÂF–7E·7G"Âç•ÒÂæöæUÓ ¢""$'V–ÆBF†RW†7B–Ö×WF&ÆR÷'F–öâöbf–ÆVBÖVF—B&W—"&÷WFRâ""  ¢—77VU÷F&vWG2Ò6VÆbå÷&W—%ö—77VU÷F&vWG2€¢7F÷'–&ö&BÀ¢Æ—7B†6W'F–f–6FRævWB‚'&W÷'B"Â·Ò’ævWB‚&—77VW2"ÂµÒ’’À¢¢–bæ÷B—77VU÷F&vWG3 ¢&WGW&âæöæRÂæöæP¢6W'F–f–6FUö–BÒ7G"†6W'F–f–6FU²&6W'F–f–6FUö–B%Ò¢&W—%ö–FVçF—G’Ò6†#Seö'—FW2€¢b&–Ö×WF&ÆR×&W—"×&÷WFS§¶6W'F–f–6FUö–GÒ"æVæ6öFR‚'WFbÓ‚"¢¢&W—%÷6¶vUö–BÒb%%²×·&W—%ö–FVçF—G•³£#E×Ò ¢&W—%ö†æFöfeö–BÒb$„äB×·&W—%ö–FVçF—G•³#C£C…×Ò ¢—77VVEöBÒ7G"†6W'F–f–6FRævWB‚&7&VFVEöB"’÷"""¢G'“ ¢—77VVEöFFWF–ÖRÒFFWF–ÖRæg&öÖ—6öf÷&ÖB€¢—77VVEöBç&WÆ6R‚%¢"Â"³£"¢¢–b—77VVEöFFWF–ÖRçG¦–æfò—2æöæS ¢—77VVEöFFWF–ÖRÒ—77VVEöFFWF–ÖRç&WÆ6R‡G¦–æfó×F–ÖW¦öæRçWF2¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"’2W†3 ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6W'F–f–6FR7&VF–öâF–ÖR—2–çfÆ–B ¢’g&öÒW†0¢W‡—&W5öBÒ†—77VVEöFFWF–ÖR²F–ÖVFVÇF†F—3Ór’’ç&WÆ6R€¢Ö–7&÷6V6öæCÓ ¢’æ—6öf÷&ÖB‚’ç&WÆ6R‚"³£"Â%¢"¢f–ævW'&–çG2Ò6÷’æFVW6÷’‡&öGV7F–öåö†æFöfe²&f–ævW'&–çG2%Ò¢VF—Eö&–æF–ærÒ6VÆbå÷fW&–f–VEöVF—Eö6W'F–f–6FUöWF†÷&—G’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢&÷FV7FVBÒ&öGV7F–öåö†æFöfbævWB‚'66÷R"Â·Ò’ævWB‚'&÷FV7FVE÷66÷R"ÂµÒ¢&öGV7F–öå÷6W76–öâÒ&öGV7F–öåö†æFöfbævWB‚&g&öÕ÷6W76–öåöf–ævW'&–çB"’÷"6†#Seö'—FW2€¢€¢&Æö6Â×6W'fW"Ö&÷VæB×&öGV7F–öã¢ ¢²7G"‡&öGV7F–öåö†æFöfbævWB‚&g&öÕ÷v–æF÷uö&–æF–æuö–B"’÷"&Ö—76–ær"¢’æVæ6öFR‚'WFbÓ‚"¢¢VF—E÷6W76–öâÒVF—Eö&–æF–ærævWB‚'6W76–öåöf–ævW'&–çB"’÷"6†#Seö'—FW2€¢€¢&Æö6Â×6W'fW"Ö&÷VæBÖVF—C¢ ¢²7G"†VF—Eö&–æF–ærævWB‚'v–æF÷uö&–æF–æuö–B"’÷"&Ö—76–ær"¢’æVæ6öFR‚'WFbÓ‚"¢¢6¶vRÒ°¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢&VF—Eö6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&VF—EöWfVçEö–B#¢6W'F–f–6FU²&WfVçEö–B%ÒÀ¢&VF—E÷Fõ÷&W—%ö†æFöfeö–B#¢&W—%ö†æFöfeö–BÀ¢'&öGV7F–öåö†æFöfeö–B#¢&öGV7F–öåö†æFöfe²&†æFöfeö–B%ÒÀ¢'&öGV7F–öå÷v÷&µö÷&FW%ö–B#¢&öGV7F–öåö†æFöfe²'v÷&µö÷&FW%ö–B%ÒÀ¢'&öGV7F–öå÷v–æF÷uö&–æF–æuö–B#¢&öGV7F–öåö†æFöfbævWB‚&g&öÕ÷v–æF÷uö&–æF–æuö–B"’À¢'&öGV7F–öåö7F÷%ö&–æF–æuö–B#¢&öGV7F–öåö†æFöfbævWB‚&g&öÕö7F÷%ö&–æF–æuö–B"’À¢'&öGV7F–öå÷6W76–öåöf–ævW'&–çB#¢&öGV7F–öå÷6W76–öâÀ¢&VF—E÷v÷&µö÷&FW%ö–B#¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢&VF—E÷v–æF÷uö&–æF–æuö–B#¢VF—Eö&–æF–ærævWB‚'v–æF÷uö&–æF–æuö–B"’À¢&VF—Eö7F÷%ö&–æF–æuö–B#¢VF—Eö&–æF–ærævWB‚&7F÷%ö&–æF–æuö–B"’À¢&VF—E÷6W76–öåöf–ævW'&–çB#¢VF—E÷6W76–öâÀ¢&VF—EöWF†÷&—G•÷&ö÷Eö–B#¢VF—Eö&–æF–ærævWB€¢&WF†÷&—G•÷&ö÷Eö–B ¢’À¢&VF—EöWF†÷&—G•öWö6…ö–B#¢VF—Eö&–æF–ærævWB€¢&WF†÷&—G•öWö6…ö–B ¢’À¢&VF—EöWF†÷&—G•övVæW&F–öâ#¢VF—Eö&–æF–ærævWB‚&vVæW&F–öâ"’À¢&VF—EöWF†÷&—G•ö6öçF–çVF–öåö–B#¢VF—Eö&–æF–ærævWB€¢&6öçF–çVF–öåö–B ¢’À¢&7&VFVEöB#¢—77VVEöBÀ¢&W‡—&W5öB#¢W‡—&W5öBÀ¢'7FGW2#¢&—77VVB"À¢&&6Uö'F–f7Eö–B#¢6W'F–f–6FU²&'F–f7Eö–B%ÒÀ¢&&6Uö6æöæ–6Å÷6†#Sb#¢6W'F–f–6FU²&6æöæ–6Å÷6†#Sb%ÒÀ¢&f–ævW'&–çG2#¢f–ævW'&–çG2À¢&WF†÷&—¦VE÷66÷R#¢°¢'6†÷Eö–G2#¢6÷'FVB‡·&÷u²'6†÷Eö–B%Òf÷"&÷r–â—77VU÷F&vWG7Ò’À¢&f–VÆG2#¢6÷'FVB‡·&÷u²&f–VÆB%Òf÷"&÷r–â—77VU÷F&vWG7Ò’À¢&—77VUö6öFW2#¢6÷'FVB‡·&÷u²&—77VUö6öFR%Òf÷"&÷r–â—77VU÷F&vWG7Ò’À¢ÒÀ¢'&÷FV7FVE÷66÷Uö†6‚#¢6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2‡&÷FV7FVB’’À¢&—77VU÷F&vWG2#¢—77VU÷F&vWG2À¢'F÷öÆöw•öf–ævW'&–çB#¢6VÆbå÷F÷öÆöw•öf–ævW'&–çB‡7F÷'–&ö&B’À¢'6÷W&6UöVF—Eö†æFöfeö–B#¢&öGV7F–öåö†æFöfe²&†æFöfeö–B%ÒÀ¢'6÷W&6UöVF—EöWfVçE÷G&6Uö–B#¢6W'F–f–6FU²'7FvU÷G&6Uö–B%ÒÀ¢'6÷W&6UöVF—E÷&÷WFU÷G&6Uö–B#¢6W'F–f–6FU°¢'&öGV7F–öå÷G&6Uö–B ¢ÒÀ¢'6÷W&6U÷&öGV7F–öå÷7FvU÷G&6Uö–B#¢6W'F–f–6FU°¢'&öGV7F–öå÷7FvU÷G&6Uö–B ¢ÒÀ¢Ð¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&–Ö×WF&ÆU÷&W—%÷6¶vR"Â6¶vP¢¢&W—%ö†æFöfbÒ6÷’æFVW6÷’‡&öGV7F–öåö†æFöfb¢f÷"¶W’–â€¢'&V6÷&Eö–B"À¢'&ö¦V7Eö–B"À¢&–çFVw&—G•ö†Ö2"À¢&6Æ–ÖVEö'•÷v÷&µö÷&FW%ö–B"À¢&6Æ–Õ÷Fö¶Vâ"À¢&6Æ–ÕövVæW&F–öâ"À¢&6Æ–ÕöWö6…÷6V6öæG2"À¢&6Æ–ÕöÆV6U÷6V6öæG2"À¢&6Æ–ÕöÆV6UöW‡—&W5öB"À¢&6Æ–ÕöÆV6UöW‡—&W5öWö6…÷6V6öæG2"À¢&6Æ–ÕöWF†÷&—G•ö6öçG&7B"À¢&6Æ–ÕöWF†÷&—G•÷&ö÷Eö–B"À¢&6Æ–ÕöWF†÷&—G•övVæW&F–öâ"À¢&6Æ–ÕöWF†÷&—G•÷6W76–öåöf–ævW'&–çB"À¢&6Æ–Õö†—7F÷'’"À¢&–FV×÷FVçE÷&WÆ’"À¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"À¢&6öç7VÖVEöB"À¢&6öç7V×F–öåö6Æ–Ò"À¢&ÆFW7EöVF—Eö6W'F–f–6FUö–B"À¢'&VVF—Eö¶–æB"À¢'&VVF—E÷&ö÷Eö†æFöfeö–B"À¢'&VVF—E÷&VçEö†æFöfeö–B"À¢'&VVF—E÷6WVVæ6R"À¢'&VVF—E÷&÷WFU÷G&6Uö–B"À¢'&VVF—EöWfVçE÷G&6Uö–B"À¢'&VVF—EöÆ–æVvU÷6†#Sb"À¢'&VVF—EöVF—E÷v–æF÷uö&–æF–æuö–B"À¢'&VVF—EöVF—Eö7F÷%ö&–æF–æuö–B"À¢'&VVF—EöVF—E÷6W76–öåöf–ævW'&–çB"À¢'&VVF—EöVF—EöWF†÷&—G•÷&ö÷Eö–B"À¢'&VVF—EöVF—EöWF†÷&—G•öWö6…ö–B"À¢'&VVF—EöVF—EöWF†÷&—G•övVæW&F–öâ"À¢'&VVF—EöVF—EöWF†÷&—G•ö6öçF–çVF–öåö–B"À¢'&Wf–÷W5öVF—Eö6W'F–f–6FUö–B"À¢'&Wf–÷W5öVF—Eö6W'F–f–6FUö†Ö2"À¢'&Wf–÷W5öVF—EöWfVçE÷6†#Sb"À¢'6W'fW%öÖ–çFVEöÆ–æVvR"À¢'&VVF—E÷&V6Æ–Õ÷&ö¦V7F–öå÷6†#Sb"À¢'&VVF—E÷&W&VÆV6U÷&ö¦V7F–öå÷6†#Sb"À¢“ ¢&W—%ö†æFöfbç÷†¶W’ÂæöæR¢&W—%ö†æFöfbçWFFR€¢°¢&†æFöfeö–B#¢&W—%ö†æFöfeö–BÀ¢'v÷&µö÷&FW%ö–B#¢7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢&VF—Eö7–6ÆUö–B#¢7G"‡&öGV7F–öåö†æFöfe²&VF—Eö7–6ÆUö–B%Ò’À¢&†æFöfe÷G—R#¢&VF—E÷Fõ÷&W—""À¢&7&VFVEöB#¢—77VVEöBÀ¢&g&öÕ÷v–æF÷u÷&öÆR#¢&–æFWVæFVçEöVF—B"À¢'Fõ÷v–æF÷u÷&öÆR#¢'&W—""À¢&VF—Eö6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&–Ö×WF&ÆU÷&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢&6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢'7FvU÷G&6Uö–B#¢6W'F–f–6FU²'7FvU÷G&6Uö–B%ÒÀ¢'7FGW2#¢'&W&VB"À¢'&öGV6W%ö6Æ–×2#¢°¢¢¦6÷’æFVW6÷’‡&öGV7F–öåö†æFöfe²'&öGV6W%ö6Æ–×2%Ò’À¢&6Æ–Õ÷G—R#¢&VF—Eö—77VUö&6VÆ–æR"À¢'&VfÆ–v‡Eöf–æF–æw2#¢6÷’æFVW6÷’†6W'F–f–6FU²'&W÷'B%Õ²&—77VW2%Ò’À¢ÒÀ¢&g&öÕ÷v–æF÷uö&–æF–æuö–B#¢VF—Eö&–æF–ærævWB€¢'v–æF÷uö&–æF–æuö–B ¢’À¢&g&öÕö7F÷%ö&–æF–æuö–B#¢VF—Eö&–æF–ærævWB€¢&7F÷%ö&–æF–æuö–B ¢’À¢&g&öÕ÷6W76–öåöf–ævW'&–çB#¢VF—Eö&–æF–ærævWB€¢'6W76–öåöf–ævW'&–çB ¢’À¢&g&öÕöWF†÷&—G•÷&ö÷Eö–B#¢VF—Eö&–æF–ærævWB€¢&WF†÷&—G•÷&ö÷Eö–B ¢’À¢&g&öÕöWF†÷&—G•öWö6…ö–B#¢VF—Eö&–æF–ærævWB€¢&WF†÷&—G•öWö6…ö–B ¢’À¢&g&öÕöWF†÷&—G•övVæW&F–öâ#¢VF—Eö&–æF–ærævWB€¢&vVæW&F–öâ ¢’À¢'6÷W&6UöVF—Eö†æFöfeö–B#¢&öGV7F–öåö†æFöfe²&†æFöfeö–B%ÒÀ¢'6÷W&6UöVF—EöWfVçE÷G&6Uö–B#¢6W'F–f–6FU°¢'7FvU÷G&6Uö–B ¢ÒÀ¢'6÷W&6UöVF—E÷&÷WFU÷G&6Uö–B#¢6W'F–f–6FU°¢'&öGV7F–öå÷G&6Uö–B ¢ÒÀ¢Ð¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â&W—%ö†æFöf`¢¢&WGW&â6¶vRÂ&W—%ö†æFöf` ¢FVb÷W'6—7E÷&W—%÷6¶vUöæEö†æFöfb€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢&öGV7F–öåö†æFöfc¢F–7E·7G"Âç•ÒÀ¢VF—E÷v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢&ö÷Eö†æFöfc¢F–7E·7G"Âç•ÒÂæöæRÒæöæRÀ¢’ÓâGWÆU¶F–7E·7G"Âç•ÒÂæöæRÂF–7E·7G"Âç•ÒÂæöæUÓ ¢6¶vRÂ&W—%ö†æFöfbÒ€¢6VÆbåöFWFW&Ö–æ—7F–5÷&W—%÷6¶vUöæEö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢&öGV7F–öåö†æFöfc×&öGV7F–öåö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢¢–b6¶vR—2æöæR÷"&W—%ö†æFöfb—2æöæS ¢&WGW&âæöæRÂæöæP¢6W'F–f–6FUö–BÒ7G"†6W'F–f–6FU²&6W'F–f–6FUö–B%Ò¢&W—%÷6¶vUö–BÒ7G"‡6¶vU²'&W—%÷6¶vUö–B%Ò¢&W—%ö†æFöfeö–BÒ7G"‡&W—%ö†æFöfe²&†æFöfeö–B%Ò¢&W—%÷&ö÷Eö†æFöfbÒ&ö÷Eö†æFöfb÷"&öGV7F–öåö†æFöf`¢W‡V7FVE÷&ö÷Eö†æFöfeö–BÒ7G"€¢&öGV7F–öåö†æFöfbævWB‚'&VVF—E÷&ö÷Eö†æFöfeö–B"¢÷"&öGV7F–öåö†æFöfe²&†æFöfeö–B%Ð¢¢–b€¢&W—%÷&ö÷Eö†æFöfbævWB‚&†æFöfeö–B"¢ÒW‡V7FVE÷&ö÷Eö†æFöfeö–@¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷&÷WFUög&W6†æW75ö–çfÆ–C¢fW&–f–VB&ö÷B†æFöfbF–ffW'2 ¢¢WF†÷&—G•öÖæ–fW7C¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢–b&W—%÷&ö÷Eö†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖVB# ¢&÷WFUöf–ævW'&–çG2Ò&öGV7F–öåö†æFöfbævWB‚&f–ævW'&–çG2"¢–bæ÷B—6–ç7Fæ6R‡&÷WFUöf–ævW'&–çG2ÂF–7B“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷&÷WFUög&W6†æW75ö–çfÆ–C¢&÷WFRf–ævW'&–çG2&RÖ—76–ær ¢¢fW&–f–VE÷&ö÷BÂfW&–f–VEöÆFW7BÒ6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfc×&öGV7F–öåö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3×&÷WFUöf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÕG'VRÀ¢&W—%÷&ö¦V7F–öãÔfÇ6RÀ¢WF†÷&—G•öÖæ–fW7Eö÷WCÖWF†÷&—G•öÖæ–fW7BÀ¢¢–b€¢fW&–f–VE÷&ö÷BævWB‚&†æFöfeö–B"¢Ò&W—%÷&ö÷Eö†æFöfbævWB‚&†æFöfeö–B"¢÷"fW&–f–VEöÆFW7BævWB‚&†æFöfeö–B"¢Ò&öGV7F–öåö†æFöfbævWB‚&†æFöfeö–B"¢÷"fW&–f–VEöÆFW7BævWB‚&–çFVw&—G•ö†Ö2"¢Ò&öGV7F–öåö†æFöfbævWB‚&–çFVw&—G•ö†Ö2"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷&÷WFUög&W6†æW75ö–çfÆ–C¢fW&–f–VBVF—B&÷WFR6†ævVB&Vf÷&R&W—"V&Æ–6F–öâ ¢¢W'6—7FVE÷6¶vRÂW'6—7FVEö†æFöfbÒ€¢6VÆbç7F÷&Ræ6öÖÖ—E÷&W—%÷6¶vU÷&÷WFR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vUö–C×&W—%÷6¶vUö–BÀ¢6¶vS×6¶vRÀ¢†æFöfeö–C×&W—%ö†æFöfeö–BÀ¢†æFöfc×&W—%ö†æFöfbÀ¢W‡V7FVE÷&ö÷Eö†æFöfeö–CÖW‡V7FVE÷&ö÷Eö†æFöfeö–BÀ¢W‡V7FVE÷&ö÷Eö†æFöfeö†Ö3×7G"€¢&W—%÷&ö÷Eö†æFöfbævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢W‡V7FVEöÆFW7Eö†æFöfeö–C×7G"€¢&öGV7F–öåö†æFöfe²&†æFöfeö–B%Ð¢’À¢W‡V7FVEöÆFW7Eö†æFöfeö†Ö3×7G"€¢&öGV7F–öåö†æFöfbævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢W‡V7FVEöÆFW7Eö6W'F–f–6FUö–CÖ6W'F–f–6FUö–BÀ¢W‡V7FVEöÆFW7Eö6W'F–f–6FUö†Ö3×7G"€¢6W'F–f–6FRævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢Æ–æVvUöWF†÷&—G•öÖæ–fW7CÖWF†÷&—G•öÖæ–fW7BÀ¢W‡V7FVEö6Æ–Õ÷Fö¶VãÒ€¢7G"‡&öGV7F–öåö†æFöfbævWB‚&6Æ–Õ÷Fö¶Vâ"’¢–b&öGV7F–öåö†æFöfbævWB‚&6Æ–Õ÷Fö¶Vâ"¢VÇ6RæöæP¢’À¢W‡V7FVEö6Æ–ÕövVæW&F–öãÒ€¢–çB‡&öGV7F–öåö†æFöfe²&6Æ–ÕövVæW&F–öâ%Ò¢–b&öGV7F–öåö†æFöfbævWB‚&6Æ–ÕövVæW&F–öâ"¢—2æ÷BæöæP¢VÇ6RæöæP¢’À¢¢¢&WGW&âW'6—7FVE÷6¶vRÂW'6—7FVEö†æFöf` ¢FVbö76W'EöÆFW7E÷&W—%÷6¶vUöWF†÷&—G’€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢6¶vS¢F–7E·7G"Âç•ÒÀ¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢&6Uö'F–f7Eö–C¢7G"À¢7W'&VçEöf–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢’ÓâæöæS ¢""%&V¦V7B&W—"&÷WFR7WW'6VFVB'’ÆFW"6ö×ÆWFVB&RÖVF—Bâ""  ¢6÷W&6Uö†æFöfeö–BÒ7G"‡6¶vRævWB‚'&öGV7F–öåö†æFöfeö–B"’÷"""¢G'“ ¢6÷W&6Uö†æFöfbÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×6÷W&6Uö†æFöfeö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B6VÆbåö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†2À¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×6÷W&6Uö†æFöfeö–BÀ¢“ ¢&—6P¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢÷&–v–æÂ&öGV7F–öâ†æFöfb—2Ö—76–ær ¢’g&öÒW†0¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡6÷W&6Uö†æFöfb“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6÷W&6RVF—B†æFöfb—2Vç6–væVB÷"F×W&VB ¢¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢VF—E÷v÷&µö÷&FW"ÒæW‡B€¢€¢&÷p¢f÷"&÷r–â‡V"ævWB‚'v÷&µö÷&FW'2"ÂµÒ¢–b&÷rævWB‚'v÷&µö÷&FW%ö–B"¢ÓÒ6¶vRævWB‚&VF—E÷v÷&µö÷&FW%ö–B"¢’À¢æöæRÀ¢¢–bæ÷B—6–ç7Fæ6R†VF—E÷v÷&µö÷&FW"ÂF–7B“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6÷W&6RVF—Bv÷&²÷&FW"—2Ö—76–ær ¢¢&ö÷Eö†æFöfbÂÆFW7Eö†æFöfbÒ6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfc×6÷W&6Uö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Ö7W'&VçEöf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÔfÇ6RÀ¢¢ÆFW7Eö6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–C×7G"†ÆFW7Eö†æFöfbævWB‚&VF—Eö6W'F–f–6FUö–B"’÷"""’À¢¢W‡V7FVEöWfVçE÷G&6Uö–BÒ7G"€¢ÆFW7Eö6W'F–f–6FRævWB‚'7FvU÷G&6Uö–B"’÷"" ¢¢W‡V7FVE÷&÷WFU÷G&6Uö–BÒ7G"€¢ÆFW7Eö6W'F–f–6FRævWB‚'&öGV7F–öå÷G&6Uö–B"’÷"" ¢¢W‡V7FVE÷&öGV7F–öå÷G&6Uö–BÒ7G"€¢ÆFW7Eö6W'F–f–6FRævWB‚'&öGV7F–öå÷7FvU÷G&6Uö–B"’÷"" ¢¢W‡V7FVEöWF†÷&—G’Ò°¢'&öGV7F–öåö†æFöfeö–B#¢ÆFW7Eö†æFöfbævWB‚&†æFöfeö–B"’À¢'6÷W&6UöVF—Eö†æFöfeö–B#¢ÆFW7Eö†æFöfbævWB‚&†æFöfeö–B"’À¢&VF—Eö6W'F–f–6FUö–B#¢ÆFW7Eö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"’À¢&VF—EöWfVçEö–B#¢ÆFW7Eö6W'F–f–6FRævWB‚&WfVçEö–B"’À¢'6÷W&6UöVF—EöWfVçE÷G&6Uö–B#¢W‡V7FVEöWfVçE÷G&6Uö–BÀ¢'6÷W&6UöVF—E÷&÷WFU÷G&6Uö–B#¢W‡V7FVE÷&÷WFU÷G&6Uö–BÀ¢'6÷W&6U÷&öGV7F–öå÷7FvU÷G&6Uö–B#¢W‡V7FVE÷&öGV7F–öå÷G&6Uö–BÀ¢Ð¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†ÆFW7Eö6W'F–f–6FR¢÷"6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢ÒÆFW7Eö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢÷"ç’€¢6¶vRævWB†¶W’’ÒfÇVP¢f÷"¶W’ÂfÇVR–âW‡V7FVEöWF†÷&—G’æ—FV×2‚¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷7WW'6VFVC¢ÆFW"6ö×ÆWFVB–æFWVæFVçBVF—B—2F†RöæÇ’&W—"WF†÷&—G’ ¢ ¢ÆVFvW"Ò6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B¢ÖF6†–æuöWfVçG2Ò°¢&÷p¢f÷"&÷r–âÆVFvW"ævWB‚&WfVçG2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB&÷rævWB‚&WfVçEö–B"’ÓÒÆFW7Eö6W'F–f–6FRævWB‚&WfVçEö–B"¢Ð¢–bÆVâ†ÖF6†–æuöWfVçG2’Ò ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢ÆFW7BVF—BWfVçB—2Ö—76–ær÷"GWÆ–6FVB ¢¢WfVçBÒÖF6†–æuöWfVçG5³Ð¢W‡V7FVEöWfVçEö&–æF–æw2Ò°¢&†æFöfeö–B#¢ÆFW7Eö†æFöfbævWB‚&†æFöfeö–B"’À¢'v–æF÷uö¶W’#¢VF—E÷v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’À¢'7FvU÷G&6Uö–B#¢W‡V7FVEöWfVçE÷G&6Uö–BÀ¢&VF—Eö66†Uö¶W’#¢ÆFW7Eö6W'F–f–6FRævWB‚&66†Uö¶W’"’À¢&f–ÆUöf–ævW'&–çB#¢7W'&VçEöf–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢&'F–f7E÷6VÖçF–5öf–ævW'&–çB#¢7W'&VçEöf–ævW'&–çG5°¢&'F–f7E÷6VÖçF–2 ¢ÒÀ¢&6æöæ–6Åöf–ævW'&–çB#¢7W'&VçEöf–ævW'&–çG5²&6æöæ–6Â%ÒÀ¢'6÷W&6Uöf–ævW'&–çB#¢7W'&VçEöf–ævW'&–çG5²'6÷W&6Uö'—FW2%ÒÀ¢&&÷VæF'•öf–ævW'&–çB#¢7W'&VçEöf–ævW'&–çG5°¢&W—6öFUö&÷VæF'’ ¢ÒÀ¢Ð¢–bç’€¢WfVçBævWB†¶W’’ÒfÇVP¢f÷"¶W’ÂfÇVR–âW‡V7FVEöWfVçEö&–æF–æw2æ—FV×2‚¢’÷"æ÷Bç’€¢—6–ç7Fæ6R‡66÷RÂF–7B¢æB66÷RævWB‚&Æ7EöWfVçEö–B"’ÓÒWfVçBævWB‚&WfVçEö–B"¢f÷"66÷R–âÆVFvW"ævWB‚'66÷U÷7FFW2"ÂµÒ¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷7WW'6VFVC¢6¶vRWfVçB—2æ÷BF†RÆFW7BÆVFvW"66÷RWF†÷&—G’ ¢ ¢&öGV7F–öå÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–CÖW‡V7FVE÷&öGV7F–öå÷G&6Uö–BÀ¢¢VF—E÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–CÖW‡V7FVEöWfVçE÷G&6Uö–BÀ¢¢&ö÷Eö†æFöfe÷G—RÒ7G"‡&ö÷Eö†æFöfbævWB‚&†æFöfe÷G—R"’÷"""¢–çF¶U÷6÷W&6U÷G&6RÒ&ö÷Eö†æFöfe÷G—RÓÒ&VF—Eö–çF¶R ¢W‡V7FVE÷6÷W&6U÷G&6U÷&öÆRÒ€¢&–æFWVæFVçEöVF—B ¢–b–çF¶U÷6÷W&6U÷G&6P¢VÇ6R&ö÷Eö†æFöfbævWB‚&g&öÕ÷v–æF÷u÷&öÆR"¢¢–b€¢&ö÷Eö†æFöfbævWB‚'7FvU÷G&6Uö–B"¢ÒW‡V7FVE÷&öGV7F–öå÷G&6Uö–@¢2âW‡FW&æÂ–çF¶R†2æò&öGV6W"FVÆVÖWG'’â—G2&ö÷BG&6R—0¢2F†RW†7BVF—BWfVçBF†B7&VFVBF†R6–væVB–çF¶R†æFöfbÂ6ð¢2F†BG&6R6÷'&V7FÇ’&VÆöæw2Fò–æFWVæFVçEöVF—B&F†W"F†à¢2–×W'6öæF–ærW‡FW&æÅö–çWBâWfW'’&öGV6VB6÷W&6R¶VW2¢2F—7F–æ7B&öGV6W"G&6RæBF†R6–væVB6÷W&6R×v–æF÷r&öÆRà¢÷"€¢W‡V7FVE÷&öGV7F–öå÷G&6Uö–BÓÒW‡V7FVEöWfVçE÷G&6Uö–@¢æBæ÷B–çF¶U÷6÷W&6U÷G&6P¢¢÷"€¢–çF¶U÷6÷W&6U÷G&6P¢æB€¢&ö÷Eö†æFöfbævWB‚&g&öÕ÷v–æF÷u÷&öÆR"’Ò&W‡FW&æÅö–çWB ¢÷"&ö÷Eö†æFöfbævWB‚'Fõ÷v–æF÷u÷&öÆR"¢Ò&–æFWVæFVçEöVF—B ¢÷"&öGV7F–öå÷G&6RævWB‚&÷W&F–öâ"’Ò&VF—E÷7F÷'–&ö&B ¢¢¢÷"æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&öGV7F–öå÷G&6R¢÷"&öGV7F–öå÷G&6RævWB‚'G&6Uö–B"¢ÒW‡V7FVE÷&öGV7F–öå÷G&6Uö–@¢÷"&öGV7F–öå÷G&6RævWB‚'v–æF÷u÷&öÆR"¢ÒW‡V7FVE÷6÷W&6U÷G&6U÷&öÆP¢÷"&öGV7F–öå÷G&6RævWB‚&'F–f7Eö–B"’Ò&6Uö'F–f7Eö–@¢÷"&öGV7F–öå÷G&6RævWB‚&–çWEöf–ævW'&–çG2"¢Ò7W'&VçEöf–ævW'&–çG0¢÷"æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†VF—E÷G&6R¢÷"VF—E÷G&6RævWB‚'G&6Uö–B"’ÒW‡V7FVEöWfVçE÷G&6Uö–@¢÷"VF—E÷G&6RævWB‚&÷W&F–öâ"’Ò&VF—E÷7F÷'–&ö&B ¢÷"VF—E÷G&6RævWB‚'v–æF÷u÷&öÆR"’Ò&–æFWVæFVçEöVF—B ¢÷"VF—E÷G&6RævWB‚&'F–f7Eö–B"’Ò&6Uö'F–f7Eö–@¢÷"VF—E÷G&6RævWB‚&–çWEöf–ævW'&–çG2"¢Ò7W'&VçEöf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢&öGV7F–öâ÷"VF—BG&6R6†ævVB–FVçF—G’ ¢¢–bÆFW7Eö†æFöfbævWB‚'&VVF—Eö¶–æB"“ ¢&÷WFU÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–CÖW‡V7FVE÷&÷WFU÷G&6Uö–BÀ¢¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&÷WFU÷G&6R¢÷"&÷WFU÷G&6RævWB‚'G&6Uö–B"’ÒW‡V7FVE÷&÷WFU÷G&6Uö–@¢÷"&÷WFU÷G&6RævWB‚&÷W&F–öâ"¢Ò&VF—E÷7F÷'–&ö&E÷&VVF—E÷&÷WFR ¢÷"&÷WFU÷G&6RævWB‚'v–æF÷u÷&öÆR"’Ò&–æFWVæFVçEöVF—B ¢÷"&÷WFU÷G&6RævWB‚&'F–f7Eö–B"’Ò&6Uö'F–f7Eö–@¢÷"&÷WFU÷G&6RævWB‚&–çWEöf–ævW'&–çG2"¢Ò7W'&VçEöf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢&RÖVF—B&÷WFRG&6R6†ævVB–FVçF—G’ ¢¢VÆ–bW‡V7FVE÷&÷WFU÷G&6Uö–BÒW‡V7FVE÷&öGV7F–öå÷G&6Uö–C ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢&ö÷BVF—B&÷WFRG&6R6†ævVB–FVçF—G’ ¢ ¢7FF–6ÖWF†ö@¢FVbö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†3¢fÇVTW'&÷"À¢¢À¢¶–æC¢7G"À¢&V6÷&Eö–C¢7G"À¢’Óâ&ööÃ ¢""$F—7F–æwV—6‚â'6VçB&V6÷&Bg&öÒFÖvVB&V6÷&Bv—F‚F†R6ÖR”Bâ""  ¢&WGW&â7G"†W†2’ÓÒb'Væ¶æ÷vâ¶¶–æGÒ&V6÷&Eö–C¢·&V6÷&Eö–GÒ  ¢FVbö6Æ–Õö—5ö7W'&VçEöWF†÷&—G•öæ6W7F÷"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6Æ–Ó¢F–7E·7G"Âç•ÒÀ¢’Óâ&ööÃ ¢""%&WGW&âv†WF†W"öæR6–væVB6Æ–Ò&VÆöæw2FòF†—2WF†÷&—G’6†–ââ""  ¢v÷&µö÷&FW%ö–BÒ7G"‡v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’÷"""¢&ö÷Eö–BÒ6VÆbåöWF†÷&—G•÷&ö÷Eö–B‡&ö¦V7Eö–BÂv÷&µö÷&FW%ö–B¢7W'&VçBÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÂ&ö¦V7Eö–BÂv÷&µö÷&FW ¢¢vVæW&F–öâÒ6Æ–ÒævWB‚&6Æ–ÕöWF†÷&—G•övVæW&F–öâ"¢–b€¢6Æ–ÒævWB‚&6Æ–ÕöWF†÷&—G•ö6öçG&7B"¢Ò'v–æF÷r×&öÆRÖWF†÷&—G’Ö6Æ–Ò×c ¢÷"6Æ–ÒævWB‚&6Æ–ÕöWF†÷&—G•÷&ö÷Eö–B"’Ò&ö÷Eö–@¢÷"—6–ç7Fæ6R†vVæW&F–öâÂ&ööÂ¢÷"æ÷B—6–ç7Fæ6R†vVæW&F–öâÂ–çB¢÷"vVæW&F–öâÂ ¢÷"7W'&VçBævWB‚&WF†÷&—G•÷&ö÷Eö–B"’Ò&ö÷Eö–@¢÷"æ÷B—6–ç7Fæ6R†7W'&VçBævWB‚&vVæW&F–öâ"’Â–çB¢÷"vVæW&F–öââ–çB†7W'&VçE²&vVæW&F–öâ%Ò¢“ ¢&WGW&âfÇ6P¢G'“ ¢6Æ–ÖVEö&–æF–ærÒ6VÆbå÷fW&–f–VEöWF†÷&—G•ö&–æF–æuöEövVæW&F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW"À¢vVæW&F–öâÀ¢¢W†6WBfÇVTW'&÷# ¢&WGW&âfÇ6P¢&WGW&â&ööÂ€¢6Æ–ÖVEö&–æF–ærævWB‚&WF†÷&—G•÷&ö÷Eö–B"’ÓÒ&ö÷Eö–@¢æB6Æ–ÖVEö&–æF–ærævWB‚'v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–@¢æB6Æ–ÖVEö&–æF–ærævWB‚&vVæW&F–öâ"’ÓÒvVæW&F–öà¢æB6Æ–ÒævWB‚&6Æ–ÕöWF†÷&—G•÷6W76–öåöf–ævW'&–çB"¢ÓÒ6Æ–ÖVEö&–æF–ærævWB‚'6W76–öåöf–ævW'&–çB"¢ ¢FVbö6öç7VÖVE÷&W—%÷7V66W76÷%öFW67&—F÷"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6¶vS¢F–7E·7G"Âç•ÒÀ¢W‡V7FVEö¶–æC¢7G"ÂæöæRÒæöæRÀ¢W‡V7FVE÷66÷Uö†6ƒ¢7G"ÂæöæRÒæöæRÀ¢’ÓâF–7E·7G"Âç•Ó ¢""%fÆ–FFRF†RW†7BtÂÆVgBgFW"6¶vR6öç7VÖRæB&Vf÷&R42à ¢6öç7VÖVB&W—"6¶vR—2æ÷&ÖÆÇ’FW&Ö–æÂâ—B&V6öÖW0¢&V6÷fW&&ÆRöæÇ’v†VâWfW'’GW&&ÆR&ö¦V7F–öâæÖW2F†R6ÖR7F–ÆÂÐ¢–æ6ö×ÆWFR7V66W76÷"G&ç67F–öâæB6æF–FFRâF†—2†VÇW"—0¢FVÆ–&W&FVÇ’&VBÖöæÇ“²F†R÷W&F–öâÖfVæ6VB&V6÷fW'’&VÆ÷rW&f÷&×0¢F†Rf÷'v&Bö6ö×Vç6F–öâw&—FW2à¢""  ¢v÷&µö÷&FW%ö–BÒ7G"‡v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’÷"""¢6¶vUö–BÒ7G"‡6¶vRævWB‚'&W—%÷6¶vUö–B"’÷"""¢–b€¢æ÷B6¶vUö–@¢÷"æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡6¶vR¢÷"6¶vRævWB‚'&V6÷&Eö–B"’Ò6¶vUö–@¢÷"6¶vRævWB‚'&ö¦V7Eö–B"’Ò&ö¦V7Eö–@¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢&W—"6¶vR—2Vç6–væVB÷"†2F–ffW&VçB&–Ö'’–FVçF—G’ ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&–Ö×WF&ÆU÷&W—%÷6¶vR"Â6¶vP¢¢6öç7V×F–öâÒ6¶vRævWB‚&6öç7V×F–öåö6Æ–Ò"¢–b€¢6¶vRævWB‚'7FGW2"’Ò&6öç7VÖVB ¢÷"6¶vRævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"æ÷B—6–ç7Fæ6R†6öç7V×F–öâÂF–7B¢÷"6öç7V×F–öâævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"¢Òv÷&µö÷&FW%ö–@¢÷"6¶vRævWB‚&6Æ–ÕövVæW&F–öâ"¢Ò6öç7V×F–öâævWB‚&6Æ–ÕövVæW&F–öâ"¢÷"æ÷B6VÆbåö6Æ–Õö—5ö7W'&VçEöWF†÷&—G•öæ6W7F÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6Æ–ÓÖ6öç7V×F–öâÀ¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6öç7VÖVB6Æ–ÒWF†÷&—G’F–ffW'2 ¢ ¢&6Uö–BÒ7G"‡6¶vRævWB‚&&6Uö'F–f7Eö–B"’÷"""¢7V66W76÷%ö–BÒ7G"‡6¶vRævWB‚'7V66W76÷%ö'F–f7Eö–B"’÷"""¢7V66W76÷%ö†6‚Ò7G"€¢6¶vRævWB‚'7V66W76÷%ö6æöæ–6Å÷6†#Sb"’÷"" ¢¢†æFöfeö–BÒ7G"€¢6¶vRævWB‚'&W—%÷FõöVF—Eö†æFöfeö–B"’÷"" ¢¢–bæ÷BÆÂ€¢‡6¶vUö–BÂ&6Uö–BÂ7V66W76÷%ö–BÂ7V66W76÷%ö†6‚Â†æFöfeö–B¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6öç7VÖVB6¶vR–FVçF—G’—2–æ6ö×ÆWFR ¢ ¢6÷W&6Uö6W'F–f–6FUö–BÒ7G"€¢6¶vRævWB‚&VF—Eö6W'F–f–6FUö–B"’÷"" ¢¢6÷W&6Uö6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–C×6÷W&6Uö6W'F–f–6FUö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡6÷W&6Uö6W'F–f–6FR“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6÷W&6RVF—B6W'F–f–6FR—2Vç6–væVB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbåöÆVFvW%÷66†VÖÀ¢&–æFWVæFVçEöVF—Eö6W'F–f–6FR"À¢6÷W&6Uö6W'F–f–6FRÀ¢¢–b€¢6÷W&6Uö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢Ò6÷W&6Uö6W'F–f–6FUö–@¢÷"6÷W&6Uö6W'F–f–6FRævWB‚&VF—Eö6Æ72"¢ÒTD•Eô4Ä55ô”äDUTäDTå@¢÷"6÷W&6Uö6W'F–f–6FRævWB‚'&W7VÇB"’Ò&f–Åö7W'&VçB ¢÷"6÷W&6Uö6W'F–f–6FRævWB‚&WfVçEö–B"¢Ò6¶vRævWB‚&VF—EöWfVçEö–B"¢÷"6÷W&6Uö6W'F–f–6FRævWB‚&'F–f7Eö–B"’Ò&6Uö–@¢÷"6÷W&6Uö6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"¢Ò6¶vRævWB‚&&6Uö6æöæ–6Å÷6†#Sb"¢÷"6÷W&6Uö6W'F–f–6FRævWB‚'&W÷'E÷6†#Sb"¢Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2‡6÷W&6Uö6W'F–f–6FRævWB‚'&W÷'B"’÷"·Ò¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6÷W&6RVF—B6W'F–f–6FRF–ffW'2g&öÒ6¶vR ¢ ¢&6RÂö&6U÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ&6Uö–@¢¢&6UöÖWFFFÒ&6RævWB‚&ÖWFFF"¢G&ç67F–öâÒ€¢&6UöÖWFFFævWB‚'7V66W76÷%÷G&ç67F–öâ"¢–b—6–ç7Fæ6R†&6UöÖWFFFÂF–7B¢VÇ6RæöæP¢¢G&ç67F–öåö–BÒ6VÆbç7F÷&Rå÷7V66W76÷%÷G&ç67F–öåö–B‡G&ç67F–öâ¢G&ç67F–öå÷7FFRÒ€¢7G"‡G&ç67F–öâævWB‚'7FFR"’÷"""¢–b—6–ç7Fæ6R‡G&ç67F–öâÂF–7B¢VÇ6R" ¢¢7V66W76÷%ö¶–æBÒ€¢7G"‡G&ç67F–öâævWB‚'7V66W76÷%ö¶–æB"’÷"""¢–b—6–ç7Fæ6R‡G&ç67F–öâÂF–7B¢VÇ6R" ¢¢66÷Uö†6‚Ò€¢7G"‡G&ç67F–öâævWB‚'66÷Uö†6‚"’÷"""¢–b—6–ç7Fæ6R‡G&ç67F–öâÂF–7B¢VÇ6R" ¢¢–b€¢G&ç67F–öå÷7FFRæ÷B–â²'&W&VB"Â&&÷'FVB"Â&6öÖÖ—GFVB'Ð¢÷"7V66W76÷%ö¶–æBæ÷B–â²'F6‚"Â'&W—"'Ð¢÷"æ÷BG&ç67F–öåö–@¢÷"æ÷B66÷Uö†6€¢÷"G&ç67F–öâævWB‚&&6Uö'F–f7Eö–B"’Ò&6Uö–@¢÷"G&ç67F–öâævWB‚&W‡V7FVEö6æöæ–6Åö†6‚"¢Ò6¶vRævWB‚&&6Uö6æöæ–6Å÷6†#Sb"¢÷"€¢W‡V7FVEö¶–æB—2æ÷BæöæP¢æB7V66W76÷%ö¶–æBÒW‡V7FVEö¶–æ@¢¢÷"€¢W‡V7FVE÷66÷Uö†6‚—2æ÷BæöæP¢æB66÷Uö†6‚ÒW‡V7FVE÷66÷Uö†6€¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢&6RG&ç67F–öâF–ffW'2 ¢ ¢7V66W76÷"Â÷7V66W76÷%÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7V66W76÷%ö–BÀ¢–æ6ÇVFU÷Væ6öÖÖ—GFVCÕG'VRÀ¢¢†æFöfbÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB††æFöfb“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢7V66W76÷"†æFöfb—2Vç6–væVB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöf`¢¢6æöæ–6Åö–BÒ7G"††æFöfbævWB‚&6æöæ–6Åö'F–f7Eö–B"’÷"""¢6æöæ–6ÂÂö6æöæ–6Å÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6æöæ–6Åö–BÀ¢–æ6ÇVFU÷Væ6öÖÖ—GFVCÕG'VRÀ¢¢÷WGWEö6W'F–f–6FUö–BÒ7G"††æFöfbævWB‚&6W'F–f–6FUö–B"’÷"""¢÷WGWEö6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–CÖ÷WGWEö6W'F–f–6FUö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†÷WGWEö6W'F–f–6FR“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢7V66W76÷"&öGV7F–öâ6W'F–f–6FR—2Vç6–væVB ¢¢–b€¢÷WGWEö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢Ò÷WGWEö6W'F–f–6FUö–@¢÷"÷WGWEö6W'F–f–6FRævWB‚&6W'F–f–6FU÷G—R"¢ÒTD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…@¢÷"÷WGWEö6W'F–f–6FRævWB‚&VF—Eö6Æ72"¢ÒTD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…@¢÷"÷WGWEö6W'F–f–6FRævWB‚'v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"÷WGWEö6W'F–f–6FRævWB‚&'F–f7Eö–B"’Ò7V66W76÷%ö–@¢÷"÷WGWEö6W'F–f–6FRævWB‚&6æöæ–6Åö'F–f7Eö–B"¢Ò6æöæ–6Åö–@¢÷"÷WGWEö6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"¢Ò7V66W76÷%ö†6€¢÷"÷WGWEö6W'F–f–6FRævWB‚'&VfÆ–v‡E÷7FGW2"¢Ò$ôET5D”ôåõ$TdÄ”t…Eõ50¢÷"—6–ç7Fæ6R€¢÷WGWEö6W'F–f–6FRævWB‚'&VfÆ–v‡Eö—77VUö6÷VçB"’Â&ööÀ¢¢÷"æ÷B—6–ç7Fæ6R€¢÷WGWEö6W'F–f–6FRævWB‚'&VfÆ–v‡Eö—77VUö6÷VçB"’Â–ç@¢¢÷"÷WGWEö6W'F–f–6FRævWB‚'&VfÆ–v‡Eö—77VUö6÷VçB"’Ò ¢÷"÷WGWEö6W'F–f–6FRævWB‚'&W÷'E÷6†#Sb"¢Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2†÷WGWEö6W'F–f–6FRævWB‚'&W÷'B"’÷"·Ò¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢7V66W76÷"&öGV7F–öâ6W'F–f–6FRF–ffW'2 ¢¢6æF–FFW2Ò‡7V66W76÷"Â6æöæ–6Â¢ÆÆ÷vVE÷V&Æ–6F–öâÒ°¢'&W&VB#¢²'VæF–ær'ÒÀ¢&&÷'FVB#¢²'VæF–ær"Â&&÷'FVB"Â'&V¦V7FVB'ÒÀ¢&6öÖÖ—GFVB#¢²'VæF–ær"Â&6öÖÖ—GFVB'ÒÀ¢Õ·G&ç67F–öå÷7FFUÐ¢f÷"6æF–FFR–â6æF–FFW3 ¢ÖWFFFÒ6æF–FFRævWB‚&ÖWFFF"¢–b€¢6æF–FFRævWB‚'&ö¦V7Eö–B"’Ò&ö¦V7Eö–@¢÷"6æF–FFRævWB‚'v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"6æF–FFRævWB‚'6÷W&6Uö'F–f7Eö–B"’Ò&6Uö–@¢÷"æ÷B—6–ç7Fæ6R†ÖWFFFÂF–7B¢÷"ÖWFFFævWB‚'7V66W76÷%÷G&ç67F–öåö–B"¢ÒG&ç67F–öåö–@¢÷"ÖWFFFævWB‚'7V66W76÷%ö&6Uö'F–f7Eö–B"’Ò&6Uö–@¢÷"ÖWFFFævWB‚'7V66W76÷%ö¶–æB"’Ò7V66W76÷%ö¶–æ@¢÷"ÖWFFFævWB‚'7V66W76÷%÷66÷Uö†6‚"’Ò66÷Uö†6€¢÷"6VÆbç7F÷&Ræ'F–f7E÷V&Æ–6F–öå÷7FFR†6æF–FFR¢æ÷B–âÆÆ÷vVE÷V&Æ–6F–öà¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6æF–FFRFW67&—F÷"F–ffW'2 ¢¢–b€¢7V66W76÷"ævWB‚&'F–f7Eö–B"’Ò7V66W76÷%ö–@¢÷"7V66W76÷"ævWB‚&ÖWFFF"Â·Ò’ævWB‚&6æöæ–6Å÷6†#Sb"¢Ò7V66W76÷%ö†6€¢÷"†æFöfbævWB‚&†æFöfe÷G—R"’Ò'&W—%÷FõöVF—B ¢÷"†æFöfbævWB‚'v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"†æFöfbævWB‚&–Ö×WF&ÆU÷&W—%÷6¶vUö–B"’Ò6¶vUö–@¢÷"†æFöfbævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"’Ò7V66W76÷%ö–@¢÷"†æFöfbævWB‚&6æöæ–6Åö'F–f7Eö–B"’Ò6æöæ–6Åö–@¢÷"†æFöfbævWB‚&6æöæ–6Å÷6†#Sb"’Ò7V66W76÷%ö†6€¢÷"†æFöfbævWB‚'7V66W76÷%÷G&ç67F–öåö–B"’ÒG&ç67F–öåö–@¢÷"†æFöfbævWB‚'7V66W76÷%ö&6Uö'F–f7Eö–B"’Ò&6Uö–@¢÷"†æFöfbævWB‚'7V66W76÷%ö¶–æB"’Ò7V66W76÷%ö¶–æ@¢÷"†æFöfbævWB‚'7V66W76÷%÷66÷Uö†6‚"’Ò66÷Uö†6€¢÷"†æFöfbævWB‚'V&Æ–6F–öå÷7FFR"¢æ÷B–âÆÆ÷vVE÷V&Æ–6F–öà¢÷"æ÷B€¢†æFöfbævWB‚'7FGW2"’ÓÒ'&W&VB ¢÷"€¢G&ç67F–öå÷7FFRÓÒ&&÷'FVB ¢æB†æFöfbævWB‚'7FGW2"’ÓÒ'&V¦V7FVB ¢æB†æFöfbævWB‚'V&Æ–6F–öå÷7FFR"’ÓÒ&&÷'FVB ¢¢¢÷"ç’€¢†æFöfbævWB†f–VÆB¢f÷"f–VÆB–â€¢&6Æ–ÖVEö'•÷v÷&µö÷&FW%ö–B"À¢&6Æ–Õ÷Fö¶Vâ"À¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"À¢&6öç7V×F–öåö6Æ–Ò"À¢&VF—Eö6W'F–f–6FUö–B"À¢¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6¶vRÂ'F–f7BÂ÷"†æFöfbF–ffW'2 ¢¢–bG&ç67F–öå÷7FFRÓÒ&6öÖÖ—GFVB"æB€¢&6UöÖWFFFævWB‚'7V66W76÷%ö'F–f7Eö–B"’Ò7V66W76÷%ö–@¢÷"G&ç67F–öâævWB‚'7V66W76÷%ö'F–f7Eö–B"’Ò7V66W76÷%ö–@¢÷"&6UöÖWFFFævWB‚'7V66W76÷%ö6æöæ–6Åö†6‚"¢Ò7V66W76÷%ö†6€¢÷"G&ç67F–öâævWB‚'7V66W76÷%ö6æöæ–6Åö†6‚"¢Ò7V66W76÷%ö†6€¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢6öÖÖ—GFVB&6Rö–çFW"F–ffW'2 ¢ ¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢‡V%öÖF6†W2Ò°¢&÷p¢f÷"&÷r–â‡V"ævWB‚&†æFöfg2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfeö–@¢Ð¢–bÆVâ†‡V%öÖF6†W2’Ò÷"ç’€¢‡V%öÖF6†W5³ÒævWB†¶W’’Ò†æFöfbævWB†¶W’¢f÷"¶W’–â€¢'v÷&µö÷&FW%ö–B"À¢'7F÷'–&ö&Eö'F–f7Eö–B"À¢&6æöæ–6Åö'F–f7Eö–B"À¢'7V66W76÷%÷G&ç67F–öåö–B"À¢'7V66W76÷%ö&6Uö'F–f7Eö–B"À¢'7V66W76÷%ö¶–æB"À¢'7V66W76÷%÷66÷Uö†6‚"À¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö–çfÆ–C¢‡V"6æF–FFR&ö¦V7F–öâF–ffW'2 ¢¢&WGW&â°¢&&6R#¢&6RÀ¢&&6Uö'F–f7Eö–B#¢&6Uö–BÀ¢'G&ç67F–öâ#¢G&ç67F–öâÀ¢'G&ç67F–öåö–B#¢G&ç67F–öåö–BÀ¢'G&ç67F–öå÷7FFR#¢G&ç67F–öå÷7FFRÀ¢'7V66W76÷%ö¶–æB#¢7V66W76÷%ö¶–æBÀ¢'66÷Uö†6‚#¢66÷Uö†6‚À¢'7V66W76÷"#¢7V66W76÷"À¢'7V66W76÷%ö'F–f7Eö–B#¢7V66W76÷%ö–BÀ¢'7V66W76÷%ö6æöæ–6Å÷6†#Sb#¢7V66W76÷%ö†6‚À¢&6æöæ–6Â#¢6æöæ–6ÂÀ¢&6æöæ–6Åö'F–f7Eö–B#¢6æöæ–6Åö–BÀ¢&†æFöfb#¢†æFöfbÀ¢&†æFöfeö–B#¢†æFöfeö–BÀ¢'6÷W&6Uö6W'F–f–6FR#¢6÷W&6Uö6W'F–f–6FRÀ¢&÷WGWEö6W'F–f–6FR#¢÷WGWEö6W'F–f–6FRÀ¢&6öç7V×F–öåö6Æ–Ò#¢6öç7V×F–öâÀ¢Ð ¢FVb÷&V6÷fW%ö6öç7VÖVE÷&W—%÷7V66W76÷"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6¶vS¢F–7E·7G"Âç•ÒÀ¢W‡V7FVEö¶–æC¢7G"À¢W‡V7FVE÷66÷Uö†6ƒ¢7G"À¢’ÓâFööÅ&W7öç6RÂæöæS ¢FW67&—F÷"Ò6VÆbåö6öç7VÖVE÷&W—%÷7V66W76÷%öFW67&—F÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6¶vS×6¶vRÀ¢W‡V7FVEö¶–æCÖW‡V7FVEö¶–æBÀ¢W‡V7FVE÷66÷Uö†6ƒÖW‡V7FVE÷66÷Uö†6‚À¢¢7FFRÒ7G"†FW67&—F÷%²'G&ç67F–öå÷7FFR%Ò¢–b7FFRÓÒ&&÷'FVB# ¢2â&÷'FVB&6RG&ç67F–öâ—2âW‡Æ–6—BæVvF—fR42&W7VÇBà¢2æWfW"&W7W'&V7B—BÂWfVâv†Vâ—G26æF–FFR—27F–ÆÂVæF–æs ¢2&V¦V7BF†RW†7BVæ6Æ–ÖVB6æF–FFR—"Â&VÆV6RöæÇ’F†P¢26¶vRw26–væVBf—†VB–FVçF—G’ÂæB&WV—&Rg&W6‚&W—"v÷&°¢2÷&FW"†æBF†W&Vf÷&Rg&W6‚ÆVFvW"VF—B7–6ÆR’à¢6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×7G"‡v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢'F–f7Eö–C×7G"†FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ò’À¢6æöæ–6Åö'F–f7Eö–C×7G"€¢FW67&—F÷%²&6æöæ–6Åö'F–f7Eö–B%Ð¢’À¢†æFöfeö–C×7G"†FW67&—F÷%²&†æFöfeö–B%Ò’À¢G&ç67F–öåö–C×7G"†FW67&—F÷%²'G&ç67F–öåö–B%Ò’À¢7FFSÒ&&÷'FVB"À¢&V6öãÒ'&V6÷fW"&÷'FVB6öç7VÖVB&W—"7V66W76÷""À¢¢6Æ–ÒÒFW67&—F÷%²&6öç7V×F–öåö6Æ–Ò%Ð¢&W7F÷&VBÒ6VÆbç7F÷&Rç&öÆÆ&6µö6öç7VÖVE÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡6¶vU²'&W—%÷6¶vUö–B%Ò’À¢v÷&µö÷&FW%ö–C×7G"‡v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢6Æ–Õ÷Fö¶Vã×7G"†6Æ–ÒævWB‚&6Æ–Õ÷Fö¶Vâ"’÷"""’À¢6Æ–ÕövVæW&F–öãÖ–çB†6Æ–ÒævWB‚&6Æ–ÕövVæW&F–öâ"’÷"’À¢&6Uö'F–f7Eö–C×7G"†FW67&—F÷%²&&6Uö'F–f7Eö–B%Ò’À¢G&ç67F–öåö–C×7G"†FW67&—F÷%²'G&ç67F–öåö–B%Ò’À¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–C×7G"€¢FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ð¢’À¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#Sc×7G"€¢FW67&—F÷%²'7V66W76÷%ö6æöæ–6Å÷6†#Sb%Ð¢’À¢&W—%÷FõöVF—Eö†æFöfeö–C×7G"†FW67&—F÷%²&†æFöfeö–B%Ò’À¢¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"€¢æÖW76RÂ&ö¦V7Eö–BÂ&W7F÷&V@¢¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö6ö×Vç6FVC¢&V¦V7FVB6æF–FFRv2&VÆV6VC²7F'BæWr&W—"v÷&²÷&FW" ¢ ¢G&ç67F–öâÒFW67&—F÷%²'G&ç67F–öâ%Ð¢–b7FFRÓÒ'&W&VB# ¢2&WW6–ærF†RW†7BG&ç67F–öâ–B&VæWw2âW‡—&VB7V66W76÷ ¢2ÆV6R'WB6ææ÷B6†ævR¶–æBÂ&6RÂ&r†6‚Â÷"66÷Rà¢6VÆbç7F÷&Rç&W&U÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†FW67&—F÷%²&&6Uö'F–f7Eö–B%Ò’À¢W‡V7FVEö6æöæ–6Åö†6ƒ×7G"€¢G&ç67F–öå²&W‡V7FVEö6æöæ–6Åö†6‚%Ð¢’À¢W‡V7FVE÷&uöf–ævW'&–çC×G&ç67F–öâævWB€¢&W‡V7FVE÷&uöf–ævW'&–çB ¢’À¢G&ç67F–öåö–C×7G"†FW67&—F÷%²'G&ç67F–öåö–B%Ò’À¢7V66W76÷%ö¶–æCÖW‡V7FVEö¶–æBÀ¢66÷Uö†6ƒÖW‡V7FVE÷66÷Uö†6‚À¢¢6VÆbç7F÷&Ræ6öÖÖ—E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†FW67&—F÷%²&&6Uö'F–f7Eö–B%Ò’À¢G&ç67F–öåö–C×7G"†FW67&—F÷%²'G&ç67F–öåö–B%Ò’À¢7V66W76÷%ö'F–f7Eö–C×7G"€¢FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ð¢’À¢7V66W76÷%ö6æöæ–6Åö†6ƒ×7G"€¢FW67&—F÷%²'7V66W76÷%ö6æöæ–6Å÷6†#Sb%Ð¢’À¢¢V&Æ–6F–öâÒ6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×7G"‡v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢'F–f7Eö–C×7G"†FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ò’À¢6æöæ–6Åö'F–f7Eö–C×7G"€¢FW67&—F÷%²&6æöæ–6Åö'F–f7Eö–B%Ð¢’À¢†æFöfeö–C×7G"†FW67&—F÷%²&†æFöfeö–B%Ò’À¢G&ç67F–öåö–C×7G"†FW67&—F÷%²'G&ç67F–öåö–B%Ò’À¢7FFSÒ&6öÖÖ—GFVB"À¢¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"†æÖW76RÂ&ö¦V7Eö–BÂ6¶vR¢'F–f7BÒV&Æ–6F–öå²&'F–f7G2%Õ°¢7G"†FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ò¢Ð¢†æFöfbÒV&Æ–6F–öå²&†æFöfb%Ð¢6W'F–f–6FRÒFW67&—F÷%²&÷WGWEö6W'F–f–6FR%Ð¢&WGW&âFööÅ&W7öç6R€¢7FGW3Õ$ôET5D”ôåõ$TdÄ”t…Eõ52À¢7VÖÖ'“Ò€¢.KúîZHÞK¨¾Xª[{.{+îzî{ºÞKÙÎûÙÎXZŽŠŽyIþKª~š(Nj8˜	®‹ø~ûÙÎ[è^xºÎz¸¾{¸ŽZê ¢’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×7G"‡v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢'F–f7Eö–C×7G"†FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ò’À¢F÷væÆöE÷W&Ã×6VÆbç7F÷&Rç6–væVEöF÷væÆöE÷W&Â€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†FW67&—F÷%²'7V66W76÷%ö'F–f7Eö–B%Ò’À¢7G"†'F–f7E²&f–ÆUöæÖR%Ò’À¢’À¢G&6Uö–C×7G"††æFöfbævWB‚'7FvU÷G&6Uö–B"’÷"""’À¢FF×°¢&'F–f7B#¢'F–f7BÀ¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢FW67&—F÷%°¢&6æöæ–6Åö'F–f7Eö–B ¢ÒÀ¢&6æöæ–6Å÷6†#Sb#¢FW67&—F÷%°¢'7V66W76÷%ö6æöæ–6Å÷6†#Sb ¢ÒÀ¢&†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’††æFöfb’À¢&†æFöfeö'F–f7Eö–B#¢FW67&—F÷%²&†æFöfeö–B%ÒÀ¢&6W'F–f–6FR#¢6VÆbåö6W'F–f–6FU÷7VÖÖ'’†6W'F–f–6FR’À¢'&W÷'B#¢6÷’æFVW6÷’†6W'F–f–6FRævWB‚'&W÷'B"’÷"·Ò’À¢'&W—%÷6¶vUö–B#¢6¶vRævWB‚'&W—%÷6¶vUö–B"’À¢'&W—%÷6¶vR#¢6÷’æFVW6÷’‡6¶vR’À¢'&W—%÷G&ç67F–öåö–B#¢FW67&—F÷%²'G&ç67F–öåö–B%ÒÀ¢'&W—%÷G&ç67F–öå÷7FFR#¢&6öÖÖ—GFVB"À¢'&V6÷fW&VEö6öç7VÖVE÷7V66W76÷"#¢G'VRÀ¢&–FV×÷FVçE÷&WÆ’#¢7FFRÓÒ&6öÖÖ—GFVB"À¢&æW‡Eö7F–öâ#¢&–æFWVæFVçEöVF—B"À¢ÒÀ¢ ¢FVb÷&W—%÷6¶vUöf÷%÷F6‚€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6W'F–f–6FS¢F–7E·7G"Âç•ÒÀ¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢&6Uö'F–f7Eö–C¢7G"À¢&6Uö6æöæ–6Åö†6ƒ¢7G"À¢7W'&VçEöf–ævW'&–çG3¢F–7E·7G"Â7G%ÒÀ¢’ÓâGWÆU¶F–7E·7G"Âç•ÒÂF–7E·7G"Âç•ÕÓ ¢""$ÆöBæBfW&–g’F†R6W'fW"Ö—77VVBWF†÷&—¦F–öâf÷"öæR&W—"à ¢F†R6W'F–f–6FR–B—2öæÇ’â–æFW‚–çFòF†R6–væVB6¶vR7F÷&Râ—@¢—2æWfW"—G6VÆbG&VFVB2&W—"WF†÷&—¦F–öâà¢""  ¢6¶vRÒ6VÆbç7F÷&Ræf–æE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢f–VÆCÒ&VF—Eö6W'F–f–6FUö–B"À¢fÇVSÖ6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢¢–b6¶vR—2æöæR÷"æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡6¶vR“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷&WV—&VC¢6–væVB–Ö×WF&ÆR6¶vR—2Ö—76–ær÷"F×W&VB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&–Ö×WF&ÆU÷&W—%÷6¶vR"Â6¶vP¢¢6¶vU÷7FGW2Ò7G"‡6¶vRævWB‚'7FGW2"’÷"""¢–b6¶vU÷7FGW2ÓÒ&6öç7VÖ–ær# ¢G'“ ¢ÆV6UöW‡—&W2ÒfÆöB€¢6¶vRævWB‚&6Æ–ÕöÆV6UöW‡—&W5öWö6…÷6V6öæG2"’÷" ¢¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢ÆV6UöW‡—&W2Ò ¢–b€¢6¶vRævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"¢Òv÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"¢÷"ÆV6UöW‡—&W2ÃÒ ¢÷"ÆV6UöW‡—&W2âF–ÖRçF–ÖR‚¢÷"æ÷B6VÆbåö6Æ–Õö—5ö7W'&VçEöWF†÷&—G•öæ6W7F÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6Æ–Ó×6¶vRÀ¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷Væf–Æ&ÆS¢6¶vR†2æò&V6÷fW&&ÆRW‡—&VB6Æ–Ò ¢¢VÆ–b6¶vU÷7FGW2ÓÒ&6öç7VÖVB# ¢–b6¶vRævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW"ævWB€¢'v÷&µö÷&FW%ö–B ¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷Væf–Æ&ÆS¢6¶vR—2Ç&VG’6öç7VÖVB ¢¢6VÆbåö6öç7VÖVE÷&W—%÷7V66W76÷%öFW67&—F÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6¶vS×6¶vRÀ¢¢VÆ–b6¶vU÷7FGW2Ò&—77VVB# ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vU÷Væf–Æ&ÆS¢6¶vR—2æ÷B—77VVB"¢VÆ–bç’€¢—6–ç7Fæ6R†—FVÒÂF–7B¢æB—FVÒævWB‚'7FFR"¢ÓÒ'&öÆÆVEö&6µögFW%÷7V66W76÷%ö65öf–ÇW&R ¢æB—FVÒævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"¢ÓÒv÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"¢f÷"—FVÒ–â6¶vRævWB‚&6Æ–Õö†—7F÷'’"ÂµÒ¢“ ¢2W†7B6ö×Vç6F–öâ†2Ç&VG’&V¦V7FVB&öGV7F–öâ6æF–FFP¢2f÷"F†—2v÷&²÷&FW"â&WG'––ærVæFW"F†R6ÖRv÷&²÷&FW"v÷VÆ@¢2VæB6V6öæB6æF–FFRFò—G2ÆVFvW"VF—B7–6ÆRà¢&—6RfÇVTW'&÷"€¢'&W—%÷7V66W76÷%÷&V6÷fW'•ö6ö×Vç6FVC¢7F'BæWr&W—"v÷&²÷&FW" ¢¢G'“ ¢W‡—&W5öBÒFFWF–ÖRæg&öÖ—6öf÷&ÖB€¢7G"‡6¶vU²&W‡—&W5öB%Ò’ç&WÆ6R‚%¢"Â"³£"¢¢–bW‡—&W5öBçG¦–æfò—2æöæS ¢W‡—&W5öBÒW‡—&W5öBç&WÆ6R‡G¦–æfó×F–ÖW¦öæRçWF2¢W†6WB„¶W”W'&÷"ÂG—TW'&÷"ÂfÇVTW'&÷"’2W†3 ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vUö–çfÆ–C¢W‡—'’—2ÖÆf÷&ÖVB"’g&öÒW†0¢–b€¢6¶vU÷7FGW2Ò&6öç7VÖVB ¢æBW‡—&W5öBÃÒFFWF–ÖRææ÷r‡F–ÖW¦öæRçWF2¢“ ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vUöW‡—&VC¢–Ö×WF&ÆR6¶vR†2W‡—&VB"¢–b€¢6¶vRævWB‚&&6Uö'F–f7Eö–B"’Ò&6Uö'F–f7Eö–@¢÷"6¶vRævWB‚&&6Uö6æöæ–6Å÷6†#Sb"’Ò&6Uö6æöæ–6Åö†6€¢÷"6¶vRævWB‚&VF—EöWfVçEö–B"’Ò6W'F–f–6FRævWB‚&WfVçEö–B"¢÷"6¶vRævWB‚&f–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG0¢÷"6¶vRævWB‚'F÷öÆöw•öf–ævW'&–çB"’Ò6VÆbå÷F÷öÆöw•öf–ævW'&–çB‡7F÷'–&ö&B¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢&6R'F–f7BÂWfVçBÂf–ævW'&–çG2Â÷"F÷öÆöw’6†ævVB ¢ ¢†æFöfeö–BÒ7G"‡6¶vRævWB‚&VF—E÷Fõ÷&W—%ö†æFöfeö–B"’÷"""¢G'“ ¢†æFöfbÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B6VÆbåö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†2À¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢“ ¢&—6P¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢VF—E÷Fõ÷&W—"†æFöfb—2Ö—76–ær ¢’g&öÒW†0¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB††æFöfb“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢VF—E÷Fõ÷&W—"†æFöfb—2Vç6–væVB÷"F×W&VB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöf`¢¢&÷FV7FVE÷66÷RÒ†æFöfbævWB‚'66÷R"Â·Ò’ævWB‚'&÷FV7FVE÷66÷R"ÂµÒ¢–b€¢†æFöfbævWB‚&†æFöfe÷G—R"’Ò&VF—E÷Fõ÷&W—" ¢÷"†æFöfbævWB‚&–Ö×WF&ÆU÷&W—%÷6¶vUö–B"¢Ò6¶vRævWB‚'&W—%÷6¶vUö–B"¢÷"†æFöfbævWB‚&VF—Eö6W'F–f–6FUö–B"’Ò6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢÷"†æFöfbævWB‚&Æöv–6Åö'F–f7Eö–B"’Ò&6Uö'F–f7Eö–@¢÷"†æFöfbævWB‚&f–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG0¢÷"6¶vRævWB‚'&÷FV7FVE÷66÷Uö†6‚"¢Ò6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2‡&÷FV7FVE÷66÷R’¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢VF—B†æFöfbFöW2æ÷B&–æBF†RW†7B6¶vRæB'F–f7B ¢¢6VÆbåö76W'EöÆFW7E÷&W—%÷6¶vUöWF†÷&—G’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6¶vS×6¶vRÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢7W'&VçEöf–ævW'&–çG3Ö7W'&VçEöf–ævW'&–çG2À¢¢&WGW&â6¶vRÂ†æFöf` ¢FVb÷7–æ5÷&W—%÷6¶vUö–åö‡V"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢6¶vS¢F–7E·7G"Âç•ÒÀ¢’ÓâæöæS ¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢&÷w2Ò‡V"ç6WFFVfVÇB‚'&W—%÷6¶vW2"ÂµÒ¢6¶vUö–BÒ6¶vRævWB‚'&W—%÷6¶vUö–B"¢f÷"–æFW‚Â&÷r–âVçVÖW&FR‡&÷w2“ ¢–b&÷rævWB‚'&W—%÷6¶vUö–B"’ÓÒ6¶vUö–C ¢&÷w5¶–æFW…ÒÒ6÷’æFVW6÷’‡6¶vR¢'&V°¢VÇ6S ¢&÷w2æVæB†6÷’æFVW6÷’‡6¶vR’ ¢FVbö76W'E÷&W—%÷6W&F–öâ€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6¶vS¢F–7E·7G"Âç•ÒÀ¢VF—Eö†æFöfc¢F–7E·7G"Âç•ÒÀ¢’ÓâæöæS ¢–bv÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"’ÓÒ6¶vRævWB‚&VF—E÷v÷&µö÷&FW%ö–B"“ ¢&—6RfÇVTW'&÷"‚'&W—%÷v–æF÷u÷6W&F–öå÷&WV—&VC¢&W—"æBVF—Bv÷&²÷&FW'2×W7BF–ffW""¢&W—%ö&–æF–ærÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÂ&ö¦V7Eö–BÂv÷&µö÷&FW ¢¢&W—%÷v–æF÷rÒ&W—%ö&–æF–ærævWB‚'v–æF÷uö&–æF–æuö–B"¢&W—%ö7F÷"Ò&W—%ö&–æF–ærævWB‚&7F÷%ö&–æF–æuö–B"¢&W—%÷6W76–öâÒ&W—%ö&–æF–ærævWB‚'6W76–öåöf–ævW'&–çB"¢f÷"Æ&VÂÂv–æF÷rÂ7F÷"Â6W76–öâ–â€¢€¢'&öGV7F–öâ"À¢6¶vRævWB‚'&öGV7F–öå÷v–æF÷uö&–æF–æuö–B"’À¢6¶vRævWB‚'&öGV7F–öåö7F÷%ö&–æF–æuö–B"’À¢6¶vRævWB‚'&öGV7F–öå÷6W76–öåöf–ævW'&–çB"’À¢’À¢€¢&VF—B"À¢6¶vRævWB‚&VF—E÷v–æF÷uö&–æF–æuö–B"’÷"VF—Eö†æFöfbævWB‚&g&öÕ÷v–æF÷uö&–æF–æuö–B"’À¢6¶vRævWB‚&VF—Eö7F÷%ö&–æF–æuö–B"’÷"VF—Eö†æFöfbævWB‚&g&öÕö7F÷%ö&–æF–æuö–B"’À¢6¶vRævWB‚&VF—E÷6W76–öåöf–ævW'&–çB"’÷"VF—Eö†æFöfbævWB‚&g&öÕ÷6W76–öåöf–ævW'&–çB"’À¢’À¢“ ¢–b&W—%÷v–æF÷ræBv–æF÷ræB&W—%÷v–æF÷rÓÒv–æF÷s ¢&—6RfÇVTW'&÷"†b'&W—%÷v–æF÷u÷6W&F–öå÷&WV—&VC¢&W—"&WW6W2¶Æ&VÇÒv–æF÷r"¢–b&W—%ö7F÷"æB7F÷"æB&W—%ö7F÷"ÓÒ7F÷# ¢&—6RfÇVTW'&÷"†b'&W—%÷v–æF÷u÷6W&F–öå÷&WV—&VC¢&W—"&WW6W2¶Æ&VÇÒ7F÷""¢–b&W—%÷6W76–öâæB6W76–öâæB&W—%÷6W76–öâÓÒ6W76–öã ¢&—6RfÇVTW'&÷"†b'&W—%÷v–æF÷u÷6W&F–öå÷&WV—&VC¢&W—"&WW6W2¶Æ&VÇÒ6W76–öâ" ¢FVbögVÆÅ÷&W—%öWF†÷&—¦F–öâ€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢v÷&µö÷&FW#¢F–7E·7G"Âç•ÒÀ¢6÷W&6Uö'F–f7Eö–C¢7G"ÂæöæRÀ¢&W—%÷6¶vUö–C¢7G"À¢’ÓâF–7E·7G"Âç•Ó ¢""%&W6öÇfRæBfW&–g’F†R6ö×ÆWFRÂ6W'fW"×6–væVB7G'V7GW&Â×&W—"6†–âà ¢6¶vR–B—2öæÇ’â–æFW‚âWF†÷&—¦F–öâ6öÖW2g&öÒF†RW†7@¢–Ö×WF&ÆR6¶vRÂ–æFWVæFVçBÖVF—B6W'F–f–6FRÂ&öGV7F–öâ†æFöfbÀ¢VF—B×Fò×&W—"†æFöfbÂ÷&–v–æÂ'F–f7B'—FW2ÂæBF—7F–æ7B&W— ¢WF†÷&—G’ÆÂw&VV–ær&Vf÷&RF†R6¶vR—26Æ–ÖVBà¢""  ¢–bæ÷B6÷W&6Uö'F–f7Eö–C ¢&—6RfÇVTW'&÷"€¢'&W—%ö&6U÷&WV—&VC¢7G'V7GW&Â&W—"×W7BæÖRF†RW†7BVF—FVB&6R'F–f7B ¢¢G'“ ¢W†7E÷6¶vRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×&W—%÷6¶vUö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B6VÆbåö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†2À¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×&W—%÷6¶vUö–BÀ¢“ ¢&—6P¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷&WV—&VC¢6–væVB–Ö×WF&ÆR6¶vRFöW2æ÷BW†—7B–âF†—2&ö¦V7B ¢’g&öÒW†0¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†W†7E÷6¶vR“ ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6¶vR—2Vç6–væVB÷"F×W&VB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&–Ö×WF&ÆU÷&W—%÷6¶vR"ÂW†7E÷6¶vP¢¢–bW†7E÷6¶vRævWB‚'&W—%÷6¶vUö–B"’Ò&W—%÷6¶vUö–C ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6¶vR–FVçF—G’F–ffW'2" ¢6W'F–f–6FUö–BÒ7G"†W†7E÷6¶vRævWB‚&VF—Eö6W'F–f–6FUö–B"’÷"""¢G'“ ¢6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–CÖ6W'F–f–6FUö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B6VÆbåö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†2À¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–CÖ6W'F–f–6FUö–BÀ¢“ ¢&—6P¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢6¶vRÖ&÷VæB6W'F–f–6FR—2Ö—76–ær ¢’g&öÒW†0¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†6W'F–f–6FR“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR—2Vç6–væVB÷"F×W&VB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbåöÆVFvW%÷66†VÖÂ&–æFWVæFVçEöVF—Eö6W'F–f–6FR"Â6W'F–f–6FP¢¢–b€¢6W'F–f–6FRævWB‚'&ö¦V7Eö–B"’Ò&ö¦V7Eö–@¢÷"6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"’Ò6W'F–f–6FUö–@¢÷"6W'F–f–6FRævWB‚&VF—Eö6Æ72"’ÒTD•Eô4Ä55ô”äDUTäDTå@¢÷"6W'F–f–6FRævWB‚&WfVçEö–B"’ÒW†7E÷6¶vRævWB‚&VF—EöWfVçEö–B"¢“ ¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR–FVçF—G’Â6Æ72Â÷"VF—BWfVçBF–ffW'2 ¢¢6W'F–f–6FU÷&W÷'BÒ6W'F–f–6FRævWB‚'&W÷'B"¢–b€¢æ÷B—6–ç7Fæ6R†6W'F–f–6FU÷&W÷'BÂF–7B¢÷"æ÷B—6–ç7Fæ6R†6W'F–f–6FU÷&W÷'BævWB‚&—77VW2"’ÂÆ—7B¢÷"6W'F–f–6FRævWB‚'&W÷'E÷6†#Sb"¢Ò6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2†6W'F–f–6FU÷&W÷'B’¢“ ¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR&W÷'B—2Ö—76–ær÷"f–ævW'&–çBF–ffW'2 ¢¢–bæ÷B6W'F–f–6FU÷&W÷'BævWB‚&—77VW2"“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6ÆVâVF—B6ææ÷BWF†÷&—¦R&W—" ¢¢–bæ÷B6VÆbå÷fÆ–Eö66†Uö–FVçF—G’†6W'F–f–6FRÂTD•Eô4Ä55ô”äDUTäDTåB“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR66†R–FVçF—G’—2–çfÆ–B" ¢&6U÷7F÷'–&ö&BÂ&6U÷&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ6÷W&6Uö'F–f7Eö–@¢¢&6Uö6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb†&6U÷7F÷'–&ö&B¢&6U÷&uöf–ævW'&–çBÒ7G"†&6U÷&V6÷&BævWB‚&f–ævW'&–çB"’÷"""¢–b€¢W†7E÷6¶vRævWB‚&&6Uö'F–f7Eö–B"’Ò6÷W&6Uö'F–f7Eö–@¢÷"W†7E÷6¶vRævWB‚&&6Uö6æöæ–6Å÷6†#Sb"’Ò&6Uö6æöæ–6Åö†6€¢÷"6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"’Ò&6Uö6æöæ–6Åö†6€¢÷"6÷W&6Uö'F–f7Eö–@¢æ÷B–â°¢6W'F–f–6FRævWB‚&'F–f7Eö–B"’À¢6W'F–f–6FRævWB‚&6æöæ–6Åö'F–f7Eö–B"’À¢Ð¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6¶vRæB6W'F–f–6FRFòæ÷B&–æBF†RW†7B&6R6æöæ–6Â ¢ ¢6÷W&6Uöf–ævW'&–çBÒ6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6U÷7F÷'–&ö&BÀ¢&6U÷&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"’À¢&6U÷&V6÷&BÀ¢¢&÷VæF'•ö†6‚Ò6VÆbåö&÷VæF'•ö†6‚†&6U÷7F÷'–&ö&B¢7W'&VçEöf–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢&6U÷7F÷'–&ö&BÀ¢&6U÷&V6÷&BÀ¢6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢¢–bW†7E÷6¶vRævWB‚&f–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG3 ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢÷&–v–æÂ&6Rf–ævW'&–çG26†ævVB ¢¢6W'F–f–6FU÷&rÒ7G"€¢6W'F–f–6FRævWB‚&f–ævW'&–çG2"Â·Ò’ævWB‚&'F–f7E÷&r"¢÷"6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"Â·Ò’ævWB‚&'F–f7B"¢÷"" ¢¢–bæ÷B&6U÷&uöf–ævW'&–çB÷"6W'F–f–6FU÷&rÒ&6U÷&uöf–ævW'&–çC ¢&—6RfÇVTW'&÷"€¢&'F–f7E÷&uöf–ævW'&–çEöÖ—6ÖF6ƒ¢VF—FVB&6R'—FW26†ævVB ¢¢6W'F–f–6FUöf–ævW'&–çEöÖÒ°¢'6÷W&6R#¢'6÷W&6Uö'—FW2"À¢&&÷VæF'’#¢&W—6öFUö&÷VæF'’"À¢&'F–f7E÷&r#¢&'F–f7E÷&r"À¢&'F–f7E÷6VÖçF–2#¢&'F–f7E÷6VÖçF–2"À¢&6æöæ–6Â#¢&6æöæ–6Â"À¢''6W"#¢''6W""À¢'66†VÖ#¢'66†VÖ"À¢''VçF–ÖR#¢''VçF–ÖR"À¢''VÆW2#¢''VÆW2"À¢'fÆ–FF÷"#¢'fÆ–FF÷""À¢'&ö¦V7Eöæ6†÷'2#¢'&ö¦V7Eöæ6†÷'2"À¢'F&vWEöÖöFVÅö6öçG&7B#¢'F&vWEöÖöFVÅö6öçG&7B"À¢Ð¢6W'F–f–6FUöf–ævW'&–çG2Ò6W'F–f–6FRævWB‚&f–ævW'&–çG2"¢–bæ÷B—6–ç7Fæ6R†6W'F–f–6FUöf–ævW'&–çG2ÂF–7B’÷"ç’€¢6W'F–f–6FUöf–ævW'&–çG2ævWB†6W'F–f–6FUö¶W’¢Ò7W'&VçEöf–ævW'&–çG2ævWB†'VæFÆUö¶W’¢f÷"6W'F–f–6FUö¶W’Â'VæFÆUö¶W’–â6W'F–f–6FUöf–ævW'&–çEöÖæ—FV×2‚¢“ ¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FRf–ævW'&–çG2Fòæ÷BÖF6‚F†R÷&–v–æÂ&6R ¢¢7W'&VçEö66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢'F–f7Eöf–ævW'&–çCÖ&6U÷&uöf–ævW'&–çBÀ¢7F÷'–&ö&CÖ&6U÷7F÷'–&ö&BÀ¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"Â·Ò’ævWB€¢''VçF–ÖU÷FW7Eö6W'F–f–6FUö–B ¢¢÷"" ¢’À¢¢7W'&VçEö66†Uö6ö×öæVçG5²'66÷R%ÒÒ7G"€¢6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"Â·Ò’ævWB‚'66÷R"’÷"" ¢¢–b6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"’Ò7W'&VçEö66†Uö6ö×öæVçG3 ¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢6÷W&6RÂ&÷VæF'’Â'VÆW2Â÷"fÆ–FF÷"6†ævVBgFW"VF—B ¢ ¢6¶vRÂVF—Eö†æFöfbÒ6VÆbå÷&W—%÷6¶vUöf÷%÷F6‚€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢7F÷'–&ö&CÖ&6U÷7F÷'–&ö&BÀ¢&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢&6Uö6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢7W'&VçEöf–ævW'&–çG3Ö7W'&VçEöf–ævW'&–çG2À¢¢–b6¶vRævWB‚'&W—%÷6¶vUö–B"’Ò&W—%÷6¶vUö–C ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6W'F–f–6FR&W6öÇfW2FòF–ffW&VçB6¶vR ¢ ¢&öGV7F–öåö†æFöfeö–BÒ7G"‡6¶vRævWB‚'&öGV7F–öåö†æFöfeö–B"’÷"""¢G'“ ¢&öGV7F–öåö†æFöfbÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×&öGV7F–öåö†æFöfeö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B6VÆbåö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†2À¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×&öGV7F–öåö†æFöfeö–BÀ¢“ ¢&—6P¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢÷&–v–æÂ&öGV7F–öâ†æFöfb—2Ö—76–ær ¢’g&öÒW†0¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&öGV7F–öåö†æFöfb“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢÷&–v–æÂ&öGV7F–öâ†æFöfb—2Vç6–væVB÷"F×W&VB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â&öGV7F–öåö†æFöf`¢¢–b€¢2&W—"&W7VÇBæBâW‡FW&æÂVF—B–çF¶R&R&÷F‚ÆVv—F–ÖFP¢2VF—FVB6÷W&6W2â¶VWF†—2Æ—7B–FVçF–6ÂFòF†R†æFöfbG—W0¢2F†R–æFWVæFVçBÖVF—B&÷VæF'’6â6Æ–Ó²F†R&VÖ–æ–ær6†V6·0¢27F–ÆÂ&–æBF†RW†7B6–væVB†æFöfbÂ6¶vRÂ6W'F–f–6FRÀ¢2'F–f7BÂ6æöæ–6ÂÂf–ævW'&–çG2Â&öGV6W"æBG&ç7÷'Bà¢&öGV7F–öåö†æFöfbævWB‚&†æFöfe÷G—R"¢æ÷B–âTD•Eõ4õU$4Uô„äDôdeõE•U0¢÷"&öGV7F–öåö†æFöfbævWB‚&†æFöfeö–B"’Ò&öGV7F–öåö†æFöfeö–@¢÷"6W'F–f–6FRævWB‚&†æFöfeö–B"’Ò&öGV7F–öåö†æFöfeö–@¢÷"&öGV7F–öåö†æFöfbævWB‚'v÷&µö÷&FW%ö–B"¢Ò6¶vRævWB‚'&öGV7F–öå÷v÷&µö÷&FW%ö–B"¢÷"&öGV7F–öåö†æFöfbævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"¢Ò6÷W&6Uö'F–f7Eö–@¢÷"&öGV7F–öåö†æFöfbævWB‚&6æöæ–6Å÷6†#Sb"’Ò&6Uö6æöæ–6Åö†6€¢÷"&öGV7F–öåö†æFöfbævWB‚&f–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG0¢÷"&öGV7F–öåö†æFöfbævWB‚&g&öÕ÷v–æF÷uö&–æF–æuö–B"¢Ò6¶vRævWB‚'&öGV7F–öå÷v–æF÷uö&–æF–æuö–B"¢÷"&öGV7F–öåö†æFöfbævWB‚&g&öÕö7F÷%ö&–æF–æuö–B"¢Ò6¶vRævWB‚'&öGV7F–öåö7F÷%ö&–æF–æuö–B"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢&öGV7F–öâ†æFöfbFöW2æ÷B&–æBF†R÷&–v–æÂ&6RWF†÷&—G’ ¢¢–b€¢6VÆbç6WGF–æw2æFWÆ÷–ÖVçEöÖöFRÓÒ&6Æ÷VB ¢æB&öGV7F–öåö†æFöfbævWB‚&g&öÕ÷6W76–öåöf–ævW'&–çB"¢Ò6¶vRævWB‚'&öGV7F–öå÷6W76–öåöf–ævW'&–çB"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢&öGV7F–öâG&ç7÷'B6W76–öâF–ffW'2 ¢¢–b€¢VF—Eö†æFöfbævWB‚'v÷&µö÷&FW%ö–B"’Ò6¶vRævWB‚&VF—E÷v÷&µö÷&FW%ö–B"¢÷"VF—Eö†æFöfbævWB‚&g&öÕ÷v–æF÷uö&–æF–æuö–B"¢Ò6¶vRævWB‚&VF—E÷v–æF÷uö&–æF–æuö–B"¢÷"VF—Eö†æFöfbævWB‚&g&öÕö7F÷%ö&–æF–æuö–B"¢Ò6¶vRævWB‚&VF—Eö7F÷%ö&–æF–æuö–B"¢÷"VF—Eö†æFöfbævWB‚&VF—Eö7–6ÆUö–B"¢Ò&öGV7F–öåö†æFöfbævWB‚&VF—Eö7–6ÆUö–B"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢VF—B×Fò×&W—"†æFöfbFöW2æ÷B&–æBF†R÷&–v–æÂVF—F÷" ¢¢–b€¢6VÆbç6WGF–æw2æFWÆ÷–ÖVçEöÖöFRÓÒ&6Æ÷VB ¢æBVF—Eö†æFöfbævWB‚&g&öÕ÷6W76–öåöf–ævW'&–çB"¢Ò6¶vRævWB‚&VF—E÷6W76–öåöf–ævW'&–çB"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢VF—BG&ç7÷'B6W76–öâF–ffW'2 ¢¢6VÆbåö76W'E÷&W—%÷6W&F–öâ€¢æÖW76RÂ&ö¦V7Eö–BÂv÷&µö÷&FW"Â6¶vRÂVF—Eö†æFöf`¢¢&WGW&â°¢'6¶vR#¢6¶vRÀ¢&6W'F–f–6FR#¢6W'F–f–6FRÀ¢&VF—Eö†æFöfb#¢VF—Eö†æFöfbÀ¢'&öGV7F–öåö†æFöfb#¢&öGV7F–öåö†æFöfbÀ¢&&6U÷7F÷'–&ö&B#¢&6U÷7F÷'–&ö&BÀ¢&&6U÷&V6÷&B#¢&6U÷&V6÷&BÀ¢&&6Uö6æöæ–6Åö†6‚#¢&6Uö6æöæ–6Åö†6‚À¢&&6U÷&uöf–ævW'&–çB#¢&6U÷&uöf–ævW'&–çBÀ¢&f–ævW'&–çG2#¢7W'&VçEöf–ævW'&–çG2À¢Ð ¢FVböÖ&µ÷&W—%ö†æFöfg5÷&V¦V7FVB€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢&W—%÷6¶vUö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢&V6öã¢7G"À¢’ÓâæöæS ¢""$Ö¶RâVæ6öÖÖ—GFVB&W—"&W7VÇB–×÷76–&ÆRf÷"VF—BFò6öç7VÖRâ""  ¢&V¦V7FVEö–G3¢6WE·7G%ÒÒ6WB‚¢f÷"†æFöfb–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G2€¢æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ&†æFöfg2 ¢“ ¢–b€¢†æFöfbævWB‚&†æFöfe÷G—R"’Ò'&W—%÷FõöVF—B ¢÷"†æFöfbævWB‚&–Ö×WF&ÆU÷&W—%÷6¶vUö–B"’Ò&W—%÷6¶vUö–@¢÷"†æFöfbævWB‚'v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"†æFöfbævWB‚'7FGW2"’æ÷B–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢“ ¢6öçF–çVP¢†æFöfbÒ6÷’æFVW6÷’††æFöfb¢†æFöfe²'7FGW2%ÒÒ'&V¦V7FVB ¢†æFöfe²'&V¦V7F–öå÷&V6öâ%ÒÒ7G"‡&V6öâ÷"'&W—"G&ç67F–öâ&÷'FVB"•³£S%Ð¢†æFöfe²'&V¦V7FVEöB%ÒÒWF5÷7F×‚¢†æFöfbç÷‚&6Æ–ÖVEö'•÷v÷&µö÷&FW%ö–B"ÂæöæR¢†æFöfbç÷‚&6Æ–ÕöWö6…÷6V6öæG2"ÂæöæR¢W'6—7FVBÒ6VÆbç7F÷&Rç6fU÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–C×7G"††æFöfe²&†æFöfeö–B%Ò’À¢fÇVSÖ†æFöfbÀ¢¢&V¦V7FVEö–G2æFB‡7G"‡W'6—7FVE²&†æFöfeö–B%Ò’¢–b&V¦V7FVEö–G3 ¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢&÷w2Ò‡V"ç6WFFVfVÇB‚&†æFöfg2"ÂµÒ¢f÷"–æFW‚Â&÷r–âVçVÖW&FR‡&÷w2“ ¢–b7G"‡&÷rævWB‚&†æFöfeö–B"’÷"""’–â&V¦V7FVEö–G3 ¢WFFVBÒ6÷’æFVW6÷’‡&÷r¢WFFVE²'7FGW2%ÒÒ'&V¦V7FVB ¢WFFVE²'&V¦V7F–öå÷&V6öâ%ÒÒ7G"€¢&V6öâ÷"'&W—"G&ç67F–öâ&÷'FVB ¢•³£S%Ð¢WFFVE²'&V¦V7FVEöB%ÒÒWF5÷7F×‚¢&÷w5¶–æFW…ÒÒWFFV@ ¢FVb÷&W7F÷&U÷&W—%÷6¶vUögFW%öf–ÆVEö6öÖÖ—B€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢¢À¢÷&–v–æÅ÷6¶vS¢F–7E·7G"Âç•ÒÀ¢v÷&µö÷&FW%ö–C¢7G"À¢W‡V7FVEö6Æ–Õ÷Fö¶Vã¢7G"À¢W‡V7FVEö6Æ–ÕövVæW&F–öã¢–çBÀ¢&6Uö'F–f7Eö–C¢7G"À¢G&ç67F–öåö–C¢7G"À¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–C¢7G"ÂæöæRÒæöæRÀ¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#Sc¢7G"ÂæöæRÒæöæRÀ¢&W—%÷FõöVF—Eö†æFöfeö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâF–7E·7G"Âç•Ó ¢""%&VÆV6R6Æ–ÒÂ–æ6ÇVF–ær6ö×Vç6F–öâgFW"&RÔ426öç7VÖRâ""  ¢6¶vUö–BÒ7G"†÷&–v–æÅ÷6¶vU²'&W—%÷6¶vUö–B%Ò¢7W'&VçBÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×6¶vUö–BÀ¢¢–b€¢7W'&VçBævWB‚'7FGW2"’ÓÒ&6öç7VÖ–ær ¢æB7W'&VçBævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–@¢“ ¢–b€¢7W'&VçBævWB‚&6Æ–Õ÷Fö¶Vâ"’ÒW‡V7FVEö6Æ–Õ÷Fö¶Và¢÷"7W'&VçBævWB‚&6Æ–ÕövVæW&F–öâ"¢ÒW‡V7FVEö6Æ–ÕövVæW&F–öà¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö6Æ–Õö6öæfÆ–7C¢f–ÆVB&W—"æòÆöævW"÷vç2F†R7W'&VçB6Æ–Ò ¢¢&W7F÷&VBÒ6VÆbç7F÷&Ræf–æ—6…÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6¶vUö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6Æ–Õ÷Fö¶VãÖW‡V7FVEö6Æ–Õ÷Fö¶VâÀ¢7V66W76÷%ö'F–f7Eö–CÔæöæRÀ¢7V66W76÷%ö6æöæ–6Å÷6†#ScÔæöæRÀ¢¢VÆ–b€¢7W'&VçBævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢æB7W'&VçBævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–@¢“ ¢–bæ÷BÆÂ€¢€¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–BÀ¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#SbÀ¢&W—%÷FõöVF—Eö†æFöfeö–BÀ¢¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷&öÆÆ&6µö–FVçF—G•ö–çfÆ–C¢f—†VB7V66W76÷"–FVçF—G’—2&WV—&VB ¢¢6öç7V×F–öâÒ7W'&VçBævWB‚&6öç7V×F–öåö6Æ–Ò"¢–b€¢æ÷B—6–ç7Fæ6R†6öç7V×F–öâÂF–7B¢÷"6öç7V×F–öâævWB‚&6Æ–Õ÷Fö¶Vâ"’ÒW‡V7FVEö6Æ–Õ÷Fö¶Và¢÷"6öç7V×F–öâævWB‚&6Æ–ÕövVæW&F–öâ"¢ÒW‡V7FVEö6Æ–ÕövVæW&F–öà¢÷"7W'&VçBævWB‚&6Æ–ÕövVæW&F–öâ"¢ÒW‡V7FVEö6Æ–ÕövVæW&F–öà¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö6Æ–Õö6öæfÆ–7C¢6öç7VÖVB6¶vR—2æ÷B÷væVB'’F†Rf—†VB6Æ–Ò ¢¢&W7F÷&VBÒ6VÆbç7F÷&Rç&öÆÆ&6µö6öç7VÖVE÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6¶vUö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6Æ–Õ÷Fö¶VãÖW‡V7FVEö6Æ–Õ÷Fö¶VâÀ¢6Æ–ÕövVæW&F–öãÖW‡V7FVEö6Æ–ÕövVæW&F–öâÀ¢&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–C×7G"€¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–@¢’À¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#Sc×7G"€¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#S`¢’À¢&W—%÷FõöVF—Eö†æFöfeö–C×7G"€¢&W—%÷FõöVF—Eö†æFöfeö–@¢’À¢¢VÆ–b7W'&VçBævWB‚'7FGW2"’ÓÒ&—77VVB# ¢&W7F÷&VBÒ7W'&Vç@¢VÇ6S ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö6Æ–Õö6öæfÆ–7C¢f–ÆVB&W—"6ææ÷B&VÆV6Ræ÷F†W"WF†÷&—G’w26¶vR ¢¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"†æÖW76RÂ&ö¦V7Eö–BÂ&W7F÷&VB¢&WGW&â&W7F÷&V@ ¢FVbö6öÖÖ—E÷7F÷'–&ö&EögVÆÅ÷&W—"€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢f–ÆUöæÖS¢7G"À¢6÷W&6Uö'F–f7Eö–C¢7G"ÂæöæRÀ¢&W—%÷6¶vUö–C¢7G"À¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÀ¢’ÓâFööÅ&W7öç6S ¢""$W†V7WFRöæR6¶vRÖ&÷VæB7G'V7GW&Â&W—"27V66W76÷"42â""  ¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢WF†÷&—¦F–öâÒ6VÆbåögVÆÅ÷&W—%öWF†÷&—¦F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢&W—%÷6¶vUö–C×&W—%÷6¶vUö–BÀ¢¢&6Uö6æöæ–6Åö†6‚Ò7G"†WF†÷&—¦F–öå²&&6Uö6æöæ–6Åö†6‚%Ò¢&6U÷&uöf–ævW'&–çBÒ7G"†WF†÷&—¦F–öå²&&6U÷&uöf–ævW'&–çB%Ò¢&W—&VEö–çWEö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢–b&W—&VEö–çWEö†6‚ÓÒ&6Uö6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"€¢'&W—%öæõö6†ævS¢6ö×ÆWFR&W—"×W7B6†ævRF†RVF—FVB6æöæ–6Â7F÷'–&ö&B ¢ ¢66÷Uö†6‚Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢&VF—Eö6W'F–f–6FUö–B#¢WF†÷&—¦F–öå²&6W'F–f–6FR%ÒævWB€¢&6W'F–f–6FUö–B ¢’À¢&VF—E÷Fõ÷&W—%ö†æFöfeö–B#¢WF†÷&—¦F–öå°¢&VF—Eö†æFöfb ¢ÒævWB‚&†æFöfeö–B"’À¢'&W—%÷v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢&&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢&&6U÷&uöf–ævW'&–çB#¢&6U÷&uöf–ævW'&–çBÀ¢'&W—&VEö–çWE÷6†#Sb#¢&W—&VEö–çWEö†6‚À¢Ð¢¢¢–bWF†÷&—¦F–öå²'6¶vR%ÒævWB‚'7FGW2"’ÓÒ&6öç7VÖVB# ¢&V6÷fW&VBÒ6VÆbå÷&V6÷fW%ö6öç7VÖVE÷&W—%÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6¶vSÖWF†÷&—¦F–öå²'6¶vR%ÒÀ¢W‡V7FVEö¶–æCÒ'&W—""À¢W‡V7FVE÷66÷Uö†6ƒ×66÷Uö†6‚À¢¢–b&V6÷fW&VB—2æ÷BæöæS ¢&V6÷fW&VBæFFçWFFR€¢°¢&gVÆÅ÷&öGV7F–öå÷fÆ–FF–öâ#¢G'VRÀ¢&f–æÅögVÆÅ÷fÆ–FF–öâ#¢G'VRÀ¢'7G'V7GW&Å÷&W—"#¢G'VRÀ¢&&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢&&6U÷&uöf–ævW'&–çB#¢&6U÷&uöf–ævW'&–çBÀ¢'&W—%÷66÷Uö†6‚#¢66÷Uö†6‚À¢&VF—Eö6W'F–f–6FUö–B#¢WF†÷&—¦F–öå°¢&6W'F–f–6FR ¢ÒævWB‚&6W'F–f–6FUö–B"’À¢&VF—E÷Fõ÷&W—%ö†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’€¢WF†÷&—¦F–öå²&VF—Eö†æFöfb%Ð¢’À¢'&WGW&å÷FõöVF—E÷v÷&µö÷&FW%ö–B#¢WF†÷&—¦F–öå°¢'6¶vR ¢ÒævWB‚&VF—E÷v÷&µö÷&FW%ö–B"’À¢Ð¢¢&WGW&â&V6÷fW&V@¢WF†÷&—¦F–öå²'6¶vR%ÒÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×&W—%÷6¶vUö–BÀ¢¢G&ç67F–öåö–BÒöæWuö–B‚%$U•""¢6¶vUö6Æ–ÖVBÒfÇ6P¢G&ç67F–öå÷&W&VBÒfÇ6P¢&W7öç6S¢FööÅ&W7öç6RÂæöæRÒæöæP¢6æF–FFUö–G3¢GWÆU·7G"Â7G"Â7G%ÒÂæöæRÒæöæP ¢FVb&öÆÆ&6²‡&V6öã¢7G"’ÓâæöæS ¢6ÆVçWöW'&÷'3¢Æ—7E·7G%ÒÒµÐ¢–bG&ç67F–öå÷&W&VC ¢G'“ ¢6VÆbç7F÷&Ræ&÷'E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡6÷W&6Uö'F–f7Eö–B’À¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢&V6öã×&V6öâÀ¢¢W†6WBW†6WF–öâ2W†3 ¢6ÆVçWöW'&÷'2æVæB†b'G&ç67F–öã§¶W†7Ò"¢–b6æF–FFUö–G2—2æ÷BæöæS ¢G'“ ¢6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢†æFöfeö–CÖ6æF–FFUö–G5³%ÒÀ¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢7FFSÒ&&÷'FVB"À¢&V6öã×&V6öâÀ¢¢W†6WBW†6WF–öâ2W†3 ¢6ÆVçWöW'&÷'2æVæB†b&6æF–FFS§¶W†7Ò"¢–b6¶vUö6Æ–ÖVC ¢G'“ ¢6VÆbå÷&W7F÷&U÷&W—%÷6¶vUögFW%öf–ÆVEö6öÖÖ—B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷&–v–æÅ÷6¶vSÖWF†÷&—¦F–öå²'6¶vR%ÒÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢W‡V7FVEö6Æ–Õ÷Fö¶Vã×7G"€¢6Æ–ÖVE÷6¶vRævWB‚&6Æ–Õ÷Fö¶Vâ"’÷"" ¢’À¢W‡V7FVEö6Æ–ÕövVæW&F–öãÖ–çB€¢6Æ–ÖVE÷6¶vRævWB‚&6Æ–ÕövVæW&F–öâ"’÷" ¢’À¢&6Uö'F–f7Eö–C×7G"‡6÷W&6Uö'F–f7Eö–B’À¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–CÒ€¢6æF–FFUö–G5³Ò–b6æF–FFUö–G2VÇ6RæöæP¢’À¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#ScÒ€¢7G"‡&W7öç6RæFFævWB‚&6æöæ–6Å÷6†#Sb"’÷"""¢–b&W7öç6R—2æ÷BæöæP¢VÇ6RæöæP¢’À¢&W—%÷FõöVF—Eö†æFöfeö–CÒ€¢6æF–FFUö–G5³%Ò–b6æF–FFUö–G2VÇ6RæöæP¢’À¢¢W†6WBW†6WF–öâ2W†3 ¢6ÆVçWöW'&÷'2æVæB†b'6¶vS§¶W†7Ò"¢–b6ÆVçWöW'&÷'3 ¢&—6R'VçF–ÖTW'&÷"€¢'&W—%÷&öÆÆ&6µö–æ6ö×ÆWFS¢"²#²"æ¦ö–â†6ÆVçWöW'&÷'2¢ ¢G'“ ¢6Æ–ÖVE÷6¶vRÒ6VÆbç7F÷&Ræ6Æ–Õ÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vUö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢¢6¶vUö6Æ–ÖVBÒG'VP¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"€¢æÖW76RÂ&ö¦V7Eö–BÂ6Æ–ÖVE÷6¶vP¢¢6VÆbç7F÷&Rç&W&U÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡6÷W&6Uö'F–f7Eö–B’À¢W‡V7FVEö6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢W‡V7FVE÷&uöf–ævW'&–çCÖ&6U÷&uöf–ævW'&–çBÀ¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢7V66W76÷%ö¶–æCÒ'&W—""À¢66÷Uö†6ƒ×66÷Uö†6‚À¢¢G&ç67F–öå÷&W&VBÒG'VP¢&W7öç6RÒ6VÆbæ6öÖÖ—E÷7F÷'–&ö&B€¢æÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢&WVW7E÷6W76–öåö–C×&WVW7E÷6W76–öåö–BÀ¢&W—%÷6¶vUö–C×&W—%÷6¶vUö–BÀ¢6÷W&6UöÆ–æµöÖöFSÒ'&W—""À¢÷&W—%÷G&ç67F–öåöÖævVCÕG'VRÀ¢÷7V66W76÷%÷G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢÷7V66W76÷%ö¶–æCÒ'&W—""À¢÷7V66W76÷%ö&6Uö'F–f7Eö–C×7G"‡6÷W&6Uö'F–f7Eö–B’À¢÷7V66W76÷%÷66÷Uö†6ƒ×66÷Uö†6‚À¢¢–b&W7öç6Ræ'F–f7Eö–C ¢6æF–FFUö–G2Ò€¢7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7G"‡&W7öç6RæFFævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"’÷"""’À¢7G"‡&W7öç6RæFFævWB‚&†æFöfeö'F–f7Eö–B"’÷"""’À¢¢–bæ÷BÆÂ†6æF–FFUö–G2“ ¢&—6R'VçF–ÖTW'&÷"€¢'&W—"7V66W76÷"V&Æ–6F–öâFW67&—F÷"—2–æ6ö×ÆWFR ¢¢W†6WBW†6WF–öâ2W†3 ¢G'“ ¢&öÆÆ&6²†b&gVÆÂ&W—"6öÖÖ—B&—6VB·G—R†W†2’åõöæÖUõ÷Ò"¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢&—6R6ÆVçWöW†2g&öÒW†0¢&—6P ¢&W÷'BÒ&W7öç6RæFFævWB‚'&W÷'B"¢–b€¢&W7öç6Rç7FGW2Ò$ôET5D”ôåõ$TdÄ”t…Eõ50¢÷"æ÷B&W7öç6Ræ'F–f7Eö–@¢÷"æ÷B—6–ç7Fæ6R‡&W÷'BÂF–7B¢÷"&ööÂ‡&W÷'BævWB‚&—77VW2"’¢“ ¢&öÆÆ&6²‚&gVÆÂ&W—"F–Bæ÷B726ö×ÆWFRfÆ–FF–öâ"¢&W7öç6RæFFçWFFR€¢°¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢'&W—%÷6¶vU÷7FGW2#¢&—77VVB"À¢'&W—%÷G&ç67F–öåö–B#¢G&ç67F–öåö–BÀ¢'&W—%÷G&ç67F–öå÷7FFR#¢&&÷'FVB"À¢&&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢&æW‡Eö7F–öâ#¢&6÷'&V7EögVÆÅ÷&W—%÷F†Vå÷&WG'•÷v—F…öæWu÷&W—%÷v÷&µö÷&FW""À¢Ð¢¢–b—6–ç7Fæ6R‡&W7öç6RæFFævWB‚&†æFöfb"’ÂF–7B“ ¢&W7öç6RæFF²&†æFöfb%Õ²'7FGW2%ÒÒ'&V¦V7FVB ¢&WGW&â&W7öç6P ¢†æFöfeö–BÒ7G"‡&W7öç6RæFFævWB‚&†æFöfeö'F–f7Eö–B"’÷"""¢G'“ ¢&W—%÷FõöVF—BÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&†æFöfg2"À¢&V6÷&Eö–CÖ†æFöfeö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&W—%÷FõöVF—B“ ¢&—6RfÇVTW'&÷"‚'&W—%÷FõöVF—B†æFöfb—2Vç6–væVB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â&W—%÷FõöVF—@¢¢6¶vRÒWF†÷&—¦F–öå²'6¶vR%Ð¢–b€¢&W—%÷FõöVF—BævWB‚'7FGW2"’Ò'&W&VB ¢÷"&W—%÷FõöVF—BævWB‚&†æFöfe÷G—R"’Ò'&W—%÷FõöVF—B ¢÷"&W—%÷FõöVF—BævWB‚&–Ö×WF&ÆU÷&W—%÷6¶vUö–B"¢Ò&W—%÷6¶vUö–@¢÷"&W—%÷FõöVF—BævWB‚'7F÷'–&ö&Eö'F–f7Eö–B"¢Ò&W7öç6Ræ'F–f7Eö–@¢÷"&W—%÷FõöVF—BævWB‚&6æöæ–6Å÷6†#Sb"¢Ò&W7öç6RæFFævWB‚&6æöæ–6Å÷6†#Sb"¢÷"&W—%÷FõöVF—BævWB‚'&WGW&å÷FõöVF—E÷v÷&µö÷&FW%ö–B"¢Ò6¶vRævWB‚&VF—E÷v÷&µö÷&FW%ö–B"¢÷"&W—%÷FõöVF—BævWB‚'&WGW&å÷FõöVF—E÷v–æF÷uö&–æF–æuö–B"¢Ò6¶vRævWB‚&VF—E÷v–æF÷uö&–æF–æuö–B"¢÷"&W—%÷FõöVF—BævWB‚'&WGW&å÷FõöVF—Eö7F÷%ö&–æF–æuö–B"¢Ò6¶vRævWB‚&VF—Eö7F÷%ö&–æF–æuö–B"¢÷"&W—%÷FõöVF—BævWB‚'&WGW&å÷FõöVF—E÷6W76–öåöf–ævW'&–çB"¢Ò6¶vRævWB‚&VF—E÷6W76–öåöf–ævW'&–çB"¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuö–æ6ö×ÆWFS¢&W7VÇBFöW2æ÷B&WGW&âFòF†R÷&–v–æÂVF—F÷" ¢¢æWuö6æöæ–6Åö†6‚Ò7G"‡&W7öç6RæFFævWB‚&6æöæ–6Å÷6†#Sb"’÷"""¢–bæ÷BæWuö6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"‚'&W—"7V66W76÷"6æöæ–6Â†6‚—2Ö—76–ær"¢&W7öç6RæFF²&'F–f7B%ÒÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡&W7öç6Ræ'F–f7Eö–B’À¢°¢'&W—%ö&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢'&W—%ö&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢'&W—%ö&6U÷&uöf–ævW'&–çB#¢&6U÷&uöf–ævW'&–çBÀ¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢'&W—%öVF—Eö6W'F–f–6FUö–B#¢WF†÷&—¦F–öå°¢&6W'F–f–6FR ¢ÒævWB‚&6W'F–f–6FUö–B"’À¢'&W—%÷66÷Uö†6‚#¢66÷Uö†6‚À¢'&W—%÷G&ç67F–öåö–B#¢G&ç67F–öåö–BÀ¢ÒÀ¢¢FVbf–æÆ—¦UöW†7E÷7V66W76÷"‚’ÓâF–7E·7G"Âç•Ó ¢W†7E÷6¶vRÒ6VÆbç7F÷&Ræf–æ—6…÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vUö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6Æ–Õ÷Fö¶Vã×7G"†6Æ–ÖVE÷6¶vRævWB‚&6Æ–Õ÷Fö¶Vâ"’÷"""’À¢7V66W76÷%ö'F–f7Eö–C×7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7V66W76÷%ö6æöæ–6Å÷6†#ScÖæWuö6æöæ–6Åö†6‚À¢&W—%÷FõöVF—Eö†æFöfeö–CÖ†æFöfeö–BÀ¢¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"€¢æÖW76RÂ&ö¦V7Eö–BÂW†7E÷6¶vP¢¢6VÆbç7F÷&Ræ6öÖÖ—E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡6÷W&6Uö'F–f7Eö–B’À¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢7V66W76÷%ö'F–f7Eö–C×7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7V66W76÷%ö6æöæ–6Åö†6ƒÖæWuö6æöæ–6Åö†6‚À¢¢&WGW&âW†7E÷6¶vP ¢G'“ ¢6öç7VÖVE÷6¶vRÒf–æÆ—¦UöW†7E÷7V66W76÷"‚¢W†6WBW†6WF–öâ2f—'7Eöf–æÆ—¦UöW'&÷# ¢2&V6öæ6–ÆRÆ÷7B&W7öç6RgFW"V—F†W"F†R6–væVB6¶vP¢2w&—FR÷"F†R&6R7V66W76÷"42â&÷F‚7F÷&vR÷W&F–öç0¢266WBöæÇ’'—FRÖW†7B&WÆ“²6†ævVB&–æF–æw2f–Â6Æ÷6VBà¢G'“ ¢6öç7VÖVE÷6¶vRÒf–æÆ—¦UöW†7E÷7V66W76÷"‚¢W†6WBW†6WF–öâ2&WÆ•öW'&÷# ¢–b†6GG"‡&WÆ•öW'&÷"Â&FEöæ÷FR"“ ¢&WÆ•öW'&÷"æFEöæ÷FR€¢&–æ—F–Â&W—"f–æÆ—¦F–öâW'&÷#¢ ¢²7G"†f—'7Eöf–æÆ—¦UöW'&÷"•³£S%Ð¢¢&—6P¢G&ç67F–öå÷&W&VBÒfÇ6P¢6¶vUö6Æ–ÖVBÒfÇ6P¢W†6WBW†6WF–öâ2W†3 ¢G'“ ¢&öÆÆ&6²†b&gVÆÂ&W—"f–æÆ—¦F–öâ&—6VB·G—R†W†2’åõöæÖUõ÷Ò"¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢&—6R6ÆVçWöW†2g&öÒW†0¢&—6P ¢76W'B6æF–FFUö–G2—2æ÷BæöæP¢G'“ ¢V&Æ–6F–öâÒ6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢†æFöfeö–CÖ6æF–FFUö–G5³%ÒÀ¢G&ç67F–öåö–C×G&ç67F–öåö–BÀ¢7FFSÒ&6öÖÖ—GFVB"À¢¢&W7öç6RæFF²&'F–f7B%ÒÒV&Æ–6F–öå²&'F–f7G2%Õ°¢6æF–FFUö–G5³Ð¢Ð¢&W7öç6RæFF²&†æFöfb%ÒÒ6VÆbåö†æFöfe÷7VÖÖ'’€¢V&Æ–6F–öå²&†æFöfb%Ð¢¢W†6WBW†6WF–öâ2&ö¦V7F–öåöW†3 ¢2F†R6öÖÖ—GFVB&6RG&ç67F–öâ—2F†RV&Æ–6F–öâWF†÷&—G“°¢2&ö¦V7F–öç2&R&WG'–&ÆRæB×W7Bæ÷B&W7W'&V7BF†R6¶vRà¢&W7öç6RæFF²'V&Æ–6F–öå÷&ö¦V7F–öå÷v&æ–ær%ÒÒ7G"€¢&ö¦V7F–öåöW†0¢•³£S%Ð ¢&W7öç6RæFFçWFFR€¢°¢&gVÆÅ÷&öGV7F–öå÷fÆ–FF–öâ#¢G'VRÀ¢&f–æÅögVÆÅ÷fÆ–FF–öâ#¢G'VRÀ¢'7G'V7GW&Å÷&W—"#¢G'VRÀ¢&&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢&&6U÷&uöf–ævW'&–çB#¢&6U÷&uöf–ævW'&–çBÀ¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢'&W—%÷6¶vR#¢6öç7VÖVE÷6¶vRÀ¢'&W—%÷G&ç67F–öåö–B#¢G&ç67F–öåö–BÀ¢'&W—%÷G&ç67F–öå÷7FFR#¢&6öÖÖ—GFVB"À¢'&W—%÷66÷Uö†6‚#¢66÷Uö†6‚À¢&VF—Eö6W'F–f–6FUö–B#¢WF†÷&—¦F–öå²&6W'F–f–6FR%ÒævWB€¢&6W'F–f–6FUö–B ¢’À¢&VF—E÷Fõ÷&W—%ö†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’€¢WF†÷&—¦F–öå²&VF—Eö†æFöfb%Ð¢’À¢'&WGW&å÷FõöVF—E÷v÷&µö÷&FW%ö–B#¢6öç7VÖVE÷6¶vRævWB€¢&VF—E÷v÷&µö÷&FW%ö–B ¢’À¢Ð¢¢&W7öç6Rç7VÖÖ'’Ò.ZèÎi[N{¹>ièNKúîZHÞ[{.hùKªNûÙÎXZŽŠŽyIþKª~š(Nj8˜	®‹ø~ûÙÎ[{.XÙ^jÊkhŽ‹KžKúîZHÞXÈ^ûÙÎ‹ùNY¹îXéþxºÎz¸¾ZêŠê ¢&WGW&â&W7öç6P ¢öfVæ6U÷&öÆUö÷W&F–öà¢÷&VÆV6Uö6öç7V×F–öåö6Æ–×5ööåöW'&÷ ¢FVb6öÖÖ—E÷7F÷'–&ö&B€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢7F÷'–&ö&C¢F–7E·7G"Âç•ÒÀ¢f–ÆUöæÖS¢7G"À¢6÷W&6Uö'F–f7Eö–C¢7G"ÂæöæRÀ¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢&W—%÷6¶vUö–C¢7G"ÂæöæRÒæöæRÀ¢G&gE÷&VfÆ–v‡Eö–C¢7G"ÂæöæRÒæöæRÀ¢6÷W&6UöÆ–æµöÖöFS¢7G"Ò'&W—""À¢&WfÆ–FFVE÷&W÷'C¢F–7E·7G"Âç•ÒÂæöæRÒæöæRÀ¢&WfÆ–FFVEö6æöæ–6Åö†6ƒ¢7G"ÂæöæRÒæöæRÀ¢FVÆVÖWG'•ö÷W&F–öã¢7G"Ò&6öÖÖ—E÷7F÷'–&ö&B"À¢FVÆVÖWG'•÷7F'FVEöç3¢–çBÂæöæRÒæöæRÀ¢FVÆVÖWG'•÷&W7FvW5ö×3¢F–7E·7G"ÂfÆöEÒÂæöæRÒæöæRÀ¢FVÆVÖWG'•öÖWG&–73¢F–7E·7G"Âç•ÒÂæöæRÒæöæRÀ¢÷&W—%÷G&ç67F–öåöÖævVC¢&ööÂÒfÇ6RÀ¢÷7V66W76÷%÷G&ç67F–öåö–C¢7G"ÂæöæRÒæöæRÀ¢÷7V66W76÷%ö¶–æC¢7G"ÂæöæRÒæöæRÀ¢÷7V66W76÷%ö&6Uö'F–f7Eö–C¢7G"ÂæöæRÒæöæRÀ¢÷7V66W76÷%÷66÷Uö†6ƒ¢7G"ÂæöæRÒæöæRÀ¢÷&W&VEö–FVçF—G•öÖæ–fW7C¢F–7E·7G"Âç•ÒÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢6VÆbå÷&WV—&U÷'VçF–ÖR‚¢7F'FVEöç2ÒFVÆVÖWG'•÷7F'FVEöç2÷"F–ÖRçW&eö6÷VçFW%öç2‚¢–bæ÷B—6–ç7Fæ6R‡7F÷'–&ö&BÂF–7B“ ¢&—6RfÇVTW'&÷"‚'7F÷'–&ö&B×W7B&Râö&¦V7B"¢7V&Ö—GFVE÷–ÆöEö'—FW2ÒÆVâ…÷7F&ÆUö§6öåö'—FW2‡7F÷'–&ö&B’¢7V66W76÷%ö6öçFW‡BÒ€¢÷7V66W76÷%÷G&ç67F–öåö–BÀ¢÷7V66W76÷%ö¶–æBÀ¢÷7V66W76÷%ö&6Uö'F–f7Eö–BÀ¢÷7V66W76÷%÷66÷Uö†6‚À¢¢–bç’‡7V66W76÷%ö6öçFW‡B’æBæ÷BÆÂ‡7V66W76÷%ö6öçFW‡B“ ¢&—6RfÇVTW'&÷"‚&–æ6ö×ÆWFR7V66W76÷"V&Æ–6F–öâ6öçFW‡B"¢–b÷7V66W76÷%ö¶–æBæB÷7V66W76÷%ö¶–æBæ÷B–â²&FVÇF"Â'F6‚"Â'&W—"'Ó ¢&—6RfÇVTW'&÷"‚&–çfÆ–B7V66W76÷"V&Æ–6F–öâ¶–æB"¢–b÷&W&VEö–FVçF—G•öÖæ–fW7B—2æ÷BæöæRæB€¢÷7V66W76÷%ö¶–æBÒ&FVÇF ¢÷"&WfÆ–FFVE÷&W÷'B—2æöæP¢÷"&WfÆ–FFVEö6æöæ–6Åö†6‚—2æöæP¢“ ¢&—6RfÇVTW'&÷"€¢'&W&VEö6ö×7Eö6öçFW‡Eö–çfÆ–C¢öæÇ’&WfÆ–FFVBFVÇF7V66W76÷"Ö’'—726ö×7F–öâ ¢¢–b€¢÷7V66W76÷%ö&6Uö'F–f7Eö–@¢æB6÷W&6Uö'F–f7Eö–BÒ÷7V66W76÷%ö&6Uö'F–f7Eö–@¢“ ¢&—6RfÇVTW'&÷"‚'7V66W76÷"V&Æ–6F–öâ&6RFöW2æ÷BÖF6‚6÷W&6R'F–f7B"¢7V66W76÷%÷VæF–ærÒ&ööÂ…÷7V66W76÷%÷G&ç67F–öåö–B¢7V66W76÷%öÖWFFF¢F–7E·7G"Âç•ÒÒ·Ð¢–b7V66W76÷%÷VæF–æs ¢7V66W76÷%öÖWFFFÒ°¢'V&Æ–6F–öå÷7FFR#¢'VæF–ær"À¢'7V66W76÷%÷G&ç67F–öåö–B#¢÷7V66W76÷%÷G&ç67F–öåö–BÀ¢'7V66W76÷%ö¶–æB#¢÷7V66W76÷%ö¶–æBÀ¢'7V66W76÷%ö&6Uö'F–f7Eö–B#¢÷7V66W76÷%ö&6Uö'F–f7Eö–BÀ¢'7V66W76÷%÷66÷Uö†6‚#¢÷7V66W76÷%÷66÷Uö†6‚À¢'V&Æ–6F–öå÷&W&VEöB#¢WF5÷7F×‚’À¢Ð¢‡V%÷6æ6†÷BÂöÆVFvW%÷6æ6†÷BÒ6VÆbå÷&WV—&Uö7W'&VçE÷&ö¦V7E÷7FFR€¢æÖW76RÂ&ö¦V7Eö–@¢¢v÷&µö÷&FW%÷6æ6†÷BÒöf–æE÷v÷&µö÷&FW"†‡V%÷6æ6†÷BÂv÷&µö÷&FW%ö–B¢W‡V7FVE÷&öÆRÒ6VÆbå÷v÷&µö÷&FW%÷&öÆR€¢7G"‡v÷&µö÷&FW%÷6æ6†÷BævWB‚&÷W&F–öâ"’÷"""’À¢7G"‡v÷&µö÷&FW%÷6æ6†÷BævWB‚&ÖöFR"’÷"""’À¢¢–bW‡V7FVE÷&öÆRæ÷B–â²'&öGV7F–öâ"Â'&W—"'Ó ¢&—6RfÇVTW'&÷"‚'&öGV7F–öåöWF†÷&—G•÷&WV—&VC¢VF—Bv÷&²÷&FW'26ææ÷B6öÖÖ—B7F÷'–&ö&G2"¢6VÆbåö76W'E÷7F÷'–&ö&EöWF†÷&—G’‡v÷&µö÷&FW%÷6æ6†÷BÂW‡V7FVE÷&öÆR¢–bW‡V7FVE÷&öÆRÓÒ'&W—""æBæ÷B&W—%÷6¶vUö–C ¢&—6RfÇVTW'&÷"‚'&W—%÷6¶vU÷&WV—&VC¢&W—"WF†÷&—G’6ææ÷B6öÖÖ—Bv—F†÷WBâ–Ö×WF&ÆR6¶vR"¢–bW‡V7FVE÷&öÆRÓÒ'&öGV7F–öâ"æB&W—%÷6¶vUö–C ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷&öÆUöÖ—6ÖF6ƒ¢&öGV7F–öâWF†÷&—G’6ææ÷B6öç7VÖR&W—"6¶vR ¢¢–b6÷W&6UöÆ–æµöÖöFRæ÷B–â²'&W—""Â'&Wf—6–öâ'Ó ¢&—6RfÇVTW'&÷"‚&–çfÆ–B6÷W&6UöÆ–æµöÖöFR"¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ€¢æÖW76RÂv÷&µö÷&FW%÷6æ6†÷BÂ&WVW7E÷6W76–öåö–@¢¢&V6÷fW&–æuöG&gEö6öÖÖ—BÒfÇ6P¢G&gEö6öÖÖ—E÷–ÆöE÷6†#Sc¢7G"ÂæöæRÒæöæP¢&V6÷fW&–æu÷&W—%ö6öÖÖ—BÒfÇ6P¢–bW‡V7FVE÷&öÆRÓÒ'&W—""æB&W—%÷6¶vUö–C ¢G'“ ¢&V6÷fW'•÷6¶vRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×7G"‡&W—%÷6¶vUö–B’À¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B6VÆbåö—5÷Væ¶æ÷vå÷&ö¦V7E÷&V6÷&EöW'&÷"€¢W†2À¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×7G"‡&W—%÷6¶vUö–B’À¢“ ¢&—6P¢VÇ6S ¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB€¢&V6÷fW'•÷6¶vP¢“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vUö&–æF–æuöÖ—6ÖF6ƒ¢6¶vR—2Vç6–væVB÷"F×W&VB ¢¢–b&V6÷fW'•÷6¶vRævWB‚'7FGW2"’ÓÒ&6öç7VÖVB# ¢–b&V6÷fW'•÷6¶vRævWB€¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B ¢’Òv÷&µö÷&FW%÷6æ6†÷BævWB‚'v÷&µö÷&FW%ö–B"“ ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷Væf–Æ&ÆS¢6¶vR—2Ç&VG’6öç7VÖVB ¢¢2fÆ–FF–ær&W—"v÷&²÷&FW"Ö’öæÇ’7&÷72F†Rw&—FP¢2vFRgFW"F†R6ö×ÆWFR6öç7VÖVB×7V66W76÷"tÂ†2&VVà¢2fW&–f–VBâ&÷vFRFW67&—F÷"W'&÷'2†W&R&F†W"F†à¢2Ö6¶–ærF×W"2vVæW&–2v÷&²Ö÷&FW"7FGW2f–ÇW&Rà¢6VÆbåö6öç7VÖVE÷&W—%÷7V66W76÷%öFW67&—F÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW%÷6æ6†÷BÀ¢6¶vS×&V6÷fW'•÷6¶vRÀ¢¢&V6÷fW&–æu÷&W—%ö6öÖÖ—BÒG'VP¢VÆ–b&V6÷fW'•÷6¶vRævWB‚'7FGW2"’ÓÒ&6öç7VÖ–ær# ¢G'“ ¢&V6÷fW'•öW‡—'’ÒfÆöB€¢&V6÷fW'•÷6¶vRævWB€¢&6Æ–ÕöÆV6UöW‡—&W5öWö6…÷6V6öæG2 ¢¢÷" ¢¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"“ ¢&V6÷fW'•öW‡—'’Ò ¢&V6÷fW&–æu÷&W—%ö6öÖÖ—BÒ&ööÂ€¢&V6÷fW'•÷6¶vRævWB€¢&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B ¢¢ÓÒv÷&µö÷&FW%ö–@¢æB&V6÷fW'•öW‡—'’â ¢æB&V6÷fW'•öW‡—'’ÃÒF–ÖRçF–ÖR‚¢æB6VÆbåö6Æ–Õö—5ö7W'&VçEöWF†÷&—G•öæ6W7F÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW%÷6æ6†÷BÀ¢6Æ–Ó×&V6÷fW'•÷6¶vRÀ¢¢¢–b7G"‡v÷&µö÷&FW%÷6æ6†÷BævWB‚'7FGW2"’÷"""’æ÷B–âu$•DUõ$TE•õtõ$µôõ$DU%õ5DEU4U3 ¢–bG&gE÷&VfÆ–v‡Eö–C ¢&WÆ•÷&V6÷&BÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&G&gE÷&VfÆ–v‡G2"À¢&V6÷&Eö–CÖG&gE÷&VfÆ–v‡Eö–BÀ¢¢&V6÷fW'•÷7F÷'–&ö&BÂ÷&V6÷fW'•ö–FVçF—G’Ò€¢÷&W&Uö6ö×7E÷7F÷'–&ö&Eöf÷%ö6öÖÖ—B‡7F÷'–&ö&B¢¢ö76W'E÷#÷73ö÷WGWEö6öçG&7B‡&V6÷fW'•÷7F÷'–&ö&B¢&V6÷fW'•ö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb€¢&V6÷fW'•÷7F÷'–&ö&@¢¢G&gEö6öÖÖ—E÷–ÆöE÷6†#SbÒöG&gEö6öÖÖ—E÷–ÆöE÷6†#Sb€¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢G&gE÷&VfÆ–v‡Eö–C×7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6æöæ–6Å÷6†#Sc×&V6÷fW'•ö†6‚À¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢¢6öç7VÖVBÒ&WÆ•÷&V6÷&BævWB‚&6öç7VÖVEö'•ö6öÖÖ—B"¢–b—6–ç7Fæ6R†6öç7VÖVBÂF–7B“ ¢7F—fUö&–æF–ærÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%÷6æ6†÷BÀ¢‡V#Ö‡V%÷6æ6†÷BÀ¢¢&WVW7Eöf–ævW'&–çBÒ6VÆbå÷6W76–öåöf–ævW'&–çB€¢æÖW76RÂ&WVW7E÷6W76–öåö–@¢¢÷&–v–æÅ÷6W76–öå÷&WÆ’Ò€¢&WÆ•÷&V6÷&BævWB‚'6W76–öåöf–ævW'&–çB"¢ÓÒ&WVW7Eöf–ævW'&–ç@¢¢&V6÷fW&VE÷6W76–öå÷&WÆ’Ò€¢6öç7VÖVBævWB‚'&V6÷fW'•öWF†÷&—G•ö6öçG&7B"¢ÓÒ&G&gBÖ6öÖÖ—BÖFW66VæFçB×&V6÷fW'’×cS ¢æB6öç7VÖVBævWB‚&6ö×ÆWF–öåöWF†÷&—G•÷&ö÷Eö–B"¢ÓÒ7F—fUö&–æF–ærævWB‚&WF†÷&—G•÷&ö÷Eö–B"¢æB6öç7VÖVBævWB‚&6ö×ÆWF–öåöWF†÷&—G•övVæW&F–öâ"¢ÓÒ7F—fUö&–æF–ærævWB‚&vVæW&F–öâ"¢æB6öç7VÖVBævWB€¢&6ö×ÆWF–öåöWF†÷&—G•÷6W76–öåöf–ævW'&–çB ¢¢ÓÒ&WVW7Eöf–ævW'&–ç@¢æB6öç7VÖVBævWB‚&6öÖÖ—E÷–ÆöE÷6†#Sb"¢ÓÒG&gEö6öÖÖ—E÷–ÆöE÷6†#S`¢¢–b€¢&WÆ•÷&V6÷&BævWB‚'&ö¦V7Eö–B"’Ò&ö¦V7Eö–@¢÷"&WÆ•÷&V6÷&BævWB‚'v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"&WÆ•÷&V6÷&BævWB‚&6æF–FFU÷6†#Sb"¢Ò&V6÷fW'•ö†6€¢÷"æ÷B€¢÷&–v–æÅ÷6W76–öå÷&WÆ¢÷"&V6÷fW&VE÷6W76–öå÷&WÆ¢¢÷"6öç7VÖVBævWB‚&6æöæ–6Å÷6†#Sb"’Ò&V6÷fW'•ö†6€¢÷"6öç7VÖVBævWB‚'6÷W&6Uö'F–f7Eö–B"’Ò6÷W&6Uö'F–f7Eö–@¢÷"6öç7VÖVBævWB‚&f–ÆUöæÖR"’Òf–ÆUöæÖP¢“ ¢&—6RfÇVTW'&÷"‚&G&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&WÆ•ö6öæfÆ–7B"¢'F–f7BÂö'F–f7E÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†6öç7VÖVE²&'F–f7Eö–B%Ò’À¢¢&WGW&âFööÅ&W7öç6R€¢7FGW3×7G"†6öç7VÖVE²'7FGW2%Ò’À¢7VÖÖ'“×7G"†6öç7VÖVE²'7VÖÖ'’%Ò’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–C×7G"†6öç7VÖVE²&'F–f7Eö–B%Ò’À¢F÷væÆöE÷W&Ã×6VÆbç7F÷&Rç6–væVEöF÷væÆöE÷W&Â€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†6öç7VÖVE²&'F–f7Eö–B%Ò’À¢7G"†'F–f7E²&f–ÆUöæÖR%Ò’À¢’À¢G&6Uö–C×7G"†6öç7VÖVE²'G&6Uö–B%Ò’À¢FF×°¢&'F–f7B#¢'F–f7BÀ¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢6öç7VÖVBævWB€¢&6æöæ–6Åö§6öåö'F–f7Eö–B ¢’À¢&6æöæ–6Å÷6†#Sb#¢&V6÷fW'•ö†6‚À¢&†æFöfb#¢6÷’æFVW6÷’†6öç7VÖVBævWB‚&†æFöfb"’’À¢&6W'F–f–6FR#¢6÷’æFVW6÷’€¢6öç7VÖVBævWB‚&6W'F–f–6FR"¢’À¢&G&gE÷&VfÆ–v‡B#¢°¢&G&gE÷&VfÆ–v‡Eö–B#¢G&gE÷&VfÆ–v‡Eö–BÀ¢&6æF–FFU÷6†#Sb#¢&V6÷fW'•ö†6‚À¢'VÆ—G’#¢&WÆ•÷&V6÷&BævWB‚'VÆ—G’"’À¢ÒÀ¢&–FV×÷FVçE÷&WÆ’#¢G'VRÀ¢&WF†÷&—G•ö6öçF–çVF–öå÷&V6÷fW'’#¢&ööÂ€¢&V6÷fW&VE÷6W76–öå÷&WÆ¢’À¢&æW‡Eö7F–öâ#¢&–æFWVæFVçEöVF—B"À¢ÒÀ¢¢&V6÷fW'•÷&WVW7E÷6†#SbÒ6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢&G&gE÷&VfÆ–v‡Eö–B#¢G&gE÷&VfÆ–v‡Eö–BÀ¢&6æöæ–6Å÷6†#Sb#¢&V6÷fW'•ö†6‚À¢'6÷W&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&f–ÆUöæÖR#¢f–ÆUöæÖRÀ¢'6W76–öâ#¢6VÆbå÷6W76–öåöf–ævW'&–çB€¢æÖW76RÂ&WVW7E÷6W76–öåö–@¢’À¢Ð¢¢¢&V6÷fW&–æuöG&gEö6öÖÖ—BÒ&ööÂ€¢&WÆ•÷&V6÷&BævWB‚&6öÖÖ—E÷&WVW7E÷6†#Sb"¢ÓÒ&V6÷fW'•÷&WVW7E÷6†#S`¢æB—6–ç7Fæ6R‡&WÆ•÷&V6÷&BævWB‚&6öÖÖ—E÷&öw&W72"’ÂF–7B¢¢–b€¢æ÷B&V6÷fW&–æuöG&gEö6öÖÖ—@¢æBW‡V7FVE÷&öÆRÓÒ'&öGV7F–öâ ¢“ ¢&V6÷fW'•ö6öçFW‡BÒ€¢6VÆbåöFW66VæFçEöG&gEö6öÖÖ—E÷&V6÷fW'•ö6öçFW‡B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢G&gE÷&VfÆ–v‡Eö–C×7G"†G&gE÷&VfÆ–v‡Eö–B’À¢7F÷'–&ö&C×&V6÷fW'•÷7F÷'–&ö&BÀ¢6æöæ–6Å÷6†#Sc×&V6÷fW'•ö†6‚À¢–ÆöE÷6†#ScÖG&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢¢¢&WGW&â6VÆbåö6ö×ÆWFUöFW66VæFçEöG&gEö6öÖÖ—E÷&V6÷fW'’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢G&gE÷&VfÆ–v‡Eö–C×7G"†G&gE÷&VfÆ–v‡Eö–B’À¢–ÆöE÷6†#ScÖG&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢6öçFW‡C×&V6÷fW'•ö6öçFW‡BÀ¢7F'FVEöç3×7F'FVEöç2À¢¢–bæ÷B‡&V6÷fW&–æuöG&gEö6öÖÖ—B÷"&V6÷fW&–æu÷&W—%ö6öÖÖ—B“ ¢6VÆbåö76W'E÷v÷&µö÷&FW%÷w&—FU÷&VG’‡v÷&µö÷&FW%÷6æ6†÷B¢–bæ÷B‡&V6÷fW&–æuöG&gEö6öÖÖ—B÷"&V6÷fW&–æu÷&W—%ö6öÖÖ—B“ ¢6VÆbåö76W'E÷v÷&µö÷&FW%÷w&—FU÷&VG’‡v÷&µö÷&FW%÷6æ6†÷B¢–bW‡V7FVE÷&öÆRÓÒ'&W—""æBæ÷B÷&W—%÷G&ç67F–öåöÖævVC ¢&WGW&â6VÆbåö6öÖÖ—E÷7F÷'–&ö&EögVÆÅ÷&W—"€¢æÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢&W—%÷6¶vUö–C×7G"‡&W—%÷6¶vUö–B’À¢&WVW7E÷6W76–öåö–C×&WVW7E÷6W76–öåö–BÀ¢¢'VçF–ÖU÷FW7Eö6W'F–f–6FRÒ6VÆbå÷&WV—&U÷'VçF–ÖU÷FW7Eö6W'F–f–6FR†æÖW76RÂ&ö¦V7Eö–B¢–b€¢6VÆbç6WGF–æw2æFWÆ÷–ÖVçEöÖöFRÓÒ&6Æ÷VB ¢æBW‡V7FVE÷&öÆRÓÒ'&öGV7F–öâ ¢æBæ÷B7V66W76÷%÷VæF–æp¢æBæ÷BG&gE÷&VfÆ–v‡Eö–@¢“ ¢&—6RfÇVTW'&÷"€¢&G&gE÷&VfÆ–v‡E÷&WV—&VC¢6Æ÷VBãRã’gVÆÂ6öÖÖ—G2&WV—&R6–væVBgVÆÂÖG&gB&VfÆ–v‡B ¢¢7FvU÷G&6Uö–BÒöæWuö–B‚%E$2"¢ÖFW&–Æ—¦U÷7F'FVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢2F†RW'6—7FVæ6R&÷VæF'’—2öæR×v“¢6öÆÆ6RöÆB7Æ—B–çWG2öæ6RÀ¢2F†VâGF6‚7F&ÆR42–FVçF—F–W2âæòÆöær&ö×B÷"6VVFæ6R&F6€¢2—2vVæW&FVB÷"&WF–æVB'’6öÖÖ—Bà¢–b÷&W&VEö–FVçF—G•öÖæ–fW7B—2æöæS ¢7F÷'–&ö&BÂ–FVçF—G•öÖæ–fW7BÒ÷&W&Uö6ö×7E÷7F÷'–&ö&Eöf÷%ö6öÖÖ—B€¢7F÷'–&ö&@¢¢VÇ6S ¢7F÷'–&ö&BÒ6÷’æFVW6÷’‡7F÷'–&ö&B¢76W'Eö6ö×7E÷7F÷'–&ö&Eö6öçG&7B‡7F÷'–&ö&B¢–FVçF—G•öÖæ–fW7BÒ6÷’æFVW6÷’…÷&W&VEö–FVçF—G•öÖæ–fW7B¢–b–FVçF—G•öÖæ–fW7BÒ7F÷'–&ö&Eö–FVçF—G•öÖæ–fW7B‡7F÷'–&ö&B“ ¢&—6RfÇVTW'&÷"€¢'&W&VEö6ö×7Eö–FVçF—G•öÖ—6ÖF6ƒ¢FVÇF–FVçF—G’Öæ–fW7B—27FÆR ¢¢ö76W'E÷#÷73ö÷WGWEö6öçG&7B‡7F÷'–&ö&B¢6VÆbåö76W'EögVÆÅö6öÖÖ—EöW—6öFU÷66÷Uö–çFVw&—G’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW%÷6æ6†÷BÀ¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢6æF–FFU÷7F÷'–&ö&C×7F÷'–&ö&BÀ¢¢&÷VæEöG&gE÷&VfÆ–v‡C¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢G&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã¢7G"ÂæöæRÒæöæP¢–bG&gE÷&VfÆ–v‡Eö–C ¢&÷VæEöG&gE÷&VfÆ–v‡BÒ6VÆbå÷fW&–g•öG&gE÷&VfÆ–v‡B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–BÀ¢G&gE÷&VfÆ–v‡Eö–BÀ¢7F÷'–&ö&BÀ¢&WVW7E÷6W76–öåö–BÀ¢¢ÖFW&–Æ—¦VEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢–bv÷&µö÷&FW"ævWB‚&÷W&F–öâ"’æ÷B–â5Dõ%”$ô$EôõU$D”ôå3 ¢&—6RfÇVTW'&÷"‚'v÷&²÷&FW"÷W&F–öâ6ææ÷B6öÖÖ—B7F÷'–&ö&B"¢6VÆbåöVç7W&Uö6åöW†V7WFR‡v÷&µö÷&FW"¢&–÷%ö—77VUö¶W—3¢6WE·GWÆU·7G"Â7G%ÕÒÒ6WB‚¢—5÷&W—%öÆ–æ²Ò&ööÂ‡6÷W&6Uö'F–f7Eö–BæB6÷W&6UöÆ–æµöÖöFRÓÒ'&W—""¢–b—5÷&W—%öÆ–æ³ ¢÷&–÷%÷7F÷'–&ö&BÂ&–÷%÷&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B†æÖW76RÂ&ö¦V7Eö–BÂ6÷W&6Uö'F–f7Eö–B¢6W&–Æ—¦VEö¶W—2Ò&–÷%÷&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB‚&—77VU÷6–væGW&W2"ÂµÒ¢f÷"—FVÒ–â6W&–Æ—¦VEö¶W—3 ¢–b—6–ç7Fæ6R†—FVÒÂÆ—7B’æBÆVâ†—FVÒ’ÓÒ# ¢&–÷%ö—77VUö¶W—2æFB‚‡7G"†—FVÕ³Ò’Â7G"†—FVÕ³Ò’’¢–bæ÷B&–÷%ö—77VUö¶W—3 ¢&–÷%÷&W÷'BÒfÆ–FFU÷7F÷'–&ö&BçfÆ–FFU÷&ö¦V7B…÷&–÷%÷7F÷'–&ö&BÂÖöFSÒ&VF—B"¢&–÷%ö—77VUö¶W—2Ò°¢‡7G"†—77VRævWB‚&W'&÷%ö6öFR"’’Â7G"†—77VRævWB‚&Æö6F–öâ"’’¢f÷"—77VR–â&–÷%÷&W÷'BævWB‚&—77VW2"ÂµÒ¢Ð¢Wf–FVæ6RÒ7F÷'–&ö&Bç6WFFVfVÇB‚'&WfVçF–öåöWf–FVæ6R"Â·Ò¢†—7F÷'’ÒWf–FVæ6Rç6WFFVfVÇB‚'&W—%ö†—7F÷'’"ÂµÒ¢–bæ÷B—6–ç7Fæ6R††—7F÷'’ÂÆ—7B“ ¢†—7F÷'’ÒµÐ¢Wf–FVæ6U²'&W—%ö†—7F÷'’%ÒÒ†—7F÷'¢†—7F÷'’æVæB€¢°¢'&÷VæB#¢ÆVâ††—7F÷'’’²À¢&–æ—F–Åö—77VUö6÷VçB#¢ÆVâ‡&–÷%ö—77VUö¶W—2’À¢'&W6–GVÅö—77VUö6÷VçB#¢À¢&–çG&öGV6VEö—77VUö6÷VçB#¢À¢'&WVE÷&÷VæG2#¢À¢&7F–öâ#¢'Væ–f–VEögVÆÅö6†–å÷&W—""À¢'6÷W&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢'&V6÷&FVEö'’#¢'6†÷'FG&Ö×&ö¦V7BÖvVçB"À¢Ð¢¢–b&WfÆ–FFVE÷&W÷'B—2æ÷BæöæS ¢–b&WfÆ–FFVEö6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B“ ¢&—6RfÇVTW'&÷"‚'&WfÆ–FFVEö6æöæ–6ÅöÖ—6ÖF6‚"¢&W÷'BÒ6÷’æFVW6÷’‡&WfÆ–FFVE÷&W÷'B¢VÇ6S ¢&W÷'BÒfÆ–FFU÷7F÷'–&ö&BçfÆ–FFU÷&ö¦V7B€¢7F÷'–&ö&BÂÖöFSÒ'&W—""–b—5÷&W—%öÆ–æ²VÇ6R&VF—B ¢¢–b—5÷&W—%öÆ–æ³ ¢7W'&VçEö—77VUö¶W—2Ò°¢‡7G"†—77VRævWB‚&W'&÷%ö6öFR"’’Â7G"†—77VRævWB‚&Æö6F–öâ"’’¢f÷"—77VR–â&W÷'BævWB‚&—77VW2"ÂµÒ¢–bæ÷B7G"†—77VRævWB‚&Æö6F–öâ"Â""’’ç7F'G7v—F‚‚'&W—%ö†—7F÷'•²"¢Ð¢&W—%÷&÷rÒ7F÷'–&ö&E²'&WfVçF–öåöWf–FVæ6R%Õ²'&W—%ö†—7F÷'’%Õ²ÓÐ¢&W—%÷&÷u²'&W6–GVÅö—77VUö6÷VçB%ÒÒÆVâ†7W'&VçEö—77VUö¶W—2b&–÷%ö—77VUö¶W—2¢&W—%÷&÷u²&–çG&öGV6VEö—77VUö6÷VçB%ÒÒÆVâ†7W'&VçEö—77VUö¶W—2Ò&–÷%ö—77VUö¶W—2¢–b&W—%÷&÷u²'&W6–GVÅö—77VUö6÷VçB%Ó ¢&W—%÷&÷u²'&WVE÷&÷VæG2%ÒÒ ¢&W—%÷&÷u²&7F–öâ%ÒÒ'&ö÷Eö6W6U÷&W—" ¢&W÷'BÒfÆ–FFU÷7F÷'–&ö&BçfÆ–FFU÷&ö¦V7B‡7F÷'–&ö&BÂÖöFSÒ'&W—""¢fÆ–FFVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢¦W&õö—77VRÒæ÷B&W÷'BævWB‚&—77VW2"¢–bæ÷B¦W&õö—77VS ¢6æF–FFUö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂÆVFvW"“ ¢Æ—fUö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢6VÆbå÷7F'EöW†V7WF–öâ†‡V"ÂÆ—fUö÷&FW"¢–bÆ—fUö÷&FW"ævWB‚'7FGW2"’ÓÒ&W†V7WF–ær# ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"€¢‡V"Âv÷&µö÷&FW%ö–BÂ&f–ÆVEö7W'&VçB ¢¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ&f–ÆVEö7W'&VçB ¢&öf–ÆRÒ7F÷'–&ö&EövVçBæ7F—fU÷&WfVçF–öå÷&öf–ÆR†ÆVFvW"¢¶æ÷vå÷GFW&ç2Ò°¢7G"‡&÷rævWB‚'GFW&åö¶W’"’¢f÷"&÷r–â&öf–ÆRævWB‚'GFW&ç2"ÂµÒ¢–b&÷rævWB‚'GFW&åö¶W’"¢Ð¢VÆ—G’ÒöG&gE÷VÆ—G•öÖWG&–72€¢7F÷'–&ö&BÀ¢Æ—7B‡&W÷'BævWB‚&—77VW2"ÂµÒ’’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢¶æ÷vå÷GFW&åö¶W—3Ö¶æ÷vå÷GFW&ç2À¢¢WfVçEö¶W’Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢&6æF–FFR#¢6æF–FFUö†6‚À¢'&W÷'B#¢&W÷'BÀ¢&6Æ72#¢'&öGV7F–öåöG&gE÷&VfÆ–v‡B"À¢Ð¢¢¢WfVçEö—77VW2ÒµÐ¢f÷"—77VR–â&W÷'BævWB‚&—77VW2"ÂµÒ“ ¢Æö6F–öâÒ7G"†—77VRævWB‚&Æö6F–öâ"’÷"&G&gEö6æF–FFR"¢6WfW&—G’Ò7G"†—77VRævWB‚'6WfW&—G’"’÷"&f–Â"¢–b6WfW&—G’ÓÒ'v&æ–ær# ¢6WfW&—G’Ò'v&â ¢WfVçEö—77VW2æVæB€¢°¢&W'&÷%ö6öFR#¢7G"†—77VRævWB‚&W'&÷%ö6öFR"’÷"$RÔE$eBÔ4ôåE$5BÓ"’À¢&6FVv÷'’#¢7G"€¢—77VRævWB‚'6VÖçF–5öÆ–W""¢÷"ö—77VUö6FVv÷'’‡7G"†—77VRævWB‚&W'&÷%ö6öFR"’÷"""’¢’À¢'6WfW&—G’#¢6WfW&—G¢–b6WfW&—G’–â7F÷'–&ö&EövVçBå4UdU$•D”U0¢VÇ6R&f–Â"À¢&&Æö6¶–ær#¢&ööÂ€¢—77VRævWB‚&&Æö6¶–ær"Â6WfW&—G’Ò'v&â"¢’À¢'6†÷Eö–G2#¢ö—77VU÷6†÷Eö–G2‡7F÷'–&ö&BÂ—77VR’À¢&f–VÆB#¢Æö6F–öâç'7Æ—B‚"â"Â•²ÓÒÀ¢&Æö6F–öâ#¢Æö6F–öâÀ¢&—77VU÷G—R#¢—77VRævWB‚'FV×ÆFUövFUö–B"¢÷"ö—77VUö6FVv÷'’‡7G"†—77VRævWB‚&W'&÷%ö6öFR"’÷"""’’À¢&Wf–FVæ6R#¢7G"†—77VRævWB‚&Wf–FVæ6R"’÷"Æö6F–öâ’À¢'6÷W&6U÷6VçFVæ6R#¢7G"€¢—77VRævWB‚&Wf–FVæ6R"’÷".yIþKª~hùKªNX˜ÞˆØžz‹þš(Nj8 ¢’À¢'&W—%ö7F–öâ#¢7G"€¢—77VRævWB‚'&W—%ö7F–öâ"’÷".KúîjÚ>Yî˜xÞikš(Nj88" ¢’À¢'7FGW2#¢&–æ—F–Â"À¢Ð¢¢7F÷'–&ö&EövVçBæ–ævW7EöWfVçB€¢ÆVFvW"À¢°¢&WfVçEö–B#¢b$UeB×¶WfVçEö¶W•³£#×Ò"À¢'F–ÖW7F×#¢WF5÷7F×‚’À¢&W—6öFUö–B#¢7G"€¢7F÷'–&ö&BævWB‚'6÷W&6UöÆö6²"Â·Ò’ævWB‚&W—6öFUö–B"¢÷".XZŽšžyºâ ¢’À¢&f–ÆUöæÖR#¢b&f–ÆVBÖG&gB×·v÷&µö÷&FW%ö–GÒ"À¢&f–ÆUöf–ævW'&–çB#¢6æF–FFUö†6‚À¢'6÷W&6U÷fW'6–öâ#¢&G&gBÖ6æF–FFR"À¢&VF—Eö7–6ÆUö–B#¢b$E$eB×·v÷&µö÷&FW%ö–GÒ×¶6æF–FFUö†6…³£%×Ò"À¢'F6µ÷G—R#¢'&öGV7F–öåöG&gE÷&VfÆ–v‡B"À¢&VF—Eö6Æ72#¢'&öGV7F–öåöG&gE÷&VfÆ–v‡B"À¢&VF—E÷&÷VæB#¢À¢'&W7VÇB#¢&f–Åö7W'&VçB"À¢&6†V6¶VE÷6†÷Eö6÷VçB#¢ÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢&—77VW2#¢WfVçEö—77VW2À¢'VÆ—G’#¢VÆ—G’À¢&6öÖÖ—E÷&VG’#¢fÇ6RÀ¢ÒÀ¢6†V6·ö–çEöWF†÷&—¦W#×6VÆbåöÆVFvW%ö6†V6·ö–çEöWF†÷&—¦W"€¢æÖW76RÂ&ö¦V7Eö–@¢’À¢¢FVÆVÖWG'’Ò6VÆbå÷6fU÷FVÆVÖWG'’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷W&F–öã×FVÆVÖWG'•ö÷W&F–öâÀ¢7F'FVEöç3×7F'FVEöç2À¢7FvW5ö×3×°¢¢¦F–7B‡FVÆVÖWG'•÷&W7FvW5ö×2÷"·Ò’À¢&ÖFW&–Æ—¦R#¢†ÖFW&–Æ—¦VEöç2ÒÖFW&–Æ—¦U÷7F'FVEöç2¢òóóÀ¢'fÆ–FFR#¢‡fÆ–FFVEöç2ÒÖFW&–Æ—¦VEöç2’òóóÀ¢ÒÀ¢'F–f7Eö–CÔæöæRÀ¢v–æF÷u÷&öÆSÖW‡V7FVE÷&öÆRÀ¢F6µ÷G—SÒ'&öGV7F–öå÷&VfÆ–v‡B"À¢6†÷Eö6÷VçCÖÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢&F6…ö6÷VçCÓÀ¢–çWE÷–ÆöEö'—FW3×7V&Ö—GFVE÷–ÆöEö'—FW2À¢÷WGWE÷–ÆöEö'—FW3ÓÀ¢ÖFW&–Æ—¦F–öå÷76W3ÓÀ¢66†U÷&V6öãÒ'&VfÆ–v‡B—77VW2&Æö6¶VBW‡÷'BæBW'6—7FVæ6R"À¢¢&WGW&âFööÅ&W7öç6R€¢7FGW3Ò&f–Åö7W'&VçB"À¢7VÖÖ'“Ò€¢b.yIþKª~š(Nj8iÊ®˜	®‹ø~ûÙÎ™zîš)‡¶ÆVâ‡&W÷'BævWB‚v—77VW2rÂµÒ’—Þš’ ¢.ûÙÎiÊ®ZûÎX{®8iÊ®hùKªN8iÊ®[»®z¸¾ZêŠêKªNhêR ¢’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢G&6Uö–C×7G"‡FVÆVÖWG'•²'FVÆVÖWG'•ö–B%Ò’À¢FF×°¢'&W÷'B#¢&W÷'BÀ¢&'F–f7Eö7&VFVB#¢fÇ6RÀ¢'fW'6–öåö7&VFVB#¢fÇ6RÀ¢'†Ç7…ö7&VFVB#¢fÇ6RÀ¢&†æFöfeö7&VFVB#¢fÇ6RÀ¢'VÆ—G’#¢VÆ—G’À¢'FVÆVÖWG'’#¢FVÆVÖWG'’À¢ÒÀ¢¢6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢6öÖÖ—E÷&WVW7E÷6†#Sc¢7G"ÂæöæRÒæöæP¢G&gEö6öÖÖ—Eö–G3¢F–7E·7G"Â7G%ÒÒ·Ð¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæS ¢G&gEö6öÖÖ—E÷–ÆöE÷6†#SbÒöG&gEö6öÖÖ—E÷–ÆöE÷6†#Sb€¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢G&gE÷&VfÆ–v‡Eö–C×7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6æöæ–6Å÷6†#ScÖ6æöæ–6Åö†6‚À¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢¢6öÖÖ—E÷&WVW7E÷6†#SbÒ6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢&G&gE÷&VfÆ–v‡Eö–B#¢G&gE÷&VfÆ–v‡Eö–BÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&f–ÆUöæÖR#¢f–ÆUöæÖRÀ¢'6W76–öâ#¢6VÆbå÷6W76–öåöf–ævW'&–çB€¢æÖW76RÂ&WVW7E÷6W76–öåö–@¢’À¢Ð¢¢¢7FvU÷G&6Uö–BÒb%E$2×¶6öÖÖ—E÷&WVW7E÷6†#Se³£#×Ò ¢G&gEö6öÖÖ—Eö–G2Ò°¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢€¢b$%B×·6†#Seö'—FW2‚†6öÖÖ—E÷&WVW7E÷6†#Sb²s¦§6öâr’æVæ6öFR‚’•³£e×Ò ¢’À¢&'F–f7Eö–B#¢€¢b$%B×·6†#Seö'—FW2‚†6öÖÖ—E÷&WVW7E÷6†#Sb²s§†Ç7‚r’æVæ6öFR‚’•³£e×Ò ¢’À¢&6W'F–f–6FUö–B#¢€¢b$4U%B×·6†#Seö'—FW2‚†6öÖÖ—E÷&WVW7E÷6†#Sb²s¦6W'F–f–6FRr’æVæ6öFR‚’•³£#×Ò ¢’À¢&†æFöfeö–B#¢€¢b$„äB×·6†#Seö'—FW2‚†6öÖÖ—E÷&WVW7E÷6†#Sb²s¦†æFöfbr’æVæ6öFR‚’•³£#×Ò ¢’À¢Ð¢&W&F–öåöwV&BÒ€¢6VÆbç7F÷&RæG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&W&F–öâ€¢æÖW76RÂ&ö¦V7Eö–BÂ7G"†G&gE÷&VfÆ–v‡Eö–B¢¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæP¢VÇ6R6öçFW‡FÆ–"æçVÆÆ6öçFW‡B‚¢¢2F†—2&ö6W72ÖÆö6ÂwV&B—2–çFVçF–öæÆÇ’æöâ×W'6—7FVçC¢f–ÇW&W0¢2GW&–ær6÷W&6Rf–ævW'&–çF–ær÷"v÷&¶&öö²&ö¦V7F–öâ&VÆV6R—@¢2–ÖÖVF–FVÇ’Âv†–ÆRF†RGW&&ÆR7&÷72×&ö6W726Æ–Ò&VÖ–ç2FVfW'&V@¢2VçF–ÂF†Rf—'7B'F–f7Bw&—FR&÷VæF'’à¢v—F‚&W&F–öåöwV&C ¢6÷W&6Uöf–ævW'&–çBÒ6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢6÷W&6Uö'F–f7Eö–BÀ¢¢&÷VæF'•ö†6‚Ò6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B¢66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢VF—Eö6Æ73ÔTD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢66÷S×°¢'F&vWG2#¢v÷&µö÷&FW%÷6æ6†÷BævWB‚'F&vWG2"ÂµÒ’À¢'&÷FV7FVE÷66÷R#¢v÷&µö÷&FW%÷6æ6†÷BævWB‚'&÷FV7FVE÷66÷R"ÂµÒ’À¢ÒÀ¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢’À¢¢VF—Eö66†Uö¶W’Ò6VÆbåö66†Uö¶W•ög&öÕö6ö×öæVçG2†66†Uö6ö×öæVçG2¢'F–f7G2Ò6VÆbç7F÷&RæÆ—7Eö'F–f7G2†æÖW76RÂ&ö¦V7Eö–B¢W†—7F–æuö6öÖÖ—Eö'F–f7C¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢–bG&gEö6öÖÖ—Eö–G3 ¢v—F‚6öçFW‡FÆ–"ç7W&W72…fÇVTW'&÷"“ ¢W†—7F–æuö6öÖÖ—Eö'F–f7BÂöW†—7F–æu÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢G&gEö6öÖÖ—Eö–G5²&'F–f7Eö–B%ÒÀ¢¢fW'6–öâÒ€¢7G"†W†—7F–æuö6öÖÖ—Eö'F–f7E²'fW'6–öâ%Ò¢–bW†—7F–æuö6öÖÖ—Eö'F–f7B—2æ÷BæöæP¢VÇ6Rö'F–f7E÷fW'6–öâ†'F–f7G2Â'7F÷'–&ö&B"¢¢&6UöæÖRÒF‚€¢6ÇVuöf–ÆVæÖR†f–ÆUöæÖR÷"b.jÚ>[ÈþXˆn™YÅ÷·fW'6–öçÒç†Ç7‚"¢’ç7FVÐ¢2f–æ—6‚F†RFWFW&Ö–æ—7F–2&ö¦V7F–öâæB—G2&V÷Vâ÷&÷VæB×G&— ¢26†V6·2&Vf÷&RW'6—7F–ærV—F†W"†ÆböbF†R6æöæ–6Âõ„Å5‚—"à¢v÷&¶&ööµö'—FW2ÒöW‡÷'Eö6ö×7E÷7F÷'–&ö&B‡7F÷'–&ö&BÂ&W÷'B¢26Æ–ÒöæÇ’gFW"WfW'’W&R6Æ7VÆF–öâÂ6÷W&6RÆöö·WÂfÆ–FF–öâÀ¢2fW'6–öâ6VÆV7F–öâæBv÷&¶&öö²&V÷Vâ÷&÷VæB×G&—6†V6²†276VBà¢2F†RæW‡B÷W&F–öâ—2F†Rf—'7BW'6—7FVçB'F–f7B6–FRVffV7Bà¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæS ¢76W'B6öÖÖ—E÷&WVW7E÷6†#Sb—2æ÷BæöæP¢76W'BG&gEö6öÖÖ—E÷–ÆöE÷6†#Sb—2æ÷BæöæP¢6Æ–Õ÷&W7VÇBÒ6VÆbç7F÷&Ræ6Æ–ÕöG&gE÷&VfÆ–v‡Eö6öÖÖ—B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢&WVW7E÷6†#ScÖ6öÖÖ—E÷&WVW7E÷6†#SbÀ¢–ÆöE÷6†#ScÖG&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢¢–b6Æ–Õ÷&W7VÇE²&6Æ–Õ÷7FGW2%ÒÓÒ&–å÷&öw&W72# ¢&—6RfÇVTW'&÷"‚&G&gE÷&VfÆ–v‡Eö6öÖÖ—Eö–å÷&öw&W72"¢–b6Æ–Õ÷&W7VÇE²&6Æ–Õ÷7FGW2%ÒÓÒ&6öç7VÖVB# ¢&—6RfÇVTW'&÷"‚&G&gE÷&VfÆ–v‡EöÇ&VG•ö6öç7VÖVB"¢&÷VæEöG&gE÷&VfÆ–v‡BÒ6÷’æFVW6÷’†6Æ–Õ÷&W7VÇE²'&V6÷&B%Ò¢G&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÒ7G"†6Æ–Õ÷&W7VÇE²&6Æ–Õ÷Fö¶Vâ%Ò¢ô5D•dUô4ôå5TÕD”ôåô4Ä”Õ2ç6WB€¢€¢¥ô5D•dUô4ôå5TÕD”ôåô4Ä”Õ2ævWB‚’À¢°¢&¶–æB#¢&G&gE÷&VfÆ–v‡B"À¢&æÖW76R#¢æÖW76RÀ¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢'&V6÷&Eö–B#¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢&6Æ–Õ÷Fö¶Vâ#¢G&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢ÒÀ¢¢¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ&6Æ–ÖVB"À¢&öw&W73×²¢¦G&gEö6öÖÖ—Eö–G2Â'fW'6–öâ#¢fW'6–öçÒÀ¢¢§6öå÷&V6÷&BÒ6VÆbç7F÷&Rç6fUö'F–f7Eö§6öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢f–ÆUöæÖSÖb'¶&6UöæÖWÒæ§6öâ"À¢'F–f7E÷G—SÒ'7F÷'–&ö&Eö§6öâ"À¢fW'6–öã×fW'6–öâÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢'F–f7Eö–CÖG&gEö6öÖÖ—Eö–G2ævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"’À¢ÖWFFF×°¢¢§7V66W76÷%öÖWFFFÀ¢¢¦6ö×7Eö6öçG&7EöWf–FVæ6R‚’À¢'&öGV7F–öå÷&VfÆ–v‡E÷7FGW2#¢$ôET5D”ôåõ$TdÄ”t…Eõ52À¢'&öGV7F–öå÷&VfÆ–v‡Eö—77VUö6÷VçB#¢ÆVâ‡&W÷'BævWB‚&—77VW2"ÂµÒ’’À¢&–æFWVæFVçEöVF—E÷7FGW2#¢%TäD”äuôTD•B"À¢&VF—E÷7FGW2#¢%TäD”äuôTD•B"À¢&—77VUö6÷VçB#¢æöæRÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uöf–ævW'&–çB#¢6÷W&6Uöf–ævW'&–çBÀ¢&&÷VæF'•ö†6‚#¢&÷VæF'•ö†6‚À¢''VÆW6WE÷fW'6–öâ#¢6VÆbå÷'VÆW6WE÷fW'6–öâÀ¢'fÆ–FF÷%÷fW'6–öâ#¢6VÆbå÷fÆ–FF÷%÷fW'6–öâ‚’À¢&—77VU÷6–væGW&W2#¢°¢·7G"†—77VRævWB‚&W'&÷%ö6öFR"’’Â7G"†—77VRævWB‚&Æö6F–öâ"’•Ð¢f÷"—77VR–â&W÷'BævWB‚&—77VW2"ÂµÒ¢ÒÀ¢'7F&ÆUö–FVçF—G•öÖæ–fW7E÷6†#Sb#¢6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2†–FVçF—G•öÖæ–fW7B¢’À¢'7F&ÆU÷6†÷E÷V–Eö6÷VçB#¢ÆVâ†–FVçF—G•öÖæ–fW7BævWB‚'6†÷E÷V–G2"ÂµÒ’’À¢'7F&ÆUö&F6…÷V–Eö6÷VçB#¢ÆVâ†–FVçF—G•öÖæ–fW7BævWB‚&&F6…÷V–G2"ÂµÒ’’À¢&G&gE÷&VfÆ–v‡Eö–B#¢G&gE÷&VfÆ–v‡Eö–BÀ¢&G&gEö6öÖÖ—E÷&WVW7E÷6†#Sb#¢6öÖÖ—E÷&WVW7E÷6†#SbÀ¢&G&gEö6öÖÖ—E÷–ÆöE÷6†#Sb#¢G&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb#¢€¢&÷VæEöG&gE÷&VfÆ–v‡BævWB‚'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb"¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæP¢VÇ6RæöæP¢’À¢ÒÀ¢¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæRæBG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ&6æöæ–6Å÷6fVB"À¢&öw&W73×°¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Åö§6öåöf–ævW'&–çB#¢§6öå÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢ÒÀ¢¢†Ç7…÷&V6÷&BÒ6VÆbç7F÷&Rç6fUö'F–f7Eö'—FW2€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&¶&ööµö'—FW2À¢f–ÆUöæÖSÖb'¶&6UöæÖWÒç†Ç7‚"À¢'F–f7E÷G—SÒ'7F÷'–&ö&B"À¢fW'6–öã×fW'6–öâÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6÷W&6Uö'F–f7Eö–C×6÷W&6Uö'F–f7Eö–BÀ¢'F–f7Eö–CÖG&gEö6öÖÖ—Eö–G2ævWB‚&'F–f7Eö–B"’À¢ÖWFFF×°¢¢§7V66W76÷%öÖWFFFÀ¢¢¦6ö×7Eö6öçG&7EöWf–FVæ6R‚’À¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Åö§6öåöf–ævW'&–çB#¢§6öå÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uöf–ævW'&–çB#¢6÷W&6Uöf–ævW'&–çBÀ¢&&÷VæF'•ö†6‚#¢&÷VæF'•ö†6‚À¢''VÆW6WE÷fW'6–öâ#¢6VÆbå÷'VÆW6WE÷fW'6–öâÀ¢'fÆ–FF÷%÷fW'6–öâ#¢6VÆbå÷fÆ–FF÷%÷fW'6–öâ‚’À¢'&öGV7F–öå÷&VfÆ–v‡Eö66†Uö¶W’#¢VF—Eö66†Uö¶W’À¢'&öGV7F–öå÷&VfÆ–v‡E÷7FGW2#¢$ôET5D”ôåõ$TdÄ”t…Eõ52À¢'&öGV7F–öå÷&VfÆ–v‡Eö—77VUö6÷VçB#¢ÆVâ‡&W÷'BævWB‚&—77VW2"ÂµÒ’’À¢&–æFWVæFVçEöVF—E÷7FGW2#¢%TäD”äuôTD•B"À¢&VF—E÷7FGW2#¢%TäD”äuôTD•B"À¢&—77VUö6÷VçB#¢æöæRÀ¢&—77VU÷6–væGW&W2#¢°¢·7G"†—77VRævWB‚&W'&÷%ö6öFR"’’Â7G"†—77VRævWB‚&Æö6F–öâ"’•Ð¢f÷"—77VR–â&W÷'BævWB‚&—77VW2"ÂµÒ¢ÒÀ¢'7F&ÆUö–FVçF—G•öÖæ–fW7E÷6†#Sb#¢6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2†–FVçF—G•öÖæ–fW7B¢’À¢'7F&ÆU÷6†÷E÷V–Eö6÷VçB#¢ÆVâ†–FVçF—G•öÖæ–fW7BævWB‚'6†÷E÷V–G2"ÂµÒ’’À¢'7F&ÆUö&F6…÷V–Eö6÷VçB#¢ÆVâ†–FVçF—G•öÖæ–fW7BævWB‚&&F6…÷V–G2"ÂµÒ’’À¢&G&gE÷&VfÆ–v‡Eö–B#¢G&gE÷&VfÆ–v‡Eö–BÀ¢&G&gEö6öÖÖ—E÷&WVW7E÷6†#Sb#¢6öÖÖ—E÷&WVW7E÷6†#SbÀ¢&G&gEö6öÖÖ—E÷–ÆöE÷6†#Sb#¢G&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb#¢€¢&÷VæEöG&gE÷&VfÆ–v‡BævWB‚'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb"¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæP¢VÇ6RæöæP¢’À¢ÒÀ¢¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæRæBG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ&'F–f7E÷—%÷6fVB"À¢&öw&W73×°¢&'F–f7Eö–B#¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&'F–f7Eöf–ævW'&–çB#¢†Ç7…÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢ÒÀ¢¢–b7V66W76÷%÷VæF–æs ¢V&Æ–6F–öåö÷væW"Ò7G"‡†Ç7…÷&V6÷&E²&'F–f7Eö–B%Ò¢§6öå÷&V6÷&BÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†§6öå÷&V6÷&E²&'F–f7Eö–B%Ò’À¢²'V&Æ–6F–öåö÷væW%ö'F–f7Eö–B#¢V&Æ–6F–öåö÷væW'ÒÀ¢¢†Ç7…÷&V6÷&BÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢V&Æ–6F–öåö÷væW"À¢²'V&Æ–6F–öåö÷væW%ö'F–f7Eö–B#¢V&Æ–6F–öåö÷væW'ÒÀ¢¢66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢VF—Eö6Æ73ÔTD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…BÀ¢'F–f7Eöf–ævW'&–çC×7G"‡†Ç7…÷&V6÷&E²&f–ævW'&–çB%Ò’À¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢66÷S×°¢'F&vWG2#¢v÷&µö÷&FW%÷6æ6†÷BævWB‚'F&vWG2"ÂµÒ’À¢'&÷FV7FVE÷66÷R#¢v÷&µö÷&FW%÷6æ6†÷BævWB‚'&÷FV7FVE÷66÷R"ÂµÒ’À¢ÒÀ¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"‡'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%Ò’À¢¢VF—Eö66†Uö¶W’Ò6VÆbåö66†Uö¶W•ög&öÕö6ö×öæVçG2†66†Uö6ö×öæVçG2¢†Ç7…÷&V6÷&BÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢²'&öGV7F–öå÷&VfÆ–v‡Eö66†Uö¶W’#¢VF—Eö66†Uö¶W—ÒÀ¢¢W'6—7FVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢6W'F–f–6FRÒ6VÆbå÷W'6—7EöVF—Eö6W'F–f–6FR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–C×†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢&W÷'C×&W÷'BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6W'F–f–6FUö–CÖG&gEö6öÖÖ—Eö–G2ævWB‚&6W'F–f–6FUö–B"’À¢¢†æFöfbÒ6VÆbå÷W'6—7E÷&öGV7F–öåö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢7F÷'–&ö&Eö'F–f7Eö–C×†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢'F–f7E÷&V6÷&C×†Ç7…÷&V6÷&BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW%÷6æ6†÷BÀ¢7FvU÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢&W—%÷6¶vUö–C×&W—%÷6¶vUö–BÀ¢V&Æ–6F–öå÷7FFSÒ'VæF–ær"–b7V66W76÷%÷VæF–ærVÇ6R&6öÖÖ—GFVB"À¢7V66W76÷%÷G&ç67F–öåö–CÕ÷7V66W76÷%÷G&ç67F–öåö–BÀ¢7V66W76÷%ö&6Uö'F–f7Eö–CÕ÷7V66W76÷%ö&6Uö'F–f7Eö–BÀ¢7V66W76÷%ö¶–æCÕ÷7V66W76÷%ö¶–æBÀ¢7V66W76÷%÷66÷Uö†6ƒÕ÷7V66W76÷%÷66÷Uö†6‚À¢†æFöfeö–CÖG&gEö6öÖÖ—Eö–G2ævWB‚&†æFöfeö–B"’À¢¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæRæBG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ&6W'F–f–6FUöæEö†æFöfe÷6fVB"À¢&öw&W73×°¢&6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&†æFöfeö–B#¢†æFöfe²&†æFöfeö–B%ÒÀ¢ÒÀ¢¢†Ç7…÷&V6÷&BÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢°¢'&öGV7F–öåö6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&†æFöfeö–B#¢†æFöfe²&†æFöfeö–B%ÒÀ¢ÒÀ¢¢§6öå÷&V6÷&BÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢°¢'&öGV7F–öåö6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&†æFöfeö–B#¢†æFöfe²&†æFöfeö–B%ÒÀ¢ÒÀ¢¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæRæBG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢76W'BG&gEö6öÖÖ—E÷–ÆöE÷6†#Sb—2æ÷BæöæP¢&V6÷fW'•÷7VÖÖ'’Ò€¢b.yIþh‰ZèÎh‰ûÙÇ·&W÷'BævWB‚w6†÷Eö6÷VçBrÂ—Þ™YÎûÙÎh¾i{n™[÷·7F÷'–&ö&BævWB‚wF÷FÅöGW&F–öârÂ—×2 ¢.ûÙÎyIþKª~š(Nj8˜	®‹ø~ûÙÎ[è^xºÎz¸¾ZêŠê ¢¢FVÆVÖWG'•ö&ÇVW&–çBÒ°¢'FVÆVÖWG'•ö–B#¢7FvU÷G&6Uö–BÀ¢&÷W&F–öâ#¢FVÆVÖWG'•ö÷W&F–öâÀ¢'v–æF÷u÷&öÆR#¢7G"€¢v÷&µö÷&FW%÷6æ6†÷BævWB‚'6W'fW%ö7F÷%÷&öÆR"¢÷"'&öGV7F–öâ ¢’À¢'F6µ÷G—R#¢€¢'&W—" ¢–bv÷&µö÷&FW%÷6æ6†÷BævWB‚'6W'fW%ö7F÷%÷&öÆR"¢ÓÒ'&W—" ¢VÇ6R'&öGV7F–öå÷&VfÆ–v‡B ¢’À¢&–çWEöf–ævW'&–çG2#¢6÷’æFVW6÷’€¢†æFöfe²&f–ævW'&–çG2%Ð¢’À¢'6†÷Eö6÷VçB#¢ÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢&–çWE÷–ÆöEö'—FW2#¢€¢–çB€¢‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB€¢&–çWE÷–ÆöEö'—FW2"Â ¢¢¢÷"7V&Ö—GFVE÷–ÆöEö'—FW0¢’À¢&÷WGWE÷–ÆöEö'—FW2#¢Ö‚€¢ÆVâ‡v÷&¶&ööµö'—FW2¢²ÆVâ…÷7F&ÆUö§6öåö'—FW2‡7F÷'–&ö&B’’À¢–çB€¢‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB€¢&÷WGWE÷–ÆöEö'—FW2"Â ¢¢’À¢’À¢&ffV7FVE÷6†÷Eö6÷VçB#¢–çB€¢‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB€¢&ffV7FVE÷6†÷Eö6÷VçB"Â ¢¢’À¢'&W6W'fVE÷6VÖçF–5ö6÷VçB#¢–çB€¢‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB€¢'&W6W'fVE÷6VÖçF–5ö6÷VçB"Â ¢¢’À¢&66†U÷&V6öâ#¢7G"€¢‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB‚&66†U÷&V6öâ"¢÷"&6ö×7B6æöæ–6ÂfÆ–FF–öã²æòGWÆ–6FR&ö×B÷"6VVFæ6R&F6‚vVæW&F–öâ ¢’À¢'7FvW5ö×2#¢°¢¢¦F–7B‡FVÆVÖWG'•÷&W7FvW5ö×2÷"·Ò’À¢&ÖFW&–Æ—¦R#¢€¢ÖFW&–Æ—¦VEöç2ÒÖFW&–Æ—¦U÷7F'FVEöç0¢¢òóóÀ¢'fÆ–FFR#¢‡fÆ–FFVEöç2ÒÖFW&–Æ—¦VEöç2¢òóóÀ¢'6W&–Æ—¦UöæE÷W'6—7B#¢€¢W'6—7FVEöç2ÒfÆ–FFVEöç0¢¢òóóÀ¢2F†RGW&&ÆRÆVFvW"w&—FR†2æ÷B7F'FVBâ&V6÷fW'¢2&V6÷&G2—B2âVæÖV7W&VB¦W&òÖÆVæwF‚&÷VæF'’&F†W ¢2F†â–çfVçF–ærF–Ö–ærgFW"&ö6W72FVF‚à¢&ÆVFvW"#¢ãÀ¢ÒÀ¢Ð¢6ö×ÆWF–öåö&ÇVW&–çBÒ°¢&'F–f7Eö–B#¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&f–ÆUöæÖR#¢f–ÆUöæÖRÀ¢'7FGW2#¢$ôET5D”ôåõ$TdÄ”t…Eõ52À¢'7VÖÖ'’#¢&V6÷fW'•÷7VÖÖ'’À¢'G&6Uö–B#¢7FvU÷G&6Uö–BÀ¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb#¢€¢&÷VæEöG&gE÷&VfÆ–v‡BævWB€¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb ¢¢’À¢Ð¢&V6÷fW'•öFW67&—F÷"Ò°¢&6öçG&7B#¢&G&gBÖ6öÖÖ—BÖFW66VæFçB×&V6÷fW'’×cS"À¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢&G&gE÷&VfÆ–v‡Eö–B#¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢'–ÆöE÷6†#Sb#¢G&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢'&WVW7E÷6†#Sb#¢6öÖÖ—E÷&WVW7E÷6†#SbÀ¢&—77Væ6UöWF†÷&—G•÷6†#Sb#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB€¢&6öÖÖ—Eö—77Væ6UöWF†÷&—G•÷6†#Sb ¢’À¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&f–ÆUöæÖR#¢f–ÆUöæÖRÀ¢&'F–f7Eö–B#¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&'F–f7Eöf–ævW'&–çB#¢†Ç7…÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢&'F–f7EöÖWF÷6†#Sb#¢6VÆbç7F÷&Rç&ö¦V7E÷7FFU÷6†#Sb€¢†Ç7…÷&V6÷&@¢’À¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Åö§6öåöf–ævW'&–çB#¢§6öå÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢&6æöæ–6ÅöÖWF÷6†#Sb#¢6VÆbç7F÷&Rç&ö¦V7E÷7FFU÷6†#Sb€¢§6öå÷&V6÷&@¢’À¢&6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢&6W'F–f–6FUö†Ö2#¢6W'F–f–6FRævWB‚&–çFVw&—G•ö†Ö2"’À¢&6W'F–f–6FU÷6†#Sb#¢6VÆbç7F÷&Rç&ö¦V7E÷7FFU÷6†#Sb€¢6W'F–f–6FP¢’À¢&†æFöfeö–B#¢†æFöfe²&†æFöfeö–B%ÒÀ¢&†æFöfeö†Ö2#¢†æFöfbævWB‚&–çFVw&—G•ö†Ö2"’À¢&†æFöfe÷6†#Sb#¢6VÆbç7F÷&Rç&ö¦V7E÷7FFU÷6†#Sb††æFöfb’À¢'FVÆVÖWG'’#¢FVÆVÖWG'•ö&ÇVW&–çBÀ¢&6ö×ÆWF–öâ#¢6ö×ÆWF–öåö&ÇVW&–çBÀ¢Ð¢&V6÷fW'•öFW67&—F÷%²&FW67&—F÷%÷6†#Sb%ÒÒ€¢6VÆbç7F÷&Rç&ö¦V7E÷7FFU÷6†#Sb‡&V6÷fW'•öFW67&—F÷"¢¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ'&V6÷fW'•÷&VG’"À¢&öw&W73×²'&V6÷fW'•öFW67&—F÷"#¢&V6÷fW'•öFW67&—F÷'ÒÀ¢¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂÆVFvW"“ ¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢–bv÷&µö÷&FW"ævWB‚'7FGW2"’Ò'fÆ–FF–ær# ¢6VÆbå÷7F'EöW†V7WF–öâ†‡V"Âv÷&µö÷&FW"¢–b¦W&õö—77VS ¢2ÆVv7’öÆö6Â6ÆÆW'2Ö’†fRÆV&æVBf–ÆVB6æF–FFR'¢26ÆÆ–ær6öÖÖ—BF—&V7FÇ’â6Æ÷6RF†÷6RW†7BG&gB66÷W2æ÷p¢2F†BF†R6ÖRv÷&²÷&FW"†2&öGV6VB6ÆVâ6æöæ–6Ã°¢2†—7F÷'’æBGFW&âÆV&æ–ær&VÖ–â–çF7Bà¢ÆVv7•öG&gE÷66÷W2Ò°¢6÷’æFVW6÷’‡66÷R¢f÷"66÷R–âÆVFvW"ævWB‚'66÷U÷7FFW2"ÂµÒ¢–b66÷RævWB‚&f–ÆUöæÖR"’ÓÒb&f–ÆVBÖG&gB×·v÷&µö÷&FW%ö–GÒ ¢æB66÷RævWB‚&÷Våö—77VUö–G2"¢æB–çB‡66÷RævWB‚&Æ7EöVF—E÷&÷VæB"Â’’Â–çB€¢ÆVFvW"ævWB‚'öÆ–7’"Â·Ò’ævWB‚&Ö…öWFõ÷&W—%÷&÷VæG2"Â2¢¢Ð¢f÷"G&gE÷66÷R–âÆVv7•öG&gE÷66÷W3 ¢6Æ÷7W&Uö¶W’Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'66÷Uö¶W’#¢G&gE÷66÷RævWB‚'66÷Uö¶W’"’À¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'&W7VÇB#¢'72"À¢Ð¢¢¢7F÷'–&ö&EövVçBæ–ævW7EöWfVçB€¢ÆVFvW"À¢°¢&WfVçEö–B#¢b$UeB×¶6Æ÷7W&Uö¶W•³£#×Ò"À¢'F–ÖW7F×#¢WF5÷7F×‚’À¢&W—6öFUö–B#¢G&gE÷66÷RævWB‚&W—6öFUö–B"’÷".XZŽšžyºâ"À¢&f–ÆUöæÖR#¢G&gE÷66÷U²&f–ÆUöæÖR%ÒÀ¢&f–ÆUöf–ævW'&–çB#¢6æöæ–6Åö†6‚À¢'6÷W&6U÷fW'6–öâ#¢&6ÆVâ×&öGV7F–öâÖ6æF–FFR"À¢&VF—Eö7–6ÆUö–B#¢G&gE÷66÷U²&VF—Eö7–6ÆUö–B%ÒÀ¢'F6µ÷G—R#¢'&öGV7F–öåöG&gE÷&VfÆ–v‡B"À¢&VF—Eö6Æ72#¢'&öGV7F–öåöG&gE÷&VfÆ–v‡B"À¢&VF—E÷&÷VæB#¢–çB†G&gE÷66÷U²&Æ7EöVF—E÷&÷VæB%Ò’²À¢'&W7VÇB#¢'72"À¢&6†V6¶VE÷6†÷Eö6÷VçB#¢ÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢&—77VW2#¢µÒÀ¢&6öÖÖ—E÷&VG’#¢G'VRÀ¢ÒÀ¢6†V6·ö–çEöWF†÷&—¦W#×6VÆbåöÆVFvW%ö6†V6·ö–çEöWF†÷&—¦W"€¢æÖW76RÂ&ö¦V7Eö–@¢’À¢¢–bæ÷Bç’€¢&÷rævWB‚&FVÆ—fW&&ÆUö–B"’ÓÒ†Ç7…÷&V6÷&E²&'F–f7Eö–B%Ð¢f÷"&÷r–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ¢“ ¢&ö¦V7EövVçBç&V6÷&EöFVÆ—fW&&ÆR€¢‡V"À¢°¢&FVÆ—fW&&ÆUö–B#¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢'F–ÖW7F×#¢WF5÷7F×‚’À¢&'F–f7E÷G—R#¢'7F÷'–&ö&B"À¢&f–ÆUöæÖR#¢†Ç7…÷&V6÷&E²&f–ÆUöæÖR%ÒÀ¢'fW'6–öâ#¢fW'6–öâÀ¢&f–ævW'&–çB#¢†Ç7…÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢2&öGV7F–öâ&VfÆ–v‡B6âæWfW"fÆ–FFR7F÷'–&ö&Bà¢2¶VWF†R&öGV6W"FVÆ—fW&&ÆR–âF†RW†—7F–ær66†VÖw0¢2G&gB7FFRVçF–Ââ–æFWVæFVçBVF—B6–vç2—Bà¢'7FGW2#¢&G&gB"À¢'V&Æ–6F–öå÷7FFR#¢€¢'VæF–ær"–b7V66W76÷%÷VæF–ærVÇ6R&6öÖÖ—GFVB ¢’À¢'6÷W&6U÷fW'6–öç2#¢·6÷W&6Uö'F–f7Eö–EÒ–b6÷W&6Uö'F–f7Eö–BVÇ6RµÒÀ¢ÒÀ¢¢–bv÷&µö÷&FW"ævWB‚'7FGW2"’Ò'fÆ–FF–ær# ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–BÂ'fÆ–FF–ær"¢VF—E÷&W7VÇBÒ6VÆbå÷&V6÷&EöVF—B€¢æÖW76SÖæÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢'F–f7E÷&V6÷&C×†Ç7…÷&V6÷&BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢&W÷'C×&W÷'BÀ¢ÆVFvW#ÖÆVFvW"À¢VF—Eö7–6ÆUö–CÖb$TB×·v÷&µö÷&FW%ö–GÒ"À¢VF—Eö6Æ73ÔTD•Eô4Ä55õ$ôET5D”ôåõ$TdÄ”t…BÀ¢VF—Eö66†Uö¶W“ÖVF—Eö66†Uö¶W’À¢¢&V6÷&FVEö—77VW2ÒVF—E÷&W7VÇBævWB‚&WfVçB"Â·Ò’ævWB‚&—77VW2"ÂµÒ¢&W6–GVÅö6÷VçBÒ7VÒ†—77VRævWB‚'7FGW2"’ÓÒ'&W6–GVÂ"f÷"—77VR–â&V6÷&FVEö—77VW2¢–çG&öGV6VEö6÷VçBÒ7VÒ†—77VRævWB‚'7FGW2"’ÓÒ&–çG&öGV6VB"f÷"—77VR–â&V6÷&FVEö—77VW2¢2&öGV7F–öâÖ’&V6÷&B—G2&VfÆ–v‡BWf–FVæ6RÂ'WB—B6ææ÷@¢2—77VRf–æÂfÆ–FF–öâ÷"Gfæ6RF†R7F÷'–&ö&Bv÷&²÷&FW"Fð¢276VBâöæÇ’F†R–æFWVæFVçBVF—BF‚&VÆ÷rÖ’FòF†Bà¢fÆ–FF–öå÷&W7VÇBÒ°¢'7FGW2#¢$ôET5D”ôåõ$TdÄ”t…Eõ52–b¦W&õö—77VRVÇ6R$ôET5D”ôåõ$TdÄ”t…Eôd”ÂÀ¢&f–æÅ÷fW&F–7B#¢fÇ6RÀ¢&vFU÷&W7VÇG2#¢övFU÷&W7VÇG2€¢v÷&µö÷&FW%²'&WV—&VE÷fÆ–FF–öåövFW2%ÒÂ&W÷'BævWB‚&—77VW2"ÂµÒ¢’À¢&f–ÆVEövFW2#¢°¢vFP¢f÷"vFRÂ&W7VÇB–âövFU÷&W7VÇG2€¢v÷&µö÷&FW%²'&WV—&VE÷fÆ–FF–öåövFW2%ÒÂ&W÷'BævWB‚&—77VW2"ÂµÒ¢’æ—FV×2‚¢–b&W7VÇBÒ'72 ¢ÒÀ¢'&W6–GVÅö—77VUö6÷VçB#¢&W6–GVÅö6÷VçBÀ¢&–çG&öGV6VEö—77VUö6÷VçB#¢–çG&öGV6VEö6÷VçBÀ¢Ð¢v÷&µö÷&FW%²'&öGV7F–öå÷&VfÆ–v‡B%ÒÒ6÷’æFVW6÷’‡fÆ–FF–öå÷&W7VÇB¢v÷&µö÷&FW%²&v—F–æuö–æFWVæFVçEöVF—B%ÒÒ&ööÂ€¢¦W&õö—77VRæBæ÷B7V66W76÷%÷VæF–æp¢¢–b¦W&õö—77VS ¢2&VÖ–â–âfÆ–FF–æs²6–vâÖöfb6ææ÷B'VâVçF–ÂF†RVF—@¢2v÷&²÷&FW"6öç7VÖW2F†RÖF6†–ær†æFöfbà¢v÷&µö÷&FW%²'7FGW2%ÒÒ'fÆ–FF–ær ¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ'fÆ–FF–ær ¢VÇ6S ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–BÂ&f–ÆVEö7W'&VçB"¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ&f–ÆVEö7W'&VçB ¢–bæ÷Bç’€¢&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfbævWB‚&†æFöfeö–B"¢f÷"&÷r–â‡V"ç6WFFVfVÇB‚&†æFöfg2"ÂµÒ¢“ ¢‡V%²&†æFöfg2%ÒæVæB€¢6VÆbåö†æFöfe÷&V6÷&E÷&ö¦V7F–öâ††æFöfb¢¢–çFW&æÅ÷&÷VæBÒv÷&µö÷&FW"ævWB‚&–çFW&æÅ÷&÷VæB"Â¢ÆVFvW%öç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢F÷væÆöE÷W&ÂÒ6VÆbç7F÷&Rç6–væVEöF÷væÆöE÷W&Â€¢æÖW76RÂ&ö¦V7Eö–BÂ†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÂ†Ç7…÷&V6÷&E²&f–ÆUöæÖR%Ð¢¢–b¦W&õö—77VS ¢7VÖÖ'’Ò€¢b.yIþh‰ZèÎh‰ûÙÇ·&W÷'BævWB‚w6†÷Eö6÷VçBrÂ—Þ™YÎûÙÎh¾i{n™[÷·7F÷'–&ö&BævWB‚wF÷FÅöGW&F–öârÂ—×2 ¢.ûÙÎyIþKª~š(Nj8˜	®‹ø~ûÙÎ[è^xºÎz¸¾ZêŠê ¢¢7FGW2Ò$ôET5D”ôåõ$TdÄ”t…Eõ50¢VÇ6S ¢7VÖÖ'’Òb.iÊ®˜	®‹ø~ûÙÎ™zîš)‡¶ÆVâ‡&W÷'BævWB‚v—77VW2rÂµÒ’—ÞšžûÙÎzÊÇ¶–çFW&æÅ÷&÷VæGÒó>‹Úâ ¢–b–çFW&æÅ÷&÷VæBãÒ3 ¢7VÖÖ'’³Ò.ûÙÎ™ÈŠhKˆ®k‹Ž˜xÞ[»¢ ¢7FGW2Ò&f–Åö7W'&VçB ¢7FvW5ö×2ÒF–7B‡FVÆVÖWG'•÷&W7FvW5ö×2÷"·Ò¢7FvW5ö×2ç6WFFVfVÇB€¢&ÖFW&–Æ—¦R"Â†ÖFW&–Æ—¦VEöç2ÒÖFW&–Æ—¦U÷7F'FVEöç2’òóó ¢¢7FvW5ö×2ç6WFFVfVÇB‚'fÆ–FFR"Â‡fÆ–FFVEöç2ÒÖFW&–Æ—¦VEöç2’òóó¢7FvW5ö×5²'6W&–Æ—¦UöæE÷W'6—7B%ÒÒ‡W'6—7FVEöç2ÒfÆ–FFVEöç2’òóó ¢7FvW5ö×5²&ÆVFvW"%ÒÒ†ÆVFvW%öç2ÒW'6—7FVEöç2’òóó ¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæRæBG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢Æ—fU÷&VfÆ–v‡BÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&G&gE÷&VfÆ–v‡G2"À¢&V6÷&Eö–C×7G"†G&gE÷&VfÆ–v‡Eö–B’À¢¢Æ—fU÷&öw&W72ÒÆ—fU÷&VfÆ–v‡BævWB‚&6öÖÖ—E÷&öw&W72"¢Æ—fUöFW67&—F÷"Ò€¢6÷’æFVW6÷’†Æ—fU÷&öw&W72ævWB‚'&V6÷fW'•öFW67&—F÷""’¢–b—6–ç7Fæ6R†Æ—fU÷&öw&W72ÂF–7B¢VÇ6RæöæP¢¢–bæ÷B—6–ç7Fæ6R†Æ—fUöFW67&—F÷"ÂF–7B’÷"æ÷B—6–ç7Fæ6R€¢Æ—fUöFW67&—F÷"ævWB‚'FVÆVÖWG'’"’ÂF–7@¢“ ¢&—6R'VçF–ÖTW'&÷"€¢&G&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&V6÷fW'•öFW67&—F÷%öÖ—76–ær ¢¢Æ—fUöFW67&—F÷%²'FVÆVÖWG'’%Õ²'7FvW5ö×2%ÒÒ6÷’æFVW6÷’€¢7FvW5ö×0¢¢Æ—fUöFW67&—F÷"ç÷‚&FW67&—F÷%÷6†#Sb"ÂæöæR¢Æ—fUöFW67&—F÷%²&FW67&—F÷%÷6†#Sb%ÒÒ€¢6VÆbç7F÷&Rç&ö¦V7E÷7FFU÷6†#Sb†Æ—fUöFW67&—F÷"¢¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ'FVÆVÖWG'•÷&VG’"À¢&öw&W73×²'&V6÷fW'•öFW67&—F÷"#¢Æ—fUöFW67&—F÷'ÒÀ¢¢FVÆVÖWG'’Ò6VÆbå÷6fU÷FVÆVÖWG'’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷W&F–öã×FVÆVÖWG'•ö÷W&F–öâÀ¢7F'FVEöç3×7F'FVEöç2À¢7FvW5ö×3×7FvW5ö×2À¢'F–f7Eö–C×7G"‡†Ç7…÷&V6÷&E²&'F–f7Eö–B%Ò’À¢FVÆVÖWG'•ö–C×7FvU÷G&6Uö–BÀ¢v–æF÷u÷&öÆS×7G"‡v÷&µö÷&FW%÷6æ6†÷BævWB‚'6W'fW%ö7F÷%÷&öÆR"’÷"'&öGV7F–öâ"’À¢F6µ÷G—SÒ'&W—""–bv÷&µö÷&FW%÷6æ6†÷BævWB‚'6W'fW%ö7F÷%÷&öÆR"’ÓÒ'&W—""VÇ6R'&öGV7F–öå÷&VfÆ–v‡B"À¢–çWEöf–ævW'&–çG3Ö†æFöfe²&f–ævW'&–çG2%ÒÀ¢6†÷Eö6÷VçCÖÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢&F6…ö6÷VçCÓÀ¢ffV7FVE÷6†÷Eö6÷VçCÖ–çB‚‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB‚&ffV7FVE÷6†÷Eö6÷VçB"Â’’À¢&W6W'fVE÷6VÖçF–5ö6÷VçCÖ–çB‚‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB‚'&W6W'fVE÷6VÖçF–5ö6÷VçB"Â’’À¢–çWE÷–ÆöEö'—FW3Ò€¢–çB‚‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB‚&–çWE÷–ÆöEö'—FW2"Â’¢÷"7V&Ö—GFVE÷–ÆöEö'—FW0¢’À¢÷WGWE÷–ÆöEö'—FW3ÖÖ‚€¢ÆVâ‡v÷&¶&ööµö'—FW2’²ÆVâ…÷7F&ÆUö§6öåö'—FW2‡7F÷'–&ö&B’’À¢–çB‚‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB‚&÷WGWE÷–ÆöEö'—FW2"Â’’À¢’À¢ÖFW&–Æ—¦F–öå÷76W3ÓÀ¢66†U÷&V6öã×7G"‚‡FVÆVÖWG'•öÖWG&–72÷"·Ò’ævWB‚&66†U÷&V6öâ"’÷"&6ö×7B6æöæ–6ÂfÆ–FF–öã²æòGWÆ–6FR&ö×B÷"6VVFæ6R&F6‚vVæW&F–öâ"’À¢¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢–bæ÷Bç’€¢&÷rævWB‚'FVÆVÖWG'•ö–B"’ÓÒFVÆVÖWG'’ævWB‚'FVÆVÖWG'•ö–B"¢f÷"&÷r–â‡V"ç6WFFVfVÇB‚'W&f÷&Öæ6U÷G&6W2"ÂµÒ¢“ ¢‡V%²'W&f÷&Öæ6U÷G&6W2%ÒæVæB†6÷’æFVW6÷’‡FVÆVÖWG'’’¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæRæBG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢6VÆbç7F÷&RçWFFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—E÷&öw&W72€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†G&gE÷&VfÆ–v‡Eö–B’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢7FvSÒ'&ö¦V7E÷7FFUöæE÷FVÆVÖWG'•÷6fVB"À¢&öw&W73×²'FVÆVÖWG'•ö–B#¢FVÆVÖWG'•²'FVÆVÖWG'•ö–B%×ÒÀ¢¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æ÷BæöæS ¢–bæ÷BG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶Vã ¢&—6R'VçF–ÖTW'&÷"‚&G&gE÷&VfÆ–v‡Eö6öÖÖ—Eö6Æ–ÕöÖ—76–ær"¢6öç7VÖVEö'•ö6öÖÖ—BÒ°¢&6öç7VÖVEöB#¢WF5÷7F×‚’À¢&'F–f7Eö–B#¢†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'6÷W&6Uö'F–f7Eö–B#¢6÷W&6Uö'F–f7Eö–BÀ¢&f–ÆUöæÖR#¢f–ÆUöæÖRÀ¢'7FGW2#¢7FGW2À¢'7VÖÖ'’#¢7VÖÖ'’À¢'G&6Uö–B#¢FVÆVÖWG'•²'FVÆVÖWG'•ö–B%ÒÀ¢&6öÖÖ—E÷–ÆöE÷6†#Sb#¢G&gEö6öÖÖ—E÷–ÆöE÷6†#SbÀ¢&—77Væ6UöWF†÷&—G•÷6†#Sb#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB€¢&6öÖÖ—Eö—77Væ6UöWF†÷&—G•÷6†#Sb ¢’À¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB€¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb ¢’À¢&†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’††æFöfb’À¢&6W'F–f–6FR#¢6VÆbåö6W'F–f–6FU÷7VÖÖ'’†6W'F–f–6FR’À¢Ð¢&÷VæEöG&gE÷&VfÆ–v‡BÒ6VÆbç7F÷&Ræ6ö×ÆWFUöG&gE÷&VfÆ–v‡Eö6öÖÖ—B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†&÷VæEöG&gE÷&VfÆ–v‡E²&G&gE÷&VfÆ–v‡Eö–B%Ò’À¢6Æ–Õ÷Fö¶VãÖG&gEö6öÖÖ—Eö6Æ–Õ÷Fö¶VâÀ¢6öç7VÖVEö'•ö6öÖÖ—CÖ6öç7VÖVEö'•ö6öÖÖ—BÀ¢¢&WGW&âFööÅ&W7öç6R€¢7FGW3×7FGW2À¢7VÖÖ'“×7VÖÖ'’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–C×†Ç7…÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢F÷væÆöE÷W&ÃÖF÷væÆöE÷W&ÂÀ¢G&6Uö–C×FVÆVÖWG'•²'FVÆVÖWG'•ö–B%ÒÀ¢FF×°¢&'F–f7B#¢†Ç7…÷&V6÷&BÀ¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢§6öå÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢&†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’††æFöfb’À¢&†æFöfeö'F–f7Eö–B#¢†æFöfe²&†æFöfeö–B%ÒÀ¢&6W'F–f–6FR#¢6VÆbåö6W'F–f–6FU÷7VÖÖ'’†6W'F–f–6FR’À¢'FVÆVÖWG'•ö–B#¢FVÆVÖWG'•²'FVÆVÖWG'•ö–B%ÒÀ¢'FVÆVÖWG'’#¢FVÆVÖWG'’À¢'fÆ–FF–öâ#¢fÆ–FF–öå÷&W7VÇBÀ¢&VF—B#¢VF—E÷&W7VÇBÀ¢'&W÷'B#¢&W÷'BÀ¢&–FVçF—G•öÖæ–fW7B#¢–FVçF—G•öÖæ–fW7BÀ¢&6ö×7Eö6öçG&7B#¢6ö×7Eö6öçG&7EöWf–FVæ6R‚’À¢&G&gE÷&VfÆ–v‡B#¢€¢æöæP¢–b&÷VæEöG&gE÷&VfÆ–v‡B—2æöæP¢VÇ6R°¢&G&gE÷&VfÆ–v‡Eö–B#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB‚&G&gE÷&VfÆ–v‡Eö–B"’À¢&6æF–FFU÷6†#Sb#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB‚&6æF–FFU÷6†#Sb"’À¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB€¢'66VæUö6÷fW&vUöÖæ–fW7E÷6†#Sb ¢’À¢'VÆ—G’#¢&÷VæEöG&gE÷&VfÆ–v‡BævWB‚'VÆ—G’"’À¢Ð¢’À¢&æW‡Eö7F–öâ#¢&–æFWVæFVçEöVF—B"–b¦W&õö—77VRVÇ6R‚'W7G&VÕ÷&V'V–ÆB"–b–çFW&æÅ÷&÷VæBãÒ2VÇ6R'&W—%öÆÅö—77VW5÷F†Vå÷&V6öÖÖ—B"’À¢ÒÀ¢ ¢öfVæ6U÷&öÆUö÷W&F–öà¢FVb6öÖÖ—E÷7F÷'–&ö&EöFVÇF€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢&6Uö'F–f7Eö–C¢7G"À¢W‡V7FVEö&6Uö6æöæ–6Å÷6†#Sc¢7G"À¢W‡V7FVEö&6U÷&uöf–ævW'&–çC¢7G"À¢66÷U÷F&vWC¢7G"À¢ffV7FVE÷6†÷E÷V–G3¢Æ—7E·7G%ÒÀ¢ÆVgEö&÷VæF'•÷6†÷E÷V–C¢7G"ÂæöæRÀ¢&–v‡Eö&÷VæF'•÷6†÷E÷V–C¢7G"ÂæöæRÀ¢&WÆ6VÖVçE÷6†÷G3¢Æ—7E¶F–7E·7G"Âç•ÕÒÀ¢F÷öÆWfVÅ÷WFFW3¢F–7E·7G"Âç•ÒÂæöæRÀ¢f–ÆUöæÖS¢7G"À¢Wf–FVæ6U÷WFFW3¢F–7E·7G"Âç•ÒÂæöæRÒæöæRÀ¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢&WÆ6VÖVçEö&F6†W3¢Æ—7E¶F–7E·7G"Âç•ÕÒÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢""$ÖW&vRöæR&öGV7F–öâÖWF†÷&—¦VB&ævRæB'VâF†RVæ6†ævVBgVÆÂvFW2â""  ¢6VÆbå÷&WV—&U÷'VçF–ÖR‚¢7F'FVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢7WÆ–VEöÆVv7•ö&F6…ö6÷VçBÒÆVâ‡&WÆ6VÖVçEö&F6†W2÷"µÒ¢&÷VæFVE÷&WVW7BÒ7F÷'–&ö&DFVÇF&WVW7D–çWBæÖöFVÅ÷fÆ–FFR€¢°¢&ffV7FVE÷6†÷E÷V–G2#¢ffV7FVE÷6†÷E÷V–G2À¢'&WÆ6VÖVçE÷6†÷G2#¢&WÆ6VÖVçE÷6†÷G2À¢'&WÆ6VÖVçEö&F6†W2#¢&WÆ6VÖVçEö&F6†W2÷"µÒÀ¢&Wf–FVæ6U÷WFFW2#¢Wf–FVæ6U÷WFFW2÷"·ÒÀ¢Ð¢¢&÷VæFVE÷–ÆöBÒ&÷VæFVE÷&WVW7BæÖöFVÅöGV×†ÖöFSÒ'—F†öâ"¢ffV7FVE÷6†÷E÷V–G2Ò&÷VæFVE÷–ÆöE²&ffV7FVE÷6†÷E÷V–G2%Ð¢&WÆ6VÖVçE÷6†÷G2Ò&÷VæFVE÷–ÆöE²'&WÆ6VÖVçE÷6†÷G2%Ð¢2F†RG—VB&÷VæF'’66WG2F†RöÆB&ÖWFW"ÂF†VâW&6W2—BâF†—0¢26÷VçB—2Wf–FVæ6Röb6ö×F–&–Æ—G’–çWBÂæWfW"vVæW&FVB÷WGWBà¢&WÆ6VÖVçEö&F6†W2Ò&÷VæFVE÷–ÆöE²'&WÆ6VÖVçEö&F6†W2%Ð¢Wf–FVæ6U÷WFFW2Ò&÷VæFVE÷–ÆöE²&Wf–FVæ6U÷WFFW2%Ð¢'VçF–ÖU÷FW7Eö6W'F–f–6FRÒ6VÆbå÷&WV—&U÷'VçF–ÖU÷FW7Eö6W'F–f–6FR†æÖW76RÂ&ö¦V7Eö–B¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢6VÆbåö76W'E÷7F÷'–&ö&EöWF†÷&—G’‡v÷&µö÷&FW"Â'&öGV7F–öâ"¢–bv÷&µö÷&FW"ævWB‚&÷W&F–öâ"’æ÷B–â²'7F÷'–&ö&E÷&VvVæW&FR"Â'7F÷'–&ö&Eö6†ævR'Ó ¢&—6RfÇVTW'&÷"€¢&FVÇF÷v÷&µö÷&FW%ö–çfÆ–C¢W6R7F÷'–&ö&E÷&VvVæW&FR÷"7F÷'–&ö&Eö6†ævR&öGV7F–öâv÷&²÷&FW" ¢¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ†æÖW76RÂv÷&µö÷&FW"Â&WVW7E÷6W76–öåö–B¢6VÆbåöVç7W&Uö6åöW†V7WFR‡v÷&µö÷&FW"¢66÷U÷F&vWBÒ7G"‡66÷U÷F&vWB÷"""’ç7G&—‚¢WF†÷&—¦VE÷F&vWG2Ò·7G"‡fÇVR’ç7G&—‚’f÷"fÇVR–âv÷&µö÷&FW"ævWB‚'F&vWG2"ÂµÒ—Ð¢–bæ÷B66÷U÷F&vWB÷"66÷U÷F&vWBæ÷B–âWF†÷&—¦VE÷F&vWG3 ¢&—6RfÇVTW'&÷"‚&FVÇF÷66÷U÷VæWF†÷&—¦VC¢66÷U÷F&vWB×W7BW†7FÇ’ÖF6‚v÷&²Ö÷&FW"F&vWB" ¢7F÷'–&ö&BÂ&6U÷&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ&6Uö'F–f7Eö–@¢¢ÆöFVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢&6Uö6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢–bW‡V7FVEö&6Uö6æöæ–6Å÷6†#SbÒ&6Uö6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"‚&6æöæ–6Åö†6…ö6öæfÆ–7C¢W‡V7FVB&6R6æöæ–6ÂFöW2æ÷BÖF6‚"¢–bW‡V7FVEö&6U÷&uöf–ævW'&–çBÒ7G"†&6U÷&V6÷&BævWB‚&f–ævW'&–çB"’÷"""“ ¢&—6RfÇVTW'&÷"‚&'F–f7E÷&uöf–ævW'&–çEöÖ—6ÖF6ƒ¢W‡V7FVB&6R'—FW2Fòæ÷BÖF6‚"¢ÖWFFFÒ&6U÷&V6÷&BævWB‚&ÖWFFF"’÷"·Ð¢–bç’€¢ÖWFFFævWB†¶W’¢f÷"¶W’–â€¢'7V66W76÷%ö'F–f7Eö–B"À¢'F6…÷7V66W76÷%ö'F–f7Eö–B"À¢&FVÇF÷7V66W76÷%ö'F–f7Eö–B"À¢'&W—%÷7V66W76÷%ö'F–f7Eö–B"À¢¢“ ¢&—6RfÇVTW'&÷"‚&6æöæ–6Åö†6…ö6öæfÆ–7C¢&6R6æöæ–6ÂÇ&VG’†26öÖÖ—GFVB7V66W76÷"" ¢–FVçF—G•÷7F÷'–&ö&BÂ–FVçF—G•öÖæ–fW7BÒVç7W&U÷6†÷E÷V–G2‡7F÷'–&ö&B¢–FVçF—F–W2Ò–FVçF—G•öÖæ–fW7BævWB‚'6†÷E÷V–G2"ÂµÒ¢V–E÷Fõ÷&÷rÒ°¢7G"‡&÷rævWB‚'6†÷E÷V–B"’“¢&÷rf÷"&÷r–â–FVçF—F–W2–b—6–ç7Fæ6R‡&÷rÂF–7B¢Ð¢&WVW7FVE÷V–G2Ò·7G"‡fÇVR’ç7G&—‚’f÷"fÇVR–âffV7FVE÷6†÷E÷V–G5Ð¢–bæ÷B&WVW7FVE÷V–G2÷"ç’‡V–Bæ÷B–âV–E÷Fõ÷&÷rf÷"V–B–â&WVW7FVE÷V–G2“ ¢&—6RfÇVTW'&÷"‚&FVÇF÷66÷UöÖ—76–æs¢öæR÷"Ö÷&RffV7FVB6†÷E÷V–G2&R'6VçBg&öÒF†R&6R"¢÷&F–æÇ2Ò¶–çB‡V–E÷Fõ÷&÷u·V–EÕ²&÷&F–æÂ%Ò’f÷"V–B–â&WVW7FVE÷V–G5Ð¢–b÷&F–æÇ2ÒÆ—7B‡&ævR†÷&F–æÇ5³ÒÂ÷&F–æÇ5²ÓÒ²’“ ¢&—6RfÇVTW'&÷"‚&FVÇF÷66÷Uöæöæ6öçF–wV÷W3¢ffV7FVB6†÷E÷V–G2×W7B&RöæR÷&FW&VB&ævR"¢7GVÅöÆVgBÒ€¢7G"†–FVçF—F–W5¶÷&F–æÇ5³ÒÒÕ²'6†÷E÷V–B%Ò’–b÷&F–æÇ5³ÒâVÇ6RæöæP¢¢7GVÅ÷&–v‡BÒ€¢7G"†–FVçF—F–W5¶÷&F–æÇ5²ÓÒ²Õ²'6†÷E÷V–B%Ò¢–b÷&F–æÇ5²ÓÒ²ÂÆVâ†–FVçF—F–W2¢VÇ6RæöæP¢¢–bÆVgEö&÷VæF'•÷6†÷E÷V–BÒ7GVÅöÆVgB÷"&–v‡Eö&÷VæF'•÷6†÷E÷V–BÒ7GVÅ÷&–v‡C ¢&—6RfÇVTW'&÷"€¢&FVÇFö&÷VæF'•öÖ—6ÖF6ƒ¢ÆVgB÷&–v‡B&÷VæF'’6†÷E÷V–G2Fòæ÷BÖF6‚F†RW†7B&6RæV–v†&÷'2 ¢ ¢æ÷&ÖÆ—¦VE÷66÷RÒ&Rç7V"‡"%Ç2²"Â""Â66÷U÷F&vWB’æ66VföÆB‚¢&÷FV7FVE÷66÷RÒÆ—7B‡v÷&µö÷&FW"ævWB‚'&÷FV7FVE÷66÷R"’÷"µÒ¢ffV7FVE÷&÷w2Ò–FVçF—G•÷7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ•¶÷&F–æÇ5³Ò¢÷&F–æÇ5²ÓÒ²Ð¢ffV7FVEöF—7Æ•ö–G2Ò°¢7G"‡&÷rævWB‚.™YÎZKN{ÉnXûr"’÷"""’ç7G&—‚¢f÷"&÷r–âffV7FVE÷&÷w0¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢Ð¢&6U÷&WfVçF–öâÒ–FVçF—G•÷7F÷'–&ö&BævWB‚'&WfVçF–öåöWf–FVæ6R"’÷"·Ð¢ffV7FVEöWf–FVæ6RÒ°¢&Æö6³¢°¢&÷p¢f÷"&÷r–â&6U÷&WfVçF–öâævWB†&Æö6²ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB7G"‡&÷rævWB‚'6†÷Eö–B"’÷"""’ç7G&—‚’–âffV7FVEöF—7Æ•ö–G0¢Ð¢f÷"&Æö6²–â‚'6†÷E÷Æâ"Â'W&f÷&Öæ6Uö6†–ç2"Â'66VæU÷7FFUöÆVFvW""¢–b—6–ç7Fæ6R†&6U÷&WfVçF–öâÂF–7B¢Ð¢&6U÷66÷Uö'—FW2ÒÆVâ€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'6†÷G2#¢ffV7FVE÷&÷w2À¢'&WfVçF–öåöWf–FVæ6R#¢ffV7FVEöWf–FVæ6RÀ¢Ð¢¢¢&÷VæFVE÷&WVW7Eö'—FW2ÒÆVâ…÷7F&ÆUö§6öåö'—FW2†&÷VæFVE÷–ÆöB’¢Ö†–×VÕöFVÇFö'—FW2ÒÖ‚ƒ#‚¢#BÂ&6U÷66÷Uö'—FW2¢B¢–b&÷VæFVE÷&WVW7Eö'—FW2âÖ†–×VÕöFVÇFö'—FW3 ¢&—6RfÇVTW'&÷"€¢&FVÇFöW‡ç6–öå÷&F–õöW†6VVFVC¢&WVW7B—2Æ&vW"F†âf÷W"F–ÖW2F†RffV7FVB ¢b&&6R66÷R‡¶&÷VæFVE÷&WVW7Eö'—FW7Óç¶Ö†–×VÕöFVÇFö'—FW7Ò’ ¢¢6VÆbåö76W'EöFVÇF÷66÷Uö&–æF–ær‡66÷U÷F&vWBÂffV7FVE÷&÷w2Â&WVW7FVE÷V–G2¢ffV7FVE÷66VæW2Ò°¢7G"‡&÷rævWB‚.YË®išþ‹ùî{ºÞh
t”B"’÷"""’ç7G&—‚’æ66VföÆB‚¢f÷"&÷r–âffV7FVE÷&÷w0¢–b—6–ç7Fæ6R‡&÷rÂF–7B’æB7G"‡&÷rævWB‚.YË®išþ‹ùî{ºÞh
t”B"’÷"""’ç7G&—‚¢Ð¢ffV7FVEöW—6öFW2Ò°¢7G"‡&÷rævWB‚&W—6öFUö–B"’÷"""’ç7G&—‚’æ66VföÆB‚¢f÷"&÷r–âffV7FVE÷&÷w0¢–b—6–ç7Fæ6R‡&÷rÂF–7B’æB7G"‡&÷rævWB‚&W—6öFUö–B"’÷"""’ç7G&—‚¢Ð¢f÷"&÷FV7FVB–â&÷FV7FVE÷66÷S ¢æ÷&ÖÆ—¦VBÒ&Rç7V"‡"%Ç2²"Â""Â7G"‡&÷FV7FVB’’æ66VföÆB‚¢–bæ÷&ÖÆ—¦VB–â²&ÆÂ"Â.XZŽ˜:‚"Â.XZŽŠ‚"Â.ZèÎi[NXˆn™YÂ'Ó ¢&—6RfÇVTW'&÷"†b'&÷FV7FVE÷66÷U÷f–öÆF–öã¢·&÷FV7FVGÒ"¢–bæ÷&ÖÆ—¦VE÷66÷RæB€¢æ÷&ÖÆ—¦VBÓÒæ÷&ÖÆ—¦VE÷66÷P¢÷"æ÷&ÖÆ—¦VE÷66÷R–âæ÷&ÖÆ—¦V@¢÷"æ÷&ÖÆ—¦VB–âæ÷&ÖÆ—¦VE÷66÷P¢“ ¢&—6RfÇVTW'&÷"†b'&÷FV7FVE÷66÷U÷f–öÆF–öã¢·&÷FV7FVGÒ"¢–bæ÷&ÖÆ—¦VBç7F'G7v—F‚‚'66VæS¢"’æBæ÷&ÖÆ—¦VE³c¥Ò–âffV7FVE÷66VæW3 ¢&—6RfÇVTW'&÷"†b'&÷FV7FVE÷66÷U÷f–öÆF–öã¢·&÷FV7FVGÒ"¢–bæ÷&ÖÆ—¦VBç7F'G7v—F‚‚&W—6öFS¢"’æBæ÷&ÖÆ—¦VE³ƒ¥Ò–âffV7FVEöW—6öFW3 ¢&—6RfÇVTW'&÷"†b'&÷FV7FVE÷66÷U÷f–öÆF–öã¢·&÷FV7FVGÒ"¢f÷"–æFW‚–â&ævR†Ö‚†ÆVâ†ffV7FVE÷&÷w2’ÂÆVâ‡&WÆ6VÖVçE÷6†÷G2’’“ ¢&Vf÷&RÒffV7FVE÷&÷w5¶–æFW…Ò–b–æFW‚ÂÆVâ†ffV7FVE÷&÷w2’VÇ6RæöæP¢gFW"Ò&WÆ6VÖVçE÷6†÷G5¶–æFW…Ò–b–æFW‚ÂÆVâ‡&WÆ6VÖVçE÷6†÷G2’VÇ6RæöæP¢–b&Vf÷&R—2æ÷BæöæRæBgFW"—2æ÷BæöæS ¢6ö×&—6öåögFW"ÒF–7B†gFW"¢f÷"–FVçF—G•öf–VÆB–â‚'6†÷E÷V–B"Â&W—6öFUö–B"Â'66VæU÷V–B"Â.YË®išþ‹ùî{ºÞh
t”B"“ ¢–b6ö×&—6öåögFW"ævWB†–FVçF—G•öf–VÆB’–â„æöæRÂ""“ ¢6ö×&—6öåögFW%¶–FVçF—G•öf–VÆEÒÒ&Vf÷&RævWB†–FVçF—G•öf–VÆB¢6†ævVEöf–VÆG2Ò6†ævVE÷6†÷Eöf–VÆG2†&Vf÷&RÂ6ö×&—6öåögFW"¢VÇ6S ¢&÷rÒ&Vf÷&R÷"gFW"÷"·Ð¢6†ævVEöf–VÆG2Ò6WB‡&÷r’Â².™YÎZKN{ÉnXûr'Ð¢6†÷Eö–BÒ7G"€¢†&Vf÷&R÷"·Ò’ævWB‚.™YÎZKN{ÉnXûr"¢÷"†gFW"÷"·Ò’ævWB‚.™YÎZKN{ÉnXûr"¢÷"b'÷6—F–öâ×¶–æFW‚²Ò ¢¢f÷"f–VÆB–â6÷'FVB†6†ævVEöf–VÆG2“ ¢†—BÒ6VÆbå÷&÷FV7FVE÷66÷Uö†—B‡&÷FV7FVE÷66÷RÂ6†÷Eö–BÂf–VÆB¢–b†—B—2æ÷BæöæS ¢&—6RfÇVTW'&÷"€¢b'&÷FV7FVE÷66÷U÷f–öÆF–öã¢·6†÷Eö–GÒç¶f–VÆGÒÖF6†W2¶†—B'Ò ¢¢66÷Uö6†V6¶VEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚ ¢ÖW&vVBÂFVÇFöÖæ–fW7BÒÖW&vU÷7F÷'–&ö&EöFVÇF€¢7F÷'–&ö&BÀ¢ffV7FVE÷6†÷E÷V–G3×&WVW7FVE÷V–G2À¢&WÆ6VÖVçE÷6†÷G3×&WÆ6VÖVçE÷6†÷G2À¢&WÆ6VÖVçEö&F6†W3×&WÆ6VÖVçEö&F6†W2À¢Wf–FVæ6U÷WFFW3ÖWf–FVæ6U÷WFFW2À¢F÷öÆWfVÅ÷WFFW3×F÷öÆWfVÅ÷WFFW2À¢¢FVÇFöÖæ–fW7E²&F—66&FVEöÆVv7•÷&WÆ6VÖVçEö&F6…ö6÷VçB%ÒÒ€¢7WÆ–VEöÆVv7•ö&F6…ö6÷Vç@¢¢ÖW&vVE÷6†÷G5÷fÇVRÒÖW&vVBævWB‚'6†÷G2"ÂµÒ¢ÖW&vVEöffV7FVE÷&÷w2ÒÖW&vVE÷6†÷G5÷fÇVU°¢÷&F–æÇ5³Ò¢÷&F–æÇ5³Ò²ÆVâ‡&WÆ6VÖVçE÷6†÷G2¢Ð¢6VÆbåö76W'EöFVÇF÷66÷Uö&–æF–ær€¢66÷U÷F&vWBÂÖW&vVEöffV7FVE÷&÷w2Â&WVW7FVE÷V–G0¢¢&W&VEö–FVçF—G•öÖæ–fW7BÒ7F÷'–&ö&Eö–FVçF—G•öÖæ–fW7B†ÖW&vVB¢ÖW&vVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢&W÷'BÒfÆ–FFU÷7F÷'–&ö&BçfÆ–FFU÷&ö¦V7B†ÖW&vVBÂÖöFSÒ&VF—B"¢fÆ–FFVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢ÖFW&–Æ—¦Uö×2ÒfÆöB€¢FVÇFöÖæ–fW7BævWB‚&–çFW&æÅ÷7FvUö×2"Â·Ò’ævWB‚&ÖFW&–Æ—¦R"Âã¢¢ÖW&vU÷F÷FÅö×2ÒfÆöB€¢FVÇFöÖæ–fW7BævWB‚&–çFW&æÅ÷7FvUö×2"Â·Ò’ævWB‚&ÖW&vU÷F÷FÂ"Âã¢¢&W7FvW2Ò°¢&&6UöÆöB#¢†ÆöFVEöç2Ò7F'FVEöç2’òóóÀ¢'66÷U÷fW&–g’#¢‡66÷Uö6†V6¶VEöç2ÒÆöFVEöç2’òóóÀ¢&FVÇFöÖW&vR#¢Ö‚ƒãÂÖW&vU÷F÷FÅö×2ÒÖFW&–Æ—¦Uö×2’À¢&ÖFW&–Æ—¦R#¢ÖFW&–Æ—¦Uö×2À¢'fÆ–FFR#¢‡fÆ–FFVEöç2ÒÖW&vVEöç2’òóóÀ¢Ð¢FVÇFö–çWEö'—FW2Ò–çB†FVÇFöÖæ–fW7BævWB‚&FVÇFö–çWEö'—FW2"’÷"¢ÖFW&–Æ—¦VEö'—FW2Ò–çB†FVÇFöÖæ–fW7BævWB‚&ÖFW&–Æ—¦VEö6æöæ–6Åö'—FW2"’÷"¢–b&W÷'BævWB‚&—77VW2"“ ¢FVÆVÖWG'’Ò6VÆbå÷6fU÷FVÆVÖWG'’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷W&F–öãÒ&6öÖÖ—E÷7F÷'–&ö&EöFVÇF"À¢7F'FVEöç3×7F'FVEöç2À¢7FvW5ö×3×&W7FvW2À¢'F–f7Eö–CÔæöæRÀ¢v–æF÷u÷&öÆSÒ'&öGV7F–öâ"À¢F6µ÷G—SÒ'&öGV7F–öå÷&VfÆ–v‡B"À¢–çWEöf–ævW'&–çG3×°¢&&6Uö6æöæ–6Â#¢&6Uö6æöæ–6Åö†6‚À¢&&6U÷&r#¢W‡V7FVEö&6U÷&uöf–ævW'&–çBÀ¢'66÷R#¢6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2‡&WVW7FVE÷V–G2’’À¢ÒÀ¢6†÷Eö6÷VçCÖÆVâ†ÖW&vVBævWB‚'6†÷G2"ÂµÒ’’À¢&F6…ö6÷VçCÓÀ¢ffV7FVE÷6†÷Eö6÷VçCÖÆVâ‡&WVW7FVE÷V–G2’À¢&W6W'fVE÷6VÖçF–5ö6÷VçCÖ–çB†FVÇFöÖæ–fW7BævWB‚'VçF÷V6†VE÷6†÷Eö6÷VçB"’÷"’À¢–çWE÷–ÆöEö'—FW3ÖFVÇFö–çWEö'—FW2À¢÷WGWE÷–ÆöEö'—FW3ÖÖFW&–Æ—¦VEö'—FW2À¢ÖFW&–Æ—¦F–öå÷76W3ÓÀ¢66†U÷&V6öãÒ&6ö×7BFVÇFÖW&vVB'WBgVÆÂfÆ–FF÷"&Æö6¶VBW'6—7FVæ6R"À¢¢&WGW&âFööÅ&W7öç6R€¢7FGW3Ò&f–Åö7W'&VçB"À¢7VÖÖ'“Öb.Z)î˜xþYŽ[›niÊ®˜	®‹ø~ûÙÎ™zîš)‡¶ÆVâ‡&W÷'BævWB‚v—77VW2rÂµÒ’—ÞšžûÙÎiÊ®XižXZ^jÚ>[Èþx˜ŽiÊÂ"À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢G&6Uö–C×7G"‡FVÆVÖWG'•²'FVÆVÖWG'•ö–B%Ò’À¢FF×°¢'&W÷'B#¢&W÷'BÀ¢&FVÇFöÖæ–fW7B#¢FVÇFöÖæ–fW7BÀ¢&&6Uö'F–f7Eö–B#¢&6Uö'F–f7Eö–BÀ¢'FVÆVÖWG'’#¢FVÆVÖWG'’À¢&ÖV7W&VÖVçE÷66÷R#¢'6W'fW%ööæÇ’"À¢&æW‡Eö7F–öâ#¢'&W—%ö6ö×7EöFVÇF÷F†Vå÷&WG'’"À¢ÒÀ¢ ¢66÷Uö†6‚Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'66÷U÷F&vWB#¢66÷U÷F&vWBÀ¢&ffV7FVE÷6†÷E÷V–G2#¢&WVW7FVE÷V–G2À¢&ÆVgB#¢ÆVgEö&÷VæF'•÷6†÷E÷V–BÀ¢'&–v‡B#¢&–v‡Eö&÷VæF'•÷6†÷E÷V–BÀ¢Ð¢¢¢6Æ–Õö–BÒöæWuö–B‚$DTÅD"¢G&ç67F–öå÷&W&VBÒfÇ6P¢&W7öç6S¢FööÅ&W7öç6RÂæöæRÒæöæP¢6æF–FFUö–G3¢GWÆU·7G"Â7G"Â7G%ÒÂæöæRÒæöæP¢G'“ ¢6VÆbç7F÷&Rç&W&U÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6Uö'F–f7Eö–BÀ¢W‡V7FVEö6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢W‡V7FVE÷&uöf–ævW'&–çCÖW‡V7FVEö&6U÷&uöf–ævW'&–çBÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7V66W76÷%ö¶–æCÒ&FVÇF"À¢66÷Uö†6ƒ×66÷Uö†6‚À¢¢G&ç67F–öå÷&W&VBÒG'VP¢&W7öç6RÒ6VÆbæ6öÖÖ—E÷7F÷'–&ö&B€¢æÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢7F÷'–&ö&CÖÖW&vVBÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢6÷W&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢&WVW7E÷6W76–öåö–C×&WVW7E÷6W76–öåö–BÀ¢6÷W&6UöÆ–æµöÖöFSÒ'&Wf—6–öâ"À¢&WfÆ–FFVE÷&W÷'C×&W÷'BÀ¢&WfÆ–FFVEö6æöæ–6Åö†6ƒÖ6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb†ÖW&vVB’À¢FVÆVÖWG'•ö÷W&F–öãÒ&6öÖÖ—E÷7F÷'–&ö&EöFVÇF"À¢FVÆVÖWG'•÷7F'FVEöç3×7F'FVEöç2À¢FVÆVÖWG'•÷&W7FvW5ö×3×&W7FvW2À¢FVÆVÖWG'•öÖWG&–73×°¢&ffV7FVE÷6†÷Eö6÷VçB#¢ÆVâ‡&WVW7FVE÷V–G2’À¢'&W6W'fVE÷6VÖçF–5ö6÷VçB#¢FVÇFöÖæ–fW7BævWB‚'VçF÷V6†VE÷6†÷Eö6÷VçB"Â’À¢&–çWE÷–ÆöEö'—FW2#¢FVÇFö–çWEö'—FW2À¢&÷WGWE÷–ÆöEö'—FW2#¢ÖFW&–Æ—¦VEö'—FW2À¢&66†U÷&V6öâ#¢&W†7B&6R6æöæ–6Â&WW6VC²ffV7FVB&ævRg&W6†Ç’ÖFW&–Æ—¦VBæBgVÆÇ’fÆ–FFVB"À¢ÒÀ¢÷7V66W76÷%÷G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢÷7V66W76÷%ö¶–æCÒ&FVÇF"À¢÷7V66W76÷%ö&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢÷7V66W76÷%÷66÷Uö†6ƒ×66÷Uö†6‚À¢÷&W&VEö–FVçF—G•öÖæ–fW7C×&W&VEö–FVçF—G•öÖæ–fW7BÀ¢¢–b&W7öç6Rç7FGW2Ò$ôET5D”ôåõ$TdÄ”t…Eõ52÷"æ÷B&W7öç6Ræ'F–f7Eö–C ¢&—6R'VçF–ÖTW'&÷"‚&FVÇFf–æÂ6öÖÖ—BF–Bæ÷B72"¢6æF–FFUö–G2Ò€¢7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7G"‡&W7öç6RæFFævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"’÷"""’À¢7G"‡&W7öç6RæFFævWB‚&†æFöfeö'F–f7Eö–B"’÷"""’À¢¢–bæ÷BÆÂ†6æF–FFUö–G2“ ¢&—6R'VçF–ÖTW'&÷"‚&FVÇF7V66W76÷"V&Æ–6F–öâFW67&—F÷"—2–æ6ö×ÆWFR"¢æWuö†6‚Ò7G"‡&W7öç6RæFFævWB‚&6æöæ–6Å÷6†#Sb"’÷"""¢6VÆbç7F÷&Ræ6öÖÖ—E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6Uö'F–f7Eö–BÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7V66W76÷%ö'F–f7Eö–C×7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7V66W76÷%ö6æöæ–6Åö†6ƒÖæWuö†6‚À¢¢G&ç67F–öå÷&W&VBÒfÇ6P¢W†6WBW†6WF–öâ2W†3 ¢6ÆVçWöW'&÷'3¢Æ—7E·7G%ÒÒµÐ¢–bG&ç67F–öå÷&W&VC ¢G'“ ¢6VÆbç7F÷&Ræ&÷'E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6Uö'F–f7Eö–BÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢&V6öãÖb&FVÇF7V66W76÷"42f–ÆVC¢·G—R†W†2’åõöæÖUõ÷Ò"À¢¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢6ÆVçWöW'&÷'2æVæB†b'G&ç67F–öã§¶6ÆVçWöW†7Ò"¢–b6æF–FFUö–G2—2æ÷BæöæS ¢G'“ ¢6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢†æFöfeö–CÖ6æF–FFUö–G5³%ÒÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7FFSÒ&&÷'FVB"À¢&V6öãÖb&FVÇF7V66W76÷"42f–ÆVC¢·G—R†W†2’åõöæÖUõ÷Ò"À¢¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢6ÆVçWöW'&÷'2æVæB†b&6æF–FFS§¶6ÆVçWöW†7Ò"¢f÷"6ÆVçWöW'&÷"–â6ÆVçWöW'&÷'3 ¢–b†6GG"†W†2Â&FEöæ÷FR"“ ¢W†2æFEöæ÷FR†b&FVÇF&öÆÆ&6²v&æ–æs¢¶6ÆVçWöW'&÷'Ò"¢&—6P ¢76W'B&W7öç6R—2æ÷BæöæRæB6æF–FFUö–G2—2æ÷BæöæP¢G'“ ¢V&Æ–6F–öâÒ6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢†æFöfeö–CÖ6æF–FFUö–G5³%ÒÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7FFSÒ&6öÖÖ—GFVB"À¢¢&W7öç6RæFF²&'F–f7B%ÒÒV&Æ–6F–öå²&'F–f7G2%Õ¶6æF–FFUö–G5³ÕÐ¢&W7öç6RæFF²&†æFöfb%ÒÒ6VÆbåö†æFöfe÷7VÖÖ'’€¢V&Æ–6F–öå²&†æFöfb%Ð¢¢W†6WBW†6WF–öâ2&ö¦V7F–öåöW†3 ¢2F†R&6R42—2F†RV&Æ–6F–öâWF†÷&—G’âG–æÖ–2&VBæ@¢2†æFöfbvFW2F†W&Vf÷&R&VÖ–â6÷'&V7BWfVâ–bF†—2FVæ÷&ÖÆ—¦V@¢2&ö¦V7F–öâWFFRæVVG2ÆFW"&V6÷fW'’à¢&W7öç6RæFF²'V&Æ–6F–öå÷&ö¦V7F–öå÷v&æ–ær%ÒÒ7G"€¢&ö¦V7F–öåöW†0¢•³£S%Ð ¢&W7öç6RæFF²&'F–f7B%ÒÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡&W7öç6Ræ'F–f7Eö–B’À¢°¢&FVÇFö&6Uö'F–f7Eö–B#¢&6Uö'F–f7Eö–BÀ¢&FVÇFö&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢&FVÇF÷66÷Uö†6‚#¢66÷Uö†6‚À¢&FVÇF÷66÷U÷F&vWB#¢66÷U÷F&vWBÀ¢&FVÇFöÖæ–fW7B#¢FVÇFöÖæ–fW7BÀ¢&ÖFW&–Æ—¦W%÷fW'6–öâ#¢4ôÕ5EôÔDU$”Ä•¤U%ô4ôåE$5BÀ¢¢¤äõôEUÄ”4DUôtTäU$D”ôåôUd”DTä4RÀ¢''VçF–ÖU÷FW7Eö6W'F–f–6FUö–B#¢'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢ÒÀ¢¢&W7öç6RæFFçWFFR€¢°¢&FVÇFöÖæ–fW7B#¢FVÇFöÖæ–fW7BÀ¢&&6Uö'F–f7Eö–B#¢&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢'66÷Uö†6‚#¢66÷Uö†6‚À¢&ÖV7W&VÖVçE÷66÷R#¢'6W'fW%ööæÇ’"À¢'VæÖV7W&VE÷7FvW2#¢²&6†FwEöÖöFVÅövVæW&F–öâ%ÒÀ¢Ð¢¢&W7öç6Rç7VÖÖ'’Ò€¢b.Z)î˜xþyIþKª~ZèÎh‰ûÙÎ[ÛY8×¶ÆVâ‡&WVW7FVE÷V–G2—Þ™YÎûÙÎKùÞyYžŠúÞK˜’ ¢b'¶FVÇFöÖæ–fW7BævWB‚wVçF÷V6†VE÷6†÷Eö6÷VçBrÂ—Þ™YÎûÙÎXZŽŠŽyIþKª~š(Nj8˜	®‹ø~ûÙÎ[è^xºÎz¸¾ZêŠê ¢¢&WGW&â&W7öç6P ¢7FF–6ÖWF†ö@¢FVb÷F6…öf–VÆE÷&WV—&W5ögVÆÅ÷&V'V–ÆB†f–VÆC¢7G"’Óâ&ööÃ ¢æ÷&ÖÆ—¦VBÒ&Rç7V"‡"%Ç2²"Â""Â7G"†f–VÆB’’æ66VföÆB‚¢–bæ÷&ÖÆ—¦VB–âD4…ôdõ$$”DDTåôd”TÄE3 ¢&WGW&âG'VP¢–bç’‡FW&Ò–âæ÷&ÖÆ—¦VBf÷"FW&Ò–âD4…ôdõ$$”DDTåôd”TÄEõDU$Õ2“ ¢&WGW&âG'VP¢2æW7FVBF‡26âW66RF†RW‡Æ–6—FÇ’VF—FVB6†÷Bf–VÆBæB&P¢2F†W&Vf÷&R÷WG6–FRF†RÆö6Â×F6‚6öçG&7Bà¢&WGW&âç’†Ö&¶W"–âæ÷&ÖÆ—¦VBf÷"Ö&¶W"–â‚"â"Â%²"Â%Ò"Â%õò"’ ¢7FF–6ÖWF†ö@¢FVb÷&÷FV7FVE÷66÷Uö†—B‡&÷FV7FVE÷66÷S¢Æ—7E´ç•ÒÂ6†÷Eö–C¢7G"Âf–VÆC¢7G"’Óâ7G"ÂæöæS ¢6†÷Eö¶W’Ò6†÷Eö–Bç7G&—‚’æ66VföÆB‚¢f–VÆEö¶W’Ò&Rç7V"‡"%Ç2²"Â""Âf–VÆB’æ66VföÆB‚¢W†7BÒ°¢6†÷Eö¶W’À¢f–VÆEö¶W’À¢b'·6†÷Eö¶W—Òç¶f–VÆEö¶W—Ò"À¢b'·6†÷Eö¶W—Ó§¶f–VÆEö¶W—Ò"À¢b'·6†÷Eö¶W—Ò÷¶f–VÆEö¶W—Ò"À¢Ð ¢FVb6†÷EöçVÖ&W"‡fÇVS¢7G"’ÓâGWÆU·7G"Â–çEÒÂæöæS ¢266WB&÷F‚ÆVv7’3æB#Õ52ÓURÓ–FVçF–f–W'2'¢2G&VF–ærF†Rf–æÂçVÖW&–26VvÖVçB2F†R–âÖW—6öFR÷&F–æÂà¢ÖF6‚Ò&RægVÆÆÖF6‚‡""‚ƒó¢â¥ÄB“ò’…ÆB²’"ÂfÇVR¢–bæ÷BÖF6ƒ ¢&WGW&âæöæP¢&WGW&âÖF6‚æw&÷Wƒ’Â–çB†ÖF6‚æw&÷Wƒ"’ ¢7W'&VçBÒ6†÷EöçVÖ&W"‡6†÷Eö¶W’¢f÷"—FVÒ–â&÷FV7FVE÷66÷S ¢fÇVRÒ&Rç7V"‡"%Ç2²"Â""Â7G"†—FVÒ’’æ66VföÆB‚¢–bfÇVR–â²&ÆÂ"Â.XZŽ˜:‚"Â.XZŽŠ‚"Â.ZèÎi[NXˆn™YÂ'Ò÷"fÇVR–âW†7C ¢&WGW&â7G"†—FVÒ¢2&ævRf÷&×266WFVB'’&öGV7F–öâFö7VÖVçG3¢3Õ3À¢23ûÙå3Â3ˆ{53Â÷F–öæÆÇ’föÆÆ÷vVB'’æf–VÆBó¦f–VÆBà¢&ævUöÖF6‚Ò&RæÖF6‚€¢"%â‚â³ò’ƒó§çÎûÙçÎ(	GÎˆ{2’‚â³ò’ƒó¥²ã¢þûÉ¥Ò‚â²’“òB"À¢fÇVRÀ¢¢–bæ÷B&ævUöÖF6ƒ ¢2¶VWF†RVæÖ&–wV÷W2ÆVv7’‡—†Vâf÷&Ò3Õ3â# ¢2–FVçF–f–W'2Ç&VG’6öçF–â‡—†VâæBF†W&Vf÷&RW6Rà¢2VÒF6‚÷F–ÆFRþˆ{2&WGvVVâF†V—"Gvò&ævRVæGö–çG2à¢&ævUöÖF6‚Ò&RæÖF6‚€¢"%â…¶×¥ÇS3CÕÇS–ffeÒ£õÆB²’Ò…¶×¥ÇS3CÕÇS–ffeÒ£õÆB²’ƒó¥²ã¢þûÉ¥Ò‚â²’“òB"À¢fÇVRÀ¢¢–b&ævUöÖF6‚æB7W'&VçC ¢7F'BÒ6†÷EöçVÖ&W"‡&ævUöÖF6‚æw&÷Wƒ’¢VæBÒ6†÷EöçVÖ&W"‡&ævUöÖF6‚æw&÷Wƒ"’¢66÷VEöf–VÆBÒ&Rç7V"‡"%Ç2²"Â""Â&ævUöÖF6‚æw&÷Wƒ2’÷"""’æ66VföÆB‚¢–b€¢7F'@¢æBVæ@¢æB7W'&VçE³ÒÓÒ7F'E³ÒÓÒVæE³Ð¢æBÖ–â‡7F'E³ÒÂVæE³Ò’ÃÒ7W'&VçE³ÒÃÒÖ‚‡7F'E³ÒÂVæE³Ò¢æB†æ÷B66÷VEöf–VÆB÷"66÷VEöf–VÆBÓÒf–VÆEö¶W’¢“ ¢&WGW&â7G"†—FVÒ¢–b6†÷Eö¶W’–âfÇVRæBf–VÆEö¶W’–âfÇVS ¢&WGW&â7G"†—FVÒ¢&WGW&âæöæP ¢öfVæ6U÷&öÆUö÷W&F–öà¢FVb6öÖÖ—E÷7F÷'–&ö&E÷F6‚€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢&6Uö'F–f7Eö–C¢7G"À¢W‡V7FVEö6æöæ–6Åö†6ƒ¢7G"À¢W‡V7FVE÷&uö'F–f7Eöf–ævW'&–çC¢7G"ÂæöæRÀ¢VF—Eö6W'F–f–6FUö–C¢7G"À¢÷W&F–öç3¢Æ—7E¶F–7E·7G"Âç•ÕÒÀ¢–×7E÷66÷S¢Æ—7E·7G%ÒÀ¢f–ÆUöæÖS¢7G"À¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢""$Ç’Wf–FVæ6RÖ&÷VæB6†÷BÖf–VÆB&W—'2ÂF†Vâ'VâF†RgVÆÂfÆ–FF÷"à ¢F†—2—2–çFVçF–öæÆÇ’æ'&÷vW"F†â6öÖÖ—E÷7F÷'–&ö&F¢F†R6ÆÆW ¢6ææ÷BÇFW"7G'V7GW&ÂÂ6÷W&6RÂ&÷VæF'’ÂF–Ö–ærÂ÷"6övæ—F–öâf–VÆG2À¢æBâ–æFWVæFVçBÖVF—B6W'F–f–6FR×W7B&÷fRWfW'’&WVW7FVBf—‚à¢F†Rf–æÂW'6—7FVæ6RæB&ö¦V7BÖÆVFvW"F‚—2FVÆVvFVBFòF†P¢W†—7F–ærgVÆÂ6öÖÖ—BgVæ7F–öâ6òF6†–æræWfW"vV¶Vç2#vFW2à¢""  ¢6VÆbå÷&WV—&U÷'VçF–ÖR‚¢–bæ÷B—6–ç7Fæ6R†÷W&F–öç2ÂÆ—7B’÷"æ÷B÷W&F–öç3 ¢&—6RfÇVTW'&÷"‚'F6…ö÷W&F–öç5÷&WV—&VC¢÷W&F–öç2×W7B&RæöâÖV×G’"¢–bæ÷B—6–ç7Fæ6R†–×7E÷66÷RÂÆ—7B’÷"æ÷B–×7E÷66÷S ¢&—6RfÇVTW'&÷"‚&–×7E÷66÷Uö–æ6ö×ÆWFS¢–×7E÷66÷R×W7B&RæöâÖV×G’" ¢‡V"Ò6VÆbç7F÷&RæÆöEö‡V"†æÖW76RÂ&ö¦V7Eö–B¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢–bv÷&µö÷&FW"ævWB‚&÷W&F–öâ"’Ò'7F÷'–&ö&E÷&W—"# ¢&—6RfÇVTW'&÷"€¢'F6…÷v÷&µö÷&FW%ö–çfÆ–C¢6öÖÖ—E÷7F÷'–&ö&E÷F6‚&WV—&W27F÷'–&ö&E÷&W—" ¢¢–bv÷&µö÷&FW"ævWB‚&WF†÷&—¦F–öâ"’Ò&W‡Æ–6—EöW†V7WFR# ¢&—6RfÇVTW'&÷"‚'F6…öWF†÷&—¦F–öå÷&WV—&VC¢v÷&²÷&FW"×W7B†fRW‡Æ–6—EöW†V7WFR"¢6VÆbåöVç7W&Uö6åöW†V7WFR‡v÷&µö÷&FW"¢6VÆbåö76W'E÷7F÷'–&ö&EöWF†÷&—G’‡v÷&µö÷&FW"Â'&W—""¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ†æÖW76RÂv÷&µö÷&FW"Â&WVW7E÷6W76–öåö–B ¢7F÷'–&ö&BÂ&6U÷&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ&6Uö'F–f7Eö–@¢¢&6Uö6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢–bW‡V7FVEö6æöæ–6Åö†6‚Ò&6Uö6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"€¢&6æöæ–6Åö†6…ö6öæfÆ–7C¢W‡V7FVB6æöæ–6Â†6‚FöW2æ÷BÖF6‚F†R&6R'F–f7B ¢¢&6UöÖWFFFÒ&6U÷&V6÷&BævWB‚&ÖWFFF"’÷"·Ð¢&6Uö†5ö6öÖÖ—GFVE÷7V66W76÷"Òç’€¢&6UöÖWFFFævWB†¶W’¢f÷"¶W’–â€¢'7V66W76÷%ö'F–f7Eö–B"À¢'F6…÷7V66W76÷%ö'F–f7Eö–B"À¢&FVÇF÷7V66W76÷%ö'F–f7Eö–B"À¢'&W—%÷7V66W76÷%ö'F–f7Eö–B"À¢¢ ¢G'“ ¢6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–CÖVF—Eö6W'F–f–6FUö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FRFöW2æ÷BW†—7B–âF†—2&ö¦V7B"’g&öÒW†0¢–b6W'F–f–6FRævWB‚'&ö¦V7Eö–B"’Ò&ö¦V7Eö–C ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR&VÆöæw2Fòæ÷F†W"&ö¦V7B"¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†6W'F–f–6FR“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢Vç6–væVB÷"F×W&VB6W'F–f–6FR"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbåöÆVFvW%÷66†VÖÂ&–æFWVæFVçEöVF—Eö6W'F–f–6FR"Â6W'F–f–6FP¢¢–b6W'F–f–6FRævWB‚&VF—Eö6Æ72"’ÒTD•Eô4Ä55ô”äDUTäDTåC ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢–æFWVæFVçEöVF—B6W'F–f–6FR—2&WV—&VB"¢–b6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"’Ò&6Uö6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR—2æ÷B&÷VæBFòF†R&6R6æöæ–6Â†6‚"¢–b&6Uö'F–f7Eö–Bæ÷B–â°¢6W'F–f–6FRævWB‚&'F–f7Eö–B"’À¢6W'F–f–6FRævWB‚&6æöæ–6Åö'F–f7Eö–B"’À¢Ó ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR—2æ÷B&÷VæBFòF†R&6R'F–f7B"¢–bæ÷B6VÆbå÷fÆ–Eö66†Uö–FVçF—G’†6W'F–f–6FRÂTD•Eô4Ä55ô”äDUTäDTåB“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR66†R–FVçF—G’—2–çfÆ–B"¢7W'&VçE÷6÷W&6Uöf–ævW'&–çBÒ6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢&6U÷&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"’À¢&6U÷&V6÷&BÀ¢¢7W'&VçEö66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çCÖ7W'&VçE÷6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒ×6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B’À¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢'F–f7Eöf–ævW'&–çC×7G"†&6U÷&V6÷&BævWB‚&f–ævW'&–çB"’÷"""’À¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"Â·Ò’ævWB‚''VçF–ÖU÷FW7Eö6W'F–f–6FUö–B"’÷"" ¢’À¢¢7W'&VçEö66†Uö6ö×öæVçG5²'66÷R%ÒÒ7G"€¢6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"Â·Ò’ævWB‚'66÷R"’÷"" ¢¢–b6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"’Ò7W'&VçEö66†Uö6ö×öæVçG3 ¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢6÷W&6RÂ&÷VæF'’Â'VÆW2Â÷"fÆ–FF÷"6†ævVBgFW"VF—B ¢¢6W'F–f–6FU÷&W÷'BÒ6W'F–f–6FRævWB‚'&W÷'B"¢–bæ÷B—6–ç7Fæ6R†6W'F–f–6FU÷&W÷'BÂF–7B’÷"æ÷B—6–ç7Fæ6R€¢6W'F–f–6FU÷&W÷'BævWB‚&—77VW2"’ÂÆ—7@¢“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR&W÷'B—2Ö—76–ær"¢–b6W'F–f–6FRævWB‚'&W÷'E÷6†#Sb"’Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2†6W'F–f–6FU÷&W÷'B¢“ ¢&—6RfÇVTW'&÷"‚&–çfÆ–EöVF—Eö6W'F–f–6FS¢6W'F–f–6FR&W÷'Bf–ævW'&–çBÖ—6ÖF6‚"¢6W'F–f–6FU÷&rÒ7G"€¢6W'F–f–6FRævWB‚&f–ævW'&–çG2"Â·Ò’ævWB‚&'F–f7E÷&r"¢÷"6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"Â·Ò’ævWB‚&'F–f7B"¢÷"" ¢¢&WV—&VE÷&rÒW‡V7FVE÷&uö'F–f7Eöf–ævW'&–çB÷"6W'F–f–6FU÷&p¢–bæ÷B&WV—&VE÷&r÷"&WV—&VE÷&rÒ7G"†&6U÷&V6÷&BævWB‚&f–ævW'&–çB"’÷"""“ ¢&—6RfÇVTW'&÷"€¢&'F–f7E÷&uöf–ævW'&–çEöÖ—6ÖF6ƒ¢F6‚&6R&r'—FW2Fòæ÷BÖF6‚F†RVF—B6W'F–f–6FR ¢¢–b6W'F–f–6FU÷&rÒ&WV—&VE÷&s ¢&—6RfÇVTW'&÷"€¢&–çfÆ–EöVF—Eö6W'F–f–6FS¢W‡V7FVB&r'F–f7Bf–ævW'&–çB—2æ÷B6W'F–f–6FRÖ&÷VæB ¢¢7W'&VçEöf–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢7F÷'–&ö&BÀ¢&6U÷&V6÷&BÀ¢6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çCÖ7W'&VçE÷6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒ×6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B’À¢¢&W—%÷6¶vRÂVF—E÷Fõ÷&W—%ö†æFöfbÒ6VÆbå÷&W—%÷6¶vUöf÷%÷F6‚€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢&6Uö6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢7W'&VçEöf–ævW'&–çG3Ö7W'&VçEöf–ævW'&–çG2À¢¢6VÆbåö76W'E÷&W—%÷6W&F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW"À¢&W—%÷6¶vRÀ¢VF—E÷Fõ÷&W—%ö†æFöfbÀ¢¢&W—%÷6¶vUö–BÒ7G"‡&W—%÷6¶vU²'&W—%÷6¶vUö–B%Ò ¢6†÷G2Ò7F÷'–&ö&BævWB‚'6†÷G2"¢–bæ÷B—6–ç7Fæ6R‡6†÷G2ÂÆ—7B’÷"æ÷B6†÷G3 ¢&—6RfÇVTW'&÷"‚&gVÆÅ÷&V'V–ÆE÷&WV—&VC¢6æöæ–6Â7F÷'–&ö&B†2æò7F&ÆR6†÷G2"¢6†÷G5ö'•ö–C¢F–7E·7G"ÂF–7E·7G"Âç•ÕÒÒ·Ð¢GWÆ–6FUö–G3¢6WE·7G%ÒÒ6WB‚¢f÷"6†÷B–â6†÷G3 ¢–bæ÷B—6–ç7Fæ6R‡6†÷BÂF–7B“ ¢&—6RfÇVTW'&÷"‚&gVÆÅ÷&V'V–ÆE÷&WV—&VC¢WfW'’6†÷B×W7B&Râö&¦V7B"¢6†÷Eö–BÒ7G"‡6†÷BævWB‚.™YÎZKN{ÉnXûr"’÷"""’ç7G&—‚¢–bæ÷B6†÷Eö–C ¢&—6RfÇVTW'&÷"‚&gVÆÅ÷&V'V–ÆE÷&WV—&VC¢WfW'’6†÷B&WV—&W27F&ÆR™YÎZKN{ÉnXûr"¢–b6†÷Eö–B–â6†÷G5ö'•ö–C ¢GWÆ–6FUö–G2æFB‡6†÷Eö–B¢6†÷G5ö'•ö–E·6†÷Eö–EÒÒ6†÷@¢–bGWÆ–6FUö–G3 ¢&—6RfÇVTW'&÷"€¢&gVÆÅ÷&V'V–ÆE÷&WV—&VC¢GWÆ–6FR6†÷Eö–BfÇVW3¢"²"Â"æ¦ö–â‡6÷'FVB†GWÆ–6FUö–G2’¢ ¢—77VU÷&÷w2Ò6W'F–f–6FU÷&W÷'BævWB‚&—77VW2"ÂµÒ¢—77VW5ö'•ö6öFS¢F–7E·7G"ÂÆ—7E¶F–7E·7G"Âç•ÕÕÒÒ·Ð¢f÷"—77VR–â—77VU÷&÷w3 ¢–b—6–ç7Fæ6R†—77VRÂF–7B“ ¢—77VW5ö'•ö6öFRç6WFFVfVÇB‡7G"†—77VRævWB‚&W'&÷%ö6öFR"’÷"""’ÂµÒ’æVæB†—77VR¢6¶vU÷F&vWG2Ò°¢€¢7G"‡&÷rævWB‚&—77VUö6öFR"’÷"""’À¢7G"‡&÷rævWB‚'6†÷Eö–B"’÷"""’ç7G&—‚’À¢&Rç7V"‡"%Ç2²"Â""Â7G"‡&÷rævWB‚&f–VÆB"’÷"""’’æ66VföÆB‚’À¢¢f÷"&÷r–â&W—%÷6¶vRævWB‚&—77VU÷F&vWG2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢Ð¢WF†÷&—¦VE÷6†÷Eö–G2Ò°¢7G"‡fÇVR’ç7G&—‚¢f÷"fÇVR–â&W—%÷6¶vRævWB‚&WF†÷&—¦VE÷66÷R"Â·Ò’ævWB‚'6†÷Eö–G2"ÂµÒ¢–b7G"‡fÇVR’ç7G&—‚¢Ð ¢&÷FV7FVE÷66÷RÒÆ—7B‡v÷&µö÷&FW"ævWB‚'&÷FV7FVE÷66÷R"’÷"µÒ¢–×7Eö–G2Ò·7G"†—FVÒ’ç7G&—‚’f÷"—FVÒ–â–×7E÷66÷R–b7G"†—FVÒ’ç7G&—‚—Ð¢VæWF†÷&—¦VEö–×7BÒ6÷'FVB†–×7Eö–G2ÒWF†÷&—¦VE÷6†÷Eö–G2¢–bVæWF†÷&—¦VEö–×7C ¢&—6RfÇVTW'&÷"€¢'&W—%÷6¶vU÷66÷UöÖ—6ÖF6ƒ¢VæWF†÷&—¦VB–×7B6†÷B–G2 ¢²"Â"æ¦ö–â‡VæWF†÷&—¦VEö–×7B¢¢F6†VBÒ6÷’æFVW6÷’‡7F÷'–&ö&B¢F6†VE÷6†÷G2Ò°¢7G"‡6†÷BævWB‚.™YÎZKN{ÉnXûr"’÷"""’ç7G&—‚“¢6†÷@¢f÷"6†÷B–âF6†VBævWB‚'6†÷G2"ÂµÒ¢–b—6–ç7Fæ6R‡6†÷BÂF–7B¢Ð¢Æ–VEö÷W&F–öç3¢Æ—7E¶F–7E·7G"Âç•ÕÒÒµÐ¢ÖöF–f–VE÷6†÷G3¢6WE·7G%ÒÒ6WB‚¢ÖöF–f–VE÷F&vWG3¢6WE·GWÆU·7G"Â7G%ÕÒÒ6WB‚ ¢f÷"–æFW‚Â÷W&F–öâ–âVçVÖW&FR†÷W&F–öç2Â“ ¢–bæ÷B—6–ç7Fæ6R†÷W&F–öâÂF–7B’÷"÷W&F–öâævWB‚&÷"’Ò'&WÆ6Uöf–VÆB# ¢&—6RfÇVTW'&÷"†b'F6…ö÷W&F–öåö–çfÆ–C¢÷W&F–öç5·¶–æFW‡ÕÒ×W7B&R&WÆ6Uöf–VÆB"¢6†÷Eö–BÒ7G"†÷W&F–öâævWB‚'6†÷Eö–B"’÷"""’ç7G&—‚¢f–VÆBÒ7G"†÷W&F–öâævWB‚&f–VÆB"’÷"""’ç7G&—‚¢–b6†÷Eö–Bæ÷B–âF6†VE÷6†÷G3 ¢&—6RfÇVTW'&÷"†b'F6…÷F&vWEöÖ—76–æs¢Væ¶æ÷vâ6†÷Eö–B·6†÷Eö–B'Ò"¢–b6VÆbå÷F6…öf–VÆE÷&WV—&W5ögVÆÅ÷&V'V–ÆB†f–VÆB“ ¢&—6RfÇVTW'&÷"€¢b&gVÆÅ÷&V'V–ÆE÷&WV—&VC¢f–VÆB¶f–VÆB'Ò—27G'V7GW&ÂÂ&÷VæF'’Â6övæ—F–öâÂF–Ö–ærÂ÷"6÷W&6RWf–FVæ6R ¢¢F&vWEö¶W’Ò‡6†÷Eö–BÂf–VÆB¢–bF&vWEö¶W’–âÖöF–f–VE÷F&vWG3 ¢&—6RfÇVTW'&÷"€¢b'F6…ö÷W&F–öåö6öæfÆ–7C¢GWÆ–6FR&WÆ6VÖVçBf÷"·6†÷Eö–GÒç¶f–VÆGÒ ¢¢ÖöF–f–VE÷F&vWG2æFB‡F&vWEö¶W’¢–bf–VÆBæ÷B–âF6†VE÷6†÷G5·6†÷Eö–EÓ ¢&—6RfÇVTW'&÷"€¢b&gVÆÅ÷&V'V–ÆE÷&WV—&VC¢&WÆ6Uöf–VÆB6ææ÷B7&VFRVæ¶æ÷vâf–VÆB¶f–VÆB'Ò ¢¢&÷FV7FVEö†—BÒ6VÆbå÷&÷FV7FVE÷66÷Uö†—B‡&÷FV7FVE÷66÷RÂ6†÷Eö–BÂf–VÆB¢–b&÷FV7FVEö†—B—2æ÷BæöæS ¢&—6RfÇVTW'&÷"€¢b'&÷FV7FVE÷66÷U÷f–öÆF–öã¢·6†÷Eö–GÒç¶f–VÆGÒÖF6†W2v÷&²Ö÷&FW"&÷FV7FVB66÷R·&÷FV7FVEö†—B'Ò ¢ ¢—77VUö–G2Ò÷W&F–öâævWB‚&—77VUö–G2"¢–bæ÷B—6–ç7Fæ6R†—77VUö–G2ÂÆ—7B’÷"æ÷B—77VUö–G2÷"ç’€¢æ÷B7G"†—FVÒ’ç7G&—‚’f÷"—FVÒ–â—77VUö–G0¢“ ¢&—6RfÇVTW'&÷"€¢b'F6…öWf–FVæ6U÷&WV—&VC¢÷W&F–öç5·¶–æFW‡ÕÒæ—77VUö–G2×W7B&RæöâÖV×G’ ¢¢f÷"—77VUö–E÷fÇVR–â—77VUö–G3 ¢—77VUö–BÒ7G"†—77VUö–E÷fÇVR’ç7G&—‚¢ÖF6†–æuö—77VW2Ò—77VW5ö'•ö6öFRævWB†—77VUö–BÂµÒ¢–bæ÷BÖF6†–æuö—77VW3 ¢&—6RfÇVTW'&÷"€¢b'F6…öWf–FVæ6UöÖ—6ÖF6ƒ¢—77VR¶—77VUö–B'Ò—2'6VçBg&öÒF†R–æFWVæFVçBVF—B6W'F–f–6FR ¢¢66÷VEöÖF6†W2ÒµÐ¢f÷"—77VR–âÖF6†–æuö—77VW3 ¢&VÆFVE÷6†÷G2Ò6VÆbå÷6†÷Eö–G5öf÷%öÆö6F–öâ€¢7F÷'–&ö&BÂ7G"†—77VRævWB‚&Æö6F–öâ"’÷"""¢¢—77VUöf–VÆBÒ7G"€¢—77VRævWB‚&f–VÆB"¢÷"7G"†—77VRævWB‚&Æö6F–öâ"’÷"""’ç'7Æ—B‚"â"Â•²ÓÐ¢’ç7G&—‚¢–b€¢6†÷Eö–B–â&VÆFVE÷6†÷G0¢æB&Rç7V"‡"%Ç2²"Â""Â—77VUöf–VÆB’æ66VföÆB‚¢ÓÒ&Rç7V"‡"%Ç2²"Â""Âf–VÆB’æ66VföÆB‚¢“ ¢66÷VEöÖF6†W2æVæB†—77VR¢–bæ÷B66÷VEöÖF6†W3 ¢&—6RfÇVTW'&÷"€¢b'F6…öWf–FVæ6UöÖ—6ÖF6ƒ¢—77VR¶—77VUö–B'ÒFöW2æ÷BÇ’FòW†7BF&vWB·6†÷Eö–GÒç¶f–VÆGÒ ¢¢–b€¢—77VUö–BÀ¢6†÷Eö–BÀ¢&Rç7V"‡"%Ç2²"Â""Âf–VÆB’æ66VföÆB‚’À¢’æ÷B–â6¶vU÷F&vWG3 ¢&—6RfÇVTW'&÷"€¢b'&W—%÷6¶vU÷66÷UöÖ—6ÖF6ƒ¢6¶vRFöW2æ÷BWF†÷&—¦R¶—77VUö–GÒB·6†÷Eö–GÒç¶f–VÆGÒ ¢ ¢&V6öâÒ7G"†÷W&F–öâævWB‚'&V6öâ"’÷"""’ç7G&—‚¢–bæ÷B&V6öã ¢&—6RfÇVTW'&÷"†b'F6…÷&V6öå÷&WV—&VC¢÷W&F–öç5·¶–æFW‡ÕÒç&V6öâ—2&WV—&VB"¢&Vf÷&U÷fÇVRÒ6÷’æFVW6÷’‡F6†VE÷6†÷G5·6†÷Eö–EÕ¶f–VÆEÒ¢F6†VE÷6†÷G5·6†÷Eö–EÕ¶f–VÆEÒÒ6÷’æFVW6÷’†÷W&F–öâævWB‚'fÇVR"’¢ÖöF–f–VE÷6†÷G2æFB‡6†÷Eö–B¢Æ–VEö÷W&F–öç2æVæB€¢°¢&÷#¢'&WÆ6Uöf–VÆB"À¢'6†÷Eö–B#¢6†÷Eö–BÀ¢&f–VÆB#¢f–VÆBÀ¢&—77VUö–G2#¢·7G"†—FVÒ’ç7G&—‚’f÷"—FVÒ–â—77VUö–G5ÒÀ¢'&V6öâ#¢&V6öâÀ¢&&Vf÷&U÷6†#Sb#¢6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2†&Vf÷&U÷fÇVR’’À¢&gFW%÷6†#Sb#¢6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2†÷W&F–öâævWB‚'fÇVR"’’’À¢Ð¢ ¢Ö—76–æuö–×7BÒ6÷'FVB†ÖöF–f–VE÷6†÷G2Ò–×7Eö–G2¢–bÖ—76–æuö–×7C ¢&—6RfÇVTW'&÷"€¢&–×7E÷66÷Uö–æ6ö×ÆWFS¢Ö—76–ærÖöF–f–VB6†÷B–G2"²"Â"æ¦ö–â†Ö—76–æuö–×7B¢¢F6†VEö–çWEö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡F6†VB¢–bF6†VEö–çWEö†6‚ÓÒ&6Uö6æöæ–6Åö†6ƒ ¢&—6RfÇVTW'&÷"‚'F6…öæõö6†ævS¢÷W&F–öç2Fòæ÷B6†ævRF†R6æöæ–6Â7F÷'–&ö&B" ¢66÷Uö†6‚Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2€¢°¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢&VF—Eö6W'F–f–6FUö–B#¢VF—Eö6W'F–f–6FUö–BÀ¢&–×7E÷66÷R#¢6÷'FVB†–×7Eö–G2’À¢&÷W&F–öç2#¢Æ–VEö÷W&F–öç2À¢'F6†VEö–çWE÷6†#Sb#¢F6†VEö–çWEö†6‚À¢Ð¢¢¢–b&W—%÷6¶vRævWB‚'7FGW2"’ÓÒ&6öç7VÖVB# ¢&V6÷fW&VBÒ6VÆbå÷&V6÷fW%ö6öç7VÖVE÷&W—%÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW#×v÷&µö÷&FW"À¢6¶vS×&W—%÷6¶vRÀ¢W‡V7FVEö¶–æCÒ'F6‚"À¢W‡V7FVE÷66÷Uö†6ƒ×66÷Uö†6‚À¢¢–b&V6÷fW&VB—2æ÷BæöæS ¢&V6÷fW&VBæFFçWFFR€¢°¢&gVÆÅ÷&öGV7F–öå÷fÆ–FF–öâ#¢G'VRÀ¢&f–æÅögVÆÅ÷fÆ–FF–öâ#¢G'VRÀ¢&&6Uö'F–f7Eö–B#¢&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Åö†6‚#¢&6Uö6æöæ–6Åö†6‚À¢&æWuö6æöæ–6Åö†6‚#¢&V6÷fW&VBæFFævWB€¢&6æöæ–6Å÷6†#Sb ¢’À¢&VF—Eö6W'F–f–6FUö–B#¢VF—Eö6W'F–f–6FUö–BÀ¢&–×7E÷66÷R#¢6÷'FVB†–×7Eö–G2’À¢&Æ–VEö÷W&F–öç2#¢Æ–VEö÷W&F–öç2À¢Ð¢¢&WGW&â&V6÷fW&V@¢&W—%÷6¶vRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×&W—%÷6¶vUö–BÀ¢¢&6Uö†5ö6öÖÖ—GFVE÷7V66W76÷"ÒfÇ6P¢VÆ–b&6Uö†5ö6öÖÖ—GFVE÷7V66W76÷# ¢&—6RfÇVTW'&÷"€¢&6æöæ–6Åö†6…ö6öæfÆ–7C¢&6R6æöæ–6Â†6‚v2Ç&VG’6öç7VÖVB ¢¢6Æ–Õö–BÒöæWuö–B‚%D4‚"¢6¶vUö6Æ–ÖVBÒfÇ6P¢G&ç67F–öå÷&W&VBÒfÇ6P¢&W7öç6S¢FööÅ&W7öç6RÂæöæRÒæöæP¢6æF–FFUö–G3¢GWÆU·7G"Â7G"Â7G%ÒÂæöæRÒæöæP ¢FVb&öÆÆ&6µ÷F6‚‡&V6öã¢7G"’ÓâÆ—7E·7G%Ó ¢6ÆVçWöW'&÷'3¢Æ—7E·7G%ÒÒµÐ¢–bG&ç67F–öå÷&W&VC ¢G'“ ¢6VÆbç7F÷&Ræ&÷'E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6Uö'F–f7Eö–BÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢&V6öã×&V6öâÀ¢¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢6ÆVçWöW'&÷'2æVæB†b'G&ç67F–öã§¶6ÆVçWöW†7Ò"¢–b6æF–FFUö–G2—2æ÷BæöæS ¢G'“ ¢6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢†æFöfeö–CÖ6æF–FFUö–G5³%ÒÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7FFSÒ&&÷'FVB"À¢&V6öã×&V6öâÀ¢¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢6ÆVçWöW'&÷'2æVæB†b&6æF–FFS§¶6ÆVçWöW†7Ò"¢–b6¶vUö6Æ–ÖVC ¢G'“ ¢6VÆbå÷&W7F÷&U÷&W—%÷6¶vUögFW%öf–ÆVEö6öÖÖ—B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷&–v–æÅ÷6¶vS×&W—%÷6¶vRÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢W‡V7FVEö6Æ–Õ÷Fö¶Vã×7G"€¢6Æ–ÖVE÷6¶vRævWB‚&6Æ–Õ÷Fö¶Vâ"’÷"" ¢’À¢W‡V7FVEö6Æ–ÕövVæW&F–öãÖ–çB€¢6Æ–ÖVE÷6¶vRævWB‚&6Æ–ÕövVæW&F–öâ"’÷" ¢’À¢&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢W‡V7FVE÷7V66W76÷%ö'F–f7Eö–CÒ€¢6æF–FFUö–G5³Ò–b6æF–FFUö–G2VÇ6RæöæP¢’À¢W‡V7FVE÷7V66W76÷%ö6æöæ–6Å÷6†#ScÒ€¢7G"‡&W7öç6RæFFævWB‚&6æöæ–6Å÷6†#Sb"’÷"""¢–b&W7öç6R—2æ÷BæöæP¢VÇ6RæöæP¢’À¢&W—%÷FõöVF—Eö†æFöfeö–CÒ€¢6æF–FFUö–G5³%Ò–b6æF–FFUö–G2VÇ6RæöæP¢’À¢¢W†6WBW†6WF–öâ26ÆVçWöW†3 ¢6ÆVçWöW'&÷'2æVæB†b'6¶vS§¶6ÆVçWöW†7Ò"¢&WGW&â6ÆVçWöW'&÷'0 ¢G'“ ¢6Æ–ÖVE÷6¶vRÒ6VÆbç7F÷&Ræ6Æ–Õ÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vUö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢¢6¶vUö6Æ–ÖVBÒG'VP¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"†æÖW76RÂ&ö¦V7Eö–BÂ6Æ–ÖVE÷6¶vR¢6VÆbç7F÷&Rç&W&U÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6Uö'F–f7Eö–BÀ¢W‡V7FVEö6æöæ–6Åö†6ƒÖ&6Uö6æöæ–6Åö†6‚À¢W‡V7FVE÷&uöf–ævW'&–çC×&WV—&VE÷&rÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7V66W76÷%ö¶–æCÒ'F6‚"À¢66÷Uö†6ƒ×66÷Uö†6‚À¢¢G&ç67F–öå÷&W&VBÒG'VP¢&W7öç6RÒ6VÆbæ6öÖÖ—E÷7F÷'–&ö&B€¢æÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢7F÷'–&ö&C×F6†VBÀ¢f–ÆUöæÖSÖf–ÆUöæÖRÀ¢6÷W&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢&WVW7E÷6W76–öåö–C×&WVW7E÷6W76–öåö–BÀ¢&W—%÷6¶vUö–C×&W—%÷6¶vUö–BÀ¢÷&W—%÷G&ç67F–öåöÖævVCÕG'VRÀ¢÷7V66W76÷%÷G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢÷7V66W76÷%ö¶–æCÒ'F6‚"À¢÷7V66W76÷%ö&6Uö'F–f7Eö–CÖ&6Uö'F–f7Eö–BÀ¢÷7V66W76÷%÷66÷Uö†6ƒ×66÷Uö†6‚À¢¢–b&W7öç6Ræ'F–f7Eö–C ¢6æF–FFUö–G2Ò€¢7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7G"‡&W7öç6RæFFævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"’÷"""’À¢7G"‡&W7öç6RæFFævWB‚&†æFöfeö'F–f7Eö–B"’÷"""’À¢¢–bæ÷BÆÂ†6æF–FFUö–G2“ ¢&—6R'VçF–ÖTW'&÷"€¢'F6‚7V66W76÷"V&Æ–6F–öâFW67&—F÷"—2–æ6ö×ÆWFR ¢¢W†6WBW†6WF–öâ2W†3 ¢f÷"6ÆVçWöW'&÷"–â&öÆÆ&6µ÷F6‚€¢b'F6‚&W&F–öâf–ÆVC¢·G—R†W†2’åõöæÖUõ÷Ò ¢“ ¢–b†6GG"†W†2Â&FEöæ÷FR"“ ¢W†2æFEöæ÷FR†b'F6‚&öÆÆ&6²v&æ–æs¢¶6ÆVçWöW'&÷'Ò"¢&—6P ¢76W'B&W7öç6R—2æ÷BæöæRæB6æF–FFUö–G2—2æ÷BæöæP¢&W÷'BÒ&W7öç6RæFFævWB‚'&W÷'B"¢æWuö6æöæ–6Åö†6‚Ò7G"‡&W7öç6RæFFævWB‚&6æöæ–6Å÷6†#Sb"’÷"""¢&W—%÷FõöVF—Eö†æFöfbÒ&W7öç6RæFFævWB‚&†æFöfb"¢–çfÆ–E÷&W7VÇC¢7G"ÂæöæRÒæöæP¢–bæ÷B—6–ç7Fæ6R‡&W÷'BÂF–7B“ ¢–çfÆ–E÷&W7VÇBÒ'F6‚f–æÂgVÆÂ×F&ÆRfÆ–FF–öâ&W÷'B—2Ö—76–ær ¢VÆ–bæ÷BæWuö6æöæ–6Åö†6ƒ ¢–çfÆ–E÷&W7VÇBÒ'F6‚W'6—7FVB6æöæ–6Â†6‚—2Ö—76–ær ¢VÆ–b&W7öç6Rç7FGW2ÓÒ$ôET5D”ôåõ$TdÄ”t…Eõ52æBæ÷B&W÷'BævWB‚&—77VW2"’æB€¢æ÷B—6–ç7Fæ6R‡&W—%÷FõöVF—Eö†æFöfbÂF–7B¢÷"&W—%÷FõöVF—Eö†æFöfbævWB‚&†æFöfe÷G—R"’Ò'&W—%÷FõöVF—B ¢÷"&W—%÷FõöVF—Eö†æFöfbævWB‚&–Ö×WF&ÆU÷&W—%÷6¶vUö–B"¢Ò&W—%÷6¶vUö–@¢“ ¢–çfÆ–E÷&W7VÇBÒ€¢'&W—%÷6¶vUö&–æF–æuö–æ6ö×ÆWFS¢W'6—7FVB&W—%÷FõöVF—B†æFöfb—2Ö—76–ær ¢ ¢–b–çfÆ–E÷&W7VÇB—2æ÷BæöæS ¢W'&÷"Ò'VçF–ÖTW'&÷"†–çfÆ–E÷&W7VÇB¢f÷"6ÆVçWöW'&÷"–â&öÆÆ&6µ÷F6‚†–çfÆ–E÷&W7VÇB“ ¢–b†6GG"†W'&÷"Â&FEöæ÷FR"“ ¢W'&÷"æFEöæ÷FR†b'F6‚&öÆÆ&6²v&æ–æs¢¶6ÆVçWöW'&÷'Ò"¢&—6RW'&÷  ¢–b&W7öç6Rç7FGW2ÓÒ$ôET5D”ôåõ$TdÄ”t…Eõ52æBæ÷B&W÷'BævWB‚&—77VW2"“ ¢&W7öç6RæFF²&'F–f7B%ÒÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡&W7öç6Ræ'F–f7Eö–B’À¢°¢'F6…ö&6Uö'F–f7Eö–B#¢&6Uö'F–f7Eö–BÀ¢'F6…ö&6Uö6æöæ–6Å÷6†#Sb#¢&6Uö6æöæ–6Åö†6‚À¢'F6…öVF—Eö6W'F–f–6FUö–B#¢VF—Eö6W'F–f–6FUö–BÀ¢'F6…÷&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢'F6…ö–×7E÷66÷R#¢6÷'FVB†–×7Eö–G2’À¢'F6…÷66÷Uö†6‚#¢66÷Uö†6‚À¢ÒÀ¢ ¢FVbf–æÆ—¦UöW†7E÷F6…÷7V66W76÷"‚’ÓâF–7E·7G"Âç•Ó ¢W†7E÷6¶vRÒ6VÆbç7F÷&Ræf–æ—6…÷&W—%÷6¶vR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&W—%÷6¶vUö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢6Æ–Õ÷Fö¶Vã×7G"†6Æ–ÖVE÷6¶vRævWB‚&6Æ–Õ÷Fö¶Vâ"’÷"""’À¢7V66W76÷%ö'F–f7Eö–C×7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7V66W76÷%ö6æöæ–6Å÷6†#ScÖæWuö6æöæ–6Åö†6‚À¢&W—%÷FõöVF—Eö†æFöfeö–C×7G"€¢&W—%÷FõöVF—Eö†æFöfe²&†æFöfeö–B%Ð¢’À¢¢6VÆbå÷7–æ5÷&W—%÷6¶vUö–åö‡V"€¢æÖW76RÂ&ö¦V7Eö–BÂW†7E÷6¶vP¢¢6VÆbç7F÷&Ræ6öÖÖ—E÷7F÷'–&ö&E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&6Uö'F–f7Eö–BÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7V66W76÷%ö'F–f7Eö–C×7G"‡&W7öç6Ræ'F–f7Eö–B’À¢7V66W76÷%ö6æöæ–6Åö†6ƒÖæWuö6æöæ–6Åö†6‚À¢¢&WGW&âW†7E÷6¶vP ¢G'“ ¢6öç7VÖVE÷6¶vRÒf–æÆ—¦UöW†7E÷F6…÷7V66W76÷"‚¢G&ç67F–öå÷&W&VBÒfÇ6P¢6¶vUö6Æ–ÖVBÒfÇ6P¢W†6WBW†6WF–öâ2f—'7Eöf–æÆ—¦UöW'&÷# ¢G'“ ¢6öç7VÖVE÷6¶vRÒf–æÆ—¦UöW†7E÷F6…÷7V66W76÷"‚¢G&ç67F–öå÷&W&VBÒfÇ6P¢6¶vUö6Æ–ÖVBÒfÇ6P¢W†6WBW†6WF–öâ2&WÆ•öW'&÷# ¢–b†6GG"‡&WÆ•öW'&÷"Â&FEöæ÷FR"“ ¢&WÆ•öW'&÷"æFEöæ÷FR€¢&–æ—F–ÂF6‚f–æÆ—¦F–öâW'&÷#¢ ¢²7G"†f—'7Eöf–æÆ—¦UöW'&÷"•³£S%Ð¢¢f÷"6ÆVçWöW'&÷"–â&öÆÆ&6µ÷F6‚€¢'F6‚7V66W76÷"42f–ÆVC¢ ¢²G—R‡&WÆ•öW'&÷"’åõöæÖUõð¢“ ¢–b†6GG"‡&WÆ•öW'&÷"Â&FEöæ÷FR"“ ¢&WÆ•öW'&÷"æFEöæ÷FR€¢b'F6‚&öÆÆ&6²v&æ–æs¢¶6ÆVçWöW'&÷'Ò ¢¢&—6P ¢G'“ ¢V&Æ–6F–öâÒ6VÆbå÷6WE÷7V66W76÷%ö6æF–FFU÷V&Æ–6F–öâ€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢6æöæ–6Åö'F–f7Eö–CÖ6æF–FFUö–G5³ÒÀ¢†æFöfeö–CÖ6æF–FFUö–G5³%ÒÀ¢G&ç67F–öåö–CÖ6Æ–Õö–BÀ¢7FFSÒ&6öÖÖ—GFVB"À¢¢&W7öç6RæFF²&'F–f7B%ÒÒV&Æ–6F–öå²&'F–f7G2%Õ°¢6æF–FFUö–G5³Ð¢Ð¢&W7öç6RæFF²&†æFöfb%ÒÒ6VÆbåö†æFöfe÷7VÖÖ'’€¢V&Æ–6F–öå²&†æFöfb%Ð¢¢W†6WBW†6WF–öâ2&ö¦V7F–öåöW†3 ¢&W7öç6RæFF²'V&Æ–6F–öå÷&ö¦V7F–öå÷v&æ–ær%ÒÒ7G"€¢&ö¦V7F–öåöW†0¢•³£S%Ð¢VÇ6S ¢&öÆÆ&6µöW'&÷'2Ò&öÆÆ&6µ÷F6‚€¢'F6‚F–Bæ÷B726ö×ÆWFRfÆ–FF–öâ ¢¢–b&öÆÆ&6µöW'&÷'3 ¢&—6R'VçF–ÖTW'&÷"€¢'F6…÷&öÆÆ&6µö–æ6ö×ÆWFS¢"²#²"æ¦ö–â‡&öÆÆ&6µöW'&÷'2¢¢&VÆV6VE÷6¶vRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'&W—%÷6¶vW2"À¢&V6÷&Eö–C×&W—%÷6¶vUö–BÀ¢¢–b&VÆV6VE÷6¶vRævWB‚'7FGW2"’Ò&—77VVB# ¢&—6R'VçF–ÖTW'&÷"€¢'F6…÷&öÆÆ&6µö–æ6ö×ÆWFS¢&W—"6¶vRv2æ÷B&VÆV6VB ¢¢6¶vUö6Æ–ÖVBÒfÇ6P¢6öç7VÖVE÷6¶vRÒ&VÆV6VE÷6¶vP¢–b—6–ç7Fæ6R‡&W7öç6RæFFævWB‚&†æFöfb"’ÂF–7B“ ¢&W7öç6RæFF²&†æFöfb%Õ²'7FGW2%ÒÒ'&V¦V7FVB ¢&W7öç6RæFF²&†æFöfb%Õ²'V&Æ–6F–öå÷7FFR%ÒÒ&&÷'FVB  ¢&W7öç6RæFFçWFFR€¢°¢&gVÆÅ÷&öGV7F–öå÷fÆ–FF–öâ#¢G'VRÀ¢&f–æÅögVÆÅ÷fÆ–FF–öâ#¢G'VRÀ¢&&6Uö'F–f7Eö–B#¢&6Uö'F–f7Eö–BÀ¢&&6Uö6æöæ–6Åö†6‚#¢&6Uö6æöæ–6Åö†6‚À¢&æWuö6æöæ–6Åö†6‚#¢æWuö6æöæ–6Åö†6‚À¢&VF—Eö6W'F–f–6FUö–B#¢VF—Eö6W'F–f–6FUö–BÀ¢'&W—%÷6¶vUö–B#¢&W—%÷6¶vUö–BÀ¢'&W—%÷6¶vR#¢6öç7VÖVE÷6¶vRÀ¢&VF—E÷Fõ÷&W—%ö†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’†VF—E÷Fõ÷&W—%ö†æFöfb’À¢&–×7E÷66÷R#¢6÷'FVB†–×7Eö–G2’À¢&Æ–VEö÷W&F–öç2#¢Æ–VEö÷W&F–öç2À¢'&W÷'B#¢&W÷'BÀ¢Ð¢¢&W7öç6Rç7VÖÖ'’Ò€¢.[˜:ŽKúîZHÞ[{.ZèÎh‰ûÙÎXZŽŠŽyIþKª~š(Nj8˜	®‹ø~ûÙÎ[è^xºÎz¸¾{¸ŽZê ¢–b&W7öç6Rç7FGW2ÓÒ$ôET5D”ôåõ$TdÄ”t…Eõ50¢VÇ6R&W7öç6Rç7VÖÖ'¢¢&WGW&â&W7öç6P ¢öfVæ6U÷&öÆUö÷W&F–öà¢÷&VÆV6Uö6öç7V×F–öåö6Æ–×5ööåöW'&÷ ¢FVbVF—E÷7F÷'–&ö&B€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢'F–f7Eö–C¢7G"À¢VF—Eö7–6ÆUö–C¢7G"ÂæöæRÀ¢v÷&µö÷&FW%ö–C¢7G"ÂæöæRÒæöæRÀ¢†æFöfeö'F–f7Eö–C¢7G"ÂæöæRÒæöæRÀ¢f÷&6UöÖ6†–æU÷&V6†V6³¢&ööÂÒfÇ6RÀ¢6VÖçF–5öVF—C¢F–7E·7G"Âç•ÒÂæöæRÒæöæRÀ¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢6VÆbå÷&WV—&U÷'VçF–ÖR‚¢7F'FVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢2F†R–æFWVæFVçBÖVF—BWF†÷&—G’—2fW&–f–VB&Vf÷&R'F–f7B66W72à¢2–â'F–7VÆ"Âw&öærVF—B6W76–öâ×W7Bæ÷BG&–vvW"ÆVv7’v÷&¶&öö°¢26æöæ–6Â&V6÷fW'’öÆ–æ¶–ær26–FRVffV7Bà¢VF—E÷v÷&µö÷&FW"Ò6VÆbåöVç7W&UöVF—E÷v÷&µö÷&FW"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢&WVW7E÷6W76–öåö–C×&WVW7E÷6W76–öåö–BÀ¢¢'VçF–ÖU÷FW7Eö6W'F–f–6FRÒ6VÆbå÷&WV—&U÷'VçF–ÖU÷FW7Eö6W'F–f–6FR†æÖW76RÂ&ö¦V7Eö–B¢7FvU÷G&6Uö–BÒöæWuö–B‚%E$2"¢7F÷'–&ö&BÂ&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–BÀ¢W'6—7E÷&V6÷fW&VEö6æöæ–6ÃÔfÇ6RÀ¢&WV—&Uö&÷VæE÷&ö¦V7F–öãÕG'VRÀ¢¢ÆöFVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢†æFöfbÒ6VÆbåöÖF6†–æuö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢†æFöfeö–CÖ†æFöfeö'F–f7Eö–BÀ¢¢–b†æFöfb—2æöæS ¢–çFW&æÅ÷&öGV7F–öâÒ&ööÂ€¢&V6÷&BævWB‚'v÷&µö÷&FW%ö–B"¢÷"&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB‚'&öGV7F–öåö6W'F–f–6FUö–B"¢¢–b–çFW&æÅ÷&öGV7F–öã ¢&—6RfÇVTW'&÷"€¢&†æFöfe÷&WV—&VC¢–çFW&æÆÇ’&öGV6VB7F÷'–&ö&B†2æòÖF6†–ær&öGV7F–öå÷FõöVF—B†æFöfb ¢¢†æFöfbÒ6VÆbå÷W'6—7EöVF—Eö–çF¶R€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢&V6÷&C×&V6÷&BÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢7FvU÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢¢6VÆbåö76W'Eö†—7F÷&–6Åö†æFöfeöVF—Eö–çF¶R€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢†æFöfcÖ†æFöfbÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢¢6–væVEö7–6ÆUö–BÒ7G"††æFöfbævWB‚&VF—Eö7–6ÆUö–B"’÷"""¢–bVF—Eö7–6ÆUö–B—2æ÷BæöæRæB7G"†VF—Eö7–6ÆUö–B’Ò6–væVEö7–6ÆUö–C ¢&—6RfÇVTW'&÷"€¢&VF—Eö7–6ÆUöÖ—6ÖF6ƒ¢VF—Eö7–6ÆUö–B—26W'fW"ÖFW&—fVBg&öÒF†R6–væVB†æFöfb ¢¢VF—Eö7–6ÆUö–BÒ6–væVEö7–6ÆUö–@¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ€¢æÖW76RÂVF—E÷v÷&µö÷&FW"Â&WVW7E÷6W76–öåö–@¢¢6VÆbåö76W'EöVF—E÷6W&F–öâ€¢æÖW76RÂ&ö¦V7Eö–BÂVF—E÷v÷&µö÷&FW"Â†æFöf`¢¢&÷VæF'•ö†6‚Ò6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B¢&öGV7F–öåö6ö×öæVçG2Ò†æFöfbævWB‚&66†Uö6ö×öæVçG2"Â·Ò¢6÷W&6Uöf–ævW'&–çBÒ7G"€¢&öGV7F–öåö6ö×öæVçG2ævWB‚'6÷W&6R"¢÷"†æFöfbævWB‚'6÷W&6Uöf–ævW'&–çB"¢÷"6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"’À¢&V6÷&BÀ¢¢¢f–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢7F÷'–&ö&BÀ¢&V6÷&BÀ¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢¢†æFöfeöf–ævW'&–çG2Ò†æFöfbævWB‚&f–ævW'&–çG2"¢–bæ÷B—6–ç7Fæ6R††æFöfeöf–ævW'&–çG2ÂF–7B’÷"ç’€¢†æFöfeöf–ævW'&–çG2ævWB†¶W’’ÒfÇVP¢f÷"¶W’ÂfÇVR–âf–ævW'&–çG2æ—FV×2‚¢“ ¢&—6RfÇVTW'&÷"‚&†æFöfeöf–ævW'&–çEöÖ—6ÖF6ƒ¢6÷W&6Rö'F–f7Bö6æöæ–6Â÷'VçF–ÖR&–æF–ær6†ævVB"¢&W6öÇfVE÷&VVF—E÷&ö÷C¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢&W6öÇfVE÷&VVF—EöÆFW7C¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢6ö×ÆWFVE÷&VVF—E÷&WÆ’ÒfÇ6P¢6ö×ÆWFVE÷&VVF—E÷&V6V—C¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçBÒ&ööÂ€¢f÷&6UöÖ6†–æU÷&V6†V6²æB6VÖçF–5öVF—B—2æ÷BæöæP¢¢–b€¢†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢æBæ÷B†æFöfbævWB‚'&VVF—Eö¶–æB"¢æB†æFöfbævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"¢ÒVF—E÷v÷&µö÷&FW"ævWB‚'v÷&µö÷&FW%ö–B"¢“ ¢&—6RfÇVTW'&÷"€¢&†æFöfe÷7FFUö6öæfÆ–7C¢6öç7VÖVB&öGV7F–öâ†æFöfb&VÆöæw2FòF–ffW&VçBVF—Bv÷&²÷&FW" ¢¢–b€¢†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢÷"†æFöfbævWB‚'&VVF—Eö¶–æB"¢ÓÒ&–æFWVæFVçEöVF—E÷7V66W76÷" ¢“ ¢&W6öÇfVE÷&VVF—E÷&ö÷BÂ&W6öÇfVE÷&VVF—EöÆFW7BÒ€¢6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÖf÷&6UöÖ6†–æU÷&V6†V6²À¢&W—%÷&ö¦V7F–öãÔfÇ6RÀ¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçCÒ€¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&Vç@¢’À¢¢¢–b€¢f÷&6UöÖ6†–æU÷&V6†V6°¢æB&W6öÇfVE÷&VVF—EöÆFW7BævWB‚'7FGW2"¢–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢“ ¢2&W7F'B&W7VÖW2F†R6W'fW"ÖÖ–çFVBWfVçB–FVçF—G’&F†W ¢2F†â7&VF–ær6V6öæB&÷WFRf÷"F†R6ÖR&VçBà¢7FvU÷G&6Uö–BÒ7G"€¢&W6öÇfVE÷&VVF—EöÆFW7E²'&VVF—EöWfVçE÷G&6Uö–B%Ð¢¢†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢VÆ–bæ÷Bf÷&6UöÖ6†–æU÷&V6†V6³ ¢2â÷&F–æ'’&WG'’öbF†R÷&–v–æÂ&öGV7F–öâ„äB×W7B&WÆ¢2F†RæWvW7B6ö×ÆWFVB7V66W76÷"fW&F–7BÂæWfW"&W7W'&V7BF†P¢2&ö÷B6W'F–f–6FRF†Bâ–æFWVæFVçB&RÖVF—B7WW'6VFVBà¢†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢VÆ–b&W6öÇfVE÷&VVF—EöÆFW7BævWB‚'7FGW2"’ÓÒ&6öç7VÖVB# ¢6ö×ÆWFVE÷&VVF—E÷&V6V—BÒ€¢6VÆbåö6ö×ÆWFVE÷&VVF—E÷&V6V—B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢ÆFW7Eö†æFöfc×&W6öÇfVE÷&VVF—EöÆFW7BÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢6VÖçF–5öVF—C×6VÖçF–5öVF—BÀ¢f–ævW'&–çG3Öf–ævW'&–çG2À¢¢¢–b6ö×ÆWFVE÷&VVF—E÷&V6V—B—2æ÷BæöæS ¢2F†R6–væVB6W'F–f–6FR—2GW&&ÆR6ö×ÆWF–öâ&V6V—@¢2f÷"F†—2æ÷&ÖÆ—¦VB6VÖçF–27V&Ö—76–öââ&WÆ––ærF†P¢26ÖRf÷&6R&WVW7B×W7Bæ÷B'W&âæ÷F†W"VF—B&÷VæBà¢6ö×ÆWFVE÷&VVF—E÷&WÆ’ÒG'VP¢†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢VæF–æu÷&VVF—Eö†æFöfbÒ€¢&W6öÇfVE÷&VVF—EöÆFW7@¢–bf÷&6UöÖ6†–æU÷&V6†V6°¢æB—6–ç7Fæ6R‡&W6öÇfVE÷&VVF—EöÆFW7BÂF–7B¢æB&W6öÇfVE÷&VVF—EöÆFW7BævWB‚'7FGW2"¢–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢VÇ6RæöæP¢¢66†U÷66÷Uö†æFöfbÒVæF–æu÷&VVF—Eö†æFöfb÷"†æFöf`¢66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢'F–f7Eöf–ævW'&–çC×7G"‡&V6÷&BævWB‚&f–ævW'&–çB"’÷"""’À¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢66÷S×6VÆbåö–æFWVæFVçEöVF—Eö66†U÷66÷R€¢66†U÷66÷Uö†æFöfbÂ'F–f7Eö–@¢’À¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"‡'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%Ò’À¢¢VF—Eö66†Uö¶W’Ò6VÆbåö66†Uö¶W•ög&öÕö6ö×öæVçG2†66†Uö6ö×öæVçG2¢66†VEö6W'F–f–6FRÒ6ö×ÆWFVE÷&VVF—E÷&V6V—@¢&WW6VEöVF—EöWfVçC¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢–b€¢66†VEö6W'F–f–6FR—2æöæP¢æBVæF–æu÷&VVF—Eö†æFöfb—2æ÷BæöæP¢“ ¢66†VEö6W'F–f–6FRÂ&WW6VEöVF—EöWfVçBÒ€¢6VÆbå÷&V6÷fW&&ÆU÷&VVF—Eö6W'F–f–6FR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢VæF–æuö†æFöfc×VæF–æu÷&VVF—Eö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢¢¢&V6÷fW&–æu÷&VVF—Eö6ö×ÆWF–öâÒ&ööÂ€¢VæF–æu÷&VVF—Eö†æFöfb—2æ÷BæöæP¢æB66†VEö6W'F–f–6FR—2æ÷BæöæP¢¢&V6÷fW&–æu÷&ö÷Eö6ö×ÆWF–öâÒfÇ6P¢–b66†VEö6W'F–f–6FR—2æöæRæBæ÷Bf÷&6UöÖ6†–æU÷&V6†V6³ ¢66†Uö6æF–FFW2Ò°¢6æF–FFP¢f÷"6æF–FFR–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G2€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢¢–b6æF–FFRævWB‚&66†Uö¶W’"’ÓÒVF—Eö66†Uö¶W¢Ð¢–b66†Uö6æF–FFW3 ¢27&VFVEöF†2öæR×6V6öæB&V6—6–öâÂ6òGvòVF—B&÷VæG0¢26âF–RâF†RVæBÖöæÇ’ÆVFvW"WfVçB÷&FW"—2F†RGW&&ÆP¢2WF†÷&—G’f÷"v†–6‚6ÖRÖVçf—&öæÖVçBfW&F–7B7WW'6VFW2F†P¢2÷F†W#²&æFöÒ6W'F–f–6FRf–ÆVæÖW2×W7BæWfW"FV6–FR—Bà¢WfVçEö÷&FW"Ò°¢7G"†WfVçBævWB‚&WfVçEö–B"’÷"""“¢–æFW€¢f÷"–æFW‚ÂWfVçB–âVçVÖW&FR€¢6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B’ævWB€¢&WfVçG2"ÂµÐ¢¢¢–b—6–ç7Fæ6R†WfVçBÂF–7B¢Ð¢66†VEö6W'F–f–6FRÒÖ‚€¢66†Uö6æF–FFW2À¢¶W“ÖÆÖ&F6æF–FFS¢€¢WfVçEö÷&FW"ævWB€¢7G"†6æF–FFRævWB‚&WfVçEö–B"’÷"""’ÂÓ¢’À¢7G"†6æF–FFRævWB‚&7&VFVEöB"’÷"""’À¢7G"†6æF–FFRævWB‚&6W'F–f–6FUö–B"’÷"""’À¢’À¢¢–b€¢66†VEö6W'F–f–6FR—2æ÷BæöæP¢æBæ÷Bf÷&6UöÖ6†–æU÷&V6†V6°¢æB†æFöfbævWB‚'7FGW2"’–â²'&W&VB"Â&6öç7VÖ–ær'Ð¢æBæ÷B†æFöfbævWB‚'&VVF—Eö¶–æB"¢“ ¢&V6÷fW&VE÷&ö÷Eö6W'F–f–6FRÒ€¢6VÆbå÷&V6÷fW&&ÆU÷&ö÷EöVF—Eö6W'F–f–6FR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢VæF–æuö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢6æF–FFSÖ66†VEö6W'F–f–6FRÀ¢¢¢&V6÷fW&–æu÷&ö÷Eö6ö×ÆWF–öâÒ€¢&V6÷fW&VE÷&ö÷Eö6W'F–f–6FR—2æ÷BæöæP¢¢–b&V6÷fW&VE÷&ö÷Eö6W'F–f–6FR—2æ÷BæöæS ¢66†VEö6W'F–f–6FRÒ&V6÷fW&VE÷&ö÷Eö6W'F–f–6FP¢–b6ö×ÆWFVE÷&VVF—E÷&WÆ’æB66†VEö6W'F–f–6FR—2æ÷BæöæS ¢6ö×ÆWFVEöWfVçG2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆöEöÆVFvW"€¢æÖW76RÂ&ö¦V7Eö–@¢’ævWB‚&WfVçG2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB&÷rævWB‚&WfVçEö–B"¢ÓÒ66†VEö6W'F–f–6FRævWB‚&WfVçEö–B"¢Ð¢–bÆVâ†6ö×ÆWFVEöWfVçG2’Ò ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–æ6ö×ÆWFS¢6ö×ÆWFVB&V6V—B†2æòVæ—VRÆVFvW"WfVçB ¢¢&WW6VEöVF—EöWfVçBÒ6ö×ÆWFVEöWfVçG5³Ð¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçC×&WW6VEöVF—EöWfVçBÀ¢†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢6W'F–f–6FSÖ66†VEö6W'F–f–6FRÀ¢¢66†Uö†—BÒ66†VEö6W'F–f–6FR—2æ÷BæöæP¢–b66†VEö6W'F–f–6FR—2æ÷BæöæS ¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†66†VEö6W'F–f–6FR¢÷ ¢æ÷B6VÆbå÷fÆ–Eö66†Uö–FVçF—G’†66†VEö6W'F–f–6FRÂTD•Eô4Ä55ô”äDUTäDTåB¢÷"66†VEö6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"’Ò6æöæ–6Åö†6€¢÷"66†VEö6W'F–f–6FRævWB‚&66†Uö6ö×öæVçG2"’Ò66†Uö6ö×öæVçG0¢÷"66†VEö6W'F–f–6FRævWB‚'6VÖçF–5öVF—Eö6öçG&7B"¢Ò4TÔåD”5ôTD•Eô4ôåE$5EõdU%4”ôà¢“ ¢&—6RfÇVTW'&÷"‚&66†VB–æFWVæFVçBVF—B6W'F–f–6FR–FVçF—G’—2–çfÆ–B"¢&W÷'BÒ6÷’æFVW6÷’†66†VEö6W'F–f–6FRævWB‚'&W÷'B"’¢–bæ÷B—6–ç7Fæ6R‡&W÷'BÂF–7B’÷"æ÷B—6–ç7Fæ6R‡&W÷'BævWB‚&—77VW2"’ÂÆ—7B“ ¢&—6RfÇVTW'&÷"‚&66†VB–æFWVæFVçBVF—B6W'F–f–6FR—2–çfÆ–B"¢–b66†VEö6W'F–f–6FRævWB‚'&W÷'E÷6†#Sb"’Ò6†#Seö'—FW2…÷7F&ÆUö§6öåö'—FW2‡&W÷'B’“ ¢&—6RfÇVTW'&÷"‚&66†VB–æFWVæFVçBVF—B&W÷'Bf–ævW'&–çBÖ—6ÖF6‚"¢–b€¢æ÷B—6–ç7Fæ6R‡&W÷'BævWB‚'6VÖçF–5öVF—B"’ÂF–7B¢÷"&W÷'BævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢Ò66†VEö6W'F–f–6FRævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢“ ¢&—6RfÇVTW'&÷"€¢&66†VB–æFWVæFVçBVF—B6W'F–f–6FR†2æò&÷VæB6VÖçF–2&Wf–Wr ¢¢6W'F–f–6FRÒ66†VEö6W'F–f–6FP¢266†R÷&V6÷fW'’&W7öç6R—2&÷VæBFòF†R÷&–v–æÂ6–væVBVF—@¢2G&6Râ&WW6–ær—Bfö–G2ÖçVf7GW&–ær&WVW7BFVÆVÖWG'’F†@¢2v÷VÆB×WFFRWF†÷&—G’GW&–ærâ÷F†W'v—6R&VBÖöæÇ’&WÆ’à¢7FvU÷G&6Uö–BÒ7G"†6W'F–f–6FRævWB‚'7FvU÷G&6Uö–B"’÷"""¢–bæ÷B7FvU÷G&6Uö–C ¢&—6RfÇVTW'&÷"€¢&66†VB–æFWVæFVçBVF—B6W'F–f–6FR†2æòVF—BG&6R ¢¢VÇ6S ¢–b6VÖçF–5öVF—B—2æöæS ¢&—6RfÇVTW'&÷"€¢'6VÖçF–5öVF—E÷&WV—&VC¢æòÖF6†–ær–æFWVæFVçBÖVF—B6W'F–f–6FRW†—7G3² ¢'7V&Ö—BF†R6ö×ÆWFRV–v‡BÖÆ–W"6VÖçF–2&Wf–Wr ¢¢&W÷'BÒ6VÆbåöGF6…ö–æFWVæFVçEöWf–FVæ6UöÖG&—‚€¢fÆ–FFU÷7F÷'–&ö&BçfÆ–FFU÷&ö¦V7B‡7F÷'–&ö&BÂÖöFSÒ&VF—B"’À¢7F÷'–&ö&BÀ¢6VÖçF–5öVF—BÀ¢¢6ö×ÆWFVEö6öç7VÖVE÷&WÆ’Ò&ööÂ€¢66†VEö6W'F–f–6FR—2æ÷BæöæP¢æ@¢VæF–æu÷&VVF—Eö†æFöfb—2æöæP¢æB†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢æB†æFöfbævWB‚&VF—Eö6W'F–f–6FUö–B"¢ÓÒ66†VEö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢¢–b6ö×ÆWFVEö6öç7VÖVE÷&WÆ’æB&WW6VEöVF—EöWfVçB—2æöæS ¢6ö×ÆWFVEöWfVçG2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆöEöÆVFvW"€¢æÖW76RÂ&ö¦V7Eö–@¢’ævWB‚&WfVçG2"ÂµÒ¢–b—6–ç7Fæ6R‡&÷rÂF–7B¢æB&÷rævWB‚&WfVçEö–B"’ÓÒ6W'F–f–6FRævWB‚&WfVçEö–B"¢Ð¢–bÆVâ†6ö×ÆWFVEöWfVçG2’Ò ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–æ6ö×ÆWFS¢6ö×ÆWFVBVF—B†2æòVæ—VRÆVFvW"WfVçB ¢¢&WW6VEöVF—EöWfVçBÒ6ö×ÆWFVEöWfVçG5³Ð¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçC×&WW6VEöVF—EöWfVçBÀ¢†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢¢–b&W÷'BævWB‚&—77VW2"“ ¢2&W—"WF†÷&—¦F–öâ—2'BöbF†RVF—B6öçG&7BâfÆ–FFP¢2WfW'’F&vWB&Vf÷&RF†R†æFöfb6Æ–ÒÂÆVFvW"WfVçBÂ6W'F–f–6FRÀ¢2÷"7V66W76÷"&÷WFR—2ÆÆ÷vVBFò&V6öÖRGW&&ÆRà¢6VÆbå÷&W—%ö—77VU÷F&vWG2€¢7F÷'–&ö&BÂÆ—7B‡&W÷'BævWB‚&—77VW2"ÂµÒ’¢¢&V6÷fW&–æuöWfVçEööæÇ’Ò&ööÂ€¢&WW6VEöVF—EöWfVçB—2æ÷BæöæP¢æB66†VEö6W'F–f–6FR—2æöæP¢¢–b&WW6VEöVF—EöWfVçB—2æ÷BæöæS ¢W‡V7FVE÷&W7VÇBÒ€¢&f–Åö7W'&VçB"–b&W÷'BævWB‚&—77VW2"’VÇ6R'72 ¢¢WfVçE÷6VÖçF–5÷6†Ò&WW6VEöVF—EöWfVçBævWB€¢'6VÖçF–5öVF—E÷6†#Sb ¢¢WfVçE÷&W÷'E÷6†Ò&WW6VEöVF—EöWfVçBævWB€¢'6VÖçF–5÷&W÷'Eö–çWE÷6†#Sb ¢¢ÆVv7•÷6VÖçF–5÷—"Ò€¢WfVçE÷6VÖçF–5÷6†–â„æöæRÂ""¢æBWfVçE÷&W÷'E÷6†–â„æöæRÂ""¢¢'F–Å÷6VÖçF–5÷—"Ò€¢†WfVçE÷6VÖçF–5÷6†–â„æöæRÂ""’¢Ò†WfVçE÷&W÷'E÷6†–â„æöæRÂ""’¢¢–b'F–Å÷6VÖçF–5÷—"÷"€¢ÆVv7•÷6VÖçF–5÷— ¢æB€¢66†VEö6W'F–f–6FR—2æöæP¢÷"æ÷B6VÆbå÷6VÆVEöÆVv7•÷&V6÷fW'•öWfVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"‡&WW6VEöVF—EöWfVçBævWB‚&WfVçEö–B"’÷"""’À¢¢¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöWfVçE÷&V6÷fW'•öÖ—6ÖF6ƒ¢6VÖçF–2WfVçB–FVçF—G’—2–æ6ö×ÆWFR ¢¢–b€¢€¢WfVçE÷6VÖçF–5÷6†¢æ÷B–â„æöæRÂ""¢æBWfVçE÷6VÖçF–5÷6†¢Ò&W÷'BævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢¢÷"€¢WfVçE÷&W÷'E÷6†æ÷B–â„æöæRÂ""¢æBWfVçE÷&W÷'E÷6†¢Ò6VÆbåöVF—EöWfVçE÷&W÷'Eö–çWE÷6†#Sb‡&W÷'B¢¢÷"&WW6VEöVF—EöWfVçBævWB‚'&W7VÇB"’ÒW‡V7FVE÷&W7VÇ@¢÷"–çB€¢&WW6VEöVF—EöWfVçBævWB‚&6†V6¶VE÷6†÷Eö6÷VçB"’÷"Ó¢¢ÒÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöWfVçE÷&V6÷fW'•öÖ—6ÖF6ƒ¢&W7V&Ö—GFVB6VÖçF–2&W÷'BFöW2æ÷BW†7FÇ’ÖF6‚F†RGW&&ÆRWfVçB ¢¢–b&V6÷fW&–æuöWfVçEööæÇ’æB€¢WfVçE÷6VÖçF–5÷6†¢Ò&W÷'BævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢÷"WfVçE÷&W÷'E÷6†¢Ò6VÆbåöVF—EöWfVçE÷&W÷'Eö–çWE÷6†#Sb‡&W÷'B¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöWfVçE÷&V6÷fW'•öÖ—6ÖF6ƒ¢&W7V&Ö—GFVB6VÖçF–2&W÷'BFöW2æ÷BW†7FÇ’ÖF6‚F†RGW&&ÆRWfVçB ¢¢–b66†VEö6W'F–f–6FR—2æ÷BæöæRæB€¢66†VEö6W'F–f–6FRævWB‚&WfVçEö–B"¢Ò&WW6VEöVF—EöWfVçBævWB‚&WfVçEö–B"¢÷"66†VEö6W'F–f–6FRævWB‚'&W7VÇB"’ÒW‡V7FVE÷&W7VÇ@¢÷"–çB€¢66†VEö6W'F–f–6FRævWB‚&6†V6¶VE÷6†÷Eö6÷VçB"’÷"Ó¢¢ÒÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’¢÷"€¢WfVçE÷6VÖçF–5÷6†¢æ÷B–â„æöæRÂ""¢æB66†VEö6W'F–f–6FRævWB‚'6VÖçF–5öVF—E÷6†#Sb"¢ÒWfVçE÷6VÖçF–5÷6†¢¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—EöWfVçE÷&V6÷fW'•öÖ—6ÖF6ƒ¢6W'F–f–6FRæBGW&&ÆRWfVçBF—6w&VR ¢¢6VÆbå÷fÆ–FFU÷&VVF—E÷&V6÷fW'•öWfVçEö6öçFVçB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢WfVçC×&WW6VEöVF—EöWfVçBÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢&W÷'C×&W÷'BÀ¢¢–b€¢&W6öÇfVE÷&VVF—E÷&ö÷B—2æ÷BæöæP¢æB&W6öÇfVE÷&VVF—EöÆFW7B—2æ÷BæöæP¢“ ¢&W6öÇfVE÷&VVF—E÷&ö÷BÂ&W6öÇfVE÷&VVF—EöÆFW7BÒ€¢6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÖf÷&6UöÖ6†–æU÷&V6†V6²À¢&W—%÷&ö¦V7F–öãÖæ÷B6ö×ÆWFVEö6öç7VÖVE÷&WÆ’À¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçCÒ€¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&Vç@¢’À¢¢¢–bVæF–æu÷&VVF—Eö†æFöfb—2æ÷BæöæS ¢VæF–æu÷&VVF—Eö†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢VÆ–b6ö×ÆWFVE÷&VVF—E÷&WÆ“ ¢†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢–b6ö×ÆWFVEö6öç7VÖVE÷&WÆ“ ¢&W—%÷&ö¦V7F–öåö6ö×ÆWFRÂ÷6¶vRÂ÷&W—%ö†æFöfbÒ€¢6VÆbåö6ö×ÆWFVE÷&W—%÷&ö¦V7F–öå÷7FFR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢†æFöfcÖ†æFöfbÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢WfVçC×&WW6VEöVF—EöWfVçBÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢¢¢–bæ÷B&W—%÷&ö¦V7F–öåö6ö×ÆWFS ¢6VÆbå÷W'6—7E÷&W—%÷6¶vUöæEö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢&öGV7F–öåö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢&ö÷Eö†æFöfcÒ€¢&W6öÇfVE÷&VVF—E÷&ö÷B÷"†æFöf`¢’À¢¢&WGW&â6VÆbåö6ö×ÆWFVEöVF—E÷&W7öç6R€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢WfVçC×&WW6VEöVF—EöWfVçBÀ¢f–ævW'&–çG3Öf–ævW'&–çG2À¢¢fÆ–FFVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢WfVçEö–BÒ€¢7G"†6W'F–f–6FRævWB‚&WfVçEö–B"’¢–b66†VEö6W'F–f–6FP¢VÇ6R7G"‡&WW6VEöVF—EöWfVçBævWB‚&WfVçEö–B"’¢–b&WW6VEöVF—EöWfVçB—2æ÷BæöæP¢VÇ6RöæWuö–B‚$UeB"¢¢&VVF—FVE÷&ö÷Eö†æFöfc¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢&VVF—FVE÷&÷WFU÷G&6S¢F–7E·7G"Âç•ÒÂæöæRÒæöæP¢—5÷&VVF—E÷7V66W76÷"ÒfÇ6P¢–bVæF–æu÷&VVF—Eö†æFöfb—2æ÷BæöæS ¢–b&W6öÇfVE÷&VVF—E÷&ö÷B—2æöæS ¢&—6RfÇVTW'&÷"€¢'&VVF—EöÆ–æVvUö–çfÆ–C¢VæF–ær7V66W76÷"†2æòfW&–f–VB&ö÷B ¢¢&VVF—FVE÷&ö÷Eö†æFöfbÒ&W6öÇfVE÷&VVF—E÷&ö÷@¢†æFöfbÂ&VVF—FVE÷&÷WFU÷G&6RÒ€¢6VÆbå÷W'6—7E÷&VVF—E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&ö÷C×&VVF—FVE÷&ö÷Eö†æFöfbÀ¢&VçC×VæF–æu÷&VVF—Eö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢VF—EöWfVçE÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢f–ævW'&–çG3Öf–ævW'&–çG2À¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6†÷Eö6÷VçCÖÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢¢¢—5÷&VVF—E÷7V66W76÷"ÒG'VP¢VÆ–b€¢†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢æBf÷&6UöÖ6†–æU÷&V6†V6°¢æBæ÷B6ö×ÆWFVE÷&VVF—E÷&WÆ¢“ ¢&VVF—FVE÷&ö÷Eö†æFöfbÒ&W6öÇfVE÷&VVF—E÷&ö÷@¢ÆFW7Eö†æFöfbÒ&W6öÇfVE÷&VVF—EöÆFW7@¢–b&VVF—FVE÷&ö÷Eö†æFöfb—2æöæR÷"ÆFW7Eö†æFöfb—2æöæS ¢&VVF—FVE÷&ö÷Eö†æFöfbÂÆFW7Eö†æFöfbÒ€¢6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÕG'VRÀ¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçCÒ€¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&Vç@¢’À¢¢¢2&Wf–WrF†RW†7BÆVFvW"G&ç6—F–öâöââ—6öÆFVB6÷’&Vf÷&P¢2V&Æ—6†–ær&÷WFRâ&÷VæBÖÆ–Ö—BÂ66†VÖÂ&V÷VâÂæBWF†÷&—G¢2&V¦V7F–öâF†W&Vf÷&RÆVfRæò7V66W76÷"÷"ÆVFvW"×WFF–öâà¢&Wf–Wu÷&W7VÇBÒ6VÆbå÷&V6÷&EöVF—B€¢æÖW76SÖæÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢&W÷'CÖ6÷’æFVW6÷’‡&W÷'B’À¢ÆVFvW#Ö6÷’æFVW6÷’€¢6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B¢’À¢VF—Eö7–6ÆUö–CÖVF—Eö7–6ÆUö–BÀ¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢VF—Eö66†Uö¶W“ÖVF—Eö66†Uö¶W’À¢WfVçEö–CÖWfVçEö–BÀ¢†æFöfeö–CÖb$„äBÕ$Ud”Ur×¶WfVçEö–GÒ"À¢7FvU÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢f–ævW'&–çG3Öf–ævW'&–çG2À¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢’À¢v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢fÆ–FF–öå÷&öÆSÒ&–æFWVæFVçEöVF—B"À¢ÆÆ÷u÷&V÷VãÕG'VRÀ¢¢–b&Wf–Wu÷&W7VÇBævWB‚'7FGW2"’ÓÒ&&Æö6¶VB# ¢&WGW&âFööÅ&W7öç6R€¢7FGW3Ò&&Æö6¶VB"À¢7VÖÖ'“Ò.ZêŠê™‹¾ZîûÙÎ[{.‹ëã>‹ÚîûÙÎ™ÈŠhKˆ®k‹Ž˜xÞ[»¢"À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢FF×°¢&VF—B#¢&Wf–Wu÷&W7VÇBÀ¢&VF—Eö6Æ72#¢TD•Eô4Ä55ô”äDUTäDTåBÀ¢&VF—E÷v÷&µö÷&FW%ö–B#¢VF—E÷v÷&µö÷&FW%°¢'v÷&µö÷&FW%ö–B ¢ÒÀ¢&†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’††æFöfb’À¢'6–FUöVffV7G2#¢&æöæR"À¢ÒÀ¢¢&VVF—FVE÷&ö÷Eö†æFöfbÂÆFW7Eö†æFöfbÒ€¢6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÕG'VRÀ¢&W—%÷&ö¦V7F–öãÕG'VRÀ¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&VçCÒ€¢ÆÆ÷uöÆVv7•÷F–Åö5÷&VVF—E÷&Vç@¢’À¢¢¢†æFöfbÂ&VVF—FVE÷&÷WFU÷G&6RÒ6VÆbå÷W'6—7E÷&VVF—E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&ö÷C×&VVF—FVE÷&ö÷Eö†æFöfbÀ¢&VçCÖÆFW7Eö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢VF—EöWfVçE÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢f–ævW'&–çG3Öf–ævW'&–çG2À¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6†÷Eö6÷VçCÖÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢¢66†Uö6ö×öæVçG2Ò6VÆbåöVF—Eö66†Uö6ö×öæVçG2€¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢'F–f7Eöf–ævW'&–çC×7G"‡&V6÷&BævWB‚&f–ævW'&–çB"’÷"""’À¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢66÷S×6VÆbåö–æFWVæFVçEöVF—Eö66†U÷66÷R€¢†æFöfbÂ'F–f7Eö–@¢’À¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢’À¢¢VF—Eö66†Uö¶W’Ò6VÆbåö66†Uö¶W•ög&öÕö6ö×öæVçG2†66†Uö6ö×öæVçG2¢—5÷&VVF—E÷7V66W76÷"ÒG'VP¢VÆ–b†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖVB"æBæ÷B66†Uö†—C ¢&—6RfÇVTW'&÷"€¢'&VVF—Eöf÷&6U÷&WV—&VC¢6öç7VÖVB†æFöfb6ææ÷BWF†÷&—¦RæWrVF—B&÷VæB ¢ ¢–b—5÷&VVF—E÷7V66W76÷# ¢2WfW'’&V6÷fW'’&WVW7Bö'F–ç2g&W6‚fVæ6VB6Æ–ÒâGW&&ÆP¢2WfVçB¶6W'F–f–6FR—"FöW2æ÷BWF†÷&—¦R6V6öæB&ö6W72Fð¢2'—72Æ—fRÆV6RæB&6RF†R&VÖ–æ–ær&ö¦V7F–öç2à¢6Æ–ÖVEö†æFöfbÒ6VÆbç7F÷&Ræ6Æ–Õ÷&VVF—E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"††æFöfe²&†æFöfeö–B%Ò’À¢6öç7VÖW%÷v÷&µö÷&FW%ö–C×7G"€¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ð¢’À¢¢VÇ6S ¢6Æ–ÖVEö†æFöfbÒ6VÆbç7F÷&Ræ6Æ–Õö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"††æFöfe²&†æFöfeö–B%Ò’À¢6öç7VÖW%÷v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢W‡V7FVE÷Fõ÷&öÆSÒ&–æFWVæFVçEöVF—B"À¢W‡V7FVE÷G—W3×6WB„TD•Eõ4õU$4Uô„äDôdeõE•U2’À¢¢†æFöfbÒ6Æ–ÖVEö†æFöf`¢–b€¢†æFöfbævWB‚'7FGW2"’ÓÒ&6öç7VÖ–ær ¢æB†æFöfbævWB‚&6Æ–Õ÷Fö¶Vâ"¢“ ¢ô5D•dUô4ôå5TÕD”ôåô4Ä”Õ2ç6WB€¢€¢¥ô5D•dUô4ôå5TÕD”ôåô4Ä”Õ2ævWB‚’À¢°¢&¶–æB#¢€¢'&VVF—Eö†æFöfb ¢–b—5÷&VVF—E÷7V66W76÷ ¢VÇ6R&†æFöfb ¢’À¢&æÖW76R#¢æÖW76RÀ¢'&ö¦V7Eö–B#¢&ö¦V7Eö–BÀ¢'&V6÷&Eö–B#¢7G"††æFöfe²&†æFöfeö–B%Ò’À¢&6öç7VÖW%÷v÷&µö÷&FW%ö–B#¢7G"€¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ð¢’À¢&6Æ–Õ÷Fö¶Vâ#¢7G"††æFöfe²&6Æ–Õ÷Fö¶Vâ%Ò’À¢ÒÀ¢¢¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂÆVFvW"“ ¢VF—E÷v÷&µö÷&FW%öÆ—fRÒöf–æE÷v÷&µö÷&FW"†‡V"Â7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’¢–b€¢æ÷B66†Uö†—@¢æBVF—E÷v÷&µö÷&FW%öÆ—fU²'7FGW2%Ð¢–â²'66÷UöÆö6¶VB"Â&f–ÆVEö7W'&VçB'Ð¢“ ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"€¢‡V"ÂVF—E÷v÷&µö÷&FW%öÆ—fU²'v÷&µö÷&FW%ö–B%ÒÂ&W†V7WF–ær ¢¢–b&WW6VEöVF—EöWfVçB—2æ÷BæöæS ¢VF—E÷&W7VÇBÒ°¢'7FGW2#¢&GWÆ–6FR"À¢&WfVçB#¢6÷’æFVW6÷’‡&WW6VEöVF—EöWfVçB’À¢&–ævW7B#¢æöæRÀ¢Ð¢VÇ6S ¢VF—E÷&W7VÇBÒ6VÆbå÷&V6÷&EöVF—B€¢æÖW76SÖæÖW76RÀ¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢'F–f7E÷&V6÷&C×&V6÷&BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢&W÷'C×&W÷'BÀ¢ÆVFvW#ÖÆVFvW"À¢VF—Eö7–6ÆUö–CÖVF—Eö7–6ÆUö–BÀ¢VF—Eö6Æ73ÔTD•Eô4Ä55ô”äDUTäDTåBÀ¢VF—Eö66†Uö¶W“ÖVF—Eö66†Uö¶W’À¢WfVçEö–CÖWfVçEö–BÀ¢†æFöfeö–C×7G"††æFöfe²&†æFöfeö–B%Ò’À¢7FvU÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢f–ævW'&–çG3Öf–ævW'&–çG2À¢'VçF–ÖU÷FW7Eö6W'F–f–6FUö–C×7G"€¢'VçF–ÖU÷FW7Eö6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢’À¢v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢fÆ–FF–öå÷&öÆSÒ&–æFWVæFVçEöVF—B"À¢ÆÆ÷u÷&V÷VãÖf÷&6UöÖ6†–æU÷&V6†V6²À¢¢–bVF—E÷&W7VÇBævWB‚'7FGW2"’ÓÒ&GWÆ–6FR# ¢27&6‚öW†6WF–öâ&V6÷fW'’×W7B&–æBF†R6W'F–f–6FRFòF†P¢2Ç&VG’Ö–ævW7FVB–Ö×WF&ÆRWfVçBÂæ÷BFòF†—2&WG'’w2g&W6€¢2&÷f—6–öæÂ–Bà¢WfVçEö–BÒ7G"†VF—E÷&W7VÇE²&WfVçB%Õ²&WfVçEö–B%Ò¢f—'7E÷VÆ—G’ÒVF—E÷&W7VÇBævWB‚&WfVçB"Â·Ò’ævWB€¢&f—'7EöG&gE÷VÆ—G’ ¢¢–b—6–ç7Fæ6R†f—'7E÷VÆ—G’ÂF–7B“ ¢‡V"ç6WFFVfVÇB‚'&ö¦V7E÷7FFR"Â·Ò•°¢&ÆFW7Eöf—'7EöG&gE÷VÆ—G’ ¢ÒÒ6÷’æFVW6÷’†f—'7E÷VÆ—G’¢VF—EöWfVçBÒVF—E÷&W7VÇBævWB‚&WfVçB"Â·Ò¢–b€¢VF—EöWfVçBævWB‚&VF—E÷&÷VæB"’ÓÒ¢æBf—'7E÷VÆ—G’ævWB‚&w&FR"’–â²$""Â$2'Ð¢“ ¢‡V%²'&ö¦V7E÷7FFR%Õ²'&öGV7F–öå÷VÆ—G•ö&Æö6²%ÒÒ°¢&7F—fR#¢G'VRÀ¢'&V6öâ#¢&f—'7EöG&gE÷VÆ—G•ö&VÆ÷u÷F‡&W6†öÆB"À¢&w&FR#¢f—'7E÷VÆ—G’ævWB‚&w&FR"’À¢&'F–f7Eö–B#¢'F–f7Eö–BÀ¢&VF—Eö7–6ÆUö–B#¢VF—Eö7–6ÆUö–BÀ¢'&WV—&VEö7F–öâ#¢'&W—%÷&ö÷Eö6W6UöæE÷&WfÆ–FFU÷&WfVçF–öåövFR"À¢&7&VFVEöB#¢WF5÷7F×‚’À¢Ð¢VF—Eö&Æö6¶VBÒVF—E÷&W7VÇBævWB‚'7FGW2"’ÓÒ&&Æö6¶VB ¢–bVF—Eö&Æö6¶VC ¢&W÷'Bç6WFFVfVÇB‚&—77VW2"ÂµÒ’æVæB€¢°¢&W'&÷%ö6öFR#¢$RÕ5•5DTÔ”2Ó"À¢'6WfW&—G’#¢&f–Â"À¢&&Æö6¶–ær#¢G'VRÀ¢&Æö6F–öâ#¢&–æFWVæFVçEöVF—BæVF—Eö7–6ÆR"À¢&Wf–FVæ6R#¢7G"†VF—E÷&W7VÇBævWB‚'&V6öâ"’÷".ZêŠê‹ÚîjÊ[{.‹ëîKˆ®™™"’À¢'&W—%ö7F–öâ#¢.Y¹î˜[›n˜xÞ[»®Kˆ®k‹Ž{¹>ièNYî[»®z¸¾iky¨NZêŠêYŽiÉþ8""À¢Ð¢¢WfVçE÷7VÖÖ'’Ò°¢&–æ—F–Åö—77VUö6÷VçB#¢ÆVâ‡&W÷'BævWB‚&—77VW2"ÂµÒ’’À¢'&W6–GVÅö—77VUö6÷VçB#¢ÆVâ‡&W÷'BævWB‚&—77VW2"ÂµÒ’’À¢&–çG&öGV6VEö—77VUö6÷VçB#¢À¢&&Æö6¶–æuö—77VUö6÷VçB#¢ÆVâ‡&W÷'BævWB‚&—77VW2"ÂµÒ’’À¢&f–æÅ÷7FGW2#¢&&Æö6¶VB"À¢Ð¢VÇ6S ¢WfVçE÷7VÖÖ'’Ò6÷’æFVW6÷’€¢VF—E÷&W7VÇBævWB‚&WfVçB"Â·Ò’ævWB‚&VF—E÷7VÖÖ'’"’÷"·Ð¢¢–bWfVçE÷7VÖÖ'“ ¢WfVçE÷7VÖÖ'’çWFFR€¢°¢&VF—E÷G&6Uö–B#¢7FvU÷G&6Uö–BÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢&'F–f7E÷&u÷6†#Sb#¢f–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢Ð¢¢&W÷'E²&VF—E÷7VÖÖ'’%ÒÒWfVçE÷7VÖÖ'¢F‡&W6†öÆG2Ò€¢&W÷'BævWB‚&–æFWVæFVçEöWf–FVæ6UöÖG&—‚"Â·Ò¢ævWB‚'VÆ—G•÷F‡&W6†öÆG2"¢¢–b—6–ç7Fæ6R‡F‡&W6†öÆG2ÂF–7B“ ¢F‡&W6†öÆG5²&&Æö6¶–æuöW'&÷%ö6÷VçB%ÒÒ–çB€¢WfVçE÷7VÖÖ'’ævWB‚&&Æö6¶–æuö—77VUö6÷VçB"Â¢¢F‡&W6†öÆG5²'&W6–GVÅö—77VUö6÷VçB%ÒÒ–çB€¢WfVçE÷7VÖÖ'’ævWB‚'&W6–GVÅö—77VUö6÷VçB"Â¢¢F‡&W6†öÆG5²&–çG&öGV6VEö—77VUö6÷VçB%ÒÒ–çB€¢WfVçE÷7VÖÖ'’ævWB‚&–çG&öGV6VEö—77VUö6÷VçB"Â¢¢–b66†VEö6W'F–f–6FR—2æöæS ¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ€¢æÖW76RÂVF—E÷v÷&µö÷&FW"Â&WVW7E÷6W76–öåö–@¢¢VF—Eö&–æF–ærÒ6VÆbåö7F—fUöWF†÷&—G•ö&–æF–ær€¢æÖW76RÂ&ö¦V7Eö–BÂVF—E÷v÷&µö÷&FW ¢¢6W'F–f–6FRÒ6VÆbå÷W'6—7EöVF—Eö6W'F–f–6FR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6æöæ–6Åö'F–f7Eö–C×&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB‚&6æöæ–6Åö§6öåö'F–f7Eö–B"’À¢66†Uö6ö×öæVçG3Ö66†Uö6ö×öæVçG2À¢&W÷'C×&W÷'BÀ¢v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢WfVçEö–CÖWfVçEö–BÀ¢†æFöfeö–C×7G"††æFöfe²&†æFöfeö–B%Ò’À¢7FvU÷G&6Uö–C×7FvU÷G&6Uö–BÀ¢&öGV7F–öå÷G&6Uö–C×7G"€¢†æFöfbævWB‚'&VVF—E÷&÷WFU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢’À¢&öGV7F–öå÷7FvU÷G&6Uö–C×7G"€¢†æFöfbævWB‚'&öGV7F–öå÷7FvU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢’À¢f–ævW'&–çG3Öf–ævW'&–çG2À¢&öGV7F–öå÷v÷&µö÷&FW%ö–C×7G"††æFöfe²'v÷&µö÷&FW%ö–B%Ò’À¢VF—Eö7F÷%ö&–æF–æuö–C×7G"€¢VF—Eö&–æF–ærævWB‚&7F÷%ö&–æF–æuö–B"’÷"" ¢’À¢VF—E÷6W76–öåöf–ævW'&–çCÖVF—Eö&–æF–ærævWB€¢'6W76–öåöf–ævW'&–çB ¢’À¢VF—EöWF†÷&—G•ö&–æF–æsÖVF—Eö&–æF–ærÀ¢&W7VÇEö÷fW'&–FSÒ&&Æö6¶VB"–bVF—Eö&Æö6¶VBVÇ6RæöæRÀ¢6W'F–f–6FUö–CÒ€¢$4U%BÒ ¢²6†#Seö'—FW2€¢€¢'&VVF—BÖWfVçB×&V6÷fW'“¢ ¢²&ö¦V7Eö–@¢²#¢ ¢²WfVçEö–@¢’æVæ6öFR‚'WFbÓ‚"¢•³£eÐ¢–b&V6÷fW&–æuöWfVçEööæÇ¢VÇ6RæöæP¢’À¢¢6W'F–f–6FRÒ6VÆbåöÆöE÷fW&–f–VEö–æFWVæFVçEöVF—Eö6W'F–f–6FR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢6W'F–f–6FUö–C×7G"†6W'F–f–6FU²&6W'F–f–6FUö–B%Ò’À¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢6æöæ–6Å÷6†#ScÖ6æöæ–6Åö†6‚À¢6÷W&6Uö'F–f7E÷&u÷6†#ScÖf–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢¢–b6W'F–f–6FRævWB‚'&W7VÇB"’ÓÒ'72"æB–çB€¢6W'F–f–6FRævWB‚&—77VUö6÷VçB"Â¢’ÓÒ ¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢&Æö6²Ò‡V"ævWB‚'&ö¦V7E÷7FFR"Â·Ò’ævWB€¢'&öGV7F–öå÷VÆ—G•ö&Æö6² ¢¢–b—6–ç7Fæ6R†&Æö6²ÂF–7B’æB&Æö6²ævWB‚&7F—fR"“ ¢Æ–æVvUö–G3¢6WE·7G%ÒÒ6WB‚¢7W'6÷%ö–BÒ7G"†'F–f7Eö–B¢f÷"ò–â&ævRƒ3"“ ¢–bæ÷B7W'6÷%ö–B÷"7W'6÷%ö–B–âÆ–æVvUö–G3 ¢'&V°¢Æ–æVvUö–G2æFB†7W'6÷%ö–B¢G'“ ¢Æ–æVvU÷&V6÷&BÂöÆ–æVvU÷F‚Ò6VÆbç7F÷&RævWEö'F–f7B€¢æÖW76RÂ&ö¦V7Eö–BÂ7W'6÷%ö–@¢¢W†6WBfÇVTW'&÷# ¢'&V°¢7W'6÷%ö–BÒ7G"€¢Æ–æVvU÷&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"¢÷"Æ–æVvU÷&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB€¢'6÷W&6U÷7F÷'–&ö&Eö'F–f7Eö–B ¢¢÷"" ¢¢–b€¢7G"†&Æö6²ævWB‚&VF—Eö7–6ÆUö–B"’÷"""¢ÓÒ7G"†VF—Eö7–6ÆUö–B¢æB7G"†&Æö6²ævWB‚&'F–f7Eö–B"’÷"""’–âÆ–æVvUö–G0¢“ ¢&Æö6²çWFFR€¢°¢&7F—fR#¢fÇ6RÀ¢&6ÆV&VEöB#¢WF5÷7F×‚’À¢&6ÆV&VEö'•öVF—Eö7–6ÆUö–B#¢VF—Eö7–6ÆUö–BÀ¢&6ÆV&VEö'•ö'F–f7Eö–B#¢'F–f7Eö–BÀ¢&6ÆV&VEö'•ö6W'F–f–6FUö–B#¢6W'F–f–6FU°¢&6W'F–f–6FUö–B ¢ÒÀ¢Ð¢¢&W—%÷6¶vRÒæöæP¢VF—E÷Fõ÷&W—%ö†æFöfbÒæöæP¢–b6W'F–f–6FRævWB‚&—77VUö6÷VçB"Â“ ¢2FWFW&Ö–æ—7F–2–G2ÇW2F†R&V6÷fW&&ÆRGvò×&V6÷&B6öÖÖ—BÖ¶P¢2WfW'’&WG'’&V6öæ6–ÆRF†RW†7B6–væVB6¶vRÂ†æFöfbæB‡V ¢2&ö¦V7F–öç2âæWfW"fÆÂ&6²FòâöÆFW"6W'F–f–6FRw2&÷WFRà¢&W—%÷6¶vRÂVF—E÷Fõ÷&W—%ö†æFöfbÒ€¢6VÆbå÷W'6—7E÷&W—%÷6¶vUöæEö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&C×7F÷'–&ö&BÀ¢6W'F–f–6FSÖ6W'F–f–6FRÀ¢&öGV7F–öåö†æFöfcÖ†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢&ö÷Eö†æFöfcÒ€¢&VVF—FVE÷&ö÷Eö†æFöf`¢÷"&W6öÇfVE÷&VVF—E÷&ö÷@¢÷"†æFöf`¢’À¢¢¢&V6÷&FVEöç2ÒF–ÖRçW&eö6÷VçFW%öç2‚¢–bVF—E÷&W7VÇE²'7FGW2%ÒÓÒ&GWÆ–6FR# ¢&–÷"ÒVF—E÷&W7VÇE²&WfVçB%Ð¢—77VUö6÷VçBÒÆVâ‡&–÷"ævWB‚&—77VW2"ÂµÒ’¢7FGW2Ò'72"–b&–÷"ævWB‚'&W7VÇB"’ÓÒ'72"VÇ6R&f–Åö7W'&VçB ¢7VÖÖ'’Òb.ZêŠê{¹>iéÎiÊ®XùŽûÙÎ™zîš)‡¶—77VUö6÷VçGÞšžûÙÎiÊ®ikZ)î‹ÚîjÊ ¢VÆ–bVF—E÷&W7VÇE²'7FGW2%ÒÓÒ&&Æö6¶VB# ¢7FGW2Ò&&Æö6¶VB ¢7VÖÖ'’Ò.ZêŠê™‹¾ZîûÙÎ[{.‹ëã>‹ÚîûÙÎ™ÈŠhKˆ®k‹Ž˜xÞ[»¢ ¢VÇ6S ¢—77VUö6÷VçBÒÆVâ‡&W÷'BævWB‚&—77VW2"ÂµÒ’¢7FGW2Ò'72"–b—77VUö6÷VçBÓÒVÇ6R&f–Åö7W'&VçB ¢–b—77VUö6÷VçBÓÒ ¢7VÖÖ'’Ò.ZêŠê˜	®‹ø~ûÙÎ™‹¾Zî™IžŠúóûÙÎXéþ™zîš)Žjè¾yY“ûÙÎKúîiKžikZ)î™zîš)ƒ ¢VÇ6S ¢VF—Eö6÷VçG2Ò&W÷'BævWB‚&VF—E÷7VÖÖ'’"’÷"·Ð¢7VÖÖ'’Ò€¢b.ZêŠêiÊ®˜	®‹ø~ûÙÎX[¶—77VUö6÷VçGÞš’ ¢b.ûÙÎ™‹¾Zç¶–çB†VF—Eö6÷VçG2ævWB‚v&Æö6¶–æuö—77VUö6÷VçBrÂ—77VUö6÷VçB’—Ò ¢b.ûÙÎjè¾yY—¶–çB†VF—Eö6÷VçG2ævWB‚w&W6–GVÅö—77VUö6÷VçBrÂ’—Ò ¢b.ûÙÎikZ)ç¶–çB†VF—Eö6÷VçG2ævWB‚v–çG&öGV6VEö—77VUö6÷VçBrÂ’—Ò ¢ ¢2F†R&öGV7F–öâv÷&¶&öö²&VÖ–ç2–Ö×WF&ÆRæBG'WF†gVÆÇ’&WF–ç2—G0¢2TäD”äuôTD•BvRâ–æFWVæFVçBVF—B&öGV6W2æWr6W'F–f–6FRÐ¢2&÷VæBv÷&¶&öö²v†÷6Rf—6–&ÆR÷fW&Æ’6'&–W2F†R6–væVB6öæ6ÇW6–öâÀ¢26÷VçG2ÂG&6RæB6÷W&6Rf–ævW'&–çBv—F†÷WB6†æv–ær6æöæ–6À¢27F÷'–&ö&B6VÖçF–72à¢6W'F–f–6FU÷7VÖÖ'’Ò6÷’æFVW6÷’†6W'F–f–6FRævWB‚&VF—E÷7VÖÖ'’"’÷"·Ò¢÷fW&Æ•÷7FGW2Ò7G"†6W'F–f–6FRævWB‚'&W7VÇB"’÷"&f–Åö7W'&VçB"’çWW"‚¢6VÖçF–5ö6öæ6ÇW6–öâÒ6W'F–f–6FRævWB‚'6VÖçF–5ö6öæ6ÇW6–öâ"¢÷fW&Æ•ö6öæ6ÇW6–öâÒ€¢7G"‡6VÖçF–5ö6öæ6ÇW6–öâævWB‚'7VÖÖ'’"’¢–b—6–ç7Fæ6R‡6VÖçF–5ö6öæ6ÇW6–öâÂF–7B¢æB6VÖçF–5ö6öæ6ÇW6–öâævWB‚'7VÖÖ'’"¢VÇ6R7VÖÖ'¢¢VF—Eö÷fW&Æ•÷–ÆöBÒ°¢'7FGW2#¢÷fW&Æ•÷7FGW2À¢&6öæ6ÇW6–öâ#¢÷fW&Æ•ö6öæ6ÇW6–öâÀ¢&–æ—F–Åö—77VUö6÷VçB#¢–çB€¢6W'F–f–6FU÷7VÖÖ'’ævWB‚&–æ—F–Åö—77VUö6÷VçB"Â6W'F–f–6FRævWB‚&—77VUö6÷VçB"Â’¢’À¢'&W6–GVÅö—77VUö6÷VçB#¢–çB€¢6W'F–f–6FU÷7VÖÖ'’ævWB‚'&W6–GVÅö—77VUö6÷VçB"Â¢’À¢&–çG&öGV6VEö—77VUö6÷VçB#¢–çB€¢6W'F–f–6FU÷7VÖÖ'’ævWB‚&–çG&öGV6VEö—77VUö6÷VçB"Â¢’À¢&&Æö6¶–æuö—77VUö6÷VçB#¢–çB€¢6W'F–f–6FU÷7VÖÖ'’ævWB€¢&&Æö6¶–æuö—77VUö6÷VçB"Â6W'F–f–6FRævWB‚&—77VUö6÷VçB"Â¢¢’À¢&VF—E÷G&6Uö–B#¢7G"†6W'F–f–6FRævWB‚&VF—E÷G&6Uö–B"’÷"7FvU÷G&6Uö–B’À¢&6W'F–f–6FUö–B#¢7G"†6W'F–f–6FU²&6W'F–f–6FUö–B%Ò’À¢&6W'F–f–6FUö–çFVw&—G•ö†Ö2#¢7G"€¢6W'F–f–6FRævWB‚&–çFVw&—G•ö†Ö2"’÷"" ¢’À¢&6æöæ–6Å÷6†#Sb#¢7G"†6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"’÷"6æöæ–6Åö†6‚’À¢'6÷W&6Uö'F–f7E÷&u÷6†#Sb#¢7G"€¢†6W'F–f–6FRævWB‚&f–ævW'&–çG2"’÷"·Ò’ævWB‚&'F–f7E÷&r"¢÷"f–ævW'&–çG5²&'F–f7E÷&r%Ð¢’À¢Ð¢VF—Eö÷fW&Æ’Ò÷6VÅ÷6W'f–6U÷fW&–f–VEöVF—Eö÷fW&Æ’€¢VF—Eö÷fW&Æ•÷–Æö@¢¢6–væVE÷&W÷'BÒ6÷’æFVW6÷’†6W'F–f–6FRævWB‚'&W÷'B"’÷"&W÷'B¢VF—FVE÷&V6÷&BÒæW‡B€¢€¢6æF–FFP¢f÷"6æF–FFR–â6VÆbç7F÷&RæÆ—7Eö'F–f7G2†æÖW76RÂ&ö¦V7Eö–B¢–b6æF–FFRævWB‚&'F–f7E÷G—R"’ÓÒ'7F÷'–&ö&EöVF—Eö÷fW&Æ’ ¢æB6æF–FFRævWB‚&ÖWFFF"Â·Ò’ævWB€¢&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B ¢¢ÓÒ6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢’À¢æöæRÀ¢¢–bVF—FVE÷&V6÷&B—2æöæS ¢VF—FVEö'—FW2ÒöW‡÷'Eö6ö×7E÷7F÷'–&ö&B€¢7F÷'–&ö&BÀ¢6–væVE÷&W÷'BÀ¢VF—Eö÷fW&Æ“ÖVF—Eö÷fW&Æ’À¢¢VF—FVE÷&V6÷&BÒ6VÆbç7F÷&Rç6fUö'F–f7Eö'—FW2€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢VF—FVEö'—FW2À¢f–ÆUöæÖSÒ€¢b'µF‚‡7G"‡&V6÷&BævWB‚vf–ÆUöæÖRr’÷"w7F÷'–&ö&Br’’ç7FV×Ò ¢b%þxºÎz¸¾ZêŠê÷¶÷fW&Æ•÷7FGW7Òç†Ç7‚ ¢’À¢'F–f7E÷G—SÒ'7F÷'–&ö&EöVF—Eö÷fW&Æ’"À¢fW'6–öã×7G"‡&V6÷&BævWB‚'fW'6–öâ"’÷"&7W'&VçB"’À¢v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢6÷W&6Uö'F–f7Eö–CÖ'F–f7Eö–BÀ¢ÖWFFF×°¢¢¦6ö×7Eö6öçG&7EöWf–FVæ6R‚’À¢&6æöæ–6Åö§6öåö'F–f7Eö–B#¢&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB€¢&6æöæ–6Åö§6öåö'F–f7Eö–B ¢’À¢&6æöæ–6Åö§6öåöf–ævW'&–çB#¢&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB€¢&6æöæ–6Åö§6öåöf–ævW'&–çB ¢’À¢&6æöæ–6Å÷6†#Sb#¢VF—Eö÷fW&Æ•÷–ÆöE²&6æöæ–6Å÷6†#Sb%ÒÀ¢'6÷W&6U÷7F÷'–&ö&Eö'F–f7Eö–B#¢'F–f7Eö–BÀ¢'6÷W&6U÷7F÷'–&ö&E÷&u÷6†#Sb#¢VF—Eö÷fW&Æ•÷–ÆöE°¢'6÷W&6Uö'F–f7E÷&u÷6†#Sb ¢ÒÀ¢&–æFWVæFVçEöVF—E÷7FGW2#¢÷fW&Æ•÷7FGW2À¢&VF—E÷7FGW2#¢÷fW&Æ•÷7FGW2À¢&—77VUö6÷VçB#¢–çB†6W'F–f–6FRævWB‚&—77VUö6÷VçB"Â’’À¢&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B#¢6W'F–f–6FU°¢&6W'F–f–6FUö–B ¢ÒÀ¢&–æFWVæFVçEöVF—E÷G&6Uö–B#¢VF—Eö÷fW&Æ•÷–ÆöE°¢&VF—E÷G&6Uö–B ¢ÒÀ¢&–æFWVæFVçEöVF—E÷7VÖÖ'’#¢6W'F–f–6FU÷7VÖÖ'’À¢'6–væVEöVF—Eö÷fW&Æ’#¢G'VRÀ¢ÒÀ¢¢FVÆVÖWG'’ÒæöæP¢–b66†Uö†—C ¢G'“ ¢FVÆVÖWG'’Ò6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–C×7FvU÷G&6Uö–BÀ¢¢W†6WBfÇVTW'&÷"2W†3 ¢–bæ÷B7G"†W†2’ç7F'G7v—F‚‚'Væ¶æ÷vâFVÆVÖWG'’&V6÷&Eö–C¢"“ ¢&—6P¢–bæ÷B€¢&V6÷fW&–æu÷&VVF—Eö6ö×ÆWF–öà¢÷"&V6÷fW&–æu÷&ö÷Eö6ö×ÆWF–öà¢“ ¢&—6RfÇVTW'&÷"€¢&66†VB–æFWVæFVçBVF—BFVÆVÖWG'’—2Ö—76–ær ¢’g&öÒW†0¢–bFVÆVÖWG'’—2æ÷BæöæRæB€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡FVÆVÖWG'’¢÷"FVÆVÖWG'’ævWB‚&÷W&F–öâ"’Ò&VF—E÷7F÷'–&ö&B ¢÷"FVÆVÖWG'’ævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"FVÆVÖWG'’ævWB‚&–çWEöf–ævW'&–çG2"’Òf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"€¢'&VVF—E÷&V6÷fW'•ö–çfÆ–C¢VF—BFVÆVÖWG'’6†ævVB–FVçF—G’ ¢¢–bFVÆVÖWG'’—2æöæS ¢FVÆVÖWG'’Ò6VÆbå÷6fU÷FVÆVÖWG'’€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢÷W&F–öãÒ&VF—E÷7F÷'–&ö&B"À¢7F'FVEöç3×7F'FVEöç2À¢7FvW5ö×3×°¢&6æöæ–6ÅöÆöB#¢†ÆöFVEöç2Ò7F'FVEöç2’òóóÀ¢&Ö6†–æU÷fÆ–FF–öâ#¢‡fÆ–FFVEöç2ÒÆöFVEöç2¢òóóÀ¢&ÆVFvW"#¢‡&V6÷&FVEöç2ÒfÆ–FFVEöç2’òóóÀ¢ÒÀ¢66†Uö†—CÖ66†Uö†—BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢FVÆVÖWG'•ö–C×7FvU÷G&6Uö–BÀ¢v–æF÷u÷&öÆSÒ&–æFWVæFVçEöVF—B"À¢F6µ÷G—SÒ&–æFWVæFVçEöVF—B"À¢–çWEöf–ævW'&–çG3Öf–ævW'&–çG2À¢6†÷Eö6÷VçCÖÆVâ‡7F÷'–&ö&BævWB‚'6†÷G2"ÂµÒ’’À¢2F†—2ÖWG&–26÷VçG2&F6†W2vVæW&FVB'’F†RcãR7FvRÂæ÷@¢2ÆVv7’&VBÖöæÇ’&÷w2&W6VçB–ââöÆB'F–f7Bà¢&F6…ö6÷VçCÓÀ¢¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂÆVFvW"“ ¢–bæ÷Bç’€¢&÷rævWB‚'FVÆVÖWG'•ö–B"’ÓÒFVÆVÖWG'’ævWB‚'FVÆVÖWG'•ö–B"¢f÷"&÷r–âÆVFvW"ç6WFFVfVÇB‚'W&f÷&Öæ6U÷G&6W2"ÂµÒ¢“ ¢ÆVFvW%²'W&f÷&Öæ6U÷G&6W2%ÒæVæB€¢6÷’æFVW6÷’‡FVÆVÖWG'’¢¢–b&VVF—FVE÷&÷WFU÷G&6R—2æ÷BæöæRæBæ÷Bç’€¢&÷rævWB‚'FVÆVÖWG'•ö–B"¢ÓÒ&VVF—FVE÷&÷WFU÷G&6RævWB‚'FVÆVÖWG'•ö–B"¢f÷"&÷r–âÆVFvW%²'W&f÷&Öæ6U÷G&6W2%Ð¢“ ¢ÆVFvW%²'W&f÷&Öæ6U÷G&6W2%ÒæVæB€¢6÷’æFVW6÷’‡&VVF—FVE÷&÷WFU÷G&6R¢¢–bæ÷Bç’€¢&÷rævWB‚&6W'F–f–6FUö–B"’ÓÒ6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢f÷"&÷r–âÆVFvW"ç6WFFVfVÇB‚&VF—Eö6W'F–f–6FW2"ÂµÒ¢“ ¢ÆVFvW%²&VF—Eö6W'F–f–6FW2%ÒæVæB†6÷’æFVW6÷’†6W'F–f–6FR’¢VF—Eö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Â7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’¢2WfW'’6W'F–f–6FRæB÷fW&Æ’—2v÷&²Ö÷&FW"FVÆ—fW&&ÆRWfVà¢2v†Vâ7V66W76÷"d”Â&Wfö¶W2âÇ&VG’×76VBVF—B÷&FW"à¢2&Vv—7G&F–öâ—2'’–Ö×WF&ÆR–B6ò&WG&–W2&RW†7BæòÖ÷2à¢–bæ÷Bç’€¢&÷rævWB‚&FVÆ—fW&&ÆUö–B"’ÓÒ6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢f÷"&÷r–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ¢“ ¢&ö¦V7EövVçBç&V6÷&EöFVÆ—fW&&ÆR€¢‡V"À¢°¢&FVÆ—fW&&ÆUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢'v÷&µö÷&FW%ö–B#¢VF—Eö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢'F–ÖW7F×#¢WF5÷7F×‚’À¢&'F–f7E÷G—R#¢&VF—E÷&W÷'B"À¢&f–ÆUöæÖR#¢b'¶6W'F–f–6FU²v6W'F–f–6FUö–Bu×Òæ§6öâ"À¢'fW'6–öâ#¢7G"‡&V6÷&BævWB‚'fW'6–öâ"’÷"&7W'&VçB"’À¢&f–ævW'&–çB#¢6W'F–f–6FU²'&W÷'E÷6†#Sb%ÒÀ¢'7FGW2#¢'fÆ–FFVB"–b7FGW2ÓÒ'72"VÇ6R&G&gB"À¢'6÷W&6U÷fW'6–öç2#¢¶'F–f7Eö–EÒÀ¢ÒÀ¢¢–bæ÷Bç’€¢&÷rævWB‚&FVÆ—fW&&ÆUö–B"’ÓÒVF—FVE÷&V6÷&E²&'F–f7Eö–B%Ð¢f÷"&÷r–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ¢“ ¢&ö¦V7EövVçBç&V6÷&EöFVÆ—fW&&ÆR€¢‡V"À¢°¢&FVÆ—fW&&ÆUö–B#¢VF—FVE÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢'v÷&µö÷&FW%ö–B#¢VF—Eö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢'F–ÖW7F×#¢WF5÷7F×‚’À¢&'F–f7E÷G—R#¢'7F÷'–&ö&EöVF—Eö÷fW&Æ’"À¢&f–ÆUöæÖR#¢VF—FVE÷&V6÷&E²&f–ÆUöæÖR%ÒÀ¢'fW'6–öâ#¢7G"†VF—FVE÷&V6÷&BævWB‚'fW'6–öâ"’÷"&7W'&VçB"’À¢&f–ævW'&–çB#¢VF—FVE÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢'7FGW2#¢'fÆ–FFVB"–b7FGW2ÓÒ'72"VÇ6R&G&gB"À¢'6÷W&6U÷fW'6–öç2#¢¶'F–f7Eö–EÒÀ¢ÒÀ¢¢–bVF—Eö÷&FW%²'7FGW2%ÒÓÒ&W†V7WF–ær# ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"ÂVF—Eö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÂ'fÆ–FF–ær"¢6ÆVâÒ7FGW2ÓÒ'72 ¢–bVF—Eö÷&FW%²'7FGW2%ÒÓÒ'fÆ–FF–ær# ¢VF—Eö÷&FW%²'fÆ–FF–öâ%ÒÒ°¢'7FGW2#¢€¢'72"–b6ÆVâVÇ6R&&Æö6¶VB"–b7FGW2ÓÒ&&Æö6¶VB"VÇ6R&f–Â ¢’À¢'&V6÷&G2#¢·²&6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%×ÕÒÀ¢Ð¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"€¢‡V"À¢VF—Eö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢€¢'76VB ¢–b6ÆVà¢VÇ6R&&Æö6¶VB"–b7FGW2ÓÒ&&Æö6¶VB"VÇ6R&f–ÆVEö7W'&VçB ¢’À¢¢VÆ–bæ÷B6ÆVâæBVF—Eö÷&FW"ævWB‚'7FGW2"’–â°¢'76VB"À¢&v—F–æu÷6–væöfb"À¢Ó ¢2f–ævW'&–çBÖ–çfÆ–FF–ær&RÖVF—B—2æWrWF†÷&—G’â—G0¢2d”Â×W7B&Wfö¶RF†RVF—Bv÷&²÷&FW"w2V&Æ–W"522vVÆÀ¢22F†R&öGV6W"öÆFW7B'F–f7B7FFR&VÆ÷rà¢VF—Eö÷&FW%²'7FGW2%ÒÒ&f–ÆVEö7W'&VçB ¢VF—Eö÷&FW%²'&V÷VæVEö'•ö–æFWVæFVçEöVF—B%ÒÒ6W'F–f–6FU°¢&6W'F–f–6FUö–B ¢Ð¢VF—Eö÷&FW%²'fÆ–FF–öâ%ÒÒ°¢'7FGW2#¢&&Æö6¶VB"–b7FGW2ÓÒ&&Æö6¶VB"VÇ6R&f–Â"À¢'&V6÷&G2#¢°¢²&6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%×Ð¢ÒÀ¢Ð¢&öGV6W%ö÷&FW"Ò€¢æöæP¢–b†æFöfbævWB‚&†æFöfe÷G—R"’ÓÒ&VF—Eö–çF¶R ¢VÇ6Röf–æE÷v÷&µö÷&FW"†‡V"Â7G"††æFöfe²'v÷&µö÷&FW%ö–B%Ò’¢¢–b€¢6ÆVà¢æB—6–ç7Fæ6R‡&öGV6W%ö÷&FW"ÂF–7B¢æB&öGV6W%ö÷&FW%²'7FGW2%ÒÓÒ'fÆ–FF–ær ¢“ ¢&öGV6W%ö÷&FW%²'fW&–f–VEö–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B%ÒÒ€¢6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢¢f–æÅ÷fÆ–FF–öâÒ&ö¦V7EövVçBç&V6÷&E÷fÆ–FF–öâ€¢‡V"À¢°¢'v÷&µö÷&FW%ö–B#¢&öGV6W%ö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢'F–ÖW7F×#¢WF5÷7F×‚’À¢'&W7VÇB#¢'72"À¢&vFU÷&W7VÇG2#¢°¢vFS¢'72"f÷"vFR–â&öGV6W%ö÷&FW%²'&WV—&VE÷fÆ–FF–öåövFW2%Ð¢ÒÀ¢'&W6–GVÅö—77VUö6÷VçB#¢À¢&–çG&öGV6VEö—77VUö6÷VçB#¢À¢&&Æö6¶W'2#¢µÒÀ¢ÒÀ¢¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"Â&öGV6W%ö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÂ'76VB"¢&öGV6W%ö÷&FW%²&v—F–æuö–æFWVæFVçEöVF—B%ÒÒfÇ6P¢&öGV6W%ö÷&FW%²&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B%ÒÒ6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢&öGV6W%ö÷&FW%²&–æFWVæFVçEöVF—E÷fÆ–FF–öâ%ÒÒf–æÅ÷fÆ–FF–öà¢–b6ÆVã ¢&öGV6W%öFVÆ—fW&&ÆRÒæW‡B€¢€¢&÷p¢f÷"&÷r–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ¢–b&÷rævWB‚&FVÆ—fW&&ÆUö–B"’ÓÒ'F–f7Eö–@¢’À¢æöæRÀ¢¢–b&öGV6W%öFVÆ—fW&&ÆR—2æ÷BæöæS ¢&öGV6W%öFVÆ—fW&&ÆU²'7FGW2%ÒÒ'fÆ–FFVB ¢‡V%²'&ö¦V7E÷7FFR%Õ²&ÆFW7Eö'F–f7G2%Õ²'7F÷'–&ö&B%ÒÒ°¢&FVÆ—fW&&ÆUö–B#¢VF—FVE÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&f–ÆUöæÖR#¢VF—FVE÷&V6÷&E²&f–ÆUöæÖR%ÒÀ¢'fW'6–öâ#¢7G"†VF—FVE÷&V6÷&BævWB‚'fW'6–öâ"’÷"&7W'&VçB"’À¢&f–ævW'&–çB#¢VF—FVE÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢Ð¢VÇ6S ¢2æWvW"–æFWVæFVçBd”Â&Wfö¶W2ç’V&Æ–W"WF†÷&—G’f÷ ¢2F†—2W†7B&öGV6W"'F–f7BâFòæ÷BÆVfR7FÆR50¢2÷fW&Æ’2F†R&ö¦V7Bw2ÆFW7B7F÷'–&ö&Bà¢–çfÆ–FFVEöFVÆ—fW&&ÆUö–G2Ò¶'F–f7Eö–GÐ¢f÷"FVÆ—fW&&ÆR–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ“ ¢6÷W&6U÷fW'6–öç2Ò°¢7G"‡fÇVR’f÷"fÇVR–âFVÆ—fW&&ÆRævWB‚'6÷W&6U÷fW'6–öç2"ÂµÒ¢Ð¢–b€¢7G"†FVÆ—fW&&ÆRævWB‚&FVÆ—fW&&ÆUö–B"’÷"""’ÓÒ'F–f7Eö–@¢÷"'F–f7Eö–B–â6÷W&6U÷fW'6–öç0¢“ ¢FVÆ—fW&&ÆU²'7FGW2%ÒÒ&G&gB ¢–çfÆ–FFVEöFVÆ—fW&&ÆUö–G2æFB€¢7G"†FVÆ—fW&&ÆRævWB‚&FVÆ—fW&&ÆUö–B"’÷"""¢¢ÆFW7BÒ‡V%²'&ö¦V7E÷7FFR%Õ²&ÆFW7Eö'F–f7G2%ÒævWB€¢'7F÷'–&ö&B ¢¢–b—6–ç7Fæ6R†ÆFW7BÂF–7B’æB7G"€¢ÆFW7BævWB‚&FVÆ—fW&&ÆUö–B"’÷"" ¢’–â–çfÆ–FFVEöFVÆ—fW&&ÆUö–G3 ¢‡V%²'&ö¦V7E÷7FFR%Õ²&ÆFW7Eö'F–f7G2%Òç÷€¢'7F÷'–&ö&B"ÂæöæP¢¢–b—6–ç7Fæ6R‡&öGV6W%ö÷&FW"ÂF–7B“ ¢&öGV6W%ö÷&FW%²&v—F–æuö–æFWVæFVçEöVF—B%ÒÒG'VP¢&öGV6W%ö÷&FW"ç÷‚&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B"ÂæöæR¢&öGV6W%ö÷&FW"ç÷€¢'fW&–f–VEö–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B"ÂæöæP¢¢&öGV6W%ö÷&FW"ç÷‚&–æFWVæFVçEöVF—E÷fÆ–FF–öâ"ÂæöæR¢–b&öGV6W%ö÷&FW"ævWB‚'7FGW2"’ÓÒ'fÆ–FF–ær# ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"€¢‡V"Â&öGV6W%ö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÂ&f–ÆVEö7W'&VçB ¢¢VÆ–b&öGV6W%ö÷&FW"ævWB‚'7FGW2"’–â°¢'76VB"À¢&v—F–æu÷6–væöfb"À¢Ó ¢2F†R7FFRÖ6†–æR†2æò÷&F–æ'’52Óäd”ÂVFvR&V6W6P¢2F†—2—2WF†÷&—G’&Wfö6F–öâÂæ÷B&öGV7F–öâ7F–öâà¢&öGV6W%ö÷&FW%²'7FGW2%ÒÒ&f–ÆVEö7W'&VçB ¢&öGV6W%ö÷&FW%²'&V÷VæVEö'•ö–æFWVæFVçEöVF—B%ÒÒ€¢6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ&f–ÆVEö7W'&VçB ¢ÖF6†–æuö†æFöfbÒæW‡B€¢€¢&÷p¢f÷"&÷r–â‡V"ævWB‚&†æFöfg2"ÂµÒ¢–b&÷rævWB‚&†æFöfeö–B"’ÓÒ†æFöfbævWB‚&†æFöfeö–B"¢’À¢æöæRÀ¢¢–bÖF6†–æuö†æFöfb—2æ÷BæöæRæBæ÷B—5÷&VVF—E÷7V66W76÷# ¢ÖF6†–æuö†æFöfe²'7FGW2%ÒÒ&6öç7VÖVB ¢ÖF6†–æuö†æFöfe²&VF—Eö6W'F–f–6FUö–B%ÒÒ6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢6W'F–f–VE÷7VÖÖ'’Ò6÷’æFVW6÷’†6W'F–f–6FRævWB‚&VF—E÷7VÖÖ'’"’÷"·Ò¢–æFWVæFVçE÷7FGW2Ò7G"†6W'F–f–6FRævWB‚'&W7VÇB"’÷"&f–Åö7W'&VçB"’çWW"‚¢ÖWFFF÷WFFRÒ°¢2W‡FW&æÂVF—B–çF¶R†2æò&öGV7F–öâ6öÖÖ—BFò6VVBF†—240¢26ö÷&F–æFRâW'6—7BF†R6æöæ–6ÂF†BF†—2W†7B6–væVBVF—@¢2§W7BfW&–f–VB6òÆFW"6¶vRÖ&÷VæB&W—"6âFöÖ–6ÆÇ¢26ö×&R&÷F‚6æöæ–6Â6VÖçF–72æBF†RVæ6†ævVB&r'—FW2à¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢&–æFWVæFVçEöVF—E÷7FGW2#¢–æFWVæFVçE÷7FGW2À¢&VF—E÷7FGW2#¢–æFWVæFVçE÷7FGW2À¢&—77VUö6÷VçB#¢–çB†6W'F–f–6FRævWB‚&—77VUö6÷VçB"Â’’À¢&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B#¢6W'F–f–6FU²&6W'F–f–6FUö–B%ÒÀ¢266†R†—B7&VFW2æWr&WVW7BG&6R'WB&WW6W2F†R÷&–v–æÀ¢26–væVBVF—BG&6RâÖWFFF×W7B&VÖ–â6W'F–f–6FRÖ&÷VæBà¢&–æFWVæFVçEöVF—E÷G&6Uö–B#¢7G"€¢6W'F–f–6FRævWB‚&VF—E÷G&6Uö–B"’÷"7FvU÷G&6Uö–@¢’À¢&–æFWVæFVçEöVF—E÷7VÖÖ'’#¢6W'F–f–VE÷7VÖÖ'’À¢&–æFWVæFVçEöVF—Eö÷fW&Æ•ö'F–f7Eö–B#¢VF—FVE÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&–æFWVæFVçEöVF—Eö÷fW&Æ•öf–ævW'&–çB#¢VF—FVE÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢Ð¢&V6÷&BÒ6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢'F–f7Eö–BÀ¢ÖWFFF÷WFFRÀ¢¢6æöæ–6Åö'F–f7Eö–BÒ&V6÷&BævWB‚&ÖWFFF"Â·Ò’ævWB€¢&6æöæ–6Åö§6öåö'F–f7Eö–B ¢¢–b6æöæ–6Åö'F–f7Eö–C ¢6VÆbç7F÷&RçWFFUö'F–f7EöÖWFFF€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"†6æöæ–6Åö'F–f7Eö–B’À¢ÖWFFF÷WFFRÀ¢¢276VÖ&ÆRF†RöæÇ’&W7öç6R6ö×öæVçBF†B6â7F–ÆÂ&—6R&Vf÷&P¢26öç7VÖ–æröæR×F–ÖR7V66W76÷"âöæ6Rf–æ—6‚7V66VVG2Â6öç7G'V7F–æp¢2F†R–âÖÖVÖ÷'’&W7öç6R&VÆ÷rW&f÷&×2æò7F÷&vR÷"6–væ–ærv÷&²à¢VF—FVEöF÷væÆöE÷W&ÂÒ6VÆbç7F÷&Rç6–væVEöF÷væÆöE÷W&Â€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢VF—FVE÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢VF—FVE÷&V6÷&E²&f–ÆUöæÖR%ÒÀ¢¢–b—5÷&VVF—E÷7V66W76÷# ¢†æFöfbÒ6VÆbç7F÷&Ræf–æ—6…÷&VVF—E÷7V66W76÷"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"††æFöfe²&†æFöfeö–B%Ò’À¢6öç7VÖW%÷v÷&µö÷&FW%ö–C×7G"€¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ð¢’À¢VF—Eö6W'F–f–6FUö–C×7G"†6W'F–f–6FU²&6W'F–f–6FUö–B%Ò’À¢6Æ–Õ÷Fö¶Vã×7G"†6Æ–ÖVEö†æFöfbævWB‚&6Æ–Õ÷Fö¶Vâ"’÷"""’À¢¢VÇ6S ¢†æFöfbÒ6VÆbç7F÷&Ræf–æ—6…ö†æFöfb€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7G"††æFöfe²&†æFöfeö–B%Ò’À¢6öç7VÖW%÷v÷&µö÷&FW%ö–C×7G"†VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%Ò’À¢VF—Eö6W'F–f–6FUö–C×7G"†6W'F–f–6FU²&6W'F–f–6FUö–B%Ò’À¢6Æ–Õ÷Fö¶VãÒ€¢7G"†6Æ–ÖVEö†æFöfbævWB‚&6Æ–Õ÷Fö¶Vâ"’¢–b6Æ–ÖVEö†æFöfbævWB‚&6Æ–Õ÷Fö¶Vâ"¢VÇ6RæöæP¢’À¢¢&öGV7F–öå÷G&6Uö–BÒ7G"††æFöfbævWB‚'7FvU÷G&6Uö–B"’÷"""¢÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–BÒ7G"€¢†æFöfbævWB‚'&öGV7F–öå÷7FvU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢¢VF—E÷G&6Uö–BÒ7G"‡FVÆVÖWG'•²'FVÆVÖWG'•ö–B%Ò¢f7E÷F…öWf–FVæ6RÒ°¢&66†Uö†—B#¢66†Uö†—BÀ¢&66†U÷&V6öâ#¢€¢&W†7B6–væVB6VÖçF–2ÖVF—B6W'F–f–6FR&WW6VB ¢–b66†Uö†—@¢VÇ6R&æòW†7B6–væVB6VÖçF–2ÖVF—B6W'F–f–6FR ¢’À¢'6VÖçF–5÷7V&Ö—76–öå÷&WV—&VB#¢æ÷B66†Uö†—BÀ¢'6VÖçF–5÷7V&Ö—76–öå÷W6VB#¢&ööÂ‡6VÖçF–5öVF—B—2æ÷BæöæRæBæ÷B66†Uö†—B’À¢&Ö6†–æU÷fÆ–FF÷%öW†V7WFVB#¢æ÷B66†Uö†—BÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'&Wf–WvVE÷6†÷Eö6÷VçB#¢6W'F–f–6FRævWB‚'&Wf–WvVE÷6†÷Eö6÷VçB"’À¢'&Wf–WvVEö&F6…ö6÷VçB#¢6W'F–f–6FRævWB‚'&Wf–WvVEö&F6…ö6÷VçB"’À¢'&Wf–WvVEöF¦6VçE÷—%ö6÷VçB#¢6W'F–f–6FRævWB€¢'&Wf–WvVEöF¦6VçE÷—%ö6÷VçB ¢’À¢&F¦6VçE÷—%ö÷&FW%÷6†#Sb#¢6W'F–f–6FRævWB€¢&F¦6VçE÷—%ö÷&FW%÷6†#Sb ¢’À¢'6VÖçF–5öVF—E÷6†#Sb#¢6W'F–f–6FRævWB‚'6VÖçF–5öVF—E÷6†#Sb"’À¢'&öGV7F–öå÷G&6Uö–B#¢&öGV7F–öå÷G&6Uö–BÀ¢&÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–B#¢÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–BÀ¢'&VVF—E÷&÷WFU÷G&6Uö–B#¢€¢†æFöfbævWB‚'&VVF—E÷&÷WFU÷G&6Uö–B"¢–b—5÷&VVF—E÷7V66W76÷ ¢VÇ6RæöæP¢’À¢&VF—E÷G&6Uö–B#¢VF—E÷G&6Uö–BÀ¢&6W'F–f–6FUöVF—E÷G&6Uö–B#¢6W'F–f–6FRævWB‚&VF—E÷G&6Uö–B"’À¢&ÖV7W&VÖVçE÷66÷R#¢'6W'fW%ööæÇ’"À¢Ð¢&WGW&âFööÅ&W7öç6R€¢7FGW3×7FGW2À¢7VÖÖ'“×7VÖÖ'’À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢'F–f7Eö–CÖ'F–f7Eö–BÀ¢G&6Uö–C×FVÆVÖWG'•²'FVÆVÖWG'•ö–B%ÒÀ¢FF×°¢'&W÷'B#¢&W÷'BÀ¢&VF—B#¢VF—E÷&W7VÇBÀ¢&VF—Eö6Æ72#¢TD•Eô4Ä55ô”äDUTäDTåBÀ¢&VF—E÷v÷&µö÷&FW%ö–B#¢VF—E÷v÷&µö÷&FW%²'v÷&µö÷&FW%ö–B%ÒÀ¢&66†Uö†—B#¢66†Uö†—BÀ¢&6æöæ–6Å÷6†#Sb#¢6æöæ–6Åö†6‚À¢'&öGV7F–öå÷G&6Uö–B#¢&öGV7F–öå÷G&6Uö–BÀ¢&÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–B#¢÷&–v–æÅ÷&öGV7F–öå÷G&6Uö–BÀ¢'&VVF—E÷&÷WFU÷G&6R#¢&VVF—FVE÷&÷WFU÷G&6RÀ¢&VF—E÷G&6Uö–B#¢VF—E÷G&6Uö–BÀ¢&f7E÷F…öWf–FVæ6R#¢f7E÷F…öWf–FVæ6RÀ¢&†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’††æFöfb’À¢&6W'F–f–6FR#¢6VÆbåö6W'F–f–6FU÷7VÖÖ'’†6W'F–f–6FR’À¢'&W—%÷6¶vR#¢&W—%÷6¶vRÀ¢&VF—E÷Fõ÷&W—%ö†æFöfb#¢6VÆbåö†æFöfe÷7VÖÖ'’†VF—E÷Fõ÷&W—%ö†æFöfb’À¢&VF—FVE÷v÷&¶&öö²#¢°¢&'F–f7Eö–B#¢VF—FVE÷&V6÷&E²&'F–f7Eö–B%ÒÀ¢&f–ÆUöæÖR#¢VF—FVE÷&V6÷&E²&f–ÆUöæÖR%ÒÀ¢&f–ævW'&–çB#¢VF—FVE÷&V6÷&E²&f–ævW'&–çB%ÒÀ¢'7FGW2#¢÷fW&Æ•÷7FGW2À¢'6÷W&6Uö'F–f7Eö–B#¢'F–f7Eö–BÀ¢ÒÀ¢&VF—FVE÷v÷&¶&ööµöF÷væÆöE÷W&Â#¢VF—FVEöF÷væÆöE÷W&ÂÀ¢'FVÆVÖWG'•ö–B#¢FVÆVÖWG'•²'FVÆVÖWG'•ö–B%ÒÀ¢'FVÆVÖWG'’#¢FVÆVÖWG'’À¢ÒÀ¢ ¢öfVæ6U÷&öÆUö÷W&F–öà¢FVb6ö×ÆWFU÷fÆ–FF–öâ€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢vFU÷&W7VÇG3¢F–7E·7G"Â7G%ÒÀ¢&W6–GVÅö—77VUö6÷VçC¢–çBÀ¢–çG&öGV6VEö—77VUö6÷VçC¢–çBÀ¢&Æö6¶W'3¢Æ—7E·7G%ÒÀ¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢6VÆbå÷&WV—&U÷'VçF–ÖR‚¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ€¢æÖW76RÂv÷&µö÷&FW"Â&WVW7E÷6W76–öåö–@¢¢–bv÷&µö÷&FW"ævWB‚'6W'fW%ö7F÷%÷&öÆR"’Ò'&öGV7F–öâ# ¢&—6RfÇVTW'&÷"€¢'v–æF÷u÷&öÆUöÖG&—…öÖ—6ÖF6ƒ¢æöâ×7F÷'–&ö&BfÆ–FF–öâ ¢'&WV—&W2F†Rv÷&²÷&FW"w26W'fW"Ö&÷VæB&öGV7F–öâWF†÷&—G’ ¢¢–bv÷&µö÷&FW"ævWB‚&÷W&F–öâ"’–â5Dõ%”$ô$EôõU$D”ôå2÷"7G"€¢v÷&µö÷&FW"ævWB‚&÷W&F–öâ"’÷"" ¢’ç7F'G7v—F‚‚'7F÷'–&ö&Eò"“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢7F÷'–&ö&BfÆ–FF–öâ6âöæÇ’&Rf–æÆ—¦VB'’VF—E÷7F÷'–&ö&B ¢¢–bv÷&µö÷&FW%²'7FGW2%ÒÓÒ&W†V7WF–ær# ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–BÂ'fÆ–FF–ær"¢–bv÷&µö÷&FW%²'7FGW2%ÒÒ'fÆ–FF–ær# ¢&—6RfÇVTW'&÷"†b'v÷&²÷&FW"—2æ÷BfÆ–FF–æs¢·v÷&µö÷&FW%²w7FGW2u×Ò"¢6ÆVå÷&WVW7FVBÒ€¢ÆÂ‡fÇVRÓÒ'72"f÷"fÇVR–âvFU÷&W7VÇG2çfÇVW2‚’¢æB&W6–GVÅö—77VUö6÷VçBÓÒ ¢æB–çG&öGV6VEö—77VUö6÷VçBÓÒ ¢æBæ÷B&Æö6¶W'0¢¢&W7VÇBÒ&ö¦V7EövVçBç&V6÷&E÷fÆ–FF–öâ€¢‡V"À¢°¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢'F–ÖW7F×#¢WF5÷7F×‚’À¢'&W7VÇB#¢'72"–b6ÆVå÷&WVW7FVBVÇ6R&f–Â"À¢&vFU÷&W7VÇG2#¢vFU÷&W7VÇG2À¢'&W6–GVÅö—77VUö6÷VçB#¢&W6–GVÅö—77VUö6÷VçBÀ¢&–çG&öGV6VEö—77VUö6÷VçB#¢–çG&öGV6VEö—77VUö6÷VçBÀ¢&&Æö6¶W'2#¢&Æö6¶W'2À¢ÒÀ¢¢6ÆVâÒ&W7VÇE²'7FGW2%ÒÓÒ'72 ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–BÂ'76VB"–b6ÆVâVÇ6R&f–ÆVEö7W'&VçB"¢–b6ÆVã ¢f÷"FVÆ—fW&&ÆR–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ“ ¢–bFVÆ—fW&&ÆRævWB‚'v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–C ¢FVÆ—fW&&ÆU²'7FGW2%ÒÒ'fÆ–FFVB ¢‡V%²'&ö¦V7E÷7FFR%Õ²&ÆFW7Eö'F–f7G2%Õ¶FVÆ—fW&&ÆU²&'F–f7E÷G—R%ÕÒÒ°¢&FVÆ—fW&&ÆUö–B#¢FVÆ—fW&&ÆU²&FVÆ—fW&&ÆUö–B%ÒÀ¢&f–ÆUöæÖR#¢FVÆ—fW&&ÆU²&f–ÆUöæÖR%ÒÀ¢'fW'6–öâ#¢FVÆ—fW&&ÆU²'fW'6–öâ%ÒÀ¢&f–ævW'&–çB#¢FVÆ—fW&&ÆU²&f–ævW'&–çB%ÒÀ¢Ð¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ'76VB"–b6ÆVâVÇ6R&f–ÆVEö7W'&VçB ¢&WGW&âFööÅ&W7öç6R€¢7FGW3Ò'72"–b6ÆVâVÇ6R&f–Åö7W'&VçB"À¢7VÖÖ'“Ò.š¨ÎŠø˜	®‹ø~ûÙÎjè¾yY“ûÙÎikZ)ã"–b6ÆVâVÇ6R.š¨ÎŠøiÊ®˜	®‹ø~ûÙÎzhjÚ.‹é>X{¥52"À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢FF×&W7VÇBÀ¢ ¢FVb&V6÷&Eö6&–Æ—G•öv€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢&WVW7C¢7G"À¢Ö—76–æuöFWVæFVæ7“¢7G"À¢&—6³¢7G"À¢6fUöfÆÆ&6³¢7G"À¢&÷÷6VEö–×ÆVÖVçFF–öã¢7G"À¢&WV—&VE÷FW7G3¢Æ—7E·7G%ÒÀ¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢6VÆbå÷&WV—&U÷'VçF–ÖR‚¢&V6÷&BÒ°¢&vö–B#¢öæWuö–B‚$t"’À¢'v÷&µö÷&FW%ö–B#¢v÷&µö÷&FW%ö–BÀ¢'F–ÖW7F×#¢WF5÷7F×‚’À¢'&WVW7B#¢&WVW7BÀ¢&Ö—76–æuöFWVæFVæ7’#¢Ö—76–æuöFWVæFVæ7’À¢'&—6²#¢&—6²À¢'6fUöfÆÆ&6²#¢6fUöfÆÆ&6²À¢'&÷÷6VEö–×ÆVÖVçFF–öâ#¢&÷÷6VEö–×ÆVÖVçFF–öâÀ¢'&WV—&VE÷FW7G2#¢&WV—&VE÷FW7G2À¢Ð¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂöÆVFvW"“ ¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢2¶VWF†RWF†÷&—G’6†V6²–ç6–FRF†R&ö¦V7BÆö6²æB–ÖÖVF–FVÇ¢2&Vf÷&RF†Rf—'7Bw&—FR6ò7FÆRö7&÷72×v–æF÷rv÷&²÷&FW"6ææ÷@¢2&RW6VBFòVæBâ÷F†W'v—6RÆW6–&ÆR&÷÷6Â&V6÷&Bà¢6VÆbåö76W'Eö6&–Æ—G•övöWF†÷&—G’€¢æÖW76RÀ¢v÷&µö÷&FW"À¢&WVW7E÷6W76–öåö–BÀ¢¢&W7VÇBÒ&ö¦V7EövVçBç&V6÷&Eö6&–Æ—G•öv†‡V"Â&V6÷&B¢&WGW&âFööÅ&W7öç6R€¢7FGW3Ò'&V6÷&FVB"À¢7VÖÖ'“Ò.ˆ;ÞX©¾{Ë®Xú>[{.Šë[Ù^ûÙÎiÊ®i8^ˆz®jŠh¹þhš~ŠÂ"À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢FF×²¢§&W7VÇBÂ&vö–B#¢&V6÷&E²&vö–B%×ÒÀ¢ ¢FVbö6ÆVåö–æFWVæFVçEö6W'F–f–6FUöf÷%÷v÷&µö÷&FW"€¢6VÆbÀ¢æÖW76S¢7G"À¢&ö¦V7Eö–C¢7G"À¢‡V#¢F–7E·7G"Âç•ÒÀ¢v÷&µö÷&FW%ö–C¢7G"À¢’ÓâF–7E·7G"Âç•Ó ¢FVÆ—fW&&ÆRÒæW‡B€¢€¢—FVÐ¢f÷"—FVÒ–â&WfW'6VB†‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ’¢–b—FVÒævWB‚'v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–@¢æB—FVÒævWB‚&'F–f7E÷G—R"’ÓÒ'7F÷'–&ö&B ¢’À¢æöæRÀ¢¢–bFVÆ—fW&&ÆR—2æöæS ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢7F÷'–&ö&BFVÆ—fW&&ÆR—2Ö—76–ær"¢'F–f7Eö–BÒ7G"†FVÆ—fW&&ÆRævWB‚&FVÆ—fW&&ÆUö–B"’÷"""¢7F÷'–&ö&BÂ&V6÷&BÒ6VÆbå÷7F÷'–&ö&Eög&öÕö'F–f7B†æÖW76RÂ&ö¦V7Eö–BÂ'F–f7Eö–B¢6æöæ–6Åö†6‚Ò6æöæ–6Å÷7F÷'–&ö&E÷6†#Sb‡7F÷'–&ö&B¢6÷W&6Uöf–ævW'&–çBÒ6VÆbå÷6÷W&6Uöf–ævW'&–çB€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢7F÷'–&ö&BÀ¢&V6÷&BævWB‚'6÷W&6Uö'F–f7Eö–B"’À¢&V6÷&BÀ¢¢&÷VæF'•ö†6‚Ò6VÆbåö&÷VæF'•ö†6‚‡7F÷'–&ö&B¢7W'&VçEöf–ævW'&–çG2Ò6VÆbåöf–ævW'&–çEö'VæFÆR€¢7F÷'–&ö&BÀ¢&V6÷&BÀ¢6æöæ–6Åö†6ƒÖ6æöæ–6Åö†6‚À¢6÷W&6Uöf–ævW'&–çC×6÷W&6Uöf–ævW'&–çBÀ¢&÷VæF'•ö†6ƒÖ&÷VæF'•ö†6‚À¢¢&ö÷Eö6æF–FFW2Ò°¢&÷p¢f÷"&÷r–â6VÆbç7F÷&RæÆ—7E÷&ö¦V7E÷&V6÷&G5÷7G&–7B€¢æÖW76RÂ&ö¦V7Eö–BÂ¶–æCÒ&†æFöfg2 ¢¢–bæ÷B&÷rævWB‚'&VVF—Eö¶–æB"¢æB&÷rævWB‚'v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–@¢æB&÷rævWB‚&Æöv–6Åö'F–f7Eö–B"’ÓÒ'F–f7Eö–@¢æB&÷rævWB‚&f–ævW'&–çG2"’ÓÒ7W'&VçEöf–ævW'&–çG0¢æB&÷rævWB‚'7FGW2"’ÓÒ&6öç7VÖVB ¢æB&÷rævWB‚&VF—Eö6W'F–f–6FUö–B"¢Ð¢–bÆVâ‡&ö÷Eö6æF–FFW2’Ò ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢W†7B7F÷'–&ö&B†2æòVæ—VR6ö×ÆWFVB&öGV7F–öâ†æFöfb ¢¢&ö÷Eö†æFöfbÒ&ö÷Eö6æF–FFW5³Ð¢VF—E÷v÷&µö÷&FW%ö–BÒ7G"€¢&ö÷Eö†æFöfbævWB‚&6öç7VÖVEö'•÷v÷&µö÷&FW%ö–B"’÷"" ¢¢VF—E÷v÷&µö÷&FW"ÒæW‡B€¢€¢&÷p¢f÷"&÷r–â‡V"ævWB‚'v÷&µö÷&FW'2"ÂµÒ¢–b&÷rævWB‚'v÷&µö÷&FW%ö–B"’ÓÒVF—E÷v÷&µö÷&FW%ö–@¢’À¢æöæRÀ¢¢–bæ÷B—6–ç7Fæ6R†VF—E÷v÷&µö÷&FW"ÂF–7B“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢&÷VæBVF—Bv÷&²÷&FW"—2Ö—76–ær ¢¢G'“ ¢÷fW&–f–VE÷&ö÷BÂ†æFöfbÒ6VÆbå÷&W6öÇfU÷&VVF—EöÆ–æVvR€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢&WVW7FVEö†æFöfc×&ö÷Eö†æFöfbÀ¢VF—E÷v÷&µö÷&FW#ÖVF—E÷v÷&µö÷&FW"À¢f–ævW'&–çG3Ö7W'&VçEöf–ævW'&–çG2À¢ÆÆ÷u÷VæF–æsÔfÇ6RÀ¢&ö¦V7EöÆö6µö†VÆCÕG'VRÀ¢¢6W'F–f–6FRÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ&6W'F–f–6FW2"À¢&V6÷&Eö–C×7G"††æFöfbævWB‚&VF—Eö6W'F–f–6FUö–B"’÷"""’À¢¢W†6WBfÇVTW'&÷"2W†3 ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢6–væVBVF—BÆ–æVvR—2Ö—76–ærÂ7FÆRÂ÷"F×W&VB ¢’g&öÒW†0¢–b€¢6W'F–f–6FRævWB‚'&W7VÇB"’Ò'72 ¢÷"–çB†6W'F–f–6FRævWB‚&—77VUö6÷VçB"ÂÓ’’Ò ¢÷"æ÷B6VÆbå÷fÆ–Eö66†Uö–FVçF—G’†6W'F–f–6FRÂTD•Eô4Ä55ô”äDUTäDTåB¢÷"6W'F–f–6FRævWB‚'&öGV7F–öå÷v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"'F–f7Eö–@¢æ÷B–â°¢6W'F–f–6FRævWB‚&'F–f7Eö–B"’À¢6W'F–f–6FRævWB‚&6æöæ–6Åö'F–f7Eö–B"’À¢Ð¢÷"6W'F–f–6FRævWB‚&6æöæ–6Å÷6†#Sb"’Ò6æöæ–6Åö†6€¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢W†7B7F÷'–&ö&BfW'6–öâæVVG26ÆVâ–æFWVæFVçBVF—B&Vf÷&R6–vâÖöfb ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†6W'F–f–6FR“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢6W'F–f–6FR—2Vç6–væVB÷"F×W&VB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbåöÆVFvW%÷66†VÖÂ&–æFWVæFVçEöVF—Eö6W'F–f–6FR"Â6W'F–f–6FP¢¢&W÷'BÒ6W'F–f–6FRævWB‚'&W÷'B"¢–bæ÷B—6–ç7Fæ6R‡&W÷'BÂF–7B’÷"6W'F–f–6FRævWB‚'&W÷'E÷6†#Sb"’Ò6†#Seö'—FW2€¢÷7F&ÆUö§6öåö'—FW2‡&W÷'B¢“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢6W'F–f–6FR&W÷'Bf–ævW'&–çBÖ—6ÖF6‚"¢6W'Eöf–ævW'&–çG2Ò6W'F–f–6FRævWB‚&f–ævW'&–çG2"’÷"·Ð¢W‡V7FVE÷7V'6WBÒ°¢'6÷W&6R#¢7W'&VçEöf–ævW'&–çG5²'6÷W&6Uö'—FW2%ÒÀ¢&&÷VæF'’#¢7W'&VçEöf–ævW'&–çG5²&W—6öFUö&÷VæF'’%ÒÀ¢&'F–f7E÷&r#¢7W'&VçEöf–ævW'&–çG5²&'F–f7E÷&r%ÒÀ¢&'F–f7E÷6VÖçF–2#¢7W'&VçEöf–ævW'&–çG5²&'F–f7E÷6VÖçF–2%ÒÀ¢&6æöæ–6Â#¢7W'&VçEöf–ævW'&–çG5²&6æöæ–6Â%ÒÀ¢''6W"#¢7W'&VçEöf–ævW'&–çG5²''6W"%ÒÀ¢'66†VÖ#¢7W'&VçEöf–ævW'&–çG5²'66†VÖ%ÒÀ¢''VçF–ÖR#¢7W'&VçEöf–ævW'&–çG5²''VçF–ÖR%ÒÀ¢''VÆW2#¢7W'&VçEöf–ævW'&–çG5²''VÆW2%ÒÀ¢'fÆ–FF÷"#¢7W'&VçEöf–ævW'&–çG5²'fÆ–FF÷"%ÒÀ¢'&ö¦V7Eöæ6†÷'2#¢7W'&VçEöf–ævW'&–çG5²'&ö¦V7Eöæ6†÷'2%ÒÀ¢'F&vWEöÖöFVÅö6öçG&7B#¢7W'&VçEöf–ævW'&–çG5²'F&vWEöÖöFVÅö6öçG&7B%ÒÀ¢Ð¢–b6W'Eöf–ævW'&–çG2ÒW‡V7FVE÷7V'6WC ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢6W'F–f–6FRf–ævW'&–çG2&R7FÆR"¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB††æFöfb“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢†æFöfb—2Vç6–væVB÷"F×W&VB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B‡6VÆbå÷&ö¦V7E÷66†VÖÂ&†æFöfeöÖæ–fW7B"Â†æFöfb¢–b€¢6W'F–f–6FRævWB‚&†æFöfeö–B"’Ò†æFöfbævWB‚&†æFöfeö–B"¢÷ ¢†æFöfbævWB‚'v÷&µö÷&FW%ö–B"’Òv÷&µö÷&FW%ö–@¢÷"†æFöfbævWB‚&Æöv–6Åö'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"†æFöfbævWB‚&f–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢†æFöfb&–æF–ærÖ—6ÖF6‚"¢&öGV7F–öå÷G&6Uö–BÒ7G"€¢6W'F–f–6FRævWB‚'&öGV7F–öå÷7FvU÷G&6Uö–B"’÷"" ¢¢&÷WFU÷G&6Uö–BÒ7G"†6W'F–f–6FRævWB‚'&öGV7F–öå÷G&6Uö–B"’÷"""¢VF—E÷G&6Uö–BÒ7G"†6W'F–f–6FRævWB‚&VF—E÷G&6Uö–B"’÷"""¢–b€¢æ÷B&öGV7F–öå÷G&6Uö–@¢÷"&öGV7F–öå÷G&6Uö–@¢Ò7G"‡&ö÷Eö†æFöfbævWB‚'7FvU÷G&6Uö–B"’÷"""¢÷"æ÷B&÷WFU÷G&6Uö–@¢÷"&÷WFU÷G&6Uö–@¢Ò7G"€¢†æFöfbævWB‚'&VVF—E÷&÷WFU÷G&6Uö–B"¢÷"†æFöfbævWB‚'7FvU÷G&6Uö–B"¢÷"" ¢¢÷"æ÷BVF—E÷G&6Uö–@¢÷"VF—E÷G&6Uö–BÒ7G"†6W'F–f–6FRævWB‚'7FvU÷G&6Uö–B"’÷"""¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢&öGV7F–öâöVF—BG&6R6W'F–f–6FR&–æF–ærÖ—6ÖF6‚ ¢¢&öGV7F–öå÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–C×&öGV7F–öå÷G&6Uö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&öGV7F–öå÷G&6R“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢&öGV7F–öâG&6R—2Vç6–væVB÷"F×W&VB ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ'7FvU÷F–Ö–æu÷G&6R"Â&öGV7F–öå÷G&6P¢¢–b€¢&öGV7F–öå÷G&6RævWB‚'G&6Uö–B"’Ò&öGV7F–öå÷G&6Uö–@¢÷"&öGV7F–öå÷G&6RævWB‚'v–æF÷u÷&öÆR"¢Ò&ö÷Eö†æFöfbævWB‚&g&öÕ÷v–æF÷u÷&öÆR"¢÷"&öGV7F–öå÷G&6RævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"&öGV7F–öå÷G&6RævWB‚&–çWEöf–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢&öGV7F–öâG&6R&–æF–ærÖ—6ÖF6‚ ¢¢–b†æFöfbævWB‚'&VVF—Eö¶–æB"’ÓÒ&–æFWVæFVçEöVF—E÷7V66W76÷"# ¢&÷WFU÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–C×&÷WFU÷G&6Uö–BÀ¢¢–b€¢æ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB‡&÷WFU÷G&6R¢÷"&÷WFU÷G&6RævWB‚'G&6Uö–B"’Ò&÷WFU÷G&6Uö–@¢÷"&÷WFU÷G&6RævWB‚&÷W&F–öâ"¢Ò&VF—E÷7F÷'–&ö&E÷&VVF—E÷&÷WFR ¢÷"&÷WFU÷G&6RævWB‚'v–æF÷u÷&öÆR"’Ò&–æFWVæFVçEöVF—B ¢÷"&÷WFU÷G&6RævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"&÷WFU÷G&6RævWB‚&–çWEöf–ævW'&–çG2"¢Ò7W'&VçEöf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢&RÖVF—B&÷WFRG&6R&–æF–ærÖ—6ÖF6‚ ¢¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ'7FvU÷F–Ö–æu÷G&6R"Â&÷WFU÷G&6P¢¢VÆ–b&÷WFU÷G&6Uö–BÒ&öGV7F–öå÷G&6Uö–C ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢&ö÷B6W'F–f–6FR&öGV7F–öâG&6R6†ævVB ¢¢ÆVFvW"Ò6VÆbç7F÷&RæÆöEöÆVFvW"†æÖW76RÂ&ö¦V7Eö–B¢WfVçBÒæW‡B€¢€¢&÷p¢f÷"&÷r–âÆVFvW"ævWB‚&WfVçG2"ÂµÒ¢–b&÷rævWB‚&WfVçEö–B"’ÓÒ6W'F–f–6FRævWB‚&WfVçEö–B"¢’À¢æöæRÀ¢¢–b€¢æ÷B—6–ç7Fæ6R†WfVçBÂF–7B¢÷"WfVçBævWB‚&†æFöfeö–B"’Ò6W'F–f–6FRævWB‚&†æFöfeö–B"¢÷"WfVçBævWB‚'7FvU÷G&6Uö–B"’Ò6W'F–f–6FRævWB‚'7FvU÷G&6Uö–B"¢÷"WfVçBævWB‚&f–ÆUöf–ævW'&–çB"’Ò7W'&VçEöf–ævW'&–çG5²&'F–f7E÷&r%Ð¢÷"WfVçBævWB‚&6æöæ–6Åöf–ævW'&–çB"’Ò7W'&VçEöf–ævW'&–çG5²&6æöæ–6Â%Ð¢“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢VF—BWfVçB&–æF–ærÖ—6ÖF6‚"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B‡6VÆbåöÆVFvW%÷66†VÖÂ&VF—EöWfVçB"ÂWfVçB¢–bæ÷Bç’€¢—6–ç7Fæ6R‡66÷RÂF–7B¢æB66÷RævWB‚&Æ7EöWfVçEö–B"’ÓÒWfVçBævWB‚&WfVçEö–B"¢f÷"66÷R–âÆVFvW"ævWB‚'66÷U÷7FFW2"ÂµÒ¢“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷&WV—&VC¢ÆFW7BÆ–æVvR6W'F–f–6FR—2æ÷BF†RÆVFvW"66÷RWF†÷&—G’ ¢¢VF—E÷G&6RÒ6VÆbç7F÷&RævWE÷&ö¦V7E÷&V6÷&B€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢¶–æCÒ'FVÆVÖWG'’"À¢&V6÷&Eö–CÖVF—E÷G&6Uö–BÀ¢¢–bæ÷B6VÆbç7F÷&Rç&ö¦V7E÷&V6÷&Eö—5÷6–væVB†VF—E÷G&6R“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢VF—BG&6R—2Vç6–væVB÷"F×W&VB"¢6VÆbå÷fÆ–FFU÷66†VÖ÷&V6÷&B€¢6VÆbå÷&ö¦V7E÷66†VÖÂ'7FvU÷F–Ö–æu÷G&6R"ÂVF—E÷G&6P¢¢–b€¢VF—E÷G&6RævWB‚'G&6Uö–B"’ÒVF—E÷G&6Uö–@¢÷"VF—E÷G&6RævWB‚'v–æF÷u÷&öÆR"’Ò&–æFWVæFVçEöVF—B ¢÷"VF—E÷G&6RævWB‚&'F–f7Eö–B"’Ò'F–f7Eö–@¢÷"VF—E÷G&6RævWB‚&–çWEöf–ævW'&–çG2"’Ò7W'&VçEöf–ævW'&–çG0¢“ ¢&—6RfÇVTW'&÷"‚&–æFWVæFVçEöVF—E÷&WV—&VC¢VF—BG&6R&–æF–ærÖ—6ÖF6‚"¢&WGW&â6W'F–f–6FP ¢öfVæ6U÷&öÆUö÷W&F–öà¢FVb6–væöfe÷F6²€¢6VÆbÀ¢æÖW76S¢7G"À¢¢À¢&ö¦V7Eö–C¢7G"À¢v÷&µö÷&FW%ö–C¢7G"À¢FV6—6–öã¢7G"À¢6–væW#¢7G"À¢&WVW7E÷6W76–öåö–C¢7G"ÂæöæRÒæöæRÀ¢’ÓâFööÅ&W7öç6S ¢–bæ÷B6–væW"ç7G&—‚“ ¢&—6RfÇVTW'&÷"‚'6–væW"—2&WV—&VBf÷"‡VÖâ6–vâÖöfb"¢v—F‚6VÆbç7F÷&Ræ×WFFU÷&ö¦V7B†æÖW76RÂ&ö¦V7Eö–B’2†‡V"ÂÆVFvW"“ ¢v÷&µö÷&FW"Òöf–æE÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–B¢6VÆbåö76W'E÷v÷&µö÷&FW%÷6W76–öâ€¢æÖW76RÂv÷&µö÷&FW"Â&WVW7E÷6W76–öåö–@¢¢÷W&F–öâÒ7G"‡v÷&µö÷&FW"ævWB‚&÷W&F–öâ"’÷"""¢–b÷W&F–öâ–â5Dõ%”$ô$EôõU$D”ôå3 ¢6VÆbåö76W'E÷7F÷'–&ö&EöWF†÷&—G’€¢v÷&µö÷&FW"À¢6VÆbå÷v÷&µö÷&FW%÷&öÆR€¢÷W&F–öâÂ7G"‡v÷&µö÷&FW"ævWB‚&ÖöFR"’÷"""¢’À¢¢VÆ–bv÷&µö÷&FW"ævWB‚'6W'fW%ö7F÷%÷&öÆR"’Ò'&öGV7F–öâ# ¢&—6RfÇVTW'&÷"€¢'v–æF÷u÷&öÆUöÖG&—…öÖ—6ÖF6ƒ¢6–vâÖöfb&WV—&W2F†Rv÷&² ¢&÷&FW"w26W'fW"Ö&÷VæB&öGV7F–öâ÷"&W—"WF†÷&—G’ ¢¢–æFWVæFVçEö6W'F–f–6FRÒæöæP¢–bFV6—6–öâÓÒ&&÷fR"æBv÷&µö÷&FW"ævWB‚&÷W&F–öâ"’–â5Dõ%”$ô$EôõU$D”ôå3 ¢–æFWVæFVçEö6W'F–f–6FRÒ6VÆbåö6ÆVåö–æFWVæFVçEö6W'F–f–6FUöf÷%÷v÷&µö÷&FW"€¢æÖW76RÀ¢&ö¦V7Eö–BÀ¢‡V"À¢v÷&µö÷&FW%ö–BÀ¢¢–bv÷&µö÷&FW%²'7FGW2%ÒÓÒ'76VB# ¢&ö¦V7EövVçBçG&ç6—F–öå÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–BÂ&v—F–æu÷6–væöfb"¢&W7VÇBÒ&ö¦V7EövVçBç6–væöfe÷v÷&µö÷&FW"†‡V"Âv÷&µö÷&FW%ö–BÂFV6—6–öâ¢‡V%²&‡VÖå÷6–væöfg2%Õ²ÓÕ²'6–væW"%ÒÒ6–væW"ç7G&—‚¢–b–æFWVæFVçEö6W'F–f–6FR—2æ÷BæöæS ¢‡V%²&‡VÖå÷6–væöfg2%Õ²ÓÕ²&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B%ÒÒ€¢–æFWVæFVçEö6W'F–f–6FU²&6W'F–f–6FUö–B%Ð¢¢WfVçEö–BÒ7G"†–æFWVæFVçEö6W'F–f–6FRævWB‚&WfVçEö–B"’÷"""¢VF—E÷66÷RÒæW‡B€¢€¢&÷p¢f÷"&÷r–âÆVFvW"ævWB‚'66÷U÷7FFW2"ÂµÒ¢–b7G"‡&÷rævWB‚&Æ7EöWfVçEö–B"’÷"""’ÓÒWfVçEö–@¢’À¢æöæRÀ¢¢–bæ÷B—6–ç7Fæ6R†VF—E÷66÷RÂF–7B“ ¢&—6RfÇVTW'&÷"€¢&–æFWVæFVçEöVF—E÷66÷UöÖ—76–æs¢‡VÖâ6–vâÖöfb6ææ÷B ¢&Gfæ6R‡V"v—F†÷WBF†RÖF6†–ærÆVFvW"66÷R ¢¢7F÷'–&ö&EövVçBç6–væöfe÷66÷R€¢ÆVFvW"À¢7G"†VF—E÷66÷U²'66÷Uö¶W’%Ò’À¢&&÷fR"–bFV6—6–öâÓÒ&&÷fR"VÇ6R'&V÷Vâ"À¢6†V6·ö–çEöWF†÷&—¦W#×6VÆbåöÆVFvW%ö6†V6·ö–çEöWF†÷&—¦W"€¢æÖW76RÂ&ö¦V7Eö–@¢’À¢¢–bFV6—6–öâÓÒ&&÷fR# ¢f÷"FVÆ—fW&&ÆR–â‡V"ævWB‚&FVÆ—fW&&ÆW2"ÂµÒ“ ¢–bFVÆ—fW&&ÆRævWB‚'v÷&µö÷&FW%ö–B"’ÓÒv÷&µö÷&FW%ö–C ¢FVÆ—fW&&ÆU²'7FGW2%ÒÒ&f–æÂ ¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ&&6†—fVB ¢VÇ6S ¢‡V%²'&ö¦V7E÷7FFR%Õ²&7W'&VçE÷7FvR%ÒÒ&&÷fVB ¢&WGW&âFööÅ&W7öç6R€¢7FGW3×7G"‡&W7VÇE²'7FGW2%Ò’À¢7VÖÖ'“Öb.K«®[z^zÛîjŽ[{.Šë[Ù^ûÙÇ¶FV6—6–öçÞûÙÇ·&W7VÇE²w7FGW2u×Ò"À¢&ö¦V7Eö–C×&ö¦V7Eö–BÀ¢v÷&µö÷&FW%ö–C×v÷&µö÷&FW%ö–BÀ¢FF×°¢¢§&W7VÇBÀ¢&–æFWVæFVçEöVF—Eö6W'F–f–6FUö–B#¢€¢–æFWVæFVçEö6W'F–f–6FRævWB‚&6W'F–f–6FUö–B"¢–b–æFWVæFVçEö6W'F–f–6FR—2æ÷BæöæP¢VÇ6RæöæP¢’À¢ÒÀ¢