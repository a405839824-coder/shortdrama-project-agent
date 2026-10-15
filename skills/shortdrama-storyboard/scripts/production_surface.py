"""Source-bound surface effects and explicitly disembodied scene speech.

This is a finite optical state model, not fluid/light transport simulation or
proof that a declared effect is entailed by the source. Source review remains
mandatory. No actor, mouth, knowledge, or sound effect is inferred.
"""
from __future__ import annotations

import copy

KIND = 'surface_effect'
FIELDS = {'to'}


def validate_surface(value, path):
    from production_decisions import _need, _obj, _num
    _obj(value, {'pattern', 'glow', 'mist', 'extent_m'}, path)
    _need(value['pattern'] in ('still', 'ripple'), path, 'unsupported surface pattern')
    _need(value['glow'] in ('none', 'faint', 'flicker'), path, 'unsupported surface glow')
    _need(value['mist'] in ('none', 'local_white'), path, 'unsupported local mist')
    _need(0 < _num(value['extent_m'], path + '.extent_m') <= 1, path,
          'surface effects must stay within an explicit radius of at most one metre')


def prepare(world, action, path, *, bridge):
    from production_decisions import _need, _hash
    prop = world['props'].get(action['actor'])
    _need(not bridge and action['source_use_id'] is not None, path,
          'surface effect requires a frozen source step, not omitted staging')
    _need(prop is not None and 'surface' in prop, path, 'surface actor must be an explicitly registered optical prop')
    _need(prop['physical']['integrity'] == 'intact', path, 'cannot activate a broken optical surface')
    validate_surface(action['to'], path + '.to')
    _need(action['to']['extent_m'] == prop['surface']['extent_m'], path,
          'effect cannot expand outside the registered prop-local envelope')
    _need(action['to'] != prop['surface'], path, 'surface effect must change its actual current state')
    return {'before_surface': copy.deepcopy(prop['surface']), 'before_world_sha256': _hash(world)}


def apply(world, action, info, tick):
    # At the exact start frame the previous state is still present. During the
    # declared effect interval the new bounded pattern is present; completion
    # does not extinguish it. A later source step must explicitly clear it.
    world['props'][action['actor']]['surface'] = copy.deepcopy(
        info['before_surface'] if tick == action['start'] else action['to'])


def surface_text(name, value, *, static=False):
    pattern = {'still': '镜面平整', 'ripple': '镜面具有涟漪纹理'}[value['pattern']]
    glow = {'none': '无自发光', 'faint': '镜面有淡淡微光',
            'flicker': '镜面有明暗不均的光纹' if static else '镜面光影明灭'}[value['glow']]
    mist = ('镜前有白雾' if static else '镜前白雾缓缓缭绕') if value['mist'] == 'local_white' else '镜前无白雾'
    return f'{name}{pattern}，{glow}；{mist}，效果范围限于镜面周围{value["extent_m"]:g}米内。'


def sentence(action, info, name):
    return '变化', f'{action["actor"]}镜面由原有状态变化：' + surface_text(name, action['to'])


def validate_emitter(world, action, scene_id, path):
    from production_decisions import _need, _obj, _anchor
    emitter = action.get('emitter')
    _obj(emitter, {'prop', 'mode'}, path + '.emitter')
    _need(emitter['mode'] == 'disembodied', path, 'only explicit disembodied scene emission is supported')
    _need(action['actor'] not in world['actors'], path,
          'a bodily speaker cannot be relabelled as disembodied to bypass mouth/gaze checks')
    _need(isinstance(emitter['prop'], str), path, 'emitter prop requires a registered identity')
    prop = world['props'].get(emitter['prop'])
    _need(prop is not None, path, 'unknown scene voice emitter prop')
    _need(_anchor(world, prop)[0] == scene_id, path, 'voice emitter belongs to another physical scene')
    _need(prop['physical']['integrity'] == 'intact', path, 'broken prop emission requires a separately supported source mechanism')
    return copy.deepcopy(emitter)


def speaker_scene(world, action):
    from production_decisions import _anchor
    if 'emitter' in action:
        return _anchor(world, world['props'][action['emitter']['prop']])[0]
    return world['actors'][action['actor']]['scene_id']


def local_effects(world, scene_id, prop_ids=None):
    from production_decisions import _anchor
    return {name: copy.deepcopy(prop['surface']) for name, prop in world['props'].items()
            if 'surface' in prop and (prop_ids is None or name in prop_ids)
            and _anchor(world, prop)[0] == scene_id}


def lighting(blocks, effects, display_names, *, allow_mist=False):
    """Restrict the ordinary clear-air contract around actual local effects."""
    if not effects and not allow_mist:
        return blocks
    blocks = copy.deepcopy(blocks)
    mist = allow_mist or any(v['mist'] == 'local_white' for v in effects.values())
    if mist:
        blocks['lighting_fields']['光影'] = blocks['lighting_fields']['光影'].replace(
            '空气清澈，背景结构清晰可辨，不泛白、不雾化。',
            '镜面局部效果范围之外空气清澈，背景结构清晰可辨。')
        blocks['generation_limits_append'] = '局部白雾仅限已登记镜面范围；禁止全景泛白、全室雾化或额外烟雾。'
    for name, state in effects.items():
        if state['glow'] != 'none':
            blocks['lighting_fields']['光影'] += f'{display_names.get(name, name)}表面可见局部微光，不改变本镜主光方向，不自动给人物添加轮廓光。'
    return blocks


def environment(initial_world, final_world, scene_id, actions, display_names, visible_props=None):
    initial = local_effects(initial_world, scene_id, visible_props)
    final = local_effects(final_world, scene_id, visible_props)
    lines = []
    for name, value in initial.items():
        if value['pattern'] == 'ripple' or value['glow'] == 'flicker' or value['mist'] != 'none':
            lines.append('本镜开始时' + surface_text(display_names.get(name, name), value))
    events = []
    for action in actions:
        if action['kind'] == KIND:
            lines.append(f'{action["start"]/10:g}—{action["end"]/10:g}秒：' +
                         surface_text(display_names.get(action['actor'], action['actor']), action['to']))
            events.append({'action_id': action['id'], 'source_use_id': action['source_use_id'],
                           'prop': action['actor'], 'start_tick': action['start'], 'end_tick': action['end'],
                           'to': copy.deepcopy(action['to'])})
    return ''.join(lines), {'initial': initial, 'final': final, 'events': events}
