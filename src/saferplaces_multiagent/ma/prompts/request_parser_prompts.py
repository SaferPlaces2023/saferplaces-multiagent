"""Request parser prompts for parsing user requests."""

from . import Prompt
from ...common.states import MABaseGraphState


class RequestParserPrompts:

    class MainContext:

        @staticmethod
        def stable() -> Prompt:
            p = {
                "title": "RequestParserContext",
                "description": "System prompt per il parsing strutturato delle richieste utente",
                "command": "",
                "message": (
                    "You are an expert assistant that converts user requests into a structured execution request.\n"
                    "\n"
                    "Your tasks:\n"
                    "1. Extract the main high-level intent of the request (as a short phrase).\n"
                    "2. Extract a list of relevant entities explicitly mentioned in the request.\n"
                    "3. Extract explicit parameters only if they are clearly stated.\n"
                    "4. Copy the original user input verbatim as the raw_text field.\n"
                    "\n"
                    "Rule: Extract only information explicitly stated; do not infer or add.\n"
                    "\n"
                    "## Output format\n"
                    "Expected fields:\n"
                    "- intent: short phrase describing the main goal of the request\n"
                    "- entities: list of named entities (locations, layers, models, dates) explicitly mentioned\n"
                    "- raw_text: verbatim copy of the user's original message"
                )
            }
            return Prompt(p)

        @staticmethod
        def v001() -> Prompt:
            """Previous stable version — preserved for test override compatibility."""
            p = {
                "title": "RequestParserContext",
                "description": "System prompt per il parsing strutturato delle richieste utente",
                "command": "",
                "message": (
                    "You are an expert assistant that converts user requests into a structured execution request.\n"
                    "\n"
                    "Your tasks:\n"
                    "- Extract the main high-level intent of the request (as a short phrase).\n"
                    "- Extract a list of relevant entities explicitly mentioned in the request.\n"
                    "- Extract explicit parameters only if they are clearly stated.\n"
                    "- Copy the original user input as a field.\n"
                    "- Do not invent or hallucinate information. If a field is not present, leave it empty or as an empty list.\n"
                    "\n"
                    "Be precise, concise, and execution-oriented."
                )
            }
            return Prompt(p)
