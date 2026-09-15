"""Shared episode checks over the actual timeline, not model-reported totals.

Fragment diagnostics never certify an episode. Semantic labels and source
citations remain assertions for the independent reviewer, not semantic proof.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

CONTRACT = 'episode-production-contract-v1'
REF = Path(__file__).resolve().parents[1] / 'references'
DELIVERY_FIELDS = ('画面内容', '起始状态', '心理音轨', '环境音效', '环境动态')


def _delivery_interval(evidence, shot_id, delivery, source_ids, *, verified):
    """Resolve an exact delivery in a replayed trace; never trust beat clocks.

    Sub-quotes conservatively occupy the whole bound event. Shorter spoken
    delivery needs its own real utterance event, not a claimed timestamp.
    Unsupported modalities remain explicit; source labels alone are no proof.
    """
    if not verified:
        raise ValueError('delivery requires replayed production evidence')
    refs = delivery.get('event_ids')
    if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x for x in refs) or len(set(refs)) != len(refs):
        raise ValueError('delivery.event_ids requires unique actual event identities')
    proof = evidence.get('production_derivation') or {}
    trace = proof.get('action_trace') or {}
    uses = (proof.get('source_compilation') or {}).get('uses_by_id', {})
    shot = next((s for s in trace.get('shots', []) if s.get('shot_id') == shot_id), {})
    actions = {x['id']: x for x in shot.get('actions', [])}
    events = {x['id']: x for x in trace.get('world_events', []) if x.get('shot_id') == shot_id}
    spans = []; actual_sources = set()
    for identity in refs:
        state = next((s for s in shot.get('current_written_deliveries', []) if s['id'] == identity), None)
        if state is not None:
            use = uses.get(state['source_use_id'])
            if not use or delivery['field'] != state['field'] or delivery['quote'] not in state['text']:
                raise ValueError('current written delivery differs from actual initial visible state')
            actual_sources.update(s['source_id'] for fact in use.get('source_constraints', []) for s in fact.get('supports', []))
            spans.append((seconds(state['start']), seconds(state['end'])))
            continue
        event = events.get(identity); action = actions.get(identity)
        if event is None or action is None:
            raise ValueError('delivery event is absent from the owning shot execution')
        use = uses.get(action.get('source_use_id'))
        if not use:
            raise ValueError('delivery event has no supported source-use binding; not_supported')
        sound = next((s for s in shot.get('environment_binding', {}).get('sound_bindings', [])
                      if s['action_id'] == identity and delivery['field'] == '环境音效'), None)
        field = '环境音效' if sound else '心理音轨' if event.get('kind') in {'narrate', 'inner_voice'} else '画面内容'
        if field != delivery['field'] or action.get('projection_status') == 'hidden':
            raise ValueError('delivery field or actual visibility differs from the event')
        actual = sound['rendered_text'] if sound else use['text'] if event.get('kind') in {'speak', 'narrate', 'inner_voice'} else action.get('world_action_description', '')
        if delivery['quote'] not in actual:
            raise ValueError('delivery quote is not part of the referenced event')
        actual_sources.update(s['source_id'] for fact in use.get('source_constraints', []) for s in fact.get('supports', []))
        spans.append((seconds(event['start']), seconds(event['end'])))
    if set(source_ids) != actual_sources:
        raise ValueError('delivery sources differ from the actual event source bindings')
    _, merged = union_seconds(spans)
    if len(merged) != 1:
        raise ValueError('delivery must be a contiguous actual event interval')
    return merged[0]


def policy():
    return json.loads((REF/'rule_registry.json').read_text(encoding='utf-8'))['runtime']['episode_rule_policy']


def chapter_required(obj):
    """One trigger for machine and independent-audit gates; no mode downgrades."""
    intake = obj.get('source_ingestion')
    evidence = obj.get('prevention_evidence')
    contract = evidence.get('episode_contract') if isinstance(evidence, dict) else None
    return (isinstance(obj.get('chapter_intake'), dict)
            or isinstance(intake, dict) and (intake.get('mode') == 'chapter_to_storyboard'
                or any(k in intake for k in ('chapter_id', 'complete_read', 'chunks_read', 'chapter_goal')))
            or isinstance(contract, dict) and contract.get('source_kind') == 'novel')


def episode_id(shot, obj=None):
    value = shot.get('episode_id') or (obj or {}).get('episode_id')
    if value: return str(value)
    found = re.match(r'(EP\d+)[-_:]', str(shot.get('镜头编号') or ''), re.I)
    return found[1].upper() if found else '__unidentified_episode__'


def seconds(value):
    if isinstance(value, bool): raise ValueError('boolean is not duration')
    try: number = Decimal(str(value))
    except (InvalidOperation, TypeError): raise ValueError('duration must be finite numeric seconds')
    if not number.is_finite() or number < 0: raise ValueError('duration must be finite nonnegative seconds')
    return number


def union_seconds(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]: merged[-1][1] = max(end, merged[-1][1])
        else: merged.append([start, end])
    return sum((end-start for start,end in merged), Decimal(0)), merged


def delivery_text(shot):
    return '\n'.join(str(shot.get(f) or '') for f in DELIVERY_FIELDS)


def _inspect_episodes(obj, shots=None, *, timing=None, rules=None, production_verified=False, fragment=False):
    p = rules or policy()
    shots = shots if shots is not None else obj.get('shots', [])
    evidence = obj.get('prevention_evidence') or {}
    contract = evidence.get('episode_contract')
    faults, results = [], []
    def fault(code, loc, message, status='fail'):
        faults.append({'error_code':code,'location':loc,'evidence':message,'status':status})
    groups, by_id, offsets = defaultdict(list), {}, {}
    for i,s in enumerate(shots):
        if not isinstance(s,dict): continue
        sid = str(s.get('镜头编号') or s.get('shot_id') or i)
        ep = episode_id(s,obj)
        try: duration=seconds(s.get('时长'))
        except ValueError as e:
            fault('E-EPISODE-DURATION-001',sid,str(e));continue
        offset = sum((x[2] for x in groups[ep]),Decimal(0))
        groups[ep].append((sid,s,duration)); by_id[sid]=(ep,s,duration);offsets[sid]=offset
    if contract is not None and (not isinstance(contract,dict) or contract.get('contract') != CONTRACT
                                or contract.get('scope') not in {'fragment','complete_episodes','complete_series'}):
        fault('E-EPISODE-CONTRACT-001','episode_contract','unsupported or malformed episode contract')
        contract = None
    complete = not fragment and isinstance(contract,dict) and contract.get('scope') != 'fragment'
    if complete and not groups:
        fault('E-EPISODE-CONTRACT-001','episode_contract','an empty candidate cannot certify a complete episode or series','insufficient_evidence')
    declared = contract.get('episodes',[]) if isinstance(contract,dict) else []
    if not isinstance(declared,list) or any(not isinstance(x,dict) for x in declared):
        fault('E-EPISODE-CONTRACT-001','episode_contract.episodes','episode rows must be objects');declared=[]
    ids=[x.get('episode_id') for x in declared]
    if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):
        fault('E-EPISODE-CONTRACT-001','episode_contract.episodes','missing or duplicated episode identities')
    declared_by_id={str(x.get('episode_id')):x for x in declared}
    if complete and set(declared_by_id)!=set(groups):
        fault('E-EPISODE-CONTRACT-001','episode_contract.episodes','complete episode identities must exactly match actual shots')
    if isinstance(contract,dict) and contract.get('profile_id') != p['profile_id']:
        fault('E-EPISODE-CONTRACT-001','episode_contract.profile_id','profile must match the server runtime; candidate thresholds are not authoritative')
    if isinstance(contract,dict) and any(k in contract for k in ('min_seconds','max_seconds','thresholds','overrides')):
        fault('E-EPISODE-CONTRACT-001','episode_contract','caller-supplied threshold overrides are not accepted')
    source_rows=evidence.get('source_ledger') or []
    source_rows=source_rows if isinstance(source_rows,list) else []
    source_map={str(x.get('source_id')):x for x in source_rows if isinstance(x,dict)}
    utterances=(timing or {}).get('utterances',[])
    for ep,rows in groups.items():
        total=sum((x[2] for x in rows),Decimal(0)); row_ids={x[0] for x in rows}
        details={'episode_id':ep,'seconds':float(total),'shot_count':len(rows),
                 'scope_status':'complete' if complete else 'insufficient_evidence',
                 'semantic_status':'pending_independent_review','actual_video_status':'not_measured'}
        results.append(details)
        if total>seconds(p['max_seconds']):
            fault('E-EPISODE-DURATION-001','episode:'+ep,f'{total}s exceeds {p["max_seconds"]}s; replan boundaries without dropping source content')
        if complete and total<seconds(p['min_seconds']):
            fault('E-EPISODE-DURATION-001','episode:'+ep,f'{total}s below complete episode minimum {p["min_seconds"]}s')
        if total==0: continue
        for limit,name,threshold in [(1,'short_under_1_percent','short_under_1_percent'),(2,'short_under_2_percent','short_under_2_percent')]:
            count=sum(d<limit for _,_,d in rows)
            rate=Decimal(count)*100/len(rows);details[name]=float(rate)
            if complete and rate<seconds(p[threshold]):
                fault('E-EPISODE-RHYTHM-001','episode:'+ep,f'{name}={rate} below {p[threshold]}; only source-grounded functional cuts are valid')
        spans=[]
        for u in utterances:
            if not isinstance(u,dict) or str(u.get('shot_id')) not in row_ids or u.get('kind')!='dialogue':continue
            sid=str(u['shot_id'])
            try: a,b=seconds(u['start']),seconds(u['end'])
            except (KeyError,ValueError):continue
            if b>a and b<=by_id[sid][2]:spans.append((offsets[sid]+a,offsets[sid]+b))
        spoken,merged=union_seconds(spans)
        details['dialogue_seconds']=float(spoken);details['dialogue_percent']=float(spoken*100/total)
        silence=sorted([b-a for a,b in zip([Decimal(0)]+[b for _,b in merged],[a for a,_ in merged]+[total])])
        details['longest_dialogue_gap_seconds']=float(max(silence,default=total))
        details['dialogue_quarter_seconds']=[float(union_seconds([(max(a,total*i/4),min(b,total*(i+1)/4)) for a,b in merged if a<total*(i+1)/4 and b>total*i/4])[0]) for i in range(4)]
        if not complete:continue
        er=declared_by_id.get(ep,{})
        # Exceptional stories remain explicit, signed independent-review work;
        # a candidate's exemption flag never suppresses a hard finding.
        if spoken*100/total < seconds(p['dialogue_min_percent']):
            fault('E-EPISODE-DIALOGUE-001','episode:'+ep,f'actual role dialogue={details["dialogue_percent"]:.3f}% below {p["dialogue_min_percent"]}%; psychological audio does not substitute; source conflict requires scoped policy revision, not invented speech')
        core=er.get('core_actor')
        details['opening_face_status']='insufficient_evidence'
        if not isinstance(core,str) or not core.strip():
            fault('E-EPISODE-OPENING-001','episode:'+ep,'explicit core_actor required; never infer a legacy project character','insufficient_evidence')
        elif not production_verified:
            fault('E-EPISODE-OPENING-001','episode:'+ep,'face-window verification requires a replayed production plan','insufficient_evidence')
        else:
            payload=evidence.get('production_derivation',{})
            try: planned=json.loads(payload['source_graph_json'])['production_plan']['shots']
            except (KeyError,TypeError,ValueError):planned=[]
            found=False
            for item in planned:
                sid=str(item.get('shot_id'))
                if sid not in row_ids:continue
                for req in item.get('visible',[]):
                    if req.get('subject')==core and req.get('kind') in {'face','eyes'}:
                        start=offsets[sid]+seconds(req.get('start'))/10
                        end=offsets[sid]+seconds(req.get('end'))/10
                        if start<seconds(p['opening_face_seconds']) and end>start:found=True
            # The replayed engine exposes eye landmarks, not a face mesh or
            # projected facial area. Do not certify the historical >=30% rule
            # from eye visibility. That remains a required independent check.
            details['opening_face_status']='partial' if found else 'fail'
            details['opening_face_area_status']='pending_independent_review'
            details['opening_face_area_min_percent']=p['opening_face_area_min_percent']
            if not found:fault('E-EPISODE-OPENING-001','episode:'+ep,'verified core face window does not intersect the first 3 seconds')
        beats=er.get('beat_events')
        if not isinstance(beats,list) or not beats:
            fault('E-EPISODE-BEAT-001','episode:'+ep,'source-bound opening, advancement and ending events required','insufficient_evidence');beats=[]
        points=[];kind_spans=defaultdict(list);seen=set(); delivered_events=set()
        for j,b in enumerate(beats):
            loc=f'episode:{ep}.beat_events[{j}]'
            if not isinstance(b,dict):fault('E-EPISODE-BEAT-001',loc,'beat must be an object');continue
            sid=str(b.get('shot_id'));kind=b.get('kind');identity=b.get('id')
            src=b.get('source_ids'); delivery=b.get('delivery') or {}
            if not identity or identity in seen or sid not in row_ids or kind not in p['beat_kinds']:
                fault('E-EPISODE-BEAT-001',loc,'invalid/duplicate event identity, shot or kind');continue
            seen.add(identity)
            if not isinstance(src,list) or not src or not set(src)<=set(source_map):
                fault('E-EPISODE-BEAT-001',loc,'beat source IDs must exist');continue
            if any(sid not in source_map[s].get('target_shots',[]) for s in src):
                fault('E-EPISODE-BEAT-001',loc,'source-to-shot mapping differs');continue
            field=delivery.get('field');quote=delivery.get('quote')
            if field not in DELIVERY_FIELDS or not isinstance(quote,str) or not quote.strip() or quote not in str(by_id[sid][1].get(field,'')):
                fault('E-EPISODE-BEAT-001',loc,'actual delivery quote required in its owning AV field');continue
            try:a,bend=seconds(b['start']),seconds(b['end'])
            except (KeyError,ValueError):fault('E-EPISODE-BEAT-001',loc,'finite local event interval required');continue
            if bend<=a or bend>by_id[sid][2]:fault('E-EPISODE-BEAT-001',loc,'event outside shot interval');continue
            try:
                actual_a, actual_b = _delivery_interval(evidence, sid, delivery, src, verified=production_verified)
            except (ValueError, KeyError, TypeError) as exc:
                fault('E-EPISODE-BEAT-001',loc,str(exc),'insufficient_evidence');continue
            if (a, bend) != (actual_a, actual_b):
                fault('E-EPISODE-BEAT-001',loc,'declared beat clock differs from actual delivery execution')
            # Continue metrics using actual execution even when the declaration
            # was wrong, so one bad claim cannot hide the real missing hook.
            a, bend = actual_a, actual_b
            event_keys = set(delivery['event_ids'])
            if kind in {'opening_hook','advance','ending_hook','closure'}:
                if event_keys & delivered_events:
                    fault('E-EPISODE-BEAT-001',loc,'relabeling the same source delivery cannot create another advancement');continue
                delivered_events.update(event_keys)
            span=(offsets[sid]+a,offsets[sid]+bend);kind_spans[kind].append(span)
            if kind in {'opening_hook','advance','ending_hook','closure'}:points.append(span[0])
        details['beat_semantics']='not_certified_by_labels_or_quotes'
        if not any(a<seconds(p['opening_hook_seconds']) for a,_ in kind_spans['opening_hook']):
            fault('E-EPISODE-BEAT-001','episode:'+ep,'missing source-bound opening hook in opening window')
        gaps=[b-a for a,b in zip([Decimal(0)]+sorted(set(points)),sorted(set(points))+[total])]
        details['longest_advance_gap_seconds']=float(max(gaps,default=total))
        if max(gaps,default=total)>seconds(p['advance_gap_max_seconds']):
            fault('E-EPISODE-BEAT-001','episode:'+ep,'advancement gap exceeds configured window; repeated descriptions are not new facts')
        ending=er.get('ending')
        expected='closure' if ending=='finale' else 'ending_hook'
        if ending not in {'hook','finale'} or not any(b>total-seconds(p['ending_window_seconds']) for a,b in kind_spans[expected]):
            fault('E-EPISODE-BEAT-001','episode:'+ep,'ending hook or explicit finale closure must occur in the ending window')
        carry_shots = {b.get('shot_id') for b in beats if isinstance(b,dict) and b.get('kind')=='carryover'}
        if len(carry_shots)>p['carryover_max_shots'] or union_seconds(kind_spans['carryover'])[0]>seconds(p['carryover_max_seconds']):
            fault('E-EPISODE-BEAT-001','episode:'+ep,'opening carryover exceeds shot/time budget')
        # Source purpose and target shots are a separate enum, never S/A/B/C grades.
        functions=er.get('source_functions')
        if not isinstance(functions,list):
            fault('E-EPISODE-SOURCE-BUDGET-001','episode:'+ep,'complete source-function classification required','insufficient_evidence');functions=[]
        relevant={s for s,r in source_map.items() if set(r.get('target_shots',[])) & row_ids}
        covered=set();totals=defaultdict(set);micro_by_shot=defaultdict(set)
        for j,item in enumerate(functions):
            loc=f'episode:{ep}.source_functions[{j}]'
            if not isinstance(item,dict):fault('E-EPISODE-SOURCE-BUDGET-001',loc,'classification must be an object');continue
            source=item.get('source_id');kind=item.get('kind');targets=item.get('shot_ids')
            if source not in relevant or source in covered or kind not in {'plot','montage','detail','description'} or not isinstance(targets,list) or not targets or len(set(targets))!=len(targets) or not set(targets)<=row_ids:
                fault('E-EPISODE-SOURCE-BUDGET-001',loc,'classification must cover exact unique real source and shot IDs');continue
            covered.add(source)
            if set(targets)!=(set(source_map[source].get('target_shots',[]))&row_ids):
                fault('E-EPISODE-SOURCE-BUDGET-001',loc,'classified shots differ from actual source ledger')
            totals[kind].update(targets);duration=sum((by_id[s][2] for s in targets),Decimal(0))
            if kind=='montage' and len(targets)>p['montage_shots_per_source_max']:
                fault('E-EPISODE-SOURCE-BUDGET-001',loc,'montage source exceeds per-source shot limit')
            if kind=='description' and (len(targets)>p['description_shots_per_source_max'] or duration>seconds(p['description_seconds_per_source_max'])):
                fault('E-EPISODE-SOURCE-BUDGET-001',loc,'description exceeds per-source shot/time limit')
            if kind=='detail':
                elements=item.get('micro_elements')
                if not isinstance(elements,list) or not elements or any(not isinstance(x,str) or not x.strip() for x in elements):
                    fault('E-EPISODE-SOURCE-BUDGET-001',loc,'detail requires explicit source-grounded micro elements');elements=[]
                for sid in targets:micro_by_shot[sid].update(elements)
                try:baseline=seconds(item['baseline_seconds'])
                except (KeyError,ValueError):baseline=Decimal(0)
                if baseline<=0 or duration>baseline*seconds(p['detail_duration_multiplier_max']):
                    fault('E-EPISODE-SOURCE-BUDGET-001',loc,'detail requires an independently reviewed baseline and bounded duration')
        if covered!=relevant:fault('E-EPISODE-SOURCE-BUDGET-001','episode:'+ep,'source-function ledger is incomplete')
        for sid,elements in micro_by_shot.items():
            if len(elements)>p['detail_elements_per_shot_max']:fault('E-EPISODE-SOURCE-BUDGET-001',sid,'detail micro elements exceed per-shot budget')
        for kind,max_shots,max_time in [('montage',p['montage_shot_percent_max'],p['montage_time_percent_max'])]:
            n=len(totals[kind]); t=sum((by_id[s][2] for s in totals[kind]),Decimal(0))
            details[kind+'_shot_percent']=100*n/len(rows);details[kind+'_time_percent']=float(100*t/total)
            if 100*Decimal(n)>seconds(max_shots)*len(rows) or 100*t>seconds(max_time)*total:
                fault('E-EPISODE-SOURCE-BUDGET-001','episode:'+ep,kind+' exceeds aggregate budget')
        runs=[];run=Decimal(0)
        for sid,_,dur in rows:
            if sid in totals['description']:run+=dur
            else:runs.append(run);run=Decimal(0)
        runs.append(run)
        details['longest_description_run_seconds']=float(max(runs,default=Decimal(0)))
        if max(runs,default=Decimal(0))*100>seconds(p['description_run_time_percent_max'])*total:
            fault('E-EPISODE-SOURCE-BUDGET-001','episode:'+ep,'continuous description exceeds episode time proportion')
    if not fragment and isinstance(contract,dict) and contract.get('scope')=='complete_series':
        expected=contract.get('series_episode_ids')
        if not isinstance(expected,list) or len(set(expected))!=len(expected) or set(expected)!=set(groups):
            fault('E-EPISODE-CONTRACT-001','episode_contract.series_episode_ids','complete series requires the exact entire episode set')
    return {'contract':CONTRACT,'profile_id':p['profile_id'],'status':'fail' if faults else 'pass' if complete else 'insufficient_evidence',
            'semantic_accuracy_percent':None,'episodes':results,'faults':faults,
            'independent_review_obligations':review_obligations(obj) if complete else []}


def _inspect_borrowing(obj):
    evidence=obj.get('prevention_evidence') or {};sources=evidence.get('source_ledger') or []
    borrowed=[r for r in sources if isinstance(r,dict) and r.get('treatment')=='借用']
    if not borrowed:return []
    ledger=evidence.get('borrow_ledger'); faults=[]
    if not isinstance(ledger,list):ledger=[]
    def add(source,message):faults.append({'error_code':'E-BORROW-CONTRACT-001','location':'borrow:'+str(source),'evidence':message})
    known={str(s.get('source_id')):s for s in sources if isinstance(s,dict)}
    ids=[x.get('source_id') for x in ledger if isinstance(x,dict)]
    if len(ids)!=len(set(ids)):add('ledger','duplicate borrowed source records')
    for row in borrowed:
        sid=row.get('source_id');matches=[x for x in ledger if isinstance(x,dict) and x.get('source_id')==sid]
        if len(matches)!=1:add(sid,'one exact borrow record required');continue
        b=matches[0]
        required=('authorization_id','authorization_basis','origin_episode','target_episode','next_source_id','repayment_state')
        if any(b.get(k) in (None,'','无','未授权','pending') for k in required):add(sid,'authorization, source origin, next boundary and debt state required')
        if row.get('authorization')!=b.get('authorization_id'):add(sid,'source authorization and borrow authorization identity differ')
        if b.get('origin_episode')==b.get('target_episode'):add(sid,'borrowing must identify different origin and target episodes')
        if b.get('next_source_id') not in known or b.get('next_source_id')==sid:add(sid,'next source must be a real different source identity')
        if b.get('repayment_state') not in {'boundary_replanned','retired_from_origin'}:add(sid,'debt must be resolved by a concrete subsequent boundary plan')
        targets=row.get('target_shots') or []
        shots={str(s.get('镜头编号')):s for s in obj.get('shots',[]) if isinstance(s,dict)}
        if any(t not in shots or episode_id(shots[t],obj)!=b.get('target_episode') for t in targets):add(sid,'borrow target episode differs from actual target shots')
    return faults


def inspect_episodes(obj, shots=None, **kwargs):
    try:return _inspect_episodes(obj,shots,**kwargs)
    except (ValueError,TypeError,KeyError,AttributeError,IndexError) as exc:
        return {'contract':CONTRACT,'status':'fail','episodes':[], 'semantic_accuracy_percent':None,
                'faults':[{'error_code':'E-EPISODE-CONTRACT-001','location':'episode_contract',
                           'evidence':'malformed episode evidence: '+str(exc),'status':'fail'}]}


def inspect_borrowing(obj):
    try:return _inspect_borrowing(obj)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        return [{'error_code':'E-BORROW-CONTRACT-001','location':'borrow_ledger','evidence':'malformed borrow evidence: '+str(exc)}]


def inspect_planned_budget(raw):
    """Author-side numeric checks before lowering; no invented dialogue budget."""
    findings=[]
    try:
        project=raw['project']; rows=raw['shots']; p=policy()
        contract=(project.get('prevention_evidence') or {}).get('episode_contract') or {}
        complete=contract.get('scope') in {'complete_episodes','complete_series'}
        totals=defaultdict(lambda:Decimal(0)); durations=defaultdict(list)
        for row in rows:
            ticks=row['ticks']
            if not isinstance(ticks,list) or not ticks or ticks[0]!=0:
                raise ValueError('shot ticks must start at zero')
            d=seconds(ticks[-1])/10; ep=episode_id(row,project)
            totals[ep]+=d;durations[ep].append(d)
        for ep,total in totals.items():
            if total>seconds(p['max_seconds']) or complete and total<seconds(p['min_seconds']):
                findings.append({'code':'E-EPISODE-DURATION-001','path':'episode:'+ep,
                    'message':f'planned {total}s outside applicable episode budget; replan before prose rendering'})
            if complete:
                for n,key in [(1,'short_under_1_percent'),(2,'short_under_2_percent')]:
                    if sum(d<n for d in durations[ep])*100 < seconds(p[key])*len(durations[ep]):
                        findings.append({'code':'E-EPISODE-RHYTHM-001','path':'episode:'+ep,
                            'message':key+' below profile; do not invent cuts to meet a ratio'})
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        findings.append({'code':'E-EPISODE-CONTRACT-001','path':'episode_planned_budget','message':str(exc)})
    return findings


def review_obligations(obj):
    """Exact current episode evidence for the existing independent audit gate.

    These are questions to the reviewer, never a self-reported semantic score.
    Hashing the whole candidate prevents carrying reviews across local repairs.
    """
    evidence=obj.get('prevention_evidence') or {}
    contract=evidence.get('episode_contract')
    if not isinstance(contract,dict) or contract.get('scope') not in {'complete_episodes','complete_series'}:
        return []
    # Avoid a circular hash through generated obligations in the derivation.
    bound={k:v for k,v in obj.items() if k!='prevention_evidence'}
    bound['episode_contract']=contract;bound['source_ledger']=evidence.get('source_ledger')
    bound['borrow_ledger']=evidence.get('borrow_ledger')
    from production_derivation import digest
    fingerprint=digest(bound)
    result=[]
    for ep in contract.get('episodes',[]):
        eid=ep['episode_id']
        shots=[s for s in obj.get('shots',[]) if episode_id(s,obj)==eid]
        sids=[str(s.get('镜头编号') or s.get('shot_id')) for s in shots]
        checks=[('opening_face','机位可见性','核对核心角色确在原文场景，开头3秒面部可辨且占画面至少30%；眼部点可见不是面积证据。',['机位','起始状态']),
                ('beat_semantics','源文对白','逐项核对开篇、推进、末尾钩子或终局闭合、连续道具动作最长22秒和承接；排除同义改写冒充新推进。',['画面内容']),
                ('short_function','源文对白','逐个核对短镜有来源和叙事功能，重复空镜或重复反应不得凑比例；对白分布和无对白空档符合源文。',['画面内容','心理音轨']),
                ('source_budget','源文对白','逐来源核对plot/montage/detail/description分类和细节合理基准时长；导演选择不能发明事实。',['画面内容','原文支撑句'])]
        if any(r.get('target_episode')==eid for r in evidence.get('borrow_ledger',[]) if isinstance(r,dict)):
            checks.append(('borrowing','源文对白','核对真实借用授权原件、来源所属集、偿还及后续边界；检查连三集或末三集限制的项目适用依据，不能仅凭候选人自报授权。',['画面内容','原文支撑句']))
        for kind,layer,question,fields in checks:
            identity=hashlib.sha256((fingerprint+':'+eid+':'+kind).encode()).hexdigest()
            result.append({'evidence_id':'episode-review:'+identity,'episode_id':eid,'kind':kind,
                'layer':layer,'question':question,'field_evidence_ids':[f'field:{sid}.{f}' for sid in sids for f in fields]})
    return result
