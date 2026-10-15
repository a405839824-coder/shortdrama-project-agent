"""Bound server references keep large compiled candidates off the MCP wire."""
import copy
import json
from .canonical import canonical_sha256

CONTRACT='compiled-candidate-reference-v1'
INLINE_LIMIT=256*1024


def pack(service,namespace,project_id,work_order_id,preflight_id,candidate):
    if len(json.dumps(candidate,ensure_ascii=False).encode())<=INLINE_LIMIT:
        return copy.deepcopy(candidate)
    sha=canonical_sha256(candidate)
    value={'work_order_id':work_order_id,'draft_preflight_id':preflight_id,
           'canonical_sha256':sha,'candidate':candidate}
    try:
        current=service.store.get_project_record(namespace,project_id,kind='preflight_candidates',record_id=preflight_id)
    except ValueError as exc:
        if not service._is_unknown_project_record_error(exc,kind='preflight_candidates',record_id=preflight_id):raise
        service.store.save_project_record(namespace,project_id,kind='preflight_candidates',record_id=preflight_id,value=value)
    else:
        if (not service.store.project_record_is_signed(current)
                or any(current.get(k)!=v for k,v in value.items())):
            raise ValueError('preflight_candidate_reference_conflict')
    return {'contract':CONTRACT,'draft_preflight_id':preflight_id,'canonical_sha256':sha}


def unpack(service,namespace,project_id,work_order_id,value):
    if not isinstance(value,dict) or value.get('contract')!=CONTRACT:return value
    if set(value)!={'contract','draft_preflight_id','canonical_sha256'}:
        raise ValueError('preflight_candidate_reference_shape')
    if not all(isinstance(value[k],str) and value[k] for k in ('draft_preflight_id','canonical_sha256')):
        raise ValueError('preflight_candidate_reference_identity')
    record=service.store.get_project_record(namespace,project_id,kind='preflight_candidates',record_id=value['draft_preflight_id'])
    preflight=service.store.get_project_record(namespace,project_id,kind='draft_preflights',record_id=value['draft_preflight_id'])
    candidate=record.get('candidate')
    if (not service.store.project_record_is_signed(record)
            or not service.store.project_record_is_signed(preflight)
            or record.get('work_order_id')!=work_order_id
            or preflight.get('work_order_id')!=work_order_id
            or record.get('draft_preflight_id')!=value['draft_preflight_id']
            or record.get('canonical_sha256')!=value['canonical_sha256']
            or preflight.get('candidate_sha256')!=value['canonical_sha256']
            or not isinstance(candidate,dict)
            or canonical_sha256(candidate)!=value['canonical_sha256']):
        raise ValueError('preflight_candidate_reference_binding')
    return copy.deepcopy(candidate)
