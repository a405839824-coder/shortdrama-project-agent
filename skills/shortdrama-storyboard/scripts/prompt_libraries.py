"""Explicit style-library selection; never infer style from a title or dialogue."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

REF = Path(__file__).resolve().parents[1] / "references"
CONTRACT = "seedance25-prompt-library-v1"
FILES = {"animation": "prompt_library_animation.json",
         "photorealistic": "prompt_library_photorealistic.json"}


class PromptLibraryError(ValueError):
    pass


def _read(name):
    return json.loads((REF / name).read_text(encoding="utf-8"))


def _fail(path, reason):
    raise PromptLibraryError(f"prompt_library:{path}: {reason}")


def make_selection(library_id, variant):
    if not isinstance(library_id, str) or library_id not in FILES:
        _fail("library_id", "choose exactly one of animation / photorealistic")
    library = _read(FILES[library_id])
    if not isinstance(variant, str) or variant not in library["variants"]:
        _fail("variant", f"not in selected library {library_id}")
    content = {"common": _read("seedance25_guidance.json"), "library": library}
    checksum = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":")).encode()).hexdigest()
    return {"contract": CONTRACT, "library_id": library_id, "variant": variant,
            "content_sha256": checksum}


def resolve_selection(selection):
    if not isinstance(selection, dict) or set(selection) != {"contract", "library_id", "variant", "content_sha256"}:
        _fail("selection", "explicit, complete selection object required")
    if not isinstance(selection["library_id"], str) or not isinstance(selection["variant"], str):
        _fail("selection", "library_id and variant must be strings")
    expected = make_selection(selection["library_id"], selection["variant"])
    if selection != expected:
        _fail("selection", "unknown contract or stale library fingerprint; reselect explicitly")
    library = _read(FILES[selection["library_id"]])
    # Only the selected variant is returned: no other library's examples or prose.
    return {"selection": copy.deepcopy(selection), "common": _read("seedance25_guidance.json"),
            "library_id": library["library_id"], "label": library["label"],
            "profile": copy.deepcopy(library["variants"][selection["variant"]])}


def library_contract():
    return {"contract": CONTRACT, "model": "Seedance 2.5",
            "selection_location": "source_lock.prompt_library",
            "reference_location": "source_lock.prompt_references",
            "selection_required_for": "new writes; legacy read-only remains unclassified",
            "selection_policy": "explicit_only_no_default_no_cross_library_fallback",
            "read_tool": {"name": "get_agent_status", "parameters": ["prompt_library_id", "prompt_variant"],
                          "response_field": "selected_prompt_library", "changes_project_selection": False},
            "reference": "references/seedance25_prompt_libraries.md",
            "catalog": [{"library_id": key, "label": _read(filename)["label"],
                         "variants": [{"id": variant, "selection": make_selection(key, variant)}
                                      for variant in _read(filename)["variants"]]}
                        for key, filename in FILES.items()]}


def selected_profile(source_lock, *, required=False):
    if not isinstance(source_lock, dict):
        if required:
            _fail("source_lock", "explicit library selection required before new production")
        return None
    if "prompt_library" not in source_lock:
        if required or "prompt_references" in source_lock:
            _fail("source_lock.prompt_library", "select library; no automatic photorealistic default")
        return None
    resolved = resolve_selection(source_lock["prompt_library"])
    if source_lock.get("target_model") != "Seedance 2.5":
        _fail("source_lock.target_model", "this library is bound to Seedance 2.5")
    references = source_lock.get("prompt_references", [])
    if not isinstance(references, list):
        _fail("prompt_references", "expected explicit reference-role records")
    seen = set()
    for index, reference in enumerate(references):
        path = f"prompt_references[{index}]"
        if not isinstance(reference, dict) or set(reference) != {"id", "role", "library_id", "variant"}:
            _fail(path, "requires id, role, library_id, variant")
        rid = reference["id"]
        if not isinstance(rid, str) or not rid.strip() or rid in seen:
            _fail(path, "reference ID must be nonempty and unique")
        seen.add(rid)
        role = reference["role"]
        if not all(isinstance(reference[key], str) for key in ("role", "library_id", "variant")):
            _fail(path, "role and style declarations must be strings")
        if role not in {"appearance", "style", "scene_style", "composition", "motion", "audio", "lighting"}:
            _fail(path, "unknown reference responsibility")
        if role in {"appearance", "style", "scene_style"}:
            if (reference["library_id"], reference["variant"]) != (
                    source_lock["prompt_library"]["library_id"], source_lock["prompt_library"]["variant"]):
                _fail(path, "visual reference differs from selected style; use a converted target-style asset")
        elif reference["library_id"] not in {*FILES, "neutral", "unknown"}:
            _fail(path, "unknown source library")
        elif reference["library_id"] in FILES:
            make_selection(reference["library_id"], reference["variant"])
    return resolved["profile"]


def style_fragment(source_lock):
    profile = selected_profile(source_lock)
    return profile["style_prompt"] if profile is not None else ""


def assert_prompt_library(storyboard, *, required=False):
    lock = storyboard.get("source_lock")
    profile = selected_profile(lock, required=required)
    if profile is None:
        return
    for index, shot in enumerate(storyboard.get("shots", [])):
        if not isinstance(shot, dict):
            continue  # The existing canonical/schema validator owns row structure.
        limits = str(shot.get("生成限制", ""))
        expected = profile["style_prompt"].rstrip("。；;")
        path = f"shots[{index}].生成限制"
        if limits.count(expected) != 1:
            _fail(path, "selected library style must appear exactly once")
        for clause in re.split(r"[。；;，,\n]", limits.replace(expected, "")):
            clause = clause.strip()
            # Bounded negative clauses are not positive style requests. A contrast
            # must be inspected instead of using a negative prefix as a bypass.
            if re.match(r"^(?:禁止|不得|避免|不要|排除|不采用|不使用)", clause) and not re.search(r"但是|但|然而|而是|改为|同时采用", clause):
                continue
            for term in profile["incompatible_positive_terms"]:
                if term in clause:
                    _fail(path, f"conflicting positive style: {term}")


def findings(storyboard):
    try:
        assert_prompt_library(storyboard)
    except PromptLibraryError as exc:
        return [{"error_code": "E-REFERENCE-001", "severity": "fail", "blocking": True,
                 "location": "source_lock.prompt_library", "evidence": str(exc),
                 "repair_action": "明确选定一个库及子风格，校正素材职责与生成限制；不改原剧情或对白。"}]
    return []
