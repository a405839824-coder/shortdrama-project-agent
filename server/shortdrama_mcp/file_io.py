from __future__ import annotations

import base64
import binascii
import csv
import datetime as dt
import hashlib
import hmac
import io
import ipaddress
import json
import mimetypes
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pypdf import PdfReader

from .config import Settings
from .materializer import (
    COMPACT_SCHEMA_VERSION,
    COMPACT_SHOT_FIELDS,
    FORMAL_MERGED_FIELD,
    GPT_KEYFRAME_FIELD,
    LEGACY_STANDALONE_FIELDS,
    MOTHER_TEMPLATE_COLUMNS,
    formal_mother_template_rows,
    migrate_legacy_shot,
    migrate_storyboard_to_compact,
)
from .models import OpenAIFile
from .storage import slug_filename


SHOT_FIELDS = list(COMPACT_SHOT_FIELDS)

BATCH_FIELDS = [
    "批次编号", "覆盖镜头", "时长", "主运镜", "生成限制", "起始画面", "起始表演状态", "动作过程",
    "听话反应", "结束状态", "摄影机", "声音", "环境动态", "光影", "独立执行提示词",
]

MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_BULK_RESPONSE_CHARS = 16_000
MAX_BULK_RANGES = 16
DEFAULT_BULK_RESPONSE_CHARS = 16_000
DEFAULT_BULK_CHARACTER_LIMIT = 16_000
DEFAULT_BULK_ITEM_LIMITS = {"paragraphs": 80, "pages": 8, "rows": 50}

CANONICAL_SIDECAR_SHEET = "__shortdrama_canonical_v1"
CANONICAL_SIDECAR_MARKER = "SHORTDRAMA_CANONICAL_V1"
CANONICAL_SIDECAR_FORMAT = "canonical-storyboard-json-v1"
CANONICAL_SIDECAR_ENCODING = "base64url-utf8-json"
CANONICAL_SIDECAR_PAYLOAD_PREFIX = "b64u:"
CANONICAL_SIDECAR_CHUNK_CHARS = 24_000
CANONICAL_SIDECAR_HEADERS = [
    "record_type",
    "marker",
    "format",
    "encoding",
    "sha256",
    "chunk_index",
    "chunk_count",
    "payload",
]


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return str(value)


def _is_local_host(host: str) -> bool:
    return host.casefold() in {"localhost", "127.0.0.1", "::1"}


def validate_download_url(url: str, settings: Settings) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password:
        raise ValueError("file download URL must not contain credentials")
    if parsed.fragment:
        raise ValueError("file download URL must not contain a fragment")
    if not parsed.hostname:
        raise ValueError("file download URL has no host")
    local_allowed = settings.allow_local_file_urls and _is_local_host(parsed.hostname)
    if parsed.scheme != "https" and not (local_allowed and parsed.scheme == "http"):
        raise ValueError("file download URL must use HTTPS")
    if parsed.port not in {None, 80, 443} and not local_allowed:
        raise ValueError("file download URL uses a disallowed port")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError("file download host cannot be resolved") from exc
    for address in addresses:
        ip_value = ipaddress.ip_address(address[4][0])
        if not ip_value.is_global and not local_allowed:
            raise ValueError("file download URL resolves to a private or reserved address")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, settings: Settings):
        self.settings = settings

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        validate_download_url(newurl, self.settings)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_openai_file(file: OpenAIFile, settings: Settings) -> tuple[bytes, str, str]:
    validate_download_url(file.download_url, settings)
    request = urllib.request.Request(
        file.download_url,
        headers={"User-Agent": "shortdrama-project-agent/0.3"},
        method="GET",
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler(settings), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > settings.max_file_bytes:
                raise ValueError("uploaded file exceeds MAX_FILE_BYTES")
            buffer = bytearray()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > settings.max_file_bytes:
                    raise ValueError("uploaded file exceeds MAX_FILE_BYTES")
            mime_type = file.mime_type or response.headers.get_content_type() or "application/octet-stream"
    except urllib.error.HTTPError as exc:
        raise ValueError(f"file download failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"file download failed: {exc.reason}") from exc
    name = slug_filename(file.file_name or Path(urllib.parse.urlsplit(file.download_url).path).name, "uploaded-file")
    return bytes(buffer), name, mime_type


def _validate_zip_archive(
    data: bytes,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_uncompressed_bytes: int = MAX_ARCHIVE_UNCOMPRESSED_BYTES,
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > max_entries:
                raise ValueError("archive contains too many entries")
            total = sum(max(0, int(item.file_size)) for item in entries)
            if total > max_uncompressed_bytes:
                raise ValueError("archive expands beyond the safe limit")
            if any(item.filename.startswith(("/", "\\")) or ".." in Path(item.filename).parts for item in entries):
                raise ValueError("archive contains an unsafe path")
    except zipfile.BadZipFile as exc:
        raise ValueError("uploaded Office file is not a valid ZIP archive") from exc


def _rows_from_sheet(sheet: Any) -> tuple[list[str], list[dict[str, Any]]]:
    values = list(sheet.iter_rows(values_only=True))
    if not values:
        return [], []
    header_index = 0
    for index, row in enumerate(values[:20]):
        normalized = [str(value).strip() if value is not None else "" for value in row]
        if any(normalized):
            header_index = index
            break
    raw_headers = [str(value).strip() if value is not None else "" for value in values[header_index]]
    last = max((index for index, value in enumerate(raw_headers) if value), default=-1)
    headers = raw_headers[: last + 1]
    seen: dict[str, int] = {}
    unique_headers: list[str] = []
    for index, header in enumerate(headers, 1):
        base = header or f"列{index}"
        seen[base] = seen.get(base, 0) + 1
        unique_headers.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    rows = []
    for raw in values[header_index + 1 :]:
        row = {header: json_safe(raw[index] if index < len(raw) else None) for index, header in enumerate(unique_headers)}
        if any(value not in (None, "") for value in row.values()):
            rows.append(row)
    return unique_headers, rows


def _json_cell(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return value


def _canonical_sidecar_error(reason: str) -> ValueError:
    return ValueError(f"canonical sidecar invalid: {reason}")


def _canonical_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise _canonical_sidecar_error(f"{field} must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise _canonical_sidecar_error(f"{field} must be a positive integer")
    if parsed <= 0:
        raise _canonical_sidecar_error(f"{field} must be a positive integer")
    return parsed


def _canonical_storyboard_from_sidecar(sheets: list[dict[str, Any]]) -> dict[str, Any] | None:
    sidecars = [sheet for sheet in sheets if sheet.get("name") == CANONICAL_SIDECAR_SHEET]
    if not sidecars:
        return None
    if len(sidecars) != 1:
        raise _canonical_sidecar_error("duplicate reserved sidecar sheets")

    sidecar = sidecars[0]
    if sidecar.get("headers") != CANONICAL_SIDECAR_HEADERS:
        raise _canonical_sidecar_error("sidecar header or marker is missing")
    rows = sidecar.get("rows")
    if not isinstance(rows, list):
        raise _canonical_sidecar_error("sidecar rows are missing")

    manifests = [row for row in rows if row.get("record_type") == "manifest"]
    chunks = [row for row in rows if row.get("record_type") == "chunk"]
    if len(manifests) != 1:
        raise _canonical_sidecar_error("exactly one manifest is required")
    if len(manifests) + len(chunks) != len(rows):
        raise _canonical_sidecar_error("unknown sidecar record type")

    manifest = manifests[0]
    if manifest.get("marker") != CANONICAL_SIDECAR_MARKER:
        raise _canonical_sidecar_error("manifest marker mismatch")
    if manifest.get("format") != CANONICAL_SIDECAR_FORMAT:
        raise _canonical_sidecar_error("manifest format mismatch")
    if manifest.get("encoding") != CANONICAL_SIDECAR_ENCODING:
        raise _canonical_sidecar_error("manifest encoding mismatch")
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise _canonical_sidecar_error("manifest SHA-256 is malformed")
    chunk_count = _canonical_positive_int(manifest.get("chunk_count"), "chunk_count")
    if len(chunks) != chunk_count:
        raise _canonical_sidecar_error("canonical payload chunk count mismatch")

    payload_by_index: dict[int, str] = {}
    for chunk in chunks:
        if chunk.get("marker") != CANONICAL_SIDECAR_MARKER:
            raise _canonical_sidecar_error("chunk marker mismatch")
        if chunk.get("format") != CANONICAL_SIDECAR_FORMAT:
            raise _canonical_sidecar_error("chunk format mismatch")
        if chunk.get("encoding") != CANONICAL_SIDECAR_ENCODING:
            raise _canonical_sidecar_error("chunk encoding mismatch")
        if chunk.get("sha256") != digest:
            raise _canonical_sidecar_error("chunk SHA-256 manifest mismatch")
        if _canonical_positive_int(chunk.get("chunk_count"), "chunk_count") != chunk_count:
            raise _canonical_sidecar_error("chunk count manifest mismatch")
        index = _canonical_positive_int(chunk.get("chunk_index"), "chunk_index")
        if index > chunk_count:
            raise _canonical_sidecar_error("chunk index is out of range")
        if index in payload_by_index:
            raise _canonical_sidecar_error("duplicate canonical payload chunk")
        payload = chunk.get("payload")
        if not isinstance(payload, str) or not payload.startswith(CANONICAL_SIDECAR_PAYLOAD_PREFIX):
            raise _canonical_sidecar_error("chunk payload encoding marker is missing")
        encoded = payload[len(CANONICAL_SIDECAR_PAYLOAD_PREFIX) :]
        if not encoded:
            raise _canonical_sidecar_error("canonical payload chunk is empty")
        payload_by_index[index] = encoded

    expected_indexes = set(range(1, chunk_count + 1))
    if set(payload_by_index) != expected_indexes:
        raise _canonical_sidecar_error("canonical payload chunk sequence is incomplete")
    encoded_payload = "".join(payload_by_index[index] for index in range(1, chunk_count + 1))
    try:
        serialized = base64.b64decode(encoded_payload, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _canonical_sidecar_error("canonical payload is not valid base64url") from exc
    actual_digest = hashlib.sha256(serialized).hexdigest()
    if not hmac.compare_digest(actual_digest, digest):
        raise _canonical_sidecar_error("canonical payload SHA-256 mismatch")
    try:
        canonical = json.loads(serialized.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _canonical_sidecar_error("canonical payload is not valid UTF-8 JSON") from exc
    if not isinstance(canonical, dict):
        raise _canonical_sidecar_error("canonical storyboard root must be an object")
    return canonical


def _metadata_from_sheets(sheets: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata_sheet = next(
        (sheet for sheet in sheets if sheet.get("name") in {"元数据", "metadata"}),
        None,
    )
    evidence_sheet = next(
        (
            sheet
            for sheet in sheets
            if sheet.get("name") in {"流程证据", "prevention_evidence"}
        ),
        None,
    )
    metadata: dict[str, Any] = {}
    if metadata_sheet:
        for row in metadata_sheet.get("rows", []):
            values = list(row.values())
            if values and values[0] not in (None, ""):
                metadata[str(values[0])] = _json_cell(
                    values[1] if len(values) > 1 else None,
                    None,
                )
    evidence: dict[str, Any] = {}
    if evidence_sheet:
        for row in evidence_sheet.get("rows", []):
            values = list(row.values())
            if values and values[0] not in (None, ""):
                evidence[str(values[0])] = _json_cell(
                    values[1] if len(values) > 1 else None,
                    None,
                )
    return metadata, evidence


def _parse_labeled_cell(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    parsed: dict[str, str] = {}
    accepted = set(COMPACT_SHOT_FIELDS) | LEGACY_STANDALONE_FIELDS | {
        "切镜转场提示词",
        "光源",
        "光线方向",
        "色温",
    }
    current_label: str | None = None
    # Legacy formal cells used both newlines and full-/half-width pipes as
    # structural separators. Unlabelled fragments remain continuations of the
    # preceding field (for example movement modifiers and picture time stages).
    for raw_line in re.split(r"[\n|｜]", str(value)):
        line = raw_line.strip()
        if not line:
            continue
        candidate = ""
        text = ""
        if "：" in line:
            candidate, text = line.split("：", 1)
            candidate = candidate.strip()
        if candidate in accepted:
            current_label = candidate
            if text.strip():
                parsed[current_label] = text.strip()
            else:
                parsed.setdefault(current_label, "")
            continue
        if current_label:
            prior = parsed.get(current_label, "")
            parsed[current_label] = f"{prior}\n{line}".strip()
    for old, current in (
        ("光源", "光源来源"),
        ("光线方向", "光源方向"),
        ("色温", "色温倾向"),
    ):
        if not parsed.get(current) and parsed.get(old):
            parsed[current] = parsed[old]
        parsed.pop(old, None)
    return parsed


def _parse_function_scene_cell(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    function = ""
    scene = ""
    function_match = re.search(
        r"功能类型\s*[:：]\s*(.*?)(?=\s*(?:[|｜]\s*)?场景连续性ID\s*[:：]|$)",
        text,
    )
    scene_match = re.search(r"场景连续性ID\s*[:：]\s*(.+)$", text)
    if function_match:
        function = function_match.group(1).strip()
    elif text and "场景连续性ID" not in text:
        function = text
    if scene_match:
        scene = scene_match.group(1).strip()
    return function, scene


def _compact_shots_from_mother_sheet(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for source in sheet.get("rows", []):
        shot = _parse_labeled_cell(source.get(FORMAL_MERGED_FIELD))
        shot["镜头编号"] = source.get("分镜序号")
        shot["时长"] = source.get("时长")
        shot["累计时长"] = source.get("累计时长")
        function, scene = _parse_function_scene_cell(
            source.get("功能类型/场景连续性ID")
        )
        shot["功能类型"] = function
        shot["场景连续性ID"] = scene
        for field in ("参考图锚定", "覆盖原文", "原文依据类型", "原文支撑句"):
            shot[field] = source.get(field)
        migrated = migrate_legacy_shot(shot, legacy=True)
        if not str(migrated.get("光影") or "").strip():
            raise ValueError(
                "materialization_input_missing: legacy migration cannot derive "
                f"光影 for {migrated.get('镜头编号') or '<unknown>'}"
            )
        shots.append(migrated)
    return shots


def workbook_to_storyboard(
    sheets: list[dict[str, Any]],
    *,
    migrate: bool = False,
) -> dict[str, Any] | None:
    sidecar_storyboard = _canonical_storyboard_from_sidecar(sheets)
    if sidecar_storyboard is not None:
        # Audit callers must verify the artifact's original canonical bytes and
        # SHA before any migration. New-production callers opt into the one-way
        # compact migration explicitly.
        if migrate:
            return migrate_storyboard_to_compact(sidecar_storyboard)
        return sidecar_storyboard

    metadata, evidence = _metadata_from_sheets(sheets)
    mother_sheet = next(
        (
            sheet
            for sheet in sheets
            if list(sheet.get("headers", [])) == list(MOTHER_TEMPLATE_COLUMNS)
        ),
        None,
    )
    if mother_sheet:
        shots = _compact_shots_from_mother_sheet(mother_sheet)
        total_duration = metadata.get("total_duration")
        if total_duration in (None, "") and shots:
            total_duration = shots[-1].get("累计时长", 0)
        return {
            "project_version": metadata.get(
                "project_version",
                "V16.3-148-R10-StableRuntime",
            ),
            "schema_version": COMPACT_SCHEMA_VERSION,
            "workflow_profile": metadata.get("workflow_profile", "R10-PG1"),
            "prevention_evidence": evidence,
            "field_order": list(COMPACT_SHOT_FIELDS),
            "source_lock": metadata.get("source_lock", {}),
            "shots": shots,
            "total_duration": total_duration or 0,
        }

    # Legacy R10.0 workbook import is read-only. Its atomic and batch sheets are
    # accepted only long enough to migrate each shot; batches are discarded.
    shots_sheet = next(
        (sheet for sheet in sheets if "镜头编号" in sheet.get("headers", [])),
        None,
    )
    if not shots_sheet:
        return None
    batches_sheet = next(
        (sheet for sheet in sheets if "批次编号" in sheet.get("headers", [])),
        None,
    )
    identity_sheet = next(
        (sheet for sheet in sheets if sheet.get("name") == "内部标识"),
        None,
    )
    shots = list(shots_sheet.get("rows", []))
    batches = list(batches_sheet.get("rows", [])) if batches_sheet else []
    for row in batches:
        if isinstance(row.get("覆盖镜头"), str):
            row["覆盖镜头"] = [
                item.strip()
                for item in row["覆盖镜头"].replace("，", ",").split(",")
                if item.strip()
            ]
    if identity_sheet:
        shot_identity = {
            str(row.get("显示编号") or ""): row
            for row in identity_sheet.get("rows", [])
            if row.get("类型") == "shot"
        }
        batch_identity = {
            str(row.get("显示编号") or ""): row
            for row in identity_sheet.get("rows", [])
            if row.get("类型") == "batch"
        }
        for row in shots:
            identity = shot_identity.get(str(row.get("镜头编号") or ""))
            if identity:
                for field in ("shot_uid", "episode_id", "scene_uid"):
                    if identity.get(field) not in (None, ""):
                        row[field] = identity[field]
        for row in batches:
            identity = batch_identity.get(str(row.get("批次编号") or ""))
            if identity:
                for field in ("batch_uid", "episode_id"):
                    if identity.get(field) not in (None, ""):
                        row[field] = identity[field]
    total_duration = metadata.get("total_duration")
    if total_duration in (None, "") and shots:
        total_duration = shots[-1].get("累计时长", 0)
    legacy = {
        "project_version": metadata.get(
            "project_version",
            "V16.3-148-R10-StableRuntime",
        ),
        "schema_version": "R10.0",
        "workflow_profile": metadata.get("workflow_profile", "R10-PG1"),
        "prevention_evidence": evidence,
        "field_order": shots_sheet.get("headers", []),
        "source_lock": metadata.get("source_lock", {}),
        "shots": shots,
        "batches": batches,
        "total_duration": total_duration or 0,
    }
    # Without a canonical sidecar there is no historical canonical SHA to
    # preserve. The legacy atom sheet is therefore migrated exactly once on
    # import and never re-exported in its old shape.
    return migrate_storyboard_to_compact(legacy)


def extract_xlsx(data: bytes) -> dict[str, Any]:
    _validate_zip_archive(data)
    workbook = load_workbook(io.BytesIO(data), data_only=False, read_only=True)
    sheets = []
    for sheet in workbook.worksheets:
        headers, rows = _rows_from_sheet(sheet)
        sheets.append({"name": sheet.title, "headers": headers, "row_count": len(rows), "rows": rows})
    canonical = workbook_to_storyboard(sheets)
    public_sheets = [sheet for sheet in sheets if sheet.get("name") != CANONICAL_SIDECAR_SHEET]
    return {
        "kind": "workbook",
        "sheet_count": len(public_sheets),
        "sheets": public_sheets,
        "canonical_storyboard": canonical,
    }


def _extract_docx(data: bytes) -> dict[str, Any]:
    _validate_zip_archive(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t")).strip()
        if text:
            paragraphs.append(text)
    return {
        "kind": "text",
        "format": "docx",
        "paragraph_count": len(paragraphs),
        "paragraphs": paragraphs,
        "text": "\n".join(paragraphs),
    }


def _extract_pdf(data: bytes) -> dict[str, Any]:
    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    return {
        "kind": "text",
        "format": "pdf",
        "page_count": len(pages),
        "pages": pages,
        "text": "\n\n".join(pages),
    }


def _extract_csv(data: bytes) -> dict[str, Any]:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = [{str(key): json_safe(value) for key, value in row.items()} for row in reader]
    return {"kind": "table", "format": "csv", "headers": reader.fieldnames or [], "row_count": len(rows), "rows": rows}


def _extract_mm(data: bytes) -> dict[str, Any]:
    root = ElementTree.fromstring(data)
    lines: list[str] = []

    def visit(node: Any, depth: int) -> None:
        text = node.attrib.get("TEXT") or node.attrib.get("text")
        if text:
            lines.append(f"{'  ' * depth}- {text}")
        for child in list(node):
            visit(child, depth + (1 if text else 0))

    visit(root, 0)
    return {
        "kind": "text",
        "format": "freemind",
        "node_count": len(lines),
        "paragraphs": lines,
        "text": "\n".join(lines),
    }


def extract_file(data: bytes, file_name: str, mime_type: str | None = None) -> dict[str, Any]:
    suffix = Path(file_name).suffix.casefold()
    if suffix in {".xlsx", ".xlsm"}:
        return extract_xlsx(data)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix == ".csv":
        return _extract_csv(data)
    if suffix == ".json":
        return {"kind": "json", "format": "json", "value": json.loads(data.decode("utf-8-sig"))}
    if suffix == ".mm":
        return _extract_mm(data)
    if suffix in {".md", ".txt", ".srt", ".vtt"} or (mime_type or "").startswith("text/"):
        return {"kind": "text", "format": suffix.lstrip(".") or "text", "text": data.decode("utf-8-sig")}
    guessed = mimetypes.guess_type(file_name)[0]
    raise ValueError(f"unsupported input format: {suffix or mime_type or guessed or 'unknown'}")


def _cell_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        # Storyboard fields are prose, never formulas. Escape spreadsheet
        # formula prefixes to prevent execution when a generated XLSX is opened.
        return f"'{value}"
    return value


def _write_table(workbook: Workbook, title: str, headers: list[str], rows: list[dict[str, Any]]) -> Any:
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for row in rows:
        sheet.append([_cell_value(row.get(header)) for header in headers])
    sheet.freeze_panes = "A2"
    if headers:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(sheet.max_row, 1)}"
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F6F5F")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, header in enumerate(headers, 1):
        width = 14
        if header in {
            "画面内容",
            "动作与表演",
            "台词",
            "发声方式",
            "独立执行提示词",
            "正式分镜母版·字段合并",
            "GPT关键帧生成指令",
            "Seedance 2.5独立正式执行分镜",
            "证据",
            "修复动作",
        }:
            width = 34
        sheet.column_dimensions[get_column_letter(index)].width = width
    return sheet


def _write_canonical_sidecar(workbook: Workbook, storyboard: dict[str, Any]) -> None:
    canonical = json_safe(storyboard)
    if not isinstance(canonical, dict):
        raise ValueError("canonical storyboard root must be an object")
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()
    encoded = base64.urlsafe_b64encode(serialized).decode("ascii")
    chunks = [
        encoded[offset : offset + CANONICAL_SIDECAR_CHUNK_CHARS]
        for offset in range(0, len(encoded), CANONICAL_SIDECAR_CHUNK_CHARS)
    ]
    if not chunks:
        raise ValueError("canonical storyboard serialization is empty")

    sheet = workbook.create_sheet(CANONICAL_SIDECAR_SHEET)
    sheet.append(CANONICAL_SIDECAR_HEADERS)
    sheet.append(
        [
            "manifest",
            CANONICAL_SIDECAR_MARKER,
            CANONICAL_SIDECAR_FORMAT,
            CANONICAL_SIDECAR_ENCODING,
            digest,
            None,
            len(chunks),
            None,
        ]
    )
    for index, chunk in enumerate(chunks, 1):
        sheet.append(
            [
                "chunk",
                CANONICAL_SIDECAR_MARKER,
                CANONICAL_SIDECAR_FORMAT,
                CANONICAL_SIDECAR_ENCODING,
                digest,
                index,
                len(chunks),
                f"{CANONICAL_SIDECAR_PAYLOAD_PREFIX}{chunk}",
            ]
        )
    sheet.sheet_state = "veryHidden"


def _write_scope_audit_sheet(
    workbook: Workbook,
    storyboard: dict[str, Any],
    audit_report: dict[str, Any],
) -> Any:
    """Write the sample-compatible single visible scope/audit page."""

    sheet = workbook.create_sheet("00_范围与审计")
    sheet.append(["00_范围与审计", None, None, None, None])
    sheet.append([])
    sheet.append(["范围字段", "值", None, None, None])
    scope_rows = [
        ("project_version", storyboard.get("project_version")),
        ("schema_version", storyboard.get("schema_version")),
        ("workflow_profile", storyboard.get("workflow_profile")),
        ("total_duration", storyboard.get("total_duration")),
        ("source_lock", storyboard.get("source_lock", {})),
    ]
    for label, value in scope_rows:
        sheet.append([label, _cell_value(value), None, None, None])
    for key, value in (storyboard.get("prevention_evidence") or {}).items():
        sheet.append([f"prevention_evidence/{key}", _cell_value(value), None, None, None])

    sheet.append([])
    issue_header_row = sheet.max_row + 1
    sheet.append(["错误码", "严重度", "位置", "证据", "修复动作"])
    issue_rows = [
        [
            issue.get("error_code"),
            issue.get("severity"),
            issue.get("location"),
            issue.get("evidence"),
            issue.get("repair_action"),
        ]
        for issue in audit_report.get("issues", [])
    ]
    if not issue_rows:
        issue_rows = [["PASS", "info", "全表", "零问题", "无需修复"]]
    for row in issue_rows:
        sheet.append([_cell_value(value) for value in row])

    green = PatternFill("solid", fgColor="1F6F5F")
    for row_index in (1, 3, issue_header_row):
        for cell in sheet[row_index]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = green
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in sheet.iter_rows():
        for cell in row:
            if cell.row not in {1, 3}:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, width in enumerate((24, 28, 24, 48, 36), 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A3"
    return sheet


def storyboard_to_xlsx(
    storyboard: dict[str, Any],
    audit_report: dict[str, Any],
    *,
    assume_compact: bool = False,
) -> bytes:
    # Export is a one-way compact projection. Legacy atom sheets, batch sheets,
    # model-authored derived prose and Seedance prompt copies are never emitted.
    compact = storyboard if assume_compact else migrate_storyboard_to_compact(storyboard)
    workbook = Workbook()
    workbook.remove(workbook.active)
    _write_scope_audit_sheet(workbook, compact, audit_report)
    formal_sheet = _write_table(
        workbook,
        "正式生产母版",
        list(MOTHER_TEMPLATE_COLUMNS),
        formal_mother_template_rows(compact, assume_compact=True),
    )
    for index, width in enumerate((16, 9, 11, 23.5, 62, 60, 42, 20, 13, 46), 1):
        formal_sheet.column_dimensions[get_column_letter(index)].width = width

    _write_canonical_sidecar(workbook, compact)

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def preview_extracted(value: dict[str, Any], section: str | None, offset: int, limit: int) -> dict[str, Any]:
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), 200))
    kind = value.get("kind")
    if kind == "text":
        text = str(value.get("text") or "")
        return {"kind": kind, "offset": offset, "limit": limit, "total": len(text), "content": text[offset : offset + limit]}
    if kind == "workbook":
        if section == "canonical_storyboard":
            return {"kind": "json", "content": value.get("canonical_storyboard")}
        sheets = value.get("sheets", [])
        sheet = next((item for item in sheets if item.get("name") == section), None) if section else (sheets[0] if sheets else None)
        if not sheet:
            return {"kind": kind, "available_sections": [item.get("name") for item in sheets], "rows": []}
        rows = sheet.get("rows", [])
        return {
            "kind": kind,
            "section": sheet.get("name"),
            "headers": sheet.get("headers", []),
            "offset": offset,
            "limit": limit,
            "total": len(rows),
            "rows": rows[offset : offset + limit],
        }
    if kind == "table":
        rows = value.get("rows", [])
        return {"kind": kind, "headers": value.get("headers", []), "offset": offset, "limit": limit, "total": len(rows), "rows": rows[offset : offset + limit]}
    if kind == "json":
        content = value.get("value")
        if isinstance(content, dict):
            if not section:
                return {
                    "kind": kind,
                    "available_sections": sorted(content.keys()),
                    "summary": {
                        key: (len(item) if isinstance(item, (list, dict, str)) else item)
                        for key, item in content.items()
                    },
                }
            if section not in content:
                return {"kind": kind, "available_sections": sorted(content.keys()), "content": None}
            content = content[section]
        if isinstance(content, list):
            return {"kind": kind, "offset": offset, "limit": limit, "total": len(content), "content": content[offset : offset + limit]}
        if isinstance(content, dict):
            return {"kind": kind, "content": content}
        text = str(content or "")
        return {"kind": kind, "offset": offset, "limit": limit, "total": len(text), "content": text[offset : offset + limit]}
    return {"kind": kind, "content": value}


def _paragraphs_from_text(text: str) -> list[str]:
    blocks = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    if len(blocks) <= 1:
        lines = [item.strip() for item in text.splitlines() if item.strip()]
        if lines:
            return lines
    return blocks


def _range_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return dict(value)
    raise ValueError("bulk_ranges entries must be objects")


def _bulk_char_result(
    text: str,
    *,
    section: str | None,
    offset: int,
    limit: int | None,
    budget: int,
) -> tuple[dict[str, Any], int]:
    total = len(text)
    requested = limit if limit is not None else DEFAULT_BULK_CHARACTER_LIMIT
    take = min(max(0, requested), max(0, budget))
    content = text[offset : offset + take]
    next_offset = min(total, offset + len(content))
    result = {
        "section": section,
        "unit": "characters",
        "offset": offset,
        "limit": requested,
        "total": total,
        "content": content,
        "next_offset": next_offset,
        "complete": next_offset >= total,
    }
    return result, len(content)


def _bulk_text_items_result(
    items: list[Any],
    *,
    section: str | None,
    unit: str,
    offset: int,
    inner_offset: int,
    limit: int | None,
    budget: int,
) -> tuple[dict[str, Any], int]:
    total = len(items)
    requested = limit if limit is not None else DEFAULT_BULK_ITEM_LIMITS[unit]
    index = min(max(0, offset), total)
    first_inner = max(0, inner_offset)
    remaining = max(0, budget)
    parts: list[str] = []
    completed_items = 0
    partial = False
    next_inner_offset = 0

    while index < total and completed_items < requested and remaining > 0:
        item = str(items[index] or "")
        item_offset = first_inner if completed_items == 0 else 0
        item_offset = min(item_offset, len(item))
        segment = item[item_offset:]
        separator = "\n\n" if parts else ""
        required = len(separator) + len(segment)
        if required <= remaining:
            parts.append(separator + segment)
            remaining -= required
            index += 1
            completed_items += 1
            first_inner = 0
            continue
        separator_size = len(separator)
        available = max(0, remaining - separator_size)
        if available:
            parts.append(separator + segment[:available])
            next_inner_offset = item_offset + available
            remaining = 0
            partial = next_inner_offset < len(item)
            if not partial:
                index += 1
                completed_items += 1
                next_inner_offset = 0
        break

    content = "".join(parts)
    result = {
        "section": section,
        "unit": unit,
        "offset": offset,
        "inner_offset": inner_offset,
        "limit": requested,
        "total": total,
        "content": content,
        "next_offset": index,
        "next_inner_offset": next_inner_offset,
        "complete": index >= total and not partial,
        "partial_item": partial,
    }
    return result, len(content)


def _bulk_rows_result(
    rows: list[Any],
    *,
    section: str | None,
    offset: int,
    limit: int | None,
    budget: int,
) -> tuple[dict[str, Any], int]:
    total = len(rows)
    requested = limit if limit is not None else DEFAULT_BULK_ITEM_LIMITS["rows"]
    selected: list[Any] = []
    used = 0
    index = min(max(0, offset), total)
    oversized_item = False
    while index < total and len(selected) < requested:
        row = rows[index]
        row_size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        separator_size = 1 if selected else 0
        if used + separator_size + row_size > max(0, budget):
            oversized_item = not selected and row_size > max(0, budget)
            break
        selected.append(row)
        used += separator_size + row_size
        index += 1
    result = {
        "section": section,
        "unit": "rows",
        "offset": offset,
        "limit": requested,
        "total": total,
        "rows": selected,
        "next_offset": index,
        "complete": index >= total,
        "oversized_item": oversized_item,
    }
    if oversized_item:
        result["hint"] = "single row exceeds max_chars; retry this section with unit=characters"
    return result, used


def _workbook_sheet(value: dict[str, Any], section: str | None) -> dict[str, Any] | None:
    sheets = value.get("sheets", [])
    if section:
        return next((item for item in sheets if item.get("name") == section), None)
    return sheets[0] if sheets else None


def _bulk_one_range(
    value: dict[str, Any],
    request: dict[str, Any],
    *,
    default_unit: str,
    budget: int,
) -> tuple[dict[str, Any], int]:
    kind = str(value.get("kind") or "unknown")
    section = request.get("section")
    offset = max(0, int(request.get("offset", 0)))
    inner_offset = max(0, int(request.get("inner_offset", 0)))
    limit_value = request.get("limit")
    limit = None if limit_value is None else max(1, int(limit_value))
    requested_unit = str(request.get("unit") or default_unit or "auto")

    if kind == "text":
        text = str(value.get("text") or "")
        if requested_unit == "auto":
            if value.get("format") == "pdf" and isinstance(value.get("pages"), list):
                requested_unit = "pages"
            elif isinstance(value.get("paragraphs"), list):
                requested_unit = "paragraphs"
            else:
                requested_unit = "characters"
        if requested_unit == "characters":
            return _bulk_char_result(text, section=section, offset=offset, limit=limit, budget=budget)
        if requested_unit == "paragraphs":
            paragraphs = value.get("paragraphs")
            if not isinstance(paragraphs, list):
                paragraphs = _paragraphs_from_text(text)
            return _bulk_text_items_result(
                paragraphs,
                section=section,
                unit="paragraphs",
                offset=offset,
                inner_offset=inner_offset,
                limit=limit,
                budget=budget,
            )
        if requested_unit == "pages":
            pages = value.get("pages")
            if not isinstance(pages, list):
                raise ValueError("pages unit is only available for page-indexed text")
            return _bulk_text_items_result(
                pages,
                section=section,
                unit="pages",
                offset=offset,
                inner_offset=inner_offset,
                limit=limit,
                budget=budget,
            )
        raise ValueError(f"unit {requested_unit!r} is not valid for text")

    if kind == "workbook":
        if section == "canonical_storyboard":
            content = value.get("canonical_storyboard")
            text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if requested_unit not in {"auto", "characters"}:
                raise ValueError("canonical_storyboard supports unit=characters")
            return _bulk_char_result(text, section=section, offset=offset, limit=limit, budget=budget)
        sheet = _workbook_sheet(value, section)
        if not sheet:
            return (
                {
                    "section": section,
                    "unit": "rows",
                    "offset": offset,
                    "limit": limit or DEFAULT_BULK_ITEM_LIMITS["rows"],
                    "total": 0,
                    "rows": [],
                    "next_offset": 0,
                    "complete": True,
                    "available_sections": [item.get("name") for item in value.get("sheets", [])],
                },
                0,
            )
        rows = list(sheet.get("rows") or [])
        if requested_unit == "characters":
            text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            return _bulk_char_result(text, section=sheet.get("name"), offset=offset, limit=limit, budget=budget)
        if requested_unit not in {"auto", "rows"}:
            raise ValueError(f"unit {requested_unit!r} is not valid for a workbook sheet")
        result, used = _bulk_rows_result(
            rows,
            section=sheet.get("name"),
            offset=offset,
            limit=limit,
            budget=budget,
        )
        result["headers"] = sheet.get("headers", [])
        return result, used

    if kind == "table":
        rows = list(value.get("rows") or [])
        if requested_unit == "characters":
            text = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            return _bulk_char_result(text, section=section, offset=offset, limit=limit, budget=budget)
        if requested_unit not in {"auto", "rows"}:
            raise ValueError(f"unit {requested_unit!r} is not valid for a table")
        result, used = _bulk_rows_result(rows, section=section, offset=offset, limit=limit, budget=budget)
        result["headers"] = value.get("headers", [])
        return result, used

    if kind == "json":
        content = value.get("value")
        if isinstance(content, dict) and section:
            if section not in content:
                return (
                    {
                        "section": section,
                        "unit": requested_unit,
                        "offset": offset,
                        "limit": limit,
                        "total": 0,
                        "content": "",
                        "next_offset": 0,
                        "complete": True,
                        "available_sections": sorted(content.keys()),
                    },
                    0,
                )
            content = content[section]
        if isinstance(content, list) and requested_unit in {"auto", "rows"}:
            return _bulk_rows_result(content, section=section, offset=offset, limit=limit, budget=budget)
        if requested_unit not in {"auto", "characters"}:
            raise ValueError(f"unit {requested_unit!r} is not valid for this JSON value")
        text = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        return _bulk_char_result(text, section=section, offset=offset, limit=limit, budget=budget)

    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if requested_unit not in {"auto", "characters"}:
        raise ValueError(f"unit {requested_unit!r} is not valid for {kind}")
    return _bulk_char_result(text, section=section, offset=offset, limit=limit, budget=budget)


def preview_extracted_bulk(
    value: dict[str, Any],
    ranges: list[Any] | None,
    *,
    default_unit: str = "auto",
    max_chars: int = DEFAULT_BULK_RESPONSE_CHARS,
) -> dict[str, Any]:
    """Read several bounded ranges in one MCP round trip.

    `max_chars` limits returned source/table content across every requested
    range. Metadata is intentionally small and is not counted as source text.
    """

    budget = max(1, min(int(max_chars), MAX_BULK_RESPONSE_CHARS))
    raw_ranges = ranges or [{}]
    if len(raw_ranges) > MAX_BULK_RANGES:
        raise ValueError(f"bulk_ranges supports at most {MAX_BULK_RANGES} ranges per call")
    requests = [_range_dict(item) for item in raw_ranges]
    results: list[dict[str, Any]] = []
    used = 0
    for request in requests:
        remaining = max(0, budget - used)
        result, consumed = _bulk_one_range(value, request, default_unit=default_unit, budget=remaining)
        used += min(max(0, consumed), remaining)
        results.append(result)
    truncated = any(not bool(item.get("complete")) for item in results)
    return {
        "kind": value.get("kind"),
        "ranges": results,
        "max_chars": budget,
        "returned_chars": used,
        "truncated": truncated,
    }
