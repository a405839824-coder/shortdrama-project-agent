"""Source-bound performance evidence, never seven compulsory actor motions.

The v3 plan owns the director's declarations; the lowered timeline owns modelled
events and states. This projection joins those two inputs without interpreting
their prose as proof of source entailment, emotion, naturalness or visibility.
Only production_derivation's full replay can authorize the generated projection.
Legacy authored performance_chains keep their existing gate.
"""
from __future__ import annotations

import copy

CONTRACT = 'performance-evidence-v1'
FIELD = 'performance_evidence'
CHAIN_FIELDS = ('stimulus', 'listen_or_observe_reaction', 'gaze_path',
                'micro_expression', 'ecological_action', 'body_or_breath', 'end_state')
DESIGN_FIELDS = ('stimulus', 'visible_choice', 'gesture_choice', 'voice_choice')


def _sources(value):
    return value.to_dict() if hasattr(value, 'to_dict') else value


def _present(value):
    return value is not None and value != [] and value != {} and (not isinstance(value, str) or bool(value.strip()))


def _objects(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _mapping(value):
    return value if isinstance(value, dict) else {}


def chain_relevant(function, has_dialogue):
    return bool(has_dialogue) or any(word in str(function) for word in ('情绪', '关系', '冲突', '反应', '内心'))


def inspect_performance_input(payload, sources=None, *, raw_plan=None):
    """Independent input diagnostics; do not guess failed source compilation.

Actual actor visibility and state feasibility still require lowering. A valid
plan's declarations replace a second hand-written prose chain, not its evidence
obligation. This function cannot authorize a generated evidence block.
    """
    result = {'basis': 'unknown', 'findings': [], 'semantic_status': 'unknown',
              'deferred_checks': ['actual visibility and event/state replay',
                                  'source entailment and performance meaning']}
    if not isinstance(payload, dict):
        return result
    evidence = _mapping(_mapping(payload.get('project')).get('prevention_evidence'))
    def fault(path, message):
        result['findings'].append({'path': path, 'message': message, 'code': 'performance_evidence_requirement'})
    if FIELD in evidence:
        fault('project.prevention_evidence.' + FIELD,
              'compiler-owned performance evidence cannot be supplied as author input')
    source = _sources(sources)
    source_verified = isinstance(source, dict)
    if not source_verified and not isinstance(raw_plan, dict):
        return result
    source = source if source_verified else {}
    plan = source.get('production_plan') if source_verified else raw_plan
    if isinstance(plan, dict) and plan.get('contract') == 'storyboard-production-plan-v3':
        result['basis'] = 'frozen_v3_plan' if source_verified else 'unverified_v3_plan_shape'
        if not source_verified:
            result['deferred_checks'].append('source compilation and plan identity')
        plan_rows = {s['shot_id']: s for s in _objects(plan.get('shots')) if isinstance(s.get('shot_id'), str)}
        uses = _mapping(source.get('uses_by_id'))
        if not source_verified:
            # Read identity only, with an explicitly unverified shape basis.
            # Failed source compilation must not suppress an independently
            # determinable missing declared speaker, nor certify source truth.
            uses = {use['id']: use for use in _objects(_mapping(evidence.get('source_decisions')).get('uses'))
                    if isinstance(use.get('id'), str)}
        for shot in _objects(payload.get('shots')):
            sid = shot.get('shot_id')
            if not isinstance(sid, str):
                continue  # Identity/schema failures belong to their own checks.
            spec = plan_rows.get(sid, {})
            rows = _mapping(spec.get('design')).get('performance')
            path = '$graph.production_plan.shots.' + str(sid) + '.design.performance'
            if not isinstance(rows, list):
                fault(path, 'requires explicit per-actor performance declarations; [] is only for an actorless shot')
                continue
            identities = []
            for index, row in enumerate(rows):
                p = path + '[' + str(index) + ']'
                if not isinstance(row, dict):
                    fault(p, 'performance declaration must be an object')
                    continue
                identity = row.get('actor')
                if not isinstance(identity, str):
                    fault(p + '.actor', 'actor identity must be a string')
                identities.append(identity if isinstance(identity, str) else None)
                missing = [key for key in ('actor', 'fact_ids', *DESIGN_FIELDS) if not _present(row.get(key))]
                if missing:
                    fault(p, 'missing explicit source-bound performance declarations: ' + ', '.join(missing))
            if len(set(identities)) != len(identities):
                fault(path, 'duplicate actor performance declaration')
            required = {v['subject'] for v in _objects(spec.get('visible'))
                        if isinstance(v.get('kind'), str) and v['kind'] in {'actor', 'eyes', 'left_hand', 'right_hand'}
                        and isinstance(v.get('subject'), str)}
            for action in _objects(shot.get('actions')):
                if action.get('kind') == 'speak' and isinstance(action.get('source_use_id'), str):
                    speaker = _mapping(uses.get(action['source_use_id'])).get('actor')
                    if isinstance(speaker, str):
                        required.add(speaker)
            missing = required - set(identities) - {None}
            if missing:
                fault(path, 'missing declared visible actor/speaker choices: ' + ', '.join(sorted(missing)))
        return result
    if not source_verified:
        return result
    result['basis'] = 'legacy_chain'
    chains = evidence.get('performance_chains')
    by_shot = {row['shot_id']: row for row in _objects(chains) if isinstance(row.get('shot_id'), str)}
    for shot in _objects(payload.get('shots')):
        if not isinstance(shot.get('shot_id'), str):
            continue
        if not chain_relevant(_mapping(shot.get('editorial')).get('功能类型'),
                              any(a.get('kind') == 'speak' for a in _objects(shot.get('actions')))):
            continue
        sid = shot.get('shot_id')
        missing = [name for name in CHAIN_FIELDS if not _present(by_shot.get(sid, {}).get(name))]
        if missing:
            fault('project.prevention_evidence.performance_chains:' + str(sid),
                  'legacy performance chain lacks evidence fields: ' + ', '.join(missing))
    return result


def _actor_state(state, actor):
    progress = state.get('动作进度', {})
    return {'position': copy.deepcopy(state.get('人物位置', {}).get(actor)),
            'orientation': copy.deepcopy(state.get('身体朝向', {}).get(actor)),
            'gaze': copy.deepcopy(state.get('视线落点', {}).get(actor)),
            'eye_state': progress.get('眼态', {}).get(actor),
            'performance': copy.deepcopy(progress.get('表演状态', {}).get(actor)),
            'action': copy.deepcopy(progress.get('动作状态', {}).get(actor))}


def _field(*, bound, declaration=None, modelled=None, references=()):
    return {'evidence_status': 'bound' if bound else 'unknown',
            'declaration': copy.deepcopy(declaration), 'modelled': copy.deepcopy(modelled),
            'references': list(references), 'semantic_status': 'unknown'}


def _gaze_coverage(record, actor):
    """Observe actual compiler samples, never infer a trajectory from endpoints.

Eye landmarks in frame do not imply open eyes. The compiler retains one sample
per tick including the terminal state; visible intervals are half-open, so only
ticks strictly before the shot end have screen duration. Missing/unknown samples
cannot establish either a continuously resolved gaze or continuous closure.
    """
    from production_derivation import digest
    result = {'status': 'unknown', 'reason': 'complete actual eye/gaze timeline unavailable',
              'timeline_sha256': None, 'visible_ticks': [], 'closed_visible_ticks': [],
              'required_gaze_ticks': [], 'unknown_ticks': [], 'semantic_status': 'unknown'}
    timeline = _mapping(record.get('performance_eye_timeline'))
    duration = timeline.get('duration_ticks')
    samples = _mapping(timeline.get('actors')).get(actor)
    if (timeline.get('contract') != 'performance-eye-timeline-v1' or timeline.get('tick_hz') != 10
            or not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0
            or not isinstance(samples, list) or len(samples) != duration + 1):
        return result
    if any(not isinstance(sample, dict) or sample.get('tick') != tick
           or not isinstance(sample.get('eye_visible'), bool)
           or 'eye_state' not in sample or 'gaze' not in sample
           for tick, sample in enumerate(samples)):
        return result
    windows = record.get('eye_visibility_ticks', {}).get(actor)
    if not isinstance(windows, list) or any(
            not isinstance(window, list) or len(window) != 2
            or any(not isinstance(t, int) or isinstance(t, bool) for t in window)
            or not 0 <= window[0] < window[1] <= duration for window in windows):
        return result
    visible = [sample for sample in samples[:-1] if sample['eye_visible']]
    visible_ticks = [sample['tick'] for sample in visible]
    if [tick for start, end in windows for tick in range(start, end)] != visible_ticks:
        return result
    result.update(timeline_sha256=digest({'contract': timeline['contract'], 'tick_hz': 10,
        'duration_ticks': duration, 'actor': actor, 'samples': samples}), visible_ticks=visible_ticks)
    for sample in visible:
        tick, state = sample['tick'], sample['eye_state']
        if state == 'closed':
            result['closed_visible_ticks'].append(tick)
        else:
            result['required_gaze_ticks'].append(tick)
            # A blink still retains the real target. Neither an unrecognized
            # state nor absent gaze is converted into a closed-eye exemption.
            if state not in ('natural_open', 'blinking') or not isinstance(sample['gaze'], dict):
                result['unknown_ticks'].append(tick)
    if result['unknown_ticks']:
        result['reason'] = 'visible non-closed or unknown eye state lacks actual resolved gaze at recorded ticks'
    elif not visible:
        result.update(status='not_applicable', reason='no modelled visible-eye interval')
    elif len(result['closed_visible_ticks']) == len(visible):
        result.update(status='not_applicable', reason='every actual visible tick has explicitly closed eyes')
    else:
        result.update(status='bound', reason='actual gaze retained at every visible non-closed tick')
    return result


def derive_performance_evidence(payload, lowered, source_compilation):
    """Pure projection of an actual successful lower; replay is its authority.

No missing value is populated with an action, a neutral pose or PASS. Unknown
eye/breath/expression remains unknown in the recorded state. A modelled static
pose is evidence without requiring a blink, inhale, gesture or gaze shift.
    """
    from production_derivation import digest
    source = _sources(source_compilation)
    trace = lowered['derivation']
    report = trace.get('production_prevention', {})
    plan = (source or {}).get('production_plan')
    if report.get('status') != 'pass' or not isinstance(plan, dict) or plan.get('contract') != 'storyboard-production-plan-v3':
        return None
    if FIELD in payload['project']['prevention_evidence']:
        raise ValueError('compiler-owned performance evidence cannot be supplied as author input')
    typed = {row['shot_id']: row for row in lowered['typed_decisions']['shots']}
    plans = {row['shot_id']: row for row in plan['shots']}
    uses = source['uses_by_id']
    rows = []
    for record in trace['shots']:
        sid = record['shot_id']; row = typed[sid]; spec = plans[sid]
        events = [event for event in trace['world_events'] if event['shot_id'] == sid]
        projections = {event['id']: event for event in record['actions']}
        actor_rows = []
        for declared in spec['design']['performance']:
            actor = declared['actor']; start = _actor_state(row['start_state'], actor); end = _actor_state(row['end_state'], actor)
            actor_events = []
            for event in events:
                if event['actor'] != actor:
                    continue
                projection = projections[event['id']]
                uid = projection.get('source_use_id')
                actor_events.append({'event': copy.deepcopy(event), 'projection': copy.deepcopy(projection),
                                     'source_use': copy.deepcopy(uses.get(uid)) if uid is not None else None})
            event_ids = [e['event']['id'] for e in actor_events]
            eye_windows = copy.deepcopy(record['eye_visibility_ticks'].get(actor))
            facts = list(declared['fact_ids'])
            refs = ['fact:' + fid for fid in facts] + ['event:' + eid for eid in event_ids]
            refs += ['state:' + record['start_state_sha256'], 'state:' + record['end_state_sha256']]
            acquisitions = [copy.deepcopy(a) for a in plan['knowledge']['acquisitions']
                            if a['actor'] == actor and a['action_id'] in {e['id'] for e in events}]
            explicit = all(isinstance(declared.get(k), str) and declared[k].strip() for k in DESIGN_FIELDS)
            have_state = bool(start['position'] and end['position'] and start['orientation'] and end['orientation'])
            gaze_coverage = _gaze_coverage(record, actor)
            expression_known = all(isinstance(s['action'], dict) and s['action'].get('expression') not in (None, '', 'unknown') for s in (start, end))
            fields = {
                'stimulus': _field(bound=bool(facts) and explicit, declaration=declared['stimulus'], references=refs),
                'listen_or_observe_reaction': _field(bound=bool(facts) and explicit and have_state,
                    declaration={k: declared[k] for k in ('visible_choice', 'gesture_choice', 'voice_choice')},
                    modelled={'acquisitions_in_shot': acquisitions, 'actor_event_ids': event_ids,
                              'causal_meaning': 'unknown'}, references=refs),
                'gaze_path': _field(bound=gaze_coverage['status'] == 'bound', modelled={'start': start['gaze'], 'end': end['gaze'],
                    'eye_visibility_ticks': eye_windows,
                    'tick_coverage': gaze_coverage,
                    'event_ids': [e['event']['id'] for e in actor_events if e['event']['kind'] in ('look', 'focus_adjust', 'turn')]}, references=refs),
                'micro_expression': _field(bound=expression_known, declaration=declared['visible_choice'],
                    modelled={'start': (start['action'] or {}).get('expression'), 'end': (end['action'] or {}).get('expression'),
                              'event_ids': [e['event']['id'] for e in actor_events if e['event']['kind'] == 'expression'],
                              'face_visibility': 'unknown'}, references=refs),
                'ecological_action': _field(bound=explicit and have_state, declaration=declared['gesture_choice'],
                    modelled={'actor_event_ids': event_ids, 'start': start['action'], 'end': end['action']}, references=refs),
                'body_or_breath': _field(bound=have_state,
                    modelled={'start_body': start['orientation'], 'end_body': end['orientation'],
                              'start_performance': start['performance'], 'end_performance': end['performance'],
                              'breath_visibility': 'unknown'}, references=refs),
                'end_state': _field(bound=have_state, modelled=end, references=refs),
            }
            # Only complete actual timeline observations can make visible gaze
            # inapplicable. Keep unresolved underlying values, including None in
            # the bound trace; never manufacture a target for closed eyes.
            if gaze_coverage['status'] == 'not_applicable':
                fields['gaze_path']['evidence_status'] = 'not_applicable'
                fields['gaze_path']['reason'] = gaze_coverage['reason'] + '; underlying gaze retained, not asserted visible'
            # A validated prop emitter has no body to perform these fields.
            # Keep its real voice events and source/knowledge evidence; text
            # claiming an unseen speaker alone never grants this applicability.
            unbodied = (actor not in payload['initial_world']['actors'] and bool(actor_events)
                and all(e['event']['kind'] == 'speak'
                        and e['event'].get('emitter', {}).get('mode') == 'disembodied'
                        and e['projection'].get('emitter') == e['event']['emitter']
                        for e in actor_events))
            if unbodied:
                fields['listen_or_observe_reaction']['evidence_status'] = 'bound' if facts and explicit else 'unknown'
                for name in ('gaze_path', 'micro_expression', 'ecological_action', 'body_or_breath', 'end_state'):
                    fields[name].update(evidence_status='not_applicable',
                        reason='replayed source-bound disembodied prop voice; no bodily actor',
                        emitter_events=copy.deepcopy(actor_events))
            actor_rows.append({'actor': actor, 'fact_ids': facts, 'authored_design': copy.deepcopy(declared),
                               'start_state': start, 'end_state': end, 'events': actor_events, 'fields': fields})
        rows.append({'shot_id': sid, 'applicability': 'actor_performance' if actor_rows else 'no_modelled_actor_or_speaker',
                     'actors': actor_rows, 'start_state_sha256': record['start_state_sha256'],
                     'end_state_sha256': record['end_state_sha256']})
    return {'contract': CONTRACT, 'input_sha256': digest(payload), 'source_graph_sha256': source['graph_sha256'],
            'plan_sha256': report['plan_sha256'], 'action_trace_sha256': digest(trace), 'shots': rows,
            'semantic_status': 'unknown', 'independent_review_required': True,
            'scope': 'source-linked declarations and modelled event/state evidence; not source entailment, causal meaning, naturalness or rendered visibility'}


def inspect_performance_evidence(candidate, production_binding):
    """Return only evidence recomputed by the existing complete binding replay."""
    submitted = candidate.get('prevention_evidence', {}).get(FIELD)
    expected = production_binding.get(FIELD) if production_binding.get('valid') is True else None
    verified = isinstance(expected, dict) and expected == submitted
    rows = expected.get('shots', []) if verified else []
    unknown = [{'shot_id': row['shot_id'], 'actor': actor['actor'], 'field': name}
               for row in rows for actor in row['actors'] for name, value in actor['fields'].items()
               if value['evidence_status'] == 'unknown']
    return {'present': submitted is not None, 'verified': verified,
            'basis': 'replayed_source_plan_actions_states' if verified else 'legacy_chain',
            'semantic_status': 'unknown', 'independent_review_required': True,
            'unknown_evidence': unknown, 'shots': copy.deepcopy(rows)}
