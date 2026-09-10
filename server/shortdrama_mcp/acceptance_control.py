"""Host-owned finite acceptance accounting; this module is not an OS sandbox.

The host freezes a scope, reserves each execution before running it, and reports
observations afterwards. MCP callers must not choose the registry or obtain the
host APIs. A controlled service injects a single registry and operation lease.
Two rounds, one repair and one reservation per named step per round are fixed.
Failures/crashes never refund a reservation or an operation. A partial journal
fails closed. Same-host administrators able to replace files or select another
registry remain outside this boundary. Hash identity is not semantic sign-off.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

CONTRACT_VERSION = "finite-acceptance-v1"
TERMINAL_STATES = frozenset({"passed", "stopped"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "contract_version", "contract_id", "scope_id", "lineage_id", "namespace",
    "project_id", "frozen_files", "candidate_files", "repair_allowlist",
    "tracked_roots", "exclude_globs", "steps",
}
_RESULT_KEYS = {"classification", "stage", "outcome", "reason_codes",
                "reached_states", "evidence", "summary"}
_CLASSIFICATIONS = {"completed", "implementation_defect", "input_error",
                    "blocked_dependency", "unknown", "architecture_change", "interrupted"}


class AcceptanceControlError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": copy.deepcopy(self.details)}


def _fail(code: str, message: str, **details: Any) -> None:
    raise AcceptanceControlError(code, message, details)


def _strict(value: Any, depth: int = 0) -> None:
    if depth > 40:
        _fail("invalid_json", "JSON nesting is too deep")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is list:
        for item in value:
            _strict(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _strict(item, depth + 1)
        return
    _fail("invalid_json", "Only plain JSON values and integer numbers are supported")


def _bytes(value: Any) -> bytes:
    _strict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _obj(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail("invalid_shape", "Object keys differ from the contract", path=where, expected=sorted(keys))
    return value


def _id(value: Any, where: str) -> str:
    if type(value) is not str or not _ID.fullmatch(value):
        _fail("invalid_identifier", "Expected a safe nonempty identifier", path=where)
    return value


def _text(value: Any, where: str) -> str:
    if type(value) is not str or not value.strip() or len(value) > 20000 or "\0" in value:
        _fail("invalid_text", "Expected bounded nonempty text", path=where)
    return value


def _sha(value: Any, where: str) -> str:
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("invalid_hash", "Expected lowercase SHA-256", path=where)
    return value


def _enum(value: Any, values: set[str], where: str) -> str:
    if type(value) is not str or value not in values:
        _fail("invalid_enum", "Unsupported value", path=where, allowed=sorted(values))
    return value


def _rel(value: Any, where: str, *, dot: bool = False) -> str:
    if dot and value == ".":
        return "."
    if type(value) is not str or not value or "\\" in value or ":" in value:
        _fail("invalid_path", "Expected a canonical workspace-relative POSIX path", path=where)
    for part in value.split("/"):
        if part in ("", ".", "..") or part.endswith((" ", ".")) or re.search(r'[<>"|?*\x00-\x1f]', part):
            _fail("invalid_path", "Ambiguous or reserved path component", path=where)
        base = part.split(".", 1)[0].upper()
        if base in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(COM|LPT)[1-9]", base):
            _fail("invalid_path", "Device paths are forbidden", path=where)
    return value


def _list(value: Any, where: str, validator: Any, *, required: bool = False) -> list[str]:
    if type(value) is not list or (required and not value):
        _fail("invalid_shape", "Expected a list" + (" with entries" if required else ""), path=where)
    out = [validator(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if len({item.casefold() for item in out}) != len(out):
        _fail("duplicate_value", "Duplicate or case-ambiguous entries", path=where)
    return out


def _hashes(value: Any, where: str, *, required: bool = False) -> dict[str, str]:
    if type(value) is not dict or (required and not value):
        _fail("invalid_shape", "Expected path-to-hash object", path=where)
    _list(list(value), where, _rel)
    for path, digest in value.items():
        _sha(digest, where + "." + path)
    return value


def _reparse(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _root(value: str | Path, *, create: bool = False) -> Path:
    if not isinstance(value, (str, Path)):
        _fail("invalid_path", "Root must be a filesystem path")
    target = Path(os.path.abspath(os.fspath(value)))
    for part in reversed([target, *target.parents]):
        if (part.exists() or part.is_symlink()) and _reparse(part):
            _fail("unsafe_path", "Symlinks and reparse points are forbidden", path=str(part))
    if create:
        target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        _fail("invalid_path", "Root must be an existing directory", path=str(target))
    return target.resolve(strict=True)


def _resolve(root: Path, relative: str, *, missing: bool = False) -> Path:
    _rel(relative, "relative_path", dot=True)
    target = root if relative == "." else root.joinpath(*relative.split("/"))
    for part in [target, *target.parents]:
        if part != root and root not in part.parents:
            continue
        if (part.exists() or part.is_symlink()) and _reparse(part):
            _fail("unsafe_path", "Symlinks and reparse points are forbidden", path=relative)
    resolved = target.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        _fail("path_escape", "Path escapes the frozen workspace", path=relative)
    if not missing and not target.exists():
        _fail("missing_file", "Frozen path is missing", path=relative)
    return target


def _file_bytes(root: Path, relative: str) -> bytes:
    path = _resolve(root, relative)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        _fail("invalid_file", "Expected a regular file", path=relative)
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        _fail("unstable_file", "File changed during reading", path=relative)
    return raw


def _file_hash(root: Path, relative: str) -> str:
    return hashlib.sha256(_file_bytes(root, relative)).hexdigest()


def _under(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(directory + "/")


def _excluded(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) or
               (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:])) for pattern in patterns)


def _inventory(root: Path, manifest: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    folded: dict[str, str] = {}
    for name in manifest["tracked_roots"]:
        directory = _resolve(root, name)
        if not directory.is_dir():
            _fail("invalid_tracked_root", "Tracked root is not a directory", path=name)
        for current, directories, files in os.walk(directory, followlinks=False):
            keep = []
            for child in sorted(directories):
                path = Path(current) / child
                rel = path.relative_to(root).as_posix()
                if _excluded(rel + "/", manifest["exclude_globs"]):
                    continue
                if _reparse(path):
                    _fail("unsafe_path", "Tracked directory is a reparse point", path=rel)
                _rel(rel, "tracked_path")
                keep.append(child)
            directories[:] = keep
            for child in sorted(files):
                rel = (Path(current) / child).relative_to(root).as_posix()
                if _excluded(rel, manifest["exclude_globs"]):
                    continue
                _rel(rel, "tracked_path")
                if rel.casefold() in folded and folded[rel.casefold()] != rel:
                    _fail("duplicate_path", "Tracked files differ only by case", path=rel)
                folded[rel.casefold()] = rel
                found[rel] = _file_hash(root, rel)
    return dict(sorted(found.items()))


def _diff(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    added, deleted = sorted(set(after) - set(before)), sorted(set(before) - set(after))
    modified = sorted(path for path in set(before) & set(after) if before[path] != after[path])
    return {"added": added, "deleted": deleted, "modified": modified,
            "changed_paths": sorted(added + deleted + modified)}


def _manifest(value: Any) -> dict[str, Any]:
    _strict(value)
    obj = copy.deepcopy(_obj(value, _MANIFEST_KEYS, "manifest"))
    _enum(obj["contract_version"], {CONTRACT_VERSION}, "contract_version")
    for key in ("contract_id", "scope_id", "lineage_id", "namespace", "project_id"):
        _id(obj[key], key)
    _hashes(obj["frozen_files"], "frozen_files")
    _hashes(obj["candidate_files"], "candidate_files", required=True)
    known = list(obj["frozen_files"]) + list(obj["candidate_files"])
    _list(known, "declared_files", _rel)
    _list(obj["repair_allowlist"], "repair_allowlist", _rel)
    roots = _list(obj["tracked_roots"], "tracked_roots", lambda v, w: _rel(v, w, dot=True), required=True)
    for index, path in enumerate(roots):
        if any(_under(path, old) or _under(old, path) for old in roots[:index]):
            _fail("overlapping_roots", "Tracked roots must not overlap", path=path)
    for pattern in _list(obj["exclude_globs"], "exclude_globs", _text):
        if pattern.startswith("/") or "\\" in pattern or ":" in pattern or ".." in pattern.split("/"):
            _fail("invalid_exclusion", "Exclusions must be relative glob patterns")
    for path in list(obj["candidate_files"]) + obj["repair_allowlist"]:
        if not any(_under(path, directory) for directory in roots) or _excluded(path, obj["exclude_globs"]):
            _fail("untracked_candidate", "Candidates and repair paths must be tracked and nonexcluded", path=path)
    if {p.casefold() for p in obj["repair_allowlist"]} & {p.casefold() for p in obj["frozen_files"]}:
        _fail("frozen_repair_path", "Frozen files cannot be repaired")
    if type(obj["steps"]) is not list or not obj["steps"]:
        _fail("invalid_steps", "At least one execution step must be frozen")
    prior: list[str] = []
    for index, step in enumerate(obj["steps"]):
        where = f"steps[{index}]"
        _obj(step, {"step_id", "recipe", "inputs", "depends_on", "expected", "operation_limits"}, where)
        sid = _id(step["step_id"], where + ".step_id")
        if sid.casefold() in {name.casefold() for name in prior}:
            _fail("duplicate_step", "Step identities cannot be reused", step_id=sid)
        recipe = _obj(step["recipe"], {"kind", "argv", "timeout_seconds", "expected_exit_code"}, where + ".recipe")
        _enum(recipe["kind"], {"python"}, where + ".recipe.kind")
        # argv is an argument vector; this controller never executes it.
        if type(recipe["argv"]) is not list or not recipe["argv"] or any(
                type(arg) is not str or "\0" in arg for arg in recipe["argv"]):
            _fail("invalid_recipe", "Recipe argv must be a nonempty string vector", step_id=sid)
        if type(recipe["timeout_seconds"]) is not int or not 1 <= recipe["timeout_seconds"] <= 86400:
            _fail("invalid_recipe", "Recipe timeout must be a positive integer", step_id=sid)
        if type(recipe["expected_exit_code"]) is not int:
            _fail("invalid_recipe", "Recipe exit code must be an integer", step_id=sid)
        inputs = _list(step["inputs"], where + ".inputs", _rel, required=True)
        if not set(inputs).issubset(known):
            _fail("unfrozen_input", "Step inputs must be frozen or candidate files", step_id=sid)
        dependencies = _list(step["depends_on"], where + ".depends_on", _id)
        if not set(dependencies).issubset(prior):
            _fail("invalid_dependency", "Dependencies must name earlier steps exactly", step_id=sid)
        expected = _obj(step["expected"], {"stage", "outcome", "reason_codes", "prohibited_states"}, where + ".expected")
        _id(expected["stage"], where + ".expected.stage")
        _enum(expected["outcome"], {"accepted", "rejected"}, where + ".expected.outcome")
        negative = expected["outcome"] == "rejected"
        _list(expected["reason_codes"], where + ".expected.reason_codes", _id, required=negative)
        _list(expected["prohibited_states"], where + ".expected.prohibited_states", _id, required=negative)
        if type(step["operation_limits"]) is not list:
            _fail("invalid_operations", "Operation limits must be a list")
        identities = set()
        for item in step["operation_limits"]:
            _obj(item, {"operation", "namespace", "project_id", "request_sha256", "limit"}, where + ".operation_limits")
            for key in ("operation", "namespace", "project_id"):
                _id(item[key], where + "." + key)
            _sha(item["request_sha256"], where + ".request_sha256")
            if item["namespace"] != obj["namespace"] or item["project_id"] != obj["project_id"]:
                _fail("operation_scope_mismatch", "Operation scope differs from contract")
            if type(item["limit"]) is not int or not 1 <= item["limit"] <= 100000:
                _fail("invalid_limit", "Operation limit must be a positive bounded integer")
            identity = tuple(item[key] for key in ("operation", "namespace", "project_id", "request_sha256"))
            if identity in identities:
                _fail("duplicate_operation", "Operation binding is duplicated")
            identities.add(identity)
        prior.append(sid)
    return obj


def _result(value: Any) -> dict[str, Any]:
    _strict(value)
    obj = copy.deepcopy(_obj(value, _RESULT_KEYS, "result"))
    _enum(obj["classification"], _CLASSIFICATIONS, "result.classification")
    _enum(obj["outcome"], {"accepted", "rejected", "error", "not_run"}, "result.outcome")
    _id(obj["stage"], "result.stage")
    _list(obj["reason_codes"], "result.reason_codes", _id)
    _list(obj["reached_states"], "result.reached_states", _id)
    _hashes(obj["evidence"], "result.evidence", required=True)
    _text(obj["summary"], "result.summary")
    return obj


class AcceptanceController:
    """Persistent host API with no executor and no caller-controlled reset.

    The registry journal is authoritative. Each event contains a complete state
    snapshot and the previous event hash. Exclusive creation plus fsync preserves
    starts before external work. Corrupt/incomplete journals refuse all actions.
    Candidate blobs preserve original bytes, including repaired input files.
    """

    def __init__(self, registry_dir: str | Path):
        self.registry_dir = _root(registry_dir, create=True)
        self._journal = self.registry_dir / "journal"
        self._blobs = self.registry_dir / "blobs"
        for path in (self._journal, self._blobs):
            if (path.exists() or path.is_symlink()) and _reparse(path):
                _fail("unsafe_registry", "Registry paths cannot be reparse points")
            path.mkdir(exist_ok=True)
        lock_path = self.registry_dir / ".acceptance.lock"
        if (lock_path.exists() or lock_path.is_symlink()) and _reparse(lock_path):
            _fail("unsafe_registry", "Registry lock cannot be a reparse point")
        self._lock = FileLock(str(lock_path), timeout=30)

    @staticmethod
    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def _load(self) -> tuple[dict[str, Any], int, str]:
        for path in (self.registry_dir, self._journal, self._blobs):
            if _reparse(path):
                _fail("unsafe_registry", "Registry paths changed after initialization")
        state: dict[str, Any] = {"contracts": {}, "scope_index": {}, "lineage_index": {}, "identity_index": {}}
        previous = "0" * 64
        files = sorted(self._journal.iterdir())
        for sequence, path in enumerate(files, 1):
            if path.name != f"{sequence:012d}.json" or _reparse(path) or not path.is_file():
                _fail("registry_corrupt", "Journal contains an unknown or noncontiguous entry", path=str(path))
            try:
                raw = path.read_bytes()
                event = json.loads(raw, object_pairs_hook=self._pairs)
                _obj(event, {"sequence", "previous_sha256", "timestamp", "kind", "details", "state"}, "journal")
                if raw != _bytes(event) or type(event["sequence"]) is not int or event["sequence"] != sequence or event["previous_sha256"] != previous:
                    raise ValueError("Journal chain mismatch")
                _obj(event["state"], {"contracts", "scope_index", "lineage_index", "identity_index"}, "journal.state")
                if any(type(value) is not dict for value in event["state"].values()):
                    raise ValueError("Malformed journal state")
            except (ValueError, TypeError, UnicodeError) as exc:
                _fail("registry_corrupt", "Incomplete journal blocks execution; consumed work is not refunded", path=str(path), cause=str(exc))
            previous = hashlib.sha256(raw).hexdigest()
            state = event["state"]
        return state, len(files), previous

    def _append(self, state: dict[str, Any], kind: str, details: dict[str, Any]) -> None:
        _, sequence, previous = self._load()
        event = {"sequence": sequence + 1, "previous_sha256": previous, "timestamp": _now(),
                 "kind": kind, "details": details, "state": state}
        path = self._journal / f"{sequence + 1:012d}.json"
        # A crash during this exclusive write leaves a blocking partial entry,
        # not an implicit refund or permission to overwrite/reuse the sequence.
        with path.open("xb") as stream:
            stream.write(_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            descriptor = os.open(self._journal, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _archive(self, root: Path, hashes: dict[str, str]) -> dict[str, str]:
        for relative, digest in hashes.items():
            raw = _file_bytes(root, relative)
            if hashlib.sha256(raw).hexdigest() != digest:
                _fail("unstable_file", "Candidate changed before its immutable snapshot", path=relative)
            path = self._blobs / (digest + ".blob")
            if path.exists() or path.is_symlink():
                if _reparse(path) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    _fail("registry_corrupt", "An immutable candidate blob is missing or changed", digest=digest)
            else:
                with path.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
        return {path: "blobs/" + digest + ".blob" for path, digest in hashes.items()}

    def _contract(self, state: dict[str, Any], contract_id: str) -> dict[str, Any]:
        _id(contract_id, "contract_id")
        record = state["contracts"].get(contract_id)
        if record is None:
            _fail("unknown_contract", "No contract has this identity", contract_id=contract_id)
        return record

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(record)
        for reservation in result["reservations"].values():
            reservation.pop("lease_id", None)
        return result

    @staticmethod
    def _active(record: dict[str, Any]) -> None:
        if record["state"] in TERMINAL_STATES:
            _fail("terminal_contract", "Terminal contracts cannot restart or repair", state=record["state"])

    def _terminal(self, state: dict[str, Any], record: dict[str, Any], code: str, message: str, **details: Any) -> None:
        record["state"] = "stopped"
        record["stop_reason"] = {"category": code, "reason": message, "details": details, "at": _now()}
        if record["rounds"] and record["rounds"][-1]["state"] == "running":
            record["rounds"][-1]["state"] = "interrupted"
        self._append(state, "contract_stopped", {"contract_id": record["contract_id"], **record["stop_reason"]})

    def _refuse(self, state: dict[str, Any], record: dict[str, Any], code: str, message: str, **details: Any) -> None:
        if record["state"] not in TERMINAL_STATES:
            self._terminal(state, record, code, message, **details)
        _fail(code, message, **details)

    def _live(self, record: dict[str, Any], observed_hashes: dict[str, str] | None = None) -> dict[str, str]:
        root = _root(record["workspace_root"])
        manifest = record["manifest"]
        frozen = {path: _file_hash(root, path) for path in manifest["frozen_files"]}
        if frozen != manifest["frozen_files"]:
            _fail("frozen_drift", "Frozen rules, criteria, interpreter or inputs changed",
                  diff=_diff(manifest["frozen_files"], frozen))
        inventory = _inventory(root, manifest)
        if inventory != record["inventory"]:
            _fail("candidate_drift", "Tracked files were added, deleted or changed outside authorized repair",
                  diff=_diff(record["inventory"], inventory))
        actual = {path: _file_hash(root, path) for path in record["candidate_hashes"]}
        if actual != record["candidate_hashes"]:
            _fail("candidate_drift", "Candidate bytes differ from their round snapshot")
        if observed_hashes is not None and observed_hashes != actual:
            _fail("observed_hash_mismatch", "Caller hashes differ from the host-read candidate", actual=actual)
        return actual

    def _guard(self, state: dict[str, Any], record: dict[str, Any],
               observed_hashes: dict[str, str] | None = None) -> dict[str, str]:
        try:
            return self._live(record, observed_hashes)
        except (AcceptanceControlError, OSError) as exc:
            code = exc.code if isinstance(exc, AcceptanceControlError) else "filesystem_unavailable"
            details = exc.details if isinstance(exc, AcceptanceControlError) else {"cause": str(exc)}
            self._refuse(state, record, code, "Execution requires stable readable frozen files", **details)
        raise AssertionError("unreachable")

    def freeze(self, manifest: dict[str, Any], workspace_root: str | Path) -> dict[str, Any]:
        obj, root = _manifest(manifest), _root(workspace_root)
        for tracked in obj["tracked_roots"]:
            directory = _resolve(root, tracked)
            if directory == self.registry_dir or directory in self.registry_dir.parents:
                _fail("registry_in_scope", "Mutable registry must be outside tracked roots")
        with self._lock:
            state, _, _ = self._load()
            for index, key in (("scope_index", "scope_id"), ("lineage_index", "lineage_id")):
                if obj[key].casefold() in state[index]:
                    _fail("scope_already_frozen", "Changing contract identity cannot reset scope or lineage",
                          existing_contract_id=state[index][obj[key].casefold()])
            if obj["contract_id"].casefold() in {key.casefold() for key in state["contracts"]}:
                _fail("contract_already_frozen", "Contract identity cannot be reused")
            # Same project / workspace with overlapping physical tracked scope
            # also cannot be reset merely by renaming both logical scope labels.
            for old in state["contracts"].values():
                previous = old["manifest"]
                if (old["workspace_root"].casefold() == str(root).casefold() and
                        previous["namespace"].casefold() == obj["namespace"].casefold() and
                        previous["project_id"].casefold() == obj["project_id"].casefold() and
                        any(_under(left.casefold(), right.casefold()) or _under(right.casefold(), left.casefold())
                            for left in previous["tracked_roots"] for right in obj["tracked_roots"])):
                    _fail("scope_already_frozen", "Renaming scope labels cannot restart overlapping host scope",
                          existing_contract_id=old["contract_id"])
            inventory = _inventory(root, obj)
            for table in ("frozen_files", "candidate_files"):
                actual = {path: _file_hash(root, path) for path in obj[table]}
                if actual != obj[table]:
                    _fail("freeze_hash_mismatch", "Manifest hashes differ from actual host bytes",
                          table=table, diff=_diff(obj[table], actual))
            if not set(obj["candidate_files"]).issubset(inventory):
                _fail("untracked_candidate", "Every candidate must be in the actual tracked inventory")
            for path in obj["repair_allowlist"]:
                if _resolve(root, path, missing=True).exists() and path not in obj["candidate_files"]:
                    _fail("noncandidate_repair_path", "An existing repair path must be explicitly a candidate", path=path)
            blobs = self._archive(root, obj["candidate_files"])
            identity = _digest({"workspace_root": str(root).casefold(), "namespace": obj["namespace"].casefold(),
                                "project_id": obj["project_id"].casefold(), "tracked_roots": sorted(obj["tracked_roots"])})
            record = {
                "contract_id": obj["contract_id"], "scope_id": obj["scope_id"], "lineage_id": obj["lineage_id"],
                "workspace_root": str(root), "manifest": obj, "manifest_sha256": _digest(obj), "state": "frozen",
                "frozen_at": _now(), "limits": {"rounds": 2, "repairs": 1}, "inventory": inventory,
                "candidate_hashes": copy.deepcopy(obj["candidate_files"]), "candidate_blobs": blobs,
                "rounds": [], "reservations": {}, "repair_used": 0, "repairs": [], "stop_reason": None,
            }
            state["contracts"][obj["contract_id"]] = record
            state["scope_index"][obj["scope_id"].casefold()] = obj["contract_id"]
            state["lineage_index"][obj["lineage_id"].casefold()] = obj["contract_id"]
            state["identity_index"][identity] = obj["contract_id"]
            self._append(state, "contract_frozen", {"contract_id": obj["contract_id"], "manifest_sha256": record["manifest_sha256"]})
            return self._public(record)

    def status(self, contract_id: str) -> dict[str, Any]:
        """Read persisted accounting, including manifest; never expose lease secrets."""
        with self._lock:
            state, _, _ = self._load()
            return self._public(self._contract(state, contract_id))

    def begin_round(self, contract_id: str, observed_hashes: dict[str, str]) -> dict[str, Any]:
        _hashes(observed_hashes, "observed_hashes", required=True)
        with self._lock:
            state, _, _ = self._load()
            record = self._contract(state, contract_id)
            self._active(record)
            if record["state"] not in {"frozen", "repair_frozen"} or len(record["rounds"]) >= 2:
                _fail("round_not_authorized", "Round requires initial freeze or the single frozen repair", state=record["state"])
            number = len(record["rounds"]) + 1
            current = {"round_number": number, "state": "running", "started_at": _now(), "steps": {},
                       "candidate_hashes": copy.deepcopy(record["candidate_hashes"]),
                       "candidate_blobs": copy.deepcopy(record["candidate_blobs"])}
            record["rounds"].append(current)
            record["state"] = "running"
            self._append(state, "round_started", {"contract_id": contract_id, "round_number": number})
            self._guard(state, record, observed_hashes)
            return {"contract_id": contract_id, "round_number": number, "state": "running",
                    "candidate_hashes": copy.deepcopy(record["candidate_hashes"]), "started_at": current["started_at"]}

    def reserve_step(self, contract_id: str, round_number: int, step_id: str,
                     observed_hashes: dict[str, str]) -> dict[str, Any]:
        _hashes(observed_hashes, "observed_hashes", required=True)
        with self._lock:
            state, _, _ = self._load()
            record = self._contract(state, contract_id)
            self._active(record)
            if type(round_number) is not int or record["state"] != "running" or record["rounds"][-1]["round_number"] != round_number:
                _fail("round_not_active", "Reservation must name the active round")
            _id(step_id, "step_id")
            step = next((item for item in record["manifest"]["steps"] if item["step_id"] == step_id), None)
            if step is None:
                self._refuse(state, record, "unlisted_step", "Execution outside the frozen step list is forbidden", step_id=step_id)
            current = record["rounds"][-1]
            if step_id in current["steps"]:
                _fail("step_already_reserved", "Reserved work is consumed even after failure or interruption",
                      reservation_id=current["steps"][step_id])
            for dependency in step["depends_on"]:
                previous_id = current["steps"].get(dependency)
                if previous_id is None or record["reservations"][previous_id]["state"] != "completed":
                    _fail("dependency_pending", "Dependency has no completed observation", dependency=dependency)
            rid, lease = "res-" + secrets.token_hex(16), "lease-" + secrets.token_hex(32)
            declared = {**record["manifest"]["frozen_files"], **record["candidate_hashes"]}
            reservation = {
                "contract_id": contract_id, "round_number": round_number, "step_id": step_id,
                "reservation_id": rid, "lease_id": lease, "state": "reserved", "reserved_at": _now(),
                "manifest_sha256": record["manifest_sha256"], "candidate_hashes": copy.deepcopy(record["candidate_hashes"]),
                "input_hashes": {path: declared[path] for path in step["inputs"]},
                "operation_counts": [0] * len(step["operation_limits"]), "operations": [], "result": None, "matched": None,
            }
            record["reservations"][rid] = reservation
            current["steps"][step_id] = rid
            self._append(state, "step_reserved", {"contract_id": contract_id, "round_number": round_number,
                                                 "step_id": step_id, "reservation_id": rid})
            self._guard(state, record, observed_hashes)
            return copy.deepcopy(reservation)

    def _reservation(self, state: dict[str, Any], *, reservation_id: str | None = None,
                     lease_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        for record in state["contracts"].values():
            for reservation in record["reservations"].values():
                if ((reservation_id is not None and reservation["reservation_id"] == reservation_id) or
                        (lease_id is not None and secrets.compare_digest(reservation["lease_id"], lease_id))):
                    return record, reservation
        _fail("unknown_reservation" if reservation_id is not None else "unknown_lease", "No host-issued capability matches")
        raise AssertionError("unreachable")

    def consume_operation(self, lease_id: str, operation: str, namespace: str, project_id: str, *,
                          request_sha256: str) -> dict[str, Any]:
        """Atomically consume an exact service request before its side effects."""
        for key, value in (("lease_id", lease_id), ("operation", operation), ("namespace", namespace), ("project_id", project_id)):
            _id(value, key)
        _sha(request_sha256, "request_sha256")
        with self._lock:
            state, _, _ = self._load()
            record, reservation = self._reservation(state, lease_id=lease_id)
            self._active(record)
            if record["state"] != "running" or reservation["state"] != "reserved" or record["rounds"][-1]["round_number"] != reservation["round_number"]:
                _fail("lease_not_live", "Lease must belong to a pending step of the active round")
            self._guard(state, record)
            step = next(item for item in record["manifest"]["steps"] if item["step_id"] == reservation["step_id"])
            current = record["rounds"][-1]
            if any(record["reservations"][current["steps"][dependency]]["matched"] is not True
                   for dependency in step["depends_on"]):
                self._refuse(state, record, "prerequisite_failed", "A failed prerequisite permits only a not-run observation")
            binding = {"operation": operation, "namespace": namespace, "project_id": project_id, "request_sha256": request_sha256}
            index = next((index for index, item in enumerate(step["operation_limits"])
                          if all(item[key] == value for key, value in binding.items())), None)
            if index is None:
                self._refuse(state, record, "operation_not_authorized", "Actual request is not in the frozen operation plan", **binding)
            maximum = step["operation_limits"][index]["limit"]
            if reservation["operation_counts"][index] >= maximum:
                self._refuse(state, record, "operation_budget_exhausted", "Frozen operation budget is exhausted", **binding)
            reservation["operation_counts"][index] += 1
            receipt = {**binding, "operation_receipt_id": "op-" + secrets.token_hex(16),
                       "contract_id": record["contract_id"], "round_number": reservation["round_number"],
                       "step_id": reservation["step_id"], "reservation_id": reservation["reservation_id"],
                       "consumed_at": _now(), "used": reservation["operation_counts"][index], "limit": maximum}
            reservation["operations"].append(receipt)
            self._append(state, "operation_consumed", receipt)
            return copy.deepcopy(receipt)

    def complete_step(self, reservation_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """Bind host observations to the frozen reason/stage/state expectation.

        Result.classification='completed' includes a negative case correctly
        rejected by the product. It does not mean the product accepted it.
        Reasons must include the frozen targets; unrelated early errors cannot
        pass. Operation limits are ceilings, not an instruction to use every
        possible branch. The host observer must establish the actual result.
        """
        _id(reservation_id, "reservation_id")
        with self._lock:
            state, _, _ = self._load()
            record, reservation = self._reservation(state, reservation_id=reservation_id)
            self._active(record)
            if record["state"] != "running" or reservation["state"] != "reserved":
                _fail("reservation_not_pending", "An inactive or completed step cannot be completed again")
            try:
                observation = _result(result)
                root = _root(record["workspace_root"])
                evidence = {path: _file_hash(root, path) for path in observation["evidence"]}
                if evidence != observation["evidence"]:
                    _fail("evidence_hash_mismatch", "Actual evidence bytes differ from the observation", actual=evidence)
            except (AcceptanceControlError, OSError) as exc:
                self._refuse(state, record, "invalid_observation", "An unverifiable result cannot release more execution", cause=str(exc))
            self._guard(state, record)
            step = next(item for item in record["manifest"]["steps"] if item["step_id"] == reservation["step_id"])
            expected, reasons = step["expected"], []
            if observation["classification"] != "completed":
                reasons.append("observation_not_completed")
            if observation["stage"] != expected["stage"]:
                reasons.append("target_stage_not_reached")
            if observation["outcome"] != expected["outcome"]:
                reasons.append("unexpected_outcome")
            if not set(expected["reason_codes"]).issubset(observation["reason_codes"]):
                reasons.append("target_reason_missing")
            if set(expected["prohibited_states"]) & set(observation["reached_states"]):
                reasons.append("prohibited_state_reached")
            current = record["rounds"][-1]
            if any(record["reservations"][current["steps"][dependency]]["matched"] is not True
                   for dependency in step["depends_on"]):
                reasons.append("prerequisite_not_matched")
            reservation.update({"state": "completed", "result": observation, "matched": not reasons,
                                "match_failures": reasons, "completed_at": _now()})
            self._append(state, "step_completed", {"contract_id": record["contract_id"], "reservation_id": reservation_id,
                                                  "matched": not reasons, "match_failures": reasons})
            if observation["classification"] in {"unknown", "architecture_change", "interrupted"}:
                self._terminal(state, record, observation["classification"], observation["summary"], reservation_id=reservation_id)
            elif len(current["steps"]) == len(record["manifest"]["steps"]) and all(
                    record["reservations"][rid]["state"] == "completed" for rid in current["steps"].values()):
                current["state"] = "completed"
                current["completed_at"] = _now()
                current["matched"] = all(record["reservations"][rid]["matched"] for rid in current["steps"].values())
                if current["matched"]:
                    record["state"] = "passed"
                elif current["round_number"] == 1:
                    record["state"] = "awaiting_repair"
                else:
                    record["state"] = "stopped"
                    record["stop_reason"] = {"category": "second_round_failed",
                                             "reason": "Second frozen round did not meet every expectation", "details": {}, "at": _now()}
                self._append(state, "round_completed", {"contract_id": record["contract_id"],
                                                        "round_number": current["round_number"], "matched": current["matched"],
                                                        "state": record["state"]})
            return self._public(record)

    def authorize_repair(self, contract_id: str, *, observed_hashes: dict[str, str],
                         changed_paths: list[str], rationale: str) -> dict[str, Any]:
        """Adopt one actual diff, without changing criteria, scope or execution.

        The host performs the already-allowlisted candidate edit, then calls this
        method. Input and code edits share the same allowance. Old bytes remain
        in immutable blobs, and both round hashes remain in the journal. Added,
        deleted and renamed paths count as actual differences.
        """
        with self._lock:
            state, _, _ = self._load()
            record = self._contract(state, contract_id)
            self._active(record)
            if record["state"] != "awaiting_repair" or record["repair_used"] != 0 or len(record["rounds"]) != 1:
                _fail("repair_not_authorized", "Repair requires the completed first failed round and unused single repair")
            try:
                _text(rationale, "rationale")
                declared = _list(changed_paths, "changed_paths", _rel, required=True)
                _hashes(observed_hashes, "observed_hashes", required=True)
                root, manifest = _root(record["workspace_root"]), record["manifest"]
                frozen = {path: _file_hash(root, path) for path in manifest["frozen_files"]}
                if frozen != manifest["frozen_files"]:
                    _fail("frozen_drift", "Repair cannot change frozen expectations, rules or inputs",
                          diff=_diff(manifest["frozen_files"], frozen))
                inventory = _inventory(root, manifest)
                changes = _diff(record["inventory"], inventory)
                if not changes["changed_paths"]:
                    _fail("empty_repair", "A no-op cannot authorize a second round")
                if set(changes["changed_paths"]) != set(declared):
                    _fail("repair_diff_mismatch", "Declared paths differ from the host-computed diff", actual=changes)
                if not set(changes["changed_paths"]).issubset(manifest["repair_allowlist"]):
                    _fail("repair_out_of_scope", "Actual repair changes a path outside the frozen allowlist", actual=changes)
                paths = (set(record["candidate_hashes"]) | set(changes["added"])) - set(changes["deleted"])
                candidate = {path: inventory[path] for path in sorted(paths)}
                if observed_hashes != candidate:
                    _fail("observed_hash_mismatch", "Repair hashes differ from actual candidate bytes", actual=candidate)
                inputs = {path for step in manifest["steps"] for path in step["inputs"]}
                if inputs & set(changes["deleted"]):
                    _fail("deleted_step_input", "The frozen execution plan cannot lose an input",
                          paths=sorted(inputs & set(changes["deleted"])))
                blobs = self._archive(root, candidate)
            except (AcceptanceControlError, OSError) as exc:
                code = exc.code if isinstance(exc, AcceptanceControlError) else "filesystem_unavailable"
                self._refuse(state, record, code, "Cannot adopt this repair within the frozen contract", cause=str(exc))
            repair = {"repair_number": 1, "at": _now(), "rationale": rationale, "diff": changes,
                      "before_candidate_hashes": copy.deepcopy(record["candidate_hashes"]),
                      "before_candidate_blobs": copy.deepcopy(record["candidate_blobs"]),
                      "after_candidate_hashes": candidate, "after_candidate_blobs": blobs,
                      "parent_round": 1, "next_round": 2, "manifest_sha256": record["manifest_sha256"]}
            record["repair_used"] = 1
            record["repairs"].append(repair)
            record["candidate_hashes"], record["candidate_blobs"] = candidate, blobs
            record["inventory"], record["state"] = inventory, "repair_frozen"
            self._append(state, "repair_frozen", {"contract_id": contract_id, **repair})
            return {"contract_id": contract_id, "state": "repair_frozen", "repair_used": 1, **copy.deepcopy(repair)}

    def stop(self, contract_id: str, reason: str, *, category: str = "host_stop") -> dict[str, Any]:
        _text(reason, "reason")
        _id(category, "category")
        with self._lock:
            state, _, _ = self._load()
            record = self._contract(state, contract_id)
            if record["state"] not in TERMINAL_STATES:
                self._terminal(state, record, category, reason)
            return self._public(record)
