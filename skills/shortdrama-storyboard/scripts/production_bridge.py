"""Explicit omitted travel/time between shots; never a visible plot delivery.

Only bounded staging actions are accepted. Source events, dialogue, impacts and
arbitrary state replacement are excluded. Frozen transition decisions own the
bridge, and the full world trace is retained for independent continuity review.
"""
import copy


def run_bridge(world, bridge, *, transition, sources, source_execution, prior_events, prior_projection, elapsed):
    import production_decisions as p
    import production_actions as x
    path='transition.bridge'
    p._obj(bridge, {'ticks','actions'}, path)
    p._need(transition['relation'] in {'later_same_place','different_place','episode_boundary'}, path,
            'a continuous cut cannot conceal an inter-shot bridge')
    limit=bridge['ticks']; p._need(type(limit) is int and 1<=limit<=1200, path, 'bridge simulation needs 1–1200 integer tenths')
    p._need(isinstance(bridge['actions'],list) and bool(bridge['actions']), path, 'bridge needs explicit actions, not endpoint states')
    actions=copy.deepcopy(bridge['actions']); ids=set(); intervals=[]; prepared={}; trace=[]
    for i,a in enumerate(actions):
        ap=f'{path}.actions[{i}]'
        p._need(isinstance(a,dict) and a.get('kind') in x.BRIDGE_KINDS, ap, 'plot/audio/source events are not eligible for an omitted bridge')
        p._obj(a,p._action_keys(a['kind']),ap)
        p._need(a['source_use_id'] is None, ap, 'bridge staging cannot consume a source fact or substitute for visible delivery')
        p._name(a['id'],ap); p._name(a['actor'],ap)
        p._need(a['id'] not in ids and a['id'] not in prior_events and a['actor'] in world['actors'],ap,'duplicate event or unknown actor')
        ids.add(a['id']); lo=p._tick(a['start'],ap); hi=p._tick(a['end'],ap)
        p._need(lo<hi<=limit,ap,'bridge action outside declared omitted interval')
        p._need(isinstance(a['after'],list) and len(set(a['after']))==len(a['after']),ap,'requires unique action prerequisites')
        channels=p._channels(a)
        for other,other_channels in intervals:
            p._need(hi<=other['start'] or lo>=other['end'] or not channels & other_channels,ap,'overlapping bridge writes')
        intervals.append((a,channels))
    by_id={a['id']:a for a in actions}
    for a in actions:
        for dep in a['after']:
            p._need((dep in by_id and by_id[dep]['end']<=a['start']) or dep in prior_events,
                    path,'bridge dependency is missing or unfinished')
    initial_hash=p._hash(world)
    for tick in range(limit+1):
        for a in sorted((a for a in actions if a['start']<tick<=a['end']),key=lambda a:a['kind']=='look'):
            p._apply_source_action(world,a,prepared[a['id']],tick,path,source_execution)
        for a in actions:
            if a['start']==tick:
                prepared[a['id']]=p._prepare(world,a,sources,path,bridge=True)
                p._apply_source_action(world,a,prepared[a['id']],tick,path,source_execution)
        p._check_gazes(world,path+'.gaze'); x.check_contacts(world,path+'.contacts')
        trace.append({'tick':tick,'world_sha256':p._hash(world)})
    records=[]
    for a in sorted(actions,key=lambda a:(a['end'],a['id'])):
        p._need(world['completed'].count(a['id'])==1,path,'bridge event did not finish exactly once')
        prior_events[a['id']]=elapsed
        inherited=[]
        for dep in a['after']: inherited.extend(prior_projection.get(dep,[]))
        prior_projection[a['id']]=list(dict.fromkeys(inherited))
        records.append({'id':a['id'],'kind':a['kind'],'actor':a['actor'],'start':a['start']/10,'end':a['end']/10,
                        'source_use_id':None,'projection_status':'omitted_bridge','visible_projection_ids':[],
                        'effect_occurrences':1,'before_sha256':prepared[a['id']]['before_world_sha256']})
    return {'contract':'inter-shot-state-bridge-v1','initial_world_sha256':initial_hash,'final_world_sha256':p._hash(world),
            'minimum_omitted_seconds':limit/10,'screen_seconds':0,'actions':records,'trace':trace,
            'source_delivery':False,'semantic_status':'pending_independent_review'}
