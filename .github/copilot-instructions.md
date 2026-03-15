# SaferPlaces Multiagent — Istruzioni per Copilot

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

Ogni subgraph segue lo stesso pattern: `Agent → InvocationConfirm → Executor`.

**File chiave:**
- [src/saferplaces_multiagent/multiagent_graph.py](../src/saferplaces_multiagent/multiagent_graph.py) — costruttore del grafo (entry point)
- [src/saferplaces_multiagent/multiagent_node.py](../src/saferplaces_multiagent/multiagent_node.py) — classe base `MultiAgentNode` (tutti i nodi ereditano da questa)
- [src/saferplaces_multiagent/common/states.py](../src/saferplaces_multiagent/common/states.py) — `MABaseGraphState` TypedDict (stato centrale)
- [src/saferplaces_multiagent/ma/names.py](../src/saferplaces_multiagent/ma/names.py) — classe `NodeNames` (tutte le costanti dei nomi dei nodi)
- [src/saferplaces_multiagent/ma/prompts/](../src/saferplaces_multiagent/ma/prompts/) — prompt LLM versionati tramite `OrchestratorPrompts`

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

---

## Creare un nuovo nodo

Tutti i nodi **devono** estendere `MultiAgentNode` e implementare `run()`:

```python
from saferplaces_multiagent.multiagent_node import MultiAgentNode
from saferplaces_multiagent.common.states import MABaseGraphState

class MyNode(MultiAgentNode):
    def __init__(self):
        super().__init__(name="my_node")

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        # implementare la logica qui
        return state
```

- `_pre_run` / `_post_run` sono gestiti automaticamente dalla classe base
- `_post_run` scrive lo stato su `__state_log__user_id=X__project_id=Y.json` (gitignored)
- Le costanti dei nomi dei nodi vanno in `ma/names.py` → classe `NodeNames`

---

## Creare un nuovo tool

I tool risiedono in `ma/specialized/tools/`. Estendere `BaseTool` da `nodes/base/base_agent_tool.py`:

```python
from saferplaces_multiagent.nodes.base.base_agent_tool import BaseTool
from pydantic import BaseModel

class MyToolInput(BaseModel):
    param: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "..."
    args_schema = MyToolInput

    def _execute(self, **kwargs):
        # implementare qui
        return result
```

- Regole di validazione e inferenza degli input: `_validators.py` / `_inferrers.py` nella cartella `tools/`
- Registrare il tool nel `ToolRegistry` dell'agente di riferimento

---

## Prompt

I prompt sono versionati e risiedono in `ma/prompts/supervisor_agent_prompts.py`.

- Usare `OrchestratorPrompts.<Section>.<method>()` per recuperare un prompt
- Ogni metodo restituisce un dataclass `Prompt` con `.to(MessageClass)` per LangChain
- Aggiungere nuove versioni come nuovi metodi statici (`v001()`, `v002()`, …) — non sovrascrivere mai `stable()`

---

## State

`MABaseGraphState` (TypedDict) è l'unico oggetto di stato che attraversa l'intero grafo.

Campi chiave:
| Campo | Tipo | Scopo |
|---|---|---|
| `messages` | `list[AnyMessage]` | Storia completa della conversazione (append-only) |
| `user_id` / `project_id` | `str` | Identificatori di sessione |
| `layer_registry` | `list[dict]` | Layer geospaziali attivi |
| `parsed_request` | `dict` | `{intent, entities, raw_text}` generato da REQUEST_PARSER |
| `plan` | `list[dict]` | `[{agent, goal}, …]` generato da SUPERVISOR_AGENT |
| `plan_confirmation` | `str` | `"pending"` \| `"accepted"` \| `"rejected"` |
| `tool_results` | `dict` | Output accumulati dai tool |
| `supervisor_next_node` | `str` | Destinazione di routing impostata da SUPERVISOR_ROUTER |

`StateManager.initialize_new_cycle(state)` va chiamato in REQUEST_PARSER per pulire i dati del ciclo precedente. `StateManager.cleanup_on_final_response(state)` pulisce le chiavi di ciclo in FINAL_RESPONDER.

---

## Convenzioni

- **Nomi dei nodi:** costanti `snake_case` nella classe `NodeNames` (`ma/names.py`)
- **Nomi delle classi:** `PascalCase`
- **Chiavi dello state:** `snake_case`
- **Costanti:** `SCREAMING_SNAKE_CASE`
- **Metodi privati:** prefisso `_`
- **Linting:** ruff (E, F, I, D, UP, T201) — eseguire prima di ogni commit
- **Stile dei commit:** Conventional Commits (`feat`, `fix`, `chore`, `refactor`, …)
- **Gestione dei path:** usare `utils.normpath()` per compatibilità cross-platform; S3 tramite `common/s3_utils.py`

---

## Storage S3

I file utente sono salvati in:
```
s3://saferplaces.co/SaferPlaces-Agent/dev/user=<USER_ID>/project=<PROJECT_ID>/
```

Usare `common/s3_utils.py` per tutte le operazioni S3. Non hardcodare mai i nomi dei bucket.

---

## Testing

I test sono definiti in `tests/tests.json` con ID (T001, T002, …). Ogni test invia una sequenza di messaggi al grafo.

```bash
python -m tests.run T001
```

I risultati vengono salvati/confrontati in `tests/result/`. Usare gli helper di `tests/_utils.py` per nuovi test.

---

## Cosa NON fare

- **Non** committare direttamente su `main` (il pre-commit hook lo blocca)
- **Non** hardcodare i nomi dei modelli LLM — usare `common/utils._base_llm()`
- **Non** aggiungere chiavi direttamente a `MABaseGraphState` senza aggiornare `StateManager`
- **Non** bypassare la classe base `MultiAgentNode` per nuovi nodi
- **Non** sovrascrivere un prompt `stable()` — aggiungere un nuovo metodo versionato
- **Non** committare i file `__state_log__*.json` (gitignored)
