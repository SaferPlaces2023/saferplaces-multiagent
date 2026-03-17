# G — Flusso di Esecuzione del Grafo Multi-Agente

> **Documento:** Specifica funzionale vivente del grafo LangGraph.
> Descrive topologia, ciclo di vita dello stato, percorsi di esecuzione e difetti noti.
>
> **Prefisso ID:** `G###` — riservato a questa specifica.
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
  ├──► MODELS_SUBGRAPH ────────────────────────────────►┤
  ├──► FINAL_RESPONDER ──► END                           │
  └──► END                                               │
                          (entrambi riportano al loop)   │
                                                         │
                    loop su ogni step del piano ─────────┘
```

> **Nota (PLN-009):** Il nodo `SUPERVISOR_ROUTER` interno al `SUPERVISOR_SUBGRAPH` può emettere un interrupt opzionale con `interrupt_type = "step-checkpoint"` tra uno step e il successivo, quando `SupervisorRouter(enabled=True)`. Questo non modifica la topologia del grafo principale — è un comportamento interno al nodo già presente.

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
                          ┌────────────┴────────────┐
                        True                      False
                          │                          │
                    SUPERVISOR_AGENT           SUPERVISOR_ROUTER
                    (re-enters loop)                 │
                                              (EXIT subgraph)
```

> Aggiornata con PLN-008: la condizione dell'arco è ora composta. Se `plan_aborted == True` il subgraph non rientra in `SUPERVISOR_AGENT` ma termina verso `SUPERVISOR_ROUTER`.

### Retriever Subgraph

```
START ──► RETRIEVER_AGENT ──► RETRIEVER_INVOCATION_CONFIRM
                                       │
                    retriever_invocation_confirmation == "rejected"?
                          ┌────────────┴──────────────┐
                        True                        False
                          │                            │
                    RETRIEVER_AGENT             RETRIEVER_EXECUTOR
                                                  (EXIT subgraph)
```

### Models Subgraph

Struttura identica al Retriever Subgraph, con i nodi corrispondenti:
`MODELS_AGENT → MODELS_INVOCATION_CONFIRM → MODELS_EXECUTOR`

---

## G003 — Descrizione dei Nodi

### REQUEST_PARSER

**File:** `ma/chat/request_parser.py`
**Classe:** `RequestParser`
**Prompt:** `RequestParserPrompts.MainContext.stable()`

**Ruolo:** Punto di ingresso di ogni ciclo di richiesta. Analizza l'ultimo messaggio utente ed estrae intento e entità.

**Logica di esecuzione:**
1. Chiama `StateManager.initialize_new_cycle(state)` — resetta tutto lo stato temporaneo del ciclo precedente.
2. Se `messages` è vuoto, ritorna senza elaborazione.
3. Se l'ultimo messaggio non è un `HumanMessage`, ritorna senza elaborazione.
4. Invoca l'LLM con l'intera cronologia (tranne l'ultimo messaggio) + `SystemMessage` di contesto + l'ultimo `HumanMessage`.
5. Popola `state["parsed_request"]` con `{intent, entities, raw_text}`.
6. Imposta `state["awaiting_user"] = False`.

**Stato in uscita:**

| Campo | Valore |
|---|---|
| `parsed_request` | `{intent, entities, raw_text}` |
| `plan` | `None` (azzerato da `initialize_new_cycle`) |
| `tool_results` | `{}` |
| `awaiting_user` | `False` |

---

### SUPERVISOR_AGENT

**File:** `ma/orchestrator/supervisor.py`
**Classe:** `SupervisorAgent`
**Prompt:** `OrchestratorPrompts.MainContext.stable()` + (create/incremental/total) plan prompt

**Ruolo:** Genera il piano di esecuzione multi-step.

**Logica di esecuzione:**
1. `_should_skip_planning()`: salta se `awaiting_user == True` oppure se esiste già un piano accettato con `current_step` definito (il nodo viene rientrante durante il loop multi-step).
2. Se `plan_confirmation == "rejected"`:
   - `replan_type == "modify"` → usa `IncrementalReplanning`
   - `replan_type == "reject"` o `None` → usa `TotalReplanning`
3. Altrimenti → usa `CreatePlan`
4. L'LLM produce un `ExecutionPlan` (schema Pydantic) con lista di `PlanStep {agent, goal}`.
5. Filtra gli step: accetta solo agenti presenti nell'`AGENT_REGISTRY` del supervisor.
6. Imposta: `plan`, `current_step=0`, `plan_confirmation="pending"`, `replan_request=None`, `replan_type=None`.

**Agent Registry disponibile:**

| Nome agente | Subgraph | Descrizione |
|---|---|---|
| `models_subgraph` | `MODELS_SUBGRAPH` | Esecuzione modelli ambientali (flood, fire, …) |
| `retriever_subgraph` | `RETRIEVER_SUBGRAPH` | Recupero dati meteorologici/osservativi |

---

### SUPERVISOR_PLANNER_CONFIRM

**File:** `ma/orchestrator/supervisor.py`
**Classe:** `SupervisorPlannerConfirm`
**Prompt:** `OrchestratorPrompts.Plan.PlanConfirmation.*`

**Ruolo:** Checkpoint human-in-the-loop per l'approvazione del piano.

**Modalità di funzionamento:** Controllata dal flag `enabled` (costruttore).

#### Modalità `enabled=False` (default in produzione)
`_auto_confirm()` viene chiamato direttamente da `__call__`, impostando `plan_confirmation = "accepted"`.
La `run()` non viene mai eseguita.

#### Modalità `enabled=True` (interattiva)

Se il piano è vuoto o già confermato, ritorna senza fare nulla.

Altrimenti:
1. Genera un messaggio di conferma via LLM (`RequestGenerator.stable(state)`).
2. Emette `interrupt({"content": ..., "interrupt_type": "plan-confirmation"})`.
3. Attende risposta utente.
4. Classifica la risposta con `ZeroShotClassifier` in: `accept | modify | clarify | reject | abort`.
5. Dispatchizza al handler corrispondente.

**Tabella di dispatch:**

| Intent classificato | Handler | Effetto sullo stato |
|---|---|---|
| `accept` | `_handle_accept` | `plan_confirmation = "accepted"` |
| `modify` | `_handle_modify` | `plan_confirmation = "rejected"`, `replan_type = "modify"`, `replan_request = HumanMessage(feedback)` |
| `reject` | `_handle_reject` | `plan_confirmation = "rejected"`, `replan_type = "reject"`, `replan_request = HumanMessage(feedback)` |
| `clarify` | `_handle_clarify` | Interrupt ricorsivo con spiegazione LLM, poi ri-classifica |
| `abort` | `_handle_abort` | `plan = []`, `plan_confirmation = "rejected"`, `supervisor_next_node = FINAL_RESPONDER` |

**Gestione `clarify`:** Fino a `max_clarify_iterations=3` iterazioni. Genera spiegazione via LLM (`RequestExplanation.stable(state, user_question)`), emette nuovo interrupt, ri-classifica. Dopo il limite: auto-accept.

---

### SUPERVISOR_ROUTER

**File:** `ma/orchestrator/supervisor.py`
**Classe:** `SupervisorRouter`

**Ruolo:** Determina il prossimo nodo del grafo in base allo stato del piano.

**Costruttore:** `SupervisorRouter(enabled=False)` — il flag `enabled` attiva il checkpoint mid-plan interrupt.

**Logica di esecuzione:**
1. `_update_additional_context(state)`: se `layer_registry` non è vuoto e `relevant_layers.is_dirty == True`, invoca `LayersAgent` per aggiornare il contesto con i layer rilevanti. Imposta `is_dirty = False` dopo.
2. **[PLN-009] Checkpoint interrupt (solo se `enabled=True`):** Se `current_step > 0` e il piano ha ancora step da eseguire, emette `interrupt({"interrupt_type": "step-checkpoint", ...})` con riepilogo dello step appena completato e il piano rimanente. Attende risposta utente classificata con `ZeroShotClassifier` (`continue | abort`). Se `abort` → `plan = []`, `plan_aborted = True`, ritorna `FINAL_RESPONDER`. Fallback su risposta non riconosciuta: `continue`.
3. `_determine_next_node(state)`:
   - Se `plan` è vuoto o `None` → `FINAL_RESPONDER`
   - Se `current_step < len(plan)` → legge `plan[current_step]["agent"]`, chiama `StateManager.initialize_specialized_agent_cycle(state, agent_type)`, ritorna il nome del subgraph
   - Se `current_step >= len(plan)` → `FINAL_RESPONDER`
4. Imposta `state["supervisor_next_node"]`.

**Nota:** Il checkpoint avviene DOPO il context refresh (fase 1) e PRIMA del routing (fase 3), in modo che il messaggio all'utente includa il contesto layer aggiornato.

---

### RETRIEVER_AGENT / MODELS_AGENT

**File:** `safercast_agent.py` / `models_agent.py`
**Classi:** `DataRetrieverAgent` / `ModelsAgent`
**Prompt:** `SaferCastPrompts.MainContext.stable()` / `ModelsPrompts.MainContext.stable()`

**Ruolo:** Seleziona e propone i tool call per l'obiettivo del passo corrente.

**Tool disponibili:**
- Retriever: `DPCRetrieverTool`, `MeteoblueRetrieverTool`
- Models: `SaferRainTool`, `DigitalTwinTool`

**Logica di esecuzione:**
1. Seleziona il prompt in base allo stato di conferma:
   - `invocation_confirmation == "rejected"` → `ReinvocationRequest.stable(state)` (con feedback utente)
   - altrimenti → `InitialRequest.stable(state)` (con obiettivo dal piano + layer rilevanti)
2. Invoca l'LLM con tool binding.
3. Se **nessun tool call** generato: `current_step += 1`, aggiunge `invocation` ai messaggi, ritorna.
4. Se tool call generati: imposta `{agent}_invocation = AIMessage`, `{agent}_invocation_confirmation = "pending"`.

---

### RETRIEVER_INVOCATION_CONFIRM / MODELS_INVOCATION_CONFIRM

**File:** `safercast_agent.py` / `models_agent.py`
**Classi:** `DataRetrieverInvocationConfirm` / `ModelsInvocationConfirm`

**Ruolo:** Inferisce, valida e (opzionalmente) chiede conferma utente per i tool call.

**Modalità `enabled=False` (default):** Inferenza + validazione, poi auto-conferma (`invocation_confirmation = "accepted"`).

**Modalità `enabled=True`:** Inferenza + validazione, poi interrupt utente.

**Flusso dettagliato:**
1. **Inferenza (`_apply_inference_to_args`):** per ogni tool call, chiama `tool._set_args_inference_rules()` e compila gli argomenti mancanti dallo stato.
2. **Validazione (`_validate_args`):** chiama `tool._set_args_validation_rules()` e verifica ogni argomento.
3. Se errori di validazione → `_handle_validation_failure()`:
   - Genera messaggio di errore via LLM.
   - Interrupt con `interrupt_type = "invocation-validation"`.
   - Risposta utente → `ToolValidationResponseHandler.process_validation_response()` → classifica in `accept/modify/clarify/reject/abort`.
4. Se nessun errore e `enabled=True` → interrupt di conferma manuale.
5. Se nessun errore e `enabled=False` → auto-conferma.

**Arco condizionale sul subgraph:**

| `invocation_confirmation` | Destinazione |
|---|---|
| `"rejected"` | Agent (reinvocazione) |
| tutto il resto | Executor |

---

### RETRIEVER_EXECUTOR / MODELS_EXECUTOR

**File:** `safercast_agent.py` / `models_agent.py`

**Ruolo:** Esegue i tool call validati e aggiorna lo stato.

**Flusso:**
1. Per ogni `tool_call` in `invocation.tool_calls[current_step:]`:
   - Chiama `tool._execute(**args)` (args già completi e validati da Confirm).
   - Formatta la risposta (`ToolMessage`).
   - Aggiunge il layer al registry tramite `LayersAgent`.
   - Registra il risultato in `tool_results`.
   - Chiama `StateManager.mark_agent_step_complete(state, agent_type)`.
2. `current_step += 1` (avanza il piano).
3. Aggiunge `[invocation, *tool_responses]` ai messaggi.
4. Imposta `is_dirty = True` in `additional_context.relevant_layers` (nuovo layer disponibile).

---

### FINAL_RESPONDER

**File:** `ma/chat/final_responder.py`
**Classe:** `FinalResponder`
**Prompt:** `FinalResponderPrompts.Response.stable()` + `FinalResponderPrompts.Context.Formatted.stable(state)`

**Ruolo:** Genera la risposta finale all'utente sintetizzando il contesto.

**Flusso:**
1. Costruisce `invoke_messages = [*state["messages"], SystemMessage(response_prompt), AIMessage(context)]`.
2. Invoca LLM.
3. Imposta `state["messages"] = [AIMessage(response.content)]`.
4. Chiama `StateManager.cleanup_on_final_response(state)`.

---

## G004 — Ciclo di Vita dello Stato

### Mappa delle mutazioni per nodo

| Nodo | Chiavi scritte | Note |
|---|---|---|
| `REQUEST_PARSER` | `parsed_request`, `plan=None`, `tool_results={}`, `additional_context.relevant_layers.is_dirty=True`, tutti i campi agente | `StateManager.initialize_new_cycle()` |
| `SUPERVISOR_AGENT` | `plan`, `current_step=0`, `plan_confirmation="pending"`, `replan_request=None`, `replan_type=None` | Skip se piano già accepted |
| `SUPERVISOR_PLANNER_CONFIRM` | `plan_confirmation`, `replan_request`, `replan_type`, `clarify_iteration_count` | Solo se `enabled=True` |
| `SUPERVISOR_ROUTER` | `supervisor_next_node`, `additional_context.relevant_layers`, `layers_*` | Refresh context se `is_dirty` |
| `{AGENT}_AGENT` | `{agent}_invocation`, `{agent}_invocation_confirmation`, `{agent}_reinvocation_request`, `current_step` | `current_step` solo se no tool calls |
| `{AGENT}_INVOCATION_CONFIRM` | `{agent}_invocation.tool_calls[*].args` (mutazione in-place!), `{agent}_invocation_confirmation`, `{agent}_reinvocation_request` | Inferenza e validazione |
| `{AGENT}_EXECUTOR` | `current_step`, `messages`, `tool_results`, `layer_registry`, `additional_context.relevant_layers.is_dirty=True` | |
| `FINAL_RESPONDER` | `messages`, tutti i campi temporanei → `None` | `StateManager.cleanup_on_final_response()` |

### Chiavi persistenti attraverso i cicli

Le seguenti chiavi **non vengono azzerate** da `cleanup_on_final_response`:

- `layer_registry` — registry cumulativo dei layer geospaziali
- `user_drawn_shapes` — forme disegnate dall'utente
- `user_id`, `project_id` — identità di sessione
- `messages` — cronologia completa della conversazione

---

## G005 — Percorsi di Esecuzione

### Percorso 1 — Query informativa (piano vuoto)

```
Utente: "Che cos'è il radar DPC?"

REQUEST_PARSER
  parsed_request = {intent: "explain DPC radar", ...}

SUPERVISOR_AGENT
  plan = []   ← LLM decide che non servono agenti
  plan_confirmation = "pending"

SUPERVISOR_PLANNER_CONFIRM (enabled=True)
  not plan → True → ritorna STATE INVARIATO (plan_confirmation rimane "pending")
  ─── oppure (enabled=False) ───
  _auto_confirm() → plan_confirmation = "accepted"

SUPERVISOR_ROUTER
  not plan → True → supervisor_next_node = "final_responder"

FINAL_RESPONDER
  → Risposta LLM basata su cronologia + contesto
```

---

### Percorso 2 — Piano multi-step accettato (caso nominale)

```
Utente: "Recupera dati DPC per Milano e poi simula alluvione con SaferRain"

REQUEST_PARSER
  parsed_request = {intent: "retrieve DPC + flood simulation", entities: ["Milano", "SaferRain"]}

SUPERVISOR_AGENT
  plan = [
    {agent: "retriever_subgraph", goal: "Retrieve DPC radar data for Milan"},
    {agent: "models_subgraph", goal: "Run SaferRain flood simulation"}
  ]
  current_step = 0

SUPERVISOR_PLANNER_CONFIRM
  → (se enabled=True) interrupt → utente: "sì, procedi" → accept → plan_confirmation = "accepted"
  → (se enabled=False) auto-confirm

SUPERVISOR_ROUTER
  plan[0].agent = "retriever_subgraph"
  initialize_specialized_agent_cycle(state, "retriever")
  supervisor_next_node = "retriever_subgraph"

RETRIEVER_SUBGRAPH:
  RETRIEVER_AGENT: LLM propone DPCRetrieverTool({product, bbox, time_range, ...})
  RETRIEVER_INVOCATION_CONFIRM: inferenza args → validazione → auto-confirmed
  RETRIEVER_EXECUTOR: esegue DPCRetrieverTool → layer aggiunto a layer_registry
    current_step = 1

SUPERVISOR_SUBGRAPH (re-enter):
  SUPERVISOR_AGENT: _should_skip_planning() → True (plan accepted, step 1/2) → skip
  SUPERVISOR_PLANNER_CONFIRM: plan_confirmation = "accepted" != "pending" → ritorna senza azione
  SUPERVISOR_ROUTER:
    layer_registry dirty → refresh relevant_layers
    plan[1].agent = "models_subgraph"
    initialize_specialized_agent_cycle(state, "models")
    supervisor_next_node = "models_subgraph"

MODELS_SUBGRAPH:
  MODELS_AGENT: LLM propone SaferRainTool({dem, rain, ...})
  MODELS_INVOCATION_CONFIRM: inferenza → validazione → auto-confirmed
  MODELS_EXECUTOR: esegue SaferRainTool → layer flood aggiunto a layer_registry
    current_step = 2

SUPERVISOR_SUBGRAPH (re-enter):
  SUPERVISOR_AGENT: skip (current_step=2 == len(plan)=2? No: skip usa >=)
    Aspetta — _should_skip_planning():
      plan is not None → True
      plan_confirmation == "accepted" → True
      current_step = 2, len(plan) = 2 → la condizione di skip usa < implicito:
        stampa "Step 2/2" e ritorna True → SKIP
  SUPERVISOR_ROUTER:
    current_step(2) >= len(plan)(2) → supervisor_next_node = "final_responder"

FINAL_RESPONDER → risposta + cleanup
```

---

### Percorso 3 — Modifica piano (modify)

```
SUPERVISOR_PLANNER_CONFIRM (enabled=True):
  Utente: "Rimuovi il secondo step, fai solo il retrieval"
  intent classificato: "modify"
  → plan_confirmation = "rejected", replan_type = "modify"
  → replan_request = HumanMessage("Rimuovi il secondo step...")
  
  Arco condizionale: plan_confirmation == "rejected" → True → SUPERVISOR_AGENT

SUPERVISOR_AGENT:
  plan_confirmation == "rejected" → _generate_plan()
  replan_type == "modify" → IncrementalReplanning prompt
  → Nuovo piano: [{agent: "retriever_subgraph", goal: "..."}]

SUPERVISOR_PLANNER_CONFIRM:
  → Nuovo interrupt con piano modificato
```

---

### Percorso 4 — Rifiuto totale piano (reject)

```
SUPERVISOR_PLANNER_CONFIRM:
  Utente: "No, questo approccio è sbagliato, fai diversamente"
  intent: "reject"
  → plan_confirmation = "rejected", replan_type = "reject"

SUPERVISOR_AGENT:
  TotalReplanning prompt con piano precedente come contesto
  → Piano completamente nuovo
```

---

### Percorso 5 — Validazione argomenti fallita (invocation-validation interrupt)

```
RETRIEVER_INVOCATION_CONFIRM:
  LLM ha proposto: DPCRetrieverTool({product: "invalid_product", bbox: null})
  Validazione: {product: "prodotto non valido", bbox: "obbligatorio"}
  → interrupt("invocation-validation", errori formattati)

Utente: "Usa SRI come prodotto e bbox 8.0,44.0,10.0,46.0"
  → ValidationResponseHandler: intent = "modify"
  → retriever_invocation_confirmation = "rejected"
  → retriever_reinvocation_request = HumanMessage(feedback)

Arco condizionale subgraph: retriever_invocation_confirmation == "rejected" → RETRIEVER_AGENT

RETRIEVER_AGENT:
  confirmation == "rejected" → ReinvocationRequest prompt con feedback
  → Nuovo LLM call con feedback utente incorporato
```

---

### Percorso 6 — Chiarimento piano (clarify loop)

```
SUPERVISOR_PLANNER_CONFIRM:
  Utente: "Cosa fa esattamente il retriever nel passo 1?"
  intent: "clarify"
  clarify_iteration_count = 1

  _generate_plan_explanation(state, user_question)
  → interrupt("plan-clarification", spiegazione + "Vuoi procedere?")

Utente: "Ok, procedi"
  intent: "accept" → plan_confirmation = "accepted", clarify_iteration_count = 0
```

---

### Percorso 7 — Abort (annullamento)

> **⚠ Percorso difettoso — vedere G007-D1**

```
SUPERVISOR_PLANNER_CONFIRM:
  Utente: "Annulla tutto, non voglio fare niente"
  intent: "abort"
  → plan = [], plan_confirmation = "rejected", supervisor_next_node = "final_responder"

Arco condizionale: plan_confirmation == "rejected" → True → SUPERVISOR_AGENT  ← BUG

SUPERVISOR_AGENT:
  plan_confirmation == "rejected", replan_type = None
  → TotalReplanning con piano vuoto e nessun feedback
  → LLM genera un piano NUOVO basato sul parsed_request originale!
  → Utente viene di nuovo interrogato per conferma
  ← Loop potenzialmente infinito
```

---

## G006 — Ruolo dei Prompt nel Controllo del Flusso

I prompt non sono solo istruzioni per l'LLM: **dirigono attivamente il flusso del grafo** attraverso i valori che l'LLM produce nello stato.

| Prompt | Impatto sul grafo |
|---|---|
| `MainContext.stable()` (supervisor) | Determina se il piano generato è vuoto o ha step → routing a FINAL_RESPONDER vs subgraph |
| `CreatePlan.stable(state)` | Qualità del piano (agenti selezionati, step order) → percorso di esecuzione intero |
| `IncrementalReplanning` / `TotalReplanning` | Piano modificato → eventuali step aggiuntivi o rimossi |
| `ZeroShotClassifier.stable(user_response)` | Classifica `accept/modify/clarify/reject/abort` → arco condizionale in supervisor subgraph |
| `InitialRequest.stable(state)` (agent) | Tool call proposti → numero di invocazioni, argomenti, validità |
| `ReinvocationRequest.stable(state)` | Correzione tool call dopo feedback → potenziale nuovo ciclo di validazione |
| `RequestParserPrompts.MainContext.stable()` | Qualità dell'intento estratto → accuratezza di tutto il piano a valle |

### Dipendenza critica: contesto mancante al planning

`OrchestratorPrompts.Plan.CreatePlan.stable(state)` legge:
```python
additional_context = state.get("plan_additional_context", "No additional context available")
```
Ma la chiave non è `"plan_additional_context"` — è `"additional_context"`. Il supervisor **pianifica sempre senza contesto dei layer disponibili** (vedere G007-D2).

---

## G007 — Difetti Noti

### D1 — Abort non raggiunge FINAL_RESPONDER

**Gravità:** Alta — il flusso si inceppa in un loop non intenzionale.

**Descrizione:** `_handle_abort()` imposta `plan_confirmation = "rejected"` per segnalare fine dell'operazione, ma l'arco condizionale del supervisor subgraph su `plan_confirmation == "rejected"` reindirizza al `SUPERVISOR_AGENT`, che ripianifica da zero ignorando la richiesta di annullamento.

**File:** [`supervisor.py`](../src/saferplaces_multiagent/ma/orchestrator/supervisor.py) — `_handle_abort()` e arco condizionale in `build_supervisor_subgraph()`

**Causa radice:** L'arco condizionale sul subgraph non distingue tra "reject per replan" e "abort per cancellazione". `supervisor_next_node` viene scritto ma non viene usato per uscire dal subgraph.

**Fix suggerito:** Aggiungere un flag dedicato di abort nello stato (es. `plan_aborted: bool`) e valutarlo sia in `_should_skip_planning()` che nell'arco condizionale del supervisor subgraph.

---

### D2 — SupervisorAgent pianifica sempre senza contesto layer

**Gravità:** Alta — il planning ignora i layer geospaziali già presenti.

**Descrizione:** `OrchestratorPrompts.Plan.CreatePlan.stable(state)` legge `state.get("plan_additional_context")` ma questa chiave non esiste nel TypedDict `MABaseGraphState` né viene mai scritta nel grafo. Il contesto rilevante dei layer, aggiornato da `SupervisorRouter._update_additional_context()`, è in `state["additional_context"]["relevant_layers"]`.

**File:** [`supervisor_agent_prompts.py`](../src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py) — `CreatePlan.stable()`, riga `state.get("plan_additional_context", ...)`

**Fix suggerito:** Sostituire con `state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])`.

---

### D3 — StateManager usa chiave errata per pulire lo stato agente

**Gravità:** Media — lo stato dell'agente precedente non viene azzerato correttamente.

**Descrizione:** `StateManager._clear_specialized_agent_state()` e `initialize_specialized_agent_cycle()` usano `state[f'{prefix}_confirmation'] = None`, ma le chiavi reali nello stato sono `{prefix}_invocation_confirmation` (es. `retriever_invocation_confirmation`). La chiave `retriever_confirmation` non è definita nel TypedDict e non viene mai letta, rendendo il cleanup silenziosamente inefficace.

**File:** [`states.py`](../src/saferplaces_multiagent/common/states.py) — `_clear_specialized_agent_state()` e `initialize_specialized_agent_cycle()`

**Fix suggerito:** Sostituire `f'{prefix}_confirmation'` con `f'{prefix}_invocation_confirmation'` nei metodi `StateManager`.

---

### D4 — FinalResponder inietta context come AIMessage

**Gravità:** Bassa — degrada la qualità della risposta.

**Descrizione:** `FinalResponder.run()` costruisce i messaggi come:
```python
[*state["messages"], SystemMessage(response_prompt), AIMessage(context)]
```
Il `SystemMessage` arriva dopo l'intera cronologia (posizione inusuale) e il contesto strutturato viene iniettato come `AIMessage`, che il modello interpreta come un messaggio proprio precedente, non come informazione di sistema. Questo può causare allucinazioni o risposte incoerenti.

**File:** [`final_responder.py`](.final_responder.py) — `run()`

**Fix suggerito:** Mettere `SystemMessage(response_prompt)` e `SystemMessage(context)` prima di `state["messages"]`, o usare un secondo `SystemMessage` all'inizio della lista.

---

### D5 — Interrupt ricorsivo in `_handle_clarify`

**Gravità:** Media — potenziale instabilità con LangGraph checkpoint.

**Descrizione:** `_handle_clarify()` chiama se stessa ricorsivamente e all'interno di ogni chiamata ricorsiva emette un nuovo `interrupt()`. LangGraph serializza il punto di interruzione nello stack di chiamata: la ricorsione può non essere restaurata correttamente al resume, causando un frame errato o una `RecursionError` non catturata.

**File:** [`supervisor.py`](.supervisor.py) — `_handle_clarify()`

**Fix suggerito:** Trasformare la ricorsione in un loop iterativo all'interno del nodo, con un contatore esplicito, evitando chiamate `interrupt()` annidate.

---

### D6 — `awaiting_user = True` non viene mai impostato

**Gravità:** Bassa — dead code che può generare confusione.

**Descrizione:** `SupervisorRouter._determine_next_node()` contiene:
```python
if state.get("awaiting_user"):
    return "END"
```
Ma nessun nodo nel grafo imposta `awaiting_user = True` in modo stabile. `RequestParser` e i vari handler la impostano a `False`. Il percorso è dead code.

**File:** [`supervisor.py`](.supervisor.py) — `_determine_next_node()`

---

### D7 — Nessun meccanismo per interrompere un piano in esecuzione

**Gravità:** Alta — ~~gap funzionale per UX interattive.~~ **Risolto con PLN-009.**

**Descrizione:** ~~Non esiste un interrupt tra due step successivi di un piano multi-step (es. tra RETRIEVER_EXECUTOR e il re-enter in SUPERVISOR_SUBGRAPH). Se l'utente invia un nuovo messaggio durante l'esecuzione, il grafo lo processa solo al termine del piano corrente. Non c'è modo per l'utente di dire "stop, cambia approccio" a mid-execution salvo durante i nodi di confirm (che sono `enabled=False` in produzione).~~

**Fix applicato (PLN-009):** Introdotto checkpoint interrupt in `SupervisorRouter` controllato dal flag `enabled`. Con `SupervisorRouter(enabled=True)`, dopo ogni step completato (se il piano ha ancora step), viene emesso un `interrupt_type = "step-checkpoint"` con riepilogo e piano rimanente. L'utente può rispondere `continue` o `abort`. Vedere G003 per la logica completa e G008 per la riga corrispondente nella tabella interrupt.

---

### D8 — `_handle_no_tool_calls` incrementa `current_step` senza verifica

**Gravità:** Bassa — potenziale `TypeError` se `current_step` è `None`.

**Descrizione:** In `DataRetrieverAgent._handle_no_tool_calls()` e `ModelsAgent._handle_no_tool_calls()`:
```python
state["current_step"] += 1
```
Se `current_step` è `None` (ad esempio se `initialize_specialized_agent_cycle` non lo ha settato correttamente a causa di D3), questa operazione genera `TypeError: unsupported operand type(s) for +=: 'NoneType' and 'int'`.

**File:** [`safercast_agent.py`](.safercast_agent.py), [`models_agent.py`](.models_agent.py)

---

### D9 — Piano vuoto con `enabled=True`: plan_confirmation rimane "pending"

**Gravità:** Bassa — stato logicamente inconsistente.

**Descrizione:** Con `SupervisorPlannerConfirm(enabled=True)`, quando il piano è vuoto (`plan = []`), `run()` ritorna immediatamente con `plan_confirmation` ancora `"pending"`. Il routing funziona correttamente (`not plan → FINAL_RESPONDER`) ma lo stato non viene pulito in modo coerente: `cleanup_on_final_response` azzerà `plan_confirmation = None`, ma durante il ciclo rimane `"pending"`.

---

### D10 — Classificazione plan_confirmation: entrambi i messaggi sono SystemMessage

**Gravità:** Bassa — compatibilità LLM.

**Descrizione:** In `_classify_user_response()`:
```python
messages = [
    OrchestratorPrompts.Plan.PlanConfirmation.ResponseClassifier.ClassifierContext.stable().to(SystemMessage),
    OrchestratorPrompts.Plan.PlanConfirmation.ResponseClassifier.ZeroShotClassifier.stable(user_response).to(SystemMessage)
]
```
Entrambi i messaggi sono `SystemMessage`. Molti provider LLM (es. Anthropic) accettano un solo `SystemMessage` iniziale e ignorano o aggregano i successivi. Il secondo messaggio (il classificatore effettivo) potrebbe non avere l'effetto desiderato.

**Fix suggerito:** Convertire il secondo prompt in `HumanMessage`.

---

## G008 — Tabella Riepilogativa Interrupt Points

| Nodo | interrupt_type | Condizione | Handler risposta |
|---|---|---|---|
| `SUPERVISOR_PLANNER_CONFIRM` | `plan-confirmation` | `enabled=True` e piano non vuoto | `ZeroShotClassifier` → dispatch accept/modify/clarify/reject/abort |
| `SUPERVISOR_PLANNER_CONFIRM` | `plan-clarification` | Dopo spiegazione LLM (clarify loop) | Stesso classifier |
| `SUPERVISOR_ROUTER` | `step-checkpoint` | `enabled=True`, `current_step > 0`, piano non esaurito | `ZeroShotClassifier` → `continue` (procedi) o `abort` (FINAL_RESPONDER) |
| `RETRIEVER_INVOCATION_CONFIRM` | `invocation-validation` | Argomenti non validi | `ToolValidationResponseHandler` |
| `RETRIEVER_INVOCATION_CONFIRM` | `invocation-confirmation` | `enabled=True` e validazione OK | `ToolInvocationConfirmationHandler` |
| `MODELS_INVOCATION_CONFIRM` | `invocation-validation` | Argomenti non validi | `ToolValidationResponseHandler` |
| `MODELS_INVOCATION_CONFIRM` | `invocation-confirmation` | `enabled=True` e validazione OK | `ToolInvocationConfirmationHandler` |

---

## G009 — Stato `MABaseGraphState`: Chiavi per Fase

| Chiave | Tipo | Fase | Persistenza |
|---|---|---|---|
| `messages` | `list[AnyMessage]` | Sempre | Permanente |
| `user_id` / `project_id` | `str` | Sempre | Permanente |
| `layer_registry` | `list[dict]` | Sempre | Permanente (cumulativa) |
| `user_drawn_shapes` | `list[dict]` | Sempre | Permanente |
| `nowtime` | `str` | Sempre | Inizializzata a costruzione |
| `parsed_request` | `dict` | REQUEST_PARSER → FINAL_RESPONDER | Ciclo |
| `additional_context` | `dict` | SUPERVISOR_ROUTER → FINAL_RESPONDER | Ciclo |
| `plan` | `list[dict]` | SUPERVISOR_AGENT → FINAL_RESPONDER | Ciclo |
| `current_step` | `int` | SUPERVISOR_AGENT → FINAL_RESPONDER | Ciclo |
| `plan_confirmation` | `str` | SUPERVISOR_AGENT → FINAL_RESPONDER | Ciclo |
| `replan_request` / `replan_type` | `AnyMessage` / `str` | SUPERVISOR_PLANNER_CONFIRM → SUPERVISOR_AGENT | Ciclo |
| `supervisor_next_node` | `str` | SUPERVISOR_ROUTER → routing main graph | Ciclo |
| `tool_results` | `dict` | Executors → FINAL_RESPONDER | Ciclo |
| `retriever_invocation` | `AIMessage` | RETRIEVER_AGENT → RETRIEVER_EXECUTOR | Step |
| `retriever_invocation_confirmation` | `str` | RETRIEVER_CONFIRM → arco condizionale | Step |
| `models_invocation` | `AIMessage` | MODELS_AGENT → MODELS_EXECUTOR | Step |
| `models_invocation_confirmation` | `str` | MODELS_CONFIRM → arco condizionale | Step |
| `layers_request` / `layers_invocation` / `layers_response` | vari | SUPERVISOR_ROUTER (context refresh) | Temporanea |
| `awaiting_user` | `bool` | Reservato per interrupt mid-plan (G007-D6) — attualmente non impostato a `True` da nessun nodo; ramo `→ END` annotato come dead code | — |
| `clarify_iteration_count` | `int` | SUPERVISOR_PLANNER_CONFIRM (clarify loop) | Ciclo |
| `plan_aborted` | `bool` | SUPERVISOR_PLANNER_CONFIRM → SUPERVISOR_SUBGRAPH | Ciclo |

### Aggiornamenti mutazioni con `plan_aborted` (PLN-008)

| Nodo | Chiavi scritte | Note |
|---|---|---|
| `REQUEST_PARSER` | `parsed_request`, `plan=None`, `tool_results={}`, `additional_context.relevant_layers.is_dirty=True`, tutti i campi agente, **`plan_aborted=False`** | `StateManager.initialize_new_cycle()` |
| `SUPERVISOR_PLANNER_CONFIRM` | `plan_confirmation`, `replan_request`, `replan_type`, `clarify_iteration_count`, **`plan_aborted=True`** (solo in `_handle_abort`) | Solo se `enabled=True` |
| `FINAL_RESPONDER` | `messages`, tutti i campi temporanei → `None`, **`plan_aborted=False`** | `StateManager.cleanup_on_final_response()` |
```

---

Oltre al file, dovrai anche aggiornare [docs/index.md](docs/index.md): aggiungi la riga `G` nella tabella dei prefissi riservati:

```markdown
| `G` | Feature — flusso di esecuzione del grafo (nodi, routing, interrupt, difetti) | `G###` | `G001`–`G009` | `docs/functional-spec-graph.md` |
```

---

**Riepilogo dei difetti identificati:**

| ID | Gravità | Difetto |
|---|---|---|
| D1 | ⛔ Alta | `abort` rientra nel `SUPERVISOR_AGENT` invece di andare a `FINAL_RESPONDER` |
| D2 | ⛔ Alta | Il supervisor legge `plan_additional_context` (chiave inesistente) — planned sempre senza contesto layer |
| D3 | 🟡 Media | `StateManager` usa `{prefix}_confirmation` invece di `{prefix}_invocation_confirmation` |
| D4 | 🟡 Bassa | `FinalResponder` inietta il contesto come `AIMessage` invece di `SystemMessage` |
| D5 | 🟡 Media | `_handle_clarify` usa ricorsione + interrupt annidati — instabile con LangGraph checkpoint |
| D6 | ⚪ Bassa | `awaiting_user = True` → `END`: dead code, mai impostato a `True` |
| D7 | ⛔ Alta | Nessun interrupt tra step di un piano in esecuzione — utente non può intervenire a mid-plan |
| D8 | 🟡 Bassa | `current_step += 1` senza guard su `None` — `TypeError` potenziale |
| D9 | ⚪ Bassa | Piano vuoto con `enabled=True`: `plan_confirmation` rimane `"pending"` invece di `"accepted"` |
| D10 | ⚪ Bassa | Entrambi i messaggi nel classifier della plan-confirmation sono `SystemMessage` |