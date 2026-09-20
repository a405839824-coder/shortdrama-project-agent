"""Shared expectations for the two real MCP transport smoke tests."""
import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = json.loads(
    (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
)["version"]
EXPECTED_TOOL_NAMES = {
    "preflight_narrative_plan",
    "review_narrative_artifact",
    "record_narrative_attempt",
    "read_narrative_record",
    "manage_user_access",
    "get_agent_status",
    "migrate_project_state",
    "recover_storyboard_handoff",
    "query_project_issues",
    "mark_performance_stage",
    "create_project",
    "begin_project_task",
    "import_source_file",
    "read_project_artifact",
    "plan_change_request",
    "commit_project_artifact",
    "preflight_storyboard_draft",
    "commit_storyboard",
    "commit_storyboard_delta",
    "commit_storyboard_patch",
    "audit_storyboard",
    "complete_task_validation",
    "record_capability_gap",
    "signoff_task",
}


async def inspect_session(session) -> dict:
    init = await session.initialize()
    tools = await session.list_tools()
    status = await session.call_tool("get_agent_status", {"full_self_test": False})
    payload = status.structured_content or {}
    return {
        "server": init.server_info.model_dump(by_alias=True),
        "tool_count": len(tools.tools),
        "tool_names": [tool.name for tool in tools.tools],
        "status_call_is_error": bool(status.is_error),
        "agent_status": payload.get("status"),
        "runtime_status": payload.get("data", {}).get("runtime_status"),
    }


def inspection_passed(inspection: dict) -> bool:
    return (
        inspection["server"].get("version") == EXPECTED_VERSION
        and inspection["tool_count"] == len(EXPECTED_TOOL_NAMES)
        and len(inspection["tool_names"]) == len(EXPECTED_TOOL_NAMES)
        and set(inspection["tool_names"]) == EXPECTED_TOOL_NAMES
        and not inspection["status_call_is_error"]
        and inspection["agent_status"] == "pass"
        and inspection["runtime_status"] == "pass"
    )
