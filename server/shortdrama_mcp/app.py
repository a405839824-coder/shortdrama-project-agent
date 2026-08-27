from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

from . import __version__
from .auth import JwtTokenVerifier, request_namespace, security_schemes
from .canonical import etag_matches, stable_json_hash
from .config import Settings
from .file_io import DEFAULT_BULK_RESPONSE_CHARS, preview_extracted_bulk
from .models import (
    ArtifactReadRange,
    ChangeOperationInput,
    OpenAIFile,
    ReadUnit,
    StoryboardPatchOperationInput,
    ToolResponse,
)
from .service import StoryboardService
from .telemetry import WorkflowTrace


def _annotations(*, read_only: bool, destructive: bool = False, open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=read_only,
        openWorldHint=open_world,
    )


def _transport_session_id(ctx: Context) -> str | None:
    """Read the server-owned MCP connection id without trusting HTTP headers."""

    try:
        connection = getattr(ctx.request_context.session, "_connection", None)
        value = getattr(connection, "session_id", None)
    except (AttributeError, ValueError):
        return None
    return str(value) if value else None


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
        description="R10-PG1 short-drama service with canonical handoff, independent audit, safe local repair, public-scene ecology, and Seedance 2.5",
        version=__version__,
        instructions=(
            "先调用get_agent_status，再创建或确认项目并调用begin_project_task。"
            "小说章节转分镜必须先完整读取并建立章节地图、取舍台账、人物认知、对白保护、时长预算和节拍计划，未完成不得生成。"
            "生成内容后必须调用提交工具；commit_storyboard只签发生产预检和handoff，不能冒充独立审计。"
            "审计窗口必须调用audit_storyboard独立重算；相同canonical、原文件、源文、边界、规则和验证器才可复用。"
            "街道、市集、酒楼、城门等公共场景必须写普通人物密度、地点角色、错峰动作和空间层次，并写具体近中远环境声、对白避让和声画来源；有因空场除外。"
            "正式分镜与Seedance批次必须复现同一组公共人物和环境声生态。"
            "正式母版执行列固定为Seedance 2.5独立正式执行分镜。"
            "非结构字段可用commit_storyboard_patch按独立审计证书局部修复，但仍全表终验；边界、认知、原文、编号和时长变化必须重建。"
            "若失败，应一次修复报告中的全部问题并重提，最多三轮；零残留、零新增才可称通过。"
            "正式归档必须同时具备精确版本的独立审计通过证书和signoff_task人工签核。"
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
        description="在新聊天、继续旧项目或执行任务前调用；检查R10运行时、项目状态和跨窗口问题台账。",
        annotations=_annotations(read_only=True),
        meta=meta("正在检查 Agent…", "Agent 状态已就绪"),
        structured_output=True,
    )
    async def get_agent_status(project_id: str | None = None, full_self_test: bool = False) -> ToolResponse:
        return service.agent_status(request_namespace(settings), project_id, full_self_test)

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
            request_session_id=_transport_session_id(ctx),
        )

    @server.tool(
        name="import_source_file",
        title="导入项目文件",
        description="用户上传小说、剧本、分镜或规则文件后调用；安全下载、保存原件并提取xlsx/docx/pdf/json/md/txt/csv/mm内容。",
        annotations=_annotations(read_only=False, open_world=True),
        meta=meta("正在导入文件…", "文件已导入", file_params=["file"]),
        structured_output=True,
    )
    async def import_source_file(
        project_id: str,
        file: OpenAIFile,
        source_role: Literal["novel", "script", "storyboard", "rule", "reference", "asset"],
    ) -> ToolResponse:
        return service.import_source_file(
            request_namespace(settings), project_id=project_id, file=file, source_role=source_role
        )

    @server.tool(
        name="read_project_artifact",
        title="读取项目文件",
        description=(
            "读取已导入或已生成文件。旧版section/offset/limit分页保持兼容；读取长章节时优先使用"
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

        bulk_mode = bulk_ranges is not None or unit != "auto" or max_chars != DEFAULT_BULK_RESPONSE_CHARS
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
        )

    @server.tool(
        name="commit_project_artifact",
        title="提交剧本、方案或生产文件",
        description="ChatGPT完成章节解析、剧情方案、剧本、改写、对白、锚点、关键帧提示词、Seedance 2.5、BGM、配音或生产包后调用；保存新版本且不覆盖旧版。",
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
        )

    @server.tool(
        name="commit_storyboard",
        title="提交并生产预检分镜",
        description="生成、重建或完整修复R10分镜后调用；执行全部生产门禁，保存canonical JSON、Excel、production_preflight证书和审计交接包。生产预检不能替代独立审计。正式执行列使用Seedance 2.5。",
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
    ) -> ToolResponse:
        return service.commit_storyboard(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            storyboard=storyboard,
            file_name=file_name,
            source_artifact_id=source_artifact_id,
            request_session_id=_transport_session_id(ctx),
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
        meta=meta("正在应用审计修复…", "局部修复与全表终验已完成"),
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
            request_session_id=_transport_session_id(ctx),
        )

    @server.tool(
        name="audit_storyboard",
        title="审计并记录分镜",
        description="审计已存在的分镜文件时调用；执行全表检查（含公共人物、环境音、声画来源和Seedance复现）并记录问题。同一指纹重复审计会复用结果，不增加轮次。",
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
    ) -> ToolResponse:
        return service.audit_storyboard(
            request_namespace(settings),
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=audit_cycle_id,
            work_order_id=work_order_id,
            handoff_artifact_id=handoff_artifact_id,
            force_machine_recheck=force_machine_recheck,
            request_session_id=_transport_session_id(ctx),
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
    ) -> ToolResponse:
        return service.complete_validation(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            gate_results=gate_results,
            residual_issue_count=residual_issue_count,
            introduced_issue_count=introduced_issue_count,
            blockers=blockers,
        )

    @server.tool(
        name="record_capability_gap",
        title="登记缺失能力",
        description="当前工具、权限或依赖无法真实完成用户请求时调用；记录安全替代、实现方案与测试门禁，禁止假装已执行。",
        annotations=_annotations(read_only=False),
        meta=meta("正在登记能力缺口…", "能力缺口已记录"),
        structured_output=True,
    )
    async def record_capability_gap(
        project_id: str,
        request: str,
        missing_dependency: str,
        risk: str,
        safe_fallback: str,
        proposed_implementation: str,
        required_tests: list[str],
    ) -> ToolResponse:
        return service.record_capability_gap(
            request_namespace(settings),
            project_id=project_id,
            request=request,
            missing_dependency=missing_dependency,
            risk=risk,
            safe_fallback=safe_fallback,
            proposed_implementation=proposed_implementation,
            required_tests=required_tests,
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
    ) -> ToolResponse:
        return service.signoff_task(
            request_namespace(settings),
            project_id=project_id,
            work_order_id=work_order_id,
            decision=decision,
            signer=signer,
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
    async def download(request: Request) -> FileResponse | PlainTextResponse:
        try:
            record, path = service.store.resolve_download_token(request.path_params["token"])
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=404)
        return FileResponse(
            path,
            filename=record["file_name"],
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

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
