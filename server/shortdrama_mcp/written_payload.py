"""Load the same typed paper parser used by standalone projection."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "skills/shortdrama-storyboard/scripts/written_payload.py"
_spec = spec_from_file_location("shortdrama_shared_written_payload", _path)
assert _spec is not None and _spec.loader is not None
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
discover_current_written_payloads = _module.discover_current_written_payloads
context_instruction_text = _module.context_instruction_text
