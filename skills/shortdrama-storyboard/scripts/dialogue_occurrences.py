"""Exact ordered source-occurrence coverage, independent of speech timing.

The source parser is the authority. This module only compares its occurrence
inventory with dialogue extracted from the actual canonical picture content.
Standalone candidate inventories remain claims until the service recomputes
them from the registered source. No candidate speaker ever enters the parser.

Every range is half-open and measured in Python/Unicode codepoints relative
to one complete audible occurrence, including all original punctuation. A
shot writes its own fragment once; timing stages reference that same text.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import hashlib
import json
from typing import Any


OCCURRENCE_RANGE_CONTRACT = "source-dialogue-occurrence-ranges-v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def build_occurrence_inventory(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve one row per occurrence; never deduplicate identical dialogue.

    Source-derived IDs, if present, are retained for later authoritative
    binding. The local fallback is explicitly a *claim* identity, not proof
    of source registration. A service call must supply its parsed inventory.
    """
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"occurrence[{index}] must be an object")
        speaker, text = row.get("speaker"), row.get("text")
        if not isinstance(speaker, str) or not speaker or speaker != speaker.strip():
            raise ValueError(f"occurrence[{index}].speaker must be an exact nonempty identity")
        if not isinstance(text, str) or not text:
            raise ValueError(f"occurrence[{index}].text must be a nonempty exact string")
        identity = row.get("occurrence_id")
        if identity is None:
            identity = "claim-occurrence:" + _digest({
                "contract": OCCURRENCE_RANGE_CONTRACT,
                "source_ordinal": index, "speaker": speaker, "text": text,
            })
        if not isinstance(identity, str) or not identity or identity != identity.strip():
            raise ValueError(f"occurrence[{index}].occurrence_id is invalid")
        if identity in seen:
            raise ValueError(f"duplicate occurrence_id: {identity}")
        seen.add(identity)
        result.append({**row, "occurrence_id": identity, "source_ordinal": index})
    return result


def storyboard_utterances(
    shots: Sequence[dict[str, Any]],
    extract_dialogues: Callable[[Any], Sequence[tuple[str, str]]],
) -> list[dict[str, Any]]:
    """Obtain actual audible text only from the canonical picture field."""
    result = []
    seen: set[str] = set()
    for shot in shots:
        shot_id = str(shot.get("镜头编号") or shot.get("shot_uid") or "")
        if not shot_id or shot_id in seen:
            raise ValueError("dialogue coverage requires unique nonempty canonical shot IDs")
        seen.add(shot_id)
        for index, (speaker, text) in enumerate(extract_dialogues(shot.get("画面内容"))):
            result.append({
                "shot_id": shot_id, "dialogue_index": index,
                "speaker": speaker, "text": text,
            })
    return result


def protected_dialogue_rows(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Select one inventory; mirrored compatibility rows cannot double count."""
    ingestion = storyboard.get("source_ingestion")
    nested = ingestion.get("protected_dialogue") if isinstance(ingestion, dict) else None
    top = storyboard.get("protected_dialogue")
    for location, present, rows in (
        ("source_ingestion.protected_dialogue", isinstance(ingestion, dict) and "protected_dialogue" in ingestion, nested),
        ("protected_dialogue", "protected_dialogue" in storyboard, top),
    ):
        if present and (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)):
            raise ValueError(f"{location} must be an array of objects")
    if nested is not None and top is not None:
        exact = lambda rows: [(row.get("speaker"), row.get("text")) for row in rows]
        if exact(nested) != exact(top):
            raise ValueError("protected_dialogue mirrors differ in source order/count/speaker/text")
        # A top-level mirror may not smuggle a different explicit occurrence
        # or mapping while the source-ingestion copy is selected.
        combined = []
        for index, (left, right) in enumerate(zip(nested, top)):
            for key in ("occurrence_id", "mapped_shot_uids", "target_shots"):
                if key in left and key in right and left[key] != right[key]:
                    raise ValueError(f"protected_dialogue mirror[{index}].{key} differs")
            combined.append({**right, **left})
        return combined
    return nested if nested is not None else (top or [])


def align_dialogue_occurrences(
    occurrences: Sequence[dict[str, Any]],
    utterances: Sequence[dict[str, Any]],
    *,
    fragments: Sequence[dict[str, Any]] | None = None,
    shot_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate or derive a lossless, single-consumption occurrence mapping.

    Unmapped unrelated dialogue is returned for the separate THG-16 adapted
    dialogue audit. A repeated/changed-speaker source fragment is not silently
    reclassified as adaptation. Explicit mapping order must equal canonical
    audible order, and each actual utterance belongs to exactly one fragment.
    """
    issues: list[dict[str, Any]] = []

    def fault(code: str, detail: str, **context: Any) -> None:
        issues.append({"code": code, "detail": detail, **context})

    try:
        inventory = build_occurrence_inventory(occurrences)
    except (TypeError, ValueError) as exc:
        return {"contract": OCCURRENCE_RANGE_CONTRACT, "status": "fail_current", "issues": [
            {"code": "invalid_occurrence_inventory", "detail": str(exc)},
        ], "occurrences": [], "fragments": [], "covered_utterances": [], "unmapped_utterances": list(utterances)}
    aliases = shot_aliases or {}
    by_occurrence = {row["occurrence_id"]: row for row in inventory}
    actual_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    actual_order: dict[tuple[str, int], int] = {}
    for position, utterance in enumerate(utterances):
        if not isinstance(utterance, dict):
            fault("invalid_utterance", "actual dialogue must be an object", position=position)
            continue
        index, shot_id = utterance.get("dialogue_index"), utterance.get("shot_id")
        if (not isinstance(shot_id, str) or not shot_id or type(index) is not int or index < 0
                or not isinstance(utterance.get("speaker"), str) or not utterance.get("speaker")
                or not isinstance(utterance.get("text"), str) or not utterance.get("text")):
            fault("invalid_utterance", "actual dialogue requires exact shot, index, speaker and text", position=position)
            continue
        key = (shot_id, index)
        if key in actual_by_key:
            fault("duplicate_utterance", "one actual dialogue identity occurs twice", shot_id=shot_id, dialogue_index=index)
        actual_by_key[key] = utterance
        actual_order[key] = position

    target_sets: dict[str, set[str] | None] = {}
    for row in inventory:
        targets = row.get("mapped_shot_uids") or row.get("target_shots")
        if targets is not None and (not isinstance(targets, list) or any(not isinstance(t, str) for t in targets)):
            fault("invalid_target_shots", "occurrence target shots must be an ordered string array", occurrence_id=row["occurrence_id"])
            target_sets[row["occurrence_id"]] = set()
        else:
            target_sets[row["occurrence_id"]] = {aliases.get(t, t) for t in targets} if targets else None

    resolved: list[dict[str, Any]] = []
    if fragments is None:
        occurrence_index = offset = 0
        for utterance in utterances:
            if not isinstance(utterance, dict):
                continue
            if occurrence_index >= len(inventory):
                break
            row = inventory[occurrence_index]
            actual_text = utterance.get("text")
            targets = target_sets[row["occurrence_id"]]
            if (isinstance(actual_text, str) and actual_text
                    and utterance.get("speaker") == row["speaker"]
                    and (targets is None or utterance.get("shot_id") in targets)
                    and row["text"][offset:].startswith(actual_text)):
                resolved.append({
                    "occurrence_id": row["occurrence_id"],
                    "shot_id": utterance["shot_id"],
                    "dialogue_index": utterance["dialogue_index"],
                    "char_range": [offset, offset + len(actual_text)],
                })
                offset += len(actual_text)
                if offset == len(row["text"]):
                    occurrence_index += 1
                    offset = 0
    elif not isinstance(fragments, (list, tuple)):
        fault("invalid_fragments", "protected_dialogue_fragments must be an array")
    else:
        resolved = list(fragments)

    used: set[tuple[str, int]] = set()
    cursors = {row["occurrence_id"]: 0 for row in inventory}
    validated: list[dict[str, Any]] = []
    previous_actual_order = -1
    previous_source_order = -1
    for fragment_index, fragment in enumerate(resolved):
        if not isinstance(fragment, dict):
            fault("invalid_fragment", "fragment must be an object", fragment_index=fragment_index)
            continue
        occurrence_id = fragment.get("occurrence_id")
        row = by_occurrence.get(occurrence_id) if isinstance(occurrence_id, str) else None
        if row is None:
            fault("unknown_occurrence", "fragment does not bind a registered source occurrence", fragment_index=fragment_index)
            continue
        shot_id = fragment.get("shot_id")
        shot_id = aliases.get(shot_id, shot_id) if isinstance(shot_id, str) else shot_id
        dialogue_index = fragment.get("dialogue_index")
        if not isinstance(shot_id, str) or type(dialogue_index) is not int or dialogue_index < 0:
            fault("invalid_fragment_reference", "fragment requires shot_id and zero-based dialogue_index", fragment_index=fragment_index)
            continue
        key = (shot_id, dialogue_index)
        utterance = actual_by_key.get(key)
        if utterance is None:
            fault("unknown_utterance", "fragment points outside actual canonical dialogue", fragment_index=fragment_index)
            continue
        char_range = fragment.get("char_range")
        if (not isinstance(char_range, (list, tuple)) or len(char_range) != 2
                or any(type(value) is not int for value in char_range)
                or not 0 <= char_range[0] < char_range[1] <= len(row["text"])):
            fault("invalid_range", "range must be nonempty [begin,end) Unicode codepoints within the complete occurrence", fragment_index=fragment_index)
            continue
        start, end = char_range
        if key in used:
            fault("duplicate_fragment_consumption", "one actual utterance cannot cover multiple occurrences or fragments", fragment_index=fragment_index)
            continue
        used.add(key)
        if actual_order[key] <= previous_actual_order:
            fault("canonical_order_mismatch", "fragment order differs from canonical audible order", fragment_index=fragment_index)
        previous_actual_order = actual_order[key]
        if row["source_ordinal"] < previous_source_order:
            fault("source_order_mismatch", "source occurrences were reordered", fragment_index=fragment_index)
        previous_source_order = row["source_ordinal"]
        if start != cursors[occurrence_id]:
            fault("range_gap_or_overlap", "source ranges must start at zero and join exactly once in order", occurrence_id=occurrence_id, expected_start=cursors[occurrence_id], actual_start=start)
        cursors[occurrence_id] = end
        targets = target_sets[occurrence_id]
        if targets is not None and shot_id not in targets:
            fault("target_shot_mismatch", "source occurrence was assigned outside its mapped shots", occurrence_id=occurrence_id, shot_id=shot_id)
        if utterance["speaker"] != row["speaker"]:
            fault("speaker_mismatch", "fragment speaker differs from the source speaker", occurrence_id=occurrence_id, shot_id=shot_id)
        if utterance["text"] != row["text"][start:end]:
            fault("exact_text_mismatch", "fragment must preserve every source character and punctuation", occurrence_id=occurrence_id, shot_id=shot_id, expected_text=row["text"][start:end], actual_text=utterance["text"])
        validated.append({
            "occurrence_id": occurrence_id, "shot_id": shot_id,
            "dialogue_index": dialogue_index, "char_range": [start, end],
        })
    for row in inventory:
        if cursors[row["occurrence_id"]] != len(row["text"]):
            fault("missing_source_range", "the source occurrence is not fully covered", occurrence_id=row["occurrence_id"], missing_range=[cursors[row["occurrence_id"]], len(row["text"])])
    unmapped = [row for key, row in actual_by_key.items() if key not in used]
    exact_direct_texts = {row["text"] for row in inventory}
    exact_direct_texts.update(
        actual_by_key[(fragment["shot_id"], fragment["dialogue_index"])]["text"]
        for fragment in validated
    )
    for utterance in unmapped:
        # Exact duplicate lines, wrong-speaker lines and repeated partial
        # fragments cannot hide in the adapted-dialogue remainder.
        # Do not use substring membership: an independently source-grounded
        # short answer may happen to occur inside a much longer direct line.
        # Such an unbound answer still requires the independent THG-16 proof.
        if utterance["text"] in exact_direct_texts:
            fault("unbound_source_fragment", "extra, repeated, reordered or wrong-speaker source fragment has no unused occurrence", shot_id=utterance["shot_id"], dialogue_index=utterance["dialogue_index"])
    return {
        "contract": OCCURRENCE_RANGE_CONTRACT,
        "status": "pass" if not issues else "fail_current",
        "occurrences": inventory, "fragments": validated,
        "covered_utterances": [{"shot_id": row["shot_id"], "dialogue_index": row["dialogue_index"]} for row in validated],
        "unmapped_utterances": unmapped, "issues": issues,
    }


def align_storyboard_dialogue(
    storyboard: dict[str, Any], shots: Sequence[dict[str, Any]],
    extract_dialogues: Callable[[Any], Sequence[tuple[str, str]]],
    *, authoritative_occurrences: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Integration entry for the validator and the registered-source service.

    Passing authoritative_occurrences verifies explicit candidate identities
    against the source parser. Local success alone cannot authorize a source.
    """
    claims = protected_dialogue_rows(storyboard)
    identity_issues = []
    if authoritative_occurrences is None:
        inventory = claims
    else:
        inventory = []
        if [(r.get("speaker"), r.get("text")) for r in claims] != [(r.get("speaker"), r.get("text")) for r in authoritative_occurrences]:
            identity_issues.append({"code": "authority_inventory_mismatch", "detail": "candidate claims must equal every authoritative occurrence in order"})
        for index, row in enumerate(authoritative_occurrences):
            claim = claims[index] if index < len(claims) else {}
            supplied_id = claim.get("occurrence_id")
            if supplied_id is not None and supplied_id != row.get("occurrence_id"):
                identity_issues.append({"code": "authority_occurrence_identity_mismatch", "detail": "candidate occurrence identity does not match registered source", "source_ordinal": index})
            inventory.append({**row, **{key: claim[key] for key in ("mapped_shot_uids", "target_shots") if key in claim}})
    evidence = storyboard.get("prevention_evidence")
    fragments = evidence.get("protected_dialogue_fragments") if isinstance(evidence, dict) else None
    aliases = {str(shot["shot_uid"]): str(shot.get("镜头编号") or shot["shot_uid"]) for shot in shots if shot.get("shot_uid")}
    result = align_dialogue_occurrences(inventory, storyboard_utterances(shots, extract_dialogues), fragments=fragments, shot_aliases=aliases)
    result["issues"] = identity_issues + result["issues"]
    result["status"] = "pass" if not result["issues"] else "fail_current"
    return result


# Explicitly named alias for callers that already extracted actual dialogue.
validate_dialogue_fragments = align_dialogue_occurrences
