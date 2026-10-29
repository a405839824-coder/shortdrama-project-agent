"""Shared current-production entry guard, including direct canonical requests.

Historical audit and signed workbook migration are explicit separate workflows.
The caller cannot obtain a new-production verdict by omitting a request wrapper.
"""
from __future__ import annotations
import hashlib
import json


def check_borrow_permissions(candidate, permissions):
    evidence=candidate.get('prevention_evidence') or {}
    if not isinstance(evidence,dict):
        raise ValueError('borrow_permission_invalid: prevention_evidence must be an object')
    if not isinstance(evidence.get('borrow_ledger',[]),list):
        raise ValueError('borrow_permission_invalid: borrow_ledger must be a list')
    sources={r.get('source_id'):r for r in evidence.get('source_ledger',[]) if isinstance(r,dict)}
    grants={r.get('authorization_id'):r for r in permissions if isinstance(r,dict)}
    for row in evidence.get('borrow_ledger',[]):
        if not isinstance(row,dict):
            raise ValueError('borrow_permission_invalid: every borrow record must be an object')
        source=sources.get(row.get('source_id'),{})
        grant=grants.get(row.get('authorization_id'))
        fields=('source_id','origin_episode','target_episode','next_source_id','authorization_basis')
        if (not grant or any(grant.get(k)!=row.get(k) for k in fields)
            or grant.get('source_sha256')!=hashlib.sha256(str(source.get('source_text') or '').encode()).hexdigest()):
            raise ValueError('borrow_permission_required: exact source, origin, target and next boundary need a server-recorded explicit task permission; candidate text is not authority')


def require_current_production(candidate, *, work_order, scope_kind, workbook_migration=None, borrow_permissions=(), source_kind=None, load_artifact=None):
    if workbook_migration is not None:
        return 'signed_historical_workbook_migration'
    if work_order.get('operation') != 'storyboard_audit':
        check_borrow_permissions(candidate, borrow_permissions)
    if work_order.get('operation') not in {'storyboard_create','storyboard_regenerate','storyboard_change','production_batch'}:
        return 'existing_artifact_audit_or_repair'
    # Also applies to locally compiled canonicals. The request wrapper cannot
    # be the only place that consults the actual imported source role.
    if source_kind == 'novel':
        contract=(candidate.get('prevention_evidence') or {}).get('episode_contract')
        intake=candidate.get('source_ingestion')
        if (not isinstance(contract,dict) or contract.get('source_kind')!='novel'
            or not isinstance(intake,dict) or intake.get('mode')!='chapter_to_storyboard'):
            raise ValueError('chapter_source_scope_required: imported novel source requires a bound novel contract and chapter intake on every current production route')
    from prompt_libraries import assert_prompt_library
    assert_prompt_library(candidate, required=True)
    from production_derivation import inspect_derivation
    from episode_rules import CONTRACT
    binding=inspect_derivation(candidate)
    if binding.get('production_prevention_verified') is not True:
        raise ValueError('production_plan_required: current production requires a successfully replayed frozen plan; direct canonical is not an alternate production route')
    from scene_narrative import verify_prior_deliveries
    graph = json.loads(candidate['prevention_evidence']['production_derivation']['source_graph_json'])
    verify_prior_deliveries(graph['production_plan']['narrative'], load_artifact)
    if scope_kind in {'full','episode'}:
        contract=(candidate.get('prevention_evidence') or {}).get('episode_contract')
        if not isinstance(contract,dict) or contract.get('contract')!=CONTRACT or contract.get('scope') not in {'complete_episodes','complete_series'}:
            raise ValueError('episode_contract_required: full/episode production requires complete episode evidence; use a scene preflight for a fragment, which does not certify the episode')
    return 'replayed_current_production'
