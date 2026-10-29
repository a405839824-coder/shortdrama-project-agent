"""Shared expectations for the two real MCP transport smoke tests."""
import json
import hashlib
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
        spec = {'style_id':'transport-synthetic-look','revision':'1',
            'render_mode':{'3d':'stylized_realism','2d':'line_art','live_action_look':'cinematic'}[variant],
            'character_design':'成年人物自然比例，五官轮廓保持一致','materials':'皮肤哑光，衣料细节清楚',
            'palette':'青灰主色，暖棕点缀','shading':'体块明暗清晰，阴影柔和'}
        prepared = await session.call_tool('get_agent_status', {'full_self_test': False,
            'prompt_library_id':library,'prompt_variant':variant,'prompt_visual_style':spec})
        card = (prepared.structured_content or {}).get('data',{}).get('selected_prompt_library',{}).get('visual_style')
        expected_card = {'contract':'project-visual-style-v1','library_id':library,'variant':variant,**spec}
        expected_card['content_sha256'] = hashlib.sha256(json.dumps(
            {'selection':chosen['selection'],'visual_style':expected_card},ensure_ascii=False,
            sort_keys=True,separators=(',',':')).encode()).hexdigest()
        library_reads[-1]['explicit_visual_card_prepared'] = not prepared.is_error and card == expected_card
    mixed = await session.call_tool('get_agent_status', {
        'prompt_library_id': 'animation', 'prompt_variant': 'live_action_look'})
    mixed_card = await session.call_tool('get_agent_status', {'prompt_library_id':'animation', 'prompt_variant':'3d',
        'prompt_visual_style':{**spec,'render_mode':'stylized_realism','materials':'采用真人实拍质感'}})
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
        "mixed_visual_card_rejected": bool(mixed_card.is_error),
        "library_arguments_exposed": {'prompt_library_id', 'prompt_variant', 'prompt_visual_style'} <= set(schema.get('properties', {})),
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
        and inspection['mixed_visual_card_rejected']
        and len(inspection['library_reads']) == 3
        and all(row['only_selected_profile'] and row['explicit_visual_card_prepared'] for row in inspection['library_reads'])
    )
