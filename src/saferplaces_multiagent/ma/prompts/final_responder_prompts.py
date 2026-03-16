"""Final responder prompts for generating user-facing responses."""

import json
from typing import Dict, Any

from ...common.states import MABaseGraphState

from . import Prompt


class FinalResponderPrompts:
    """Prompts for the final response generation stage."""

    class Response:
        """System prompts for generating the final response."""

        @staticmethod
        def stable() -> Prompt:
            """Stable version of the response system prompt."""
            p = {
                "title": "FinalResponse",
                "description": "System prompt per generare la risposta finale all'utente",
                "command": "",
                "message": (
                    "You are an expert assistant responsible for generating the final response to the user.\n"
                    "\n"
                    "Instructions:\n"
                    "- Write a clear, concise, and helpful answer based on the provided context and tool results.\n"
                    "- If there are errors or issues, explain them clearly and suggest possible next steps.\n"
                    "- If the context involves geospatial data or map layers, ensure your answer is relevant and informative.\n"
                    "- Respond in the same language as the user's original request, unless otherwise specified.\n"
                    "- Do not invent information; base your answer strictly on the available data."
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Minimal version for testing."""
            p = {
                "title": "FinalResponse",
                "description": "Minimal response system prompt",
                "command": "",
                "message": (
                    "Generate a clear and concise response based on the provided context.\n"
                    "Do not invent information."
                )
            }
            return Prompt(p)

    class Context:
        """Context prompts for providing state information to the final response."""

        class Structured:
            """Structured context in JSON format."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Stable version: serialize state as JSON."""
                context_dict = {
                    'parsed_request': state.get('parsed_request'),
                    'plan': state.get('plan'),
                    'tool_results': state.get('tool_results'),
                    'error': state.get('error')
                }
                context_json = json.dumps(context_dict, ensure_ascii=False, indent=2)
                
                p = {
                    "title": "StructuredContext",
                    "description": "Serializza lo stato come JSON per il contesto",
                    "command": "",
                    "message": f"Context JSON:\n{context_json}"
                }
                return Prompt(p)

            @staticmethod
            def v001(state: MABaseGraphState, **kwargs) -> Prompt:
                """Minimal version: only main fields."""
                context_dict = {
                    'intent': (state.get('parsed_request') or {}).get('intent'),
                    'plan': state.get('plan'),
                    'has_errors': bool(state.get('error'))
                }
                context_json = json.dumps(context_dict, ensure_ascii=False, indent=2)
                
                p = {
                    "title": "StructuredContext",
                    "description": "Minimal structured context",
                    "command": "",
                    "message": f"Context JSON:\n{context_json}"
                }
                return Prompt(p)

        class Formatted:
            """Formatted context in human-readable text."""

            @staticmethod
            def stable(state: MABaseGraphState, **kwargs) -> Prompt:
                """Stable version: formatted text context."""
                parsed_request = state.get('parsed_request') or {}
                intent = parsed_request.get('intent', 'N/A')
                entities = ', '.join(parsed_request.get('entities', [])) or 'N/A'
                plan = state.get('plan', 'N/A')
                tool_results = state.get('tool_results', 'N/A')
                error = state.get('error', 'None')
                raw_text = parsed_request.get('raw_text', 'N/A')
                
                p = {
                    "title": "FormattedContext",
                    "description": "Formatta lo stato in testo leggibile",
                    "command": "",
                    "message": (
                        "Context for your answer:\n"
                        f"- User intent: {intent}\n"
                        f"- Entities: {entities}\n"
                        f"- Plan: {plan}\n"
                        f"- Tool results: {tool_results}\n"
                        f"- Error: {error}\n"
                        f"- Original user input: {raw_text}\n"
                    )
                }
                return Prompt(p)

            @staticmethod
            def v001(state: MABaseGraphState, **kwargs) -> Prompt:
                """Minimal version: only key fields."""
                parsed_request = state.get('parsed_request') or {}
                intent = parsed_request.get('intent', 'N/A')
                plan = state.get('plan', 'N/A')
                error = state.get('error', 'None')
                
                p = {
                    "title": "FormattedContext",
                    "description": "Minimal formatted context",
                    "command": "",
                    "message": (
                        "Context for your answer:\n"
                        f"- User intent: {intent}\n"
                        f"- Plan: {plan}\n"
                        f"- Error: {error}\n"
                    )
                }
                return Prompt(p)
