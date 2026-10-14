"""Finite two-person support and opaque sleeve relations.

People remain actors. Declared landmarks are replayed and checked, not a body
mechanics, medical-treatment, load-bearing, or mesh-occlusion simulation.
"""
from __future__ import annotations

import copy
import math

FIELDS = {
    'embrace': {'target_actor', 'point', 'posture', 'landmarks', 'contacts'},
    'lift_actor': {'target_actor', 'point', 'landmarks', 'carrier_point', 'carrier_landmarks'},
    'carry_actor': {'target_actor', 'to'},
    'carry_actor_through_passage': {'target_actor', 'passage'},
    'release_embrace': {'target_actor'},
}
PASSIVE = {'look', 'speak', 'inner_voice', 'narrate', 'eye_state', 'blink',
           'eyelid', 'breath', 'blink_restraint', 'focus_adjust', 'expression', 'hold'}


def contract():
    """One capability description exported by status; no independent pass claim."""
    return {
        'actor_support': {
            'actions': sorted(FIELDS),
            'source_binding': 'source_step only; each effect is replayed and checked once; no bridge source credit; source entailment and knowledge applicability remain independently required',
            'state': 'actor.action_state.body_support=None or {target_actor,mode:embrace|carry,contacts:{left:[x,y,z],right:[x,y,z]}}; initial nonempty support forbidden; supported person remains an actor',
            'embrace': 'two free open hands; another unconstrained same-scene actor whose initial and target posture are the same seated/lying posture; no hidden posture transition; explicit point,posture,landmarks and distinct torso-relative body-local contacts within 0.35m; target displacement<=0.6m and vertical change<=0.15m; duration>=0.8s',
            'lift_actor': 'requires completed embrace; explicit target point/landmarks and carrier_point/carrier_landmarks; local carrier rise to standing; target rises 0.2..1.2m, horizontal displacement<=0.6m; both actual hands remain attached at every sample; duration>=0.8s',
            'carry_actor': 'requires completed lift and standing carrier; to is a same-scene registered point; both people translate together on level ground, <=1.5m/s; same relative support contacts preserved',
            'carry_actor_through_passage': 'requires carry relation and a declared open physical passage with endpoints<=0.5m apart; carrier must reach exit<=0.2m; both people change scene at completion; clear traveller and observer source-room gazes before crossing; no remote scene_id destination',
            'release_embrace': 'releases an actual non-airborne embrace only; releasing a carried person requires an unsupported set-down capability and is rejected',
            'geometry': 'both heads, carrier hands and target base landmark visible during source delivery; per-sample reach, support contacts, body/hand paths and known barriers checked; simultaneous independent body/hand writes, occupied carrier hands, binding, cycles and chains rejected',
            'limits': ['declared-landmark model only; not biomechanics, load-bearing or mesh-occlusion proof', 'no carried-person rotation, set-down, stairs, remote travel, rescue technique or fluid expulsion', 'original bare carry_actor/carry_actor_to_scene plans do not automatically become executable'],
        },
        'opaque_sleeve': {
            'container_schema': 'prop.container={kind:opaque_sleeve,capacity:1}; physical material=cloth and integrity=intact',
            'content_anchor': '{container:<existing prop id>}; one content prop per sleeve; bounded acyclic ancestry; nested sleeves rejected',
            'waist_anchor': '{actor:<actor id>,mount:left_waist|right_waist,offset_xyz:[body-local x,y,z]}; standing only; correct-side x=0.15..0.5m, |y|<=0.3m, z within eye_height-0.9..eye_height-0.3',
            'propagation': 'sleeve and contents follow actual carrier translation/rotation; contents keep container ownership, never count as held or visible; hidden paper cannot be an initial or executed visual target',
            'limits': ['initial containment only; insertion/extraction not supported', 'opaque full concealment only; no partially exposed ruler, mesh fit or capacity-by-volume claim', 'posture-changing waist attachment requires a separately supported transition'],
        },
        'not_supported': ['continue_rescue', 'spit_water', 'carry_actor_to_scene', 'set_down_actor', 'insert_into_container', 'extract_from_container'],
        'semantic_status': 'pending_independent_review',
    }


def _p():
    import production_decisions
    return production_decisions


def _relations(world):
    return [(name, a['action_state'].get('body_support')) for name, a in world['actors'].items()
            if a['action_state'].get('body_support') is not None]


def guard_free_hand(world, actor, hand, path):
    _p()._need(not world['actors'][actor]['action_state'].get('body_support'), path,
               'both hands are occupied by the actual two-person support relation')


def guard_action(world, action, path):
    """No independent action may detach one endpoint of a body relation."""
    participants = {name for owner, rel in _relations(world) for name in (owner, rel['target_actor'])}
    if action['actor'] in participants:
        _p()._need(action['kind'] in PASSIVE or action['kind'] in FIELDS, path,
                   'release or use the supported two-person action before independent body/hand movement')
    target = action.get('target_actor')
    if target in participants:
        _p()._need(action['kind'] in FIELDS, path, 'target already participates in a two-person support relation')
    ref = action.get('target')
    if isinstance(ref, dict) and ref.get('kind') == 'prop':
        _p()._need(prop_visible(world, ref.get('id')), path, 'opaque container blocks the visual target')
    for key in ('prop', 'tool'):
        identity = action.get(key)
        if isinstance(identity, str) and identity in world['props']:
            _p()._need(prop_visible(world, identity), path,
                       'opaque container contents and descendants require a supported extraction action')


def channels(action):
    p = _p()
    p._name(action['target_actor'], 'action.target_actor')
    return {(who, part) for who in (action['actor'], action['target_actor'])
            for part in ('position', 'body', 'left', 'right', 'body_support')}


def _offset(actor, xyz):
    angle = math.radians(actor['body_yaw']); x, y, z = xyz
    return [x * math.cos(angle) + y * math.sin(angle), -x * math.sin(angle) + y * math.cos(angle), z]


def contact_point(target, offset):
    return _p()._add(target['reach_origin_xyz'], _offset(target, offset))


def _landmarks(person, lm, posture, path):
    p = _p()
    from production_actions import POSTURES
    p._need(isinstance(posture, str) and posture in {'站立', '坐姿', '躺卧'}, path, 'unsupported supported-body posture')
    p._obj(lm, {'eye_height', 'reach_origin_offset_xyz', 'hands'}, path)
    h = p._num(lm['eye_height'], path); off = p._xyz(lm['reach_origin_offset_xyz'], path)
    p._need(POSTURES[posture][0] <= h <= POSTURES[posture][1] and math.hypot(*off[:2]) <= .8 and abs(off[2] - h) <= .7,
            path, 'incompatible explicit supported-body landmarks')
    p._obj(lm['hands'], {'left', 'right'}, path)
    for xyz in lm['hands'].values():
        p._xyz(xyz, path)
        p._need(p._distance(_offset(person, xyz), off) <= person['reach_m'] + .2, path, 'supported-body hand exceeds declared reach')


def _point(world, name, scene, path):
    p = _p(); p._name(name, path)
    value = world['points'].get(name)
    p._need(value and value['scene_id'] == scene, path, 'support endpoint must be an explicit same-scene point')
    return value['position_xyz']


def _pose(before, dest, lm, posture, f, end):
    p = _p(); person = copy.deepcopy(before)
    person.update(position_xyz=p._lerp(before['position_xyz'], dest, f),
                  reach_origin_xyz=p._lerp(before['reach_origin_xyz'], p._add(dest, lm['reach_origin_offset_xyz']), f),
                  eye_height=before['eye_height'] + (lm['eye_height'] - before['eye_height']) * f,
                  posture=posture if end else before['posture'],
                  hands={h: p._lerp(before['hands'][h], lm['hands'][h], f) for h in ('left', 'right')})
    return person


def _passage(world, action):
    a = world['actors'][action['actor']]
    return next(x for x in world['scenes'][a['scene_id']].get('passages', []) if x['id'] == action['passage'])


def prepare(world, action, info, path):
    p = _p(); who = action['actor']; k = action['kind']; a = world['actors'][who]
    target_id = action['target_actor']; target = world['actors'].get(target_id)
    p._need(target and target_id != who and target['scene_id'] == a['scene_id'], path, 'support needs another same-scene actor, never a prop')
    p._need(action['source_use_id'] is not None, path, 'two-person plot actions require a frozen physical source use')
    duration = (action['end'] - action['start']) / 10
    p._need(duration >= (.8 if k in {'embrace', 'lift_actor'} else .5), path, 'two-person action interval is too short')
    from production_actions import _contacted
    p._need(not a['action_state']['contacts'] and not target['action_state']['contacts'] and
            not _contacted(world, who) and not _contacted(world, target_id), path, 'release other actual contacts before two-person support')
    p._need(not any(prop['anchor'] == {'actor': name, 'binding': 'wrists'} for prop in world['props'].values()
                    for name in (who, target_id)), path, 'bound actors cannot enter this two-person model')
    relation = a['action_state'].get('body_support')
    if k == 'embrace':
        p._need(not any(who in (owner, rel['target_actor']) or target_id in (owner, rel['target_actor'])
                        for owner, rel in _relations(world)), path, 'support requires two unconstrained actors; no cycles or chains')
        for hand in ('left', 'right'):
            p._free_hand(world, who, hand, path)
            p._need(a['action_state']['hand_shapes'][hand] == 'open', path, 'embrace requires open unoccupied hands')
        p._need(isinstance(action['posture'], str) and action['posture'] in {'坐姿', '躺卧'}, path, 'this embrace supports seated or lying target landmarks only')
        p._need(target['posture'] in {'坐姿', '躺卧'} and target['posture'] == action['posture'],
                path, 'embrace requires an already seated or lying target in the same posture; no hidden posture transition')
        dest = _point(world, action['point'], a['scene_id'], path)
        p._need(p._distance(target['position_xyz'], dest) <= .6 and abs(dest[2] - target['position_xyz'][2]) <= .15,
                path, 'embrace cannot substitute for lifting or walking')
        _landmarks(target, action['landmarks'], action['posture'], path)
        p._obj(action['contacts'], {'left', 'right'}, path + '.contacts')
        for offset in action['contacts'].values():
            p._xyz(offset, path)
            p._need(p._distance(offset, [0, 0, 0]) <= .35, path, 'support contacts must be explicit nearby torso landmarks')
        p._need(p._distance(action['contacts']['left'], action['contacts']['right']) >= .15,
                path, 'two support hands need distinct torso contact landmarks')
    else:
        p._need(relation and relation['target_actor'] == target_id, path, 'requires the actual preceding embrace/carry relation')
        if k == 'lift_actor':
            p._need(relation['mode'] == 'embrace' and a['posture'] in {'站立', '坐姿', '蹲姿'}, path, 'lift starts from an embrace, never a second lift')
            dest = _point(world, action['point'], a['scene_id'], path)
            carrier = _point(world, action['carrier_point'], a['scene_id'], path)
            p._need(.2 <= dest[2] - target['position_xyz'][2] <= 1.2 and
                    math.hypot(*(p._sub(dest, target['position_xyz'])[:2])) <= .6,
                    path, 'lift needs a bounded upward displacement, not a walk or teleport')
            p._need(p._distance(carrier, a['position_xyz']) <= .6 and abs(carrier[2] - a['position_xyz'][2]) <= p.EPS,
                    path, 'carrier must rise locally at the same floor level')
            _landmarks(target, action['landmarks'], target['posture'], path)
            _landmarks(a, action['carrier_landmarks'], '站立', path)
        elif k == 'release_embrace':
            p._need(relation['mode'] == 'embrace', path, 'airborne actor cannot be released without a supported set-down action')
        else:
            p._need(relation['mode'] == 'carry' and a['posture'] == '站立', path, 'travel needs an actually lifted actor and standing carrier')
            if k == 'carry_actor':
                dest = _point(world, action['to'], a['scene_id'], path)
                distance = p._distance(a['position_xyz'], dest)
                p._need(p.EPS < distance <= duration * 1.5 and abs(dest[2] - a['position_xyz'][2]) <= p.EPS,
                        path, 'carry supports bounded level walking at no more than 1.5 metres per second')
            else:
                p._name(action['passage'], path)
                candidates = [x for x in world['scenes'][a['scene_id']].get('passages', []) if x['id'] == action['passage']]
                p._need(len(candidates) == 1, path, 'carry crossing needs one declared physical passage')
                passage = candidates[0]; entry = world['points'][passage['entry']]; exit_ = world['points'][passage['exit']]
                p._need(passage['opening'] + '开启。' in world['scenes'][a['scene_id']]['door_window_state'], path, 'carry passage opening is closed or absent')
                p._need(p._distance(a['position_xyz'], exit_['position_xyz']) <= .2, path, 'reach the physical doorway before crossing')
                p._need(a['gaze'] is None and target['gaze'] is None and not any(
                    person['gaze'] and person['gaze']['ref'] in ({'kind': 'actor', 'id': who}, {'kind': 'actor', 'id': target_id})
                    for name, person in world['actors'].items() if name not in {who, target_id}),
                    path, 'clear both travellers and observers source-room gazes before crossing')
                info['destination_scene'] = entry['scene_id']
    # Check both body paths, hand reach and persistent attachments at every tick.
    previous = world
    # Scene labels switch only at completion; destination barriers can still
    # intersect the earlier swept path through the physical doorway.
    passage_scenes = ({a['scene_id'], info['destination_scene']}
                      if k == 'carry_actor_through_passage' else None)
    for tick in range(action['start'], action['end'] + 1):
        sample = copy.deepcopy(world)
        for keys, value in effects(world, action, tick).items():
            node = sample
            for key in keys[:-1]: node = node[key]
            node[keys[-1]] = value
        for name in (who, target_id):
            person = sample['actors'][name]; old = previous['actors'][name]
            path_scenes = passage_scenes or {old['scene_id'], person['scene_id']}
            for part in ('position_xyz', 'reach_origin_xyz'):
                for scene in path_scenes:
                    p._need(not p._blocked(sample, scene, old[part], person[part]), path, 'barrier blocks supported actor path')
            for hand in ('left', 'right'):
                p._need(p._distance(p._hand(person, hand), person['reach_origin_xyz']) <= person['reach_m'] + .2,
                        path, 'intermediate supported pose exceeds declared reach')
                for scene in path_scenes:
                    p._need(not p._blocked(sample, scene, p._hand(old, hand), p._hand(person, hand)),
                            path, 'barrier blocks support hand path')
        check_relations(sample, path)
        previous = sample
    return info


def effects(initial, action, tick):
    p = _p(); who = action['actor']; tid = action['target_actor']; k = action['kind']
    a = copy.deepcopy(initial['actors'][who]); target = copy.deepcopy(initial['actors'][tid])
    f = (tick - action['start']) / (action['end'] - action['start']); end = tick == action['end']
    relation = copy.deepcopy(a['action_state'].get('body_support'))
    if k == 'embrace':
        target = _pose(target, initial['points'][action['point']]['position_xyz'], action['landmarks'], action['posture'], f, end)
        for hand, offset in action['contacts'].items():
            p._set_hand(a, hand, p._lerp(p._hand(initial['actors'][who], hand), contact_point(target, offset), f))
        if end: relation = {'target_actor': tid, 'mode': 'embrace', 'contacts': copy.deepcopy(action['contacts'])}
    elif k == 'lift_actor':
        a = _pose(a, initial['points'][action['carrier_point']]['position_xyz'], action['carrier_landmarks'], '站立', f, end)
        target = _pose(target, initial['points'][action['point']]['position_xyz'], action['landmarks'], target['posture'], f, end)
        for hand, offset in relation['contacts'].items(): p._set_hand(a, hand, contact_point(target, offset))
        if end: relation['mode'] = 'carry'
    elif k == 'release_embrace':
        if end: relation = None
    else:
        dest = initial['points'][action['to']] if k == 'carry_actor' else initial['points'][_passage(initial, action)['entry']]
        shift = p._sub(p._lerp(a['position_xyz'], dest['position_xyz'], f), a['position_xyz'])
        for person in (a, target):
            person['position_xyz'] = p._add(person['position_xyz'], shift)
            person['reach_origin_xyz'] = p._add(person['reach_origin_xyz'], shift)
            if end: person['scene_id'] = dest['scene_id']
    result = {}
    for name, person in ((who, a), (tid, target)):
        for key in ('position_xyz', 'reach_origin_xyz', 'eye_height', 'posture', 'hands', 'scene_id'):
            result[('actors', name, key)] = copy.deepcopy(person[key])
    result[('actors', who, 'action_state', 'body_support')] = relation
    return result


def check_relations(world, path):
    p = _p(); participants = set()
    for who, relation in _relations(world):
        p._obj(relation, {'target_actor', 'mode', 'contacts'}, path)
        target_id = relation['target_actor']
        p._need(target_id in world['actors'] and target_id != who and not {who, target_id} & participants,
                path, 'support participants must be unique actors; no cycles or chains')
        participants.update((who, target_id)); a = world['actors'][who]; target = world['actors'][target_id]
        p._need(relation['mode'] in {'embrace', 'carry'} and a['scene_id'] == target['scene_id'], path, 'support endpoints must remain together in one scene')
        p._need(p._distance(a['reach_origin_xyz'], target['reach_origin_xyz']) <= .9, path, 'supported body separated from carrier')
        p._obj(relation['contacts'], {'left', 'right'}, path)
        for hand, offset in relation['contacts'].items():
            p._need(not any(prop['anchor'] == {'actor': who, 'hand': hand} for prop in world['props'].values()), path, 'support hand cannot also hold a prop')
            point = contact_point(target, offset)
            p._need(p._distance(p._hand(a, hand), point) <= p.EPS, path, 'actual two-person support contact was broken')
            p._reach(world, who, target['scene_id'], point, path)
        if relation['mode'] == 'carry':
            p._need(a['posture'] == '站立' and target['position_xyz'][2] - a['position_xyz'][2] >= .2,
                    path, 'carry requires actual airborne target and standing carrier')


def sentence(action, info, *, complete=True):
    a = action['actor']; target = action['target_actor']; k = action['kind']
    if k == 'embrace': return ('搂住', f'{a}双臂搂住{target}。') if complete else ('伸向', f'{a}双臂伸向{target}，将其逐渐搂近。')
    if k == 'lift_actor': return ('抱起', f'{a}双臂托住并抱起{target}，起身站稳。') if complete else ('抱起', f'{a}双臂持续托住并逐渐抱起{target}，同时缓缓起身。')
    if k == 'release_embrace': return ('松开', f'{a}松开搂住{target}的双臂。') if complete else ('松开', f'{a}逐渐松开搂住{target}的双臂。')
    if k == 'carry_actor': return ('走到' if complete else '走向'), f'{a}双臂持续抱着{target}' + (f'走到{action["to"]}。' if complete else f'走向{action["to"]}。')
    return ('进入' if complete else '过门'), f'{a}持续抱着{target}经{action["passage"]}' + (f'一同进入{info["destination_scene"]}。' if complete else '一同过门。')


def static_actor(actor, name, *, view=None):
    rel = actor['action_state'].get('body_support')
    # The world retains the relation during a close crop. A static image prompt
    # may describe it only when its actual contact/body landmarks are visible.
    if rel is None or view is None or not visible({'view': view}, {'actor': name, 'target_actor': rel['target_actor']}):
        return ''
    return f'{name}双臂仍{"抱着" if rel["mode"] == "carry" else "搂住"}{rel["target_actor"]}。'


def visible(sample, action):
    rows = sample['view']['actors']; owner = rows.get(action['actor'], {}); target = rows.get(action['target_actor'], {})
    return bool(owner.get('head') and set(owner.get('hands', [])) == {'left', 'right'} and target.get('head') and target.get('feet'))


def validate_anchor(world, identity, prop, occupied):
    """Validate only new anchors. Existing anchors stay with their old owner."""
    p = _p(); anchor = prop['anchor']
    if not isinstance(anchor, dict): return False
    if set(anchor) == {'container'}:
        parent = anchor['container']
        p._need(isinstance(parent, str) and parent in world['props'] and parent != identity,
                'prop.anchor', 'container must be a different real prop')
        container = world['props'][parent].get('container')
        p._need(_is_sleeve(container), 'prop.anchor', 'requires a declared single-item opaque sleeve')
        key = ('container', parent)
        p._need(key not in occupied, 'prop.anchor', 'opaque sleeve capacity is already occupied'); occupied.add(key)
        return True
    if set(anchor) == {'actor', 'mount', 'offset_xyz'}:
        p._need(isinstance(anchor['actor'], str) and anchor['actor'] in world['actors'] and
                isinstance(anchor['mount'], str) and anchor['mount'] in {'left_waist', 'right_waist'}, 'prop.anchor', 'unsupported mounted attachment')
        offset = p._xyz(anchor['offset_xyz'], 'prop.anchor.offset_xyz'); actor = world['actors'][anchor['actor']]
        sign = -1 if anchor['mount'] == 'left_waist' else 1
        p._need(.15 <= sign * offset[0] <= .5 and abs(offset[1]) <= .3 and
                0 < offset[2] and actor['eye_height'] - .9 <= offset[2] <= actor['eye_height'] - .3,
                'prop.anchor', 'waist mount requires explicit bounded body-local landmarks')
        p._need(actor['posture'] == '站立', 'prop.anchor', 'waist mount currently requires explicit standing landmarks')
        p._need(_is_sleeve(prop.get('container')), 'prop.anchor', 'waist mount currently supports opaque sleeves only')
        key = (anchor['actor'], anchor['mount'])
        p._need(key not in occupied, 'prop.anchor', 'waist attachment is already occupied'); occupied.add(key)
        return True
    return False


def _is_sleeve(value):
    return (isinstance(value, dict) and set(value) == {'kind', 'capacity'} and
            value['kind'] == 'opaque_sleeve' and type(value['capacity']) is int and value['capacity'] == 1)


def validate_containers(world):
    p = _p()
    for prop in world['props'].values():
        if 'container' in prop:
            p._need(_is_sleeve(prop['container']), 'prop.container', 'unsupported container kind or capacity')
            p._need(prop['physical']['material'] == 'cloth' and prop['physical']['integrity'] == 'intact',
                    'prop.container', 'opaque sleeve needs actual intact cloth material')
            p._need('container' not in prop['anchor'], 'prop.container', 'nested containers are outside this finite model')


def special_anchor(world, prop, seen=None):
    p = _p(); anchor = prop['anchor']
    if set(anchor) == {'container'}:
        seen = set() if seen is None else set(seen); parent = anchor['container']
        p._need(len(seen) < 16 and parent in world['props'] and parent not in seen, 'prop.anchor', 'unknown or cyclic container chain')
        seen.add(parent); container = world['props'][parent]
        p._need(_is_sleeve(container.get('container')) and
                container.get('physical', {}).get('integrity', 'intact') == 'intact', 'prop.anchor', 'container relation lost its intact sleeve')
        return p._anchor(world, container, seen)
    if set(anchor) == {'actor', 'mount', 'offset_xyz'}:
        actor = world['actors'][anchor['actor']]
        p._need(actor['posture'] == '站立' and actor['eye_height'] - .9 <= anchor['offset_xyz'][2] <= actor['eye_height'] - .3,
                'prop.anchor', 'posture change requires a separately supported waist-mount transition')
        return actor['scene_id'], p._add(actor['position_xyz'], _offset(actor, anchor['offset_xyz']))
    return None


def prop_visible(world, identity, seen=None):
    prop = world['props'].get(identity)
    if not prop: return False
    anchor = prop['anchor']
    if 'container' in anchor: return False
    if 'prop' in anchor:
        seen = set() if seen is None else set(seen)
        if identity in seen: return False
        seen.add(identity)
        return prop_visible(world, anchor['prop'], seen)
    return True


def static_prop(world, identity):
    prop = world['props'][identity]; anchor = prop['anchor']
    if 'mount' in anchor:
        side = '左腰侧' if anchor['mount'] == 'left_waist' else '右腰侧'
        return f'{identity}固定在{anchor["actor"]}{side}，为不透明布套，内部物件不可见。'
    return ''
