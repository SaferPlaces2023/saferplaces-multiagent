# G — Flusso di Esecuzione del Grafo Multi-Agente

> **Documento:** Specifica funzionale vivente del grafo LangGraph.
> Descrive topologia, ciclo di vita dello stato, percorsi di esecuzione e difetti noti.
>
> **Prefisso ID:** `G###` — riservato a questa specifica.
> **Ultimo aggiornamento:** Marzo 2026 — revisione completa post PLN-008/PLN-009.
>
> **File sorgente chiave:**
> - [`multiagent_graph.py`](../src/saferplaces_multiagent/multiagent_graph.py)
> - [`common/states.py`](../src/saferplaces_multiagent/common/states.py)
> - [`ma/orchestrator/supervisor.py`](../src/saferplaces_multiagent/ma/orchestrator/supervisor.py)
> - [`ma/chat/request_parser.py`](../src/saferplaces_multiagent/ma/chat/request_parser.py)
> - [`ma/chat/final_responder.py`](../src/saferplaces_multiagent/ma/chat/final_responder.py)
> - [`ma/specialized/safercast_agent.py`](../src/saferplaces_multiagent/ma/specialized/safercast_agent.py)
> - [`ma/specialized/models_agent.py`](../src/saferplaces_multiagent/ma/specialized/models_agent.py)
> - [`ma/prompts/supervisor_agent_prompts.py`](../src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py)

---

## G001 — Topologia del Grafo Principale

Il grafo principale (`build_multiagent_graph`) è un `StateGraph` su `MABaseGraphState`.
Ha **5 nodi di primo livello**, tre dei quali sono subgraph compilati.

```
START
  │
  ▼
REQUEST_PARSER
  │
  ▼
SUPERVISOR_SUBGRAPH ◄────────────────────────────────────┐
  │                                                      │
  │ (conditional su supervisor_next_node)                │
  ├──► RETRIEVER_SUBGRAPH ──────────────────────────────►┤
  ├──► MODELS_SUBGRAPH ─────────────────────────────────►┤
  ├──► FINAL_RESPONDER ──► END                           │
  └──► END                          loop su ogni step ───┘
```

### Archi condizionali del grafo principale

| Sorgente | Condizione | Destinazione |
|---|---|---|
| `SUPERVISOR_SUBGRAPH` | `supervisor_next_node == "retriever_subgraph"` | `RETRIEVER_SUBGRAPH` |
| `SUPERVISOR_SUBGRAPH` | `supervisor_next_node == "models_subgraph"` | `MODELS_SUBGRAPH` |
| `SUPERVISOR_SUBGRAPH` | `supervisor_next_node == "final_responder"` | `FINAL_RESPONDER` |
| `SUPERVISOR_SUBGRAPH` | `supervisor_next_node == END` | `END` |
| `RETRIEVER_SUBGRAPH` | (fisso) | `SUPERVISOR_SUBGRAPH` |
| `MODELS_SUBGRAPH` | (fisso) | `SUPERVISOR_SUBGRAPH` |
| `FINAL_RESPONDER` | (fisso) | `END` |

---

## G002 — Topologia dei Subgraph

### Supervisor Subgraph

```
START ──► SUPERVISOR_AGENT ──► SUPERVISOR_PLANNER_CONFIRM
                                       │
     plan_confirmation == "rejected" AND NOT plan_aborted?
                     ┌─────────────────┴────────────────┐
                   True                               False
                     │                                   │
             SUPERVISOR_AGENT                    SUPERVISOR_ROUTER
             (replan loop)                        (EXIT subgraph)
```

**Nota critica:** `plan_aborted = True` (abort utente) fa uscire dal loop
verso `SUPERVISOR_ROUTER`, che emette `supervisor_next_node = FINAL_RESPONDER`.
Senza `plan_aborted`, qualsiasi `plan_confirmation == "rejected"` rientra in `SUPERVISOR_AGENT`.
La condizione è: `lambda state: state.get('plan_confirmation') == 'rejected' and not state.get('plan_aborted')`.

### Retriever Subgraph

```
START ──► RETRIEVER_AGENT ──► RETRIEVER_INVOCATION_CONFIRM
                                       │
              retriever_invocation_confirmation == "rejected"?
                     ┌─────────────────┴──────────────────┐
                   True                                 False
                     │                                     │
             RETRIEVER_AGENT                       RETRIEVER_EXECUTOR
             (reinvocazione)                        (EXIT subgraph)
```

### Models Subgraph

Struttura identica al Retriever Subgraph con nodi corrispondenti:
`MODELS_AGENT → MODELS_INVOCATION_CONFIRM → MODELS_EXECUTOR`

**Nota:** La condizione sull'arco usa la chiave `models_invocation_confirmation`
(non `models_confirmation`). Stessa convezione per retriever.

---

## G003 — Descrizione dei Nodi

### REQUEST_PARSER

**File:** `ma/chat/request_parser.py`  
**Classe:** `RequestParser`  
**Prompt:** `RequestParserPrompts.MainContext.stable()`

**Ruolo:** Punto di ingresso di ogni ciclo di richiesta. Analizza l'ultimo messaggio utente, estrae intento ed entità in modo strutturato.

**Logica di esecuzione:**

1. Chiama `StateManager.initialize_new_cycle(state)` — resetta tutto lo stato temporaneo del ciclo precedente (plan, tool_results, agent state, is_dirty).
2. Se `messages` è vuoto → ritorna senza elaborazione.
3. Se l'ultimo messaggio non è `HumanMessage` → ritorna senza elaborazione (guard per resume dopo interrupt).
4. Costruisce `invoke_messages = [*state["messages"][:-1], SystemMessage(context_prompt), HumanMessage(latest_msg)]`.  
   **Importante:** include l'intera cronologia precedente, permettendo al parser di inferire il contesto da turni precedenti.
5. Invoca LLM con `with_structured_output(ParsedRequest)` → `{intent, entities, raw_text}`.
6. Imposta `state["parsed_request"]` e `state["awaiting_user"] = False`.

**Dipendenza critica:** La qualità dell'intento estratto determina la qualità dell'intero piano a valle. Per richieste di follow-up ("fai lo stesso per Roma"), la corretta estrazione dipende dalla prompt quality e da quanto il parser LLM sfrutti la cronologia inclusa.

**Stato in uscita:**

| Campo | Valore |
|---|---|
| `parsed_request` | `{intent, entities, raw_text}` |
| `plan` | `None` |
| `tool_results` | `{}` |
| `awaiting_user` | `False` |
| `plan_aborted` | `False` |
| `additional_context.relevant_layers.is_dirty` | `True` |

---

### SUPERVISOR_AGENT

**File:** `ma/orchestrator/supervisor.py`  
**Classe:** `SupervisorAgent`  
**Prompt:** `OrchestratorPrompts.MainContext.stable()` + prompt di piano contestuale

**Ruolo:** Genera il piano di esecuzione multi-step. Viene rientrato ad ogni ciclo SUPERVISOR_SUBGRAPH, ma normalmente salta se il piano è già accepted.

**Logica di esecuzione:**

1. `_should_skip_planning(state)` → `True` in questi casi (ritorna senza modifiche):
   - `plan_aborted == True`
   - `awaiting_user == True`
   - `plan is not None AND plan_confirmation == "accepted" AND current_step is not None` (piano in esecuzione)
2. Se `plan_confirmation == "rejected"`:
   - `replan_type == "modify"` → `IncrementalReplanning.stable(state)` (modifica incrementale)
   - `replan_type == "reject"` o `None` → `TotalReplanning.stable(state)` (reset completo)
3. Altrimenti → `CreatePlan.stable(state)` (prima pianificazione o nuovo ciclo)
4. LLM con `with_structured_output(ExecutionPlan)` → lista di `PlanStep {agent, goal}`.
5. Filtra: solo agenti in `AGENT_REGISTRY` (`models_subgraph`, `retriever_subgraph`).
6. Aggiorna stato: `plan`, `current_step=0`, `plan_confirmation="pending"`, `replan_request=None`, `replan_type=None`.

**Contesto disponibile al planning:**

`CreatePlan.stable(state)` legge:
- `state["parsed_request"]` — intento ed entità estratte dal parser
- `state["additional_context"]["relevant_layers"]["layers"]` — layer disponibili (frutto dell'ultimo refresh del SUPERVISOR_ROUTER)

**Limitazione:** Il supervisor NON vede la cronologia dei messaggi (`state["messages"]`). La sua visione della richiesta è mediata esclusivamente dalla struttura `parsed_request`. Per richieste complesse con contesto distribuito su più turni, la qualità del piano dipende interamente da quanto il parser LLM riesca a sintetizzare quel contesto in `intent` e `entities`.

**Ulteriore limitazione temporale:** Il contesto layer è frutto del refresh del router nel ciclo PRECEDENTE. Al primo SUPERVISOR_AGENT (senza router precedente che abbia già fatto il refresh), `additional_context.relevant_layers.layers` potrebbe essere vuoto. Il primo piano viene quindi generato senza contesto layer aggiornato se `layer_registry` era vuoto all'avvio.

**Agent Registry disponibile:**

| Nome agente | Subgraph |
|---|---|
| `models_subgraph` | `MODELS_SUBGRAPH` |
| `retriever_subgraph` | `RETRIEVER_SUBGRAPH` |

---

### SUPERVISOR_PLANNER_CONFIRM

**File:** `ma/orchestrator/supervisor.py`  
**Classe:** `SupervisorPlannerConfirm`  
**Costruttore:** `SupervisorPlannerConfirm(enabled=True)` in produzione corrente

**Ruolo:** Checkpoint human-in-the-loop per l'approvazione del piano.

**Modalità `enabled=False`:** Chiama `_auto_confirm()` direttamente — saltando `run()`, imposta `plan_confirmation = "accepted"`.

**Modalità `enabled=True` — flusso completo:**

```
plan è vuoto o non "pending"?
  → sì: _auto_confirm() / ritorna state invariato
  → no:
      1. _generate_confirmation_message(state) — LLM formatta testo piano
      2. interrupt({interrupt_type: "plan-confirmation"})  ← attende utente
      3. _classify_user_response(user_response)  ← ZeroShotClassifier
      4. dispatch a handler:
          accept  → _handle_accept(state)
          modify  → _handle_modify(state, response)
          reject  → _handle_reject(state, response)
          clarify → _handle_clarify(state, question)   ← loop iterativo
          abort   → _handle_abort(state)
```

**Tabella di dispatch e effetti sullo stato:**

| Intent | `plan_confirmation` | `replan_type` | `plan_aborted` | `supervisor_next_node` |
|---|---|---|---|---|
| `accept` | `"accepted"` | `None` | — | — |
| `modify` | `"rejected"` | `"modify"` | — | — |
| `reject` | `"rejected"` | `"reject"` | — | — |
| `abort` | `"rejected"` | `None` | `True` | `FINAL_RESPONDER` |
| `clarify` | — (loop) | — | — | — |

**Clarify loop (iterativo, non ricorsivo):**

```python
while True:
    if clarify_count >= 3:  # max_clarify_iterations
        return _handle_accept(state)  # auto-accept al limite
    state["clarify_iteration_count"] += 1
    explanation = _generate_plan_explanation(state, question)
    interruption = interrupt({interrupt_type: "plan-clarification"})
    new_intent = classify(new_response)
    if new_intent == "clarify":
        current_question = new_response  # continua loop
    else:
        dispatch(new_intent)  # esce dal loop
```

Il loop è iterativo (non ricorsivo), sicuro con LangGraph checkpoint.

**Nota su re-entrata:** Nei cicli successivi al primo (quando il piano è già `"accepted"` e un agente sta eseguendo), `plan_confirmation != "pending"` → il nodo ritorna immediatamente senza interrupt.

---

### SUPERVISOR_ROUTER

**File:** `ma/orchestrator/supervisor.py`  
**Classe:** `SupervisorRouter`  
**Costruttore:** `SupervisorRouter(enabled=False)` — `enabled=True` attiva il checkpoint mid-plan

**Ruolo:** Aggiorna il contesto layer, decide il prossimo subgraph (o FINAL_RESPONDER), opzionalmente interroga l'utente tra gli step.

**Logica di esecuzione (in ordine):**

1. **Context refresh** (`_update_additional_context`):  
   Se `layer_registry` non è vuoto E (`relevant_layers` è vuoto OR `is_dirty == True`):
   - Invoca `LayersAgent` per filtrare i layer rilevanti per la richiesta corrente.
   - Aggiorna `state["additional_context"]["relevant_layers"]` con risultato.
   - Imposta `is_dirty = False`.

2. **Mid-plan checkpoint** (solo se `self.enabled == True`):  
   Se `current_step > 0` AND `current_step < len(plan)` (step completato, ne rimangono altri):
   - Emette `interrupt({interrupt_type: "step-checkpoint"})` con riepilogo step e piano rimanente.
   - Classifica risposta utente con `StepCheckpoint.CheckpointClassifier`.
   - `abort` → `plan = []`, `plan_aborted = True`, ritorna `FINAL_RESPONDER`.
   - `continue` (o label non riconosciuto) → procede.

3. **Routing** (`_determine_next_node`):
   - `plan` vuoto o `None` → `FINAL_RESPONDER`
   - `current_step < len(plan)` → legge `plan[current_step]["agent"]`, chiama `StateManager.initialize_specialized_agent_cycle(state, agent_type)`, ritorna il subgraph
   - `current_step >= len(plan)` → `FINAL_RESPONDER`

**Nota:** `awaiting_user == True → END` è commentato nel codice — vedi G007-D6.

---

### RETRIEVER_AGENT / MODELS_AGENT

**File:** safercast_agent.py / models_agent.py  
**Classi:** `DataRetrieverAgent` / `ModelsAgent`  
**Prompt:** `SaferCastPrompts.MainContext.stable()` + `InitialRequest` o `ReinvocationRequest`

**Tool disponibili:**

| Agent | Tool |
|---|---|
| Retriever | `DPCRetrieverTool`, `MeteoblueRetrieverTool` |
| Models | `SaferRainTool`, `DigitalTwinTool` |

**Logica di esecuzione:**

1. Sceglie il prompt in base allo stato di conferma:
   - `{agent}_invocation_confirmation == "rejected"` → `ReinvocationRequest.stable(state)` (include feedback utente + tool call precedenti)
   - altrimenti → `InitialRequest.stable(state)` (include goal + layer rilevanti)
2. Costruisce `messages = [SystemMessage(MainContext), HumanMessage(prompt)]`.  
   **IMPORTANTE:** la cronologia di conversazione `state["messages"]` NON è inclusa (vedi G007-D12).
3. Invoca LLM con tool binding.
4. Se **nessun tool call** generato (`_handle_no_tool_calls`):
   - `current_step += 1` (avanza il piano)
   - `{agent}_invocation = invocation` (testo esplicativo del modello)
   - `{agent}_invocation_confirmation = None`
   - `state["messages"] = invocation` ← **BUG: singolo oggetto, non lista** (vedi G007-D11)
5. Se tool call generati (`_prepare_invocation`):
   - `{agent}_invocation = AIMessage(tool_calls=[...])`
   - `{agent}_current_step = 0`
   - `{agent}_invocation_confirmation = "pending"`

**Contesto disponibile all'agente:**

L'agente conosce:
- Il `goal` del passo corrente (`plan[current_step]["goal"]`) — unico canale di informazione dal supervisor all'agente
- Il `parsed_request` (intent strutturato) — per contexto generale
- I `relevant_layers` (layer filtrati) — per selezionare input esistenti

L'agente NON conosce: la cronologia completa della conversazione.

---

### RETRIEVER_INVOCATION_CONFIRM / MODELS_INVOCATION_CONFIRM

**File:** safercast_agent.py / models_agent.py  
**Classi:** `DataRetrieverInvocationConfirm` / `ModelsInvocationConfirm`  
**Costruttore:** `enabled=False` (nessun interrupt manuale in produzione)

**Ruolo:** Completa gli argomenti mancanti per inferenza, valida, e (opzionalmente) chiede conferma.

**Flusso dettagliato:**

```
1. invocation has no tool_calls?  → ritorna state invariato (guarda subgraph edge)
2. Per ogni tool_call[current_step:]:
   a. _apply_inference_to_args() → riempie args mancanti/null da state
   b. _validate_args() → applica regole di validazione
3. Errori di validazione?
   → _handle_validation_failure():
       - LLM genera messaggio errore formattato
       - interrupt({interrupt_type: "invocation-validation"})
       - ToolValidationResponseHandler classifica risposta: accept/modify/clarify/reject/abort
       - Se "rejected" → reinvocation_request = feedback
4. enabled=True e nessun errore?
   → _request_user_confirmation():
       - LLM genera messaggio conferma
       - interrupt({interrupt_type: "invocation-confirmation"})
       - ToolInvocationConfirmationHandler classifica risposta
5. enabled=False e nessun errore?
   → {agent}_invocation_confirmation = "accepted"
```

**Arco condizionale sul subgraph:**

| `{agent}_invocation_confirmation` | Destinazione |
|---|---|
| `"rejected"` | Agent (reinvocazione) |
| qualsiasi altro valore | Executor |

---

### RETRIEVER_EXECUTOR / MODELS_EXECUTOR

**File:** safercast_agent.py / models_agent.py

**Ruolo:** Esegue i tool call validati e aggiorna lo stato.

**Flusso:**

```
invocation has no tool_calls?
  → current_step += 1
  → messages = [invocation]  ← lista corretta
  → ritorna

Per ogni tool_call in invocation.tool_calls[retriever_current_step:]:
  1. tool._execute(**args)  ← args già completi da Confirm
  2. _format_tool_response() → ToolMessage tool-specifico
  3. _add_layer_to_registry() → LayersAgent aggiunge layer a layer_registry
  4. _record_tool_result() → tool_results[agent_key] aggiornato
  5. StateManager.mark_agent_step_complete(state, agent_type) → retriever_current_step++

state["current_step"] += 1   ← avanza il piano globale
state["messages"] = [invocation, *tool_responses]
```

**Nota:** `is_dirty` viene impostato a `True` dopo ogni esecuzione che aggiunge layer (il flag è gestito internamente da `_add_layer_to_registry` via `LayersAgent`).

---

### FINAL_RESPONDER

**File:** `ma/chat/final_responder.py`  
**Classe:** `FinalResponder`  
**Prompt:** `FinalResponderPrompts.Response.stable()` + `FinalResponderPrompts.Context.Formatted.stable(state)`

**Ruolo:** Genera la risposta finale all'utente sintetizzando il contesto di esecuzione.

**Flusso:**

```python
invoke_messages = [
    SystemMessage(content=response_prompt.message),   # ruolo e istruzioni
    SystemMessage(content=context_prompt.message),     # intent, plan, tool_results
    *state["messages"],                                # intera cronologia
]
response = llm.invoke(invoke_messages)
state["messages"] = [AIMessage(content=response.content)]
StateManager.cleanup_on_final_response(state)
```

**Context.Formatted.stable(state)** include: `intent`, `entities`, `plan`, `tool_results`, `error`, `raw_text`.

---

## G004 — Ciclo di Vita dello Stato

### Mappa delle mutazioni per nodo

| Nodo | Chiavi scritte | Note |
|---|---|---|
| `REQUEST_PARSER` | `parsed_request`, `plan=None`, `tool_results={}`, `plan_aborted=False`, `additional_context.relevant_layers.is_dirty=True`, tutti i campi agente | `StateManager.initialize_new_cycle()` |
| `SUPERVISOR_AGENT` | `plan`, `current_step=0`, `plan_confirmation="pending"`, `replan_request=None`, `replan_type=None` | Salta se piano già accepted (skip planning) |
| `SUPERVISOR_PLANNER_CONFIRM` | `plan_confirmation`, `replan_request`, `replan_type`, `clarify_iteration_count`, `plan_aborted` (solo abort) | Solo se enabled=True; auto-confirm se enabled=False |
| `SUPERVISOR_ROUTER` | `supervisor_next_node`, `additional_context.relevant_layers`, `layers_*`, `{agent}_invocation`, `{agent}_current_step`, `{agent}_invocation_confirmation`, `{agent}_reinvocation_request` | Via `StateManager.initialize_specialized_agent_cycle` |
| `{AGENT}_AGENT` | `{agent}_invocation`, `{agent}_invocation_confirmation`, `{agent}_reinvocation_request`, `{agent}_current_step`, `current_step`, `messages` | `current_step` avanza solo se no tool calls |
| `{AGENT}_INVOCATION_CONFIRM` | `{agent}_invocation.tool_calls[*].args` (mutazione in-place), `{agent}_invocation_confirmation`, `{agent}_reinvocation_request` | Inferenza e validazione |
| `{AGENT}_EXECUTOR` | `current_step`, `messages`, `tool_results`, `layer_registry`, `{agent}_current_step` | `mark_agent_step_complete` per internal step |
| `FINAL_RESPONDER` | `messages`, tutti i campi temporanei → `None/0/[]` | `StateManager.cleanup_on_final_response()` |

### Chiavi persistenti attraverso i cicli

Le seguenti chiavi **non vengono azzerate** da `cleanup_on_final_response`:

- `layer_registry` — registry cumulativo dei layer geospaziali
- `user_drawn_shapes` — forme disegnate dall'utente
- `user_id`, `project_id` — identità di sessione
- `messages` — cronologia completa della conversazione (append-only, accumulata tramite reducer `add_messages`)

---

## G005 — Percorsi di Esecuzione

### Percorso 1 — Query informativa (piano vuoto)

```
Utente: "Che cos'è il radar DPC?"

REQUEST_PARSER
  parsed_request = {intent: "explain DPC radar", entities: [], raw_text: "..."}
  initialize_new_cycle() → tutti i campi resettati

SUPERVISOR_AGENT
  CreatePlan → LLM decide: nessun agente necessario
  plan = []
  plan_confirmation = "pending"

SUPERVISOR_PLANNER_CONFIRM (enabled=True)
  not plan → _auto_confirm() → plan_confirmation = "accepted"
  (enabled=False: _auto_confirm() sempre)

SUPERVISOR_ROUTER
  not plan → supervisor_next_node = "final_responder"

FINAL_RESPONDER
  Context contiene plan=[] e tool_results={}
  LLM risponde dalla cronologia + propria conoscenza
  cleanup_on_final_response()
```

**Nota:** La risposta è generata solo dalla conoscenza LLM + cronologia. Non viene eseguito nessun tool.

---

### Percorso 2 — Piano multi-step nominale (senza interrupt)

```
Utente: "Recupera dati DPC per Milano poi simula alluvione"

REQUEST_PARSER → parsed_request = {intent, entities: ["Milano", "DPC", "SaferRain"]}

SUPERVISOR_AGENT
  CreatePlan → plan = [
    {agent: "retriever_subgraph", goal: "Retrieve DPC radar SRI for Milan bbox"},
    {agent: "models_subgraph", goal: "Run SaferRain flood simulation using retrieved layer"}
  ]
  current_step = 0, plan_confirmation = "pending"

SUPERVISOR_PLANNER_CONFIRM (enabled=False) → _auto_confirm() → "accepted"

SUPERVISOR_ROUTER
  context refresh: layer_registry vuoto → skip
  plan[0].agent = "retriever_subgraph"
  StateManager.initialize_specialized_agent_cycle(state, "retriever")
  supervisor_next_node = "retriever_subgraph"

─── RETRIEVER_SUBGRAPH ───
RETRIEVER_AGENT:
  InitialRequest(goal="Retrieve DPC...", relevant_layers=[])
  LLM → DPCRetrieverTool({product: "SRI", bbox: "...", time_range: "..."})
  retriever_invocation = AIMessage(tool_calls=[...])
  retriever_invocation_confirmation = "pending"

RETRIEVER_INVOCATION_CONFIRM (enabled=False):
  Inferenza args → completa bbox/time da state
  Validazione args → OK
  retriever_invocation_confirmation = "accepted"

RETRIEVER_EXECUTOR:
  _execute DPCRetrieverTool → layer S3://...
  LayersAgent aggiunge layer a layer_registry
  tool_results["retriever"] = {result}
  current_step = 1
  messages += [invocation, ToolMessage]

─── SUPERVISOR_SUBGRAPH (re-enter) ───
SUPERVISOR_AGENT: _should_skip_planning() → True (plan accepted, step 1/2) → skip
SUPERVISOR_PLANNER_CONFIRM: plan_confirmation != "pending" → skip
SUPERVISOR_ROUTER:
  is_dirty = True → refresh relevant_layers → trova layer DPC appena creato
  plan[1].agent = "models_subgraph"
  StateManager.initialize_specialized_agent_cycle(state, "models")
  supervisor_next_node = "models_subgraph"

─── MODELS_SUBGRAPH ───
MODELS_AGENT:
  InitialRequest(goal="Run SaferRain...", relevant_layers=[{DPC layer}])
  LLM → SaferRainTool({dem: "...", rainfall_layer: "DPC SRI ...", ...})
  models_invocation = AIMessage(tool_calls=[...])

MODELS_INVOCATION_CONFIRM (enabled=False):
  Inferenza + validazione → OK
  models_invocation_confirmation = "accepted"

MODELS_EXECUTOR:
  _execute SaferRainTool → flood layer S3://...
  LayersAgent aggiunge flood layer a layer_registry
  tool_results["models"] = {result}
  current_step = 2

─── SUPERVISOR_SUBGRAPH (re-enter) ───
SUPERVISOR_AGENT: skip (current_step=2, plan accepted)
SUPERVISOR_PLANNER_CONFIRM: skip
SUPERVISOR_ROUTER:
  current_step(2) >= len(plan)(2) → supervisor_next_node = "final_responder"

FINAL_RESPONDER → cleanup → END
```

---

### Percorso 3 — Modifica piano (modify)

```
SUPERVISOR_PLANNER_CONFIRM (enabled=True):
  Utente: "Rimuovi il secondo step, fai solo il retrieval"
  classify → "modify"
  plan_confirmation = "rejected", replan_type = "modify"
  replan_request = HumanMessage("Rimuovi il secondo step...")
  current_step = None

Arco condizionale: plan_confirmation=="rejected" AND NOT plan_aborted → True → SUPERVISOR_AGENT

SUPERVISOR_AGENT:
  plan_confirmation=="rejected", replan_type=="modify"
  → IncrementalReplanning(parsed_request, current_plan, user_feedback)
  → NEW plan = [{agent: "retriever_subgraph", goal: "..."}]
  → plan_confirmation = "pending", replan_request = None

SUPERVISOR_PLANNER_CONFIRM:
  Nuovo interrupt con piano modificato
  Utente: "sì" → accept → plan_confirmation = "accepted"

SUPERVISOR_ROUTER → routing su nuovo piano (un solo step)
```

---

### Percorso 4 — Rifiuto totale piano (reject)

```
SUPERVISOR_PLANNER_CONFIRM:
  Utente: "No, questo approccio è sbagliato"
  classify → "reject"
  plan_confirmation = "rejected", replan_type = "reject"

SUPERVISOR_AGENT:
  TotalReplanning(parsed_request, previous_plan_REJECTED, user_feedback)
  → Piano completamente nuovo
  → plan_confirmation = "pending"
```

**Rischio:** Non c'è limite al numero di cicli modify/reject. Un utente indeciso (o un LLM che genera piani sistematicamente rifiutati) può ciclare indefinitamente. A differenza del clarify loop, non esiste `replan_iteration_count`.

---

### Percorso 5 — Chiarimento piano (clarify loop)

```
SUPERVISOR_PLANNER_CONFIRM (enabled=True):
  Utente: "Cosa fa esattamente il retriever nel passo 1?"
  classify → "clarify"
  clarify_iteration_count = 1

  _generate_plan_explanation(state, "Cosa fa il retriever?")
  → [ExplainerMainContext + RequestExplanation prompts] → testo esplicativo LLM
  interrupt({interrupt_type: "plan-clarification", content: "spiegazione + ..."})

Utente: "Ok capito, procedi"
  classify → "accept"
  clarify_iteration_count = 0
  plan_confirmation = "accepted"

─── Caso: n-esima clarify → auto-accept ───
  clarify_iteration_count >= 3 → _handle_accept(state)
```

---

### Percorso 6 — Abort operazione (abort)

```
SUPERVISOR_PLANNER_CONFIRM (enabled=True):
  Utente: "Annulla tutto"
  classify → "abort"
  _handle_abort():
    plan = []
    plan_aborted = True
    plan_confirmation = "rejected"
    supervisor_next_node = FINAL_RESPONDER

Arco condizionale: plan_confirmation=="rejected" AND NOT plan_aborted
  = "rejected" AND NOT True = False
  → SUPERVISOR_ROUTER (non SUPERVISOR_AGENT)

SUPERVISOR_ROUTER:
  not plan → FINAL_RESPONDER
  (supervisor_next_node era già impostato ma viene scritto di nuovo = FINAL_RESPONDER)

FINAL_RESPONDER → risposta di annullamento + cleanup
```

---

### Percorso 7 — Validazione argomenti fallita (invocation-validation)

```
RETRIEVER_INVOCATION_CONFIRM:
  LLM ha proposto: DPCRetrieverTool({product: "INVALID", bbox: null})
  Dopo inferenza: bbox inferita da state (es. da user_drawn_shapes)
  Dopo validazione: errors = {product: "valore non valido", bbox: "ancora null"}

  _handle_validation_failure():
    LLM genera messaggio errore formattato
    interrupt({interrupt_type: "invocation-validation", content: "errori formattati"})

Utente: "Usa SRI come prodotto e bbox 8,44,10,46"
  ToolValidationResponseHandler.process_validation_response():
    classifica → "modify"
    retriever_invocation_confirmation = "rejected"
    retriever_reinvocation_request = HumanMessage("Usa SRI...")

Arco subgraph: retriever_invocation_confirmation == "rejected" → RETRIEVER_AGENT

RETRIEVER_AGENT:
  {agent}_invocation_confirmation == "rejected"
  → ReinvocationRequest(goal, previous_tool_calls, user_feedback)
  → LLM genera nuovi tool call con SRI e bbox corretti
  → nuovo retriever_invocation, retriever_invocation_confirmation = "pending"

RETRIEVER_INVOCATION_CONFIRM → inferenza + validazione → OK → "accepted"
RETRIEVER_EXECUTOR → esegue
```

---

### Percorso 8 — Agente non genera tool call

```
MODELS_AGENT:
  InitialRequest(goal="Run flood simulation", relevant_layers=[])
  LLM non propone tool calls (es: layer DEM mancante)
  _handle_no_tool_calls(invocation):
    current_step += 1  ← avanza piano senza eseguire nulla
    models_invocation = AIMessage(content="Manca il layer DEM per eseguire la simulazione")
    models_invocation_confirmation = None
    state["messages"] = invocation  ← BUG D11: singolo oggetto non lista

MODELS_INVOCATION_CONFIRM:
  has_no_tool_calls(invocation) → True → ritorna state invariato
  (models_invocation_confirmation rimane None)

Arco subgraph: None != "rejected" → MODELS_EXECUTOR

MODELS_EXECUTOR:
  has_no_tool_calls(invocation) → True
  current_step += 1   ← duplica l'incremento! (vedi G007-D15)
  messages = [invocation]
  ritorna

─── SUPERVISOR_SUBGRAPH re-enter ───
SUPERVISOR_ROUTER: current_step avanzato (forse oltre fine piano) → FINAL_RESPONDER

FINAL_RESPONDER: spiega all'utente che mancano input necessari
```

**Problema:** `current_step` viene incrementato DUE volte: una nell'Agent e una nell'Executor (che ha la sua guard `if has_no_tool_calls`). Il piano avanza di 2 per uno step saltato — si rischia di saltare il passo successivo (vedi G007-D15).

---

### Percorso 9 — Step-checkpoint abort (mid-plan)

```
(enabled=True per SupervisorRouter)

RETRIEVER_EXECUTOR completa step 0 → current_step = 1
SUPERVISOR_SUBGRAPH re-enter:
SUPERVISOR_AGENT: skip
SUPERVISOR_PLANNER_CONFIRM: skip
SUPERVISOR_ROUTER:
  _update_additional_context() → refresh
  _maybe_checkpoint_interrupt():
    current_step=1 > 0 AND current_step=1 < len(plan)=2 → True
    completed_step = plan[0]
    interrupt({interrupt_type: "step-checkpoint", content: riepilogo})

Utente: "Basta, non voglio la simulazione"
  classify → "abort"
  plan = [], plan_aborted = True
  ritorna True (abort_requested)

SUPERVISOR_ROUTER:
  supervisor_next_node = FINAL_RESPONDER

FINAL_RESPONDER → risposta con risultati parziali + cleanup
```

---

### Percorso 10 — Conversazione multi-turn con persistenza layer

```
=== TURNO 1 ===
Utente: "Recupera radar DPC per la Lombardia"
[...percorso nominale...]
RETRIEVER_EXECUTOR: layer "DPC SRI Lombardia 2026-03-17" aggiunto a layer_registry
FINAL_RESPONDER: risposta + cleanup (layer_registry CONSERVATO)

=== TURNO 2 ===
Utente: "Adesso simula l'alluvione con quei dati"

REQUEST_PARSER:
  initialize_new_cycle() → plan=None, tool_results={}, MA layer_registry intatto
  invoke_messages include l'intera cronologia (turno 1 + turno 2)
  LLM estrae: intent="run flood simulation using previous DPC layer" entities=["DPC SRI Lombardia"]

SUPERVISOR_AGENT:
  CreatePlan con additional_context che include "DPC SRI Lombardia" (is_dirty=True da init)
  → Però: additional_context è vuoto perché il router NON ha ancora fatto il refresh!
  → Il layer è in layer_registry ma non ancora in additional_context.relevant_layers
  → LLM pianifica basandosi SOLO su parsed_request, senza vedere il layer
  → Piano: [{agent: "models_subgraph", goal: "Run flood simulation for Lombardia"}]

SUPERVISOR_ROUTER (primo ingresso):
  is_dirty = True, layer_registry ha "DPC SRI Lombardia"
  LayersAgent trova layer rilevante → additional_context aggiornato
  StateManager.initialize_specialized_agent_cycle(state, "models")

MODELS_AGENT:
  InitialRequest(goal, relevant_layers=["DPC SRI Lombardia"])
                      ↑ ora disponibile (dopo router refresh)
  LLM → SaferRainTool({rainfall_layer: "DPC SRI Lombardia", ...})
```

**Osservazione chiave:** Il supervisor pianifica SENZA vedere i layer. Il routing avviene correttamente perché `goal` contiene "Lombardia" e il modello può inferire quale layer usare. Ma se i layer avessero nomi ambigui o il goal fosse generico, l'agente potrebbe selezionare il layer sbagliato.

---

## G006 — Ruolo dei Prompt nel Controllo del Flusso

I prompt non sono solo istruzioni: **determinano attivamente il percorso del grafo** attraverso i valori che l'LLM produce.

| Prompt | Impatto sul grafo |
|---|---|
| `RequestParserPrompts.MainContext` | Qualità di `parsed_request.intent` → base dell'intero piano |
| `OrchestratorPrompts.MainContext` | Piano vuoto vs. piano con step → routing a FINAL_RESPONDER vs. subgraph |
| `CreatePlan.stable(state)` | Numero di step, agenti scelti, obiettivi dei step |
| `IncrementalReplanning` | Quanti step vengono modificati vs. mantenuti |
| `TotalReplanning` | Se l'LLM propone lo stesso piano rifiutato → loop potenzialmente lungo |
| `ZeroShotClassifier.stable(user_response)` | Classificazione accept/modify/clarify/reject/abort → arco decisivo |
| `InitialRequest.stable(state)` | Qualità dei tool call proposti, completezza degli argomenti |
| `ReinvocationRequest.stable(state)` | Se il feedback utente viene recepito correttamente |
| `FinalResponderPrompts.Context.Formatted` | Quanta informazione utile viene passata al LLM per la risposta |

### Punti di fragilità nei prompt

**`ZeroShotClassifier`:** Restituisce una label single-word, ma gli LLM possono rispondere con frasi come `"accept - the user confirms"`. Il codice fa `.strip().lower()` ma non estrae solo la prima parola. Il fallback in caso di label non riconosciuta è `"reject"`, che innesca un replan non voluto.

**`InitialRequest.stable(state)`:** Legge `plan[current_step]` senza bounds check. Se chiamato quando `current_step >= len(plan)` (scenario teoricamente impossibile ma dipendente da race conditions o bug di step counter), genera `IndexError`.

**`TotalReplanning.stable(state)`:** Se l'LLM che ha generato il piano originale è lo stesso che genera il nuovo piano, senza ulteriori vincoli nel prompt che lo guidino verso un approccio diverso, tende a riproporre lo stesso piano. Il prompt dice "Create a completely new plan. Take a fundamentally different approach" ma questo non garantisce comportamento distinto.

**`RequestParserPrompts.MainContext`:** Non include istruzioni esplicite su come gestire richieste di follow-up che referenziano il contesto di turni precedenti (es. "fai lo stesso per domani"). Il parser vede la cronologia ma non è esplicitamente guidato a inferire il contesto mancante.

---

## G007 — Difetti Noti

> **Legenda gravità:** 🔴 Alta — 🟡 Media — ⚪ Bassa  
> **Legenda stato:** `[OPEN]` aperto · `[RESOLVED]` risolto · `[PARTIAL]` rimedio parziale

---

### D1 — Abort non raggiungeva FINAL_RESPONDER `[RESOLVED — PLN-008]`

~~`plan_confirmation = "rejected"` nell'abort veniva intercettato dall'arco condizionale del subgraph che re-entrava in SUPERVISOR_AGENT invece di uscire.~~

**Fix applicato:** `plan_aborted: bool` aggiunto allo stato. `_handle_abort()` imposta `plan_aborted=True`. L'arco condizionale ora verifica `plan_confirmation == "rejected" AND NOT plan_aborted`. `_should_skip_planning()` controlla `plan_aborted` come primo test.

---

### D2 — SupervisorAgent pianificava senza contesto layer `[RESOLVED]`

~~`CreatePlan.stable(state)` leggeva `state.get("plan_additional_context")` — chiave inesistente.~~

**Fix applicato:** `CreatePlan.stable(state)` ora legge correttamente:
```python
state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
```

---

### D3 — StateManager usava chiave errata per cleanup agente `[RESOLVED]`

~~`_clear_specialized_agent_state()` e `initialize_specialized_agent_cycle()` usavano `{prefix}_confirmation` invece di `{prefix}_invocation_confirmation`.~~

**Fix applicato:** Entrambi i metodi usano `{prefix}_invocation_confirmation`.

---

### D4 — FinalResponder iniettava context come AIMessage `[RESOLVED]`

~~`invoke_messages = [*state["messages"], SystemMessage(response_prompt), AIMessage(context)]` — SystemMessage dopo la cronologia, contesto come AIMessage.~~

**Fix applicato:** Ordine corretto:
```python
invoke_messages = [
    SystemMessage(response_prompt.message),
    SystemMessage(context_prompt.message),
    *state["messages"],
]
```

---

### D5 — `_handle_clarify` usava ricorsione `[RESOLVED]`

~~Chiamata ricorsiva con `interrupt()` annidati — instabile con LangGraph checkpoint.~~

**Fix applicato:** Trasformato in loop iterativo `while True` con `current_question = new_response` per continuare. Nessuna chiamata ricorsiva, nessun interrupt annidato.

---

### D6 — `awaiting_user = True` → dead code `[OPEN]` ⚪

**Descrizione:** `SupervisorRouter._determine_next_node()` contiene il blocco commentato:
```python
# if state.get("awaiting_user"):
#     return "END"
```
Nessun nodo imposta `awaiting_user = True` in modo persistente (REQUEST_PARSER lo imposta a `False`). Il campo è nel TypedDict ma non viene mai usato in modo coerente. Dead code potenzialmente confusionario.

---

### D7 — Nessun interrupt mid-plan `[RESOLVED — PLN-009]`

~~Non esisteva un meccanismo per interrompere un piano tra due step successivi.~~

**Fix applicato:** `SupervisorRouter(enabled=True)` emette `interrupt_type = "step-checkpoint"` dopo ogni step completato (se il piano ha ancora step da eseguire), con riepilogo e risposta classify `continue|abort`.

---

### D8 — `current_step += 1` senza guard su None `[PARTIAL]`

**Descrizione:** In `DataRetrieverAgent._handle_no_tool_calls()` e `ModelsAgent._handle_no_tool_calls()`, se `current_step` è `None`, `+= 1` genera `TypeError`.

**Rimedio parziale presente:** Guard `if state.get("current_step") is None: state["current_step"] = 0` aggiunto. Il problema residuo è che `current_step` potrebbe essere `None` se `initialize_specialized_agent_cycle` non è stato chiamato (scenario teorico post-D3, ora risolto). Il guard è comunque corretto.

---

### D9 — Piano vuoto con `enabled=True`: plan_confirmation "pending" `[RESOLVED]`

~~Con `enabled=True` e piano vuoto, `run()` ritornava senza chiamare `_auto_confirm()`, lasciando plan_confirmation a "pending".~~

**Fix applicato:** `run()` inizia con:
```python
if not plan:
    return self._auto_confirm(state)
```
`_auto_confirm()` viene chiamato anche per piani vuoti quando `enabled=True`.

---

### D10 — Classificatore plan-confirmation: entrambi SystemMessage `[RESOLVED]`

~~Entrambi i messaggi del classifier erano `SystemMessage`, comportamento non supportato su alcuni provider.~~

**Fix applicato:** Il primo usa `.to(SystemMessage)` (contesto classificatore), il secondo usa `.to(HumanMessage)` (prompt classificazione effettivo).

---

### D11 — `_handle_no_tool_calls` assegna messaggio singolo invece di lista `[OPEN]` 🟡

**File:** `safercast_agent.py:_handle_no_tool_calls()`, `models_agent.py:_handle_no_tool_calls()`

**Descrizione:** Quando l'agente non genera tool call:
```python
state["messages"] = invocation   # ← singolo AIMessage, non lista
```
La chiave `messages` usa il reducer `add_messages` che attende una lista (o dict). Passare un singolo oggetto può essere coercito da LangChain o causare comportamento inatteso a seconda della versione.

**Fix suggerito:** `state["messages"] = [invocation]` (coerente con il pattern usato nell'executor, che fa `state["messages"] = [invocation]` correttamente).

---

### D12 — Agenti specializzati ignorano la cronologia della conversazione `[OPEN]` 🟡

**File:** `safercast_agent.py:_build_invocation_messages()`, `models_agent.py:_build_invocation_messages()`

**Descrizione:** I messaggi costruiti per l'invocazione LLM degli agenti specializzati sono:
```python
[SystemMessage(MainContext), HumanMessage(InitialRequest)]
```
Nessuna inclusione di `state["messages"]`. L'agente non sa cosa l'utente ha detto nei turni precedenti.

**Impatto:** Il canale informativo dal supervisor all'agente è solo la stringa `goal` nel passo del piano. Se la richiesta dell'utente era "usa la stessa area geografica del radar di ieri" e il parser ha estratto un `intent` generico, l'agente non ha modo di risolvere "l'area geografica di ieri".

**Weight:** La qualità dell'`intent` estratto dal parser mitiga parzialmente questo problema, ma non lo risolve nel caso generale.

**Fix suggerito:** Includere `*state["messages"]` tra il system prompt e lo HumanMessage di invocazione, oppure aggiungere un summary dei messaggi rilevanti nel contesto del prompt.

---

### D13 — Supervisor pianifica senza cronologia conversazione `[OPEN]` 🟡

**File:** `supervisor.py:_generate_plan()`

**Descrizione:** `_generate_plan()` costruisce:
```python
[SystemMessage(MainContext), HumanMessage(CreatePlan)]
```
Il supervisor non vede `state["messages"]`. Vede solo `parsed_request` (dict strutturato) e `additional_context`. Per richieste di follow-up complesse dove il contesto è distribuito nella cronologia, la qualità del piano dipende interamente da quanto il parser ha sintetizzato quel contesto in `intent` ed `entities`.

**Mitigazione esistente:** L'`intent` del parser è estratto vedendo l'intera cronologia. Se il parser è accurato, `intent` dovrebbe catturare il contesto necessario.

**Fix suggerito:** Includere un sommario della conversazione (ultimi N messaggi) nel `CreatePlan` prompt, oppure includere `state["messages"]` direttamente.

---

### D14 — `_handle_no_tool_calls` incrementa `current_step` due volte `[OPEN]` 🟡

**File:** safercast_agent.py, models_agent.py — `_handle_no_tool_calls()` nell'Agent e guard corrispondente nell'Executor

**Descrizione:** Quando l'agente non genera tool call:

1. **Agent** (`_handle_no_tool_calls`): `state["current_step"] += 1`
2. L'invocation ha `tool_calls = []` — l'arco condizionale del subgraph vede `invocation_confirmation = None != "rejected"` → va a **Executor**
3. **Executor** (`run()`): `if has_no_tool_calls(invocation): state["current_step"] += 1`

`current_step` viene incrementato **due volte** per uno step che non ha eseguito nulla, saltando lo step successivo nel piano.

**Scenario:** Piano = [step0: retriever, step1: models]. Se il retriever non genera tool call, `current_step` passa da 0 a 2 (invece di 1), e il models step viene saltato.

**Fix suggerito:** Rimuovere l'incremento in `_handle_no_tool_calls()` dell'Agent (lasciarlo solo nell'Executor), oppure rimuovere la guard nell'Executor.

---

### D15 — Nessun limite al loop replan (modify/reject) `[OPEN]` ⚪

**File:** supervisor.py — loop in `build_supervisor_subgraph()`

**Descrizione:** A differenza del clarify loop (limitato a `max_clarify_iterations=3`), i cicli `modify` e `reject` non hanno un contatore. Un utente che continua a modificare il piano, o un LLM che genera sistematicamente piani rifiutati, può ciclare indefinitamente nel subgraph.

**Fix suggerito:** Aggiungere `replan_iteration_count: Optional[int]` allo stato, incrementarlo in `_handle_modify` e `_handle_reject`, e includere un limite (es. 5) in `_generate_plan()` con fallback a un comportamento di uscita.

---

### D16 — Nessuna gestione degli errori nell'esecuzione dei tool `[OPEN]` 🔴

**File:** `safercast_agent.py:_execute_tool_call()`, `models_agent.py:_execute_tool_call()`

**Descrizione:** `tool._execute(**tool_args)` non è wrapped in try/except. Se il tool lancia un'eccezione (errore di rete, risposta API malformata, file S3 non trovato, ecc.), l'eccezione si propaga non gestita, crashando il grafo e lasciando lo stato in una condizione inconsistente.

**Fix suggerito:** Wrappare in try/except, catturare l'eccezione, costruire un `ToolMessage` con content di errore, registrare il fallimento in `tool_results`, e proseguire (o emettere un interrupt di errore).

---

## G008 — Tabella Riepilogativa Interrupt Points

| Nodo | `interrupt_type` | Condizione | Handler risposta |
|---|---|---|---|
| `SUPERVISOR_PLANNER_CONFIRM` | `plan-confirmation` | `enabled=True` e piano non vuoto e `plan_confirmation=="pending"` | `ZeroShotClassifier` → dispatch accept/modify/clarify/reject/abort |
| `SUPERVISOR_PLANNER_CONFIRM` | `plan-clarification` | Dentro clarify loop, per ogni iterazione | Stesso classifier |
| `SUPERVISOR_ROUTER` | `step-checkpoint` | `enabled=True`, `current_step > 0`, `current_step < len(plan)` | `StepCheckpoint.CheckpointClassifier` → `continue` / `abort` |
| `RETRIEVER_INVOCATION_CONFIRM` | `invocation-validation` | Argomenti non validi dopo inferenza | `ToolValidationResponseHandler` |
| `RETRIEVER_INVOCATION_CONFIRM` | `invocation-confirmation` | `enabled=True` e validazione OK | `ToolInvocationConfirmationHandler` |
| `MODELS_INVOCATION_CONFIRM` | `invocation-validation` | Argomenti non validi dopo inferenza | `ToolValidationResponseHandler` |
| `MODELS_INVOCATION_CONFIRM` | `invocation-confirmation` | `enabled=True` e validazione OK | `ToolInvocationConfirmationHandler` |

---

## G009 — Stato `MABaseGraphState`: Chiavi per Fase

| Chiave | Tipo | Fase | Persistenza |
|---|---|---|---|
| `messages` | `list[AnyMessage]` | Sempre | Permanente (append-only) |
| `user_id` / `project_id` | `str` | Sempre | Permanente |
| `layer_registry` | `list[dict]` | Sempre | Permanente (cumulativa) |
| `user_drawn_shapes` | `list[dict]` | Sempre | Permanente |
| `nowtime` | `str` | Sempre | Inizializzata a costruzione |
| `avaliable_tools` | `list[str]` | — | Non ancora usata |
| `parsed_request` | `dict` | REQUEST_PARSER → FINAL_RESPONDER | Ciclo |
| `additional_context` | `dict` | SUPERVISOR_ROUTER → FINAL_RESPONDER | Ciclo |
| `supervisor_next_node` | `str` | SUPERVISOR_ROUTER → routing main graph | Ciclo |
| `plan` | `list[dict]` | SUPERVISOR_AGENT → FINAL_RESPONDER | Ciclo |
| `current_step` | `int` | SUPERVISOR_AGENT → FINAL_RESPONDER | Ciclo |
| `plan_confirmation` | `ConfirmationState` | SUPERVISOR_AGENT → FINAL_RESPONDER | Ciclo |
| `plan_aborted` | `bool` | SUPERVISOR_PLANNER_CONFIRM → subgraph | Ciclo |
| `replan_request` / `replan_type` | `AnyMessage` / `str` | SUPERVISOR_PLANNER_CONFIRM → SUPERVISOR_AGENT | Ciclo |
| `clarify_iteration_count` | `int` | SUPERVISOR_PLANNER_CONFIRM (clarify loop) | Ciclo |
| `tool_results` | `dict` | Executors → FINAL_RESPONDER | Ciclo |
| `awaiting_user` | `bool` | — | Dead code (D6) |
| `retriever_invocation` | `AIMessage` | RETRIEVER_AGENT → RETRIEVER_EXECUTOR | Step |
| `retriever_invocation_confirmation` | `str` | RETRIEVER_CONFIRM → arco subgraph | Step |
| `retriever_reinvocation_request` | `AnyMessage` | RETRIEVER_CONFIRM → RETRIEVER_AGENT | Step |
| `retriever_current_step` | `int` | RETRIEVER tool loop interno | Step |
| `models_invocation` | `AIMessage` | MODELS_AGENT → MODELS_EXECUTOR | Step |
| `models_invocation_confirmation` | `str` | MODELS_CONFIRM → arco subgraph | Step |
| `models_reinvocation_request` | `AnyMessage` | MODELS_CONFIRM → MODELS_AGENT | Step |
| `models_current_step` | `int` | MODELS tool loop interno | Step |
| `layers_request` / `layers_invocation` / `layers_response` | vari | SUPERVISOR_ROUTER (context refresh) | Temporanea |

---

## G010 — Limiti Architetturali

I seguenti non sono bug ma **vincoli strutturali** del design attuale. Documentati per orientare future evoluzioni.

### L1 — Piano fisso: nessun replan adattivo durante l'esecuzione

Il piano viene generato una volta dal `SUPERVISOR_AGENT` e rimane immutato per tutta l'esecuzione. Se il risultato dello step 1 (es. nessun dato disponibile per il bbox richiesto) implicherebbe logicamente un piano diverso per lo step 2, il supervisor non lo vede: lo step 2 viene eseguito con lo stesso goal originale. L'agente specializzato allo step 2 dovrà gestire autonomamente (tramite il contesto dei layer) la situazione.

### L2 — Il `goal` è l'unico canale informativo supervisor → agente

L'intera conoscenza del supervisor sullo scopo di uno step è compressa in una stringa `goal`. I tool argument vengono proposti dall'agente specializzato dalla propria visione contestuale (layer disponibili + goal + parsed_request). Qualsiasi informazione più specifica che il supervisor volesse trasmettere (es. "usa specificamente il layer X come input") deve essere esplicitamente scritta nel goal — non c'è un meccanismo strutturato.

### L3 — Assenza di gestione delle eccezioni nei tool

Nessun meccanismo di recovery da errori di esecuzione (vedi D16). Il sistema assume che i tool abbiano sempre successo o che gli errori siano già rilevati dalla validazione in pre-esecuzione.

### L4 — LayersAgent viene richiamato a ogni re-enter del router

`_update_additional_context()` usa il flag `is_dirty` per controllare se il refresh è necessario. Tuttavia il flag viene resettato solo nello stato temporaneo del ciclo — non c'è una memorizzazione del "stato precedente" del layer_registry per evitare refresh inutili quando il registry non è cambiato. In sistemi con molti layer, questo può introdurre latenza.

### L5 — Nessun subgraph per agenti senza confermabilità

Il pattern Agent → InvocationConfirm → Executor implica sempre la possibilità di confirm/reject. Per agenti (futuri) dove la conferma non ha senso (es. consulta solo, nessun tool call), questa architettura triplica i nodi senza beneficio funzionale.

---

## Riepilogo Difetti

| ID | Gravità | Stato | Descrizione breve |
|---|---|---|---|
| D1 | 🔴 | ✅ RESOLVED (PLN-008) | Abort rientrava in SUPERVISOR_AGENT |
| D2 | 🔴 | ✅ RESOLVED | Supervisor leggeva chiave layer inesistente |
| D3 | 🟡 | ✅ RESOLVED | StateManager usava `{prefix}_confirmation` |
| D4 | 🟡 | ✅ RESOLVED | FinalResponder context come AIMessage |
| D5 | 🟡 | ✅ RESOLVED | _handle_clarify ricorsivo con interrupt annidati |
| D6 | ⚪ | 🔵 OPEN | `awaiting_user=True → END` dead code |
| D7 | 🔴 | ✅ RESOLVED (PLN-009) | Nessun interrupt mid-plan |
| D8 | ⚪ | 🟡 PARTIAL | `current_step += 1` su None (guard presente) |
| D9 | ⚪ | ✅ RESOLVED | Piano vuoto `plan_confirmation` rimane "pending" |
| D10 | ⚪ | ✅ RESOLVED | Entrambi i messaggi classifier erano SystemMessage |
| D11 | 🟡 | 🔵 OPEN | `state["messages"] = invocation` (non lista) in no-tool-calls agent |
| D12 | 🟡 | 🔵 OPEN | Agenti specializzati ignorano cronologia conversazione |
| D13 | 🟡 | 🔵 OPEN | Supervisor pianifica senza cronologia conversazione |
| D14 | 🟡 | 🔵 OPEN | Double-increment `current_step` nel percorso no-tool-calls |
| D15 | ⚪ | 🔵 OPEN | Nessun limite al loop modify/reject |
| D16 | 🔴 | 🔵 OPEN | Nessuna gestione errori nell'esecuzione tool |


---

Qui segue il **sommario analitico** dei principali elementi emersi dall'analisi, prima di scrivere il documento:

## Cosa è cambiato rispetto alla versione precedente del documento

**Difetti risolti (da marcare come RESOLVED):** D1 (PLN-008 — `plan_aborted`), D2 (chiave layer corretta in `CreatePlan`), D3 (chiave `invocation_confirmation` in StateManager), D4 (ordine SystemMessages in FinalResponder), D5 (clarify iterativo non ricorsivo), D7 (PLN-009 — step-checkpoint), D9 (auto_confirm per piano vuoto), D10 (HumanMessage nel classifier).

**Difetti nuovi identificati dall'analisi del codice:**

- **D11** — in `_handle_no_tool_calls` nei due agenti: `state["messages"] = invocation` è un singolo `AIMessage`, non una lista. Il reducer `add_messages` attende una lista.
- **D12** — gli agenti specializzati (`_build_invocation_messages`) costruiscono la chiamata LLM solo con system prompt + goal, senza includere `state["messages"]`. La cronologia della conversazione è completamente opaca all'agente.
- **D13** — analogo per il supervisor: `_generate_plan` non include la cronologia.
- **D14** — il percorso "no tool calls" incrementa `current_step` **due volte**: una nell'Agent (`_handle_no_tool_calls`) e una nell'Executor (guard `if has_no_tool_calls`). Questo salta lo step successivo.
- **D15** — nessun limite alle iterazioni modify/reject nel supervisor loop.
- **D16** — nessun try/except attorno a `tool._execute()` negli executor.

**Nuovi percorsi documentati:** Percorso 8 (no-tool-calls con double-increment), Percorso 9 (step-checkpoint abort), Percorso 10 (multi-turn con layer persistence e il ritardo del context refresh).

**Sezione G010 — Limiti architetturali** (nuova): piano fisso, goal come unico canale, assenza error recovery, LayersAgent chiamato a ogni re-enter, pattern confirm/reject per tutti gli agenti indipendentemente dalla necessità.---