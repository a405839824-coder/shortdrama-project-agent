from __future__ import annotations

from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

from . import __version__
from .auth import JwtTokenVerifier, request_namespace, security_schemes
from .config import Settings
from .models import ChangeOperationInput, OpenAIFile, ToolResponse
from .service import StoryboardService


def _annotations(*, read_only: bool, destructive: bool = False, open_world: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=read_only,
        openWorldHint=open_world,
    )


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
        description="R10-PG1 Chinese short-drama project service with chapter intake and Seedance 2.5 execution",
        version=__version__,
        instructions=(
            "先调用get_agent_status，再创建或确认项目并调用begin_project_task。"
            "小说章节转分镜必须先完整读取并建立章节地图、取舍台账、人物认知、对白保护、时长预算和节拍计划，未完成不得生成。"
            "生成内容后必须调用提交工具；分镜必须经commit_storyboard全表校验。"
            "正式母版执行列固定为Seedance 2.5独立正式执行分镜。"
            "若失败，应一次修复报告中的全部问题并重提，最多三轮；零残留、零新增才可称通过。"
            "同一文件不要重复制造审计轮次。正式归档必须调用signoff_task取得人工签核。"
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
        description="读取已导入或已生成文件的内容、工作表或分页片段；先读取元数据，再按section和offset继续。",
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
    ) -> ToolResponse:
        return service.read_artifact(
            request_namespace(settings),
            project_id=project_id,
            artifact_id=artifact_id,
            section=section,
            offset=offset,
            limit=limit,
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
        title="提交并全表审计分镜",
        description="生成、重建或修复完整R10分镜后调用；对小说输入同时校验章节前置规划与100%覆盖，保存JSON与Excel，自动全表审计、影响回归并写入跨窗口台账。正式执行列使用Seedance 2.5。不要只提交局部问题。",
        annotations=_annotations(read_only=False),
        meta=meta("正在保存并全表审计…", "分镜审计已完成"),
        structured_output=True,
    )
    async def commit_storyboard(
        project_id: str,
        work_order_id: str,
        storyboard: dict[str, Any],
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
        )

    @server.tool(
        name="audit_storyboard",
        title="审计并记录分镜",
        description="审计已存在的分镜文件时调用；执行八层全表检查并记录问题。同一指纹重复审计会复用结果，不增加轮次。",
        annotations=_annotations(read_only=False),
        meta=meta("正在全表审计…", "审计结果已记录"),
        structured_output=True,
    )
    async def audit_storyboard(
        project_id: str,
        artifact_id: str,
        audit_cycle_id: str | None = None,
    ) -> ToolResponse:
        return service.audit_storyboard(
            request_namespace(settings),
            project_id=project_id,
            artifact_id=artifact_id,
            audit_cycle_id=audit_cycle_id,
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
