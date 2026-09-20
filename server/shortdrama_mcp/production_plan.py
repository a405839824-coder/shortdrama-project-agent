"""Shared production contract; no server-specific copy of its rules."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / 'skills/shortdrama-storyboard/scripts/production_plan.py'
_spec = spec_from_file_location('shortdrama_shared_production_plan', _path)
assert _spec is not None and _spec.loader is not None
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
model_production_contract = _module.model_production_contract
shot_rhythm_statistics = _module.shot_rhythm_statistics
shot_policy = _module.shot_policy
