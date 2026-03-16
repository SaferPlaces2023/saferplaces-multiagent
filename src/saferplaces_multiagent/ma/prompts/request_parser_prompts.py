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

        @staticmethod
        def v001() -> Prompt:
            p = {
                "title": "RequestParserContext",
                "description": "System prompt per il parsing strutturato delle richieste utente",
                "command": "",
                "message": (
                    "Extract the main intent and any entities from the user request. "
                    "Keep it simple and concise."
                )
            }
            return Prompt(p)
