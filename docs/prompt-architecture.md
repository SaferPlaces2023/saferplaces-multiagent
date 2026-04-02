# Prompt Architecture — SaferPlaces Multiagent

> Documento di riferimento per la strutturazione ottimale dei prompt in ogni nodo del grafo agentico.
> Non descrive i prompt correnti ma il **design ideale** derivato dalla topologia del grafo.

---

## Principi generali

### Composizione a strati (Layered Prompting)

Ogni nodo LLM usa una composizione a **quattro livelli**:

| Layer | Nome | Tipo | Mutabilità |
|---|---|---|---|
| L1 | **Role & Scope** | `SystemMessage` statico | Immutabile a runtime |
| L2 | **World Context** | `SystemMessage` dinamico | Iniettato dallo stato |
| L3 | **Task Instruction** | `HumanMessage` o secondo `SystemMessage` | Per invocazione |
| L4 | **Output Contract** | Parte di L3 o schema Pydantic/JSONSchema | Fisso per nodo |

L1+L4 sono definiti staticamente nei file di prompt. L2+L3 vengono costruiti a runtime dalla `run()` del nodo leggendo lo stato.

### Regole trasversali

- **Nessun nodo** deve duplicare informazioni già presenti in un altro layer: L2 porta solo ciò che L1 non può sapere staticamente.
- **Output contract esplicito**: ogni prompt che produce output strutturato deve includere lo schema JSON con un esempio concreto, non solo la descrizione dei campi.
- **Negative constraints**: ogni sistema prompt include una sezione "Non fare" per ridurre le allucinazioni su usi fuori scope.
- **Lingua utente**: il testo verso l'utente (final responder, confirm nodes) usa la lingua del messaggio originale. I prompt interni (parsing, planning, tool calls) usano sempre l'inglese per massimizzare la capacità del modello.
- **Chain-of-thought selettivo**: usare `<think>` / `<reasoning>` solo nei nodi di alta complessità (SupervisorAgent). Nei nodi di esecuzione prediligere output diretto e strutturato.

---

## Mappa dei nodi LLM

```
START
  └─► STATE_PROCESSOR          [no LLM]
        └─► REQUEST_PARSER      [LLM — structured output]
              └─► SUPERVISOR_SUBGRAPH
                    ├─ SUPERVISOR_AGENT             [LLM — CoT + structured]
                    ├─ SUPERVISOR_PLANNER_CONFIRM   [interrupt — LLM opzionale]
                    └─ SUPERVISOR_ROUTER            [no LLM — deterministico]
                          ├─► RETRIEVER_SUBGRAPH
                          │     ├─ RETRIEVER_AGENT              [LLM — tool calling]
                          │     ├─ RETRIEVER_INVOCATION_CONFIRM [interrupt — no LLM]
                          │     └─ RETRIEVER_EXECUTOR           [no LLM — tool execution]
                          ├─► MODELS_SUBGRAPH
                          │     ├─ MODELS_AGENT                 [LLM — tool calling]
                          │     ├─ MODELS_INVOCATION_CONFIRM    [interrupt — no LLM]
                          │     └─ MODELS_EXECUTOR              [no LLM — tool execution]
                          ├─► MAP_AGENT              [LLM — tool calling]
                          └─► FINAL_RESPONDER        [LLM — generativo]
```

---

## Nodi dettagliati

---

### 1. REQUEST_PARSER

**Ruolo:** trasforma il messaggio grezzo dell'utente in un oggetto strutturato `ParsedRequest`.

**Quando riceve LLM call:** ad ogni nuovo ciclo, prima di entrare nel grafo supervisor.

#### L1 — Role & Scope

```
You are a request analysis specialist for SaferPlaces, an AI-assisted flood simulation platform.
Your only job is to extract structured intent and entities from the user's natural language input.
You do not plan actions, execute tools, or generate responses to the user.
```

#### L2 — World Context (dinamico da stato)

Iniettare:
- **Conversation history** (ultimi N messaggi — per cogliere riferimenti anapforici: "quello", "quel layer", "il progetto precedente")
- **Active project_id / user_id** — per disambiguare entità nominali ("il progetto di Cesena" → ID concreto)
- **Layer registry snapshot** (nomi layer attivi) — per risolvere riferimenti a layer

```
Active project: {project_id}
Known layers: {layer_names_list}
Conversation so far: {recent_messages}
```

#### L3 — Task Instruction

```
Parse the following user message and return a ParsedRequest JSON.
User message: "{raw_text}"
```

#### L4 — Output Contract

Schema obbligatorio (Pydantic o inline JSON schema):

```json
{
  "intent": "<one of: run_simulation | retrieve_data | manage_layers | general_query | clarification>",
  "entities": {
    "location": "<place name or null>",
    "time_range": "<ISO range or null>",
    "layer_refs": ["<layer name or id>"],
    "simulation_params": {},
    "data_source": "<dpc_radar | meteoblue | null>"
  },
  "raw_text": "<original user message verbatim>",
  "ambiguous": true | false,
  "ambiguity_reason": "<string or null>"
}
```

Il campo `ambiguous` guida il router verso un loop di chiarimento senza entrare nel planner.

---

### 2. SUPERVISOR_AGENT

**Ruolo:** genera il piano di esecuzione multi-step (`ExecutionPlan`) come lista ordinata di `{agent, goal}`.

**Pattern:** Chain-of-Thought + Structured Output. È il nodo più cognitivamente complesso — il CoT è giustificato per migliorare la qualità del piano.

#### L1 — Role & Scope

```
You are the orchestrator of a multi-agent system for flood risk analysis.
You receive a parsed user request and must produce an ordered execution plan that delegates work
to specialized sub-agents. You reason step by step before producing the plan.

Available agents:
- retriever: fetches observational rainfall data (DPC radar) and weather forecasts (Meteoblue)
- models: runs flood simulation (SaferRain) on a given scenario
- map: adds, removes, styles or queries geospatial layers on the map

Rules:
- A plan step can only reference one of the agents above.
- Order steps so that dependencies are respected (e.g. retrieve data before running a model).
- If the request is self-contained and needs no sub-agent, produce an empty plan [].
- Never include actions outside the platform's scope.
```

#### L2 — World Context (dinamico)

```
Parsed request:
  intent: {intent}
  entities: {entities}

Current layer registry:
{layer_registry_summary}

Previous tool results (if any):
{tool_results_summary}

Conversation memory:
{recent_exchanges}
```

Il `layer_registry_summary` deve essere compresso: ogni layer su una riga con `id | type | status`. Evitare dump raw del JSON completo.

#### L3 — Task Instruction (con CoT esplicito)

```
Think step by step:
1. What is the user ultimately trying to achieve?
2. What data or preconditions are needed before each step?
3. Which agent is best suited for each sub-task?
4. Are there any ambiguities that would block execution?

Then output the plan.
```

#### L4 — Output Contract

```json
{
  "reasoning": "<CoT trace — not shown to user>",
  "plan": [
    {"agent": "retriever", "goal": "Fetch DPC radar rainfall for ROI in the last 3 hours"},
    {"agent": "models",    "goal": "Run SaferRain simulation using the retrieved radar data"}
  ]
}
```

Il campo `reasoning` viene scartato dal nodo prima di scrivere `plan` nello stato. Questo pattern (scratchpad interno) migliora la qualità senza esporre il ragionamento intermedio all'utente.

---

### 3. SUPERVISOR_PLANNER_CONFIRM

**Ruolo:** formatta il piano per la review umana e gestisce il feedback (accept / reject / modify).

**Pattern:** interrupt node. Non fa un'invocazione LLM standard nel path principale. LLM opzionale solo se il feedback utente è "modify" (per reinterpretare la modifica richiesta e riformulare il piano).

#### Path principale (no LLM — solo formattazione)

Il nodo serializza `state["plan"]` in linguaggio naturale per l'interrupt payload:

```
Piano proposto:
  1. [retriever] → Recupera dati radar DPC per le ultime 3 ore sull'area di interesse
  2. [models]    → Esegui simulazione alluvione SaferRain con i dati recuperati

Vuoi procedere? (sì / no / modifica)
```

La struttura dell'interrupt deve contenere:
- `interrupt_type: "plan-confirmation"`
- `plan_display: [...]` (lista human-readable)
- `plan_raw: [...]` (struttura originale, per il resume)

#### Path di modifica (LLM — riformulazione)

Se il feedback è `"modify"`, il nodo fa una chiamata LLM con:

**L1:** "You are a plan editor. The user has requested a change to an execution plan."
**L2:** piano originale + feedback testuale dell'utente
**L3:** "Produce the revised plan. Change only what the user explicitly requested."
**L4:** stessa struttura `{plan: [...]}` del SupervisorAgent

Questo evita di reinvocare l'intero SupervisorAgent con l'overhead del CoT completo.

---

### 4. SUPERVISOR_ROUTER

**Ruolo:** legge il piano e imposta `supervisor_next_node`.

**Nessun LLM.** Logica puramente deterministica: prende il primo step non completato del piano e mappa `agent` → NodeName. Non richiede prompt.

---

### 5. RETRIEVER_AGENT

**Ruolo:** riceve un `goal` dal piano e genera le chiamate ai tool di recupero dati (DPC radar, Meteoblue).

**Pattern:** ReAct con tool calling. Il modello non esegue i tool — li propone. L'esecutore li lancia.

#### L1 — Role & Scope

```
You are a meteorological data retrieval specialist for SaferPlaces.
You have access to two data sources:
- DPC Radar: near-realtime observed rainfall (Italy coverage, ~15min resolution)
- Meteoblue: global weather forecasts (hourly, up to 7 days ahead)

Your task is to propose tool calls that will fetch the data needed to accomplish a given goal.
You do NOT interpret the data, run models, or communicate with the user.
```

#### L2 — World Context (dinamico)

```
Current project: {project_id}
Geographic area of interest: {bbox_or_location}
Current datetime: {iso_datetime}
Previously fetched layers: {existing_data_layers}
Goal assigned by orchestrator: "{goal}"
```

Il `bbox_or_location` deve essere derivato dal `layer_registry` (extent dei layer attivi) o dal `parsed_request.entities.location`. Questo evita che il modello debba inferire la geometria da zero.

#### L3 — Task Instruction

```
Select and call the appropriate tool(s) to fulfill the goal.
Prefer the most specific tool available. Do not call a tool if equivalent data is already
present in the "Previously fetched layers" list.
```

#### L4 — Output Contract

Output implicito nel formato native tool-calling del framework (LangChain `AIMessage` con `tool_calls`). Il nodo non produce testo libero.

Aggiungere un **system constraint** esplicito:
```
IMPORTANT: output only tool calls. Do not add any explanatory text.
```

---

### 6. RETRIEVER_INVOCATION_CONFIRM

**Ruolo:** mostra all'utente i tool call proposti dall'agente retriever prima di eseguirli.

**Pattern:** interrupt node, nessun LLM. Il nodo serializza i `tool_calls` dell'`AIMessage` in formato leggibile.

Struttura interrupt payload:
```json
{
  "interrupt_type": "invocation-confirmation",
  "agent": "retriever",
  "proposed_calls": [
    {
      "tool": "get_dpc_radar",
      "params": {"date_from": "...", "date_to": "...", "bbox": [...]}
    }
  ],
  "display_text": "Sto per recuperare i dati radar DPC dal ... al ..."
}
```

Il `display_text` è l'unica parte che richiede eventuale formattazione — può essere generato con un mini-prompt one-shot se i tool hanno parametri tecnici da umanizzare, oppure con template string deterministica (preferibile per semplicità e latenza).

---

### 7. RETRIEVER_EXECUTOR

**Nessun LLM.** Esegue i tool call e accumula i risultati in `state["tool_results"]`. Non richiede prompt. Gestione errori deterministica.

---

### 8. MODELS_AGENT

**Ruolo:** genera le chiamate ai tool di simulazione alluvione (SaferRain) dato un `goal`.

**Pattern:** identico a RETRIEVER_AGENT ma dominio diverso.

#### L1 — Role & Scope

```
You are a flood simulation specialist for SaferPlaces.
You operate the SaferRain hydraulic model.
Your task is to propose tool calls that configure and run a flood simulation
to accomplish a given goal. You do NOT interpret results or communicate with the user.

Key concepts:
- A simulation requires: a DEM layer, a rainfall scenario (intensity + duration), output resolution.
- Rainfall input can come from: radar data (already fetched), manual scenario, or Meteoblue forecast.
- You must verify that required input layers are available before proposing a run.
```

#### L2 — World Context (dinamico)

```
Current project: {project_id}
Available input layers: {layer_registry_filtered_by_type}
Previously computed results: {tool_results_summary}
Goal assigned by orchestrator: "{goal}"
```

Filtrare il `layer_registry` per mostrare solo i layer rilevanti per la simulazione (DEM, rainfall raster, boundary polygon) — non tutto il registry completo.

#### L3 — Task Instruction

```
Propose the necessary tool calls to run the simulation.
Verify preconditions: if a required input layer is missing, call the appropriate
preparation tool first. Do not run the simulation if inputs are incomplete.
```

#### L4 — Output Contract

Stesso pattern di RETRIEVER_AGENT: solo `tool_calls`, nessun testo libero.

---

### 9. MODELS_INVOCATION_CONFIRM

**Struttura identica a RETRIEVER_INVOCATION_CONFIRM** con `"agent": "models"`.

Il `display_text` per le simulazioni deve includere parametri chiave (scenario pioggia, risoluzione, area) in forma comprensibile, ad es.:
```
Sto per avviare una simulazione SaferRain:
  • Scenario: pioggia 50mm/h per 2 ore
  • Area: Cesenatico (bbox: ...)
  • Risoluzione output: 5m
```

Se questo testo viene generato via LLM, usare un prompt ultra-breve one-shot:
```
Summarize these simulation parameters in 2-3 Italian bullet points for a non-technical user:
{tool_call_params_json}
```

---

### 10. MODELS_EXECUTOR

**Nessun LLM.** Identico a RETRIEVER_EXECUTOR nel pattern.

---

### 11. MAP_AGENT

**Ruolo:** gestisce operazioni sui layer geospaziali (aggiunta, rimozione, stile, query). Può essere invocato da piano (con `parsed_request` nello stato) o direttamente da `STATE_PROCESSOR` (con `map_request`).

**Pattern:** tool calling, ma con doppio contesto (pianificato o diretto).

#### L1 — Role & Scope

```
You are a GIS layer management specialist for SaferPlaces.
You manage a geospatial layer registry that drives the map visualization.
Operations available: add layer, remove layer, restyle layer, reorder layers, query layer properties.
You do NOT run simulations or fetch external data.
```

#### L2 — World Context (dinamico)

Il context varia in base al path di invocazione:

**Path da piano (supervisore):**
```
Mode: plan-driven
Goal assigned by orchestrator: "{goal}"
Current layer registry:
{full_layer_registry}
```

**Path diretto (map_request):**
```
Mode: direct
User map request: {map_request}
Current layer registry:
{full_layer_registry}
```

La distinzione è importante: nel path da piano, l'agente sa che tornerà al supervisor dopo l'esecuzione. Nel path diretto, è l'ultimo agente e non deve produrre output narrativo.

#### L3 — Task Instruction

```
Propose tool calls to accomplish the map operation.
Always check the current layer registry before adding a duplicate.
For styling operations, use only symbology properties supported by the platform.
```

#### L4 — Output Contract

Solo `tool_calls`. Aggiornamenti al `layer_registry` vengono gestiti dall'executor, non dall'agente.

---

### 12. FINAL_RESPONDER

**Ruolo:** sintetizza tutti i risultati accumulati nello stato in una risposta finale coerente, utile e in linguaggio naturale per l'utente.

**Pattern:** generativo. È il nodo con il prompt più "aperto" ma con i vincoli più forti sulla forma.

#### L1 — Role & Scope

```
You are the communication interface of SaferPlaces, an AI-assisted flood risk platform.
Your task is to synthesize the results of a multi-agent workflow into a clear,
informative response for the end user. You speak directly to a non-technical user
(e.g. civil protection officer, urban planner).

Communication style:
- Concise and factual, no filler phrases
- Use the user's language (Italian, English, etc.)
- Highlight actionable insights over raw technical details
- If results include numerical data, round to meaningful precision
- Never fabricate data not present in the tool results
```

#### L2 — World Context (dinamico)

```
Original user request: "{raw_text}"
Execution plan that was carried out:
{plan_display}

Tool results:
{tool_results_full}

Layer registry after execution (new/modified layers):
{changed_layers}
```

Il `tool_results_full` deve essere iniettato come struttura serializzata, non come dump JSON grezzo — ogni tool result viene prefissato con il suo nome (`## DPC Radar Result`, `## SaferRain Simulation Result`) per aiutare il modello a navigarli.

#### L3 — Task Instruction

```
Write a response that:
1. Confirms what was done (briefly)
2. Presents the key findings or results
3. Mentions new layers added to the map (if any)
4. Flags any warnings or limitations in the data (if present in tool results)
5. Suggests a logical next step if appropriate (optional, only if obvious)
```

#### L4 — Output Contract

Testo libero in markdown leggero (bold per valori chiave, liste per più risultati). **Non** JSON. Il nodo scrive direttamente in `state["messages"]` come `AIMessage`.

Aggiungere constraint di sicurezza:
```
IMPORTANT: base your response strictly on the tool_results provided.
Do not infer, extrapolate, or fabricate data not explicitly present.
```

---

## Pattern combinatori

### Pattern A — Data → Simulate → Respond (caso tipico)

```
REQUEST_PARSER
  → [L1+L2 parsing]
SUPERVISOR_AGENT
  → [L1 orchestrator role + L2 context + L3 CoT]
  → plan: [retriever, models]
RETRIEVER_AGENT
  → [L1 retriever role + L2 bbox/datetime + L3 goal]
  → tool_calls: [get_dpc_radar(...)]
MODELS_AGENT
  → [L1 models role + L2 layers+results + L3 goal]
  → tool_calls: [run_saferrain(...)]
FINAL_RESPONDER
  → [L1 communicator + L2 all_results + L3 synthesis]
```

Il `tool_results` si accumula progressivamente nello stato — ogni L2 dei nodi successivi include i risultati dei nodi precedenti, creando un contesto cumulativo crescente.

### Pattern B — Clarification Loop

Quando REQUEST_PARSER imposta `ambiguous: true`, il SUPERVISOR può generare un piano con un singolo step virtuale `{agent: "clarify", goal: "..."}` oppure (preferibile) il FINAL_RESPONDER viene invocato direttamente con una domanda di chiarimento. In questo caso:

- **L1 del FINAL_RESPONDER** include una variante: "If the request is ambiguous, ask a single, specific clarifying question."
- Il piano viene marcato come `null` o `[]` nello stato
- Il ciclo successivo riprende da REQUEST_PARSER con il contesto aggiornato

### Pattern C — Plan Revision Loop

Quando SUPERVISOR_PLANNER_CONFIRM riceve `"modify"`:

1. Il feedback utente arriva come `HumanMessage`
2. SUPERVISOR_PLANNER_CONFIRM fa una mini-invocazione LLM (L1: "plan editor", L2: piano originale + feedback)
3. Il nuovo piano **sostituisce** quello precedente nello stato
4. Il ciclo continua da SUPERVISOR_ROUTER senza reinvocare il SupervisorAgent

Questo pattern è più efficiente del riavvio completo e preserva la coerenza del piano.

### Pattern D — Map-only (direct path)

Quando `STATE_PROCESSOR` rileva un `map_request` senza `HumanMessage`:

```
STATE_PROCESSOR → MAP_AGENT → END
```

MAP_AGENT usa **solo L1 + L2 (direct mode) + L4**. Non c'è L3 da piano. Nessuna risposta testuale all'utente — solo aggiornamenti al `layer_registry`.

---

## Gestione del contesto e token budget

| Nodo | Porzioni di stato incluse in L2 | Stima token L2 |
|---|---|---|
| REQUEST_PARSER | last N messages, layer names | ~500–800 |
| SUPERVISOR_AGENT | parsed_request, layer_registry (compresso), tool_results (riassunto) | ~1000–2000 |
| RETRIEVER_AGENT | goal, bbox, datetime, existing layers | ~300–600 |
| MODELS_AGENT | goal, relevant layers, previous results | ~500–1000 |
| MAP_AGENT | goal/map_request, full layer_registry | ~500–1500 |
| FINAL_RESPONDER | full tool_results, plan, raw_text | ~1500–4000 |

**Strategie di compressione per L2:**
- `layer_registry` → serializzare solo `id | type | name | status`, non geometrie
- `tool_results` → nei nodi intermedi passare solo un summary (max 3 righe per tool); solo il FINAL_RESPONDER riceve i risultati completi
- `messages history` → nei nodi intermedi usare sliding window (ultimi 6 messaggi); solo REQUEST_PARSER e FINAL_RESPONDER hanno bisogno di storia più lunga

---

## Versionamento e A/B testing

Ogni metodo prompt segue il pattern `stable()` / `v001()` / `v002()`:

| Scenario | Azione raccomandata |
|---|---|
| Modifica della struttura del piano | Nuovo `SupervisorAgent.CreatePlan.v002()` — A/B test vs `stable()` |
| Cambio tono risposta finale | Nuovo `FinalResponder.Synthesis.v002()` |
| Ottimizzazione token | Nuovo metodo con L2 compresso — confrontare qualità output |
| Nuovo agente specializzato | Nuovo modulo `{agent}_agent_prompts.py` seguendo la struttura sopra |

Il patch in test avviene con `unittest.mock.patch.object` — nessuna modifica al codice di produzione.

---

## Checklist per un nuovo nodo LLM

1. [ ] Definire **L1** statico: ruolo, scope, cosa NON fare
2. [ ] Identificare le chiavi di stato per **L2**: filtrare, comprimere, etichettare
3. [ ] Scrivere **L3** come istruzione di task specifica per invocazione
4. [ ] Definire **L4**: schema JSON con esempio concreto, o vincolo "solo tool_calls"
5. [ ] Stimare token budget per L2 nel caso peggiore
6. [ ] Aggiungere il modulo `{agent}_prompts.py` in `ma/prompts/`
7. [ ] Registrare il metodo `stable()` nella classe di prompt appropriata
8. [ ] Aggiungere test con `run_tests()` e verificare output strutturato
