"""One configurable, evidence-labelled speech and event timeline.

All seconds are planning estimates unless a matching reviewed profile is used.
The candidate profile is not a vendor limit or a measured audiovisual result.
No original dialogue is rewritten or copied into the returned evidence.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re

EPS = 1e-9
PHASE = re.compile(r"(?:【)?(?P<start>\d+(?:\.\d+)?)\s*(?:秒|s)?\s*[-—–~至]\s*(?P<end>\d+(?:\.\d+)?)\s*(?:秒|s)(?:】)?")
DIALOGUE = re.compile(r'(?:^|[；;。！？!?，,、】])\s*(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})说道[：:]?\s*[“"](?P<line>[^”"\n]+)[”"]')
LEGACY = re.compile(r'【(?P<speaker>[\u3400-\u9fffA-Za-z·]{1,12})】\s*[“"]?(?P<line>[^【\n｜|；;”"]+)[”"]?')
QUOTED = re.compile(r'[“"]([^”"\n]+)[”"]')
PSYCH_REFERENCE = re.compile(r"^心理音轨引用[：:]\s*([A-Za-z0-9_-]+)(?=[；;。\s]|$)")
PUNCT = re.compile(r"——|……|\.\.\.|？！|!\?|\?!|[，。！？；：、—…,.!?;:]")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("时间必须为有限数值")
    return float(value)


def _rules(rules=None):
    if rules is not None:
        return rules
    return json.loads((Path(__file__).resolve().parents[1] / "references" / "storyboard_runtime_rules.json").read_text(encoding="utf-8"))


def _profile(rules, profile_id=None):
    policy = rules["timing_policy"]
    identity = profile_id or policy["default_profile_id"]
    matches = [row for row in policy["profiles"] if row.get("id") == identity]
    if len(matches) != 1:
        raise ValueError(f"语速配置未唯一登记：{identity}")
    profile = matches[0]
    if _number(profile.get("units_per_second")) <= 0:
        raise ValueError("登记语速必须大于零")
    if profile.get("rate_basis") not in {"utterance_average", "articulation"}:
        raise ValueError("未知计时口径")
    if profile.get("evidence_status") not in {"provisional", "measured"}:
        raise ValueError("未知语速证据状态")
    if profile.get("rate_basis") == "articulation" or profile.get("evidence_status") == "measured":
        proof = profile.get("evidence", {})
        if profile.get("evidence_status") != "measured" or not all(proof.get(key) for key in ("sample_sha256", "alignment_verified", "reviewed", "model_version", "voice")):
            raise ValueError("实测或净发声配置缺已核对的音频对齐证据")
    return profile


def estimate_utterance(text, profile=None, pauses=()):
    profile = profile or _profile(_rules())
    rate = _number(profile.get("units_per_second"))
    if rate <= 0:
        raise ValueError("语速必须大于零")
    text = str(text or "")
    # Mixed-language/digit readings are labelled approximate until a reviewed
    # matching reading/counting profile is registered; never rewrite the source.
    units = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text))
    seen, positions, extra = {}, {}, 0.0
    for pause in pauses:
        if not isinstance(pause, dict):
            raise ValueError("停顿必须是带身份的事件")
        identity, offset = pause.get("id"), pause.get("offset")
        seconds = _number(pause.get("seconds"))
        if not identity or type(offset) is not int or not 0 <= offset <= len(text) or seconds < 0:
            raise ValueError("停顿身份、字符位置或时长非法")
        signature = (offset, seconds, pause.get("extra") is True)
        if identity in seen and seen[identity] != signature:
            raise ValueError("同一停顿身份绑定了不同事件")
        if offset in positions and positions[offset] != (seconds, pause.get("extra") is True):
            raise ValueError("同一字符位置的停顿存在矛盾时长")
        seen[identity] = signature
        if offset in positions:
            continue
        positions[offset] = (seconds, pause.get("extra") is True)
        if profile.get("rate_basis") == "articulation" or pause.get("extra") is True:
            extra += seconds
    return {"seconds": units / rate + extra, "units": units,
            "profile_id": profile.get("id"), "evidence_status": profile.get("evidence_status"),
            "rate_basis": profile.get("rate_basis"), "extra_pause_seconds": extra,
            "mixed_reading_requires_review": bool(re.search(r"[A-Za-z0-9]", text)),
            "punctuation_token_count": len(list(PUNCT.finditer(text)))}


def _phase_rows(shot):
    text = str(shot.get("画面内容") or "")
    from performance_checks import eye_visibility_timeline_scan
    scan = eye_visibility_timeline_scan(text, duration=_number(shot.get('时长')),
                                        visible_subjects=shot.get('可见主体'))
    quote_ranges = [(m.start(), m.end()) for m in QUOTED.finditer(text)]
    matches = [m for m in PHASE.finditer(scan) if not any(a <= m.start() < b for a, b in quote_ranges)]
    if not matches:
        return [{"start": 0.0, "end": _number(shot.get("时长")), "offset": 0, "stop": len(text), "body": text}]
    return [{"start": float(m.group("start")), "end": float(m.group("end")),
             "offset": m.end(), "stop": matches[i + 1].start() if i + 1 < len(matches) else len(text),
             "body": text[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]}
            for i, m in enumerate(matches)]


def _audible_rows(shot):
    text = str(shot.get("画面内容") or "")
    rows = []
    for pattern in (DIALOGUE, LEGACY):
        for match in pattern.finditer(text):
            line = match.group("line").strip()
            if pattern is LEGACY:
                sentence = re.match(r"(.+?[。！？?!](?:[。！？?!…])?)", line)
                line = sentence.group(1).strip() if sentence else line
            rows.append({"speaker": match.group("speaker").strip(), "text": line,
                         "offset": match.start("line"), "kind": "dialogue"})
    psychological = str(shot.get("心理音轨") or "").strip()
    absent = re.fullmatch(
        r"(?:无|不适用|无心理音轨|无心理旁白|不适用[：:]本镜无心理音轨)[。；;\s]*"
        r"(?:(?:人物)?不(?:张嘴|驱动口型|生成字幕|计入(?:角色)?对白(?:时长)?)[、，,；;。\s]*)*", psychological,
    )
    if psychological and not absent and not PSYCH_REFERENCE.match(psychological):
        quotes = list(QUOTED.finditer(psychological))
        if quotes:
            for match in quotes:
                rows.append({"speaker": "心理音轨", "text": match.group(1), "offset": match.start(1),
                             "kind": "psychological", "source_range": [match.start(1), match.end(1)]})
        else:
            # Do not count only a label and silently ignore unquoted audio.
            spoken = re.split(r"[；;](?=.*(?:不张嘴|不驱动|不生成|不计入))", psychological, maxsplit=1)[0]
            spoken = re.sub(r"^(?:心理旁白|心理音轨|内心独白|旁白)[：:]\s*", "", spoken)
            rows.append({"speaker": "心理音轨", "text": spoken, "offset": 0, "kind": "psychological",
                         "source_range": [0, len(spoken)]})
    return rows


def _closed_speaker(body, speaker):
    """An explicitly closed speaker is not the same as a silent listener."""
    visible = QUOTED.sub("〈台词〉", body)
    for clause in re.split(r"[；;。！？!?，,]", visible):
        if re.search(r"说完后|尾音(?:落稳|结束)?后|发言结束后", clause):
            continue
        if re.match(r"\s*" + re.escape(speaker) + r"(?:仍然|仍|始终|全程|一直|继续|保持)?(?:闭口|闭嘴|停止说话|不再发声)", clause):
            return True
        if re.match(r"\s*(?:全镜|本段全程|全程)(?:无对白|不发声)", clause):
            return True
    return False


def validate_timing(project, shots, rules=None):
    rules = _rules(rules)
    faults, advisories, metrics, spans = [], [], {"contract": "shot-timing-v1", "profiles": [], "utterances": [], "speech_calibration_status": "not_measured"}, []

    def fault(message, shot_id=None, location="timing", code="E-TIMELINE-001"):
        row = {"code": code, "message": message, "location": location}
        if shot_id:
            row["shot_id"] = shot_id
            row["location"] = f"shots[{shot_id}].timing"
        faults.append(row)

    evidence_root = project.get("prevention_evidence")
    evidence = evidence_root.get("audio_workflow", {}) if isinstance(evidence_root, dict) else {}
    timing = evidence.get("timing", {}) if isinstance(evidence, dict) else {}
    if not isinstance(timing, dict):
        fault("audio_workflow.timing必须是对象")
        timing = {}
    if timing and timing.get("contract") != "shot-timing-v1":
        fault("未知计时证据契约")
    if set(timing) - {"contract", "default_profile_id", "utterances", "events"}:
        fault("计时证据不接受自报语速、实测标签或未登记字段")
    try:
        default = _profile(rules, timing.get("default_profile_id"))
    except (ValueError, KeyError, TypeError) as exc:
        fault(str(exc))
        default = _profile(rules)
    selected = {default["id"]: default}
    target = project.get("source_lock", {}).get("target_model") if isinstance(project.get("source_lock"), dict) else None
    strict = project.get("source_lock", {}).get("output_contract") == "R10-SPC-1" if isinstance(project.get("source_lock"), dict) else False
    if strict and target and default.get("target_model") not in (None, "unmatched", target):
        fault("所选语速配置目标模型与当前严格输出模型不匹配；不能冒充同条件配置", code="E-DIALOGUE-004")
    inventory, shot_map, phases, bases, elapsed = {}, {}, {}, {}, 0.0
    for index, shot in enumerate(shots):
        sid = str(shot.get("镜头编号") or index + 1)
        try:
            from picture_presentation import timing_view
            shot = timing_view(shot)
            duration = _number(shot.get("时长"))
            if duration <= 0 or sid in shot_map:
                raise ValueError("镜长必须为正、镜头身份必须唯一")
            shot_map[sid], phases[sid], bases[sid] = shot, _phase_rows(shot), elapsed
            elapsed += duration
            dialogue_index, psych_index = 0, 0
            for row in _audible_rows(shot):
                ordinal = dialogue_index if row["kind"] == "dialogue" else psych_index
                row.update(shot_id=sid, index=ordinal)
                inventory[(sid, row["kind"], ordinal)] = row
                if row["kind"] == "dialogue":
                    dialogue_index += 1
                else:
                    psych_index += 1
        except (ValueError, TypeError) as exc:
            fault(str(exc), sid)
    configured = timing.get("utterances", [])
    if not isinstance(configured, list):
        fault("utterances必须是数组")
        configured = []
    used, ids = set(), set()
    for item in configured:
        sid = None
        try:
            if not isinstance(item, dict):
                raise ValueError("发言事件必须是对象")
            identity, kind, source = item.get("id"), item.get("kind"), item.get("source")
            if not identity or identity in ids or not isinstance(source, dict):
                raise ValueError("发言身份重复或缺源文引用")
            ids.add(identity)
            sid = source.get("shot_id")
            candidates = [(key, row) for key, row in inventory.items() if key[0] == sid and key[1] == kind
                          and (kind == "psychological" and source.get("char_range") in (None, row.get("source_range"))
                               or kind == "dialogue" and key[2] == source.get("dialogue_index"))]
            if len(candidates) != 1 or candidates[0][0] in used:
                raise ValueError("发言源文引用不唯一、缺失或已被重复消费")
            key, row = candidates[0]
            if kind == "dialogue" and item.get("speaker") != row["speaker"]:
                raise ValueError("计时发言人物与实际台词不一致")
            profile = _profile(rules, item.get("profile_id") or default["id"])
            if strict and target and profile.get("target_model") not in (None, "unmatched", target):
                raise ValueError("发言配置与当前目标模型不匹配")
            if profile.get("evidence_status") == "measured":
                import hashlib
                actual_voice_hash = hashlib.sha256(str(shot_map[sid].get("音色") or "").encode("utf-8")).hexdigest()
                if profile.get("voice_spec_sha256") != actual_voice_hash:
                    raise ValueError("实测语速配置未绑定当前实际音色规格，不能借用其他声线的实测值")
            selected[profile["id"]] = profile
            used.add(key)
            segments = item.get("segments")
            if not isinstance(segments, list) or not segments:
                raise ValueError("发言缺实际时间片段")
            # Validate every pause BEFORE filtering it into character segments.
            # Otherwise an out-of-range pause could vanish and buy false space.
            estimate_utterance(row["text"], profile, item.get("pauses", []))
            cursor, previous_end = 0, None
            for segment in segments:
                segment_sid = segment.get("shot_id")
                start, end = _number(segment.get("start")), _number(segment.get("end"))
                bounds = segment.get("char_range")
                if segment_sid not in shot_map or start < 0 or end <= start or end > float(shot_map[segment_sid]["时长"]) + EPS:
                    raise ValueError("发言时间片段超出实际镜长")
                if not isinstance(bounds, list) or len(bounds) != 2 or any(type(v) is not int for v in bounds) or not 0 <= bounds[0] < bounds[1] <= len(row["text"]) or bounds[0] != cursor:
                    raise ValueError("发言字符范围遗漏、重叠、错序或非法")
                if not any(p["start"] - EPS <= start < end <= p["end"] + EPS for p in phases[segment_sid]):
                    raise ValueError("跨阶段发言须逐阶段提供连续字符范围，片段不得跨越阶段边界")
                active_phase = next(p for p in phases[segment_sid] if p["start"] - EPS <= start < end <= p["end"] + EPS)
                if cursor == 0:
                    if segment_sid != sid:
                        raise ValueError("发言首片段不得搬离实际源文所在镜头")
                    if kind == "dialogue" and not active_phase["offset"] <= row["offset"] < active_phase["stop"]:
                        raise ValueError("发言首片段不得搬离实际对白所在阶段")
                if kind == "dialogue" and _closed_speaker(active_phase["body"], item.get("speaker") or row["speaker"]):
                    raise ValueError("发声片段与所在阶段的闭口/停止发声事实冲突")
                if kind == "psychological" and segment_sid != sid:
                    reference = PSYCH_REFERENCE.match(str(shot_map[segment_sid].get("心理音轨") or ""))
                    if not reference or reference.group(1) != identity:
                        raise ValueError("跨镜心理音轨须在承接镜明确引用同一音轨身份")
                global_start, global_end = bases[segment_sid] + start, bases[segment_sid] + end
                if previous_end is not None and abs(global_start - previous_end) > EPS:
                    raise ValueError("同一连续发言的时间片段存在间隙、重叠或错序")
                pauses = [dict(p, offset=p["offset"] - bounds[0]) for p in item.get("pauses", []) if bounds[0] <= p.get("offset", -1) < bounds[1] or p.get("offset") == bounds[1] == len(row["text"])]
                estimate = estimate_utterance(row["text"][bounds[0]:bounds[1]], profile, pauses)
                if estimate["seconds"] > end - start + EPS:
                    fault(f"按配置{profile['id']}预算需{estimate['seconds']:.3f}s，片段仅{end-start:.3f}s；这是文本预算不足，非音视频实测结论", segment_sid, code="E-DIALOGUE-003" if kind == "dialogue" else "E-PSYCH-001")
                spans.append({"id": identity, "utterance_id": identity, "shot_id": segment_sid, "speaker": item.get("speaker"), "kind": kind,
                              "start": start, "end": end, "global_start": global_start, "global_end": global_end,
                              "char_range": bounds, "profile_id": profile["id"], "planned_seconds": estimate["seconds"]})
                cursor, previous_end = bounds[1], global_end
            if cursor != len(row["text"]):
                raise ValueError("发言片段未覆盖完整原句及标点")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            fault(str(exc), sid)
    # No explicit timeline: locate each real quote in its own stage. Free time
    # in another stage cannot silently pay for that quote.
    occupied = {}
    for key, row in inventory.items():
        if key in used:
            continue
        sid, kind, ordinal = key
        if default.get("evidence_status") == "measured":
            import hashlib
            voice_hash = hashlib.sha256(str(shot_map[sid].get("音色") or "").encode("utf-8")).hexdigest()
            if default.get("voice_spec_sha256") != voice_hash:
                fault("默认实测语速配置未绑定当前实际音色规格，不能借用其他声线的实测值", sid, code="E-DIALOGUE-004")
        phase = next((p for p in phases[sid] if p["offset"] <= row["offset"] < p["stop"]), phases[sid][0]) if kind == "dialogue" else {"start": 0.0, "end": float(shot_map[sid]["时长"]), "body": ""}
        start = occupied.get((sid, kind, phase["start"]), phase["start"])
        estimate = estimate_utterance(row["text"], default)
        end = start + estimate["seconds"]
        if end > phase["end"] + EPS:
            fault(f"{kind}按配置{default['id']}预算需{estimate['seconds']:.3f}s，所在阶段剩余{max(0,phase['end']-start):.3f}s；须重排实际阶段或依原句拆镜", sid, code="E-DIALOGUE-003" if kind == "dialogue" else "E-PSYCH-001")
        occupied[(sid, kind, phase["start"])] = end
        identity = f"{sid}:{kind}:{ordinal}"
        spans.append({"id": identity, "utterance_id": identity, "shot_id": sid, "speaker": row["speaker"], "kind": kind,
                      "start": start, "end": end, "global_start": bases[sid] + start, "global_end": bases[sid] + end,
                      "char_range": [0, len(row["text"])], "profile_id": default["id"], "planned_seconds": estimate["seconds"],
                      "phase_end": phase["end"], "phase_body": phase["body"]})
        if estimate["mixed_reading_requires_review"]:
            advisories.append({"kind": "reading_review", "shot_id": sid, "message": "数字或英文暂按发音单位近似，需核对实际读法；原句不改写"})
    for sid, shot in shot_map.items():
        reference = PSYCH_REFERENCE.match(str(shot.get("心理音轨") or ""))
        if reference and not any(span["shot_id"] == sid and span["id"] == reference.group(1) and span["kind"] == "psychological" for span in spans):
            fault("心理音轨引用悬空：该音轨没有本镜实际连续片段", sid, code="E-PSYCH-001")
        # A prescribed pause is not an ordinary punctuation pause. Demand its
        # event identity instead of silently erasing it or charging it twice.
        picture = str(shot.get("画面内容") or "")
        quote_ranges = [(m.start(), m.end()) for m in QUOTED.finditer(picture)]
        for declared in re.finditer(r"(?:停顿|沉默|静止|停住)(?:约|至少|整整)?\s*(\d+(?:\.\d+)?)秒", picture):
            if any(a <= declared.start() < b for a, b in quote_ranges):
                continue
            if re.search(r"(?:不|未|禁止|不要)[^；。]{0,4}$", picture[max(0, declared.start()-8):declared.start()]):
                continue
            seconds = float(declared.group(1))
            has_pause = any(
                item.get("source", {}).get("shot_id") == sid
                and any(isinstance(p, dict) and p.get("extra") is True and p.get("seconds") == seconds for p in item.get("pauses", []))
                for item in configured if isinstance(item, dict)
            )
            has_hold = any(
                item.get("kind") == "hold" and item.get("shot_id") == sid
                and declared.group() in str(item.get("basis") or "")
                and isinstance(item.get("start"), (int, float)) and isinstance(item.get("end"), (int, float))
                and item["end"] - item["start"] + EPS >= seconds
                for item in timing.get("events", []) if isinstance(item, dict)
            ) if isinstance(timing.get("events", []), list) else False
            if not has_pause and not has_hold:
                fault("正文明确指定额外停顿时长，须绑定同一时长停顿或hold事件再校验所在阶段；无关事件不能代替，不能遗漏或重复计时", sid)
    for index, left in enumerate(spans):
        for right in spans[index + 1:]:
            if left["id"] != right["id"] and max(left["global_start"], right["global_start"]) < min(left["global_end"], right["global_end"]) - EPS:
                fault("两段发声实际重叠；须安排可听辨的不重叠时段，不能用重叠规避完整发声容量", right["shot_id"])
    from timing_events import validate_events
    event_result = validate_events(project, list(shot_map.values()), spans)
    faults.extend(event_result["faults"])
    advisories.extend(event_result["advisories"])
    for profile in selected.values():
        metrics["profiles"].append({key: profile.get(key) for key in ("id", "units_per_second", "rate_basis", "evidence_status")})
        if profile.get("evidence_status") == "provisional":
            advisories.append({"kind": "provisional_profile", "profile_id": profile["id"], "message": "候选语速未标定；文本预算不代表实际音轨或视听效果通过"})
    metrics["utterances"] = [{key: value for key, value in row.items() if key not in {"phase_body", "speaker"}} for row in spans]
    metrics["timeline_seconds"] = elapsed
    metrics["events"] = event_result.get("events", [])
    return {"faults": faults, "advisories": advisories, "metrics": metrics}


def chapter_budget(dialogues, rules=None, project=None, *, budget=None):
    """Budget ordered protected occurrences with their real speech bindings.

    A complete validated project timeline may share action and speech time.
    Missing coverage retains the conservative serial interpretation of the
    existing chapter categories; invalid evidence never becomes a clean pass.
    """
    rules, project = _rules(rules), project or {}
    faults, advisories, selected = [], [], {}

    def fault(message):
        faults.append({"code": "E-CHAPTER-BUDGET-001", "message": message})

    evidence = project.get("prevention_evidence", {})
    audio = evidence.get("audio_workflow", {}) if isinstance(evidence, dict) else {}
    timing = audio.get("timing", {}) if isinstance(audio, dict) else {}
    if not isinstance(timing, dict):
        fault("章节计时证据必须是对象")
        timing = {}
    try:
        default = _profile(rules, timing.get("default_profile_id"))
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        fault(str(exc))
        default = _profile(rules)
    selected[default["id"]] = default

    shots = project.get("shots", [])
    shots = shots if isinstance(shots, list) else []
    actual, bindings = [], {}
    configured = timing.get("utterances", [])
    if not isinstance(configured, list):
        fault("章节发言证据必须是数组")
        configured = []
    for item in configured:
        if not isinstance(item, dict) or item.get("kind") != "dialogue":
            continue
        source = item.get("source", {})
        if not isinstance(source, dict):
            fault("章节发言缺实际镜头引用")
            continue
        key = (source.get("shot_id"), source.get("dialogue_index"))
        if key in bindings:
            fault("章节发言重复绑定同一次实际对白")
        bindings[key] = item
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        ordinal = 0
        for row in _audible_rows(shot):
            if row["kind"] != "dialogue":
                continue
            actual.append(dict(row, shot_id=shot.get("镜头编号"), index=ordinal,
                               binding=bindings.get((shot.get("镜头编号"), ordinal))))
            ordinal += 1

    if not isinstance(dialogues, list):
        fault("章节保护对白必须为有序数组")
        dialogues = []
    amounts, occurrence_span_ids, cursor, mapped = [], [], 0, True
    for occurrence in dialogues:
        if not isinstance(occurrence, dict):
            fault("章节保护对白条目必须是对象")
            continue
        text, speaker = str(occurrence.get("text") or ""), occurrence.get("speaker")
        # Never index by text: two identical source occurrences must consume
        # two different actual occurrences, in their original order. Adjacent
        # exact same-speaker fragments may rejoin one protected occurrence.
        matches = None
        for begin in range(cursor, len(actual)):
            consumed, found = "", []
            for index in range(begin, len(actual)):
                row = actual[index]
                if row["speaker"] != speaker:
                    break
                consumed += row["text"]
                if not text.startswith(consumed):
                    break
                found.append(index)
                if consumed == text:
                    matches = found
                    break
            if matches is not None:
                break
        if matches is None:
            mapped = False
            amounts.append(estimate_utterance(text, default)["seconds"])
            occurrence_span_ids.append([])
            continue
        cursor = matches[-1] + 1
        amount, span_ids = 0.0, []
        for index in matches:
            row, profile, pauses = actual[index], default, []
            item = row.get("binding")
            span_ids.append(item["id"] if isinstance(item, dict) and item.get("id")
                            else f"{row['shot_id']}:dialogue:{row['index']}")
            try:
                if item is not None:
                    profile = _profile(rules, item.get("profile_id") or default["id"])
                    pauses = item.get("pauses", [])
                selected[profile["id"]] = profile
                amount += estimate_utterance(row["text"], profile, pauses)["seconds"]
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                fault(str(exc))
                amount += estimate_utterance(row["text"], default)["seconds"]
        amounts.append(amount)
        occurrence_span_ids.append(span_ids)
    seconds = sum(amounts)
    categories = {"dialogue": seconds, "action": 0.0, "transition": 0.0}
    if isinstance(budget, dict):
        for kind in categories:
            try:
                value = _number(budget.get(kind + "_seconds", categories[kind]))
                if value < 0:
                    raise ValueError("章节时间类别不得为负数")
                categories[kind] = max(seconds, value) if kind == "dialogue" else value
            except (ValueError, TypeError) as exc:
                fault(str(exc))
    total, coverage = sum(categories.values()), "serial_fallback"

    # Reuse the actual validator, including phase, character, pause, overlap
    # and action dependency checks. Do not rebuild a second speech scheduler.
    checked = validate_timing(project, shots, rules) if shots else None
    if checked is not None:
        faults.extend(checked["faults"])
        advisories.extend(checked["advisories"])
        speech = checked["metrics"].get("utterances", [])
        events = checked["metrics"].get("events", [])
        speech_seconds = sum(row["end"] - row["start"] for row in speech)
        action_seconds = sum(row["end"] - row["start"] for row in events)
        # A project event list currently has no transition kind. Nonzero
        # transition categories therefore need the explicit chapter timeline
        # below; they cannot disappear into a global maximum by assumption.
        complete = (mapped and bool(events) and not faults
                    and speech_seconds + EPS >= categories["dialogue"]
                    and action_seconds + EPS >= categories["action"]
                    and categories["transition"] == 0)
        if complete:
            spans = speech + events
            total = max((row["global_end"] for row in spans), default=0.0)
            coverage = "validated_project_timeline"

    raw_timeline = budget.get("timeline_events") if isinstance(budget, dict) else None
    if raw_timeline is not None:
        total, coverage = sum(categories.values()), "serial_fallback"
        # Chapter-only planning may supply a complete auditable schedule:
        # dialogue entries reference the ordered protected source_index;
        # action/transition entries require an explicit factual basis.
        valid, entries, source_indexes = True, {}, set()
        category_durations = {key: 0.0 for key in categories}
        if not isinstance(raw_timeline, list) or not raw_timeline:
            fault("章节timeline_events必须是非空有序事件数组")
            valid = False
        for raw in raw_timeline if isinstance(raw_timeline, list) else []:
            try:
                if not isinstance(raw, dict):
                    raise ValueError("章节时间事件必须为对象")
                identity, kind = raw.get("id"), raw.get("kind")
                if not isinstance(identity, str) or not identity or identity in entries or kind not in categories:
                    raise ValueError("章节时间事件身份重复/缺失或类别非法")
                start, end = _number(raw.get("start")), _number(raw.get("end"))
                if not 0 <= start < end:
                    raise ValueError("章节时间事件须有非负、正时长起止")
                after = raw.get("after", [])
                if not isinstance(after, list) or any(not isinstance(value, str) for value in after) or len(set(after)) != len(after):
                    raise ValueError("章节时间事件前置关系非法")
                if kind == "dialogue":
                    source_index = raw.get("source_index")
                    if type(source_index) is not int or not 0 <= source_index < len(amounts) or source_index in source_indexes:
                        raise ValueError("章节发言事件必须唯一引用每次保护对白的source_index")
                    if raw.get("speaker") != dialogues[source_index].get("speaker") or end - start + EPS < amounts[source_index]:
                        raise ValueError("章节发言人物或实际发言窗口与保护对白预算不一致")
                    if checked is not None:
                        matching = [span for span in checked["metrics"].get("utterances", [])
                                    if span["id"] in occurrence_span_ids[source_index]]
                        if (not matching
                                or abs(start - min(span["global_start"] for span in matching)) > EPS
                                or abs(end - max(span["global_end"] for span in matching)) > EPS):
                            raise ValueError("章节发言时间轴与已核对canonical发言起止不一致")
                    source_indexes.add(source_index)
                elif not isinstance(raw.get("basis"), str) or not raw["basis"].strip():
                    raise ValueError("章节动作和转场须有具体依据")
                elif checked is not None:
                    matching = [event for event in checked["metrics"].get("events", [])
                                if event["id"] == identity]
                    if (len(matching) != 1 or matching[0].get("basis") != raw["basis"]
                            or abs(start - matching[0]["global_start"]) > EPS
                            or abs(end - matching[0]["global_end"]) > EPS):
                        raise ValueError("章节动作/转场未绑定已核对canonical事件，不得另造并行时间轴")
                elif kind == "action" and (not isinstance(raw.get("actor"), str) or not raw["actor"].strip()):
                    raise ValueError("章节动作事件必须明确实际执行者actor")
                entries[identity] = dict(raw, start=start, end=end, after=after)
                category_durations[kind] += end - start
            except (ValueError, TypeError, KeyError) as exc:
                fault(str(exc))
                valid = False
        for row in entries.values():
            for dependency in row["after"]:
                if dependency not in entries or entries[dependency]["end"] > row["start"] + EPS:
                    fault("章节时间事件在前置结束前开始或引用了未知前置")
                    valid = False
        spoken = sorted((row for row in entries.values() if row["kind"] == "dialogue"), key=lambda row: row["start"])
        if any(left["end"] > right["start"] + EPS for left, right in zip(spoken, spoken[1:])):
            fault("章节发言窗口重叠，不能用重叠规避完整对白容量")
            valid = False
        from timing_events import MOUTH_ACTIONS, _positive, _closed_mouth_action
        for action in entries.values():
            # Unlike the legacy drinking basis, a closed-mouth interval must be
            # explicitly declared as an action. "先闭唇后说话" in a longer basis
            # is not a declaration that the entire action interval is silent.
            closed_mouth = _closed_mouth_action(str(action.get("action") or ""))
            if action["kind"] != "action" or not (closed_mouth or _positive(MOUTH_ACTIONS, action["basis"])):
                continue
            if any(action.get("actor") == line.get("speaker")
                   and max(action["start"], line["start"]) < min(action["end"], line["end"]) - EPS
                   for line in spoken):
                state = "闭唇" if closed_mouth else "喝水/吞咽"
                fault(f"章节时间轴将同一人物的{state}与发声重叠")
                valid = False
        if [row["source_index"] for row in spoken] != list(range(len(amounts))):
            fault("章节时间轴未按原序完整覆盖每次保护对白")
            valid = False
        complete = source_indexes == set(range(len(amounts))) and all(
            category_durations[key] + EPS >= value for key, value in categories.items()
        )
        if valid and complete and not faults:
            total = max((row["end"] for row in entries.values()), default=0.0)
            coverage = "validated_chapter_timeline"
        elif not complete:
            advisories.append({"kind": "incomplete_chapter_timeline", "message": "事件时间轴未覆盖全部类别预算，保留串行预算"})
    for profile in selected.values():
        if profile.get("evidence_status") == "provisional":
            advisory = {"kind": "provisional_profile", "profile_id": profile["id"]}
            if not any(row.get("kind") == advisory["kind"] and row.get("profile_id") == profile["id"] for row in advisories):
                advisories.append(advisory)
    return {"required_dialogue_seconds": seconds, "required_total_seconds": total,
            "faults": faults, "advisories": advisories, "profile_ids": list(selected),
            "timeline_coverage": coverage}
