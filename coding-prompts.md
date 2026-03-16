# Coding Agent Prompts — PLN-004

Prompt specializzati per completare i task di refactoring dei prompt degli agenti chat. Ogni prompt è focato su un singolo task e omette contesto duplicato.

---


---


---


---


---


---

## Riferimenti rapidi

| Cosa | Dove |
|---|---|
| Testo di `SYSTEM_REQUEST_PROMPT` | `src/saferplaces_multiagent/ma/chat/request_parser.py`, classe `Prompts` (da copiare) |
| Testo di `FINAL_RESPONSE_PROMPT` | `src/saferplaces_multiagent/ma/chat/final_responder.py`, classe `Prompts` |
| Lambda di `STRUCTURED_FINAL_CONTEXT` | `src/saferplaces_multiagent/ma/chat/final_responder.py`, classe `Prompts` |
| Lambda di `FORMAT_FINAL_CONTEXT` | `src/saferplaces_multiagent/ma/chat/final_responder.py`, classe `Prompts` |
| Dataclass `Prompt` | `src/saferplaces_multiagent/ma/prompts/__init__.py` |
| Esempio pattern F009 | `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py` |
| MABaseGraphState | `src/saferplaces_multiagent/common/states.py` |

