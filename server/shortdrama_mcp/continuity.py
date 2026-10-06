"""Load the same state comparison used by standalone runtime validation."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "skills/shortdrama-storyboard/scripts/continuity_checks.py"
_spec = spec_from_file_location("shortdrama_shared_continuity", _path)
assert _spec is not None and _spec.loader is not None
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
state_equal = _module.state_equal
speech_state_findings = _module.speech_state_findings
