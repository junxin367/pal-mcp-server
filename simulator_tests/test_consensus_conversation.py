#!/usr/bin/env python3
"""验证并行 Consensus 的跨工具 continuation 存储。"""

import json

from .conversation_base_test import ConversationBaseTest


class TestConsensusConversation(ConversationBaseTest):
    """验证 Consensus 结果写入已有会话后可被其他工具继续使用。"""

    def call_mcp_tool(self, tool_name: str, params: dict) -> tuple:
        """在当前进程内调用 MCP 工具。"""
        return self.call_mcp_tool_direct(tool_name, params)

    @property
    def test_name(self) -> str:
        return "consensus_conversation"

    @property
    def test_description(self) -> str:
        return "验证并行 Consensus 使用已有 continuation_id 并保存完整结果"

    def run_test(self) -> bool:
        """运行跨工具 continuation 场景。"""
        try:
            self.setUp()
            self.setup_test_files()

            initial_response, continuation_id = self.call_mcp_tool(
                "chat",
                {
                    "prompt": (
                        "Please use low thinking mode. I'm working on a web application and need advice "
                        "on authentication. Can you look at this code?"
                    ),
                    "absolute_file_paths": [self.test_files["python"]],
                    "model": "flash",
                },
            )
            if not initial_response or not continuation_id:
                self.logger.error("初始 Chat 未返回有效 continuation_id")
                return False

            consensus_response, _ = self.call_mcp_tool(
                "consensus",
                {
                    "step": (
                        "Should we implement OAuth2 or stick with simple session-based authentication "
                        "for this web application?"
                    ),
                    "step_number": 1,
                    "total_steps": 1,
                    "next_step_required": False,
                    "findings": "Initial analysis of OAuth2 versus session-based authentication.",
                    "models": [
                        {
                            "model": "flash",
                            "stance": "for",
                            "stance_prompt": "Focus on OAuth2 security, scalability, and industry standards.",
                        },
                        {
                            "model": "flash",
                            "stance": "against",
                            "stance_prompt": "Focus on OAuth2 complexity and simpler alternatives.",
                        },
                    ],
                    "continuation_id": continuation_id,
                },
            )
            if not consensus_response:
                self.logger.error("Consensus 未返回响应")
                return False

            data = json.loads(consensus_response)
            if data.get("status") != "consensus_workflow_complete":
                self.logger.error("Consensus 未正常完成：%s", data.get("status"))
                return False

            responses = data.get("model_responses", [])
            if [(item.get("model"), item.get("stance")) for item in responses] != [
                ("flash", "for"),
                ("flash", "against"),
            ]:
                self.logger.error("Consensus 模型响应顺序或立场错误：%s", responses)
                return False

            if data.get("continuation_id") != continuation_id:
                self.logger.error("Consensus 未沿用已有 continuation_id")
                return False

            if data.get("next_step_required") or not data.get("consensus_complete"):
                self.logger.error("Consensus 应在第 1 步返回完整结果")
                return False

            chat_response, _ = self.call_mcp_tool(
                "chat",
                {
                    "prompt": "Based on our consensus discussion, summarize the key authentication trade-offs.",
                    "continuation_id": continuation_id,
                    "model": "flash",
                },
            )
            if not chat_response:
                self.logger.error("Consensus 完成后的跨工具 continuation 失败")
                return False

            self.logger.info("✓ Consensus 完整结果已写入已有会话，并可由 Chat 继续使用")
            return True
        except Exception as exc:
            self.logger.exception("Consensus continuation 测试失败：%s", exc)
            return False
        finally:
            self.cleanup_test_files()
