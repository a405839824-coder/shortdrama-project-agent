"""Finite source-bindable actions, with explicit landmark effects.

This extends the declared landmark model, not body mechanics or source entailment.
No user supplied prose, arbitrary patch, or externally authored endpoint is executed.
The existing source plan, visibility, timing, review and release gates still apply.
"""
from __future__ import annotations

import copy
import math

FIELDS = {
    'posture': {'to', 'point', 'landmarks'},
    'fall': {'point','landmarks'},
    'hand_move': {'hand', 'to'},
    'eye_state': {'to'},
    'hand_shape': {'hand', 'to'},
    'expression': {'to'},
    'cross_passage': {'passage'},
    'pick_up': {'prop', 'hand'},
    'put_down': {'prop', 'hand', 'to'},
    'drop_prop': {'prop', 'hand', 'to', 'contact', 'breaks'},
    'wear_prop': {'prop', 'hand', 'part'},
    'remove_worn': {'prop', 'hand'},
    'dress_actor': {'prop','hand','target_actor','part'},
    'undress_actor': {'prop','hand','target_actor'},
    'release_binding': {'prop','hand','target_actor'},
    'wipe': {'prop', 'hand', 'part'},
    'contact_actor': {'hand', 'target_actor', 'target_part', 'mode'},
    'clap': {'count'},
    'kowtow': {'count'},
    'head_gesture': {'direction', 'count'},
    'set_opening': {'opening','to','hand'},
    'scene_light': {'scene_id', 'light'},
}
CONTINUOUS = set(FIELDS)
POSTURES = {'站立': (1.0, 2.2), '坐姿': (.6, 1.6), '蹲姿': (.3, 1.2),
            '跪姿': (.5, 1.4), '跪伏': (.05, .5), '躬身': (.5, 1.8), '躺卧': (.05, .8)}
EXPRESSIONS = {'平静', '微笑', '不悦', '压怒', '疼痛', '震惊', '得意', '疑惑', '委屈', '畏惧', '恭敬', '悲伤'}
PARTS = {'face', 'left_wrist', 'right_wrist', 'left_foot', 'right_foot', 'head', 'torso', 'left_hand', 'right_hand'}
PART_TEXT = {'face': '面颊', 'left_wrist': '左手腕', 'right_wrist': '右手腕', 'left_foot': '左脚背',
             'right_foot': '右脚背', 'head': '头部', 'torso': '外衣', 'left_hand': '左手', 'right_hand': '右手'}
SOURCE_REQUIRED = {'drop_prop', 'contact_actor', 'clap', 'kowtow', 'wipe', 'wear_prop', 'remove_worn', 'set_opening','fall','dress_actor','undress_actor','release_binding'}
BRIDGE_KINDS = {'move_actor', 'turn', 'look', 'posture', 'hand_move', 'eye_state', 'hand_shape',
                'expression', 'cross_passage', 'scene_light'}


def _p():
    import production_decisions
    return production_decisions


def body_point(actor, part):
    p = _p()
    if part in {'left_wrist', 'right_wrist', 'left_hand', 'right_hand'}:
        return p._hand(actor, part.split('_')[0])
    if part in {'head', 'face'}:
        return p._add(p._eye(actor), [0, 0, 0 if part == 'head' else -.12])
    if part == 'torso':
        return list(actor['reach_origin_xyz'])
    sign = -1 if part == 'left_foot' else 1
    angle = math.radians(actor['body_yaw'])
    return p._add(actor['position_xyz'], [sign * .12 * math.cos(angle), -sign * .12 * math.sin(angle), .05])


def defaults(world):
    for actor in world['actors'].values():
        actor.setdefault('action_state', {'contacts': {}, 'wet_parts': [], 'clean_parts': [],
                                        'hand_shapes': {'left': 'open', 'right': 'open'},
                                        'expression': '平静', 'claps': 0, 'kowtows': 0})
    for prop in world['props'].values():
        prop.setdefault('physical', {'material': 'unspecified', 'contents': 'none', 'integrity': 'intact'})


def validate_world(world):
    p = _p()
    for sid, scene in world['scenes'].items():
        seen = set()
        p._need(isinstance(scene.get('passages', []), list), 'scene.passages', 'requires passage list')
        for passage in scene.get('passages', []):
            p._obj(passage, {'id', 'exit', 'entry', 'opening'}, 'scene.passages')
            for value in passage.values(): p._name(value, 'scene.passages')
            p._need(passage['id'] not in seen, 'scene.passages', 'duplicate passage')
            seen.add(passage['id'])
            a, b = world['points'].get(passage['exit']), world['points'].get(passage['entry'])
            p._need(a and b and a['scene_id'] == sid and b['scene_id'] != sid,
                    'scene.passages', 'passage must join this scene to a different known scene')
            p._need(p._distance(a['position_xyz'], b['position_xyz']) <= .5,
                    'scene.passages', 'passage endpoints must share a physical doorway; no remote teleport')
    for actor in world['actors'].values():
        s = actor['action_state']
        p._obj(s, {'contacts', 'wet_parts', 'clean_parts', 'hand_shapes', 'expression', 'claps', 'kowtows'}, 'actor.action_state')
        p._need(s['contacts'] == {}, 'actor.action_state.contacts', 'initial contact needs an executed source action')
        for name in ('wet_parts', 'clean_parts'):
            p._need(isinstance(s[name], list) and all(isinstance(x,str) for x in s[name]) and len(s[name]) == len(set(s[name])) and set(s[name]) <= PARTS,
                    'actor.action_state.' + name, 'requires unique supported body parts')
        p._obj(s['hand_shapes'], {'left', 'right'}, 'actor.action_state.hand_shapes')
        p._need(all(isinstance(v,str) and v in {'open','fist'} for v in s['hand_shapes'].values()), 'actor.action_state', 'unsupported hand shape')
        p._need(isinstance(s['expression'],str) and s['expression'] in EXPRESSIONS, 'actor.action_state', 'unsupported expression')
        for name in ('claps', 'kowtows'):
            p._need(type(s[name]) is int and s[name] == 0, 'actor.action_state', 'event counts must start at zero')
    for prop in world['props'].values():
        s = prop['physical']
        p._obj(s, {'material', 'contents', 'integrity'}, 'prop.physical')
        p._need(all(isinstance(v,str) for v in s.values()), 'prop.physical', 'requires named physical states')
        p._need(s['material'] in {'unspecified', 'ceramic', 'cloth', 'metal', 'wood', 'paper'}, 'prop.physical', 'unsupported material')
        p._need(s['contents'] in {'none', 'water', 'hot_water'} and s['integrity'] in {'intact', 'broken'},
                'prop.physical', 'unsupported physical state')
        p._need(s['integrity'] == 'intact' or s['contents'] == 'none', 'prop.physical', 'broken vessel cannot retain water')


def channels(action):
    a, k = action['actor'], action['kind']
    # Channels are calculated before preparation. Validate keys needed below
    # so malformed input fails closed as a domain error, never a raw TypeError.
    for key in FIELDS[k] - {'landmarks','light','contact','breaks','count'}:
        _p()._need(isinstance(action[key],str), 'action.'+key, 'requires named action parameter')
    if k == 'scene_light': return {('scene', action['scene_id'])}
    if k == 'set_opening': return {('scene_openings','all'),(a,action['hand'])}
    if k in {'posture', 'fall', 'cross_passage', 'kowtow'}:
        return {(a, x) for x in ('position', 'body', 'head', 'left', 'right', 'gaze')}
    if k == 'eye_state': return {(a, 'eyelid_closure'), (a, 'gaze')}
    if k == 'expression': return {(a, 'expression')}
    if k == 'head_gesture': return {(a, 'head'), (a, 'gaze')}
    if k == 'clap': return {(a, 'left'), (a, 'right')}
    result = {(a, action['hand'])}
    if 'prop' in action: result.add(('prop', action['prop']))
    if k in {'dress_actor','undress_actor','release_binding'}:
        result.update((action['target_actor'],part) for part in ('position','body','head'))
        if k=='release_binding':result.update((action['target_actor'],part) for part in ('left','right'))
    contact = {'actor':action['target_actor'],'part':action['target_part']} if k=='contact_actor' else action.get('contact')
    if contact is not None:
        _p()._obj(contact, {'actor','part'}, 'action.contact')
        _p()._need(all(isinstance(v,str) for v in contact.values()), 'action.contact', 'requires named actor and part')
        target, part = contact['actor'],contact['part']
        result.update({(target,'position'),(target,'body')})
        if part in {'face','head'}: result.add((target,'head'))
        if part.endswith(('wrist','hand')): result.add((target,part.split('_')[0]))
    return result


def _contacted(world, actor, hand=None):
    return [(owner, h, c) for owner, row in world['actors'].items()
            for h, c in row['action_state']['contacts'].items()
            if c['actor'] == actor and (hand is None or c['part'] in {hand + '_wrist', hand + '_hand'})]


def prepare(world, action, info, path, *, bridge=False):
    p = _p(); a = world['actors'][action['actor']]; k = action['kind']
    info['before_all'] = copy.deepcopy(world)
    duration = (action['end'] - action['start']) / 10
    p._need(duration >= .2, path, 'action needs at least 0.2 seconds in the declared landmark model')
    if k in SOURCE_REQUIRED:
        p._need(action['source_use_id'] is not None, path, 'this plot action requires a frozen physical source use')
    if k in {'posture', 'fall', 'cross_passage'}:
        p._need(not a['action_state']['contacts'] and not _contacted(world, action['actor']), path, 'release actual body contact before relocating')
    if k in {'hand_move', 'hand_shape', 'pick_up', 'put_down', 'drop_prop', 'wear_prop', 'remove_worn', 'wipe', 'set_opening','dress_actor','undress_actor','release_binding'}:
        p._need(action['hand'] in {'left', 'right'}, path, 'unknown hand')
        p._need(action['hand'] not in a['action_state']['contacts'] and not _contacted(world, action['actor'], action['hand']),
                path, 'hand remains in an actual grasp/support contact')
    if k in {'posture','fall'}:
        to='躺卧' if k=='fall' else action['to']
        p._need(to in POSTURES and to != a['posture'], path, 'requires a different supported posture')
        point = world['points'].get(action['point'])
        p._need(point and point['scene_id'] == a['scene_id'], path, 'posture target must stay in the same physical scene')
        p._need(p._distance(a['position_xyz'], point['position_xyz']) <= 1.2, path, 'posture transition cannot replace a walk')
        p._need(not p._blocked(world, a['scene_id'], a['position_xyz'], point['position_xyz']), path, 'barrier blocks posture transition')
        p._need(duration >= .6, path, 'posture transition too short')
        lm = p._obj(action['landmarks'], {'eye_height', 'reach_origin_offset_xyz', 'hands'}, path + '.landmarks')
        height = p._num(lm['eye_height'], path)
        p._need(POSTURES[to][0] <= height <= POSTURES[to][1], path, 'eye landmark incompatible with declared posture')
        offset=p._xyz(lm['reach_origin_offset_xyz'], path)
        p._need(math.hypot(*offset[:2]) <= .8 and abs(offset[2]-height) <= .7, path, 'reach origin incompatible with pose landmarks')
        p._obj(lm['hands'], {'left', 'right'}, path)
        for xyz in lm['hands'].values():
            p._xyz(xyz, path)
            p._need(p._distance(xyz,offset) <= a['reach_m'] + .2, path, 'pose hand exceeds declared reach')
        info['destination'] = list(point['position_xyz'])
    elif k == 'hand_move':
        scene, xyz = p._target(world, {'kind': 'point', 'id': action['to']}, path)
        p._reach(world, action['actor'], scene, xyz, path)
        p._need(not p._blocked(world, scene, p._hand(a, action['hand']), xyz), path, 'barrier blocks hand path')
        p._need(p._distance(p._hand(a, action['hand']), xyz) > p.EPS, path, 'hand_move has no change')
        info['destination'] = xyz
    elif k == 'eye_state':
        p._need(action['to'] in {'natural_open', 'closed'} and action['to'] != a['eye_state'], path, 'requires changed actual eye state')
        p._need(action['to'] != 'closed' or a['gaze'] is None, path, 'clear visual gaze before closing eyes')
    elif k == 'hand_shape':
        p._need(action['to'] in {'open', 'fist'} and action['to'] != a['action_state']['hand_shapes'][action['hand']], path, 'requires a changed hand shape')
        p._free_hand(world, action['actor'], action['hand'], path)
    elif k == 'expression':
        p._need(action['to'] in EXPRESSIONS and action['to'] != a['action_state']['expression'], path, 'requires a changed supported expression')
    elif k == 'cross_passage':
        p._need(a['posture']=='站立', path, 'passage crossing requires standing posture')
        candidates = [x for x in world['scenes'][a['scene_id']].get('passages', []) if x['id'] == action['passage']]
        p._need(len(candidates) == 1, path, 'cross-room movement requires one declared passage')
        passage = candidates[0]; start = world['points'][passage['exit']]; end = world['points'][passage['entry']]
        p._need(passage['opening'] + '开启。' in world['scenes'][a['scene_id']]['door_window_state'], path, 'passage opening is closed or absent')
        p._need(p._distance(a['position_xyz'], start['position_xyz']) <= .2 and duration >= .5, path, 'reach the doorway before crossing; no teleport')
        p._need(not p._blocked(world, a['scene_id'], a['position_xyz'], start['position_xyz']) and
                not p._blocked(world, end['scene_id'], end['position_xyz'], p._add(end['position_xyz'], [0, 0, .01])), path, 'known barrier blocks passage')
        p._need(a['gaze'] is None, path, 'clear source-room gaze before crossing passage')
        p._need(not any(x['gaze'] and x['gaze']['ref'] == {'kind': 'actor', 'id': action['actor']} for x in world['actors'].values()),
                path, 'another actor still holds a source-room gaze on the departing actor')
        info.update(destination=list(end['position_xyz']), destination_scene=end['scene_id'])
    elif k in {'pick_up', 'put_down', 'drop_prop', 'wear_prop', 'remove_worn', 'wipe','dress_actor','undress_actor','release_binding'}:
        prop = world['props'].get(action['prop'])
        p._need(prop is not None and prop['physical']['integrity'] == 'intact', path, 'requires an existing intact prop')
        scene, position = p._anchor(world, prop)
        if k=='release_binding':
            p._need(action['target_actor']!=action['actor'] and prop['anchor']=={'actor':action['target_actor'],'binding':'wrists'},path,'requires actual other-person wrist binding')
            p._free_hand(world,action['actor'],action['hand'],path)
            p._reach(world,action['actor'],scene,position,path)
            p._need(p._distance(p._hand(a,action['hand']),position)<=.2,path,'hand must reach actual binding')
        elif k == 'pick_up':
            p._need(set(prop['anchor']) == {'point'}, path, 'pick_up requires an unheld prop')
            p._free_hand(world, action['actor'], action['hand'], path)
            p._reach(world, action['actor'], scene, position, path)
            p._need(p._distance(p._hand(a, action['hand']), position) <= .2, path, 'hand must reach prop before pickup')
        elif k in {'remove_worn','undress_actor'}:
            owner=action['target_actor'] if k=='undress_actor' else action['actor']
            p._need(owner in world['actors'] and world['actors'][owner]['scene_id']==a['scene_id'],path,'garment wearer is not in the same scene')
            p._need(prop['anchor'].get('actor') == owner and 'worn' in prop['anchor'], path, 'prop is not worn by the actual actor')
            p._free_hand(world, action['actor'], action['hand'], path)
            p._reach(world,action['actor'],scene,position,path)
            p._need(p._distance(p._hand(a, action['hand']), position) <= .2, path, 'hand must reach worn prop')
        else:
            p._need(prop['anchor'] == {'actor': action['actor'], 'hand': action['hand']}, path, 'actor/hand is not the real holder')
        info['prop_start'] = position
        if k in {'put_down', 'drop_prop'}:
            scene, destination = p._target(world, {'kind': 'point', 'id': action['to']}, path)
            p._need(scene == a['scene_id'], path, 'prop destination is in another scene')
            p._need(not p._blocked(world, scene, position, destination), path, 'barrier blocks prop path')
            if k == 'put_down': p._reach(world, action['actor'], scene, destination, path)
            else:
                p._need(type(action['breaks']) is bool, path, 'breaks must be explicit boolean')
                p._need(destination[2] < position[2] and math.hypot(destination[0]-position[0], destination[1]-position[1]) <= .5,
                        path, 'drop requires a lower nearby landing, not a thrown prop')
                contact = action['contact']
                if contact is not None:
                    p._obj(contact, {'actor', 'part'}, path + '.contact')
                    target = world['actors'].get(contact['actor'])
                    p._need(target and target['scene_id'] == scene and contact['part'] in PARTS, path, 'unknown contact actor/body part')
                    p._need(p._distance(body_point(target, contact['part']), destination) <= .2, path, 'landing misses declared body contact')
                p._need(not action['breaks'] or prop['physical']['material'] == 'ceramic', path, 'only declared ceramic may shatter')
            info['destination'] = destination
        elif k in {'wear_prop','dress_actor'}:
            owner=action['target_actor'] if k=='dress_actor' else action['actor']
            p._need(owner in world['actors'] and world['actors'][owner]['scene_id']==a['scene_id'],path,'garment wearer is not in the same scene')
            p._need(action['part'] in {'head', 'torso'}, path, 'unsupported worn attachment')
            p._need(not any(x['anchor'] == {'actor': owner, 'worn': action['part']} for x in world['props'].values()), path, 'worn slot is occupied')
            target=world['actors'][owner];point=body_point(target,action['part'])
            p._reach(world,action['actor'],target['scene_id'],point,path)
            p._need(p._distance(p._hand(a, action['hand']),point) <= .2, path, 'held garment has not reached attachment')
        elif k == 'wipe':
            p._need(prop['physical']['material'] == 'cloth' and action['part'] in {'face', 'left_hand', 'right_hand'}, path, 'wipe requires held cloth and supported body part')
            p._need(p._distance(p._hand(a, action['hand']), body_point(a, action['part'])) <= .2, path, 'cloth has not reached actual body part')
    elif k == 'contact_actor':
        p._need(action['hand'] in {'left', 'right'} and action['target_part'] in PARTS, path, 'unknown contact hand/body part')
        target = world['actors'].get(action['target_actor'])
        p._need(target and action['target_actor'] != action['actor'] and target['scene_id'] == a['scene_id'], path, 'requires another same-scene actor')
        p._need(action['mode'] in {'grasp', 'release_grasp', 'support', 'release_support', 'slap'}, path, 'unsupported contact mode')
        current = a['action_state']['contacts'].get(action['hand'])
        if action['mode'].startswith('release_'):
            p._need(current == {'actor': action['target_actor'], 'part': action['target_part'], 'mode': action['mode'][8:]}, path, 'release does not match an actual contact')
        else:
            p._free_hand(world, action['actor'], action['hand'], path)
            p._need(current is None and not _contacted(world, action['actor'], action['hand']), path, 'contact hand is already constrained')
            point = body_point(target, action['target_part']); p._reach(world, action['actor'], target['scene_id'], point, path)
            p._need(not p._blocked(world,a['scene_id'],p._hand(a,action['hand']),point), path, 'barrier blocks contact path')
            if action['mode']=='slap':
                p._need(p._distance(p._hand(a,action['hand']),point) >= .1, path, 'slap requires an actual approach before contact')
            if action['mode'] == 'slap': p._need(action['target_part'] == 'face', path, 'slap requires a face landmark')
            if action['mode'] == 'grasp': p._need(action['target_part'] in {'left_wrist', 'right_wrist'}, path, 'grasp requires an actual wrist')
    elif k in {'clap', 'kowtow', 'head_gesture'}:
        p._need(type(action['count']) is int and 1 <= action['count'] <= 10, path, 'requires exact bounded repetition count')
        p._need(duration >= action['count'] * (.6 if k == 'kowtow' else .4), path, 'declared repetitions cannot fit action interval')
        if k == 'clap':
            for h in ('left', 'right'): p._free_hand(world, action['actor'], h, path)
            p._need(not a['action_state']['contacts'] and not _contacted(world, action['actor']), path, 'release contacts before clapping')
        elif k == 'kowtow':
            p._need(a['posture'] == '跪姿' and a['gaze'] is None and abs(a['position_xyz'][2]) <= .05, path, 'kowtow needs a ground kneel and cleared gaze')
        else:
            p._need(action['direction'] in {'nod', 'shake'} and a['gaze'] is None, path, 'head gesture requires nod/shake and cleared gaze')
    elif k == 'set_opening':
        from production_environment import opening_states
        scene=world['scenes'][a['scene_id']]
        states=opening_states(scene['door_window_state'])
        p._need(action['opening'] in states and action['to'] in {'open','closed'} and states[action['opening']] != action['to'],
                path,'requires an actual registered opening and changed state')
        point=world['points'].get(action['opening'])
        p._need(point and point['scene_id']==a['scene_id'],path,'opening needs an actual same-scene contact point')
        p._free_hand(world,action['actor'],action['hand'],path)
        p._reach(world,action['actor'],point['scene_id'],point['position_xyz'],path)
        p._need(p._distance(p._hand(a,action['hand']),point['position_xyz'])<=.2,path,'hand must reach opening')
    elif k == 'scene_light':
        p._need(bridge and action['source_use_id'] is None, path, 'light period changes belong to a frozen inter-shot bridge')
        p._need(action['scene_id'] in world['scenes'], path, 'unknown light scene')
        p._obj(action['light'], set(world['scenes'][action['scene_id']]['light']), path + '.light')
        # Reuse the normal initial-world lighting/opening validator on the changed scene.
        from authoring_contract_blocks import interior_lighting_blocks
        light = action['light']; p._xyz(light['position_xyz'], path)
        from production_environment import normalize_openings
        normalize_openings(world['scenes'][action['scene_id']]['door_window_state'], light, path)
        interior_lighting_blocks(source={k: light[k] for k in ('period', 'kind', 'name', 'state')} |
            {'world_side': '东', 'height_m': light['position_xyz'][2]}, screen_side='左',
            receiving_surfaces=['人物面部'], weak_reflector=None, main_tone=light['main_tone'], shadow_tone=light['shadow_tone'], materials=light['materials'], air='clear', exposure='normal')
    return info


def effects(initial, action, tick):
    """A finite transition specification; caller cannot provide mutation paths."""
    p = _p(); k = action['kind']; who = action['actor']; a = initial['actors'][who]
    end = tick == action['end']; f = (tick-action['start'])/(action['end']-action['start'])
    result = {}; base = ('actors', who)
    def set_actor(key, value): result[base + (key,)] = copy.deepcopy(value)
    def state(key, value): result[base + ('action_state', key)] = copy.deepcopy(value)
    if k in {'posture','fall'}:
        dest = initial['points'][action['point']]['position_xyz']; lm = action['landmarks']
        pos = p._lerp(a['position_xyz'], dest, f); set_actor('position_xyz', pos)
        set_actor('posture', ('躺卧' if k=='fall' else action['to']) if end else a['posture'])
        set_actor('eye_height', a['eye_height'] + (lm['eye_height']-a['eye_height'])*f)
        set_actor('reach_origin_xyz', p._lerp(a['reach_origin_xyz'], p._add(dest, lm['reach_origin_offset_xyz']), f))
        set_actor('hands', {h:p._lerp(a['hands'][h], lm['hands'][h], f) for h in ('left','right')})
    elif k == 'hand_move':
        changed = copy.deepcopy(a); dest = initial['points'][action['to']]['position_xyz']
        p._set_hand(changed, action['hand'], p._lerp(p._hand(a, action['hand']), dest, f))
        result[base+('hands',action['hand'])] = changed['hands'][action['hand']]
    elif k == 'eye_state': set_actor('eye_state', action['to'] if end else a['eye_state'])
    elif k == 'hand_shape':
        result[base+('action_state','hand_shapes',action['hand'])] = action['to'] if end else a['action_state']['hand_shapes'][action['hand']]
    elif k == 'expression': state('expression', action['to'] if end else a['action_state']['expression'])
    elif k == 'cross_passage':
        passage = next(x for x in initial['scenes'][a['scene_id']]['passages'] if x['id']==action['passage'])
        dest = initial['points'][passage['entry']]; pos = p._lerp(a['position_xyz'], dest['position_xyz'], f)
        set_actor('position_xyz', pos); set_actor('reach_origin_xyz', p._add(a['reach_origin_xyz'], p._sub(pos, a['position_xyz'])))
        set_actor('scene_id', dest['scene_id'] if end else a['scene_id'])
    elif k in {'pick_up','put_down','drop_prop','wear_prop','remove_worn','dress_actor','undress_actor','release_binding'}:
        prop = initial['props'][action['prop']]; anchor = copy.deepcopy(prop['anchor'])
        if k in {'put_down','drop_prop'}:
            scene, start = p._anchor(initial, prop); dest = initial['points'][action['to']]['position_xyz']
            if k == 'put_down':
                changed = copy.deepcopy(a); p._set_hand(changed,action['hand'],p._lerp(start,dest,f))
                result[base+('hands',action['hand'])] = changed['hands'][action['hand']]
                anchor = {'point':action['to']} if end else prop['anchor']
            else:
                anchor = {'point':action['to']} if end else {'free':{'scene_id':scene,'position_xyz':p._lerp(start,dest,f*f)}}
                physical = copy.deepcopy(prop['physical'])
                if end:
                    if action['breaks']: physical['integrity']='broken'
                    if action['contact'] and physical['contents'] != 'none':
                        target=action['contact']; wet=initial['actors'][target['actor']]['action_state']['wet_parts']
                        result[('actors',target['actor'],'action_state','wet_parts')]=sorted(set(wet)|{target['part']})
                    physical['contents']='none'
                result[('props',action['prop'],'physical')]=physical
        elif end:
            owner=action['target_actor'] if k=='dress_actor' else who
            anchor = {'actor':owner,'worn':action['part']} if k in {'wear_prop','dress_actor'} else {'actor':who,'hand':action['hand']}
        result[('props',action['prop'],'anchor')]=anchor
    elif k == 'wipe':
        state('clean_parts', sorted(set(a['action_state']['clean_parts'])|{action['part']}) if end else a['action_state']['clean_parts'])
    elif k == 'contact_actor':
        contacts=copy.deepcopy(a['action_state']['contacts'])
        if not action['mode'].startswith('release_'):
            dest=body_point(initial['actors'][action['target_actor']],action['target_part'])
            changed=copy.deepcopy(a)
            p._set_hand(changed,action['hand'],p._lerp(p._hand(a,action['hand']),dest,f))
            result[base+('hands',action['hand'])]=changed['hands'][action['hand']]
        if end:
            if action['mode'].startswith('release_'): contacts.pop(action['hand'])
            elif action['mode'] != 'slap': contacts[action['hand']]={'actor':action['target_actor'],'part':action['target_part'],'mode':action['mode']}
        state('contacts',contacts)
    elif k == 'clap':
        wave=(1-math.cos(2*math.pi*action['count']*f))/2
        mid=[(x+y)/2 for x,y in zip(a['hands']['left'],a['hands']['right'])]
        set_actor('hands',{h:p._lerp(a['hands'][h],mid,wave) for h in ('left','right')})
        state('claps',a['action_state']['claps']+(action['count'] if end else 0))
    elif k == 'kowtow':
        wave=(1-math.cos(2*math.pi*action['count']*f))/2
        set_actor('eye_height',a['eye_height']+(.08-a['eye_height'])*wave)
        state('kowtows',a['action_state']['kowtows']+(action['count'] if end else 0))
    elif k == 'head_gesture':
        wave=math.sin(2*math.pi*action['count']*f)
        if action['direction']=='shake': set_actor('head_yaw',(a['head_yaw']+12*wave)%360)
        else: set_actor('eye_height',a['eye_height']-.04*abs(wave))
    elif k == 'set_opening':
        from production_environment import opening_states,normalize_openings
        scene=initial['scenes'][a['scene_id']]; states=opening_states(scene['door_window_state'])
        light=copy.deepcopy(scene['light'])
        if end:
            states[action['opening']]=action['to']
            if light['kind']=='paper_window' and light['name']==action['opening']:light['state']=action['to']
        result[('scenes',a['scene_id'],'door_window_state')]=normalize_openings(states,light,'action.opening')
        result[('scenes',a['scene_id'],'light')]=light
    elif k == 'scene_light':
        result[('scenes',action['scene_id'],'light')]=action['light'] if end else initial['scenes'][action['scene_id']]['light']
    return result


def apply(world, action, info, tick):
    if tick == action['end']:
        contact=action.get('contact') if action['kind']=='drop_prop' else (
            {'actor':action['target_actor'],'part':action['target_part']} if action['kind']=='contact_actor' else None)
        if contact:
            expected=(info['destination'] if action['kind']=='drop_prop' else
                      body_point(info['before_all']['actors'][contact['actor']],contact['part']))
            target=world['actors'][contact['actor']]
            _p()._need(target['scene_id']==info['before_all']['actors'][action['actor']]['scene_id'] and
                       _p()._distance(body_point(target,contact['part']),expected)<=.2,
                       'action.contact', 'actual endpoint misses contact at completion')
    for keys,value in effects(info['before_all'], action, tick).items():
        node=world
        for key in keys[:-1]: node=node[key]
        node[keys[-1]]=copy.deepcopy(value)


def sentence(action, info):
    a=action['actor']; k=action['kind']; hand='左手' if action.get('hand')=='left' else '右手'
    if k=='posture':
        verb={'站立':'起身','坐姿':'坐下','蹲姿':'蹲下','跪姿':'跪下','跪伏':'俯身','躬身':'躬身','躺卧':'躺下'}[action['to']]
        return verb, f"{a}{verb}，落在{action['point']}。"
    if k=='fall':return '跌倒',f"{a}跌倒在{action['point']}。"
    if k=='hand_move': return '移至',f"{a}{hand}移至{action['to']}。"
    if k=='eye_state': return ('闭上双眼',f'{a}闭上双眼。') if action['to']=='closed' else ('睁开双眼',f'{a}睁开双眼。')
    if k=='hand_shape': return ('握紧拳头',f'{a}{hand}握紧拳头。') if action['to']=='fist' else ('展开',f'{a}{hand}展开。')
    if k=='expression': return '神情转为',f"{a}神情转为{action['to']}。"
    if k=='cross_passage': return '迈入',f"{a}经{action['passage']}迈入{info['destination_scene']}。"
    if k=='pick_up': return '拿起',f"{a}{hand}拿起{action['prop']}。"
    if k=='put_down': return '放到',f"{a}{hand}将{action['prop']}放到{action['to']}。"
    if k=='drop_prop':
        target=action['contact']; landing=f"{target['actor']}{PART_TEXT[target['part']]}" if target else action['to']
        tail=''; physical=info['before_all']['props'][action['prop']]['physical']
        if physical['contents']!='none': tail+=f"{'热水' if physical['contents']=='hot_water' else '水'}洒在{landing}。"
        if action['breaks']: tail+=f"{action['prop']}触及落点后碎裂。"
        return '松开',f"{a}{hand}松开{action['prop']}，{action['prop']}下落至{landing}。"+tail
    if k=='wear_prop': return '穿戴',f"{a}将{action['prop']}穿戴到{'头部' if action['part']=='head' else '身上'}。"
    if k=='remove_worn': return '取下',f"{a}{hand}取下{action['prop']}并拿在手中。"
    if k=='dress_actor':return '穿戴',f"{a}{hand}为{action['target_actor']}穿戴{action['prop']}。"
    if k=='undress_actor':return '取下',f"{a}{hand}从{action['target_actor']}身上取下{action['prop']}并拿在手中。"
    if k=='release_binding':return '解开',f"{a}{hand}解开{action['target_actor']}双腕上的{action['prop']}，将{action['prop']}拿在手中。"
    if k=='wipe': return '擦拭',f"{a}以{hand}所持{action['prop']}擦拭自己的{PART_TEXT[action['part']]}。"
    if k=='contact_actor':
        verb={'grasp':'抓住','release_grasp':'松开','support':'扶住','release_support':'松开','slap':'扇向'}[action['mode']]
        return verb,f"{a}{hand}{verb}{action['target_actor']}的{PART_TEXT[action['target_part']]}。"
    if k=='clap': return '拍响',f"{a}双手拍响{action['count']}下。"
    if k=='kowtow': return '磕头',f"{a}跪地磕头{action['count']}下，每次额头触地后抬起。"
    if k=='head_gesture':
        verb='点头' if action['direction']=='nod' else '摇头'
        return verb,f"{a}{verb}{action['count']}次。"
    if k=='set_opening':
        verb='打开' if action['to']=='open' else '关闭'
        return verb,f"{a}{hand}{verb}{action['opening']}。"
    return '时空衔接',''


def static_actor(actor, name):
    state=actor['action_state']; rows=[]
    for part in state['wet_parts']: rows.append(f'{name}{PART_TEXT[part]}留有水渍。')
    for hand,shape in state['hand_shapes'].items():
        if shape=='fist': rows.append(f"{name}{'左手' if hand=='left' else '右手'}握拳。")
    for hand,c in state['contacts'].items():
        rows.append(f"{name}{'左手' if hand=='left' else '右手'}仍{'抓住' if c['mode']=='grasp' else '扶住'}{c['actor']}{PART_TEXT[c['part']]}。")
    if state['expression']!='平静': rows.append(f"{name}神情{state['expression']}。")
    return ''.join(rows)


def visible(sample, action):
    """Named landmarks required to project this action; no offscreen invention."""
    view=sample['view']; owner=view['actors'].get(action['actor'],{}); k=action['kind']
    def part_visible(name, part):
        row=view['actors'].get(name,{})
        if part in {'head','face'}:return bool(row.get('head'))
        if part.endswith(('hand','wrist')):return part.split('_')[0] in row.get('hands',[])
        if part.endswith('foot'):return bool(row.get('feet'))
        return bool(row)
    if k in {'eye_state','expression'}:return bool(owner.get('eye_visible'))
    if k in {'head_gesture','kowtow'}:return bool(owner.get('head'))
    if k=='clap':return set(owner.get('hands',[]))=={'left','right'}
    if k=='contact_actor':return action['hand'] in owner.get('hands',[]) and part_visible(action['target_actor'],action['target_part'])
    if k=='release_binding' and not all(part_visible(action['target_actor'],h+'_hand') for h in ('left','right')):return False
    if k=='drop_prop':
        contact=action['contact']
        return action['prop'] in view['props'] and (not contact or part_visible(contact['actor'],contact['part']))
    if 'hand' in action and action['hand'] not in owner.get('hands',[]):return False
    if 'prop' in action and action['prop'] not in view['props']:return False
    return bool(owner)


def check_contacts(world, path):
    p=_p()
    for owner,actor in world['actors'].items():
        for hand,contact in actor['action_state']['contacts'].items():
            target=world['actors'][contact['actor']]
            p._need(actor['scene_id']==target['scene_id'] and
                    p._distance(p._hand(actor,hand),body_point(target,contact['part']))<=.2,
                    path, 'ongoing body contact was broken without a release action')
            p._need(not p._blocked(world,actor['scene_id'],p._hand(actor,hand),body_point(target,contact['part'])),
                    path, 'barrier blocks ongoing body contact')


def fragment_sentence(action, info, lo, hi):
    """Do not announce landing, release or contact before its actual end."""
    a=action['actor']; k=action['kind']; hand='左手' if action.get('hand')=='left' else '右手'
    if k in {'clap','kowtow'}:
        contacts=[action['start']+(action['end']-action['start'])*(i+.5)/action['count'] for i in range(action['count'])]
        count=sum(lo<t<=hi for t in contacts)
        if count:
            return ('拍响',f'{a}双手拍响{count}下，双掌随后分开。') if k=='clap' else ('磕头',f'{a}跪地磕头{count}下，额头触地后抬起。')
        return ('移动',f'{a}双手继续移动。') if k=='clap' else ('俯身',f'{a}保持跪姿俯身。')
    if hi == action['end']:
        verb,text=sentence(action,info)
        if k=='drop_prop' and lo>action['start']:
            text=text.replace(f'{a}{hand}松开',f'{a}{hand}保持松开',1)
            return '保持',text
        return verb,text
    if k=='drop_prop': return '松开',f"{a}{hand}已松开{action['prop']}，{action['prop']}正在空中下落。"
    if k=='posture': return '转换姿态',f"{a}由{info['before']['posture']}向{action['to']}转换姿态。"
    if k=='fall':return '跌向',f"{a}身体跌向{action['point']}。"
    if k=='cross_passage': return '过门',f"{a}正经{action['passage']}过门。"
    if k=='contact_actor':
        verb='挥向' if action['mode']=='slap' else '松开' if action['mode'].startswith('release_') else '伸向'
        return verb,f"{a}{hand}逐渐{verb}{action['target_actor']}{PART_TEXT[action['target_part']]}。"
    if k in {'pick_up','put_down','wear_prop','remove_worn'}:
        verb, text = sentence(action,info)
        return verb, f"{a}正在{verb}{action['prop']}。"
    return sentence(action,info)
