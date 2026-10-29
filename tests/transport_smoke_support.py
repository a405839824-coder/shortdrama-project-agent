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
    library_reads = []
    for library, variant in [('animation', '3d'), ('animation', '2d'), ('photorealistic', 'live_action_look')]:
        result = await session.call_tool('get_agent_status', {
            'full_self_test': False, 'prompt_library_id': library, 'prompt_variant': variant})
        chosen = (result.structured_content or {}).get('data', {}).get('selected_prompt_library', {})
        expected = json.loads((PLUGIN_ROOT / 'skills/shortdrama-storyboard/references' /
                               f'prompt_library_{library}.json').read_text(encoding='utf-8'))
        library_reads.append({'library_id': library, 'variant': variant,
            'only_selected_profile': not result.is_error and
                set(chosen) == {'selection', 'common', 'library_id', 'label', 'profile'} and
                chosen['library_id'] == library and chosen['selection']['variant'] == variant and
                chosen['profile'] == expected['variants'][variant]})
    mixed = await session.call_tool('get_agent_status', {
        'prompt_library_id': 'animation', 'prompt_variant': 'live_action_look'})
    schema = next(tool.input_schema for tool in tools.tools if tool.name == 'get_agent_status')
    return {
        "server": init.server_info.model_dump(by_alias=True),
        "tool_count": len(tools.tools),
        "tool_names": [tool.name for tool in tools.tools],
        "status_call_is_error": bool(status.is_error),
        "agent_status": payload.get("status"),
        "runtime_status": payload.get("data", {}).get("runtime_status"),
        "library_reads": library_reads,
        "mixed_library_request_rejected": bool(mixed.is_error),
        "library_arguments_exposed": {'prompt_library_id', 'prompt_variant'} <= set(schema.get('properties', {})),
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
        and inspection['library_arguments_exposed']
        and inspection['mixed_library_request_rejected']
        and len(inspection['library_reads']) == 3
        and all(row['only_selected_profile'] for row in inspection['library_reads'])
    )
