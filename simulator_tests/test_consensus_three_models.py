"""验证三个模型的受限并行 Consensus。"""

import json

from .base_test import BaseSimulatorTest


class TestConsensusThreeModels(BaseSimulatorTest):
    """验证三个模型在单次调用中完成，并保持输入顺序。"""

    @property
    def test_name(self) -> str:
        return "consensus_three_models"

    @property
    def test_description(self) -> str:
        return "验证 flash:against、flash:for、local-llama:neutral 的单次并行 Consensus"

    def run_test(self) -> bool:
        """运行三个模型的 Consensus 场景。"""
        try:
            models = [
                {
                    "model": "flash",
                    "stance": "against",
                    "stance_prompt": (
                        "You are a software architecture critic. Focus on complexity overhead, "
                        "maintenance burden, over-engineering, and simpler alternatives."
                    ),
                },
                {
                    "model": "flash",
                    "stance": "for",
                    "stance_prompt": (
                        "You are a software architecture advocate. Focus on separation of concerns, "
                        "testability, maintainability, scalability, and code organization."
                    ),
                },
                {
                    "model": "local-llama",
                    "stance": "neutral",
                    "stance_prompt": (
                        "You are a pragmatic software engineer. Provide a balanced analysis for a CoolTodos app."
                    ),
                },
            ]
            response, _ = self.call_mcp_tool(
                "consensus",
                {
                    "step": "Is a sync manager class a good idea for my CoolTodos app?",
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": "Initial analysis of the sync manager architecture decision.",
                    "models": models,
                },
            )

            if not response:
                self.logger.error("三个模型的 Consensus 未返回响应")
                return False

            data = json.loads(response)
            if data.get("status") not in {"consensus_workflow_complete", "consensus_failed"}:
                self.logger.error("Consensus 返回了非终态：%s", data.get("status"))
                return False

            if data.get("next_step_required") or not data.get("consensus_complete"):
                self.logger.error("三个模型的 Consensus 应在第 1 步直接完成")
                return False

            responses = data.get("model_responses", [])
            if len(responses) != len(models):
                self.logger.error("期望 %s 个响应，实际为 %s", len(models), len(responses))
                return False

            expected_order = [(item["model"], item["stance"]) for item in models]
            actual_order = [(item.get("model"), item.get("stance")) for item in responses]
            if actual_order != expected_order:
                self.logger.error("模型顺序错误，期望 %s，实际 %s", expected_order, actual_order)
                return False

            if any(item.get("status") not in {"success", "error"} for item in responses):
                self.logger.error("模型响应包含未知状态：%s", responses)
                return False

            if data.get("successful_responses", 0) + data.get("failed_responses", 0) != len(models):
                self.logger.error("成功/失败计数与模型总数不一致")
                return False

            metadata = data.get("metadata", {})
            if metadata.get("total_models") != len(models):
                self.logger.error("metadata.total_models 错误：%s", metadata)
                return False

            self.logger.info("✓ 三个模型已在单次调用中完成并保持输入顺序")
            return True
        except Exception as exc:
            self.logger.exception("三个模型的 Consensus 测试失败：%s", exc)
            return False
