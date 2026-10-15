"""Bounded source-authored fantasy effects; no material or fluid simulation."""
import copy

FIELDS = {'shatter_surface': {'prop', 'hand', 'to'}, 'phantom_fade': {'to', 'glimmer'}}


def validate_actor(value, path):
    from production_decisions import _obj, _need, _num
    _obj(value, {'opacity', 'extent_m', 'glimmer'}, path)
    _need(0 <= _num(value['opacity'], path) <= 1, path, 'opacity outside [0,1]')
    _need(0 < _num(value['extent_m'], path) <= 1, path, 'phantom effect must remain local')
    _need(type(value['glimmer']) is bool, path, 'explicit glimmer boolean required')


def prepare(world, action, info, path, *, bridge):
    import production_decisions as p
    p._need(not bridge and action['source_use_id'] is not None, path, 'optical event requires frozen physical source')
    a = world['actors'][action['actor']]
    if action['kind'] == 'phantom_fade':
        p._need('optical' in a, path, 'only an explicitly registered phantom can fade')
        p._need(0 <= p._num(action['to'], path) < a['optical']['opacity'], path, 'fade must reduce actual opacity; resurrection unsupported')
        p._need(type(action['glimmer']) is bool, path, 'explicit local glimmer choice required')
    else:
        prop = world['props'].get(action['prop'])
        p._need(prop and 'surface' in prop and prop['physical']['integrity'] == 'intact', path, 'requires an intact registered optical surface')
        p._need(action['hand'] in {'left', 'right'}, path, 'unknown hand')
        p._free_hand(world, action['actor'], action['hand'], path)
        scene, position = p._anchor(world, prop)
        p._reach(world, action['actor'], scene, position, path)
        ds, destination = p._target(world, {'kind':'point','id':action['to']}, path)
        p._need(ds == scene and destination[2] == 0 and 0 < position[2] <= 2.5 and
                p._distance(position[:2], destination[:2]) <= prop['surface']['extent_m'], path,
                'fragments must land on the same local floor')
        p._need(action['end'] - action['start'] >= 10, path, 'strike and fall need at least one second')
    info['before_all'] = copy.deepcopy(world)
    return info


def effects(initial, action, tick):
    import production_decisions as p
    who = action['actor']; a = initial['actors'][who]
    f = (tick-action['start'])/(action['end']-action['start'])
    if action['kind'] == 'phantom_fade':
        value = copy.deepcopy(a['optical'])
        value['opacity'] = a['optical']['opacity'] + (action['to']-a['optical']['opacity'])*f
        if tick > action['start']: value['glimmer'] = action['glimmer']
        if value['opacity'] == 0: value['glimmer'] = False
        return {('actors',who,'optical'):value}
    prop = initial['props'][action['prop']]; scene, origin = p._anchor(initial, prop)
    changed = copy.deepcopy(a)
    p._set_hand(changed, action['hand'], p._lerp(p._hand(a, action['hand']), origin, min(1.,f*2)))
    physical = copy.deepcopy(prop['physical']); surface = copy.deepcopy(prop['surface'])
    anchor = copy.deepcopy(prop['anchor'])
    if f >= .5:
        physical.update(integrity='broken', contents='none')
        surface.update(pattern='still',glow='none',mist='none')
        destination = initial['points'][action['to']]['position_xyz']
        anchor = {'point':action['to']} if tick == action['end'] else {
            'free':{'scene_id':scene,'position_xyz':p._lerp(origin,destination,(f-.5)*2)}}
    return {('actors',who,'hands',action['hand']):changed['hands'][action['hand']],
            ('props',action['prop'],'physical'):physical, ('props',action['prop'],'surface'):surface,
            ('props',action['prop'],'anchor'):anchor}


def sentence(action, info, lo=None, hi=None):
    a = action['actor']
    if action['kind'] == 'phantom_fade':
        end = action['to']
        finish = hi is None or hi == action['end']
        text = f'{a}身形逐渐变得透明。'
        if action['glimmer']:
            text += f'{a}身周局部微光飘摇。'
        if end == 0 and finish: text += f'{a}在本动作末彻底消散，人物形体及身周微光消失。'
        return '变得透明', text
    impact = action['start'] + (action['end']-action['start'])/2
    hand = '左手' if action['hand']=='left' else '右手'
    if hi is not None and hi <= impact:
        return '挥向', f"{a}抬起{hand}，用力挥向{action['prop']}。"
    if lo is not None and lo >= impact:
        return '停在', f"{a}{hand}停在挥击末端；{action['prop']}镜片四散下落至{action['to']}，范围限于原镜周围。"
    return '挥向', f"{a}抬起{hand}用力挥向{action['prop']}，触及镜面时古镜碎裂，镜片四散落到{action['to']}。"


def visible(sample, action):
    import production_decisions as p
    actor = sample['world']['actors'][action['actor']]
    shown = sample['view']['actors'].get(action['actor'], {})
    if action['kind'] == 'phantom_fade':
        return bool(shown.get('head') and shown.get('feet')) or (
            sample['tick'] == action['end'] and actor['optical']['opacity'] == 0)
    return action['hand'] in shown.get('hands', []) and action['prop'] in sample['view']['props']


def static_actor(actor, name):
    value = actor.get('optical')
    if not value: return ''
    state = '轮廓完整、形体不透明' if value['opacity'] == 1 else '身形半透明、轮廓仍可辨'
    light = '身周有局部细微光点。' if value['glimmer'] else ''
    return f'{name}{state}。' + light
