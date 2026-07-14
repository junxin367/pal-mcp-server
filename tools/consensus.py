"""Parallel blinded multi-model consensus with synchronous-first background fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from pydantic import Field, model_validator

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from mcp.types import TextContent

from config import (
    CONSENSUS_BACKGROUND_WAIT_SECONDS,
    CONSENSUS_MAX_CONCURRENCY,
    CONSENSUS_SYNC_WAIT_SECONDS,
    TEMPERATURE_ANALYTICAL,
)
from systemprompts import CONSENSUS_PROMPT
from tools.shared.base_models import WorkflowRequest
from utils.consensus_tasks import get_consensus_task_manager
from utils.conversation_memory import MAX_CONVERSATION_TURNS, add_turn, create_thread, get_thread

from .workflow.base import WorkflowTool

logger = logging.getLogger(__name__)
_PROVIDER_INITIALIZATION_LOCK = threading.Lock()

# Tool-specific field descriptions for consensus workflow
CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS = {
    "step": (
        "填写所有模型都要独立评估的原始提案或问题。"
        "全部模型会在第 1 步并行执行，请直接描述待评估内容，不要填写流程说明。"
    ),
    "step_number": "填写 1。并行 Consensus 会在首次调用中咨询全部指定模型。",
    "total_steps": "填写 1。工具会在一次调用中完成全部模型咨询。",
    "next_step_required": "填写 false。工具会一起返回全部模型响应，供调用方最终综合。",
    "findings": "调用方自己的独立分析，仅用于后续综合，不会发送给被咨询模型。",
    "relevant_files": "可选的分析参考文件，必须使用完整、未缩写的绝对路径。",
    "models": (
        "要咨询的模型列表（至少提供两个条目）。每个条目可包含 model、"
        "stance（for/against/neutral）和 stance_prompt。每组 (model, stance) 必须唯一，"
        "例如 [{'model':'gpt5','stance':'for'}, {'model':'pro','stance':'against'}]。"
    ),
    "current_model_index": "兼容旧调用的内部字段；并行 Consensus 会自行管理模型索引。",
    "model_responses": "兼容旧调用的内部字段；并行 Consensus 会统一返回全部响应。",
    "images": "可选的绝对图片路径或 base64 引用，用于补充视觉上下文。",
}


class ConsensusRequest(WorkflowRequest):
    """Request model for consensus workflow steps"""

    # Required fields for each step
    step: str = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step"])
    step_number: int = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step_number"])
    total_steps: int = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["total_steps"])
    next_step_required: bool = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["next_step_required"])

    # Investigation tracking fields
    findings: str = Field(..., description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["findings"])
    confidence: str = Field(default="exploring", exclude=True, description="Not used")

    # Consensus-specific fields (only needed in step 1)
    models: list[dict] | None = Field(None, description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["models"])
    relevant_files: list[str] | None = Field(
        default_factory=list,
        description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["relevant_files"],
    )

    # Internal tracking fields
    current_model_index: int | None = Field(
        0,
        description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["current_model_index"],
    )
    model_responses: list[dict] | None = Field(
        default_factory=list,
        description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["model_responses"],
    )

    # Optional images for visual debugging
    images: list[str] | None = Field(default=None, description=CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["images"])

    # Override inherited fields to exclude them from schema
    temperature: float | None = Field(default=None, exclude=True)
    thinking_mode: str | None = Field(default=None, exclude=True)

    # Not used in consensus workflow
    files_checked: list[str] | None = Field(default_factory=list, exclude=True)
    relevant_context: list[str] | None = Field(default_factory=list, exclude=True)
    issues_found: list[dict] | None = Field(default_factory=list, exclude=True)
    hypothesis: str | None = Field(None, exclude=True)

    @model_validator(mode="after")
    def validate_step_one_requirements(self):
        """Ensure step 1 has required models field and unique model+stance combinations."""
        if self.step_number == 1:
            if not self.models:
                raise ValueError("第 1 步必须通过 'models' 字段指定要咨询的模型")
            if len(self.models) < 2:
                raise ValueError("第 1 步至少需要指定 2 个模型")

            # Check for unique model + stance combinations
            seen_combinations = set()
            for model_config in self.models:
                model_name = model_config.get("model", "")
                stance = model_config.get("stance", "neutral")
                combination = f"{model_name}:{stance}"

                if combination in seen_combinations:
                    raise ValueError(
                        f"发现重复的 model + stance 组合：{model_name}，stance 为 '{stance}'。"
                        "每组 model + stance 必须唯一。"
                    )
                seen_combinations.add(combination)

        return self


class ConsensusTool(WorkflowTool):
    """Consult independent models in parallel and return one synthesis-ready result."""

    def get_name(self) -> str:
        return "consensus"

    def get_description(self) -> str:
        return (
            "通过系统分析和结构化讨论形成多模型共识，适用于复杂决策、架构选型、功能提案和技术评估。"
            "工具会按不同立场并行咨询相互独立的模型，并统一返回全部观点。"
        )

    def get_system_prompt(self) -> str:
        # For the CLI agent's initial analysis, use a neutral version of the consensus prompt
        return CONSENSUS_PROMPT.replace(
            "{stance_prompt}",
            """BALANCED PERSPECTIVE

Provide objective analysis considering both positive and negative aspects. However, if there is overwhelming evidence
that the proposal clearly leans toward being exceptionally good or particularly problematic, you MUST accurately
reflect this reality. Being "balanced" means being truthful about the weight of evidence, not artificially creating
50/50 splits when the reality is 90/10.

Your analysis should:
- Present all significant pros and cons discovered
- Weight them according to actual impact and likelihood
- If evidence strongly favors one conclusion, clearly state this
- Provide proportional coverage based on the strength of arguments
- Help the questioner see the true balance of considerations

Remember: Artificial balance that misrepresents reality is not helpful. True balance means accurate representation
of the evidence, even when it strongly points in one direction.""",
        )

    def get_default_temperature(self) -> float:
        return TEMPERATURE_ANALYTICAL

    def get_model_category(self) -> ToolModelCategory:
        """Consensus workflow requires extended reasoning"""
        from tools.models import ToolModelCategory

        return ToolModelCategory.EXTENDED_REASONING

    def get_workflow_request_model(self):
        """Return the consensus workflow-specific request model."""
        return ConsensusRequest

    def get_input_schema(self) -> dict[str, Any]:
        """Generate input schema for consensus workflow."""
        from .workflow.schema_builders import WorkflowSchemaBuilder

        # Consensus tool-specific field definitions
        consensus_field_overrides = {
            # Override standard workflow fields that need consensus-specific descriptions
            "step": {
                "type": "string",
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step"],
            },
            "step_number": {
                "type": "integer",
                "minimum": 1,
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["step_number"],
            },
            "total_steps": {
                "type": "integer",
                "minimum": 1,
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["total_steps"],
            },
            "next_step_required": {
                "type": "boolean",
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["next_step_required"],
            },
            "findings": {
                "type": "string",
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["findings"],
            },
            "relevant_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["relevant_files"],
            },
            # consensus-specific fields (not in base workflow)
            "models": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "stance": {"type": "string", "enum": ["for", "against", "neutral"], "default": "neutral"},
                        "stance_prompt": {"type": "string"},
                    },
                    "required": ["model"],
                },
                "description": (
                    "User-specified roster of models to consult (provide at least two entries). "
                    + CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["models"]
                ),
                "minItems": 2,
            },
            "current_model_index": {
                "type": "integer",
                "minimum": 0,
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["current_model_index"],
            },
            "model_responses": {
                "type": "array",
                "items": {"type": "object"},
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["model_responses"],
            },
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": CONSENSUS_WORKFLOW_FIELD_DESCRIPTIONS["images"],
            },
        }

        # Provide guidance on available models similar to single-model tools
        model_description = (
            "When the user names a model, you MUST use that exact value or report the "
            "provider error—never swap in another option. Use the `listmodels` tool for the full roster."
        )

        summaries, total, restricted = self._get_ranked_model_summaries()
        remainder = max(0, total - len(summaries))
        if summaries:
            label = "Allowed models" if restricted else "Top models"
            top_line = "; ".join(summaries)
            if remainder > 0:
                top_line = f"{label}: {top_line}; +{remainder} more via `listmodels`."
            else:
                top_line = f"{label}: {top_line}."
            model_description = f"{model_description} {top_line}"
        else:
            model_description = (
                f"{model_description} No models detected—configure provider credentials or use the `listmodels` tool "
                "to inspect availability."
            )

        restriction_note = self._get_restriction_note()
        if restriction_note and (remainder > 0 or not summaries):
            model_description = f"{model_description} {restriction_note}."

        existing_models_desc = consensus_field_overrides["models"]["description"]
        consensus_field_overrides["models"]["description"] = f"{existing_models_desc} {model_description}"

        # Define excluded fields for consensus workflow
        excluded_workflow_fields = [
            "files_checked",  # Not used in consensus workflow
            "relevant_context",  # Not used in consensus workflow
            "issues_found",  # Not used in consensus workflow
            "hypothesis",  # Not used in consensus workflow
            "confidence",  # Not used in consensus workflow
        ]

        excluded_common_fields = [
            "model",  # Consensus uses 'models' field instead
            "temperature",  # Not used in consensus workflow
            "thinking_mode",  # Not used in consensus workflow
        ]

        requires_model = self.requires_model()
        model_field_schema = self.get_model_field_schema() if requires_model else None
        auto_mode = self.is_effective_auto_mode() if requires_model else False

        return WorkflowSchemaBuilder.build_schema(
            tool_specific_fields=consensus_field_overrides,
            model_field_schema=model_field_schema,
            auto_mode=auto_mode,
            tool_name=self.get_name(),
            excluded_workflow_fields=excluded_workflow_fields,
            excluded_common_fields=excluded_common_fields,
            require_model=requires_model,
        )

    def get_required_actions(
        self, step_number: int, confidence: str, findings: str, total_steps: int, request=None
    ) -> list[str]:  # noqa: ARG002
        """Describe the single synthesis phase after parallel consultation."""
        return [
            "并行且相互独立地咨询全部指定模型",
            "将全部返回观点综合为完整建议",
            "识别主要共识、分歧及其原因",
            "基于综合结果给出清晰、可执行的建议",
        ]

    def should_call_expert_analysis(self, consolidated_findings, request=None) -> bool:
        """Consensus workflow doesn't use traditional expert analysis - it consults models step by step."""
        return False

    def prepare_expert_analysis_context(self, consolidated_findings) -> str:
        """Not used in consensus workflow."""
        return ""

    def requires_expert_analysis(self) -> bool:
        """Consensus workflow handles its own model consultations."""
        return False

    def requires_model(self) -> bool:
        """
        Consensus tool doesn't require model resolution at the MCP boundary.

        Uses it's own set of models

        Returns:
            bool: False
        """
        return False

    # Hook method overrides for consensus-specific behavior

    def prepare_step_data(self, request) -> dict:
        """Prepare consensus-specific step data."""
        step_data = {
            "step": request.step,
            "step_number": request.step_number,
            "findings": request.findings,
            "files_checked": [],  # Not used
            "relevant_files": request.relevant_files or [],
            "relevant_context": [],  # Not used
            "issues_found": [],  # Not used
            "confidence": "exploring",  # Not used, kept for compatibility
            "hypothesis": None,  # Not used
            "images": request.images or [],  # Now used for visual context
        }
        return step_data

    async def handle_work_completion(self, response_data: dict, request, arguments: dict) -> dict:  # noqa: ARG002
        """Keep compatibility with the base workflow completion hook."""
        response_data["consensus_complete"] = True
        response_data["status"] = "consensus_workflow_complete"
        return response_data

    def handle_work_continuation(self, response_data: dict, request) -> dict:
        """Parallel consensus has no intermediate model-consultation steps."""
        response_data["status"] = "consensus_workflow_complete"
        response_data["consensus_complete"] = True
        response_data["next_step_required"] = False
        return response_data

    async def execute_workflow(self, arguments: dict[str, Any]) -> list:
        """Run all blinded model consultations concurrently during step 1."""
        request = self.get_workflow_request_model()(**arguments)

        if request.step_number != 1:
            response_data = {
                "status": "consensus_already_completed",
                "consensus_complete": True,
                "next_step_required": False,
                "next_steps": (
                    "并行 Consensus 已在第 1 步完成全部模型咨询。"
                    "请使用首次调用结果；如果首次调用返回了 task_id，请调用 consensus_status 查询。"
                ),
            }
            return [TextContent(type="text", text=json.dumps(response_data, indent=2, ensure_ascii=False))]

        continuation_id = request.continuation_id
        if not continuation_id:
            clean_args = {k: v for k, v in arguments.items() if k not in ["_model_context", "_resolved_model_name"]}
            continuation_id = create_thread(self.get_name(), clean_args)
            request.continuation_id = continuation_id

        models = [dict(model) for model in request.models or []]
        original_proposal = request.step
        request.total_steps = 1
        manager = get_consensus_task_manager()
        task_id = manager.create_record(models)
        total_timeout_seconds = CONSENSUS_SYNC_WAIT_SECONDS + CONSENSUS_BACKGROUND_WAIT_SECONDS
        manager.start(
            task_id,
            self._run_parallel_consensus(
                task_id=task_id,
                request=request,
                original_proposal=original_proposal,
                models=models,
                continuation_id=continuation_id,
            ),
            timeout_seconds=total_timeout_seconds,
        )

        result = await manager.wait(task_id, CONSENSUS_SYNC_WAIT_SECONDS)
        if result is not None:
            manager.discard(task_id)
            return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

        snapshot = manager.get_snapshot(task_id)
        response_data = {
            "status": "consensus_in_progress",
            "consensus_complete": False,
            "next_step_required": False,
            "task_id": task_id,
            "completed_models": snapshot.get("completed_models", 0),
            "total_models": snapshot.get("total_models", len(models)),
            "continuation_id": continuation_id,
            "background_wait_seconds": CONSENSUS_BACKGROUND_WAIT_SECONDS,
            "total_timeout_seconds": total_timeout_seconds,
            "next_steps": (
                "请调用 consensus_status，并传入此 task_id 查询进度或获取最终结果。"
                f"任务从开始计算最多运行 {total_timeout_seconds:g} 秒，"
                f"返回 task_id 后还可后台运行 {CONSENSUS_BACKGROUND_WAIT_SECONDS:g} 秒。"
            ),
        }
        continuation_offer = self._build_continuation_offer(continuation_id)
        if continuation_offer:
            response_data["continuation_offer"] = continuation_offer
        return [TextContent(type="text", text=json.dumps(response_data, indent=2, ensure_ascii=False))]

    async def _run_parallel_consensus(
        self,
        *,
        task_id: str,
        request: ConsensusRequest,
        original_proposal: str,
        models: list[dict[str, Any]],
        continuation_id: str,
    ) -> dict[str, Any]:
        """Consult all models with bounded concurrency and stable result ordering."""
        manager = get_consensus_task_manager()
        semaphore = asyncio.Semaphore(CONSENSUS_MAX_CONCURRENCY)

        async def consult_one(index: int, model_config: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                manager.mark_model_running(task_id, index)
                try:
                    result = await asyncio.to_thread(
                        self._consult_model_sync,
                        model_config,
                        request,
                        original_proposal,
                    )
                except Exception as exc:
                    logger.exception("Unexpected error consulting model %s", model_config)
                    result = {
                        "model": model_config.get("model", "unknown"),
                        "stance": model_config.get("stance", "neutral"),
                        "status": "error",
                        "error": str(exc),
                    }
                model_status = "completed" if result.get("status") == "success" else "error"
                manager.mark_model_finished(task_id, index, model_status)
                return result

        responses = await asyncio.gather(
            *(consult_one(index, model_config) for index, model_config in enumerate(models))
        )
        response_data = self._build_parallel_response(
            request=request,
            original_proposal=original_proposal,
            responses=responses,
            continuation_id=continuation_id,
        )
        self._store_parallel_result(continuation_id, response_data, request, responses)
        continuation_offer = self._build_continuation_offer(continuation_id)
        if continuation_offer:
            response_data["continuation_offer"] = continuation_offer
        return response_data

    def _build_parallel_response(
        self,
        *,
        request: ConsensusRequest,
        original_proposal: str,
        responses: list[dict[str, Any]],
        continuation_id: str,
    ) -> dict[str, Any]:
        """Build the synthesis-ready response shared by sync and background completion paths."""
        successful_count = sum(response.get("status") == "success" for response in responses)
        failed_count = len(responses) - successful_count
        status = "consensus_workflow_complete" if successful_count else "consensus_failed"
        confidence = "high" if successful_count >= 2 else "partial"
        model_labels = [
            f"{response.get('model', 'unknown')}:{response.get('stance', 'neutral')}" for response in responses
        ]

        return {
            "status": status,
            "step_number": 1,
            "total_steps": 1,
            "consensus_complete": True,
            "next_step_required": False,
            "current_model_index": len(responses),
            "total_models": len(responses),
            "successful_responses": successful_count,
            "failed_responses": failed_count,
            "model_responses": responses,
            "accumulated_responses": responses,
            "agent_analysis": {
                "initial_analysis": request.step,
                "findings": request.findings,
            },
            "complete_consensus": {
                "initial_prompt": original_proposal,
                "models_consulted": model_labels,
                "total_responses": len(responses),
                "consensus_confidence": confidence,
            },
            "continuation_id": continuation_id,
            "metadata": {
                "tool_name": self.get_name(),
                "workflow_type": "parallel_multi_model_consensus",
                "models_consulted": model_labels,
                "total_models": len(responses),
            },
            "next_steps": (
                "Consensus 信息收集已完成，请综合全部观点并输出：\n"
                "1. 各模型的主要共识\n"
                "2. 各模型的主要分歧及原因\n"
                "3. 最终综合建议\n"
                "4. 具体、可执行的实施步骤\n"
                "5. 必须处理的关键风险或疑虑"
            ),
        }

    def _store_parallel_result(
        self,
        continuation_id: str,
        response_data: dict[str, Any],
        request: ConsensusRequest,
        responses: list[dict[str, Any]],
    ) -> None:
        """Persist one completed consensus turn without shared workflow instance state."""
        add_turn(
            thread_id=continuation_id,
            role="assistant",
            content=json.dumps(response_data, ensure_ascii=False),
            files=list(request.relevant_files or []),
            images=list(request.images or []),
            tool_name=self.get_name(),
            model_name="consensus",
            model_metadata={
                "workflow_type": "parallel_multi_model_consensus",
                "individual_responses": responses,
            },
        )

    def _build_continuation_offer(self, continuation_id: str) -> dict[str, Any] | None:
        """Create a continuation offer without exposing prior model responses."""
        try:
            from tools.models import ContinuationOffer

            thread = get_thread(continuation_id)
            if thread and thread.turns:
                remaining_turns = max(0, MAX_CONVERSATION_TURNS - len(thread.turns))
            else:
                remaining_turns = MAX_CONVERSATION_TURNS - 1

            # Provide a neutral note specific to consensus workflow
            note = (
                f"Consensus 工作流还可继续 {remaining_turns} 轮对话。"
                if remaining_turns > 0
                else "Consensus 工作流已达到对话轮数上限。"
            )

            continuation_offer = ContinuationOffer(
                continuation_id=continuation_id,
                note=note,
                remaining_turns=remaining_turns,
            )
            return continuation_offer.model_dump()
        except Exception:
            return None

    def _consult_model_sync(
        self,
        model_config: dict[str, Any],
        request: ConsensusRequest,
        original_proposal: str,
    ) -> dict[str, Any]:
        """Consult one blinded model synchronously inside a worker thread."""
        try:
            from utils.model_context import ModelContext

            model_name = model_config["model"]
            with _PROVIDER_INITIALIZATION_LOCK:
                provider = self.get_model_provider(model_name)
                if getattr(provider, "_client", object()) is None:
                    getattr(provider, "client", None)
            model_context = ModelContext(model_name=model_name)

            prompt = original_proposal
            if request.relevant_files:
                file_content, _ = self._prepare_file_content_for_prompt(
                    list(request.relevant_files),
                    None,
                    "Context files",
                    arguments={"_remaining_tokens": None},
                    model_context=model_context,
                )
                if file_content:
                    prompt = f"{prompt}\n\n=== CONTEXT FILES ===\n{file_content}\n=== END CONTEXT ==="

            stance = model_config.get("stance", "neutral")
            stance_prompt = model_config.get("stance_prompt")
            system_prompt = self._get_stance_enhanced_prompt(stance, stance_prompt)
            validated_temperature, temp_warnings = self.validate_and_correct_temperature(
                self.get_default_temperature(), model_context
            )

            for warning in temp_warnings:
                logger.warning(warning)

            response = provider.generate_content(
                prompt=prompt,
                model_name=model_name,
                system_prompt=system_prompt,
                temperature=validated_temperature,
                thinking_mode="medium",
                images=request.images if request.images else None,
            )

            return {
                "model": model_name,
                "stance": stance,
                "status": "success",
                "verdict": response.content,
                "metadata": {
                    "provider": provider.get_provider_type().value,
                    "model_name": model_name,
                },
            }

        except Exception as exc:
            logger.exception("Error consulting model %s", model_config)
            return {
                "model": model_config.get("model", "unknown"),
                "stance": model_config.get("stance", "neutral"),
                "status": "error",
                "error": str(exc),
            }

    async def _consult_model(
        self,
        model_config: dict[str, Any],
        request: ConsensusRequest,
        original_proposal: str | None = None,
    ) -> dict[str, Any]:
        """Compatibility wrapper for callers that test a single consultation."""
        proposal = original_proposal or request.step
        return await asyncio.to_thread(self._consult_model_sync, model_config, request, proposal)

    def _get_stance_enhanced_prompt(self, stance: str, custom_stance_prompt: str | None = None) -> str:
        """Get the system prompt with stance injection."""
        base_prompt = CONSENSUS_PROMPT

        if custom_stance_prompt:
            return base_prompt.replace("{stance_prompt}", custom_stance_prompt)

        stance_prompts = {
            "for": """SUPPORTIVE PERSPECTIVE WITH INTEGRITY

You are tasked with advocating FOR this proposal, but with CRITICAL GUARDRAILS:

MANDATORY ETHICAL CONSTRAINTS:
- This is NOT a debate for entertainment. You MUST act in good faith and in the best interest of the questioner
- You MUST think deeply about whether supporting this idea is safe, sound, and passes essential requirements
- You MUST be direct and unequivocal in saying "this is a bad idea" when it truly is
- There must be at least ONE COMPELLING reason to be optimistic, otherwise DO NOT support it

WHEN TO REFUSE SUPPORT (MUST OVERRIDE STANCE):
- If the idea is fundamentally harmful to users, project, or stakeholders
- If implementation would violate security, privacy, or ethical standards
- If the proposal is technically infeasible within realistic constraints
- If costs/risks dramatically outweigh any potential benefits

YOUR SUPPORTIVE ANALYSIS SHOULD:
- Identify genuine strengths and opportunities
- Propose solutions to overcome legitimate challenges
- Highlight synergies with existing systems
- Suggest optimizations that enhance value
- Present realistic implementation pathways

Remember: Being "for" means finding the BEST possible version of the idea IF it has merit, not blindly supporting bad ideas.""",
            "against": """CRITICAL PERSPECTIVE WITH RESPONSIBILITY

You are tasked with critiquing this proposal, but with ESSENTIAL BOUNDARIES:

MANDATORY FAIRNESS CONSTRAINTS:
- You MUST NOT oppose genuinely excellent, common-sense ideas just to be contrarian
- You MUST acknowledge when a proposal is fundamentally sound and well-conceived
- You CANNOT give harmful advice or recommend against beneficial changes
- If the idea is outstanding, say so clearly while offering constructive refinements

WHEN TO MODERATE CRITICISM (MUST OVERRIDE STANCE):
- If the proposal addresses critical user needs effectively
- If it follows established best practices with good reason
- If benefits clearly and substantially outweigh risks
- If it's the obvious right solution to the problem

YOUR CRITICAL ANALYSIS SHOULD:
- Identify legitimate risks and failure modes
- Point out overlooked complexities
- Suggest more efficient alternatives
- Highlight potential negative consequences
- Question assumptions that may be flawed

Remember: Being "against" means rigorous scrutiny to ensure quality, not undermining good ideas that deserve support.""",
            "neutral": """BALANCED PERSPECTIVE

Provide objective analysis considering both positive and negative aspects. However, if there is overwhelming evidence
that the proposal clearly leans toward being exceptionally good or particularly problematic, you MUST accurately
reflect this reality. Being "balanced" means being truthful about the weight of evidence, not artificially creating
50/50 splits when the reality is 90/10.

Your analysis should:
- Present all significant pros and cons discovered
- Weight them according to actual impact and likelihood
- If evidence strongly favors one conclusion, clearly state this
- Provide proportional coverage based on the strength of arguments
- Help the questioner see the true balance of considerations

Remember: Artificial balance that misrepresents reality is not helpful. True balance means accurate representation
of the evidence, even when it strongly points in one direction.""",
        }

        stance_prompt = stance_prompts.get(stance, stance_prompts["neutral"])
        return base_prompt.replace("{stance_prompt}", stance_prompt)

    # Required abstract methods from BaseTool
    def get_request_model(self):
        """Return the consensus workflow-specific request model."""
        return ConsensusRequest

    async def prepare_prompt(self, request) -> str:  # noqa: ARG002
        """Not used - workflow tools use execute_workflow()."""
        return ""  # Workflow tools use execute_workflow() directly
