"""A frozen production plan constrains decisions before prose is assembled.

Evidence identity is not entailment. The finite landmark/hearing model does not
certify natural performance, full body occlusion, or first-draft accuracy.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

CONTRACT = 'storyboard-production-plan-v1'
POLICY_CONTRACT = 'storyboard-production-prevention-v1'
REF = Path(__file__).resolve().parents[1] / 'references'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def policy():
    return json.loads((REF / 'rule_registry.json').read_text(encoding='utf-8'))['runtime']['production_prevention_policy']


def runtime_context():
    registry = json.loads((REF / 'rule_registry.json').read_text(encoding='utf-8'))
    runtime = registry['runtime']
    return {'rule_registry_sha256': digest(registry),
            'template_contract_sha256': digest({'fields': runtime['canonical_shot_fields'],
                                                'content': runtime['field_content_policy']})}


def model_production_contract():
    p = policy()
    from episode_rules import policy as episode_policy
    import production_actions
    from production_decisions import ACTION_FIELDS
    from production_environment import SOUND_KINDS
    from source_decisions import MODES
    return {'contract': p['contract'], 'plan_contract': CONTRACT,
            'required_for': ['new_mcp_production_authoring', 'direct_canonical_new_production', 'official_production_cli'],
            'episode_contract': episode_policy(),
            'action_contract':{'reference':'references/source_actions_0537.md',
                'parameters':{k:sorted(v) for k,v in sorted(ACTION_FIELDS.items())},
                'source_modes':sorted(MODES),'postures':sorted(production_actions.POSTURES),
                'frozen_source_required':sorted(production_actions.SOURCE_REQUIRED),
                'bridge_kinds':sorted(production_actions.BRIDGE_KINDS),
                'sound_kinds':{k:sorted(v) for k,v in SOUND_KINDS.items()},
                'bridge':'transition.bridge={ticks,actions}; omitted staging only; zero screen duration; no source credit',
                'voices':'sequential speakers in the same eleven labels, each body bound by 〔ordinal:speaker〕',
                'semantics':'pending_independent_review'},
            'episode_contract_shape': {
                'contract':'episode-production-contract-v1','profile_id':'mother-episode-90-180-v1',
                'scope':'fragment | complete_episodes | complete_series', 'source_kind':'novel | script | original',
                'episodes':'[{episode_id,core_actor,ending:hook|finale,beat_events,source_functions}]',
                'beat_events':'[{id,kind,shot_id,source_ids,delivery:{field,quote,event_ids},start,end}] local seconds; exact interval from replayed execution, not caller-estimated timing',
                'source_functions':'[{source_id,kind:plot|montage|detail|description,shot_ids,micro_elements?,baseline_seconds?}]',
                'series_episode_ids':'required exact set only for complete_series',
                'reference':'references/episode_rule_contract.md; strict schema in storyboard_schema.json'},
            'borrow_authorization':'Before authoring a borrowed source, declare source_id/source_sha256/origin_episode/target_episode/next_source_id/authorization_basis in begin_project_task.borrow_permissions. If the chat schema lacks that parameter, use the task_options_contract prefixed-string transport from get_agent_status. Use the returned authorization_id in the source and borrow ledgers; candidate text cannot mint it.',
            'episode_contract_location':'project.prevention_evidence.episode_contract; freeze as an owned source-plan partition; full/episode requires complete_episodes or complete_series',
            'plan_location': 'separately frozen source graph.production_plan',
            'context': runtime_context(), 'statuses': p['statuses'],
            'rules': [{k:r[k] for k in ('id','title','prevention','exceptions','limits')} for r in p['rules']],
            'plan_shape': {
                'contract': CONTRACT,
                'context': {**runtime_context(), 'source_sha256': 'exact source graph.sources mapping',
                            'initial_world_sha256': 'SHA256 of UTF-8 sorted compact finite JSON initial_world'},
                'decisions': [{'path': 'one complete ownership partition',
                               'basis': p['authored_basis'], 'fact_ids': ['existing fact ID'],
                               'source_spans': [{'source_id':'actual source ID','start':0,'end':1,'text':'exact source span'}],
                               'value': 'exact JSON value of this partition; no inferred replacement'}],
                'knowledge': {
                    'initial': ['{actor,fact_ids,evidence_fact_ids}'],
                    'acquisitions': ['{id,actor,fact_ids,evidence_fact_ids,action_id,mode:heard|source_report}'],
                    'uses': ['{action_id,actor,fact_ids,stance,evidence_fact_ids}; every actual action exactly once']},
                'shots': ['{shot_id,purpose_fact_ids,required_action_ids,audio_action_ids,source_use_ids,visible}']},
            'decision_partitions': ['each initial_world/scenes|actors|props|points entry',
                'each shot camera|ticks|transition|editorial|shot_id|shot_uid|episode_id|scene_id|continuity_id|source_unit_ids',
                'each shot action'],
            'knowledge_stances': p['knowledge_stances'],
            'visibility_requirement': '{kind,subject,start,end}; interval in integer tenths [start,end)',
            'visibility_kinds': p['visibility_kinds'],
            'plan_reference': 'references/production_plan_contract.md',
            'semantic_status': 'pending_independent_review', 'semantic_accuracy_percent': None,
            'compatibility': 'only historical audit/repair and signed workbook migration retain compatibility; new production cannot bypass the replayed plan'}


class ProductionPlanError(ValueError):
    def __init__(self, path, reason, status='fail'):
        self.path, self.reason, self.status = path, reason, status
        self.findings = [{'code': 'E-PRODUCTION-PLAN-001', 'path': path,
                          'message': reason, 'status': status}]
        super().__init__(f'{status}: {path}: {reason}')


def need(condition, path, reason, status='fail'):
    if not condition:
        raise ProductionPlanError(path, reason, status)


def obj(value, keys, path):
    need(isinstance(value, dict) and set(value) == set(keys), path,
         'requires exactly ' + ', '.join(sorted(keys)))


def ids(value, path, allowed=None, *, nonempty=False):
    need(isinstance(value, list) and all(isinstance(x, str) and bool(x.strip()) for x in value), path,
         'requires string identity array')
    need(len(value) == len(set(value)) and (bool(value) or not nonempty), path,
         'requires unique identities' + (' with at least one source' if nonempty else ''))
    if allowed is not None:
        need(set(value) <= set(allowed), path, 'unknown identity: ' + ', '.join(sorted(set(value) - set(allowed))))


def registry(rows, id_key, path):
    need(isinstance(rows, list) and all(isinstance(x, dict) for x in rows), path, 'requires object array')
    keys = [x.get(id_key) for x in rows]
    ids(keys, path)
    return dict(zip(keys, rows))


def decision_values(payload):
    """Complete, non-overlapping authored ownership partitions; never fill values."""
    result = {}
    evidence=payload.get('project',{}).get('prevention_evidence',{})
    for name in ('episode_contract','borrow_ledger'):
        if name in evidence: result['/project/prevention_evidence/'+name]=evidence[name]
    for name in ('source_ingestion','chapter_intake'):
        if name in payload.get('project',{}): result['/project/'+name]=payload['project'][name]
    def escape(token): return str(token).replace('~', '~0').replace('/', '~1')
    for kind in ('scenes', 'actors', 'props', 'points'):
        for identity, value in payload['initial_world'][kind].items():
            result[f'/initial_world/{kind}/{escape(identity)}'] = value
    for index, shot in enumerate(payload['shots']):
        base = f'/shots/{index}'
        for name in ('camera', 'ticks', 'transition', 'editorial'):
            result[base + '/' + name] = shot[name]
        for name in ('shot_id', 'shot_uid', 'episode_id', 'scene_id', 'continuity_id', 'source_unit_ids'):
            result[base + '/' + name] = shot[name]
        for j, action in enumerate(shot['actions']):
            result[f'{base}/actions/{j}'] = action
    return result


def validate_source_plan(plan, facts, source_texts, source_hashes):
    """Validate a supplied frozen plan against real source bytes, never prose inference."""
    p = '$graph.production_plan'
    active = policy()
    obj(plan, {'contract', 'context', 'decisions', 'knowledge', 'shots'}, p)
    need(plan['contract'] == CONTRACT, p + '.contract', 'unsupported production plan contract', 'not_supported')
    expected = runtime_context()
    obj(plan['context'], {*expected, 'source_sha256', 'initial_world_sha256'}, p + '.context')
    for key, value in expected.items():
        need(plan['context'][key] == value, p + '.context.' + key, 'stale active rule/template context')
    need(plan['context']['source_sha256'] == source_hashes, p + '.context.source_sha256', 'source context differs from frozen graph')
    need(isinstance(plan['context']['initial_world_sha256'], str) and
         re.fullmatch('[0-9a-f]{64}', plan['context']['initial_world_sha256']), p + '.context.initial_world_sha256', 'exact initial world fingerprint required')
    decisions = registry(plan['decisions'], 'path', p + '.decisions')
    need(bool(decisions), p + '.decisions', 'missing owned production decisions', 'insufficient_evidence')
    for key, d in decisions.items():
        dp = p + '.decisions.' + key
        obj(d, {'path', 'basis', 'fact_ids', 'source_spans', 'value'}, dp)
        need(d['basis'] in active['authored_basis'], dp + '.basis',
             'derived_state is compiler-owned; use an actual supported authored basis')
        ids(d['fact_ids'], dp + '.fact_ids', facts, nonempty=d['basis'] == 'source_fact')
        need(isinstance(d['source_spans'], list), dp + '.source_spans', 'requires source span array')
        if d['basis'] == 'locked_setting':
            need(bool(d['source_spans']), dp, 'locked setting needs actual source text', 'insufficient_evidence')
        for span in d['source_spans']:
            obj(span, {'source_id', 'start', 'end', 'text'}, dp + '.source_spans')
            sid, lo, hi = span['source_id'], span['start'], span['end']
            need(isinstance(sid, str) and sid in source_texts and type(lo) is int and type(hi) is int and
                 0 <= lo < hi <= len(source_texts[sid]) and source_texts[sid][lo:hi] == span['text'], dp,
                 'setting span differs from actual frozen source text')
    k = plan['knowledge']
    obj(k, {'initial', 'acquisitions', 'uses'}, p + '.knowledge')
    need(isinstance(k['initial'], list), p + '.knowledge.initial', 'requires array')
    initial_pairs = set()
    for entry in k['initial']:
        obj(entry, {'actor', 'fact_ids', 'evidence_fact_ids'}, p + '.knowledge.initial')
        need(isinstance(entry['actor'], str) and bool(entry['actor']), p + '.knowledge.initial', 'actual actor identity required')
        ids(entry['fact_ids'], p + '.knowledge.initial.fact_ids', facts, nonempty=True)
        ids(entry['evidence_fact_ids'], p + '.knowledge.initial.evidence_fact_ids', facts, nonempty=True)
        for fid in entry['fact_ids']:
            pair = (entry['actor'], fid)
            need(pair not in initial_pairs, p + '.knowledge.initial', 'duplicate initial knowledge')
            initial_pairs.add(pair)
    for entry in registry(k['acquisitions'], 'id', p + '.knowledge.acquisitions').values():
        obj(entry, {'id', 'actor', 'fact_ids', 'evidence_fact_ids', 'action_id', 'mode'}, p + '.knowledge.acquisitions')
        need(entry['mode'] in active['acquisition_modes'], p + '.knowledge.acquisitions.mode',
             'acquisition channel is not modelled; retain independent source evidence', 'not_supported')
        ids(entry['fact_ids'], p + '.knowledge.acquisitions.fact_ids', facts, nonempty=True)
        ids(entry['evidence_fact_ids'], p + '.knowledge.acquisitions.evidence_fact_ids', facts, nonempty=True)
        need(all(isinstance(entry[x], str) and entry[x] for x in ('actor', 'action_id')), p + '.knowledge.acquisitions', 'actor and actual action required')
    for entry in registry(k['uses'], 'action_id', p + '.knowledge.uses').values():
        obj(entry, {'action_id', 'actor', 'fact_ids', 'stance', 'evidence_fact_ids'}, p + '.knowledge.uses')
        ids(entry['fact_ids'], p + '.knowledge.uses.fact_ids', facts)
        ids(entry['evidence_fact_ids'], p + '.knowledge.uses.evidence_fact_ids', facts)
        need(entry['stance'] in active['knowledge_stances'], p + '.knowledge.uses.stance', 'unsupported knowledge stance', 'not_supported')
        need(entry['actor'] is None or isinstance(entry['actor'], str), p + '.knowledge.uses.actor', 'invalid actor identity')
        if entry['stance'] in {'suspected', 'mistaken', 'pretended'}:
            need(bool(entry['evidence_fact_ids']) and bool(entry['fact_ids']), p + '.knowledge.uses',
                 'belief/pretence requires explicit source evidence', 'insufficient_evidence')
        if entry['stance'] == 'not_applicable':
            need(not entry['fact_ids'], p + '.knowledge.uses', 'not_applicable cannot conceal factual knowledge use')
    for shot in registry(plan['shots'], 'shot_id', p + '.shots').values():
        obj(shot, {'shot_id', 'purpose_fact_ids', 'required_action_ids', 'audio_action_ids', 'source_use_ids', 'visible'}, p + '.shots')
        ids(shot['purpose_fact_ids'], p + '.shots.purpose_fact_ids', facts, nonempty=True)
        for key in ('required_action_ids', 'audio_action_ids', 'source_use_ids'):
            ids(shot[key], p + '.shots.' + key)
        need(isinstance(shot['visible'], list), p + '.shots.visible', 'requires explicit visibility requirements')
        seen = set()
        for requirement in shot['visible']:
            obj(requirement, {'kind', 'subject', 'start', 'end'}, p + '.shots.visible')
            need(requirement['kind'] in active['visibility_kinds'], p + '.shots.visible.kind',
                 'requested visibility needs a model beyond declared landmarks', 'not_supported')
            need(isinstance(requirement['subject'], str) and bool(requirement['subject']), p + '.shots.visible.subject', 'actual subject required')
            lo, hi = requirement['start'], requirement['end']
            need(type(lo) is int and type(hi) is int and 0 <= lo < hi <= 36000, p + '.shots.visible', 'integer tenths interval required')
            marker = (requirement['kind'], requirement['subject'], lo, hi)
            need(marker not in seen, p + '.shots.visible', 'duplicate visibility requirement')
            seen.add(marker)
    return copy.deepcopy(plan)


class ProductionPlanGuard:
    """Use actual simulated views/events; never trust a producer-written PASS."""
    def __init__(self, payload, source):
        self.plan = copy.deepcopy(source.get('production_plan'))
        self.source = source
        self.reports, self.events, self.granted = [], {}, set()
        if self.plan is None:
            return
        self.actors = set(payload['initial_world']['actors'])
        p = '$graph.production_plan'
        need(self.plan['context']['initial_world_sha256'] == digest(payload['initial_world']), p + '.context.initial_world_sha256', 'initial world changed after planning')
        expected = decision_values(payload)
        authored = {d['path']: d for d in self.plan['decisions']}
        need(set(authored) == set(expected), p + '.decisions',
             'owned decision coverage differs; missing=' + str(sorted(set(expected)-set(authored))) +
             '; extra=' + str(sorted(set(authored)-set(expected))), 'insufficient_evidence')
        for path, value in expected.items():
            need(digest(value) == digest(authored[path]['value']), path, 'decision differs from independently frozen plan')
        self.shots = {s['shot_id']: s for s in self.plan['shots']}
        need(set(self.shots) == {s['shot_id'] for s in payload['shots']}, p + '.shots', 'whole-shot plan coverage differs', 'insufficient_evidence')
        self.uses = {u['action_id']: u for u in self.plan['knowledge']['uses']}
        action_ids = [a['id'] for shot in payload['shots'] for a in
                      [*shot['transition'].get('bridge',{}).get('actions',[]),*shot['actions']]]
        ids(action_ids, 'shots.actions')
        need(set(self.uses) == set(action_ids), p + '.knowledge.uses', 'every actual action needs its knowledge applicability', 'insufficient_evidence')
        self.known, self.reported = set(), set()
        for entry in self.plan['knowledge']['initial']:
            need(entry['actor'] in self.actors, p + '.knowledge.initial', 'unknown initial actor')
            self.known.update((entry['actor'], fid) for fid in entry['fact_ids'])
        for entry in self.plan['knowledge']['acquisitions']:
            need(entry['actor'] in self.actors and entry['action_id'] in self.uses,
                 p + '.knowledge.acquisitions', 'unknown recipient or actual acquisition action')

    def check_bridge(self, row, actions):
        if self.plan is None: return
        for action in actions:
            u=self.uses[action['id']]
            need(u['actor']==action['actor'] and u['stance']=='not_applicable' and not u['fact_ids'],
                 '$graph.production_plan.bridge','omitted staging cannot grant or consume plot knowledge')

    def check_shot(self, row, samples, actions, action_records):
        if self.plan is None:
            return
        p = '$graph.production_plan.shots.' + row['shot_id']
        spec = self.shots[row['shot_id']]
        need(row['transition']['relation'] not in {'prerequisite_reordering', 'same_event_reframing'},
             p + '.knowledge', 'nonlinear chronology needs an explicit story-time knowledge model', 'not_supported')
        actual = {a['id']: a for a in actions}
        need(set(spec['required_action_ids']) <= set(actual), p + '.required_action_ids', 'required event absent from this shot')
        audio = {a['id'] for a in actions if a['kind'] in {'speak', 'narrate', 'inner_voice'}}
        need(set(spec['audio_action_ids']) == audio, p + '.audio_action_ids', 'actual audio does not match the shot delivery plan')
        # Finish a disclosure at a tick before checking a dependent action at
        # that tick. No fictional hearing is inferred from a sentence string.
        timeline = sorted({0, row['ticks'][-1], *[a['start'] for a in actions], *[a['end'] for a in actions]})
        for tick in timeline:
            for a in actions:
                if a['end'] == tick:
                    self.events[a['id']] = (row['shot_id'], a, samples)
            for acquisition in self.plan['knowledge']['acquisitions']:
                if acquisition['id'] in self.granted or acquisition['action_id'] not in self.events:
                    continue
                sid, a, prior_samples = self.events[acquisition['action_id']]
                actual_use = self.source['uses_by_id'][a['source_use_id']]
                need(set(acquisition['fact_ids']) <= set(actual_use['fact_ids']), p + '.knowledge', 'disclosure does not carry the acquired fact')
                if acquisition['mode']=='heard':
                    need(a['kind'] == 'speak', p + '.knowledge', 'heard acquisition must bind an actual spoken event')
                    for sample in prior_samples[a['start']:a['end']]:
                        listener = sample['world']['actors'][acquisition['actor']]
                        speaker = sample['world']['actors'][a['actor']]
                        need(listener['scene_id'] == speaker['scene_id'] and acquisition['actor'] != a['actor'], p + '.knowledge', 'listener has no actual same-scene disclosure')
                        need(not sample['world']['scenes'][speaker['scene_id']]['barriers'], p + '.knowledge',
                             'hearing across declared barriers is not modelled', 'not_supported')
                else:
                    need(a['kind']=='narrate' and actual_use['actor'] is None, p+'.knowledge','source report must be delivered by actual objective narration')
                    reports=[f for f in actual_use['source_constraints'] if f['id'] in acquisition['evidence_fact_ids'] and f['channel']=='report']
                    need(reports and all(f['subject']==acquisition['actor'] and f['polarity']=='affirmative' and
                        f['modality']=='asserted' and f['occurrence']=='occurred' and not f['conditions'] and
                        any(span['text']==actual_use['text'] for span in f['supports']) for f in reports),
                        p+'.knowledge','source report needs exact narrated source span and an occurred receipt by this recipient')
                    need(set(acquisition['evidence_fact_ids'])<=set(actual_use['fact_ids']),p+'.knowledge','report evidence is not delivered')
                # Hearing an assertion establishes receipt, not its truth.
                self.reported.update((acquisition['actor'], fid) for fid in acquisition['fact_ids'])
                self.granted.add(acquisition['id'])
            for a in actions:
                if a['start'] != tick:
                    continue
                u = self.uses[a['id']]
                need(u['actor'] == a['actor'], p + '.knowledge', 'knowledge user differs from the actual event actor')
                sourced = self.source['uses_by_id'].get(a.get('source_use_id'))
                if sourced:
                    need(set(sourced['fact_ids']) <= set(u['fact_ids']), p + '.knowledge', 'knowledge declaration omits actual source facts')
                if a['kind'] == 'narrate':
                    need(u['stance'] == 'narrator' and u['actor'] is None, p + '.knowledge', 'objective narration requires narrator stance')
                else:
                    need(u['stance'] != 'narrator', p + '.knowledge', 'a character cannot use narrator knowledge')
                    if u['stance'] == 'known':
                        need(all((a['actor'], fid) in self.known for fid in u['fact_ids']), p + '.knowledge', 'character uses knowledge before its acquisition')
                    if u['stance'] == 'reported':
                        need(all((a['actor'], fid) in self.reported for fid in u['fact_ids']), p + '.knowledge', 'character uses a report before actually hearing it')
        for requirement in spec['visible']:
            lo, hi, identity = requirement['start'], requirement['end'], requirement['subject']
            need(hi <= row['ticks'][-1], p + '.visible', 'required visibility lies outside this shot')
            for sample in samples[lo:hi]:
                view = sample['view']; actor = view['actors'].get(identity)
                kind = requirement['kind']
                if kind == 'actor': visible = actor is not None
                elif kind == 'eyes': visible = bool(actor and actor['eye_visible'])
                elif kind in {'left_hand', 'right_hand'}: visible = bool(actor and kind.split('_')[0] in actor['hands'])
                else:
                    visible = identity in view['props']
                    if kind == 'current_written' and visible:
                        ref = sample['world']['props'][identity]['written_ref']
                        paper = self.source['written_objects_by_id'].get(ref)
                        # Actual writing can create text later; a planned paper
                        # alone is never proof that it existed in frame zero.
                        visible = paper is not None and (paper['state'] == 'current' or
                            any(a['kind'] == 'write' and a['paper_ref'] == ref and
                                (event_sid != row['shot_id'] or a['end'] <= sample['tick'])
                                for event_sid, a, _ in self.events.values()))
                need(visible, p + '.visible', f"required {kind}:{identity} not visible at tick {sample['tick']}")
        projected = {a['id'] for a in action_records if a['projection_status'] != 'hidden'}
        consumed = {a['source_use_id'] for a in actions if a.get('source_use_id') is not None and a['id'] in projected}
        for identity in samples[0]['view']['props']:
            ref = samples[0]['world']['props'][identity]['written_ref']
            paper = self.source['written_objects_by_id'].get(ref)
            if paper is not None and paper['state'] == 'current':
                consumed.add(paper['use_id'])
        need(set(spec['source_use_ids']) <= consumed, p + '.source_use_ids', 'required information has no actual source use in this shot')
        delivered = {fid for uid in spec['source_use_ids'] for fid in self.source['uses_by_id'][uid]['fact_ids']}
        need(set(spec['purpose_fact_ids']) <= delivered, p + '.purpose_fact_ids', 'shot purpose has no actual declared delivery')
        self.reports.append({'shot_id': row['shot_id'], 'status': 'pass',
                             'visibility_checks': len(spec['visible']), 'actual_audio_ids': sorted(audio),
                             'source_use_ids': sorted(spec['source_use_ids'])})

    def finish(self):
        if self.plan is None:
            return {'contract': POLICY_CONTRACT, 'status': 'insufficient_evidence',
                    'reason': 'legacy low-level input has no frozen production plan',
                    'semantic_status': 'pending_independent_review', 'semantic_accuracy_percent': None}
        need(self.granted == {x['id'] for x in self.plan['knowledge']['acquisitions']}, '$graph.production_plan.knowledge', 'unexecuted knowledge acquisition')
        return {'contract': POLICY_CONTRACT, 'status': 'pass', 'plan_sha256': digest(self.plan),
                'context': copy.deepcopy(self.plan['context']), 'shots': copy.deepcopy(self.reports),
                'scope': 'frozen decision identity; declared source and knowledge bindings; modelled landmark/time/audio feasibility',
                'semantic_status': 'pending_independent_review', 'semantic_accuracy_percent': None,
                'independent_review_required': ['source entailment and coverage', 'director choice and performance',
                                                'belief and initial knowledge evidence', 'full occlusion and audio measurement']}
