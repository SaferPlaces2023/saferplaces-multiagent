# TODO

> **Workflow**: `F###` (functional spec) → _plan-todo.md → PLN-### active plan → completed → [`archive/`](archive/) → Functional becomes "current"
> **ID Namespace**: vedere [`docs/index.md`](../docs/index.md#id-namespace-registry) per la fonte di verità di tutti i prefissi.

## Open

- [ ] **IMP-004** — Eliminare `src/saferplaces_multiagent/graph.py` (vecchio grafo legacy, non importato da nessuno). Il grafo attivo è `multiagent_graph.py`.

- [ ] **IMP-002** — Aggiungere `LAYERS_AGENT` all'`AGENT_REGISTRY` del supervisor (o documentare esplicitamente perché è escluso). Attualmente il Layers Agent viene chiamato direttamente dal supervisor (`self.layer_agent(state)`) senza passare dal router, creando un percorso implicito non documentato nel registry. File: `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py`

- [ ] **IMP-003** — Formalizzare i descrittori degli agenti (`MODELS_AGENT_DESCRIPTION`, `SAFERCAST_AGENT_DESCRIPTION`, `LAYERS_AGENT_DESCRIPTION`) in un formato strutturato condiviso (es. `AgentDescriptor` Pydantic model con campi `name`, `description`, `examples`, `when_to_use`) invece di plain dict. Semplifica la validazione e l'estensibilità quando si aggiungono nuovi agenti.

## Active Plans

> **Prossimo numero disponibile: PLN-013** — aggiornare questa riga ogni volta che si crea o archivia un piano.

| Piano | Titolo | File target |
|---|---|---|
| PLN-003 | Test con prompt override del SupervisorAgent | `tests/T006_prompt_override.py` |
