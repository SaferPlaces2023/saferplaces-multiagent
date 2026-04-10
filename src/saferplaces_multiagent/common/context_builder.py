"""Context Enrichment Pipeline (§1 of PLN-013).

Builds a rich, structured PlanningContext for the Supervisor Agent.
Tres phases:
1. Resolve geospatial context (layers summary)
2. Tool results history (what was produced in previous steps)
3. Semantically filtered conversation (only user/final/feedback messages)
"""

from __future__ import annotations

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
import json

from saferplaces_multiagent.common.states import MABaseGraphState

from .base_models import compute_geometry_metadata

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage



class ContextBuilder:

    @staticmethod
    def conversation_history(state: MABaseGraphState, max_messages: int = 5) -> str:
        """
        Build a readable conversation history for LLM context.

        Format:
        - Each message starts with a role header on its own line.
        - Multi-line content is indented for readability.
        - AI tool calls and their corresponding tool responses are grouped
          together as a single logical unit, visually bracketed.
        """
        messages = state.get("messages", [])

        if not messages:
            return "No previous conversation."

        # --- Build an index: tool_call_id → ToolMessage ---
        tool_response_index: dict[str, ToolMessage] = {}
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_response_index[msg.tool_call_id] = msg

        # --- Helper: indent multi-line text ---
        def _indent(text: str, prefix: str = "  ") -> str:
            return "\n".join(prefix + line for line in text.splitlines())

        blocks: list[str] = []
        already_rendered: set[str] = set()  # tool_call_ids already shown

        for msg in messages:

            if isinstance(msg, HumanMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                blocks.append(f"[HUMAN]\n{_indent(content)}")

            elif isinstance(msg, AIMessage):
                # Text content (if any)
                if msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    blocks.append(f"[AI]\n{_indent(content)}")

                # Tool calls — group each call with its response
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tc_id = tc.get("id", "")
                        tc_name = tc.get("name", "?")
                        args_str = json.dumps(tc.get("args", {}), ensure_ascii=False, indent=2)
                        tool_msg = tool_response_index.get(tc_id)

                        invocation_block = (
                            f"[AI → TOOL CALL: {tc_name}]\n"
                            f"{_indent(args_str)}"
                        )
                        if tool_msg:
                            already_rendered.add(tc_id)
                            response_content = (
                                tool_msg.content
                                if isinstance(tool_msg.content, str)
                                else str(tool_msg.content)
                            )
                            invocation_block += (
                                f"\n[TOOL RESPONSE: {tc_name}]\n"
                                f"{_indent(response_content)}"
                            )
                        blocks.append(invocation_block)

            elif isinstance(msg, ToolMessage):
                # Only render orphaned tool responses not already paired above
                if msg.tool_call_id not in already_rendered:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    label = msg.name or "tool"
                    blocks.append(f"[TOOL RESPONSE: {label}]\n{_indent(content)}")

            elif isinstance(msg, SystemMessage):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                blocks.append(f"[SYSTEM]\n{_indent(content)}")

        if not blocks:
            return "No previous messages."

        if len(blocks) > max_messages:
            blocks = blocks[-max_messages:]

        separator = "\n\n" + "─" * 40 + "\n\n"
        
        history = separator.join(blocks)

        print('\n------------\n' + history + '\n------------\n')

        return history
