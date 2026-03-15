# SaferPlaces Multiagent — Istruzioni per Copilot

## Ordine di priorità delle istruzioni

In caso di conflitti o ambiguità, seguire quest'ordine:

1. `.github/copilot-instructions.md` — contesto workspace (tech stack, struttura, comandi)
2. `.github/instructions/*.instructions.md` — regole per dominio (caricate con `applyTo`):
   - `coding-standards.instructions.md` → `**/*.py` — naming, linting, nodi, tool, state
   - `services.instructions.md` → `src/**` — S3, Flask, LangGraph, testing
   - `planning.instructions.md` → `implementations/**` — workflow PLN-###, _plan-todo
   - `docs.instructions.md` → `docs/**` — namespace ID, stile documentazione
3. `/memories/repo/` — fatti verificati sul codebase
4. `docs/functional-spec*.md` — stato corrente delle funzionalità (fonte di verità vivente)
5. `implementations/PLN-001-*.md` — architettura generale e roadmap
6. `implementations/PLN-002-*.md` … — dettagli implementativi per step

### Riferimenti aggiuntivi

| File | Scopo |
|---|---|
| `docs/index.md` | Hub di navigazione e fonte di verità per il namespace degli ID |
| `docs/architecture.md` | Schema DB, route API, variabili d'ambiente, topologia infra |
| `implementations/_plan-todo.md` | Open items e piani attivi |
| `implementations/archive/` | Piani completati (solo lettura) |
| `docs/functional-spec.md` | Stato corrente: agenti, stato, routing (F###) |
| `docs/functional-spec-services.md` | Stato corrente: tool e servizi esterni (S###) |
| `docs/functional-spec-map.md` | Stato corrente: layer registry e geospaziale (M###) |
| `tests/tests.json` + `tests/result/` | Comportamento atteso verificato |
| `README.md` | Overview generale del progetto |

---

## Panoramica del progetto

Sistema **multi-agent AI gerarchico** costruito su LangGraph, integrato con SaferPlaces (piattaforma di simulazione alluvioni). Gli agenti orchestrano tool geospaziali, modelli di alluvione e recupero dati attraverso un ciclo plan → confirm → execute.

---

## Architettura

```
MultiAgentGraph (LangGraph StateGraph)
├── REQUEST_PARSER          — analizza i messaggi → ParsedRequest
├── SUPERVISOR_AGENT        — genera l'ExecutionPlan (lista di {agent, goal})
├── SUPERVISOR_PLANNER_CONFIRM — approvazione umana del piano (human-in-the-loop)
├── SUPERVISOR_ROUTER       — instrada verso il subgraph specializzato o FINAL_RESPONDER
├── RETRIEVER_SUBGRAPH      — recupero dati radar DPC / previsioni Meteoblue
├── MODELS_SUBGRAPH         — simulazione alluvioni con SaferRain
└── FINAL_RESPONDER         — sintetizza la risposta finale all'utente
```

Ogni subgraph segue il pattern: `Agent → InvocationConfirm → Executor`.

**File chiave:**
- [src/saferplaces_multiagent/multiagent_graph.py](../src/saferplaces_multiagent/multiagent_graph.py) — costruttore del grafo (entry point)
- [src/saferplaces_multiagent/multiagent_node.py](../src/saferplaces_multiagent/multiagent_node.py) — classe base `MultiAgentNode`
- [src/saferplaces_multiagent/common/states.py](../src/saferplaces_multiagent/common/states.py) — `MABaseGraphState` TypedDict (stato centrale)
- [src/saferplaces_multiagent/ma/names.py](../src/saferplaces_multiagent/ma/names.py) — classe `NodeNames` (costanti dei nomi dei nodi)
- [src/saferplaces_multiagent/ma/prompts/](../src/saferplaces_multiagent/ma/prompts/) — prompt LLM versionati (`OrchestratorPrompts`)

---

## Comandi di sviluppo

```bash
# Installazione (dalla root del repo, con venv attivo)
pip install -e ".[dev,leafmap,cesium]"

# Avvio server LangGraph
langgraph dev --config src/saferplaces_multiagent/langgraph.json

# Avvio webapp Flask
flask --app src/saferplaces_multiagent/agent_interface/flask_server/app.py run --debug

# Shortcut Windows
__run_langgraph.bat
__run_flask.bat

# Eseguire un test specifico
python -m tests.run T001
```
