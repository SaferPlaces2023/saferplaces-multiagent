---
applyTo: "**/*.py"
---

# Coding Standards — SaferPlaces Multiagent

## Naming Conventions

- **Node names:** costanti `snake_case` nella classe `NodeNames` (`ma/names.py`)
- **Class names:** `PascalCase`
- **State keys:** `snake_case`
- **Constants:** `SCREAMING_SNAKE_CASE`
- **Private methods:** prefisso `_`

## Linting

Ruff è configurato con le regole `E, F, I, D, UP, T201` — eseguire prima di ogni commit.

## Commit style

Conventional Commits: `feat`, `fix`, `chore`, `refactor`, …

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

## Prompt

I prompt sono organizzati in moduli dedicati per agente in `ma/prompts/`. Ogni modulo segue una gerarchia nidificata di classi con metodi `stable()` e versioni alternative.

### Architettura

- **Modulo base**: `ma/prompts/<agent>_prompts.py` — es. `supervisor_agent_prompts.py`
- **Classe principale**: `<Agent>Prompts` — es. `OrchestratorPrompts`
- **Sezioni logiche**: classi PascalCase nidificate — es. `MainContext`, `Plan`, `PlanConfirmation`
- **Metodi statici**: `stable()` (default/produzione), `v001()`, `v002()`, … (versioni A/B test)

### Dataclass Prompt

Ogni metodo restituisce un dataclass `Prompt` (in `ma/prompts/__init__.py`):

| Campo | Scopo |
|---|---|
| `title` | Nome mnemonico del prompt |
| `description` | Descrizione breve del ruolo |
| `command` | Flag per comandi speciali (solitamente vuoto) |
| `message` | Testo completo del prompt per l'LLM |

**Conversione LangChain**:
```python
prompt = OrchestratorPrompts.MainContext.stable()
message = prompt.to(SystemMessage)  # → SystemMessage(content=prompt.message)
```

### Pattern: Signature dei metodi

**Senza stato** (prompt generici):
```python
class MainContext:
    @staticmethod
    def stable() -> Prompt:
        return Prompt({...})
```

**Con stato** (context-aware):
```python
class CreatePlan:
    @staticmethod
    def stable(state: MABaseGraphState, **kwargs) -> Prompt:
        parsed_request = state.get("parsed_request")
        return Prompt({...})
```

**Con parametri aggiuntivi**:
```python
class RequestExplanation:
    @staticmethod
    def stable(state: MABaseGraphState, user_question: str, **kwargs) -> Prompt:
        return Prompt({...})
```

### Convenzioni di naming

| Elemento | Convenzione | Esempio |
|---|---|---|
| Classe principale | `<Agent>Prompts` | `OrchestratorPrompts`, `DataRetrieverPrompts` |
| Classi di sezione | PascalCase semantico | `MainContext`, `Plan`, `PlanConfirmation` |
| Metodi statici | `stable()` / `v###()` | `stable()`, `v001()`, `v002()` |
| Costanti | `SCREAMING_SNAKE_CASE` | `AGENT_REGISTRY`, `PLAN_RESPONSE_LABELS` |
| Helper privati | prefisso `_`, snake_case | `_format_plan_for_display()` |

### Composizione gerarchica

I prompt complessi sono costruiti incrementalmente:

```
OrchestratorPrompts
├── MainContext.stable()              # System role
├── Plan.CreatePlan.stable(state)     # Task-specific context
├── Plan.PlanConfirmation.*           # Sub-context
└── …
```

Ogni livello aggiunge responsabilità e contesto.

### Versionamento

- **`stable()`**: versione in produzione (default) — metodo chiamato a runtime nei nodi **mai all'import**
- **`v001()`, `v002()`, …**: versioni alternative per test A/B — override via `unittest.mock.patch.object` (vedi Testing in `services.instructions.md`)
- **Importante**: la chiamata happening a runtime consente il patch dinamico senza modificare il codice sorgente

### Best practices

- **Non** sovrascrivere un prompt `stable()` — aggiungere un nuovo metodo versionato
- **Non** hardcodare testo nei nodi — usare sempre `OrchestratorPrompts.<Path>.stable(state)`
- Raggruppare prompts correlati nella stessa sezione per riusabilità
- Usare costanti (`AGENT_REGISTRY`, etc.) per liste riutilizzabili

## State (`MABaseGraphState`)

`MABaseGraphState` (TypedDict) è l'unico oggetto di stato che attraversa l'intero grafo.

| Campo | Tipo | Scopo |
|---|---|---|
| `messages` | `list[AnyMessage]` | Storia conversazione (append-only) |
| `user_id` / `project_id` | `str` | Identificatori di sessione |
| `layer_registry` | `list[dict]` | Layer geospaziali attivi |
| `parsed_request` | `dict` | `{intent, entities, raw_text}` da REQUEST_PARSER |
| `plan` | `list[dict]` | `[{agent, goal}, …]` da SUPERVISOR_AGENT |
| `plan_confirmation` | `str` | `"pending"` \| `"accepted"` \| `"rejected"` |
| `tool_results` | `dict` | Output accumulati dai tool |
| `supervisor_next_node` | `str` | Destinazione di routing da SUPERVISOR_ROUTER |

- `StateManager.initialize_new_cycle(state)` → chiamare in REQUEST_PARSER
- `StateManager.cleanup_on_final_response(state)` → chiamare in FINAL_RESPONDER
- **Non** aggiungere chiavi a `MABaseGraphState` senza aggiornare `StateManager`

## Path & LLM

- Usare `utils.normpath()` per compatibilità cross-platform
- S3 tramite `common/s3_utils.py` — non hardcodare mai bucket names
- Non hardcodare i nomi dei modelli LLM — usare `common/utils._base_llm()`

## Cosa NON fare

- **Non** committare direttamente su `main` (il pre-commit hook lo blocca)
- **Non** bypassare la classe base `MultiAgentNode` per nuovi nodi
- **Non** sovrascrivere un prompt `stable()` — aggiungere un nuovo metodo versionato
- **Non** committare i file `__state_log__*.json` (gitignored)
