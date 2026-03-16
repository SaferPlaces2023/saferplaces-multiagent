# Functional Spec — Core Multiagent System

> **Tipo**: Vivente — aggiornare al completamento di ogni PLN che modifica le feature elencate.
> **Namespace**: `F###` (vedere [`docs/index.md`](index.md) per il registro completo).

---

## F001 — Request Parsing

**Descrizione**: Analisi della richiesta utente e inizializzazione del ciclo di stato.

**Componente**: `chat/request_parser.py` → nodo `REQUEST_PARSER`

**Comportamento**:
- Chiama `StateManager.initialize_new_cycle()` — resetta plan, parsed_request, tool_results, agent state
- Parsa la richiesta utente con LLM → popola `parsed_request`, `additional_context`
- Transizione automatica verso `SUPERVISOR_SUBGRAPH`

**State mutations**:
| Campo | Valore post-parse |
|---|---|
| `parsed_request` | oggetto strutturato con intent, location, time, ecc. |
| `plan` | `None` (reset) |
| `tool_results` | `{}` (reset) |
| `retriever_invocation` | `None` (reset) |
| `models_invocation` | `None` (reset) |
| `additional_context.relevant_layers.is_dirty` | `True` |

---

## F002 — Supervisor Planning

**Descrizione**: Pianificazione multi-step dell'esecuzione e routing verso gli agenti specializzati.

**Componente**: `orchestrator/supervisor.py` → subgraph `SUPERVISOR_SUBGRAPH`

**Nodi del subgraph**:

### SupervisorAgent
- Legge: `parsed_request`, `layer_registry`, `additional_context`
- Genera: `ExecutionPlan` (lista ordinata di step con `agent`, `goal`)
- Scrive: `plan: List[Dict]`, `current_step: 0`, `plan_confirmation: "pending"`

### SupervisorPlannerConfirm
- Human-in-the-loop (interrupt opzionale)
- `enabled=False` → auto-approva il piano
- `enabled=True` → interrupt per conferma utente; se rejected torna a SupervisorAgent

### SupervisorRouter
- Aggiorna `additional_context` se `is_dirty` (chiama LayersAgent per context refresh)
- Determina il prossimo nodo: `RETRIEVER_SUBGRAPH` | `MODELS_SUBGRAPH` | `FINAL_RESPONDER`
- Chiama `StateManager.initialize_specialized_agent_cycle(state, agent_type)`

---

## F003 — Data Retriever Agent

**Descrizione**: Recupero dati meteorologici/climatici da sorgenti esterne.

**Componente**: `specialized/safercast_agent.py` → subgraph `RETRIEVER_SUBGRAPH`

**Nodi**:

| Nodo | Ruolo |
|---|---|
| `DataRetrieverAgent` | Invoca LLM con tool DPC/Meteoblue; se tool_calls → Confirm |
| `DataRetrieverInvocationConfirm` | Human-in-the-loop: applica inference → validation → approved/rejected |
| `DataRetrieverExecutor` | Esegue tool, aggiorna `layer_registry`, registra `tool_results[step_X]` |

**Tool disponibili**: `DPCRetrieverTool`, `MeteoblueRetrieverTool`, `ICON2IRetrieverTool`, `ICON2IIngestorTool`

**Output di ogni esecuzione**:
- `layer_registry` aggiornato con nuovi layer
- `tool_results["step_X"]` con snapshot della tool call
- `current_step` incrementato
- `is_dirty = True` (segnala necessità di context refresh)

---

## F004 — Models Agent

**Descrizione**: Esecuzione di simulazioni ambientali (alluvioni, ecc.).

**Componente**: `specialized/models_agent.py` → subgraph `MODELS_SUBGRAPH`

**Nodi**:

| Nodo | Ruolo |
|---|---|
| `ModelsAgent` | Invoca LLM con tool di simulazione; se tool_calls → Confirm |
| `ModelsInvocationConfirm` | Human-in-the-loop: inference → validation → approved/rejected |
| `ModelsExecutor` | Esegue simulazione, aggiorna `layer_registry`, registra `tool_results[step_X]` |

**Tool disponibili**: `SaferRainTool` (flood simulation)

---

## F005 — Final Responder

**Descrizione**: Sintesi della risposta finale all'utente e cleanup dello stato.

**Componente**: `chat/final_responder.py` → nodo `FINAL_RESPONDER`

**Comportamento**:
- Legge: `messages`, `layer_registry`, `tool_results`, `parsed_request`
- Genera risposta linguistica via LLM
- Chiama `StateManager.cleanup_on_final_response()`

**State lifecycle post-cleanup**:
| Campo | Azione |
|---|---|
| `plan`, `tool_results`, `agent state*` | Reset |
| `layer_registry`, `user_drawn_shapes` | **Mantenuti** (persistent across turns) |
| `user_id`, `project_id` | **Mantenuti** |
| `is_dirty` | Reset |

---

## F006 — State Lifecycle Management

**Descrizione**: Gestione centralizzata del ciclo di vita dello stato tramite `StateManager`.

**Componente**: `common/states.py`

**Metodi**:

| Metodo | Chiamato da | Effetto |
|---|---|---|
| `initialize_new_cycle(state)` | `REQUEST_PARSER` | Reset completo planning + agent state |
| `initialize_specialized_agent_cycle(state, agent_type)` | `SupervisorRouter` | Reset invocation, step counter, confirmation per agente |
| `mark_agent_step_complete(state, agent_type)` | Executor nodes | Incrementa `{agent_type}_current_step` |
| `cleanup_on_final_response(state)` | `FINAL_RESPONDER` | Reset temporaneo, mantiene persistente |

---

## F007 — Tool Inference + Validation Pattern

**Descrizione**: Pattern architetturale per il completamento e la validazione degli argomenti tool prima dell'esecuzione.

**Componenti**: `ma/specialized/tools/_inferrers.py`, `ma/specialized/tools/_validators.py`

**Flusso nel nodo Confirm**:
1. **INFERENCE** (prima): applica `_set_args_inference_rules()` con `_graph_state` in kwargs → riempie args mancanti con defaults context-aware
2. **VALIDATION** (dopo): applica `_set_args_validation_rules()` sugli args completi → ritorna `Optional[str]` (None = OK)
3. Se validation OK → `approved` → Executor

**Signature**:
```python
# Inferrer
def infer_X(**kwargs) -> Any:
    state = kwargs.pop('_graph_state', None)
    # usa state.layer_registry, parsed_request, project_id, ecc.
    return valore_inferito

# Validator
def validate_X(**kwargs) -> Optional[str]:
    if condizione_invalida:
        return "Errore: descrizione"
    return None
```

---

## F008 — Prompt Override Testing

**Descrizione**: Pattern per testare il comportamento del grafo al variare di un prompt LLM, senza modificare il codice sorgente.

**Motivazione**: I metodi `stable()` di `OrchestratorPrompts` sono `@staticmethod` chiamati a runtime dentro i nodi del grafo — non all'import né alla costruzione del grafo. Questo li rende sovrascrivibili via `unittest.mock.patch.object` per la durata di un test, senza effetti collaterali.

**Pattern**:
```python
from unittest.mock import patch
from saferplaces_multiagent.ma.prompts.supervisor_agent_prompts import OrchestratorPrompts
from saferplaces_multiagent.ma.prompts import Prompt

def _my_override() -> Prompt:
    return Prompt({"title": "...", "description": "...", "command": "", "message": "..."})

with patch.object(OrchestratorPrompts.MainContext, "stable", _my_override):
    run_tests(MESSAGES, result_file=result_file)
```

**Limiti**: se in futuro i prompt venissero cachati alla costruzione del grafo, il meccanismo smetterebbe di funzionare — occorrerebbe passare i prompt come parametri a `GraphInterface`.

**Esempio**: `tests/T006_prompt_override.py` — supervisor forzato a produrre sempre un piano vuoto; risultato in `tests/result/T006.md`.

---

## Topologia grafo principale

```
START → REQUEST_PARSER → SUPERVISOR_SUBGRAPH
                              ↓ (conditional)
                    ┌─────────┴──────────┐
                    ↓                    ↓
           RETRIEVER_SUBGRAPH    MODELS_SUBGRAPH
                    └─────────┬──────────┘
                              ↓
                    SUPERVISOR_SUBGRAPH (loop)
                              ↓ (plan esaurito)
                    FINAL_RESPONDER → END
```
