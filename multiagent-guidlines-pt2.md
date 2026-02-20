Dopo aver esaminato il codice in multiagent_graph.py e la cartella `ma`, ecco una comprensione strutturata del sistema:

## 🏗️ Architettura Generale

Il sistema è organizzato in **4 livelli**:

```
REQUEST_PARSER
      ↓
SUPERVISOR_SUBGRAPH (orchestrazione)
      ↓
┌─────────────┬──────────────┐
↓             ↓              ↓
RETRIEVER   MODELS      (altri agenti)
SUBGRAPH    SUBGRAPH
      ↓             ↓
      └─────────────┘
           ↓
    FINAL_RESPONDER
           ↓
         END
```

---

## 📋 Componenti Principali

### 1. **REQUEST_PARSER** (`chat/request_parser.py`)
- **Ruolo**: Analizza la richiesta dell'utente
- **Output**: Popola `parsed_request`, `additional_context` nello stato
- **Transizione**: → `SUPERVISOR_SUBGRAPH`

### 2. **SUPERVISOR_SUBGRAPH** (orchestratore)
Composto da 3 nodi:

#### **SupervisorAgent** (`orchestrator/supervisor.py`)
```python
# Funzione: Pianificazione e routing
- Legge: parsed_request, layer_registry, contexto
- Genera: ExecutionPlan (lista di step)
- Decide: quale agente specializzato chiamare
- Output nello stato:
  * plan: List[Dict] con goal, tool_hints, etc.
  * supervisor_next_node: "retriever_subgraph" | "models_subgraph" | END
```

#### **SupervisorPlannerConfirm**
```python
# Human-in-the-loop per la conferma del piano
- Modo: interrupt (attende conferma utente)
- Se rejected: torna a SupervisorAgent (replan)
- Se approved: procede a SupervisorRouter
```

#### **SupervisorRouter**
```python
# Routing condizionale basato su supervisor_next_node
- Switch su state["supervisor_next_node"]
- Invia a: RETRIEVER_SUBGRAPH | MODELS_SUBGRAPH | END
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
- Itera su tool_calls
- Per ogni tool:
  * Recupera tool dalla registry
  * Esegue con args
  * Cattura risultato
  * Aggrega result a state["tool_results"][step_X]

# Output:
- Aggiorna layer_registry con nuovi layer generati
- Messages con ToolMessage (risultati)
- current_step += 1
→ Ritorna a SUPERVISOR_SUBGRAPH per replan
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
# Esecuzione tool model
- Itera su tool_calls dell'invocation
- Per ogni tool:
  * Esegue il modello
  * Genera output layer (raster/vector)
  * Cattura risultato

# Output:
- Aggiorna layer_registry
- Aggiorna tool_results
- Messaggio di completamento
→ Ritorna a SUPERVISOR_SUBGRAPH
```

---

### 5. **FINAL_RESPONDER** (`chat/final_responder.py`)
```python
# Sintetizza risultati per l'utente
- Legge: messages, layer_registry, tool_results
- Genera: risposta linguistica
- Transizione: → END
```

---

## 🔄 Flusso di Stato Principale

```python
# Stato iniziale (MABaseGraphState)
{
    "messages": [user_message],
    "layer_registry": [],
    "parsed_request": {},
    "additional_context": {},
    "supervisor_next_node": None,
    "plan": None,
    "plan_confirmation": None,
    "current_step": 0,
    "tool_results": {}
}

# Dopo REQUEST_PARSER
{
    "parsed_request": {"intent": "...", "entities": []},
    "additional_context": {"relevant_layers": [...], ...}
}

# Dopo SUPERVISOR_AGENT
{
    "plan": [
        {"agent": "retriever_subgraph", "goal": "Retrieve DPC radar data", ...},
        {"agent": "models_subgraph", "goal": "Run flood simulation", ...}
    ],
    "supervisor_next_node": "retriever_subgraph",
    "plan_confirmation": "pending" → (interrupt) → "approved"|"rejected"
}

# Dopo RETRIEVER_EXECUTOR
{
    "layer_registry": [...nuovi layer da retriever...],
    "tool_results": {"step_0": [...]},
    "current_step": 1,
    "supervisor_next_node": None  # Reset per replan
}

# Ciclo: torna a SUPERVISOR_AGENT per step successivo
```

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
2. **Human-in-the-Loop**: Interrupt di conferma prima di eseguire tool
3. **Conditional Routing**: Basato su `supervisor_next_node` nello stato
4. **Layer Management**: Registry centralizzato di layer geospaziali riutilizzabili
5. **Tool Composition**: Agenti specializzati con tool specifici (DPC, meteo, modelli)
6. **Stateful Execution**: Tutto tracciato in `tool_results` per auditability

---

## 🔗 Collegamenti Principali

| File | Responsabilità |
|------|-----------------|
| `orchestrator/supervisor.py` | Pianificazione, routing, agent registry |
| `specialized/safercast_agent.py` | Retriever (DPC, Meteoblue, ICON2I) |
| `specialized/models_agent.py` | Modelli (flood, fire simulation) |
| `specialized/layers_agent.py` | Gestione layer registry |
| `chat/request_parser.py` | Parsing richiesta utente |
| `chat/final_responder.py` | Sintesi finale risposta |