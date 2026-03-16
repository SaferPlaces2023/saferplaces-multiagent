"""
T006 — Supervisor prompt override
----------------------------------
Verifica che il grafo risponda in modo diverso quando il prompt di sistema
del SupervisorAgent viene sostituito con una variante minimale.

Esecuzione:
    python -m tests.T006_prompt_override
"""

import sys
from unittest.mock import patch
from pathlib import Path

from tests._utils import run_tests, silence

with silence():
    from saferplaces_multiagent.ma.prompts.supervisor_agent_prompts import OrchestratorPrompts
    from saferplaces_multiagent.ma.prompts import Prompt


# ---------------------------------------------------------------------------
# Prompt alternativo — supervisor forzato a produrre sempre un piano vuoto
# ---------------------------------------------------------------------------
def _always_empty_plan_prompt() -> Prompt:
    return Prompt({
        "title": "OrchestrationContext",
        "description": "test override — always return empty plan",
        "command": "",
        "message": (
            "You are a test orchestration agent.\n"
            "Always return an empty plan, regardless of the user request.\n"
            "Do NOT assign any steps to any agent.\n"
            "Respond only with an empty plan."
        ),
    })


# ---------------------------------------------------------------------------
# Messaggi di test — stesse richieste di T003/T004 (richiederebbero agenti)
# ---------------------------------------------------------------------------
MESSAGES = [
    "Retrieve the latest DPC rainfall radar for northern Italy",
    "Run a SaferRain flood simulation for Rome with 50mm/h rainfall",
]


# ---------------------------------------------------------------------------
# Esecuzione con patch attivo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result_file = Path(__file__).parent / "result" / "T006.md"

    with patch.object(OrchestratorPrompts.MainContext, "stable", _always_empty_plan_prompt):
        run_tests(MESSAGES, result_file=result_file)
