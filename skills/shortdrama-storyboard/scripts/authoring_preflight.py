"""Collect independent authoring checks without filling invalid inputs.

Uses the renderer's existing primitives. A skipped dependency is recorded as
not_checked, never as a zero-finding pass. This does not grant semantic approval.
"""
from __future__ import annotations

import copy
from typing import Any, Callable


class CheckCollection:
    def __init__(self):
        self.checks: list[dict] = []
        self.findings: list[dict] = []
        self.values: dict[str, Any] = {}
        self.timing_diagnostics: dict[str, Any] | None = None

    def run(self, name: str, fn: Callable, *, requires=(), path='$'):
        missing = [key for key in requires if key not in self.values]
        if missing:
            self.checks.append({'check': name, 'status': 'not_checked',
                                'depends_on': list(requires), 'blocked_by': missing})
            return None
        try:
            value = fn()
        except Exception as exc:
            found = getattr(exc, 'findings', None)
            if not found:
                found = [{'path': getattr(exc, 'path', path),
                          'message': getattr(exc, 'reason', f'{type(exc).__name__}: {exc}')}]
            normalized = []
            for item in found:
                if hasattr(item, 'to_dict'):
                    item = item.to_dict()
                if not isinstance(item, dict):
                    item = {'path': path, 'message': str(item)}
                record = {'path': item.get('path', path),
                          'message': item.get('message', item.get('reason', str(item))),
                          'check': name}
                normalized.append(record)
                if not any(x['path'] == record['path'] and x['message'] == record['message']
                           for x in self.findings):
                    self.findings.append(record)
            self.checks.append({'check': name, 'status': 'fail',
                                'depends_on': list(requires), 'findings': normalized})
            return None
        self.values[name] = value
        self.checks.append({'check': name, 'status': 'pass', 'depends_on': list(requires)})
        return value

    def report(self):
        return {'contract': 'authoring-check-coverage-v1', 'checks': self.checks,
                'findings': self.findings,
                'not_checked': [x for x in self.checks if x['status'] == 'not_checked'],
                'timing_diagnostics': copy.deepcopy(self.timing_diagnostics) if self.timing_diagnostics is not None else {
                    'status': 'not_checked', 'reason': '计时检查前置条件尚未满足，不能当作零问题通过',
                    'actual_audio_verified': False, 'grant_approval': False},
                'semantic_audit': 'not_performed', 'commit_eligible': False}


def inspect_typed_decisions(decisions: Any, *, renderer=None) -> dict:
    # Lazy import avoids a cycle: the public recording entry invokes this file.
    if renderer is None:
        import author_storyboard as a
    else:
        a = renderer
    from timing_model import validate_timing
    c = CheckCollection()

    def root_shape():
        a._check(isinstance(decisions, dict) and set(decisions) == {'contract', 'project', 'shots'},
                 '$', 'requires exactly contract, project and shots')
        a._check(decisions['contract'] in (a.CONTRACT, a.BOUND_CONTRACT), 'contract',
                 'unsupported typed authoring contract')
        a._check(isinstance(decisions['project'], dict), 'project', 'requires an object')
        a._check(isinstance(decisions['shots'], list) and bool(decisions['shots']),
                 'shots', 'requires at least one complete shot decision')
    c.run('input_shape', root_shape)
    if 'input_shape' not in c.values:
        c.run('shot_checks', lambda: None, requires=('input_shape',))
        return c.report()
    bound = decisions['contract'] == a.BOUND_CONTRACT
    required = set(a.ShotDecision.__annotations__) | ({'staging'} if bound else set())
    parts: list[dict] = []
    for index, row in enumerate(decisions['shots']):
        prefix = f'shots[{index}]'
        def name(suffix):
            return prefix + '.' + suffix
        def shape():
            a._check(isinstance(row, dict) and set(row) == required, prefix,
                     'requires exactly the documented ShotDecision fields; no silent field removal')
        c.run(name('shape'), shape, path=prefix)
        if name('shape') not in c.values:
            for suffix in ('fields', 'duration', 'phases', 'start_state', 'end_state', 'events'):
                c.run(name(suffix), lambda: None, requires=(name('shape'),), path=prefix)
            continue

        def fields():
            value = row['fields']
            a._check(isinstance(value, dict) and set(value) == set(a.FIELDS) - a.COMPUTED,
                     name('fields'), 'requires exactly the noncomputed canonical fields')
            return copy.deepcopy(value)
        c.run(name('fields'), fields, path=name('fields'))
        c.run(name('duration'), lambda: a._ticks(row['duration'], name('duration'), positive=True), path=name('duration'))
        for endpoint in ('start_state', 'end_state'):
            c.run(name(endpoint), lambda endpoint=endpoint: a._state(row[endpoint], name(endpoint)), path=name(endpoint))

        def phase_text():
            phases = row['phases']
            a._check(isinstance(phases, list) and bool(phases), name('phases'), 'requires phase objects')
            values = []
            for pi, phase in enumerate(phases):
                pp = f'{prefix}.phases[{pi}]'
                a._check(isinstance(phase, dict) and set(phase) == {'start', 'end', 'text'}, pp,
                         'requires exactly start, end and text')
                left = a._ticks(phase['start'], pp + '.start')
                right = a._ticks(phase['end'], pp + '.end')
                text = a._text(phase['text'], pp + '.text')
                values.append((left, right, text))
            return values
        c.run(name('phase_text'), phase_text, path=name('phases'))

        def phases():
            values = c.values[name('phase_text')]
            duration = c.values[name('duration')]
            a._check(len(values) == 3, name('phases'), 'requires exactly three phases')
            cursor = 0
            for pi, (left, right, text) in enumerate(values):
                pp = f'{prefix}.phases[{pi}]'
                a._check(left == cursor and right > left and right <= duration, pp,
                         'phase must be positive, contiguous and inside the shot')
                cursor = right
            a._check(cursor == duration, name('phases'), 'phases must close exactly on duration')
            picture = '\n'.join(f'【{left / 10:.1f}—{right / 10:.1f}秒】{text}' for left, right, text in values)
            raw_fields = row['fields'] if isinstance(row['fields'], dict) else {}
            faults = a._validate_timeline(picture, duration / 10, strict=True,
                                          visible_subjects=raw_fields.get('可见主体'))
            a._check(not faults, name('phases'), '; '.join(faults))
            return picture
        c.run(name('phases'), phases, requires=(name('phase_text'), name('duration')), path=name('phases'))

        def event_structure():
            a._check(isinstance(row['events'], list), name('events'), 'requires explicit events array')
            duration = c.values[name('duration')]
            for ei, event in enumerate(row['events']):
                ep = f'{prefix}.events[{ei}]'
                a._check(isinstance(event, dict) and 'shot_id' not in event, ep,
                         'shot_id is bound by the owning shot, not a second copy')
                for key in ('id', 'actor', 'action', 'basis'):
                    a._text(event.get(key), ep + '.' + key)
                a._identity(event['id'], ep + '.id'); a._identity(event['actor'], ep + '.actor')
                left = a._ticks(event.get('start'), ep + '.start')
                right = a._ticks(event.get('end'), ep + '.end')
                a._check(0 <= left < right <= duration, ep, 'event bounds must fit its shot')
            return copy.deepcopy(row['events'])
        c.run(name('events'), event_structure, requires=(name('duration'),), path=name('events'))
        if bound:
            c.run(name('staging'), lambda: a._bind_staging_fields(row['fields'], row['staging']),
                  requires=(name('fields'),), path=name('staging'))
            def speech():
                picture = '\n'.join(text for _, _, text in c.values[name('phase_text')])
                return picture, a.extract_dialogues(picture)
            c.run(name('speech'), speech, requires=(name('phase_text'),), path=name('phases'))
            def voice():
                picture, dialogue_rows = c.values[name('speech')]
                value = row['fields']['音色']
                if dialogue_rows:
                    faults = a._voice_template_errors(value, ''.join(t for _, t in dialogue_rows), dialogue_rows)
                    a._check(not faults, name('fields.音色'), '; '.join(faults))
                else:
                    a._check(bool(a.VOICE_NO_DIALOGUE_RE.fullmatch(str(value).strip())),
                             name('fields.音色'), '无对白镜必须填写无对白，不适用。')
            c.run(name('voice'), voice, requires=(name('fields'), name('speech')), path=name('fields.音色'))
            def punctuation():
                picture, dialogue_rows = c.values[name('speech')]
                faults = a._validate_punctuation(''.join(t for _, t in dialogue_rows),
                    str(row['fields'].get('音色') or '')) if dialogue_rows else []
                a._check(not faults, name('fields.音色'), 'PUNCT: ' + '、'.join(faults))
            c.run(name('punctuation'), punctuation, requires=(name('speech'), name('fields')), path=name('fields.音色'))
        parts.append({'row': row, 'prefix': prefix})

    endpoint_checks = tuple(f'shots[{i}].{side}' for i in range(len(decisions['shots']))
                            for side in ('shape', 'start_state', 'end_state'))
    c.run('state_dependencies', lambda: a._state_dependency_diagnostics(decisions),
          requires=endpoint_checks, path='shots')

    # A provisional view contains actual authored values only and is never
    # published. Timing does not depend on VOICE format or state geometry.
    prerequisites = tuple(f'shots[{i}].{part}' for i in range(len(decisions['shots']))
                          for part in ('shape', 'fields', 'duration', 'phases', 'events'))
    def timing():
        project = copy.deepcopy(decisions['project'])
        shots = []; events = []; total = 0
        for part in parts:
            row, prefix = part['row'], part['prefix']
            fields = copy.deepcopy(row['fields'])
            ticks = c.values[prefix + '.duration']; total += ticks
            fields.update({'镜头编号': row['shot_id'], 'shot_uid': row['shot_uid'],
                           'episode_id': row['episode_id'], '时长': ticks / 10,
                           '累计时长': total / 10, '画面内容': c.values[prefix + '.phases']})
            shots.append(fields)
            events.extend(dict(event, shot_id=row['shot_id']) for event in row['events'])
        evidence = project.get('prevention_evidence')
        a._check(isinstance(evidence, dict), 'project.prevention_evidence', 'requires explicit evidence')
        workflow = evidence.get('audio_workflow')
        a._check(isinstance(workflow, dict) and isinstance(workflow.get('timing'), dict),
                 'project.prevention_evidence.audio_workflow.timing', 'requires explicit timing metadata')
        a._check('events' not in workflow['timing'], 'project.prevention_evidence.audio_workflow.timing.events',
                 'events belong to shot decisions; conflicting copies are refused')
        workflow['timing']['events'] = events
        project.update(shots=shots, total_duration=total / 10)
        timing_result = validate_timing(project, shots)
        c.timing_diagnostics = {'summary': timing_result['metrics']['planning_summary'],
                                'metrics': timing_result['metrics'], 'advisories': timing_result['advisories'],
                                'faults': timing_result['faults']}
        faults = timing_result['faults']
        if faults:
            raise a.RejectedDecisions([{'path': f"timing:{fault.get('shot_id', '')}",
                                       'message': fault['message']} for fault in faults])
    c.run('complete_timing', timing, requires=prerequisites, path='timing')
    return c.report()


def inspect_production_editorial(decisions: Any, *, renderer=None) -> dict:
    """VOICE is independently inspectable even when source compilation fails.

    Reading untrusted use text here is only a formatting diagnostic. It does not
    create a SourceCompilation, a rendered candidate, or source approval.
    """
    if renderer is None:
        import author_storyboard as a
    else:
        a = renderer
    c = CheckCollection()
    if not isinstance(decisions, dict) or not isinstance(decisions.get('shots'), list):
        return c.report()
    project = decisions.get('project', {})
    prevention = project.get('prevention_evidence', {}) if isinstance(project, dict) else {}
    source = prevention.get('source_decisions', {}) if isinstance(prevention, dict) else {}
    uses = source.get('uses', []) if isinstance(source, dict) else []
    use_map = {u.get('id'): u for u in uses if isinstance(u, dict) and isinstance(u.get('id'), str)} if isinstance(uses, list) else {}
    for i, row in enumerate(decisions['shots']):
        if not isinstance(row, dict):
            continue
        path = f'shots[{i}].actions.voice'
        def check(row=row, path=path):
            from authoring_contract_blocks import voice_block
            actions = row.get('actions')
            a._check(isinstance(actions, list), f'shots[{i}].actions', 'requires actions list')
            utterances = []
            for action in actions:
                if not isinstance(action, dict) or action.get('type', action.get('kind')) != 'speak':
                    continue
                use_id = action.get('source_use_id', action.get('use_id'))
                use = use_map.get(use_id, {})
                actor = action.get('actor', use.get('actor'))
                text = use.get('text')
                a._check(isinstance(actor, str) and isinstance(text, str) and bool(text), path,
                         'speech formatting cannot be checked without actual actor and source-use text')
                utterances.append((actor, text))
                a._check(isinstance(action.get('voice'), dict), path, 'requires explicit voice settings')
                rendered_voice = voice_block(mode='spoken', dialogue_rows=[(actor, text)], **action['voice'])
                faults = a._voice_template_errors(rendered_voice, text, [(actor, text)])
                a._check(not faults, path, '; '.join(faults))
        c.run(path, check, path=path)
    return c.report()
