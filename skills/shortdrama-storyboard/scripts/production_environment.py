"""Construct environmental fields from the same simulated production world.

These are supported rendering choices, not a certificate of source semantics.
Free environmental prose cannot add an unmodelled effect or sound. Existing
typed/legacy inputs keep their separate compatibility route.
"""
from __future__ import annotations

import copy
import re

METADATA_FIELDS = {"功能类型", "参考图锚定", "原文依据类型", "原文支撑句"}
ENVIRONMENT_FIELDS = {"环境动态", "环境音效"}
SOUND_KINDS = {
    "prop_contact": {"move_prop", "transfer", "pick_up", "put_down"},
    "writing": {"write"},
    "footsteps": {"move_actor", "cross_passage"},
    "drop_impact": {"drop_prop"},
    "slap": {"contact_actor"},
    "handclaps": {"clap"},
    "kowtow_contact": {"kowtow"},
    "cloth": {"wipe", "wear_prop", "remove_worn", "dress_actor", "undress_actor"},
    "sigh": {"breath"},
    "scene_echo": {"speak"},
    "mirror_break": {"shatter_surface"},
}


class EnvironmentDecisionError(ValueError):
    def __init__(self, path, reason):
        self.path, self.reason = path, reason
        super().__init__(f"{path}: {reason}")


def _need(ok, path, reason):
    if not ok:
        raise EnvironmentDecisionError(path, reason)


def opening_states(value, path='scene.openings'):
    """One opening-state registry; accept only a finite old prose grammar.

    General prose cannot change the door/window snapshot behind the light
    renderer. A window-light entry must be the same actual declared state.
    """
    openings = {}
    if value == "无门窗。":
        return openings  # Accept the normal form emitted for an empty registry.
    if isinstance(value, dict):
        for name, state in value.items():
            _need(isinstance(name, str) and bool(re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9_-]{1,64}", name))
                  and state in ("open", "closed"), path, "opening registry requires plain names and open/closed states")
            openings[name] = state
    elif isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[。；;]", value) if part.strip()]
        _need(bool(parts), path, "requires explicit opening states")
        for part in parts:
            match = re.fullmatch(r"([\u3400-\u9fffA-Za-z0-9_-]{1,64}?)(关闭|紧闭|开启|敞开)", part)
            _need(match is not None, path, "free opening prose unsupported; use {opening_name: open/closed}")
            name, state = match.groups()
            _need(name not in openings, path, "duplicate opening state")
            openings[name] = "closed" if state in ("关闭", "紧闭") else "open"
    else:
        _need(False, path, "requires an opening registry or exact legacy opening-state clauses")
    return openings


def normalize_openings(value, light, path):
    openings=opening_states(value,path)
    if light["kind"] in {"paper_window", "glass_window"}:
        _need(openings.get(light["name"]) == light["state"], path,
              light["kind"].replace("_", "-") + " opening state must match the same shared light state")
    return "".join(f"{name}{'开启' if state == 'open' else '关闭'}。" for name, state in sorted(openings.items())) or "无门窗。"


def environment_editorial(*, metadata, sound_cues):
    """Build the supported shared-world form; [] explicitly chooses no cues.

    No previous free prose is parsed, discarded, or silently called repaired.
    Cues still require completed matching actions when the world is lowered.
    """
    _need(isinstance(metadata, dict) and set(metadata) == METADATA_FIELDS,
          "metadata", "pass only the four descriptive metadata fields; do not discard old environment prose")
    value = {**copy.deepcopy(metadata), "环境动态": {"mode": "shared_world"},
             "环境音效": {"mode": "action_bound", "cues": copy.deepcopy(sound_cues)}}
    validate_editorial(value)
    return value


def validate_editorial(value, path="editorial"):
    _need(isinstance(value, dict) and set(value) == METADATA_FIELDS | ENVIRONMENT_FIELDS,
          path, "requires four descriptive metadata fields and two structured environment decisions")
    for key in METADATA_FIELDS:
        _need(isinstance(value[key], str) and bool(value[key].strip()), path + "." + key,
              "requires explicit metadata text")
    _need(value["环境动态"] == {"mode": "shared_world"}, path + ".环境动态",
          "free environmental prose cannot add world effects; use {mode: shared_world}; "
          "unsupported atmosphere or motion requires an explicit world/action extension")
    sound = value["环境音效"]
    _need(isinstance(sound, dict), path + ".环境音效",
          "free sound prose cannot add an event; choose none or action_bound cues")
    if sound == {"mode": "none"}:
        return
    _need(set(sound) == {"mode", "cues"} and sound["mode"] == "action_bound"
          and isinstance(sound["cues"], list), path + ".环境音效",
          "requires {mode: none} or {mode: action_bound, cues: [...]} explicitly")
    seen = set()
    for index, cue in enumerate(sound["cues"]):
        cp = f"{path}.环境音效.cues[{index}]"
        _need(isinstance(cue, dict) and set(cue) == {"action_id", "sound", "volume"}, cp,
              "requires exactly action_id, sound, volume")
        _need(isinstance(cue["action_id"], str) and bool(cue["action_id"])
              and cue["action_id"] not in seen, cp, "requires a unique actual action ID")
        seen.add(cue["action_id"])
        _need(isinstance(cue["sound"], str) and cue["sound"] in SOUND_KINDS, cp,
              "unsupported sound effect")
        _need(cue["volume"] in ("soft", "normal"), cp, "volume must be soft or normal")


def render_environment(editorial, *, initial_world, final_world, scene_id, actions, action_start_worlds=None, display_names=None, visible_props=None):
    """Render only after simulation; derive cue actor, object and clock once.

    This compiler's light model declares clear air and a fixed
    light source. Prop/actor movements remain in the shared action timeline;
    this environmental field never adds a competing motion narrative.
    """
    validate_editorial(editorial)
    light = initial_world["scenes"][scene_id]["light"]
    states = {"closed": "关闭", "open": "开启", "lit": "点燃", "unlit": "未点燃",
               "extinguished": "熄灭", "present": "已建立"}
    _need(light["state"] in states, "environment.light.state", "unsupported shared light state")
    # This grammar models no independent wind/rain/flame/background action.
    # Air appearance and the registered opening/light state have other owners.
    from production_surface import environment as surface_environment
    dynamics, surfaces = surface_environment(initial_world, final_world, scene_id, actions, display_names or {}, visible_props)
    dynamics = dynamics or "无环境动态。"
    sound = editorial["环境音效"]
    cues = sound.get("cues", [])
    lookup = {action["id"]: action for action in actions}
    expected_echo = {a['id'] for a in actions if a['kind'] == 'speak' and a.get('emitter', {}).get('effect') == 'short_echo'}
    _need(expected_echo == {c['action_id'] for c in cues if c['sound'] == 'scene_echo'},
          'editorial.环境音效', 'each declared short echo needs exactly its actual scene_echo cue')
    lines, bindings = [], []
    for index, cue in enumerate(cues):
        cp = f"editorial.环境音效.cues[{index}]"
        action = lookup.get(cue["action_id"])
        _need(action is not None and action["kind"] in SOUND_KINDS[cue["sound"]], cp,
              "sound must match an actual supported action in this shot")
        _need(final_world["completed"].count(action["id"]) == 1, cp,
              "sound requires that actual action to complete once")
        actor = action["actor"]
        action_world = (action_start_worlds or {}).get(action['id'], initial_world)
        from production_surface import speaker_scene
        _need((speaker_scene(action_world, action) if action['kind'] == 'speak' else action_world["actors"][actor]["scene_id"]) == scene_id, cp,
              "sound action belongs to another scene")
        prop_name = (display_names or {}).get(action.get('prop'), action.get('prop'))
        contact_ticks = None
        if cue['sound']=='mirror_break':
            _need(final_world['props'][action['prop']]['physical']['integrity']=='broken',cp,'mirror break sound requires actual broken result')
            event=f'{prop_name}碎裂与镜片落地的声音'
            contact_ticks=[(action['start']+action['end'])/2,action['end']]
        elif cue['sound']=='drop_impact':
            physical=final_world['props'][action['prop']]['physical']
            event=f"{prop_name}{'落下碎裂' if physical['integrity']=='broken' else '落下接触落点'}的声音"
            contact_ticks=[action['end']]
        elif cue['sound']=='slap':
            _need(action['mode']=='slap',cp,'slap sound cannot bind a grasp, release or support')
            event=f"{actor}手掌击中{action['target_actor']}面颊的声音"
            contact_ticks=[action['end']]
        elif cue['sound'] in {'handclaps','kowtow_contact'}:
            event=f"{actor}{'双掌拍合' if cue['sound']=='handclaps' else '额头触地'}的声音"
            contact_ticks=[action['start']+(action['end']-action['start'])*(i+.5)/action['count'] for i in range(action['count'])]
            _need(all(abs(t-round(t))<1e-8 for t in contact_ticks),cp,'repeat contacts must align to actual simulated integer ticks')
            contact_ticks=[round(t) for t in contact_ticks]
        elif cue['sound']=='cloth':
            event=f"{actor}使用{prop_name}时的衣料摩擦声"
        elif cue['sound']=='sigh':
            _need(action['phase']=='sigh', cp, 'sigh sound requires an actual sigh phase')
            event=f"{actor}轻轻叹气的声音"
        elif cue['sound']=='scene_echo':
            _need(action.get('emitter', {}).get('effect') == 'short_echo', cp, 'scene echo requires an explicit prop-bound short echo')
            event=f"{actor}原声的细微短回声"
        elif cue["sound"] == "prop_contact":
            event = f"{actor}移动{prop_name}时的物件接触声"
        elif cue["sound"] == "writing":
            event = f"{actor}书写时笔尖接触纸面的声音"
        else:
            _need(action_world["actors"][actor]["posture"] == "站立", cp,
                  "footsteps require an explicitly standing actor; seated/lying locomotion is not modelled")
            event = f"{actor}行走时的脚步声"
        volume = "轻声" if cue["volume"] == "soft" else "正常音量"
        text = (''.join(f'{tick/10:.1f}秒：{event}，{volume}。' for tick in contact_ticks) if contact_ticks is not None else
                f"{action['start'] / 10:.1f}—{action['end'] / 10:.1f}秒：{event}，{volume}。")
        lines.append(text)
        if cue['sound'] == 'scene_echo':
            lines.append('回声不添加字词，不遮盖原词，并在本句发言窗口结束时收住。')
        bindings.append({"action_id": action["id"], "kind": action["kind"], "actor": actor,
                         "start_tick": action["start"], "end_tick": action["end"],
                         "sound": cue["sound"], "volume": cue["volume"], "rendered_text": text,
                         **({'contact_ticks':contact_ticks} if contact_ticks is not None else {})})
    fields = {"环境动态": dynamics, "环境音效": "".join(lines) or "无额外环境音效。"}
    return fields, {"contract": "production-environment-v1", "physical_scene": scene_id,
                    "air": "localized_surface_effect" if any(v['mist'] != 'none' for phase in ('initial', 'final') for v in surfaces[phase].values()) else "clear",
                    "surface_binding": surfaces, "light": copy.deepcopy(light), "sound_bindings": bindings,
                    "fields": copy.deepcopy(fields), "semantic_status": "pending_independent_review"}
