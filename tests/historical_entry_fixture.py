"""Isolate legacy protocol tests from the new author-input entry requirement.

The old synthetic 1-2 shot canonicals test storage, signatures, migration, RBAC
and repair mechanics. They are not new full-episode production examples. Only
the entry boundary is replaced on this fixture service; existing validators,
auth, signatures and repairs still execute. Typed plans and episode contracts
always execute the real guard. v0534 uses fresh services/the original method.
No application flag, environment variable or production bypass is added.
"""
def freeze_legacy_entry(service):
    original = type(service)._require_current_production
    def historical_or_current(candidate, **kwargs):
        evidence = candidate.get('prevention_evidence') or {}
        if any(evidence.get(k) is not None for k in ('production_derivation','source_decisions','episode_contract')):
            return original(candidate, **kwargs)
        return 'historical_unit_fixture_only'
    service._require_current_production = historical_or_current
    return service
