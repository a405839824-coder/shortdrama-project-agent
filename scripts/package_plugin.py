#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal


PackageProfile = Literal["source", "connected"]

EXCLUDED_ANY_PARTS = {
    ".git",
    ".pytest_cache",
    ".railway",
    ".venv",
    "__pycache__",
}
EXCLUDED_TOP_LEVEL_PARTS = {"data", "dist"}

# Source archives are review/deployment bundles. They deliberately retain the
# server, deployment support and bundled stdio descriptor, but they do not
# contain a Python environment and must not be presented as portable installs.
SOURCE_DIRECTORIES = {
    ".github",
    ".codex-plugin",
    "assets",
    "deploy",
    "docs",
    "scripts",
    "server",
    "skills",
    "tests",
}
SOURCE_TOP_LEVEL_FILES = {
    ".gitattributes",
    ".app.json.example",
    ".dockerignore",
    ".env.cloud.example",
    ".gitignore",
    ".railwayignore",
    "Dockerfile",
    "LICENSE",
    "LICENSE.md",
    "NOTICE",
    "NOTICE.md",
    "README.md",
    "docker-compose.cloud.yml",
    "docker-compose.yml",
    "marketplace.example.json",
    "pyproject.toml",
    "railway.json",
    "requirements.txt",
    "uv.lock",
}

# A connected archive is the small private ChatGPT install bundle. The remote
# MCP connection is authoritative, so server/deployment/test material and the
# local stdio descriptor are intentionally omitted.
CONNECTED_DIRECTORIES = {".codex-plugin", "assets", "skills"}
CONNECTED_TOP_LEVEL_FILES = {
    "LICENSE",
    "LICENSE.md",
    "NOTICE",
    "NOTICE.md",
    "README.md",
}

SENSITIVE_FILENAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".railway.env",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
    "service-account.json",
    "service_account.json",
}
SENSITIVE_SUFFIXES = {
    ".7z",
    ".gz",
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".rar",
    ".tar",
    ".tgz",
    ".zip",
}

HIGH_CONFIDENCE_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"),
    ),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("Stripe live key", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}\b")),
    (
        "JWT",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\."
            r"[A-Za-z0-9_-]{10,}\b"
        ),
    ),
)

CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?im)(?:^|[\"'])\s*"
    r"(?:(?:(?:openai|anthropic|google|azure|aws)[_-]?)?api[_-]?key|"
    r"client[_-]?secret|password|access[_-]?token|"
    r"refresh[_-]?token|railway[_-]?token)"
    r"\s*[\"']?\s*[:=]\s*(?P<quote>[\"'])"
    r"(?P<value>[^\r\n\"']+)(?P=quote)"
)
URL_CREDENTIAL_RE = re.compile(
    r"(?i)(?:[?&](?:api[_-]?key|access[_-]?token|token|secret)=)"
    r"(?P<value>[^&\s\"']+)"
)
UNQUOTED_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*(?:export\s+)?"
    r"(?:(?:(?:openai|anthropic|google|azure|aws)[_-]?)?api[_-]?key|"
    r"client[_-]?secret|password|access[_-]?token|"
    r"refresh[_-]?token|railway[_-]?token)"
    r"\s*=\s*(?P<value>[^\s#]+)"
)

PLACEHOLDER_WORDS = {
    "127.0.0.1",
    "changeme",
    "dummy",
    "example",
    "fake",
    "localhost",
    "never-log",
    "placeholder",
    "redacted",
    "replace",
    "sample",
    "test",
    "your",
    "your-",
    "your_",
    "xxx",
}
PLACEHOLDER_WORD_RE = re.compile(
    r"(?:^|[^a-z0-9])(" + "|".join(re.escape(value) for value in sorted(PLACEHOLDER_WORDS))
    + r")(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)
CONNECTION_RE = re.compile(r"^(?:plugin_)?asdk_app[A-Za-z0-9_-]{8,}$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
MANIFEST_PATH_FIELDS = ("skills", "apps", "hooks")
INTERFACE_FILE_FIELDS = ("composerIcon", "logo", "logoDark")
PACKAGE_METADATA_NAME = ".codex-package.json"


class PackagingError(ValueError):
    """Raised when a package would be incomplete or unsafe."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackagingError(f"cannot read valid {label}: {path}") from error
    if not isinstance(value, dict):
        raise PackagingError(f"{label} must contain a JSON object: {path}")
    return value


def _safe_manifest_reference(value: str, field: str) -> str:
    if not value.startswith("./"):
        raise PackagingError(f"manifest {field} must be a ./ relative path: {value!r}")
    reference = PurePosixPath(value[2:])
    if not reference.parts or reference.is_absolute() or ".." in reference.parts:
        raise PackagingError(f"manifest {field} escapes the plugin root: {value!r}")
    normalized = reference.as_posix().rstrip("/")
    if not normalized or normalized == ".":
        raise PackagingError(f"manifest {field} cannot reference the plugin root")
    return normalized


def _manifest_references(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    for field in MANIFEST_PATH_FIELDS:
        value = manifest.get(field)
        if value is not None and not isinstance(value, str):
            raise PackagingError(f"manifest {field} must be a file path string")
        if isinstance(value, str):
            references.append((field, _safe_manifest_reference(value, field)))

    mcp_servers = manifest.get("mcpServers")
    if isinstance(mcp_servers, str):
        references.append(
            ("mcpServers", _safe_manifest_reference(mcp_servers, "mcpServers"))
        )
    elif mcp_servers is not None and not isinstance(mcp_servers, dict):
        raise PackagingError("manifest mcpServers must be a path or object")

    interface = manifest.get("interface")
    if interface is not None and not isinstance(interface, dict):
        raise PackagingError("manifest interface must be an object")
    if isinstance(interface, dict):
        for field in INTERFACE_FILE_FIELDS:
            value = interface.get(field)
            if value is not None and not isinstance(value, str):
                raise PackagingError(f"manifest interface.{field} must be a file path string")
            if isinstance(value, str):
                references.append(
                    (
                        f"interface.{field}",
                        _safe_manifest_reference(value, f"interface.{field}"),
                    )
                )
        screenshots = interface.get("screenshots")
        if screenshots is not None:
            if not isinstance(screenshots, list) or not all(
                isinstance(value, str) for value in screenshots
            ):
                raise PackagingError("manifest interface.screenshots must be a string list")
            for index, value in enumerate(screenshots):
                references.append(
                    (
                        f"interface.screenshots[{index}]",
                        _safe_manifest_reference(
                            value, f"interface.screenshots[{index}]"
                        ),
                    )
                )
    return references


def _validate_profile(
    plugin_root: Path,
    manifest: dict[str, Any],
    profile: PackageProfile,
) -> None:
    if profile == "source":
        if "apps" in manifest:
            raise PackagingError(
                "source profile cannot contain manifest apps; use a clean source "
                "manifest or select --profile connected"
            )
        if manifest.get("mcpServers") != "./.mcp.json":
            raise PackagingError(
                "source profile requires manifest mcpServers to reference ./.mcp.json"
            )
        if not (plugin_root / ".mcp.json").is_file():
            raise PackagingError("source profile requires .mcp.json")
        mcp_manifest = _read_json(plugin_root / ".mcp.json", "MCP manifest")
        servers = mcp_manifest.get("mcpServers")
        plugin_name = str(manifest.get("name") or "")
        entry = servers.get(plugin_name) if isinstance(servers, dict) else None
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
            raise PackagingError(
                "source MCP manifest must contain the plugin command entry"
            )
        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise PackagingError("source MCP manifest args must be a string list")
        return

    if "mcpServers" in manifest:
        raise PackagingError(
            "connected profile forbids manifest mcpServers; the ChatGPT connection "
            "must be authoritative"
        )
    apps = manifest.get("apps")
    if not isinstance(apps, str):
        raise PackagingError("connected profile requires manifest apps as a file path")
    app_reference = _safe_manifest_reference(apps, "apps")
    if app_reference != ".app.json":
        raise PackagingError("connected profile requires manifest apps to reference ./.app.json")
    app_path = plugin_root / Path(app_reference)
    if not app_path.is_file():
        raise PackagingError(
            f"connected profile requires the manifest apps file: {app_path}"
        )
    app_manifest = _read_json(app_path, "app manifest")
    apps_object = app_manifest.get("apps")
    plugin_name = str(manifest.get("name") or "")
    if not isinstance(apps_object, dict) or set(apps_object) != {plugin_name}:
        raise PackagingError(
            "connected app manifest must contain exactly the matching plugin entry"
        )
    app_entry = apps_object.get(plugin_name) if isinstance(apps_object, dict) else None
    if not isinstance(app_entry, dict):
        raise PackagingError(
            "connected app manifest must contain an entry matching the plugin name"
        )
    connection_id = app_entry.get("id")
    if (
        not isinstance(connection_id, str)
        or not CONNECTION_RE.fullmatch(connection_id)
        or _is_placeholder_connection(connection_id)
    ):
        raise PackagingError("connected app manifest has an invalid ChatGPT connection ID")
    # Older source descriptors used required=true. The host's current schema
    # accepts only id/category; formal production is enforced by the service,
    # not an unsupported manifest flag. Normalize legacy true during packaging.
    if "required" in app_entry and app_entry["required"] is not True:
        raise PackagingError("legacy connected app manifest must set required=true")
    if set(app_entry) - {"id", "category", "required"}:
        raise PackagingError("connected app manifest contains unsupported entry fields")
    if "category" in app_entry and (
        not isinstance(app_entry["category"], str) or not app_entry["category"].strip()
    ):
        raise PackagingError("connected app manifest category must be a nonempty string")


def _is_sensitive_path(relative: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in relative.parts)
    name = lowered_parts[-1]
    if name in SENSITIVE_FILENAMES:
        return True
    if name.startswith(".env") and not name.endswith(".example"):
        return True
    if name.endswith(".env"):
        return True
    if (name.startswith("credentials") or name.startswith("secrets")) and name.endswith(
        ".json"
    ):
        return True
    if Path(name).suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    return any(part in {"credentials", "secrets"} for part in lowered_parts[:-1])


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    # Source/test fixtures often contain an escaped line ending inside a
    # quoted literal (for example ``token=NEVER-LOG\n``).  Strip only these
    # trailing presentation escapes before exact placeholder matching.
    lowered = re.sub(r"(?:\\[nrt])+$", "", lowered)
    if lowered.startswith(("${", "{{", "<")):
        return True
    if lowered in {"never-log", "never_log", "xxx"}:
        return True
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    return bool(tokens) and tokens[0] in {
        "changeme",
        "dummy",
        "example",
        "fake",
        "placeholder",
        "redacted",
        "replace",
        "sample",
        "test",
        "your",
    }


def _is_placeholder_connection(value: str) -> bool:
    tokens = {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}
    return bool(tokens & {"changeme", "example", "placeholder", "replace", "test", "xxx"})


def _scan_content(relative: Path, data: bytes) -> None:
    texts = [data.decode("utf-8", errors="replace")]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")) or data.count(b"\x00") > len(data) // 5:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                decoded = data.decode(encoding)
            except UnicodeError:
                continue
            if decoded not in texts:
                texts.append(decoded)

    for text in texts:
        _scan_text(relative, text)


def _scan_text(relative: Path, text: str) -> None:

    for label, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        for match in pattern.finditer(text):
            raise PackagingError(f"possible {label} in {relative.as_posix()}")

    for pattern, label in (
        (CREDENTIAL_ASSIGNMENT_RE, "credential assignment"),
        (URL_CREDENTIAL_RE, "URL credential"),
    ):
        for match in pattern.finditer(text):
            if not _is_placeholder(match.group("value")):
                raise PackagingError(f"possible {label} in {relative.as_posix()}")

    for match in UNQUOTED_CREDENTIAL_ASSIGNMENT_RE.finditer(text):
        value = match.group("value")
        if any(character in value for character in "()[]{}"):
            continue
        if not _is_placeholder(value):
            raise PackagingError(
                f"possible unquoted credential assignment in {relative.as_posix()}"
            )


def _profile_allows(relative: Path, profile: PackageProfile) -> bool:
    if len(relative.parts) == 1:
        name = relative.name
        if profile == "source":
            return name in SOURCE_TOP_LEVEL_FILES or name == ".mcp.json"
        return name in CONNECTED_TOP_LEVEL_FILES or name == ".app.json"

    top = relative.parts[0]
    if profile == "source":
        return top in SOURCE_DIRECTORIES
    if _connected_local_executable(relative):
        return False
    return top in CONNECTED_DIRECTORIES


def _connected_local_executable(relative: Path) -> bool:
    # Connected employees use the authenticated remote service. Shipping local
    # compilers/renderers gives fresh installs an avoidable fallback path.
    return relative.parts[0] == "skills" and (
        "scripts" in relative.parts
        or relative.suffix.lower() in {".py", ".ps1", ".js", ".mjs", ".cjs", ".sh", ".bat", ".cmd", ".exe", ".dll"}
    )


def _known_but_not_in_profile(relative: Path, profile: PackageProfile) -> bool:
    if profile == "source":
        return len(relative.parts) == 1 and relative.name == ".app.json"
    if _connected_local_executable(relative):
        return True
    if len(relative.parts) == 1:
        return relative.name in SOURCE_TOP_LEVEL_FILES or relative.name in {
            ".app.json.example",
            ".mcp.json",
        }
    return relative.parts[0] in SOURCE_DIRECTORIES - CONNECTED_DIRECTORIES


def _collect_files(plugin_root: Path, profile: PackageProfile) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    casefolded_names: dict[str, str] = {}
    for path in sorted(plugin_root.rglob("*")):
        relative = path.relative_to(plugin_root)
        if relative.parts[0] in EXCLUDED_TOP_LEVEL_PARTS or any(
            part in EXCLUDED_ANY_PARTS for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise PackagingError(f"symbolic links are not allowed: {relative.as_posix()}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise PackagingError(f"only regular files may be packaged: {relative.as_posix()}")
        if path.suffix == ".pyc":
            continue
        if any(
            "\\" in part
            or ":" in part
            or any(character in part for character in '<>"|?*')
            or part.rstrip(" .") != part
            or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
            or any(ord(character) < 32 for character in part)
            for part in relative.parts
        ):
            raise PackagingError(
                f"unsafe package path is not allowed: {relative.as_posix()}"
            )
        if len(relative.parts) == 1 and relative.name == PACKAGE_METADATA_NAME:
            # Repacking an extracted source bundle must replace, not retain, its
            # generated profile marker.
            continue
        if not _profile_allows(relative, profile):
            if _known_but_not_in_profile(relative, profile):
                continue
            raise PackagingError(
                f"file is outside the controlled {profile} package roots: "
                f"{relative.as_posix()}"
            )
        if _is_sensitive_path(relative):
            raise PackagingError(
                f"sensitive file is not allowed in packages: {relative.as_posix()}"
            )
        data = path.read_bytes()
        _scan_content(relative, data)
        archive_name = relative.as_posix()
        folded = unicodedata.normalize("NFC", archive_name).casefold()
        existing = casefolded_names.get(folded)
        if existing is not None and existing != archive_name:
            raise PackagingError(
                f"case-insensitive package path collision: {existing} and {archive_name}"
            )
        casefolded_names[folded] = archive_name
        files[archive_name] = data
    return files


def _reference_is_present(field: str, reference: str, names: set[str]) -> bool:
    if field == "skills":
        return any(name.startswith(f"{reference}/") for name in names)
    return reference in names


def _validate_reference_closure(
    manifest: dict[str, Any],
    files: dict[str, bytes],
) -> None:
    names = set(files)
    for field, reference in _manifest_references(manifest):
        if not _reference_is_present(field, reference, names):
            raise PackagingError(
                f"manifest reference is missing from the package: {field} -> {reference}"
            )


def _package_metadata(profile: PackageProfile) -> bytes:
    if profile == "source":
        payload = {
            "schema_version": 1,
            "profile": "source",
            "portable_install": False,
            "purpose": (
                "Source and deployment review bundle. It does not contain a Python "
                "environment; install from a prepared source checkout."
            ),
        }
    else:
        payload = {
            "schema_version": 1,
            "profile": "connected",
            "portable_install": True,
            "production_execution": "authenticated_remote_only",
            "local_export_fallback": False,
            "formal_delivery_contract": "formal-storyboard-delivery-v1",
            "purpose": (
                "Private ChatGPT plugin bundle bound to the included app connection "
                "descriptor."
            ),
        }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_archive_atomic(output: Path, plugin_name: str, files: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for relative, data in sorted(files.items()):
                info = zipfile.ZipInfo(f"{plugin_name}/{relative}")
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = 0o100755 if relative.endswith(".sh") else 0o100644
                info.external_attr = mode << 16
                archive.writestr(info, data, compresslevel=9)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _read_existing_sums(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s{2}([^/\\]+)", line)
        if match is None:
            raise PackagingError(
                f"invalid SHA256SUMS line {line_number} in {path}"
            )
        entries[match.group(2)] = match.group(1).lower()
    return entries


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update_sha256sums(
    output: Path,
    *,
    existing_entries: dict[str, str] | None = None,
) -> Path:
    checksum_path = output.parent / "SHA256SUMS"
    entries = dict(
        existing_entries
        if existing_entries is not None
        else _read_existing_sums(checksum_path)
    )
    entries[output.name] = hashlib.sha256(output.read_bytes()).hexdigest()
    content = "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))
    _write_text_atomic(checksum_path, content)
    return checksum_path


def _validate_output_path(plugin_root: Path, output: Path) -> Path:
    raw_output = output if output.is_absolute() else Path.cwd() / output
    raw_output = Path(os.path.abspath(raw_output))
    if raw_output.is_symlink():
        raise PackagingError("package output cannot be a symbolic link")
    name = raw_output.name
    if (
        raw_output.suffix.lower() != ".zip"
        or "\\" in name
        or any(ord(character) < 32 for character in name)
    ):
        raise PackagingError("package output must be a safe .zip filename")

    try:
        relative = raw_output.relative_to(plugin_root)
    except ValueError:
        relative = None
    if relative is not None:
        if not relative.parts or relative.parts[0] != "dist":
            raise PackagingError("package output inside the plugin must be under dist/")
        cursor = plugin_root
        for part in relative.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PackagingError("package output cannot traverse a symbolic-link directory")
    resolved = raw_output.resolve()
    if resolved.exists() and (resolved.is_symlink() or not resolved.is_file()):
        raise PackagingError("package output must be a regular file when it already exists")
    return resolved


def package(
    plugin_root: Path,
    output: Path | None = None,
    *,
    profile: PackageProfile = "source",
) -> Path:
    if profile not in {"source", "connected"}:
        raise PackagingError(f"unsupported package profile: {profile!r}")
    plugin_root = plugin_root.resolve()
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = _read_json(manifest_path, "plugin manifest")
    plugin_name = manifest.get("name")
    version = manifest.get("version")
    if not isinstance(plugin_name, str) or not PLUGIN_NAME_RE.fullmatch(plugin_name):
        raise PackagingError("plugin manifest has an invalid name")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise PackagingError("plugin manifest has an invalid version")
    if plugin_root.name != plugin_name:
        raise PackagingError(
            f"plugin folder name {plugin_root.name!r} does not match manifest name "
            f"{plugin_name!r}"
        )

    _validate_profile(plugin_root, manifest, profile)
    files = _collect_files(plugin_root, profile)
    if profile == "connected":
        app_manifest = json.loads(files[".app.json"].decode("utf-8"))
        app_manifest["apps"][plugin_name].pop("required", None)
        files[".app.json"] = (json.dumps(app_manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if profile == "connected" and "skills/shortdrama-storyboard/SKILL.md" in files:
        client_skill = "skills/shortdrama-storyboard/references/connected_client_skill.md"
        if client_skill not in files:
            raise PackagingError("connected storyboard package requires its remote-only client skill")
        files["skills/shortdrama-storyboard/SKILL.md"] = files[client_skill]
    files[PACKAGE_METADATA_NAME] = _package_metadata(profile)
    _validate_reference_closure(manifest, files)

    if output is None:
        suffix = "" if profile == "source" else "-connected"
        output = plugin_root / "dist" / f"{plugin_name}-{version}{suffix}.zip"
    output = _validate_output_path(plugin_root, output)
    existing_sums = _read_existing_sums(output.parent / "SHA256SUMS")
    previous_output = output.read_bytes() if output.exists() else None
    _write_archive_atomic(output, plugin_name, files)
    try:
        update_sha256sums(output, existing_entries=existing_sums)
    except BaseException:
        if previous_output is None:
            output.unlink(missing_ok=True)
        else:
            _write_bytes_atomic(output, previous_output)
        raise
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a guarded plugin ZIP. The legacy default is the non-portable "
            "source profile; use --profile connected for a private ChatGPT install bundle."
        )
    )
    parser.add_argument(
        "--plugin-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--profile",
        choices=("source", "connected"),
        default="source",
        help=(
            "source keeps server/deployment/.mcp.json and is not portable; connected "
            "requires .app.json and excludes local MCP/server material"
        ),
    )
    args = parser.parse_args()
    output = package(args.plugin_root, args.output, profile=args.profile)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
