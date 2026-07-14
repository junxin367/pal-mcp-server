"""验证 Consensus 在单次调用中并行咨询全部模型。"""

import json

from .conversation_base_test import ConversationBaseTest


class TestConsensusWorkflowAccurate(ConversationBaseTest):
    """验证并行 Consensus 的完整响应结构和稳定模型顺序。"""

    @property
    def test_name(self) -> str:
        return "consensus_workflow_accurate"

    @property
    def test_description(self) -> str:
        return "验证两个独立模型在第 1 步并行执行，并一次返回完整结果"

    def run_test(self) -> bool:
        """运行完整的单次并行 Consensus 场景。"""
        self.setUp()

        try:
            self.logger.info("测试单次并行 Consensus：flash:for + flash:against")
            response, _ = self.call_mcp_tool_direct(
                "consensus",
                {
                    "step": (
                        "Should we add a new AI-powered search feature to our application? "
                        "Please analyze the technical feasibility, user value, and implementation complexity."
                    ),
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": (
                        "Initial assessment of AI search feature proposal considering user needs, "
                        "technical constraints, and business value."
                    ),
                    "models": [
                        {
                            "model": "flash",
                            "stance": "for",
                            "stance_prompt": "Focus on innovation benefits and competitive advantages.",
                        },
                        {
                            "model": "flash",
                            "stance": "against",
                            "stance_prompt": "Focus on implementation complexity and resource requirements.",
                        },
                    ],
                },
            )

            if not response:
                self.logger.error("Consensus 未返回响应")
                return False

            data = json.loads(response)
            if data.get("status") != "consensus_workflow_complete":
                self.logger.error(
                    "期望 status=consensus_workflow_complete，实际为 %s",
                    data.get("status"),
                )
                return False

            if data.get("step_number") != 1 or data.get("total_steps") != 1:
                self.logger.error("并行 Consensus 应在第 1 步完成：%s", data)
                return False

            if data.get("next_step_required") or not data.get("consensus_complete"):
                self.logger.error("完整结果必须标记 next_step_required=false 且 consensus_complete=true")
                return False

            responses = data.get("model_responses", [])
            if len(responses) != 2:
                self.logger.error("期望 2 个模型响应，实际为 %s", len(responses))
                return False

            expected_models = [("flash", "for"), ("flash", "against")]
            actual_models = [(item.get("model"), item.get("stance")) for item in responses]
            if actual_models != expected_models:
                self.logger.error("模型响应顺序错误，期望 %s，实际 %s", expected_models, actual_models)
                return False

            if any(not item.get("verdict") for item in responses):
                self.logger.error("模型响应缺少 verdict")
                return False

            if data.get("accumulated_responses") != responses:
                self.logger.error("accumulated_responses 应与 model_responses 一致")
                return False

            complete_consensus = data.get("complete_consensus", {})
            if complete_consensus.get("models_consulted") != ["flash:for", "flash:against"]:
                self.logger.error("complete_consensus 模型列表错误：%s", complete_consensus)
                return False

            if complete_consensus.get("total_responses") != 2:
                self.logger.error("complete_consensus.total_responses 应为 2")
                return False

            self.logger.info("✓ 两个模型已在单次调用中完成并按输入顺序返回")
            return True
        except Exception as exc:
            self.logger.exception("Consensus 并行工作流测试失败：%s", exc)
            return False
        finally:
            self.cleanup_test_files()
