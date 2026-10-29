"""Explicit style-library selection; never infer style from a title or dialogue."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import unicodedata

REF = Path(__file__).resolve().parents[1] / "references"
CONTRACT = "seedance25-prompt-library-v1"
FILES = {"animation": "prompt_library_animation.json",
         "photorealistic": "prompt_library_photorealistic.json"}
VISUAL_CONTRACT = "project-visual-style-v1"
VISUAL_DIMENSIONS = {"character_design": "造型", "materials": "材质", "palette": "配色", "shading": "明暗表现"}
VISUAL_FIELDS = ("镜头类型", "可见主体", "空间站位描述", "起始状态", "机位", "运镜方式",
                 "画面内容", "环境动态", "生成限制", "参考图锚定", "光影", "光源来源", "光源方向",
                 "色温倾向", "切镜/转场提示词")
# Finite style vocabulary, not an unrestricted natural-language classifier.
STYLE_PATTERNS = {
    "photorealistic": r"仿真人|拟真人|真人(?:电影|影视|实拍|摄影|写实|风格|质感)|真实摄影机拍摄|photorealis(?:tic|m)|live[-_]?action",
    "3d": r"(?:3d|三维)(?:模型)?(?:国漫|动漫|动画|卡通|赛璐璐|风格化写实|animation|anime|cartoon)|三渲二|cel[-_]?shad(?:ed|ing)",
    "2d": r"(?:2d|二维)(?:国漫|动漫|动画|手绘|绘画|绘制|animation|anime|cartoon)|二维风格",
}
NEGATIVE = r"(?:禁止|不得|不允许|不要|不准|避免|排除|杜绝|拒绝|不)(?:(?:再|额外|继续|直接|同时))*(?:生成|采用|使用|呈现|保留|添加|混合|融入|转为)?|并非|不是|非"
POLARITY = re.compile(r"(?P<negative>" + NEGATIVE + r")|(?:而是|改为|变成|切换为|采用|使用|呈现|保留|添加|混合|融入|带有|具有|仍要|而要|也要|需要|转为|use|adopt|apply|render)")
STYLE_LIST_GAP = re.compile(r'(?:风格|质感|观感|任何|一切|所有|以及|和|与|及|或|的|[、:：\"“”「」『』()])*')


class PromptLibraryError(ValueError):
    pass


def _read(name):
    return json.loads((REF / name).read_text(encoding="utf-8"))


def _fail(path, reason):
    raise PromptLibraryError(f"prompt_library:{path}: {reason}")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _normalized(value):
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+|[\u200b-\u200f\ufeff]", "", text)


def assert_style_text(text, profile, library_id, variant, path, *, render_mode=None):
    """Check instruction text only; callers own literal/dialogue masking."""
    allowed = variant if library_id == "animation" else library_id
    patterns = [pattern for identity, pattern in STYLE_PATTERNS.items() if identity != allowed]
    patterns += [re.escape(_normalized(term)) for term in profile["incompatible_positive_terms"]]
    patterns += [re.escape(_normalized(term)) for mode, terms in profile.get("render_mode_positive_terms", {}).items()
                 if render_mode is not None and mode != render_mode for term in terms]
    conflict = re.compile("|".join(patterns))
    for raw in re.split(r"[。；;，,\n!?！？]", unicodedata.normalize("NFKC", text)):
        clause = _normalized(raw)
        for hit in conflict.finditer(clause):
            markers = list(POLARITY.finditer(clause[:hit.start()]))
            negative = bool(markers and markers[-1].lastgroup == "negative")
            if negative:
                # A negative may cover a list of styles, not arbitrary text such
                # as '禁止字幕真人电影风格'. New positive verbs reset its scope.
                gap = conflict.sub("", clause[markers[-1].end():hit.start()])
                negative = STYLE_LIST_GAP.fullmatch(gap) is not None
                if len(markers) > 1 and markers[-2].lastgroup == "negative" and STYLE_LIST_GAP.fullmatch(
                        clause[markers[-2].end():markers[-1].start()]):
                    negative = False  # e.g. 禁止任何非仿真人风格
            if re.fullmatch(r'(?:no|without|avoid|donot(?:use|adopt|apply)?)[\"“「『(]*', clause[:hit.start()]):
                negative = True
            # A trailing negation must apply directly to this style, not to an
            # unrelated later instruction. Exceptions/double negations fail closed.
            if re.fullmatch(r"(?:风格|质感|观感)?[\"”」』)]*(?:不采用|不使用|禁用|排除)", clause[hit.end():]):
                negative = True
            if re.search(r"(?:并非|不是|不能|不得|禁止)不|不(?:禁止|排除)|(?:无需|无须)(?:避免|排除)|除外|例外", clause):
                negative = False
            if not negative:
                _fail(path, "conflicting positive style: " + hit.group())


def make_visual_style(selection, *, style_id, revision, render_mode, character_design, materials, palette, shading):
    resolved = resolve_selection(selection)
    profile = resolved["profile"]
    values = dict(style_id=style_id, revision=revision, render_mode=render_mode,
                  character_design=character_design, materials=materials, palette=palette, shading=shading)
    for key, value in values.items():
        if not isinstance(value, str) or not _normalized(value) or len(value) > 400 or "\n" in value or "\r" in value:
            _fail("visual_style." + key, "requires one explicit, nonempty bounded value")
    if render_mode not in profile["render_modes"]:
        _fail("visual_style.render_mode", "mode is not in the selected library and variant")
    for key in VISUAL_DIMENSIONS:
        if re.fullmatch(r"待定|未定|默认|自动|同上|按参考图|统一风格|按项目设定", values[key].strip()):
            _fail("visual_style." + key, "concrete project description required, not a placeholder")
        assert_style_text(values[key], profile, selection["library_id"], selection["variant"], "visual_style." + key,
                          render_mode=render_mode)
    result = {"contract": VISUAL_CONTRACT, "library_id": selection["library_id"], "variant": selection["variant"], **values}
    result["content_sha256"] = _digest({"selection": selection, "visual_style": result})
    return result


def _visual_style(source_lock, *, required=False):
    visual = source_lock.get("visual_style")
    if visual is None:
        if required:
            _fail("source_lock.visual_style", "explicit project visual style required before production; no default rendering")
        return None
    keys = {"contract", "library_id", "variant", "style_id", "revision", "render_mode", "content_sha256", *VISUAL_DIMENSIONS}
    if not isinstance(visual, dict) or set(visual) != keys:
        _fail("source_lock.visual_style", "complete project visual-style card required")
    expected = make_visual_style(source_lock["prompt_library"], **{k: visual[k] for k in
                                ("style_id", "revision", "render_mode", *VISUAL_DIMENSIONS)})
    if visual != expected:
        _fail("source_lock.visual_style", "style/library/fingerprint changed; explicitly revise and freeze a new plan")
    return visual


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
            "visual_style_contract": {"contract": VISUAL_CONTRACT, "location": "source_lock.visual_style",
                "fields": ["style_id", "revision", "render_mode", *VISUAL_DIMENSIONS],
                "fingerprint": "make_visual_style binds the full card and current library selection",
                "render_modes": "only the selected profile.render_modes; no default",
                "freeze": "project/source_lock visual_style, prompt_library and prompt_references are owned plan partitions",
                "actual_media_validation": "not_verified"},
            "selection_required_for": "new writes; legacy read-only remains unclassified",
            "selection_policy": "explicit_only_no_default_no_cross_library_fallback",
            "read_tool": {"name": "get_agent_status", "parameters": ["prompt_library_id", "prompt_variant", "prompt_visual_style"],
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
        if required or "prompt_references" in source_lock or "visual_style" in source_lock:
            _fail("source_lock.prompt_library", "select library; no automatic photorealistic default")
        return None
    resolved = resolve_selection(source_lock["prompt_library"])
    if source_lock.get("target_model") != "Seedance 2.5":
        _fail("source_lock.target_model", "this library is bound to Seedance 2.5")
    visual = _visual_style(source_lock, required=required)
    references = source_lock.get("prompt_references", [])
    if not isinstance(references, list):
        _fail("prompt_references", "expected explicit reference-role records")
    seen = set()
    for index, reference in enumerate(references):
        path = f"prompt_references[{index}]"
        base_keys = {"id", "role", "library_id", "variant"}
        if not isinstance(reference, dict) or not base_keys <= set(reference) or set(reference) - base_keys - {"asset_sha256", "visual_style_sha256"}:
            _fail(path, "requires id, role, library_id, variant; only asset/style fingerprints may be added")
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
            if visual is not None:
                if reference.get("visual_style_sha256") != visual["content_sha256"]:
                    _fail(path, "visual reference must declare this exact project visual style")
                if not isinstance(reference.get("asset_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", reference["asset_sha256"]):
                    _fail(path, "visual reference requires a file fingerprint; actual pixels still need review")
        elif reference["library_id"] not in {*FILES, "neutral", "unknown"}:
            _fail(path, "unknown source library")
        elif reference["library_id"] in FILES:
            make_selection(reference["library_id"], reference["variant"])
        if "asset_sha256" in reference and (not isinstance(reference["asset_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", reference["asset_sha256"])):
            _fail(path, "invalid asset fingerprint")
    return resolved["profile"]


def style_fragment(source_lock):
    profile = selected_profile(source_lock)
    if profile is None:
        return ""
    visual = _visual_style(source_lock, required=True)
    return _style_fragment(profile, visual)


def _style_fragment(profile, visual):
    if visual is None:  # Read-only compatibility; never used for new production.
        return profile["style_prompt"]
    parts = [profile["style_prompt"].rstrip("。；;"), "渲染风格：" + profile["render_modes"][visual["render_mode"]]]
    parts.extend("美术" + label + "：" + visual[key].rstrip("。；;") for key, label in VISUAL_DIMENSIONS.items())
    return "；".join(parts) + "。"


def assert_prompt_library(storyboard, *, required=False):
    lock = storyboard.get("source_lock")
    profile = selected_profile(lock, required=required)
    if profile is None:
        return
    visual = _visual_style(lock, required=required)
    from written_payload import context_instruction_text
    for index, shot in enumerate(storyboard.get("shots", [])):
        if not isinstance(shot, dict):
            continue  # The existing canonical/schema validator owns row structure.
        limits = str(shot.get("生成限制", ""))
        expected = _style_fragment(profile, visual).rstrip("。；;")
        path = f"shots[{index}].生成限制"
        if limits.count(expected) != 1 or not re.search(r"(?:^|[。；;])\s*" + re.escape(expected) + r"(?:[。；;]|$)", limits):
            _fail(path, "positive complete project style block must appear exactly once")
        for field in VISUAL_FIELDS:
            text = context_instruction_text(str(shot.get(field, "")), field)
            # Reuse the same literal grammar at explicit sentence boundaries:
            # earlier speech must not hide a later, separately declared paper.
            text = re.sub(r'(?:[“"「『][^”"」』\n]*[”"」』]|[^。！？!?\n])+',
                          lambda match: context_instruction_text(match.group(), field), text)
            assert_style_text(text, profile, lock["prompt_library"]["library_id"],
                              lock["prompt_library"]["variant"], f"shots[{index}].{field}",
                              render_mode=visual["render_mode"] if visual else None)


def findings(storyboard):
    try:
        assert_prompt_library(storyboard)
    except PromptLibraryError as exc:
        return [{"error_code": "E-REFERENCE-001", "severity": "fail", "blocking": True,
                 "location": "source_lock.prompt_library", "evidence": str(exc),
                 "repair_action": "明确选定一个库及子风格，校正素材职责与生成限制；不改原剧情或对白。"}]
    return []
