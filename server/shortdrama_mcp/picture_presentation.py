"""Use the same untimed picture projection as the standalone validator."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "skills/shortdrama-storyboard/scripts/picture_presentation.py"
_spec = spec_from_file_location("shortdrama_shared_picture_presentation", _path)
assert _spec is not None and _spec.loader is not None
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)

display_picture = _module.display_picture
timed_picture = _module.timed_picture
separate_shot_clock = _module.separate_shot_clock
