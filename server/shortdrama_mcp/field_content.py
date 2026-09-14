"""Use the same ownership implementation as the standalone validator."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "skills/shortdrama-storyboard/scripts/field_content.py"
_spec = spec_from_file_location("shortdrama_shared_field_content", _path)
assert _spec is not None and _spec.loader is not None
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)

CONTRACT = _module.CONTRACT
ERROR_CODE = _module.ERROR_CODE
FieldContentError = _module.FieldContentError
field_policy = _module.field_policy
field_content_findings = _module.field_content_findings
voice_content_findings = _module.voice_content_findings
assert_field_content = _module.assert_field_content
r5_keyframe_sections = _module.r5_keyframe_sections
appearance_anchor_text = _module.appearance_anchor_text
assert_static_text_preserved = _module.assert_static_text_preserved
model_field_contract = _module.model_field_contract
voice_parts = _module.voice_parts
