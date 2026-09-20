"""Reproducible generation bindings, never an author-issued audit certificate.

The embedded input is recompiled on inspection. Changing generated fields or
compiler code invalidates old bindings. Independent review uses the existing
service audit and signs the current artifact, not a mutable PASS in this object.
"""
from __future__ import annotations

import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path
import sys

CONTRACT = 'production-derivation-v1'
FIELD = 'production_derivation'
SCRIPT_DIR = Path(__file__).resolve().parent
DEPENDENCIES = (
    'production_derivation.py', 'production_decisions.py', 'source_decisions.py', 'production_input.py',
    'production_environment.py', 'production_actions.py', 'source_action_plans.py',
    'production_plan.py', 'episode_rules.py', 'field_content.py',
    'author_storyboard.py', 'authoring_preflight.py', 'production_state.py',
    'staging_geometry.py', 'staging_text_bindings.py', 'authoring_contract_blocks.py',
    'dynamic_eye_decisions.py', 'timing_model.py', 'validate_storyboard.py',
    'written_payload.py', 'boundary_decisions.py',
    'timing_events.py', 'performance_checks.py', 'lighting_checks.py',
    'picture_presentation.py',
    'source_content_limits.py', 'repair_observations.py',
)


def _json_numbers(value):
    """Bind JSON number values across runtimes without changing source text.

    JavaScript represents 0.0 and 0 identically. Preserve all string bytes,
    booleans, fractional numbers, sequence order and actual numeric changes.
    Non-finite numbers still fail in the strict JSON encoder below.
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _json_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_numbers(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(_json_numbers(value), ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def compiler_fingerprints():
    found = {name: hashlib.sha256((SCRIPT_DIR / name).read_bytes()).hexdigest() for name in DEPENDENCIES}
    # Include transitive action/bridge/voice helpers as well as direct imports.
    # An unchanged input cannot reuse a replay after any runtime helper changes.
    for path in sorted(SCRIPT_DIR.glob('*.py')):
        found.setdefault(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
    refs = SCRIPT_DIR.parent / 'references'
    # Local names are portable; the exact deployed rule/schema bytes matter.
    for path in sorted(refs.glob('*.json')):
        found['references/' + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('materializer.py', 'canonical.py', 'delta.py'):
        path = SCRIPT_DIR.parents[2] / 'server/shortdrama_mcp' / name
        if path.is_file():
            found['server/' + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def finalize_generated_candidate(candidate):
    """Use exactly the service's permitted compaction and stable-ID boundary.

    Bind only the finalized canonical. Replaying service preparation must not
    change its bytes or invalidate generation evidence. The source checkout or
    installed server package is required; no separate approximate normalizer.
    """
    server_root = SCRIPT_DIR.parents[2] / 'server'
    if server_root.is_dir() and str(server_root) not in sys.path:
        sys.path.insert(0, str(server_root))
    from shortdrama_mcp.canonical import compact_storyboard_canonical
    from shortdrama_mcp.delta import ensure_shot_uids
    return ensure_shot_uids(compact_storyboard_canonical(candidate), in_place=True)[0]


def owned_content(obj):
    """Content generated from decisions; review attachments may be added later.

    A review attachment cannot change any of these fields. Existing service
    signing binds the whole artifact including independently supplied reviews.
    """
    evidence = obj.get('prevention_evidence', {})
    return {'root': {key: obj.get(key) for key in ('project_version', 'schema_version', 'schema_profile',
             'workflow_profile', 'field_order', 'target_model', 'project_id', 'episode_id', 'total_duration',
             'source_lock', 'scene_anchors', 'shots')},
            'source_ledger': evidence.get('source_ledger'),
            'source_decisions': evidence.get('source_decisions'),
            'episode_contract': evidence.get('episode_contract'),
            'borrow_ledger': evidence.get('borrow_ledger'),
            'source_ingestion': obj.get('source_ingestion'),
            'chapter_intake': obj.get('chapter_intake'),
            'scene_state_ledger': evidence.get('scene_state_ledger'),
            'audio_timing': evidence.get('audio_workflow', {}).get('timing'),
            'authoring_evidence': evidence.get('authoring_evidence')}


def _source_compile(input_decisions, graph_bytes):
    from source_decisions import compile_source_decisions
    evidence = input_decisions['project']['prevention_evidence']
    rows = evidence['source_ledger']
    texts = {row['source_id']: row['source_text'] for row in rows}
    if len(texts) != len(rows):
        raise ValueError('duplicate source IDs')
    return compile_source_decisions(graph_bytes, evidence['source_decisions'], source_texts=texts,
                                    expected_graph_sha256=hashlib.sha256(graph_bytes).hexdigest())


def _obligations(candidate, compiled, derivation):
    """Bind each source use to actual owning fields and the entire derivation.

    Static paper may persist across multiple shots. Full candidate content is
    bound so changing geometry, time, or another output invalidates old IDs too.
    """
    uses = compiled['uses_by_id']; papers = compiled['written_objects_by_id']
    usage = {uid: set() for uid in uses}
    for shot in derivation['shots']:
        sid = shot['shot_id']
        for event in shot['actions']:
            uid = event.get('source_use_id')
            if uid in usage:
                if event.get('kind') in {'narrate', 'inner_voice'}:
                    usage[uid].add(f'field:{sid}.心理音轨')
                else:
                    usage[uid].add(f'field:{sid}.画面内容')
    shots = {shot['镜头编号']: shot for shot in candidate['shots']}
    for record in derivation['shots']:
        shot = shots[record['shot_id']]
        for paper_id in record.get('visible_current_paper_refs', []):
            paper = papers[paper_id]
            if paper['text'] not in shot['起始状态']:
                raise ValueError('visible paper missing from actual frame-zero text: ' + paper_id)
            usage[paper['use_id']].add(f"field:{shot['镜头编号']}.起始状态")
    content_hash = digest(owned_content(candidate))
    derivation_hash = digest(derivation)
    result = []
    for obligation in compiled['review_obligations']:
        uid = obligation['use_id']; fields = sorted(usage[uid])
        if not fields:
            raise ValueError('source obligation has no actual owning field: ' + uid)
        binding = {'source_obligation': obligation, 'field_evidence_ids': fields,
                   'content_sha256': content_hash, 'derivation_sha256': derivation_hash}
        identity = 'source-decision:' + digest(binding)
        result.append({**copy.deepcopy(obligation), 'id': identity, 'evidence_id': identity,
                       'field_evidence_ids': fields, 'actual_binding': binding})
    return result


def attach_derivation(candidate, input_decisions, graph_bytes, *, production=None, source_compilation=None):
    """Return a fresh canonical with reproducible provenance, not approval.

    Call only after actual rendering. Optional results must be from that render;
    inspect_derivation recompiles rather than trusting these cached results.
    """
    from production_decisions import lower_production_decisions
    input_decisions = copy.deepcopy(input_decisions)
    if FIELD in input_decisions.get('project', {}).get('prevention_evidence', {}):
        raise ValueError('old derivation cannot be supplied as a new author input')
    compilation = source_compilation or _source_compile(input_decisions, graph_bytes)
    lowered = production or lower_production_decisions(input_decisions, source_compilation=compilation)
    compiled = compilation.to_dict()
    result = finalize_generated_candidate(candidate)
    payload = {'contract': CONTRACT, 'input': input_decisions,
               'input_sha256': digest(input_decisions), 'source_graph_json': graph_bytes.decode('utf-8'),
               'source_graph_sha256': hashlib.sha256(graph_bytes).hexdigest(),
               'compiler_fingerprints': compiler_fingerprints(),
               'action_trace': lowered['derivation'], 'source_compilation': compiled,
               'content_sha256': digest(owned_content(result)),
               'source_obligations': _obligations(result, compiled, lowered['derivation']),
               'independent_audit_status': 'not_performed', 'semantic_accuracy_percent': None,
               'commit_eligible': False}
    payload['binding_sha256'] = digest(payload)
    result['prevention_evidence'][FIELD] = payload
    result['prevention_evidence'].setdefault('authoring_evidence', {})['production_binding_required'] = CONTRACT
    # The marker is part of the generated identity, including on recompile.
    payload['content_sha256'] = digest(owned_content(result))
    payload['source_obligations'] = _obligations(result, compiled, lowered['derivation'])
    payload['binding_sha256'] = digest({k: v for k, v in payload.items() if k != 'binding_sha256'})
    return result


@lru_cache(maxsize=2)
def _replay_compilation(input_json, graph_text, compiler_key):
    """Bounded memo of pure recompilation, never of authority or audit status.

    The key contains exact input/source text and freshly read runtime hashes.
    Callers still verify every candidate binding and generated field. Cached
    internals are read-only; callers receive a separate copy below.
    """
    from production_decisions import lower_production_decisions
    from author_storyboard import _render_decisions
    raw = json.loads(input_json)
    compilation = _source_compile(raw, graph_text.encode('utf-8'))
    lowered = lower_production_decisions(raw, source_compilation=compilation)
    rebuilt = finalize_generated_candidate(_render_decisions(lowered['typed_decisions']))
    rebuilt['prevention_evidence'].setdefault('authoring_evidence', {})['production_binding_required'] = CONTRACT
    compiled = compilation.to_dict()
    return {'content_sha256': digest(owned_content(rebuilt)),
            'source_compilation': compiled, 'action_trace': lowered['derivation'],
            'source_obligations': _obligations(rebuilt, compiled, lowered['derivation']),
            'production_prevention_verified': lowered['derivation'].get('production_prevention', {}).get('status') == 'pass'}


def inspect_derivation(candidate):
    evidence = candidate.get('prevention_evidence', {}) if isinstance(candidate, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    payload = evidence.get(FIELD)
    authored = evidence.get('authoring_evidence')
    authored = authored if isinstance(authored, dict) else {}
    marker = authored.get('production_binding_required')
    # New source declarations cannot be downgraded by deleting both markers.
    new_source = evidence.get('source_decisions') is not None
    production_identity = authored.get('production_contract') is not None
    response = {'present': payload is not None or marker is not None or new_source or production_identity, 'valid': False,
                'faults': [], 'source_obligations': [], 'source_graph_sha256': None, 'derivation_sha256': None}
    if not response['present']:
        return response
    def fault(path, message):
        response['faults'].append({'path': 'prevention_evidence.' + FIELD + '.' + path, 'message': message})
    if not isinstance(payload, dict) or payload.get('contract') != CONTRACT:
        fault('$', 'required production derivation missing or unsupported')
        return response
    try:
        if marker != CONTRACT: fault('marker', 'required production binding marker missing')
        if digest({k: v for k, v in payload.items() if k != 'binding_sha256'}) != payload.get('binding_sha256'):
            fault('binding_sha256', 'recorded derivation changed; recompile from decisions')
        current_compiler = compiler_fingerprints()
        if payload['compiler_fingerprints'] != current_compiler:
            fault('compiler_fingerprints', 'compiler or rules changed; old generation evidence is stale')
        if payload.get('independent_audit_status') != 'not_performed' or payload.get('commit_eligible') is not False or payload.get('semantic_accuracy_percent') is not None:
            fault('independent_audit_status', 'generation binding cannot grant independent approval')
        raw = payload['input']; graph = payload['source_graph_json'].encode('utf-8')
        if digest(raw) != payload['input_sha256']: fault('input_sha256', 'input decisions changed')
        if hashlib.sha256(graph).hexdigest() != payload['source_graph_sha256']: fault('source_graph_sha256', 'source graph changed')
        if digest(owned_content(candidate)) != payload['content_sha256']:
            fault('content_sha256', 'actual generated content changed; old state/source/output evidence invalid')
        if response['faults']: return response
        replay = copy.deepcopy(_replay_compilation(
            json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(',', ':')),
            graph.decode('utf-8'), tuple(sorted(current_compiler.items()))))
        if replay['content_sha256'] != digest(owned_content(candidate)):
            fault('content_sha256', 'actual content differs from recomputed action/source decisions')
        if replay['source_compilation'] != payload['source_compilation']: fault('source_compilation', 'source compilation differs from actual frozen graph')
        if replay['action_trace'] != payload['action_trace']: fault('action_trace', 'world/action derivation differs from actual input')
        expected = replay['source_obligations']
        if expected != payload['source_obligations']: fault('source_obligations', 'source obligations do not bind actual consumed fields')
        if not response['faults']:
            response.update(valid=True, source_obligations=expected, source_graph_sha256=payload['source_graph_sha256'],
                            derivation_sha256=payload['binding_sha256'],
                            production_prevention_verified=replay['production_prevention_verified'])
    except Exception as exc:
        fault('recompile', f'{type(exc).__name__}: {exc}')
    return response
