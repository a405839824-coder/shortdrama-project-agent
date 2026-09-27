"""Read-only v0.5.55 projection from unmodified Git blobs.

The manifest records every source path, commit, blob and SHA-256.  The private
namespace prevents current field/voice/registry rules from reinterpreting an
old signed workbook.  This adapter is never a new-production entry point.
"""
from __future__ import annotations

import builtins
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import ModuleType


SNAPSHOT_ROOT = Path(__file__).with_name("frozen_v0555")
SOURCE_COMMIT = "c315c60af23883a6807f5df71d9e868e452d8800"
_PACKAGE = __package__ + "._projection_snapshot_0555"
_package = ModuleType(_PACKAGE)
_package.__path__ = [str(SNAPSHOT_ROOT / "scripts")]
sys.modules[_PACKAGE] = _package


def _load(name):
    spec = spec_from_file_location(_PACKAGE + "." + name,
                                  SNAPSHOT_ROOT / "scripts" / (name + ".py"))
    module = module_from_spec(spec)
    # The original standalone field module has one absolute sibling import.
    # Resolve it inside this snapshot without changing process-wide sys.path
    # or replacing the live visible_entities module.
    def frozen_import(import_name, globals=None, locals=None, fromlist=(), level=0):
        if not level and import_name == "visible_entities":
            return sys.modules[_PACKAGE + ".visible_entities"]
        return builtins.__import__(import_name, globals, locals, fromlist, level)
    module.__dict__["__builtins__"] = {**vars(builtins), "__import__": frozen_import}
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


for _name in ("visible_entities", "written_payload", "picture_presentation", "field_content"):
    _load(_name)
_materializer = _load("materializer")
_workbook = _load("r5_workbook")


def formal_mother_template_rows(storyboard, *, _legacy_layout=False):
    return _materializer.formal_mother_template_rows(storyboard, _legacy_layout=_legacy_layout)


def reading_matrices(storyboard, formal_rows, *, _legacy_layout=False):
    return _workbook.reading_matrices(storyboard, formal_rows, _legacy_layout=_legacy_layout)
