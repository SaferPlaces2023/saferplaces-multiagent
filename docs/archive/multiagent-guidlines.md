Dopo aver esaminato il codice in multiagent_graph.py e la cartella `ma`, ecco una comprensione strutturata del sistema:

## 🏗️ Architettura Generale

Il sistema è organizzato in **5 livelli principali**:

```
REQUEST_PARSER (+ initialize_new_cycle)
      ↓
SUPERVISOR_SUBGRAPH (orchestrazione)
      ↓
┌─────────────┬──────────────┐
↓             ↓              ↓
RETRIEVER   MODELS      (altri agenti)
SUBGRAPH    SUBGRAPH
(+ init_agent)(+ init_agent)
      ↓             ↓
      └─────────────┘
           ↓
    FINAL_RESPONDER (+ cleanup_on_final_response)
           ↓
         END
```

---

## 📋 Componenti Principali

### 1. **REQUEST_PARSER** (`chat/request_parser.py`)
- **Ruolo**: Analizza la richiesta dell'utente e inizializza ciclo di stato
- **Actions**:
  1. Chiama `StateManager.initialize_new_cycle()` → resetta plan, parsed_request, tool_results, agent state
  2. Parsa richiesta utente con LLM
  3. Popola `parsed_request`, `additional_context`
- **Output**: Stato pronto per planning
- **Transizione**: → `SUPERVISOR_SUBGRAPH`

### 2. **SUPERVISOR_SUBGRAPH** (orchestratore)
Composto da 3 nodi:

#### **SupervisorAgent** (`orchestrator/supervisor.py`)
```python
# Funzione: Pianificazione multi-step
- Legge: parsed_request, layer_registry, additional_context
- Genera: ExecutionPlan (lista ordinata di step)
- Decide: quale agente specializzato per ogni step
- Output nello stato:
  * plan: List[Dict] con agent name, goal, etc.
  * current_step: 0 (inizio ciclo)
  * plan_confirmation: "pending"
```

#### **SupervisorPlannerConfirm**
```python
# Human-in-the-loop per conferma del piano
- Se enabled=False: auto-approva il piano
- Se enabled=True: interrupt per chiedere conferma utente
- Se rejected: torna a SupervisorAgent (replan)
- Se approved: procede a SupervisorRouter
```

#### **SupervisorRouter**
```python
# Routing condizionale e context refresh
- Chiama _update_additional_context():
  * Controlla is_dirty flag in relevant_layers
  * Se true: chiama LayersAgent per refresh
  * Aggiorna state con nuovi layer
- Chiama _determine_next_node():
  * Calcola prossimo agente da eseguire
  * Chiama StateManager.initialize_specialized_agent_cycle(state, agent_type)
    → Resetta invocation*, current_step per l'agente
  * Ritorna: RETRIEVER_SUBGRAPH | MODELS_SUBGRAPH | FINAL_RESPONDER
```

---

### 3. **RETRIEVER_SUBGRAPH** (agente dati)

Composto da 3 nodi, implementati in `specialized/safercast_agent.py`:

#### **DataRetrieverAgent**
```python
# Scopo: Recuperare dati meteorologici/climatici
# Tools disponibili:
  - DPCRetrieverTool (radar prodotti)
  - MeteoblueRetrieverTool (previsioni)
  - ICON2IRetrieverTool (osservazioni)
  - ICON2IIngestorTool (ingestion)

# Flusso:
1. Riceve state con plan[current_step].goal
2. Invoca LLM con i tool disponibili
3. Se tool_calls: → DataRetrieverInvocationConfirm
4. Se no tool_calls: ritorna stesso stato
```

#### **DataRetrieverInvocationConfirm**
```python
# Human-in-the-loop (interrupt)
- Mostra tool calls proposte
- Attende conferma: 'approved' | 'rejected'
- Se rejected: → DataRetrieverAgent (re-invoke)
- Se approved: → DataRetrieverExecutor
```

#### **DataRetrieverExecutor**
```python
# Esecuzione tool e update stato
- Itera su pending_tool_calls:
  * Per ogni tool:
    1. Esegue: tool._execute(**args)
       (args già validated+inferred da Confirm step)
    2. Formatta: tool-specific response message
    3. Aggiunge: layer a registry via LayersAgent
    4. Registra: result in tool_results[step_X]
    5. Marca: StateManager.mark_agent_step_complete(state, "retriever")
       → Incrementa retriever_current_step
- Aggrega: ToolMessage[] in state["messages"]
- Output finale:
  * layer_registry aggiornato con nuovi layer
  * tool_results con snapshot di ogni tool call
  * current_step incrementato (plan progress)
  * is_dirty flag di relevant_layers settato a True (necessità refresh context)
→ Ritorna a SUPERVISOR_SUBGRAPH per replan prossimo step
```

---

### 4. **MODELS_SUBGRAPH** (agente simulazioni)

Composto da 3 nodi, implementati in `specialized/models_agent.py`:

#### **ModelsAgent**
```python
# Scopo: Eseguire modelli ambientali (flood, fire, etc.)
# Tools disponibili:
  - SaferRainTool (flood rainfall simulation)
  - (altri tool per futuri modelli)

# Flusso:
1. Riceve plan[current_step] con goal
2. Reperisce contesto layer da LayersAgent (opzionale)
3. Invoca LLM con i model tool
4. Se tool_calls: → ModelsInvocationConfirm
5. Se no tool_calls: ritorna con messaggio
```

#### **ModelsInvocationConfirm**
```python
# Human-in-the-loop (interrupt)
- Funzione identica a DataRetrieverInvocationConfirm
- Se rejected: → ModelsAgent
- Se approved: → ModelsExecutor
```

#### **ModelsExecutor**
```python
# Esecuzione simulazione e update stato
- Itera su pending_tool_calls:
  * Per ogni tool:
    1. Esegue: tool._execute(**args)
       (args già validated+inferred da Confirm step)
    2. Formatta: tool-specific response (SaferRain, etc.)
    3. Aggiunge: output layer a registry via LayersAgent
    4. Registra: result in tool_results[step_X]
    5. Marca: StateManager.mark_agent_step_complete(state, "models")
       → Incrementa models_current_step
- Aggrega: ToolMessage[] in state["messages"]
- Output finale:
  * layer_registry aggiornato con output layer dal modello
  * tool_results con snapshot di simulazione
  * current_step incrementato (plan progress)
  * is_dirty flag settato a True (nuovo layer generato)
→ Ritorna a SUPERVISOR_SUBGRAPH
```

---

### 5. **FINAL_RESPONDER** (`chat/final_responder.py`)
```python
# Sintetizza risultati per l'utente
- Legge: messages, layer_registry, tool_results, parsed_request
- Genera: risposta linguistica via LLM
- Actions:
  1. Invoca LLM con context (parsed_request, plan, tool_results)
  2. Genera risposta user-facing
  3. Salva resposta in state["messages"]
  4. Chiama StateManager.cleanup_on_final_response()
     → Resetta: plan, tool_results, agent state*
     → Mantiene: layer_registry, user_drawn_shapes (persistent)
     → Resetta: is_dirty flag
- Transizione: → END
```

---

## 🔄 State Lifecycle Management

Il sistema gestisce il ciclo di vita dello stato attraverso **StateManager** (`common/states.py`):

### **1. New Request Cycle** (REQUEST_PARSER)
```python
StateManager.initialize_new_cycle(state):
  # Clear planning state
  state['parsed_request'] = None
  state['plan'] = None
  state['current_step'] = None
  state['plan_confirmation'] = None
  
  # Clear tool results
  state['tool_results'] = {}
  
  # Clear agent state (retriever + models)
  state['retriever_invocation'] = None
  state['retriever_current_step'] = 0
  state['models_invocation'] = None
  state['models_current_step'] = 0
  
  # Reset context dirty flag
  state['additional_context']['relevant_layers']['is_dirty'] = True
```

### **2. Agent Cycle** (SUPERVISOR_ROUTER)
```python
StateManager.initialize_specialized_agent_cycle(state, agent_type):
  # Reset agent invocation state
  state[f'{agent_type}_invocation'] = None
  state[f'{agent_type}_current_step'] = 0
  state[f'{agent_type}_confirmation'] = None
  state[f'{agent_type}_reinvocation_request'] = None
```

### **3. Step Completion** (EXECUTOR)
```python
StateManager.mark_agent_step_complete(state, agent_type):
  # Increment step counter
  state[f'{agent_type}_current_step'] += 1
```

### **4. Final Cleanup** (FINAL_RESPONDER)
```python
StateManager.cleanup_on_final_response(state):
  # Clear all temporary request state
  state['parsed_request'] = None
  state['plan'] = None
  state['tool_results'] = {}
  
  # Clear agent state
  state['retriever_invocation'] = None
  state['models_invocation'] = None
  
  # Maintain persistent data
  # state['layer_registry'] - KEPT
  # state['user_drawn_shapes'] - KEPT
  # state['user_id'], state['project_id'] - KEPT
```

---

## 🔄 Flusso di Stato Completo (Ciclo Multi-Richiesta)

```
========== USER REQUEST 1 ==========

REQUEST_PARSER: StateManager.initialize_new_cycle()
  ↓
SUPERVISOR_AGENT: Genera plan con N step
  ↓
SUPERVISOR_ROUTER: initialize_specialized_agent_cycle("retriever")
  ↓
RETRIEVER_AGENT → RETRIEVER_CONFIRM → RETRIEVER_EXECUTOR
  ├─ mark_agent_step_complete("retriever")
  ├─ Aggiorna layer_registry
  └─ tool_results["step_0"] = [...]
  ↓
SUPERVISOR_ROUTER: initialize_specialized_agent_cycle("models")
  ↓
MODELS_AGENT → MODELS_CONFIRM → MODELS_EXECUTOR
  ├─ mark_agent_step_complete("models")
  ├─ Aggiorna layer_registry
  └─ tool_results["step_1"] = [...]
  ↓
SUPERVISOR_ROUTER: Plan esaurito, route to FINAL_RESPONDER
  ↓
FINAL_RESPONDER: StateManager.cleanup_on_final_response()
  └─ Resetta: plan, tool_results, agent state
  └─ Mantiene: layer_registry, user_drawn_shapes
  ↓
END

========== USER REQUEST 2 (multi-turn) ==========

REQUEST_PARSER: StateManager.initialize_new_cycle() AGAIN
  → layer_registry from request 1 persists!
  ↓
(Ciclo ripete...)
```

### State Mutations Map

| Punto | Mutazione | Stack |
|-------|-----------|-------|
| REQUEST_PARSER | `initialize_new_cycle()` | Parse → Plan |
| SUPERVISOR_ROUTER | `initialize_specialized_agent_cycle()` | Agent loop |
| RETRIEVER/MODELS_EXECUTOR | `mark_agent_step_complete()` | Tool execution |
| FINAL_RESPONDER | `cleanup_on_final_response()` | Response → Persistence |

---

## 🛠️ Tool System

### Organizzazione Tool
```
ma/specialized/tools/
├── dpc_retriever_tool.py        # Radar DPC
├── meteoblue_retriever_tool.py  # Meteo forecast
├── safer_rain_tool.py           # Flood simulation
└── _validators.py, _inferrers.py  # Helper
```

### Tipologia Tool

**DataRetrieverTool** (eredita BaseTool da LangChain):
```python
# Signature generico
def _run(self, bbox: str, time_range: str, ...) -> dict
  # Ritorna: {"layer": {...}, "data": "..."}
```

**ModelsExecutorTool** (eredita BaseTool):
```python
def _run(self, dem_layer: str, rainfall_mm: float, ...) -> dict
  # Ritorna: {"output_layer": "s3://...", "report": "..."}
```

---

## 🎯 LayersAgent (supporto)

Implementato in `specialized/layers_agent.py`:

```python
# Ruolo: Gestire il registry di layer (visualizzati e disponibili)
# Richiamato da:
  - SupervisorAgent (per contesto di planning)
  - ModelsAgent (per contesto di simulazione)

# Tool disponibili:
  - ListLayersTool()         # Lista layer disponibili
  - GetLayerTool()           # Dettagli di un layer
  - AddLayerTool()           # Aggiungi layer manualmente
  - RemoveLayerTool()        # Rimuovi layer
  - UpdateLayerTool()        # Update metadati
  - SearchByTypeTool()       # Filtra per tipo
  - BuildLayerFromPromptTool() # Genera layer da richiesta
  - ChooseLayerTool()        # Seleziona layer per input

# Caratteristica:
  - Esecuzione tool IMMEDIATA (non interrupt)
  - Aggiorna layer_registry inline
  - Ritorna tool_responses per contesto LLM
```

---

## 📊 State Graph Topology

```python
# Supervisor subgraph
START → SupervisorAgent → SupervisorPlannerConfirm ⇄ SupervisorRouter

# Retriever subgraph
START → DataRetrieverAgent → DataRetrieverInvocationConfirm ⇄ DataRetrieverExecutor

# Models subgraph
START → ModelsAgent → ModelsInvocationConfirm ⇄ ModelsExecutor

# Main graph
START → REQUEST_PARSER → SUPERVISOR_SUBGRAPH 
         ↓ (conditional)
         ├→ RETRIEVER_SUBGRAPH ──┐
         ├→ MODELS_SUBGRAPH ─────┼→ SUPERVISOR_SUBGRAPH (loop)
         └→ FINAL_RESPONDER ─→ END
                  ↑
         (quando plan esaurito)
```

---

## ✅ Caratteristiche Chiave

1. **Multi-step Planning**: Il supervisor crea un piano multi-step, eseguito sequenzialmente
2. **Human-in-the-Loop**: Interrupt di conferma prima di eseguire tool (DataRetrieverInvocationConfirm, ModelsInvocationConfirm)
3. **Conditional Routing**: Basato su `supervisor_next_node` nello stato
4. **Layer Management**: Registry centralizzato di layer geospaziali riutilizzabili con is_dirty flag per context refresh
5. **Tool Composition**: Agenti specializzati con tool specifici (DPC, Meteoblue, SaferRain)
6. **Stateful Execution**: Tutto tracciato in `tool_results` per auditability
7. **State Lifecycle Management**: StateManager centralizzato gestisce lifecycle completo (initialize→execute→cleanup)
8. **Inference + Validation Pattern**: Confirmation node applica inference prima di validazione, poi args son pronti per Executor
9. **Per-Request Cleanup**: Distingue persistent data (layer_registry, user_drawn_shapes, session) da temporary state

---

## 🔗 Collegamenti Principali

| File | Responsabilità |
|------|-----------------|
| `common/states.py` | **StateManager**: Ciclo di vita stato (init, mark, cleanup) |
| `orchestrator/supervisor.py` | Pianificazione, routing, agent registry, context refresh |
| `specialized/safercast_agent.py` | Retriever Agent + Confirm + Executor (DPC, Meteoblue) |
| `specialized/models_agent.py` | Models Agent + Confirm + Executor (SaferRain) |
| `specialized/layers_agent.py` | Gestione layer registry (addLayer, refreshLayers) |
| `chat/request_parser.py` | Parsing + StateManager.initialize_new_cycle() |
| `chat/final_responder.py` | Sintesi risposta + StateManager.cleanup_on_final_response() |