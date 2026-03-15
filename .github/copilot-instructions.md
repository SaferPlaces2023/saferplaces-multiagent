# SaferPlaces Multiagent — Istruzioni per Copilot

## Ordine di priorità delle istruzioni

1. `.github/copilot-instructions.md` — architettura e comandi essenziali
2. `.github/instructions/*.md` — istruzioni specifiche per area (caricate con `applyTo`):
   - `coding-standards.instructions.md` → `**/*.py` — naming, linting, nodi, tool, state
   - `services.instructions.md` → `src/**` — S3, Flask, LangGraph, testing
   - `planning.instructions.md` → `implementations/**` — workflow PLN-###, _plan-todo
   - `docs.instructions.md` → `docs/**` — namespace ID, stile documentazione
3. `implementations/_plan-todo.md` + `PLN-*.md` — open items e piani attivi
4. `implementations/archive/` — piani completati (solo consultazione)
5. `docs/index.md` — fonte di verità per il namespace degli ID (`F###`, `PLN-###`, …)
6. `docs/multiagent-guidlines*.md` — linee guida di design del sistema
7. `tests/tests.json` + `tests/result/` — comportamento atteso verificato
8. `README.md` — overview generale del progetto

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
