"""Source-bound scene time and audience causality; not a semantic certificate.

Reuse the frozen fact graph and actual replay deliveries. Editing order never
grants fictional knowledge. Parallel scenes are supported only where their
physical state can be replayed independently, without reading future state.
"""
from __future__ import annotations

import hashlib
import json
import re

CONTRACT = 'scene-narrative-v1'
RELATIONS = {'initial', 'continuous', 'later', 'simultaneous'}
PURPOSES = {'advance', 'answer', 'contrast', 'setup', 'reaction', 'result'}
DELIVERY_FIELDS = {'画面内容', '起始状态', '心理音轨', '环境音效'}


def _helpers():
    from production_plan import need, obj, ids, registry
    return need, obj, ids, registry


def validate_narrative(value, shots, facts, source_texts):
    need, obj, ids, registry = _helpers()
    p = '$graph.production_plan.narrative'
    obj(value, {'contract', 'segments', 'causal_links', 'prior_deliveries'}, p)
    need(value['contract'] == CONTRACT, p, 'unsupported scene narrative contract', 'not_supported')
    shot_ids = {s['shot_id'] for s in shots}
    segments = registry(value['segments'], 'id', p + '.segments')
    need(bool(segments), p, 'scene segments must cover the complete production', 'insufficient_evidence')
    covered = []
    for segment in segments.values():
        at = p + '.segments.' + segment['id']
        obj(segment, {'id', 'shot_ids', 'time', 'purpose', 'purpose_fact_ids',
                      'orientation_subject_ids', 'entry_basis', 'cut_basis', 'audio_bridge'}, at)
        ids(segment['shot_ids'], at, shot_ids, nonempty=True)
        covered.extend(segment['shot_ids'])
        ids(segment['purpose_fact_ids'], at, facts, nonempty=True)
        ids(segment['orientation_subject_ids'], at, nonempty=True)
        need(segment['purpose'] in PURPOSES, at, 'declare the specific narrative function')
        for key in ('entry_basis', 'cut_basis'):
            need(isinstance(segment[key], str) and bool(segment[key].strip()), at + '.' + key,
                 'concrete location recognition and reason to cut required', 'insufficient_evidence')
        # ponytail: the existing audio renderer has no cross-shot overlap clock.
        # Keep J/L cuts explicit but closed until that renderer can bind them.
        need(segment['audio_bridge'] == 'none', at, 'J/L audio overlaps need a cross-shot audio renderer; use a hard cut with explicit scene recognition', 'not_supported')
        t = segment['time']
        obj(t, {'relation', 'reference_segment_id', 'start_tick', 'evidence_fact_ids'}, at + '.time')
        need(t['relation'] in RELATIONS and type(t['start_tick']) is int and t['start_tick'] >= 0,
             at, 'explicit nonnegative story time in tenths and a supported relation required')
        need(t['reference_segment_id'] is None or t['reference_segment_id'] in segments, at, 'unknown reference segment')
        ids(t['evidence_fact_ids'], at + '.time', facts, nonempty=True)
    need(len(covered) == len(set(covered)) and set(covered) == shot_ids, p,
         'every shot must belong to exactly one scene segment', 'insufficient_evidence')
    prior = registry(value['prior_deliveries'], 'id', p + '.prior_deliveries')
    for row in prior.values():
        obj(row, {'id', 'fact_ids', 'artifact_id', 'artifact_sha256', 'shot_id', 'field', 'quote'}, p + '.prior_deliveries')
        ids(row['fact_ids'], p, facts, nonempty=True)
        for key in ('artifact_id', 'shot_id', 'quote'):
            need(isinstance(row[key], str) and bool(row[key].strip()), p, 'actual prior artifact, shot and quote required')
        need(isinstance(row['artifact_sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['artifact_sha256'])
             and row['field'] in DELIVERY_FIELDS, p, 'prior delivery requires exact file hash and an audience-facing field')
    expected = {(fid, dep) for fid, fact in facts.items() for dep in set(fact['depends_on']) | set(fact['conditions'])}
    need(isinstance(value['causal_links'], list), p, 'causal links must be an array')
    found = set()
    for link in value['causal_links']:
        obj(link, {'fact_id', 'requires_fact_id', 'treatment', 'establishment_shot_id',
                   'prior_delivery_id', 'reveal_shot_id', 'future_source_span', 'reason'}, p + '.causal_links')
        pair = (link['fact_id'], link['requires_fact_id'])
        need(all(isinstance(x, str) for x in pair) and pair in expected and pair not in found, p,
             'causal link must uniquely match the frozen fact graph')
        found.add(pair)
        need(isinstance(link['reason'], str) and bool(link['reason'].strip()), p, 'explain actual presentation or intentional suspense')
        mode = link['treatment']
        need(mode in {'shown', 'prior_delivery', 'withheld'}, p, 'missing/unknown cause cannot be marked resolved', 'insufficient_evidence')
        for key in ('establishment_shot_id', 'reveal_shot_id'):
            need(link[key] is None or link[key] in shot_ids, p, 'unknown audience delivery shot')
        if mode == 'shown':
            need(link['establishment_shot_id'] is not None and all(link[k] is None for k in
                 ('prior_delivery_id', 'reveal_shot_id', 'future_source_span')), p, 'shown cause needs one actual delivery shot')
        elif mode == 'prior_delivery':
            record = prior.get(link['prior_delivery_id'])
            need(record is not None and link['requires_fact_id'] in record['fact_ids'] and
                 all(link[k] is None for k in ('establishment_shot_id', 'reveal_shot_id', 'future_source_span')),
                 p, 'prior audience knowledge needs a bound same-project artifact; source prose alone is not delivery')
        else:
            need(link['establishment_shot_id'] is None and link['prior_delivery_id'] is None and
                 (link['reveal_shot_id'] is None) != (link['future_source_span'] is None), p,
                 'intentional suspense needs either a later actual shot or an exact authorized future source span')
            span = link['future_source_span']
            if span is not None:
                obj(span, {'source_id', 'start', 'end', 'text'}, p + '.future_source_span')
                text = source_texts.get(span['source_id'])
                lo, hi = span['start'], span['end']
                need(isinstance(text, str) and type(lo) is int and type(hi) is int and
                     0 <= lo < hi <= len(text) and text[lo:hi] == span['text'], p,
                     'future payoff must bind authorized source characters, not invented continuation')
    need(found == expected, p, 'every frozen causal dependency/condition needs an audience treatment', 'insufficient_evidence')
    need(set(prior) == {link['prior_delivery_id'] for link in value['causal_links'] if link['treatment'] == 'prior_delivery'},
         p, 'unused prior evidence is not a substitute for a specific causal link')


class SceneNarrativeGuard:
    def __init__(self, payload, plan, source):
        self.need, _, _, _ = _helpers()
        self.value, self.source = plan['narrative'], source
        self.scope = payload.get('project', {}).get('prevention_evidence', {}).get('episode_contract', {}).get('scope')
        self.decisions = {d['path']: d for d in plan['decisions']}
        self.path = '$graph.production_plan.narrative'
        rows = payload['shots']; by_id = {s['shot_id']: s for s in rows}
        flat = [sid for segment in self.value['segments'] for sid in segment['shot_ids']]
        self.need(flat == [s['shot_id'] for s in rows], self.path, 'scene segments must preserve the exact edit order')
        self.shot_order = {sid: i for i, sid in enumerate(flat)}
        self.clock, self.segments, self.for_shot = {}, {}, {}
        previous = None
        for segment in self.value['segments']:
            t = segment['time']; group = [by_id[sid] for sid in segment['shot_ids']]
            first = group[0]; start = t['start_tick']; end = start
            self.need(all(s['scene_id'] == first['scene_id'] and s['continuity_id'] == first['continuity_id'] and
                          s['episode_id'] == first['episode_id'] for s in group), self.path,
                      'a scene segment cannot conceal a place, continuity or episode change')
            for i, row in enumerate(group):
                self.need(i == 0 or row['transition']['relation'] == 'continuous', self.path, 'internal scene cuts must stay continuous')
                self.clock[row['shot_id']] = end
                self.for_shot[row['shot_id']] = segment
                end += row['ticks'][-1]
            ref = self.segments.get(t['reference_segment_id'])
            if previous is None:
                self.need(t['relation'] == 'initial' and t['reference_segment_id'] is None and start == 0,
                          self.path, 'first scene starts the story-time origin')
            else:
                self.need(ref is not None, self.path, 'time relation must refer to an earlier edited segment')
                relation = t['relation']; boundary = first['transition']['relation']
                if relation == 'simultaneous':
                    self.need(ref['scene_id'] != first['scene_id'] and max(start, ref['start']) < min(end, ref['end']) and
                              boundary == 'simultaneous_different_place', self.path,
                              'simultaneous scenes need different places and overlapping story intervals')
                    self.need('bridge' not in first['transition'], self.path, 'simultaneous cut cannot hide travel', 'not_supported')
                else:
                    self.need(relation in {'continuous', 'later'} and start >= ref['end'] and
                              start >= previous['end'] and boundary != 'simultaneous_different_place', self.path,
                              'sequential scene cannot start before its reference or previous edit ends')
                    if relation == 'continuous':
                        self.need(ref is previous and start == ref['end'] and first['scene_id'] == ref['scene_id'] and
                                  boundary in {'continuous', 'episode_boundary'}, self.path, 'continuous relation must join the same place at the same instant')
                    else:
                        self.need(boundary in {'later_same_place', 'different_place', 'episode_boundary'}, self.path,
                                  'a time skip needs an explicit boundary')
                    bridge = first['transition'].get('bridge')
                    if bridge:
                        self.need(start - previous['end'] >= bridge['ticks'], self.path, 'omitted travel must fit the story-time gap')
            current = {'start': start, 'end': end, 'scene_id': first['scene_id']}
            self.segments[segment['id']] = current; previous = current
        self.resource_ends, self.event_ends, self.deliveries = {}, {}, []
        self.observations = []

    def check_shot(self, row, samples, actions, records):
        from production_decisions import _anchor, _camera, _in_frame
        sid = row['shot_id']; start = self.clock[sid]; end = start + row['ticks'][-1]
        segment = self.for_shot[sid]; p = self.path + '.' + segment['id']
        first = samples[0]; world = first['world']; scene = row['scene_id']
        # A whole physical scene is a conservative read/write resource. Replaying
        # another location earlier is valid only if no future entity state leaks.
        resources = {'scene:' + scene}
        for sample in (first, samples[-1]):
            for kind in ('actors', 'props'):
                resources.update(kind + ':' + key for key, state in sample['world'][kind].items()
                                 if (state['scene_id'] if kind == 'actors' else _anchor(sample['world'], state)[0]) == scene)
        for resource in resources:
            self.need(self.resource_ends.get(resource, 0) <= start, p,
                      'parallel edit reads an entity or scene from its future; replan the story timeline', 'not_supported')
        # Explicit remote gaze/action references require their scene's state to
        # be available at this story instant, even when they are outside frame.
        def references(value):
            if isinstance(value, dict):
                for item in value.values(): yield from references(item)
            elif isinstance(value, list):
                for item in value: yield from references(item)
            elif isinstance(value, str): yield value
        refs = set(references([row['camera'], actions, [state.get('gaze') for state in world['actors'].values()
                                                       if state['scene_id'] == scene]]))
        for kind in ('actors', 'props', 'points'):
            for key in refs & set(world[kind]):
                target_scene = _anchor(world, world[kind][key])[0] if kind == 'props' else world[kind][key]['scene_id']
                self.need(self.resource_ends.get('scene:' + target_scene, 0) <= start, p,
                          'cross-scene reference reads future state in a parallel edit', 'not_supported')
        if any(a['kind'] == 'cross_passage' for a in actions):
            self.need(start >= max(self.resource_ends.values(), default=0), p,
                      'cross-scene travel cannot enter any already replayed future scene', 'not_supported')
        if sid == segment['shot_ids'][0]:
            subjects = set(first['view']['actors']) | set(first['view']['props'])
            camera = _camera(world, row['camera'], scene, p + '.camera')
            subjects.update(key for key, point in world['points'].items() if point['scene_id'] == scene and
                            _in_frame(world, scene, camera, point['position_xyz']))
            self.need(set(segment['orientation_subject_ids']) <= subjects, p,
                      'entry recognition must bind actually visible subjects, not an empty transition explanation')
            for identity in set(segment['orientation_subject_ids']) & set(world['points']):
                key = '/initial_world/points/' + identity.replace('~', '~0').replace('/', '~1')
                proof = self.decisions[key]
                self.need(bool(proof['fact_ids'] or proof['source_spans']), p,
                          'entry landmark needs source/locked-setting evidence, not an invented location marker')
        local = {a['id']: a for a in actions}
        for a in actions:
            for dep in a['after']:
                finished = start + local[dep]['end'] if dep in local else self.event_ends.get(dep)
                self.need(finished is not None and finished <= start + a['start'], p,
                          'action prerequisite has not finished in story time')
        shown = {r['id'] for r in records if r['projection_status'] != 'hidden'}
        for a in actions:
            self.event_ends[a['id']] = start + a['end']
            if a['id'] in shown and a.get('source_use_id'):
                use = self.source['uses_by_id'][a['source_use_id']]
                self.deliveries.append({'shot_id': sid, 'id': a['id'], 'fact_ids': use['fact_ids'],
                                       'start': a['start'], 'end': a['end'],
                                       'co_delivery': a['kind'] in {'speak', 'narrate', 'inner_voice'}})
        for identity in first['view']['props']:
            ref = world['props'][identity]['written_ref']
            paper = self.source['written_objects_by_id'].get(ref)
            if paper and paper['state'] == 'current':
                self.deliveries.append({'shot_id': sid, 'id': 'written:' + sid + ':' + ref,
                    'fact_ids': self.source['uses_by_id'][paper['use_id']]['fact_ids'],
                    'start': 0, 'end': row['ticks'][-1], 'co_delivery': True})
        self.resource_ends.update({resource: end for resource in resources})
        self.observations.append({'shot_id': sid, 'segment_id': segment['id'], 'story_start_tick': start,
                                  'story_end_tick': end, 'state_order_status': 'pass'})

    def check_bridge(self, row, actions):
        start = self.clock[row['shot_id']] - row['transition']['bridge']['ticks']
        self.need(start >= max(self.resource_ends.values(), default=0), self.path,
                  'omitted multi-scene staging cannot rewind any replayed world state', 'not_supported')
        for a in actions:
            for dep in a['after']:
                local = next((x for x in actions if x['id'] == dep), None)
                end = start + local['end'] if local else self.event_ends.get(dep)
                self.need(end is not None and end <= start + a['start'], self.path,
                          'omitted staging depends on an unfinished story event')
            self.need(self.resource_ends.get('actors:' + a['actor'], 0) <= start, self.path,
                      'omitted staging reuses a future actor state', 'not_supported')
            self.event_ends[a['id']] = start + a['end']

    def finish(self):
        p = self.path; pending = []
        def where(fid, sid=None):
            return [d for d in self.deliveries if fid in d['fact_ids'] and (sid is None or d['shot_id'] == sid)]
        for segment in self.value['segments']:
            actual = {fid for d in self.deliveries if d['shot_id'] in segment['shot_ids'] for fid in d['fact_ids']}
            self.need(set(segment['purpose_fact_ids']) <= actual, p, 'scene narrative purpose has no actual delivery')
        for link in self.value['causal_links']:
            effects = where(link['fact_id'])
            if not effects: continue  # Undelivered source facts do not claim an on-screen event.
            mode = link['treatment']; cause = link['requires_fact_id']
            if mode == 'shown':
                causes = where(cause, link['establishment_shot_id'])
                for effect in effects:
                    self.need(any(self.shot_order[c['shot_id']] < self.shot_order[effect['shot_id']] or
                        (c['shot_id'] == effect['shot_id'] and (c['end'] <= effect['start'] or
                         c['id'] == effect['id'] and c['co_delivery'])) for c in causes), p,
                        'causal prerequisite has no prior actual audience delivery; source knowledge is not screen coverage')
            elif mode == 'withheld':
                if link['reveal_shot_id'] is not None:
                    reveals = where(cause, link['reveal_shot_id'])
                    first_effect = min(self.shot_order[e['shot_id']] for e in effects)
                    self.need(reveals and all(self.shot_order[r['shot_id']] > first_effect for r in reveals) and
                              all(self.shot_order[c['shot_id']] > first_effect for c in where(cause)),
                              p, 'suspense must withhold the cause initially and actually reveal it later')
                else:
                    self.need(not where(cause), p, 'future suspense cannot be declared when this batch already reveals the cause')
                    pending.append({'fact_id': link['fact_id'], 'requires_fact_id': cause,
                                    'future_source_span': link['future_source_span'], 'status': 'payoff_pending_outside_batch'})
        self.need(not pending or self.scope != 'complete_series', p,
                  'a complete-series claim cannot leave declared future payoffs outside its scope', 'insufficient_evidence')
        return {'contract': CONTRACT, 'status': 'pass', 'story_time': self.observations,
                'causal_link_count': len(self.value['causal_links']), 'pending_payoffs': pending,
                'prior_delivery_count': len(self.value['prior_deliveries']),
                'prior_delivery_authority': 'requires_server_artifact_lookup' if self.value['prior_deliveries'] else 'not_applicable',
                'semantic_status': 'pending_independent_review', 'semantic_accuracy_percent': None}


def verify_prior_deliveries(narrative, load_artifact):
    """A quoted original novel is not proof the audience saw a previous episode."""
    need, _, _, _ = _helpers()
    cache = {}
    for row in narrative['prior_deliveries']:
        need(load_artifact is not None, 'narrative.prior_deliveries',
             'prior audience knowledge needs server-side same-project artifact lookup', 'insufficient_evidence')
        aid = row['artifact_id']
        if aid not in cache:
            raw = load_artifact(aid)
            cache[aid] = (hashlib.sha256(raw).hexdigest(), json.loads(raw))
        fingerprint, value = cache[aid]
        need(fingerprint == row['artifact_sha256'], 'narrative.prior_deliveries', 'prior artifact changed')
        shots = [s for s in value.get('shots', []) if s.get('镜头编号') == row['shot_id']]
        need(len(shots) == 1 and isinstance(shots[0].get(row['field']), str) and row['quote'] in shots[0][row['field']], 'narrative.prior_deliveries',
             'prior audience evidence is absent from the actual storyboard field')
