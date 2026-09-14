"""Explicit public synthetic plan fixture; never used by application code."""
import copy
import hashlib
import json
from production_plan import CONTRACT, decision_values, digest, runtime_context


def frozen_plan(payload, graph, texts):
    graph = json.loads(graph) if isinstance(graph, bytes) else copy.deepcopy(graph)
    decisions = payload['project']['prevention_evidence']['source_decisions']
    uses = {u['id']: u for u in decisions['uses']}
    knowledge, initial, shots = [], {}, []
    for shot in payload['shots']:
        consumed = {a['source_use_id'] for a in shot['actions'] if a.get('source_use_id')}
        for prop in payload['initial_world']['props'].values():
            if prop['written_ref']:
                consumed.update(w['use_id'] for w in decisions['written_objects'] if w['id'] == prop['written_ref'])
        for a in shot['actions']:
            u = uses.get(a.get('source_use_id'))
            actor = u['actor'] if a['kind'] in {'speak','narrate'} else a.get('actor')
            fact_ids = list(u['fact_ids']) if u else []
            stance = 'narrator' if a['kind'] == 'narrate' else 'known' if fact_ids else 'not_applicable'
            knowledge.append({'action_id':a['id'],'actor':actor,'fact_ids':fact_ids,
                              'stance':stance,'evidence_fact_ids':[]})
            if actor and fact_ids: initial.setdefault(actor,set()).update(fact_ids)
        purpose = sorted({fid for uid in consumed for fid in uses[uid]['fact_ids']})
        shots.append({'shot_id':shot['shot_id'],'purpose_fact_ids':purpose,
                      'required_action_ids':[a['id'] for a in shot['actions']],
                      'audio_action_ids':[a['id'] for a in shot['actions'] if a['kind'] in {'speak','narrate'}],
                      'source_use_ids':sorted(consumed),'visible':[]})
    return {'contract':CONTRACT,
            'context':{**runtime_context(),'source_sha256':{sid:hashlib.sha256(text.encode()).hexdigest() for sid,text in texts.items()},
                       'initial_world_sha256':digest(payload['initial_world'])},
            'decisions':[{'path':path,'basis':'director_choice','fact_ids':[],
                          'source_spans':[],'value':copy.deepcopy(value)} for path,value in decision_values(payload).items()],
            'knowledge':{'initial':[{'actor':actor,'fact_ids':sorted(fids),'evidence_fact_ids':sorted(fids)} for actor,fids in initial.items()],
                         'acquisitions':[],'uses':knowledge},'shots':shots}


def with_plan(payload, graph_bytes, texts):
    graph = json.loads(graph_bytes)
    graph['production_plan'] = frozen_plan(payload, graph, texts)
    return json.dumps(graph, ensure_ascii=False, sort_keys=True).encode('utf-8')
