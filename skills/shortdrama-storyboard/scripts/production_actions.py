"""Finite source-bindable actions, with explicit landmark effects.

This extends the declared landmark model, not body mechanics or source entailment.
No user supplied prose, arbitrary patch, or externally authored endpoint is executed.
The existing source plan, visibility, timing, review and release gates still apply.
"""
from __future__ import annotations

import copy
import math
import production_relations as relations
import production_optical as optical

FIELDS = {
    'posture': {'to', 'point', 'landmarks'},
    'fall': {'point','landmarks'},
    'hand_move': {'hand', 'to'},
    'stroke_prop': {'prop', 'hand', 'to'},
    'hand_tremor': {'hand'},
    'body_shudder': set(),
    'straighten': {'rise_m'},
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
    'assist_stand': {'target_actor','hand','target_part','point','landmarks','helper_point','helper_landmarks'},
    'wipe': {'prop', 'hand', 'part'},
    'contact_actor': {'hand', 'target_actor', 'target_part', 'mode'},
    'push_actor': {'hand', 'target_actor', 'target_part', 'point'},
    'push_actor_no_displacement': {'hand', 'target_actor', 'target_part'},
    'clap': {'count'},
    'kowtow': {'count'},
    'head_gesture': {'direction', 'count'},
    'head_tilt': {'to_degrees'},
    'set_opening': {'opening','to','hand'},
    'scene_light': {'scene_id', 'light'},
}
FIELDS.update(relations.FIELDS)
FIELDS.update(optical.FIELDS)
CONTINUOUS = set(FIELDS)
POSTURES = {'站立': (1.0, 2.2), '坐姿': (.6, 1.6), '蹲姿': (.3, 1.2),
            '跪姿': (.5, 1.4), '跪伏': (.05, .5), '躬身': (.5, 1.8), '躺卧': (.05, .8)}
EXPRESSIONS = {'平静', '微笑', '不悦', '压怒', '疼痛', '震惊', '得意', '疑惑', '委屈', '畏惧', '恭敬', '悲伤'}
# Current authored poses describe visible facial state; old emotion names stay
# readable for historical inputs. A pose is selected, never filled by default.
EXPRESSION_POSES = (
    '眉眼舒展，唇线自然', '眉梢微抬，嘴角上扬', '眼角弯起，嘴角扬开',
    '眉心收紧，唇线绷直', '眉峰压低，下颌收紧', '眉心微蹙，唇角下压',
    '眉梢抬高，双眼睁大', '双眼微睁，嘴唇微张', '一侧眉梢抬起，唇角轻收',
    '眉尾下压，嘴角下垂', '眼眶泛红，唇线轻颤', '眉心微紧，嘴角勉强上提',
    '眉峰微挑，一侧嘴角上扬', '眼睑微敛，嘴角收住', '眉头舒开，唇角微扬',
    '眉心紧蹙，嘴角绷住', '双眼睁大，唇角向下', '眉尾抬起，嘴唇微张',
)
EXPRESSIONS.update(EXPRESSION_POSES)
PARTS = {'face', 'left_wrist', 'right_wrist', 'left_foot', 'right_foot', 'head', 'torso', 'left_hand', 'right_hand'}
PARTS |= {'left_upper_arm', 'right_upper_arm'}
PART_TEXT = {'face': '面颊', 'left_wrist': '左手腕', 'right_wrist': '右手腕', 'left_foot': '左脚背',
             'right_foot': '右脚背', 'head': '头部', 'torso': '外衣', 'left_hand': '左手', 'right_hand': '右手'}
HAND_PARTS = {'left_hand', 'right_hand'}
PART_TEXT.update(left_upper_arm='左上臂', right_upper_arm='右上臂', lap_cloth='膝上衣角')
PUSH_ACTIONS = {'push_actor', 'push_actor_no_displacement'}
SOURCE_REQUIRED = {'drop_prop', 'contact_actor', 'clap', 'kowtow', 'wipe', 'wear_prop', 'remove_worn', 'set_opening','fall','dress_actor','undress_actor','release_binding','assist_stand'} | PUSH_ACTIONS | set(relations.FIELDS)
SOURCE_REQUIRED |= {'stroke_prop', 'hand_tremor', 'body_shudder', 'straighten'} | set(optical.FIELDS)
BRIDGE_KINDS = {'move_actor', 'turn', 'look', 'posture', 'hand_move', 'eye_state', 'hand_shape',
                'expression', 'cross_passage', 'scene_light'}


def _p():
    import production_decisions
    return production_decisions


def body_point(actor, part):
    p = _p()
    if part.endswith('upper_arm') or part == 'lap_cloth':
        x, y, z = ((-.23 if part.startswith('left') else .23), .03, actor['eye_height'] - .38) if part != 'lap_cloth' else (.18, .30, actor['eye_height'] * .55)
        angle = math.radians(actor['body_yaw'])
        return p._add(actor['position_xyz'], [x*math.cos(angle)+y*math.sin(angle), -x*math.sin(angle)+y*math.cos(angle), z])
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
        actor['action_state'].setdefault('body_support', None)
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
        p._obj(s, {'contacts', 'wet_parts', 'clean_parts', 'hand_shapes', 'expression', 'claps', 'kowtows', 'body_support'} |
               ({'head_pitch_degrees'} if 'head_pitch_degrees' in s else set()), 'actor.action_state')
        if 'head_pitch_degrees' in s:
            p._need(-30 <= p._num(s['head_pitch_degrees'], 'actor.head_pitch_degrees') <= 30,
                    'actor.head_pitch_degrees', 'supported head pitch is -30 to 30 degrees')
        p._need(s['contacts'] == {}, 'actor.action_state.contacts', 'initial contact needs an executed source action')
        p._need(s['body_support'] is None, 'actor.action_state.body_support', 'initial body support needs an executed source action')
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
    relations.validate_containers(world)


def channels(action):
    a, k = action['actor'], action['kind']
    if k in relations.FIELDS: return relations.channels(action)
    if k == 'phantom_fade': return {(a, 'optical')}
    if k in {'body_shudder','straighten'}: return {(a, 'body'), (a, 'position')}
    # Channels are calculated before preparation. Validate keys needed below
    # so malformed input fails closed as a domain error, never a raw TypeError.
    for key in FIELDS[k] - {'landmarks','helper_landmarks','light','contact','breaks','count','to_degrees'}:
        _p()._need(isinstance(action[key],str), 'action.'+key, 'requires named action parameter')
    if k == 'assist_stand':
        return {(who,part) for who in (a,action['target_actor'])
                for part in ('position','body','head','left','right','gaze')}
    if k in PUSH_ACTIONS:
        return {(a,'position'),(a,'body'),(a,action['hand'])} | {
            (action['target_actor'],part) for part in ('position','body','left','right')}
    if k == 'scene_light': return {('scene', action['scene_id'])}
    if k == 'set_opening': return {('scene_openings','all'),(a,action['hand'])}
    if k in {'posture', 'fall', 'cross_passage', 'kowtow'}:
        return {(a, x) for x in ('position', 'body', 'head', 'left', 'right', 'gaze')}
    if k == 'eye_state': return {(a, 'eyelid_closure'), (a, 'gaze')}
    if k == 'expression': return {(a, 'expression')}
    if k == 'head_gesture': return {(a, 'head'), (a, 'gaze')}
    if k == 'head_tilt': return {(a, 'head')}
    if k == 'clap': return {(a, 'left'), (a, 'right')}
    result = {(a, action['hand'])}
    if k == 'contact_actor' and action['target_part'] in HAND_PARTS and action['mode'] in {'grasp', 'release_grasp'}:
        result.update({(a, 'position'), (a, 'body')})
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


def _push_ticks(action):
    """Finite landmark timing, not a claim about measured body dynamics."""
    length = action['end'] - action['start']
    return action['start'] + length // 4, action['start'] + 3 * length // 4


def prepare(world, action, info, path, *, bridge=False):
    p = _p(); a = world['actors'][action['actor']]; k = action['kind']
    info['before_all'] = copy.deepcopy(world)
    duration = (action['end'] - action['start']) / 10
    p._need(duration >= .2, path, 'action needs at least 0.2 seconds in the declared landmark model')
    if k in SOURCE_REQUIRED:
        p._need(action['source_use_id'] is not None, path, 'this plot action requires a frozen physical source use')
    if k in relations.FIELDS:
        return relations.prepare(world, action, info, path)
    if k in optical.FIELDS: return optical.prepare(world, action, info, path, bridge=bridge)
    if k in {'body_shudder','straighten'}:
        p._need(not bridge and a['posture']=='站立' and not a['action_state']['contacts'] and not _contacted(world, action['actor']),path,'shudder requires free standing body')
        if k=='straighten':p._need(0 < p._num(action['rise_m'],path) <= .08,path,'straightening rise must be positive and at most eight centimetres')
        return info
    if k in {'posture', 'fall', 'cross_passage'}:
        p._need(not a['action_state']['contacts'] and not _contacted(world, action['actor']), path, 'release actual body contact before relocating')
    if k in {'hand_move', 'hand_shape', 'pick_up', 'put_down', 'drop_prop', 'wear_prop', 'remove_worn', 'wipe', 'set_opening','dress_actor','undress_actor','release_binding'}:
        p._need(action['hand'] in {'left', 'right'}, path, 'unknown hand')
        p._need(action['hand'] not in a['action_state']['contacts'] and not _contacted(world, action['actor'], action['hand']),
                path, 'hand remains in an actual grasp/support contact')
    if k in PUSH_ACTIONS:
        target = world['actors'].get(action['target_actor'])
        p._need(target and action['target_actor'] != action['actor'] and target['scene_id'] == a['scene_id'],
                path, 'push requires another same-scene actor')
        p._need(action['hand'] in {'left','right'} and action['target_part'] == 'torso',
                path, 'push supports a declared hand-to-torso contact')
        stationary = k == 'push_actor_no_displacement'
        if stationary:
            p._need(a['posture'] == target['posture'] and a['posture'] in {'站立','坐姿'} and duration >= .8,
                    path, 'no-displacement push requires both sitting or both standing and at least 0.8 seconds')
        else:
            p._need(a['posture'] == target['posture'] == '站立' and duration >= .8,
                    path, 'push requires standing people and at least 0.8 seconds')
        p._free_hand(world, action['actor'], action['hand'], path)
        p._need(a['action_state']['hand_shapes'][action['hand']] == 'open', path, 'push requires an open hand')
        p._need(not a['action_state']['contacts'] and not target['action_state']['contacts'] and
                not _contacted(world, action['actor']) and not _contacted(world, action['target_actor']),
                path, 'release existing body contacts before pushing')
        p._need(not any(x['anchor'] == {'actor':action['target_actor'],'binding':'wrists'}
                       for x in world['props'].values()), path, 'bound target needs a separately supported displacement')
        destination = target['position_xyz']
        if not stationary:
            point = world['points'].get(action['point'])
            p._need(point and point['scene_id'] == target['scene_id'], path, 'push endpoint must stay in the same physical scene')
            destination = point['position_xyz']
        delta = p._sub(destination, target['position_xyz'])
        if not stationary:
            p._need(p.EPS < p._distance(delta,[0,0,0]) <= .6 and abs(delta[2]) <= p.EPS,
                    path, 'push requires a changed level endpoint within the finite 0.6 metre displacement model')
        away = p._sub(target['position_xyz'], a['position_xyz'])
        p._need(sum(delta[i]*away[i] for i in (0,1)) >= -p.EPS and math.hypot(*away[:2]) > p.EPS,
                path, 'push may move the target aside or away, not pull through the initiator')
        for start in (target['position_xyz'], body_point(target,'torso')):
            p._need(not p._blocked(world,target['scene_id'],start,p._add(start,delta)),
                    path, 'barrier blocks pushed target path')
        contact = body_point(target,'torso')
        p._reach(world,action['actor'],target['scene_id'],contact,path)
        p._need(not p._blocked(world,a['scene_id'],p._hand(a,action['hand']),contact),
                path, 'barrier blocks push contact approach')
        p._need(p._distance(p._hand(a,action['hand']),contact) > .05,
                path, 'push must show approach to the actual contact landmark')
        # The declared shoulder and arm must reach the moving contact, including
        # the final target position. This action does not secretly move its owner.
        p._reach(world,action['actor'],target['scene_id'],p._add(contact,delta),path)
        p._need(not p._blocked(world,a['scene_id'],p._add(contact,delta),p._hand(a,action['hand'])),
                path, 'barrier blocks push hand release')
        info.update(destination=list(destination))
    elif k == 'assist_stand':
        target=world['actors'].get(action['target_actor'])
        p._need(target and action['target_actor']!=action['actor'] and target['scene_id']==a['scene_id'],
                path,'assisted standing requires another same-scene person')
        expected={'actor':action['target_actor'],'part':action['target_part'],'mode':'support'}
        p._need(action['hand'] in {'left','right'} and action['target_part'] in
                {'left_wrist','right_wrist','left_hand','right_hand'},path,'unsupported support attachment')
        p._need(a['action_state']['contacts']=={action['hand']:expected} and
                _contacted(world,action['target_actor'])==[(action['actor'],action['hand'],expected)] and
                not target['action_state']['contacts'] and not _contacted(world,action['actor']),
                path,'requires the single actual support contact, without other constraints')
        p._need(a['gaze'] is None and target['gaze'] is None,path,'clear both visual gazes before assisted standing')
        p._need(not any(x['anchor']=={'actor':action['target_actor'],'binding':'wrists'}
                       for x in world['props'].values()),path,'release wrist binding before assisted standing')
        p._need(target['posture'] in {'躺卧','坐姿','蹲姿','跪姿'} and
                a['posture'] in {'站立','坐姿','蹲姿'} and duration>=.8,path,'unsupported or rushed assisted stand')
        for who,point_key,lm_key in [(action['target_actor'],'point','landmarks'),
                                     (action['actor'],'helper_point','helper_landmarks')]:
            person=world['actors'][who]; dest=world['points'].get(action[point_key]);lm=action[lm_key]
            p._need(dest and dest['scene_id']==person['scene_id'] and
                    p._distance(dest['position_xyz'],person['position_xyz'])<=1.2,
                    path,'assisted stand cannot replace a walk or cross rooms')
            p._need(not p._blocked(world,person['scene_id'],person['position_xyz'],dest['position_xyz']),
                    path,'barrier blocks assisted stand')
            p._obj(lm,{'eye_height','reach_origin_offset_xyz','hands'},path+'.'+lm_key)
            h=p._num(lm['eye_height'],path); off=p._xyz(lm['reach_origin_offset_xyz'],path)
            p._need(1<=h<=2.2 and math.hypot(*off[:2])<=.8 and abs(off[2]-h)<=.7,
                    path,'incompatible standing landmarks')
            p._obj(lm['hands'],{'left','right'},path)
            for xyz in lm['hands'].values():
                p._xyz(xyz,path);p._need(p._distance(xyz,off)<=person['reach_m']+.2,
                                       path,'standing hand exceeds declared reach')
        # Check every actual simulation sample, including the two-body support
        # hand. No independent endpoint/prose patch is accepted.
        for tick in range(action['start'],action['end']+1):
            sample=copy.deepcopy(world)
            for keys,value in effects(world,action,tick).items():
                node=sample
                for key in keys[:-1]:node=node[key]
                node[keys[-1]]=value
            helper=sample['actors'][action['actor']]
            dest=body_point(sample['actors'][action['target_actor']],action['target_part'])
            p._reach(sample,action['actor'],helper['scene_id'],dest,path)
            for who in (action['actor'],action['target_actor']):
                person=sample['actors'][who]
                for hand in ('left','right'):
                    p._need(p._distance(p._hand(person,hand),person['reach_origin_xyz'])<=person['reach_m']+.2,
                            path,'intermediate assisted pose exceeds declared reach')
            check_contacts(sample,path)
    elif k in {'posture','fall'}:
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
    elif k == 'hand_tremor':
        p._free_hand(world, action['actor'], action['hand'], path)
        p._need(.4 <= duration <= 3, path, 'finger tremor needs a bounded 0.4 to 3 second interval')
    elif k == 'stroke_prop':
        p._free_hand(world, action['actor'], action['hand'], path)
        prop = world['props'].get(action['prop'])
        p._need(prop is not None and 'surface' in prop and prop['physical']['integrity'] == 'intact', path,
                'stroke requires an actual intact optical prop')
        scene, center = p._anchor(world, prop)
        target_scene, xyz = p._target(world, {'kind': 'point', 'id': action['to']}, path)
        p._need(scene == target_scene == a['scene_id'] and duration >= .4, path, 'stroke must occur in the same scene')
        p._need(p._distance(p._hand(a, action['hand']), center) <= .15 and
                p._distance(xyz, center) <= .15 and .02 <= p._distance(p._hand(a, action['hand']), xyz) <= .2,
                path, 'stroke requires contact at two distinct bounded surface landmarks')
        p._need(not p._blocked(world, scene, p._hand(a, action['hand']), xyz), path, 'barrier blocks surface stroke')
        p._reach(world, action['actor'], scene, xyz, path)
        info['destination'] = xyz
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
            p._need(set(prop['anchor']) in ({'point'},{'prop','offset_xyz'}), path, 'pick_up requires an unheld or supported prop')
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
        p._need(action['hand'] in {'left', 'right'} and action['target_part'] in PARTS | {'lap_cloth'}, path, 'unknown contact hand/body part')
        target = world['actors'].get(action['target_actor'])
        cloth = action['mode'] in {'grasp_cloth', 'release_grasp_cloth'}
        p._need(target and target['scene_id'] == a['scene_id'] and
                (action['target_actor'] == action['actor'] if cloth else action['target_actor'] != action['actor']),
                path, 'requires own clothing or another same-scene actor')
        p._need(action['mode'] in {'grasp', 'release_grasp', 'support', 'release_support', 'slap', 'grasp_cloth', 'release_grasp_cloth'}, path, 'unsupported contact mode')
        p._need((action['target_part'] == 'lap_cloth') == cloth, path, 'lap cloth requires its explicit grasp mode')
        if cloth:
            p._need(a['posture'] == '坐姿' and sum(prop['anchor'] == {'actor': action['actor'], 'worn': 'torso'} and
                prop['physical'] == {'material': 'cloth', 'contents': 'none', 'integrity': 'intact'} for prop in world['props'].values()) == 1,
                path, 'lap cloth grasp requires one actual intact worn garment and seated posture')
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
            if action['mode'] == 'grasp':
                p._need(action['target_part'] in HAND_PARTS | {'left_wrist', 'right_wrist'}, path, 'grasp requires an actual hand or wrist')
                if action['target_part'] in HAND_PARTS:
                    target_hand = action['target_part'].split('_')[0]
                    p._free_hand(world, action['target_actor'], target_hand, path)
                    p._need(not any(prop['anchor'] == {'actor': action['target_actor'], 'binding': 'wrists'}
                                    for prop in world['props'].values()), path, 'release actual target wrist binding before grasping its hand')
                    p._need(a['action_state']['hand_shapes'][action['hand']] == 'open' and
                            target['action_state']['hand_shapes'][target_hand] == 'open', path,
                            'hand grasp requires both hands open before contact')
    elif k == 'head_tilt':
        pitch = p._num(action['to_degrees'], path + '.to_degrees')
        p._need(-30 <= pitch <= 30 and abs(pitch-a['action_state'].get('head_pitch_degrees', 0)) > p.EPS,
                path, 'head tilt requires an actual change within -30 to 30 degrees')
        p._need(duration >= .3, path, 'head tilt requires at least 0.3 seconds')
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
        # Reuse the normal initial-world lighting/opening validator on the changed scene.
        scene = world['scenes'][action['scene_id']]
        light = action['light']
        p._need(isinstance(light, dict), path + '.light', 'requires an explicit light object')
        p._need((light.get('kind') == 'outdoor_daylight') == (scene['light']['kind'] == 'outdoor_daylight'),
                path + '.light', 'a lighting bridge cannot relocate the same scene indoors/outdoors')
        p._validate_scene_light(light, scene['label'])
        from production_environment import normalize_openings
        normalize_openings(scene['door_window_state'], light, path)
    return info


def effects(initial, action, tick):
    """A finite transition specification; caller cannot provide mutation paths."""
    p = _p(); k = action['kind']; who = action['actor']; a = initial['actors'][who]
    if k in relations.FIELDS: return relations.effects(initial, action, tick)
    if k in optical.FIELDS: return optical.effects(initial, action, tick)
    if k in {'body_shudder','straighten'}:
        f=(tick-action['start'])/(action['end']-action['start'])
        dz=action['rise_m']*f if k=='straighten' else 0 if tick in {action['start'],action['end']} else .015*math.sin(2*math.pi*f)
        return {('actors',who,'eye_height'):a['eye_height']+dz,
                ('actors',who,'reach_origin_xyz'):p._add(a['reach_origin_xyz'],[0,0,dz])}
    end = tick == action['end']; f = (tick-action['start'])/(action['end']-action['start'])
    result = {}; base = ('actors', who)
    def set_actor(key, value): result[base + (key,)] = copy.deepcopy(value)
    def state(key, value): result[base + ('action_state', key)] = copy.deepcopy(value)
    if k in PUSH_ACTIONS:
        target = initial['actors'][action['target_actor']]
        contact_tick, release_tick = _push_ticks(action)
        shift = max(0., min(1., (tick-contact_tick)/(release_tick-contact_tick)))
        destination = (target['position_xyz'] if k == 'push_actor_no_displacement'
                       else initial['points'][action['point']]['position_xyz'])
        position = p._lerp(target['position_xyz'],destination,shift)
        delta = p._sub(position,target['position_xyz'])
        result[('actors',action['target_actor'],'position_xyz')] = position
        result[('actors',action['target_actor'],'reach_origin_xyz')] = p._add(target['reach_origin_xyz'],delta)
        contact = p._add(body_point(target,'torso'),delta)
        original_hand = p._hand(a,action['hand'])
        if tick < contact_tick:
            hand = p._lerp(original_hand,contact,(tick-action['start'])/(contact_tick-action['start']))
        elif tick <= release_tick:
            hand = contact
        else:
            hand = p._lerp(contact,original_hand,(tick-release_tick)/(action['end']-release_tick))
        changed = copy.deepcopy(a); p._set_hand(changed,action['hand'],hand)
        result[base+('hands',action['hand'])] = changed['hands'][action['hand']]
        contacts = copy.deepcopy(a['action_state']['contacts'])
        if contact_tick <= tick < release_tick:
            contacts[action['hand']] = {'actor':action['target_actor'],'part':'torso','mode':'push'}
        state('contacts',contacts)
    elif k == 'assist_stand':
        changed={}
        for who,point_key,lm_key in [(action['target_actor'],'point','landmarks'),
                                     (who,'helper_point','helper_landmarks')]:
            before=initial['actors'][who];dest=initial['points'][action[point_key]]['position_xyz'];lm=action[lm_key]
            person=copy.deepcopy(before)
            person.update(position_xyz=p._lerp(before['position_xyz'],dest,f),
                posture='站立' if end else before['posture'],
                eye_height=before['eye_height']+(lm['eye_height']-before['eye_height'])*f,
                reach_origin_xyz=p._lerp(before['reach_origin_xyz'],p._add(dest,lm['reach_origin_offset_xyz']),f),
                hands={h:p._lerp(before['hands'][h],lm['hands'][h],f) for h in ('left','right')})
            changed[who]=person
        helper=changed[action['actor']];target=changed[action['target_actor']]
        p._set_hand(helper,action['hand'],body_point(target,action['target_part']))
        for who,person in changed.items():
            for key in ('position_xyz','posture','eye_height','reach_origin_xyz','hands'):
                result[('actors',who,key)]=copy.deepcopy(person[key])
    elif k in {'posture','fall'}:
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
    elif k == 'hand_tremor':
        # A 3 mm local tremor; no held object, injury or supernatural force.
        offset = list(a['hands'][action['hand']])
        offset[0] += .003 * math.sin(6 * math.pi * f) if not end else 0
        result[base+('hands',action['hand'])] = offset
    elif k == 'stroke_prop':
        changed = copy.deepcopy(a)
        xyz = initial['points'][action['to']]['position_xyz']
        p._set_hand(changed, action['hand'], p._lerp(p._hand(a, action['hand']), xyz, f))
        result[base+('hands',action['hand'])] = changed['hands'][action['hand']]
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
        if action['target_part'] in HAND_PARTS and action['mode'] in {'grasp', 'release_grasp'}:
            shape = ('grip' if action['mode'] == 'grasp' else 'open') if end else a['action_state']['hand_shapes'][action['hand']]
            result[base+('action_state','hand_shapes',action['hand'])] = shape
        if action['mode'] in {'grasp_cloth', 'release_grasp_cloth'}:
            result[base+('action_state','hand_shapes',action['hand'])] = ('grip' if action['mode'] == 'grasp_cloth' else 'open') if end else a['action_state']['hand_shapes'][action['hand']]
    elif k == 'clap':
        wave=(1-math.cos(2*math.pi*action['count']*f))/2
        mid=[(x+y)/2 for x,y in zip(a['hands']['left'],a['hands']['right'])]
        set_actor('hands',{h:p._lerp(a['hands'][h],mid,wave) for h in ('left','right')})
        state('claps',a['action_state']['claps']+(action['count'] if end else 0))
    elif k == 'kowtow':
        wave=(1-math.cos(2*math.pi*action['count']*f))/2
        set_actor('eye_height',a['eye_height']+(.08-a['eye_height'])*wave)
        state('kowtows',a['action_state']['kowtows']+(action['count'] if end else 0))
    elif k == 'head_tilt':
        initial_pitch = a['action_state'].get('head_pitch_degrees', 0)
        state('head_pitch_degrees', initial_pitch + (action['to_degrees']-initial_pitch)*f)
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
            if light['kind'] in {'paper_window','glass_window'} and light['name']==action['opening']:light['state']=action['to']
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
    if k in relations.FIELDS: return relations.sentence(action, info)
    if k in optical.FIELDS: return optical.sentence(action, info)
    if k == 'head_tilt':
        verb = '低头' if action['to_degrees'] < info['before']['action_state'].get('head_pitch_degrees', 0) else '抬头'
        end_pose = '头部回正' if action['to_degrees'] == 0 else '头部微低' if action['to_degrees'] < 0 else '头部微仰'
        return verb, f'{a}{verb}至{end_pose}，身体保持原有朝向。'
    if k=='body_shudder':return '一震',f'{a}身躯短促一震，随即重新稳住，双脚保持原位。'
    if k=='straighten':return '挺直',f'{a}挺直脊背，双脚保持原位。'
    if k=='assist_stand':
        return '扶起',f"{a}{hand}持续扶住{action['target_actor']}{PART_TEXT[action['target_part']]}并扶起{action['target_actor']}，帮助其起身站稳。"
    if k=='push_actor':
        return '推开',f"{a}{hand}抵住{action['target_actor']}的躯干并推开，{action['target_actor']}被推至{action['point']}，{a}随即收回{hand}。"
    if k=='push_actor_no_displacement':
        return '推抵',f"{a}{hand}推抵{action['target_actor']}的躯干，却推不动，{action['target_actor']}保持原位，{a}随即收回{hand}。"
    if k=='posture':
        verb={'站立':'起身','坐姿':'坐下','蹲姿':'蹲下','跪姿':'跪下','跪伏':'俯身','躬身':'躬身','躺卧':'躺下'}[action['to']]
        return verb, f"{a}{verb}，落在{action['point']}。"
    if k=='fall':return '跌倒',f"{a}跌倒在{action['point']}。"
    if k=='hand_move': return '移至',f"{a}{hand}移至{action['to']}。"
    if k=='hand_tremor': return '微颤', f'{a}{hand}指尖微颤。'
    if k=='stroke_prop': return '轻拂', f"{a}{hand}指尖贴着{action['prop']}镜面轻拂，停在{action['to']}。"
    if k=='eye_state': return ('闭上双眼',f'{a}闭上双眼。') if action['to']=='closed' else ('睁开双眼',f'{a}睁开双眼。')
    if k=='hand_shape': return ('握紧拳头',f'{a}{hand}握紧拳头。') if action['to']=='fist' else ('展开',f'{a}{hand}展开。')
    if k=='expression':
        return (action['to'], f"{a}{action['to']}。") if action['to'] in EXPRESSION_POSES else ('神情转为', f"{a}神情转为{action['to']}。")
    if k=='cross_passage': return '迈入',f"{a}经{action['passage']}迈入{info['destination_scene']}。"
    if k=='pick_up': return '拿起',f"{a}{hand}拿起{action['prop']}。"
    if k=='put_down': return '放到',f"{a}{hand}将{action['prop']}放到{action['to']}。"
    if k=='drop_prop':
        target=action['contact']; landing=f"{target['actor']}{PART_TEXT[target['part']]}" if target else action['to']
        tail=''; physical=info['before_all']['props'][action.get('_prop_id', action['prop'])]['physical']
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
        if action['mode'] in {'grasp_cloth', 'release_grasp_cloth'}:
            verb = '攥紧' if action['mode'] == 'grasp_cloth' else '松开'
            return verb, f'{a}{hand}指尖{verb}自己膝上的衣角，衣物仍穿在身上。'
        verb={'grasp':'抓住','release_grasp':'松开','support':'扶住','release_support':'松开','slap':'扇向'}[action['mode']]
        if action['target_part'] in HAND_PARTS and action['mode'] in {'grasp', 'release_grasp'}:
            verb = '握住' if action['mode'] == 'grasp' else '松开'
            fingers = '手指收拢' if action['mode'] == 'grasp' else '手指展开'
            return verb,f"{a}{hand}{fingers}，{verb}{action['target_actor']}的{PART_TEXT[action['target_part']]}。"
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


def static_actor(actor, name, *, face_visible=True, view=None):
    state=actor['action_state']; rows=[optical.static_actor(actor, name)]
    pitch = state.get('head_pitch_degrees', 0)
    if pitch and (view is None or view.get('actors', {}).get(name, {}).get('head')):
        rows.append(f"{name}{'头部微低' if pitch < 0 else '头部微仰'}。")
    visible_hands = None if view is None else view.get('actors', {}).get(name, {}).get('hands', [])
    rows.append(relations.static_actor(actor, name, view=view))
    for part in state['wet_parts']: rows.append(f'{name}{PART_TEXT[part]}留有水渍。')
    for hand,shape in state['hand_shapes'].items():
        if visible_hands is not None and hand not in visible_hands: continue
        if shape=='fist': rows.append(f"{name}{'左手' if hand=='left' else '右手'}握拳。")
    for hand,c in state['contacts'].items():
        if visible_hands is not None and hand not in visible_hands: continue
        verb = {'grasp':'抓住','support':'扶住','push':'推抵','grasp_cloth':'攥住'}[c['mode']]
        if c['mode'] == 'grasp' and c['part'] in HAND_PARTS: verb = '手指收拢握住'
        rows.append(f"{name}{'左手' if hand=='left' else '右手'}仍{verb}{c['actor']}{PART_TEXT[c['part']]}。")
    if face_visible and state['expression'] != '平静':
        rows.append(f"{name}{state['expression']}。" if state['expression'] in EXPRESSION_POSES else f"{name}神情{state['expression']}。")
    return ''.join(rows)


def visible(sample, action):
    """Named landmarks required to project this action; no offscreen invention."""
    view=sample['view']; owner=view['actors'].get(action['actor'],{}); k=action['kind']
    if k in relations.FIELDS: return relations.visible(sample, action)
    if k in optical.FIELDS: return optical.visible(sample, action)
    if k in {'body_shudder','straighten'}:return bool(owner.get('head') and owner.get('hands'))
    def part_visible(name, part):
        row=view['actors'].get(name,{})
        if part in {'head','face'}:return bool(row.get('head'))
        if part.endswith(('hand','wrist')):return part.split('_')[0] in row.get('hands',[])
        if part.endswith('foot'):return bool(row.get('feet'))
        if part.endswith('upper_arm') or part == 'lap_cloth': return bool(row.get('head') and row.get('hands'))
        return bool(row)
    if k in {'eye_state','expression'}:return bool(owner.get('eye_visible'))
    if k in {'head_gesture','head_tilt','kowtow'}:return bool(owner.get('head'))
    if k=='clap':return set(owner.get('hands',[]))=={'left','right'}
    if k=='contact_actor':return action['hand'] in owner.get('hands',[]) and part_visible(action['target_actor'],action['target_part'])
    if k in PUSH_ACTIONS:
        target=view['actors'].get(action['target_actor'],{})
        # Head and feet bound the torso and its moved/unchanged location in the
        # camera frame; unknown real body occlusion still needs semantic review.
        return bool(action['hand'] in owner.get('hands',[]) and target.get('head') and target.get('feet'))
    if k=='assist_stand':
        target=view['actors'].get(action['target_actor'],{})
        return bool(owner.get('head') and target.get('head') and
                    action['hand'] in owner.get('hands',[]) and
                    part_visible(action['target_actor'],action['target_part']))
    if k=='release_binding' and not all(part_visible(action['target_actor'],h+'_hand') for h in ('left','right')):return False
    if k=='drop_prop':
        contact=action['contact']
        return action['prop'] in view['props'] and (not contact or part_visible(contact['actor'],contact['part']))
    if 'hand' in action and action['hand'] not in owner.get('hands',[]):return False
    if 'prop' in action and action['prop'] not in view['props']:return False
    return bool(owner)


def check_contacts(world, path):
    p=_p()
    relations.check_relations(world, path)
    for owner,actor in world['actors'].items():
        for hand,shape in actor['action_state']['hand_shapes'].items():
            contact = actor['action_state']['contacts'].get(hand)
            hand_grasp = contact is not None and ((contact['mode'] == 'grasp' and contact['part'] in HAND_PARTS) or
                                                  (contact['mode'] == 'grasp_cloth' and contact['part'] == 'lap_cloth' and contact['actor'] == owner))
            p._need((shape == 'grip') == hand_grasp, path, 'gripping fingers require the actual matching hand grasp')
        for hand,contact in actor['action_state']['contacts'].items():
            target=world['actors'][contact['actor']]
            if contact['mode'] == 'grasp' and contact['part'] in HAND_PARTS:
                target_hand = contact['part'].split('_')[0]
                p._need(hand not in {prop['anchor'].get('hand') for prop in world['props'].values()
                                     if prop['anchor'].get('actor') == owner}, path, 'grasping hand cannot hold a prop')
                p._need(not target['action_state']['contacts'].get(target_hand) and
                        _contacted(world, contact['actor'], target_hand) == [(owner, hand, contact)], path,
                        'grasp target hand has another contact')
                p._need(target['action_state']['hand_shapes'][target_hand] == 'open' and
                        not any(prop['anchor'] in ({'actor': contact['actor'], 'hand': target_hand},
                                                  {'actor': contact['actor'], 'binding': 'wrists'})
                                for prop in world['props'].values()), path, 'grasp target hand is occupied or constrained')
            p._need(actor['scene_id']==target['scene_id'] and
                    p._distance(p._hand(actor,hand),body_point(target,contact['part']))<=.2,
                    path, 'ongoing body contact was broken without a release action')
            p._need(not p._blocked(world,actor['scene_id'],p._hand(actor,hand),body_point(target,contact['part'])),
                    path, 'barrier blocks ongoing body contact')


def fragment_sentence(action, info, lo, hi):
    """Do not announce landing, release or contact before its actual end."""
    a=action['actor']; k=action['kind']; hand='左手' if action.get('hand')=='left' else '右手'
    if k in relations.FIELDS: return relations.sentence(action, info, complete=hi == action['end'])
    if k in optical.FIELDS: return optical.sentence(action, info, lo, hi)
    if k == 'head_gesture':
        verb = '点头' if action['direction'] == 'nod' else '摇头'
        if lo == action['start'] and hi == action['end']: return sentence(action, info)
        if lo == action['start']: return verb, f"{a}开始{verb}，本次连续动作共{action['count']}次。"
        if hi == action['end']: return verb, f'{a}完成这一次连续{verb}过程，头部恢复原有姿态。'
        return verb, f'{a}延续同一{verb}过程。'
    if k == 'body_shudder':
        if lo == action['start'] and hi == action['end']: return sentence(action, info)
        if lo == action['start']: return '一震', f'{a}身躯短促一震，双脚保持原位。'
        if hi == action['end']: return '稳住', f'{a}震后重新稳住，双脚保持原位。'
        return '稳住', f'{a}震后逐渐稳住，双脚保持原位。'
    if k == 'stroke_prop':
        if lo == action['start'] and hi == action['end']: return sentence(action, info)
        if hi == action['end']:
            return '停在', f"{a}{hand}指尖完成本次沿镜面轻拂，停在{action['to']}。"
        verb = '开始' if lo == action['start'] else '继续'
        return '轻拂', f"{a}{hand}指尖贴着{action['prop']}镜面{verb}沿同一路径轻拂。"
    if k == 'head_tilt' and hi < action['end']:
        verb, _ = sentence(action, info)
        return verb, f"{a}{'开始' if lo == action['start'] else '继续'}{verb}，身体保持原有朝向。"
    if k in PUSH_ACTIONS:
        contact, release = _push_ticks(action)
        target = action['target_actor']
        if hi <= contact:
            verb = '抵住' if hi == contact else '伸向'
            return verb, f'{a}{hand}{verb}{target}的躯干。'
        if k == 'push_actor_no_displacement':
            if hi < release:
                return '推抵', f'{a}{hand}推抵{target}的躯干，{target}仍保持原位。'
            if lo < release:
                return '推抵', f'{a}{hand}推抵{target}却推不动，{target}保持原位，{a}{hand}随即离开其躯干。'
            return '收回', f'{a}收回{hand}，{target}仍保持原位。'
        if hi < release:
            return '推动', f'{a}{hand}抵住{target}的躯干向{action["point"]}推动，{target}随推力移位。'
        if lo < release:
            return '推开', f'{a}{hand}推开{target}，{target}被推至{action["point"]}，{a}{hand}随即离开其躯干。'
        return '收回', f'{a}收回{hand}，{target}站在{action["point"]}。'
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
    if k=='assist_stand':
        return '扶起',f"{a}{hand}保持对{action['target_actor']}{PART_TEXT[action['target_part']]}的扶持并逐渐扶起{action['target_actor']}。"
    if k=='fall':return '跌向',f"{a}身体跌向{action['point']}。"
    if k=='cross_passage': return '过门',f"{a}正经{action['passage']}过门。"
    if k=='contact_actor':
        verb='挥向' if action['mode']=='slap' else '松开' if action['mode'].startswith('release_') else '伸向'
        return verb,f"{a}{hand}逐渐{verb}{action['target_actor']}{PART_TEXT[action['target_part']]}。"
    if k in {'pick_up','put_down','wear_prop','remove_worn'}:
        verb, text = sentence(action,info)
        return verb, f"{a}正在{verb}{action['prop']}。"
    return sentence(action,info)
