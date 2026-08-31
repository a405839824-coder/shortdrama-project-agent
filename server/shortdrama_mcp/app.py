from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import quote

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse

from . import __version__
from .auth import JwtTokenVerifier, request_namespace, security_schemes
from .canonical import etag_matches, stable_json_hash
from .config import Settings
from .file_io import DEFAULT_BULK_RESPONSE_CHARS, preview_extracted_bulk
from .models import (
    ArtifactReadRange,
    ChangeOperationInput,
    MAX_DELTA_ITEMS,
    OpenAIFile,
    ReadUnit,
    SemanticAuditInput,
    StoryboardDeltaEvidenceInput,
    StoryboardPatchOperationInput,
    ToolResponse,
)
from .service import StoryboardService
from .telemetry import WorkflowTrace


def _annotations(
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool | None = None,
    open_world: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=read_only if idempotent is None else idempotent,
        openWorldHint=open_world,
    )


def _window_session_id(ctx: Context) -> str | None:
    """Return a host-observed window id without trusting model arguments or headers.

    ChatGPT's Streamable HTTP calls may be stateless, so a transport connection
    id is not guaranteed to exist. ChatGPT instead injects the anonymized
    conversation id in the request `_meta["openai/session"]` envelope. Keep a
    transport-session fallback for stateful non-ChatGPT MCP clients.
    """

    try:
        meta = ctx.request_context.meta
        value = meta.get("openai/session") if meta else None
    except (AttributeError, TypeError, ValueError):
        value = None
    if isinstance(value, str):
        value = value.strip()
        if value and len(value) <= 512:
            return f"openai:{value}"

    try:
        direct = getattr(ctx, "session_id", None)
    except (AttributeError, ValueError):
        direct = None
    if direct:
        return f"mcp:{direct}"

    try:
        session = ctx.request_context.session
        connection = getattr(session, "connection", None) or getattr(
            session, "_connection", None
        )
        fallback = getattr(connection, "session_id", None)
    except (AttributeError, ValueError):
        fallback = None
    return f"mcp:{fallback}" if fallback else None


def create_mcp(settings: Settings) -> tuple[MCPServer, StoryboardService]:
    token_verifier = None
    auth_settings = None
    if settings.auth_mode == "oauth":
        token_verifier = JwtTokenVerifier(settings)
        auth_settings = AuthSettings(
            issuer_url=settings.oauth_issuer,
            resource_server_url=settings.public_base_url,
            service_documentation_url=f"{settings.public_base_url}/",
            required_scopes=list(settings.oauth_scopes),
        )
    server = MCPServer(
        name="shortdrama-project-agent",
        title="分镜项目 Agent",
        description="R10.1 compact-master short-drama service with the R10-SPC-1 strict prompt/export contract, canonical delta commits, independent audit, conditional repair, and public-scene ecology",
        version=__version__,
        instructions=(
            "先调用get_agent_status，再创建或确认项目并调用begin_project_task。"
            "小说章节转分镜必须先完整读取并建立章节地图、取舍台账、人物认知、对白保护、时长预算和节拍计划，未完成不得生成。"
            "生成内容后必须调用提交工具；commit_storyboard只签发生产预检和handoff，不能冒充独立审计。"
            "已有分镜只改受影响范围时使用commit_storyboard_delta：仅提交稳定shot_uid、精确左右边界和变化镜头；服务端合并未改内容并执行同一完整R10.1生产校验，随后交给独立审计。"
            "审计窗口必须调用audit_storyboard独立重算；相同canonical、原文件、源文、边界、规则和验证器才可复用。"
            "街道、市集、酒楼、城门等公共场景必须写普通人物密度、地点角色、错峰动作和空间层次，并写具体近中远环境声、对白避让和声画来源；有因空场除外。"
            "正式母版单镜必须在空白上下文中完整复现同一组公共人物、环境声生态、空间站位、起始状态和时间阶段。"
            "禁止后台生成或保存独立Seedance批次；正式母版画面内容就是单镜执行真源，GPT关键帧仅在导出时确定性编译。"
            "新写分镜必须遵守R10-SPC-1：EP##-###编号、单集累计重置、每镜恰好三个Seedance 2.5时间阶段，第五列表头为正式分镜母版。"
            "只有独立审计失败且用户明确授权后，独立repair窗口才可使用签名修复包；非结构字段可用commit_storyboard_patch局部修复，边界、认知、原文、编号和时长变化必须执行绑定修复包的完整重建。"
            "修复后仍执行完整生产校验并交回原独立审计窗口复核；最多三轮，只有原审计窗口确认零残留、零新增才可称通过。"
            "失效prepared生产handoff只能由受控恢复工具先预演后按精确哈希确认；旧记录保留为superseded，新记录仍待独立审计。"
            "正式归档必须同时具备精确版本的独立审计通过证书和signoff_task人工签核。"
            "只有用户要求排查原生ChatGPT耗时时才启用mark_performance_stage；按五个固定阶段记录"
            "服务端收件时间，不上传提示词、原文、对白或隐藏思考，不改变分镜质量字段。"
        ),
        token_verifier=token_verifier,
        auth=auth_settings,
    )
    service = StoryboardService(settings)
    schemes = security_schemes(settings)

    def meta(invoking: str, invoked: str, *, file_params: list[str] | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "securitySchemes": schemes,
            "openai/toolInvocation/invoking": invoking,
            "openai/toolInvocation/invoked": invoked,
        }
        if file_params:
            value["openai/fileParams"] = file_params
        return value

    def traced(response: ToolResponse, trace: WorkflowTrace) -> ToolResponse:
        response.trace_id = trace.trace_id
        response.data.setdefault("telemetry", trace.as_dict())
        return response

    @server.tool(
        name="get_agent_status",
        title="检查分镜 Agent 状态",
        description=(
            "在新聊天、继续旧项目或执行任务前调用；检查R10运行时、项目状态和跨窗口问题台账。"
            "full_self_test=true会执行完整自检并签发、持久化运行时测试证书，因此本工具不是只读工具。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在检查 Agent…", "Agent 状态已就绪"),
        structured_output=True,
    )
    async def get_agent_status(project_id: str | None = None, full_self_test: bool = False) -> ToolResponse:
        return service.agent_status(request_namespace(settings), project_id, full_self_test)

    @server.tool(
        name="migrate_project_state",
        title="迁移分镜项目控制状态",
        description=(
            "对旧项目Hub与问题台账执行可预演、签名快照、双哈希CAS和幂等恢复的0.5.5迁移。"
            "只更新控制状态与问题模式索引；绝不改写历史分镜、对白、canonical、XLSX、handoff或审计证书。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在核对项目迁移…", "项目迁移已处理"),
        structured_output=True,
    )
    async def migrate_project_state(
        project_id: str,
        work_order_id: str,
        ctx: Context,
        action: Literal["plan", "execute", "rollback"] = "plan",
        expected_current_version: str | None = None,
        target_version: str = "0.5.5",
        migration_id: str | None = None,
    ) -> ToolResponse:
        return service.migrate_project_state(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            action=action,
            target_version=target_version,
            expected_current_version=expected_current_version,
            migration_id=migration_id,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="recover_storyboard_handoff",
        title="受控恢复失效分镜交接",
        description=(
            "仅供production窗口在已登记能力缺口且用户明确授权后使用。先action=plan预演；"
            "execute必须回传精确plan_sha256和确认字面量。工具逐条使用签名日志与CAS，将失效的"
            "prepared production_to_audit handoff原子标记为superseded并按当前完整指纹重新签发；"
            "不删除历史、不改写分镜/canonical/XLSX、不签发独立审计结论，新handoff仍为PENDING_AUDIT。"
        ),
        annotations=_annotations(
            read_only=False,
            destructive=True,
            idempotent=True,
            open_world=False,
        ),
        meta=meta("正在核对交接恢复…", "交接恢复已处理"),
        structured_output=True,
    )
    async def recover_storyboard_handoff(
        project_id: str,
        work_order_id: str,
        gap_id: str,
        handoff_ids: Annotated[list[str], Field(min_length=1, max_length=32)],
        ctx: Context,
        action: Literal["plan", "execute"] = "plan",
        expected_plan_sha256: str | None = None,
        confirmation: str | None = None,
    ) -> ToolResponse:
        return service.recover_storyboard_handoff(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            gap_id=gap_id,
            handoff_ids=handoff_ids,
            action=action,
            expected_plan_sha256=expected_plan_sha256,
            confirmation=confirmation,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="query_project_issues",
        title="查询问题清单与主动防错画像",
        description=(
            "只读查询项目问题、跨批次复发模式和生成前必须扫描的主动防错画像；"
            "镜号、集数和文件名只作为发生位置，不再拆散同一模板根因。"
        ),
        annotations=_annotations(read_only=True),
        meta=meta("正在读取问题清单…", "问题清单已就绪"),
        structured_output=True,
    )
    async def query_project_issues(
        project_id: str,
        status: Literal["open", "resolved", "all"] = "all",
        error_codes: list[str] | None = None,
        episode_ids: list[str] | None = None,
        recurrence_states: list[str] | None = None,
        minimum_occurrence_count: Annotated[int, Field(ge=1)] = 1,
        include_history: bool = False,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        cursor: Annotated[int, Field(ge=0)] = 0,
    ) -> ToolResponse:
        return service.query_project_issues(
            request_namespace(settings),
            project_id=project_id,
            status=status,
            error_codes=error_codes,
            episode_ids=episode_ids,
            recurrence_states=recurrence_states,
            minimum_occurrence_count=minimum_occurrence_count,
            include_history=include_history,
            limit=limit,
            cursor=cursor,
        )

    @server.tool(
        name="mark_performance_stage",
        title="记录无API性能诊断阶段",
        description=(
            "仅在用户明确要求排查ChatGPT原生窗口耗时时调用。依次记录task_started、inputs_ready、"
            "plan_ready、canonical_ready、delivery_ready五个服务端收件检查点，并把已持久化插件trace"
            "与同一项目/角色关联，区分调用端区间和插件服务端时间。start返回diagnostic_run_id；"
            "中间使用mark；最后使用finish并返回归因报告。禁止提交原文、提示词、对白、分镜正文、"
            "隐藏思考或调用端自报时间；诊断不会生成Seedance批次，也不会删减逐镜字段。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在记录性能阶段…", "性能阶段已记录"),
        structured_output=True,
    )
    async def mark_performance_stage(
        project_id: str,
        work_order_id: str,
        action: Literal["start", "mark", "finish"],
        stage: Literal[
            "task_started",
            "inputs_ready",
            "plan_ready",
            "canonical_ready",
            "delivery_ready",
        ],
        ctx: Context,
        diagnostic_run_id: str | None = None,
        reasoning_mode: Literal[
            "light", "medium", "high", "extra_high", "max", "unknown"
        ] = "unknown",
        window_type: Literal["fresh", "continued", "unknown"] = "unknown",
        route: Literal[
            "full_rebuild",
            "episode_delta",
            "first_independent_audit",
            "repeat_audit",
            "audit_repair",
            "unknown",
        ] = "unknown",
        episode_count: Annotated[int, Field(ge=0, le=1_000)] = 0,
        expected_shot_count: Annotated[int, Field(ge=0, le=100_000)] = 0,
        observed_shot_count: Annotated[int, Field(ge=0, le=100_000)] = 0,
        observed_character_count: Annotated[int, Field(ge=0, le=100_000_000)] = 0,
        related_trace_ids: Annotated[list[str], Field(max_length=16)] | None = None,
        related_artifact_id: str | None = None,
    ) -> ToolResponse:
        return service.mark_performance_stage(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            action=action,
            stage=stage,
            diagnostic_run_id=diagnostic_run_id,
            reasoning_mode=reasoning_mode,
            window_type=window_type,
            route=route,
            episode_count=episode_count,
            expected_shot_count=expected_shot_count,
            observed_shot_count=observed_shot_count,
            observed_character_count=observed_character_count,
            related_trace_ids=related_trace_ids,
            related_artifact_id=related_artifact_id,
            request_session_id=_window_session_id(ctx),
        )


    @server.tool(
        name="create_project",
        title="创建短剧分镜项目",
        description="用户开始一个新短剧、剧本或分镜项目且尚无project_id时调用；建立跨聊天持久项目中枢和问题台账。",
        annotations=_annotations(read_only=False),
        meta=meta("正在创建项目…", "项目已创建"),
        structured_output=True,
    )
    async def create_project(project_name: str) -> ToolResponse:
        return service.create_project(request_namespace(settings), project_name)

    @server.tool(
        name="begin_project_task",
        title="建立执行工单",
        description="任何章节解析、策划、生成、改写、增删改、审计修复或生产任务开始时调用；锁定来源、范围、授权和预期交付。",
        annotations=_annotations(read_only=False),
        meta=meta("正在锁定任务范围…", "执行工单已建立"),
        structured_output=True,
    )
    async def begin_project_task(
        project_id: str,
        operation: str,
        mode: Literal["plan_only", "execute", "audit_only", "audit_and_repair"],
        authorization: Literal["read_only", "plan_only", "explicit_execute", "pending"],
        request_text: str,
        targets: list[str],
        authoritative_source: str,
        latest_version: str,
        target_boundary: str,
        must_keep: list[str],
        must_remove: list[str],
        protected_scope: list[str],
        expected_outputs: list[str],
        ctx: Context,
        window_key: str = "chat",
    ) -> ToolResponse:
        return service.begin_task(
            request_namespace(settings),
            project_id=project_id,
            operation=operation,
            mode=mode,
            authorization=authorization,
            request_text=request_text,
            targets=targets,
            authoritative_source=authoritative_source,
            latest_version=latest_version,
            target_boundary=target_boundary,
            must_keep=must_keep,
            must_remove=must_remove,
            protected_scope=protected_scope,
            expected_outputs=expected_outputs,
            window_key=window_key,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="import_source_file",
        title="导入项目文件",
        description=(
            "用户上传小说、剧本、分镜或规则文件后，由production窗口使用"
            "project_intake/source_lock/chapter_intake的execute+explicit_execute工单调用；"
            "服务端在下载或落盘前校验工单、角色和会话。audit/read_only窗口禁止导入或替换来源。"
            "安全下载、保存原件并提取xlsx/docx/pdf/json/md/txt/csv/mm内容。"
        ),
        annotations=_annotations(read_only=False, open_world=True),
        meta=meta("正在导入文件…", "文件已导入", file_params=["file"]),
        structured_output=True,
    )
    async def import_source_file(
        project_id: str,
        work_order_id: str,
        file: OpenAIFile,
        source_role: Literal["novel", "script", "storyboard", "rule", "reference", "asset"],
        ctx: Context,
    ) -> ToolResponse:
        return service.import_source_file(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            file=file,
            source_role=source_role,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="read_project_artifact",
        title="读取项目文件",
        description=(
            "读取已导入或已生成文件。默认自动批量读取长内容并返回无重叠续读位置；旧版"
            "section/offset/limit分页可用automatic_bulk=false保持兼容。也可使用"
            "bulk_ranges一次获取最多16个段落、页面、行或字符范围，合并内容硬限制为16000字符。"
            "可传if_none_match避免重复返回未变化文件。"
        ),
        annotations=_annotations(read_only=True),
        meta=meta("正在读取文件…", "文件内容已读取"),
        structured_output=True,
    )
    async def read_project_artifact(
        project_id: str,
        artifact_id: str,
        section: str | None = None,
        offset: int = 0,
        limit: int = 50,
        bulk_ranges: list[ArtifactReadRange] | None = None,
        unit: ReadUnit = "auto",
        max_chars: int = DEFAULT_BULK_RESPONSE_CHARS,
        if_none_match: str | None = None,
        automatic_bulk: bool = True,
    ) -> ToolResponse:
        namespace = request_namespace(settings)
        trace = WorkflowTrace()
        record: dict[str, Any] | None = None
        if if_none_match is not None:
            with trace.stage("artifact-lookup"):
                record, _path = service.store.get_artifact(namespace, project_id, artifact_id)
            etag = str(record.get("fingerprint") or "")
            if etag_matches(if_none_match, etag):
                metadata = record.get("metadata") or {}
                content_hash = str(
                    metadata.get("content_hash")
                    or metadata.get("canonical_sha256")
                    or etag
                )
                return traced(
                    ToolResponse(
                        status="not_modified",
                        summary=f"文件未变化｜{record['file_name']}",
                        project_id=project_id,
                        artifact_id=artifact_id,
                        data={
                            "artifact": record,
                            "etag": etag,
                            "content_hash": content_hash,
                            "not_modified": True,
                        },
                    ),
                    trace,
                )

        bulk_mode = bool(
            automatic_bulk
            or bulk_ranges is not None
            or unit != "auto"
            or max_chars != DEFAULT_BULK_RESPONSE_CHARS
        )
        if not bulk_mode:
            with trace.stage("legacy-artifact-read", offset=offset, limit=limit):
                response = service.read_artifact(
                    namespace,
                    project_id=project_id,
                    artifact_id=artifact_id,
                    section=section,
                    offset=offset,
                    limit=limit,
                )
            artifact = response.data.get("artifact") or record or {}
            etag = str(artifact.get("fingerprint") or "")
            metadata = artifact.get("metadata") or {}
            content_hash = str(
                metadata.get("content_hash")
                or metadata.get("canonical_sha256")
                or etag
            )
            response.data.setdefault("etag", etag)
            response.data.setdefault("content_hash", content_hash)
            response.data.setdefault("not_modified", False)
            response.data["read_mode"] = "legacy"
            return traced(response, trace)

        with trace.stage("artifact-load"):
            extracted, loaded_record, _path = service._load_extracted_or_payload(
                namespace, project_id, artifact_id
            )
        range_values = [item.model_dump(exclude_none=True) for item in (bulk_ranges or [])]
        if not range_values:
            range_values = [{"section": section, "offset": offset, "unit": unit}]
        with trace.stage("bulk-preview", range_count=len(range_values), unit=unit) as metrics:
            content = preview_extracted_bulk(
                extracted,
                range_values,
                default_unit=unit,
                max_chars=max_chars,
            )
            metrics["returned_chars"] = content.get("returned_chars", 0)
            metrics["truncated"] = content.get("truncated", False)
            metrics["kind"] = str(content.get("kind") or "")
        with trace.stage("content-hash"):
            extracted_content_hash = stable_json_hash(extracted)
        metadata = loaded_record.get("metadata") or {}
        content_hash = str(
            metadata.get("content_hash")
            or metadata.get("canonical_sha256")
            or loaded_record.get("fingerprint")
            or extracted_content_hash
        )
        download_url = service.store.signed_download_url(
            namespace, project_id, artifact_id, loaded_record["file_name"]
        )
        return traced(
            ToolResponse(
                status="ok",
                summary=f"已批量读取｜{loaded_record['file_name']}｜{content['returned_chars']}字符",
                project_id=project_id,
                artifact_id=artifact_id,
                download_url=download_url,
                data={
                    "artifact": loaded_record,
                    "content": content,
                    "etag": loaded_record.get("fingerprint"),
                    "content_hash": content_hash,
                    "extracted_content_hash": extracted_content_hash,
                    "not_modified": False,
                    "read_mode": "automatic_bulk" if automatic_bulk and bulk_ranges is None else "explicit_bulk",
                    "shot_identity_manifest": service.storyboard_identity_manifest(
                        namespace, project_id, loaded_record, extracted
                    ),
                },
            ),
            trace,
        )

    @server.tool(
        name="plan_change_request",
        title="登记剧本或分镜变更",
        description="执行新增、修改、删除、替换、合并、拆分或移动前调用；生成影响矩阵并确认是否具备明确执行授权。",
        annotations=_annotations(read_only=False),
        meta=meta("正在计算变更影响…", "变更单已建立"),
        structured_output=True,
    )
    async def plan_change_request(
        project_id: str,
        work_order_id: str,
        target_type: Literal["script", "storyboard", "script_and_storyboard"],
        target_file: str,
        source_version: str,
        operations: list[ChangeOperationInput],
        authorization: Literal["read_only", "plan_only", "explicit_execute", "pending"],
        ctx: Context,
    ) -> ToolResponse:
        return service.plan_change(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            target_type=target_type,
            target_file=target_file,
            source_version=source_version,
            operations=[item.model_dump() for item in operations],
            authorization=authorization,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="commit_project_artifact",
        title="提交剧本、方案或生产文件",
        description="ChatGPT完成章节解析、剧情方案、剧本、改写、对白、锚点、BGM、配音或其他非分镜生产文件后调用；正式分镜及导出投影必须使用专用分镜提交工具。保存新版本且不覆盖旧版。",
        annotations=_annotations(read_only=False),
        meta=meta("正在生成交付文件…", "交付文件已保存"),
        structured_output=True,
    )
    async def commit_project_artifact(
        project_id: str,
        work_order_id: str,
        artifact_type: str,
        output_format: Literal["markdown", "text", "json"],
        file_name: str,
        ctx: Context,
        content_text: str | None = None,
        structured_data: Any | None = None,
        source_artifact_id: str | None = None,
        change_summary: str = "",
    ) -> ToolResponse:
        return service.commit_artifact(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            artifact_type=artifact_type,
            output_format=output_format,
            file_name=file_name,
            content_text=content_text,
            structured_data=structured_data,
            source_artifact_id=source_artifact_id,
            change_summary=change_summary,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="preflight_storyboard_draft",
        title="分场或整批初稿预检",
        description=(
            "在正式提交前对scene、episode或full候选执行与提交一致的compact和R10-SPC-1门禁，"
            "计算唯一问题镜正确率、直接可用率、单集下限、已知复发和系统性重复。失败候选会写入"
            "生产学习记录，但绝不生成artifact、版本、XLSX、证书或handoff；只有full零问题预检"
            "可签发正式提交绑定。该结果不是独立审计结论。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在预检初稿…", "初稿预检已完成"),
        structured_output=True,
    )
    async def preflight_storyboard_draft(
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
        scope_kind: Literal["scene", "episode", "full"],
        scope_id: str,
        ctx: Context,
    ) -> ToolResponse:
        return service.preflight_storyboard_draft(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="commit_storyboard",
        title="提交并生产预检分镜",
        description=(
            "生成、重建或完整修复R10.1 compact分镜后调用；执行全部生产门禁，保存canonical JSON、Excel、"
            "production_preflight证书和审计交接包。结构性修复必须由repair窗口同时提交审计签发的"
            "repair_package_id和其精确base artifact；服务端单次消费修复包并把成功结果送回原独立"
            "审计窗口。生产预检不能替代独立审计。canonical只保存27个语义字段；正式母版和GPT"
            "关键帧在导出时编译，禁止保存或后台生成独立Seedance批次。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在保存并生产预检…", "分镜生产预检已完成"),
        structured_output=True,
    )
    async def commit_storyboard(
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
        ctx: Context,
        file_name: str = "正式分镜.xlsx",
        source_artifact_id: str | None = None,
        repair_package_id: str | None = None,
        draft_preflight_id: str | None = None,
    ) -> ToolResponse:
        return service.commit_storyboard(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            file_name=file_name,
            source_artifact_id=source_artifact_id,
            repair_package_id=repair_package_id,
            draft_preflight_id=draft_preflight_id,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="commit_storyboard_delta",
        title="增量提交并全表生产预检分镜",
        description=(
            "已有正式分镜仅重建工单授权的连续镜头范围时调用。必须绑定基础canonical哈希、"
            "原始文件指纹、稳定shot_uid及精确左右邻镜；仅提交27字段compact替换镜头和必要"
            "流程证据。服务端无损保留未影响语义，只重算累计时长，随后执行与全量提交相同的"
            "R10.1全表验证、JSON/XLSX导出和审计交接。旧replacement_batches参数可省略；即使"
            "旧调用方仍传入也只会被丢弃，绝不生成、保存或映射独立Seedance批次。"
            "替换镜头必须保持原shot_uid、镜头编号、episode和scene身份；插删、跨集跨场景、"
            "源文、心理音轨、认知、对白文字、引号对白、说话人或全局映射变化必须走全量路线。"
            "无对白的可见动作、表演或场景状态变化可增量，并会同步"
            "更新受影响镜头的shot_plan、performance_chains和scene_state_ledger证据，未影响证据保持哈希一致。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在合并compact受影响分镜…", "增量分镜生产预检已完成"),
        structured_output=True,
    )
    async def commit_storyboard_delta(
        project_id: str,
        work_order_id: str,
        base_artifact_id: str,
        expected_base_canonical_sha256: str,
        expected_base_raw_fingerprint: str,
        scope_target: str,
        affected_shot_uids: Annotated[list[str], Field(min_length=1, max_length=MAX_DELTA_ITEMS)],
        replacement_shots: Annotated[
            list[dict[str, Any]], Field(min_length=1, max_length=MAX_DELTA_ITEMS)
        ],
        ctx: Context,
        replacement_batches: Annotated[
            list[dict[str, Any]], Field(max_length=MAX_DELTA_ITEMS)
        ] | None = None,
        left_boundary_shot_uid: str | None = None,
        right_boundary_shot_uid: str | None = None,
        evidence_updates: StoryboardDeltaEvidenceInput | None = None,
        file_name: str = "正式分镜_增量生产版.xlsx",
    ) -> ToolResponse:
        return service.commit_storyboard_delta(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            base_artifact_id=base_artifact_id,
            expected_base_canonical_sha256=expected_base_canonical_sha256,
            expected_base_raw_fingerprint=expected_base_raw_fingerprint,
            scope_target=scope_target,
            affected_shot_uids=affected_shot_uids,
            left_boundary_shot_uid=left_boundary_shot_uid,
            right_boundary_shot_uid=right_boundary_shot_uid,
            replacement_shots=replacement_shots,
            replacement_batches=replacement_batches,
            evidence_updates=(
                evidence_updates.model_dump(mode="python")
                if evidence_updates is not None
                else None
            ),
            top_level_updates=None,
            file_name=file_name,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="commit_storyboard_patch",
        title="提交审计确认的分镜局部修复",
        description=(
            "仅用于修复独立审计证书已经确认的非结构字段问题；必须绑定未过期的基础分镜规范哈希，"
            "逐镜提供问题证据和影响范围。禁止修改镜头编号、时长、场景连续性、章节边界、人物认知、"
            "原文支撑或其他结构字段；这类变化必须重建完整分镜。补丁后仍执行完整R10全表校验，"
            "保存新的JSON与Excel版本且绝不覆盖基础版本。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在应用审计修复…", "局部修复与完整生产校验已完成，待原审计复核"),
        structured_output=True,
    )
    async def commit_storyboard_patch(
        project_id: str,
        work_order_id: str,
        base_artifact_id: str,
        expected_canonical_hash: str,
        audit_certificate_id: str,
        operations: list[StoryboardPatchOperationInput],
        impact_scope: list[str],
        ctx: Context,
        expected_raw_artifact_fingerprint: str | None = None,
        file_name: str = "正式分镜_局部修复版.xlsx",
    ) -> ToolResponse:
        return service.commit_storyboard_patch(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            base_artifact_id=base_artifact_id,
            expected_canonical_hash=expected_canonical_hash,
            expected_raw_artifact_fingerprint=expected_raw_artifact_fingerprint,
            audit_certificate_id=audit_certificate_id,
            operations=[item.model_dump() for item in operations],
            impact_scope=impact_scope,
            file_name=file_name,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="audit_storyboard",
        title="审计并记录分镜",
        description=(
            "独立审计已存在的分镜文件时调用。首次或指纹变化后的审计必须提交semantic_audit："
            "绑定准确canonical哈希和全表镜头数，逐一提交恰好八层的结论、摘要、具体证据ID"
            "及语义问题，reviewed_batch_count固定为0且禁止batch证据/目标；第六层审计正式母版"
            "单镜执行完整性。服务端再合并完整机器校验问题。只有完全匹配且已签名的独立审计证书"
            "缓存命中时才可省略semantic_audit。同一安全缓存键重复审计不增加轮次。"
        ),
        annotations=_annotations(read_only=False),
        meta=meta("正在全表审计…", "审计结果已记录"),
        structured_output=True,
    )
    async def audit_storyboard(
        project_id: str,
        artifact_id: str,
        ctx: Context,
        audit_cycle_id: str | None = None,
        work_order_id: str | None = None,
        handoff_artifact_id: str | None = None,
        force_machine_recheck: bool = False,
        semantic_audit: SemanticAuditInput | None = None,
    ) -> ToolResponse:
        return service.audit_storyboard(
            request_namespace(settings),
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=audit_cycle_id,
            work_order_id=work_order_id,
            handoff_artifact_id=handoff_artifact_id,
            force_machine_recheck=force_machine_recheck,
            semantic_audit=(
                semantic_audit.model_dump(mode="python")
                if semantic_audit is not None
                else None
            ),
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="complete_task_validation",
        title="完成任务全链路验证",
        description="非分镜交付保存后调用；提交所有必需门禁结果。只有全部pass且残留0、新增0、阻塞0时工单才通过。",
        annotations=_annotations(read_only=False),
        meta=meta("正在执行全链路验证…", "验证已完成"),
        structured_output=True,
    )
    async def complete_task_validation(
        project_id: str,
        work_order_id: str,
        gate_results: dict[str, Literal["pass", "fail"]],
        residual_issue_count: int,
        introduced_issue_count: int,
        blockers: list[str],
        ctx: Context,
    ) -> ToolResponse:
        return service.complete_validation(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            gate_results=gate_results,
            residual_issue_count=residual_issue_count,
            introduced_issue_count=introduced_issue_count,
            blockers=blockers,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="record_capability_gap",
        title="登记缺失能力",
        description="当前工具、权限或依赖无法真实完成用户请求时调用；必须使用当前窗口绑定的capability_gap方案工单，记录安全替代、实现方案与测试门禁，禁止假装已执行。",
        annotations=_annotations(read_only=False),
        meta=meta("正在登记能力缺口…", "能力缺口已记录"),
        structured_output=True,
    )
    async def record_capability_gap(
        project_id: str,
        work_order_id: str,
        request: str,
        missing_dependency: str,
        risk: str,
        safe_fallback: str,
        proposed_implementation: str,
        required_tests: list[str],
        ctx: Context,
    ) -> ToolResponse:
        return service.record_capability_gap(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            request=request,
            missing_dependency=missing_dependency,
            risk=risk,
            safe_fallback=safe_fallback,
            proposed_implementation=proposed_implementation,
            required_tests=required_tests,
            request_session_id=_window_session_id(ctx),
        )

    @server.tool(
        name="signoff_task",
        title="人工签核或重开任务",
        description="用户明确批准正式归档或要求重开已通过任务时调用；不得代表用户自动签名。",
        annotations=_annotations(read_only=False),
        meta=meta("正在记录人工签核…", "签核结果已记录"),
        structured_output=True,
    )
    async def signoff_task(
        project_id: str,
        work_order_id: str,
        decision: Literal["approve", "reopen"],
        signer: str,
        ctx: Context,
    ) -> ToolResponse:
        return service.signoff_task(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            decision=decision,
            signer=signer,
            request_session_id=_window_session_id(ctx),
        )

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": service.bootstrap.get("status"),
                "service": "shortdrama-project-agent",
                "version": __version__,
                "mcp": "/mcp",
            }
        )

    @server.custom_route("/files/{token}/{filename}", methods=["GET"], include_in_schema=False)
    async def download(request: Request) -> StreamingResponse | PlainTextResponse:
        try:
            record, stream, size = service.store.open_verified_download(
                request.path_params["token"]
            )
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=404)

        def verified_chunks():
            try:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                stream.close()

        try:
            return StreamingResponse(
                verified_chunks(),
                media_type="application/octet-stream",
                headers={
                    "Cache-Control": "private, no-store",
                    "Content-Length": str(size),
                    "Content-Disposition": (
                        "attachment; filename*=utf-8''"
                        + quote(str(record["file_name"]))
                    ),
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except (KeyError, TypeError, ValueError):
            stream.close()
            return PlainTextResponse("artifact is unavailable", status_code=404)

    @server.custom_route("/", methods=["GET"], include_in_schema=False)
    async def index(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "name": "分镜项目 Agent",
                "version": __version__,
                "mcp_endpoint": f"{settings.public_base_url}/mcp",
                "health": f"{settings.public_base_url}/health",
                "authentication": settings.auth_mode,
            }
        )

    return server, service
