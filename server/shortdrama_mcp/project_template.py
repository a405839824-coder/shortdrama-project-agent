"""Explicit project export choice; never relax source or audit requirements."""
from __future__ import annotations

import hashlib
import json

DEFAULT = "standard-v1"
MASTER_LIGHTING = "master-lighting-v1"
PREFIX = "project-template-v1:"


def validate_variant(value):
    if value not in (DEFAULT, MASTER_LIGHTING):
        raise ValueError("project_template_variant_unsupported")
    return value


def parse_request(value, *, operation, mode, authorization, action):
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return None
    if (operation != "rule_improvement" or mode != "execute"
            or authorization != "explicit_execute"
            or action not in {"create", "create_window_neutral"}):
        raise ValueError("project_template_admin_rule_authority_required")
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("project_template_duplicate_key")
            result[key] = item
        return result
    if len(value) > 512:
        raise ValueError("project_template_request_too_large")
    obj = json.loads(value[len(PREFIX):], object_pairs_hook=unique)
    if (not isinstance(obj, dict) or set(obj) != {"contract", "variant"}
            or obj["contract"] != "project-template-v1"):
        raise ValueError("project_template_request_invalid")
    return validate_variant(obj["variant"])


def selected_variant(hub):
    setting = hub.get("project_template")
    if setting is None:
        return DEFAULT
    if not isinstance(setting, dict) or set(setting) != {"variant", "authorization_work_order_id"}:
        raise ValueError("project_template_authorization_invalid")
    work = next((w for w in hub.get("work_orders", [])
                 if w.get("work_order_id") == setting["authorization_work_order_id"]), {})
    if (work.get("operation") != "rule_improvement" or work.get("mode") != "execute"
            or work.get("authorization") != "explicit_execute"
            or work.get("source_lock", {}).get("project_template_variant") != setting["variant"]):
        raise ValueError("project_template_authorization_invalid")
    return validate_variant(setting["variant"])


def canonical_variant(storyboard):
    extension = storyboard.get("canonical_extension", {})
    if not isinstance(extension, dict):
        raise ValueError("project_template_canonical_extension_invalid")
    return validate_variant(extension.get("project_template_variant", DEFAULT))


def assert_project_template(storyboard, hub):
    variant = canonical_variant(storyboard)
    if variant != selected_variant(hub):
        raise ValueError("project_template_scope_mismatch: recompile with the authorized project variant")
    return variant


def template_digest(base, variant):
    validate_variant(variant)
    if variant == DEFAULT:
        return base
    raw = json.dumps({"base": base, "variant": variant}, sort_keys=True,
                     separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()
