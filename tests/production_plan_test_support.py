"""Explicit public synthetic plan fixture; never used by application code."""
import copy
import hashlib
import json
from production_plan import CONTRACT, decision_values, digest, runtime_context


def frozen_plan(payload, graph, texts):
    graph = json.loads(graph) if isinstance(graph, bytes) else graph
    # Explicit neutral poses for these public synthetic fixtures only. Runtime
    # inputs still have to author their own source-grounded performance choices.
    for actor in payload['initial_world']['actors'].values():
        actor.setdefault('action_state', {'contacts': {}, 'wet_parts': [], 'clean_parts': [],
            'hand_shapes': {'left': 'open', 'right': 'open'}, 'expression': '眉眼舒展，唇线自然',
            'claps': 0, 'kowtows': 0})
    if graph.get('action_plans'):
        from source_action_plans import world_sha256
        graph['action_plans']['initial_world_sha256'] = world_sha256(payload['initial_world'])
    decisions = payload['project']['prevention_evidence']['source_decisions']
    uses = {u['id']: u for u in decisions['uses']}
    from production_decisions import _world, _camera, _view
    world = _world(payload['initial_world'], {}, check_source_refs=False)
    knowledge, initial, shots, segments = [], {}, [], []
    story_tick = 0
    for index, shot in enumerate(payload['shots']):
        consumed = {a['source_use_id'] for a in shot['actions'] if a.get('source_use_id')}
        for prop in payload['initial_world']['props'].values():
            if prop['written_ref']:
                consumed.update(w['use_id'] for w in decisions['written_objects'] if w['id'] == prop['written_ref'])
        for a in [*shot['transition'].get('bridge',{}).get('actions',[]),*shot['actions']]:
            u = uses.get(a.get('source_use_id'))
            actor = u['actor'] if a['kind'] in {'speak','narrate','inner_voice'} else a.get('actor')
            fact_ids = list(u['fact_ids']) if u else []
            stance = 'narrator' if a['kind'] == 'narrate' else 'known' if fact_ids else 'not_applicable'
            knowledge.append({'action_id':a['id'],'actor':actor,'fact_ids':fact_ids,
                              'stance':stance,'evidence_fact_ids':[]})
            if actor and fact_ids: initial.setdefault(actor,set()).update(fact_ids)
        purpose = sorted({fid for uid in consumed for fid in uses[uid]['fact_ids']})
        view = _view(world, _camera(world, shot['camera'], shot['scene_id'], 'fixture.camera'), shot['scene_id'])
        subjects = set(view['actors']) | set(view['props'])
        speakers = {uses[a['source_use_id']]['actor'] for a in shot['actions'] if a['kind'] == 'speak'}
        anchor = shot['camera']['target']
        design = {'framing_reason': '合成控制：固定机位观察已登记的主体和源文信息。',
            'cut_reason': '合成控制：在明确的本镜信息边界切换。',
            'spatial_anchors': {name: anchor for name in sorted(subjects)},
            'performance': [{'actor': name, 'fact_ids': purpose,
                'stimulus': '合成控制所列源文信息。', 'visible_choice': '眉眼舒展，唇线自然',
                'gesture_choice': '只执行已列实际动作。', 'voice_choice': '按实际音轨；无发声时无音色。'}
                for name in sorted(set(view['actors']) | speakers)], 'duration_exception': None}
        shots.append({'shot_id':shot['shot_id'],'purpose_fact_ids':purpose,
                      'required_action_ids':[a['id'] for a in shot['actions']],
                      'audio_action_ids':[a['id'] for a in shot['actions'] if a['kind'] in {'speak','narrate','inner_voice'}],
                      'source_use_ids':sorted(consumed),'visible':[], 'design': design})
        relation = shot['transition']['relation']
        story_tick += shot['transition'].get('bridge', {}).get('ticks', 0)
        segments.append({'id': 'segment-' + str(index), 'shot_ids': [shot['shot_id']],
            'time': {'relation': 'initial' if index == 0 else 'continuous' if relation == 'continuous' else 'later',
                     'reference_segment_id': None if index == 0 else 'segment-' + str(index - 1),
                     'start_tick': story_tick, 'evidence_fact_ids': purpose},
            'purpose': 'advance', 'purpose_fact_ids': purpose,
            'orientation_subject_ids': sorted(subjects),
            'entry_basis': '合成控制：呈现登记场景和实际入画主体。',
            'cut_basis': '合成控制：依次交付上述源文事实。', 'audio_bridge': 'none'})
        story_tick += shot['ticks'][-1]
    causal_links = [{'fact_id': fact['id'], 'requires_fact_id': dep, 'treatment': 'shown',
        'establishment_shot_id': next(s['shot_id'] for s in shots if dep in s['purpose_fact_ids']),
        'prior_delivery_id': None, 'reveal_shot_id': None, 'future_source_span': None,
        'reason': '合成控制中的前因必须由实际动作、声音或当前纸面先行交付。'}
        for fact in graph['facts'] for dep in sorted(set(fact['depends_on']) | set(fact['conditions']))]
    return {'contract':CONTRACT,
            'context':{**runtime_context(),'source_sha256':{sid:hashlib.sha256(text.encode()).hexdigest() for sid,text in texts.items()},
                       'initial_world_sha256':digest(payload['initial_world'])},
            'decisions':[{'path':path,'basis':'director_choice',
                          'fact_ids': [graph['facts'][0]['id']] if path.startswith('/initial_world/points/') else [],
                          'source_spans':[],'value':copy.deepcopy(value)} for path,value in decision_values(payload).items()],
            'knowledge':{'initial':[{'actor':actor,'fact_ids':sorted(fids),'evidence_fact_ids':sorted(fids)} for actor,fids in initial.items()],
                         'acquisitions':[],'uses':knowledge},'shots':shots,
            'narrative': {'contract': 'scene-narrative-v1', 'segments': segments,
                          'causal_links': causal_links, 'prior_deliveries': []}}


def with_plan(payload, graph_bytes, texts):
    graph = json.loads(graph_bytes)
    graph['production_plan'] = frozen_plan(payload, graph, texts)
    return json.dumps(graph, ensure_ascii=False, sort_keys=True).encode('utf-8')
