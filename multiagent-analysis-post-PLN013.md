# Analisi Post-PLN-013 — Perché l'agente è ancora "stupido"

**Data analisi**: 2026-03-19  
**Scope**: Root-cause analysis delle 5 criticità utente dopo l'implementazione completa di PLN-013  
**Baseline**: PLN-013 (§1–§7) implementato, architettura Plan→Confirm→Execute→Respond funzionante

---

## Sommario esecutivo

PLN-013 ha introdotto infrastruttura corretta (ContextBuilder, ExecutionNarrative, ResponseClassifier, template strutturati, PlanningContext), ma **l'agente resta "stupido" non per difetti architetturali, bensì per 5 difetti di integrazione** che impediscono ai dati di raggiungere l'LLM nei momenti giusti. L'infrastruttura c'è — ma i tubi non sono collegati.

---

## Le 5 criticità dell'utente → 5 root causes

| # | Criticità utente | Root cause |
|---|---|---|
| P1 | "L'agente non sa dirmi cosa sa fare" | Il FinalResponder non ha conoscenza delle capability della piattaforma |
| P2 | "Non sa dirmi quali layer ha il progetto" | Il FinalResponder non riceve il layer_registry nel suo contesto |
| P3 | "Non capisce quando mi riferisco a layer esistenti" | I layer esistenti non sono visibili al RequestParser né al SupervisorAgent al momento della pianificazione |
| P4 | "Il piano proposto ha pochissime informazioni" | Il template di conferma piano è troppo sintetico |
| P5 | "Non capisce le mie correzioni al piano" | Il prompt di replanning non presenta il piano in formato leggibile |

---

## RC-1 — FinalResponder cieco alle capability (P1, P2)

### Il problema

Quando l'utente chiede "cosa sai fare?" o "che layer ho?":
1. `RequestParser` classifica come `request_type: "info"` → nessuna ambiguità
2. `SupervisorAgent` genera piano vuoto (`steps: []`) — corretto
3. `FinalResponder` riceve il prompt `_info_response()`:

```
"You are a helpful geospatial AI assistant.
The user asked a question or made a general request that did not require any tool execution.
Answer the question directly and informatively based on the conversation context."
```

4. Il "conversation context" è costruito da `_build_from_narrative()` o `_build_from_state()`, che contengono:
   - `📋 Richiesta: <intent>` — es. "platform capabilities"
   - `📌 Piano: (Nessun'azione necessaria)`
   - `📊 Status: pending`

**Non c'è NESSUNA informazione su:**
- Cosa la piattaforma può fare (tool disponibili, capability, prodotti)
- Quali layer esistono nel progetto corrente
- Quali agenti specializzati esistono e cosa offrono

L'LLM non ha dati su cui basarsi → risponde come ChatGPT generico.

### Il flusso del problema

```
Utente: "Cosa sai fare?"
       ↓
RequestParser → {intent: "platform capabilities", type: "info", entities: [], ...}
       ↓
SupervisorAgent → plan: []  (nessuna azione)
       ↓
FinalResponder → prompt: "Answer the question based on context"
                 context: "Richiesta: platform capabilities\nPiano: nessuno"
       ↓
LLM: *non ha idea delle capability* → risposta generica
```

### Dove intervenire

| File | Modifica |
|---|---|
| `final_responder_prompts.py` → `_info_response()` | Il system prompt per query info deve contenere un **sommario statico delle capability della piattaforma** |
| `final_responder_prompts.py` → `_build_from_narrative()` | Deve includere una sezione `Layer disponibili nel progetto` dal `layer_registry` |
| `final_responder_prompts.py` → `_build_from_state()` | Fallback: includere `layer_registry` anche qui |
| `final_responder.py` → `run()` | Passare `state` al prompt `Response.stable(state)` — questo già avviene, ma il prompt non usa `state["layer_registry"]` |

### Intervento proposto

**A) System prompt con capability statiche** nel `_info_response()`:

```python
@staticmethod
def _info_response() -> str:
    return (
        "You are a geospatial AI assistant for the SaferPlaces platform.\n"
        "\n"
        "## Platform capabilities\n"
        "The platform can:\n"
        "1. **Flood simulation** (SaferRain): simulate flooding from constant rainfall (mm) or retrieved rainfall rasters. Requires a DEM.\n"
        "2. **Digital Twin creation** (DigitalTwinTool): generate DEM + buildings + land-use for any area from bounding box.\n"
        "3. **DPC meteorological data** (Italian Civil Protection): retrieve radar rainfall, precipitation, temperature, lightning data for Italy. Past/recent data only.\n"
        "   Products: SRI, VMI, SRT1/3/6/12/24, TEMP, LTG, IR108, HRD.\n"
        "4. **Meteoblue weather forecasts**: retrieve global weather forecasts for precipitation, temperature, wind, humidity. Future data up to 14 days.\n"
        "\n"
        "## Available layers\n"
        "The user's project has layers visible in the context below. Describe them when asked.\n"
        "\n"
        "Instructions:\n"
        "- Answer questions about the platform precisely using the capabilities listed above.\n"
        "- When asked about layers, list the layers from the context with their properties.\n"
        "- If the user could benefit from an action, suggest it.\n"
        "- " + FinalResponderPrompts.Response._BASE_RULES
    )
```

**B) Layer registry nel contesto del FinalResponder**:

In `_build_from_narrative()` e `_build_from_state()`, aggiungere:

```python
# Layer del progetto
layer_registry = state.get("layer_registry", [])
if layer_registry:
    lines.append(f"\n📂 Layer nel progetto: {len(layer_registry)}")
    for l in layer_registry:
        title = l.get("title", "untitled")
        ltype = l.get("type", "unknown")
        desc = l.get("description", "")
        src = l.get("src", "")
        meta = l.get("metadata", {})
        line = f"   • {title} ({ltype})"
        if desc:
            line += f" — {desc}"
        if meta.get("bbox"):
            line += f" [bbox: {meta['bbox']}]"
        lines.append(line)
```

---

## RC-2 — Layer invisibili nella pipeline di planning (P3)

### Il problema — il bug più critico

Il flusso del grafo è:

```
REQUEST_PARSER → SUPERVISOR_AGENT → SUPERVISOR_PLANNER_CONFIRM → SUPERVISOR_ROUTER → [subgraph]
```

I layer vengono caricati in `SupervisorRouter._update_additional_context()` che chiama il `LayersAgent`. Ma questo avviene DOPO sia il `RequestParser` che il `SupervisorAgent`.

**Nella prima invocazione di ogni ciclo**, i layer non sono disponibili per la pianificazione:

```
REQUEST_PARSER:
  layers = state["additional_context"]["relevant_layers"]["layers"]
  → {} (vuoto — is_dirty=True ma nessuno ha fatto refresh)
  → layer_summary = "No layers available."

SUPERVISOR_AGENT:
  ContextBuilder.build(state)
    → _build_layers_summary(state)
      → relevant_layers è {} → "Nessun layer disponibile nel registro."
  → Planning senza sapere quali layer esistono

SUPERVISOR_ROUTER:
  _update_additional_context(state)  ← FINALMENTE carica i layer
  → Ma il piano è già stato generato!
```

Il `RequestParser` **ha** accesso a `state["layer_registry"]` (la lista raw dei layer dal S3), ma il codice in `_analyze_request()` legge da:

```python
layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
```

...che è il risultato processato dal `LayersAgent`, non il registro raw. Al primo ciclo, questo è **sempre vuoto**.

Stesso problema nel `ContextBuilder._build_layers_summary()` — legge da `additional_context.relevant_layers`, non da `layer_registry`.

### Impatto

- L'utente dice "simula alluvione usando il DEM che ho" → parser non sa che il DEM esiste → non lo menziona nel `ParsedRequest`
- Il Supervisor non sa che c'è un DEM → aggiunge uno step Digital Twin inutile
- L'utente dice "nell'area del layer X" → parser non sa cos'è il layer X → non risolve il bbox

### Dove intervenire

| File | Modifica |
|---|---|
| `request_parser.py` → `_analyze_request()` | Leggere da `state["layer_registry"]` direttamente, NON da `additional_context.relevant_layers.layers` |
| `context_builder.py` → `_build_layers_summary()` | Usare `state["layer_registry"]` come fallback primario quando `relevant_layers` è vuoto |
| Opzionalmente: `states.py` → `initialize_new_cycle()` | Caricare immediatamente `relevant_layers` dal `layer_registry` senza aspettare il router |

### Intervento proposto

**A) Fix nel RequestParser** — usare `layer_registry` direttamente:

```python
def _analyze_request(self, state: MABaseGraphState, prompt_input: str) -> ParsedRequest:
    # Priorità: relevant_layers (processato da LayersAgent) > layer_registry (raw)
    layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
    if not layers:
        # Fallback al layer_registry raw (sempre disponibile dopo restore_state)
        layers = state.get("layer_registry", [])
    layer_summary = self._summarize_layers(layers) if layers else "No layers available in the project."
    ...
```

**B) Fix nel ContextBuilder** — stessa strategia di fallback:

```python
@staticmethod
def _build_layers_summary(state: Dict[str, Any]) -> str:
    layer_registry = state.get("layer_registry", [])
    additional_context = state.get("additional_context", {})
    relevant_layers = additional_context.get("relevant_layers", {})
    
    # Usa relevant_layers se disponibili, altrimenti registra raw
    layers_to_summarize = relevant_layers.get("layers", []) if isinstance(relevant_layers, dict) else []
    if not layers_to_summarize:
        layers_to_summarize = layer_registry
    
    if not layers_to_summarize:
        return "Nessun layer disponibile nel registro."
    ...
```

**C) Layer summary più ricco** — aggiungere bbox e src nella summary:

```python
@staticmethod
def _summarize_layers(layers: list) -> str:
    summaries = []
    for l in layers:
        title = l.get("title", "untitled")
        ltype = l.get("type", "unknown")
        desc = l.get("description", "")
        src = l.get("src", "")
        meta = l.get("metadata", {})
        
        line = f"• {title} ({ltype})"
        if desc:
            line += f" — {desc}"
        if meta:
            bbox = meta.get("bbox")
            if bbox:
                line += f" [bbox: {bbox}]"
            band = meta.get("band")
            if band:
                line += f" [band={band}]"
            resolution = meta.get("pixelsize") or meta.get("resolution")
            if resolution:
                line += f" [res={resolution}m]"
        if src:
            line += f"\n  src: {src}"
        summaries.append(line)
    return "\n".join(summaries)
```

---

## RC-3 — Piano troppo sintetico (P4)

### Il problema

Il template di conferma piano (`format_plan_confirmation`) mostra:

```
📋 Piano di esecuzione (2 step):

  1. [Simulazione] Create Digital Twin for Rome
  2. [Simulazione] Run SaferRain with 50mm

Rispondi:
  ✓ "ok" per procedere
  ✏️ descrivi le modifiche desiderate
  ❌ "annulla" per cancellare
```

Mancano informazioni critiche:
- **PERCHÉ** viene creato un Digital Twin (non c'è DEM per l'area)
- **QUALI parametri** verranno usati (bbox, pixelsize, rainfall_mm)
- **COSA produrrà** ogni step (DEM raster, water depth raster)
- **QUALI layer** verranno consumati come input

L'utente non ha abbastanza informazioni per decidere se il piano è corretto.

### Dove intervenire

| File | Modifica |
|---|---|
| `templates.py` → `format_plan_confirmation()` | Arricchire il template con parametri, motivazioni, output attesi |
| `supervisor_agent_prompts.py` → `MainContext.stable()` | Istruire il Supervisor a produrre goal più dettagliati con parametri e motivazioni |

### Intervento proposto

**A) Goal più dettagliati dal Supervisor**

Il prompt `MainContext.stable()` deve istruire l'LLM a produrre goal con parametri:

Aggiungere nella sezione "Output format":
```
- steps[].goal: DETAILED description that includes:
  - WHAT the tool will do
  - WHY it's needed (e.g., "no DEM available for this area")
  - KEY PARAMETERS that will be used (bbox, rainfall_mm, product, etc.)
  - EXPECTED OUTPUT (e.g., "produces a DEM raster at 30m resolution")
```

**B) Template di conferma arricchito**

```python
def format_plan_confirmation(plan: List[Dict[str, Any]], parsed_request: dict = None) -> str:
    n = len(plan)
    lines = [f"📋 Piano di esecuzione ({n} step):", ""]

    for i, step in enumerate(plan, 1):
        label = _agent_label(step.get("agent", "unknown"))
        goal = step.get("goal", "")
        lines.append(f"  {i}. [{label}] {goal}")

    # Se c'è un parsed_request con parametri, mostrarli
    if parsed_request:
        params = parsed_request.get("parameters", {})
        if params:
            lines.append("")
            lines.append("📎 Parametri rilevati:")
            for k, v in params.items():
                if v is not None:
                    lines.append(f"  • {k}: {v}")

    lines.append("")
    lines.append("Rispondi:")
    lines.append('  ✓ "ok" per procedere')
    lines.append("  ✏️ descrivi le modifiche desiderate")
    lines.append('  ❌ "annulla" per cancellare')

    return "\n".join(lines)
```

**C) Passare il `parsed_request` al template** dal `SupervisorPlannerConfirm`:

```python
def _generate_confirmation_message(self, state, plan):
    parsed_request = state.get("parsed_request", {})
    return format_plan_confirmation(plan, parsed_request=parsed_request)
```

---

## RC-4 — Replanning non strutturato (P5)

### Il problema

Quando l'utente chiede modifiche al piano, il prompt `IncrementalReplanning.stable()` passa:

```python
current_plan = state.get("plan", "No plan available")
```

`state["plan"]` è una **lista di dict Python**, renderizzata come:

```
## Current Plan
[{'agent': 'models_subgraph', 'goal': 'Create Digital Twin for Rome'}, {'agent': 'models_subgraph', 'goal': 'Run SaferRain with 50mm'}]
```

Questo è illeggibile per l'LLM. Il feedback dell'utente è passato raw senza interpretazione.

Inoltre il prompt di replanning non ha accesso al contesto arricchito (layer disponibili, capability degli agenti) — solo al piano raw e al feedback.

### Dove intervenire

| File | Modifica |
|---|---|
| `supervisor_agent_prompts.py` → `IncrementalReplanning.stable()` | Formattare il piano corrente in formato leggibile, aggiungere contesto layer e capability |
| `supervisor_agent_prompts.py` → `TotalReplanning.stable()` | Stessa formattazione |

### Intervento proposto

**A) Formattare il piano nel prompt di replanning**:

```python
class IncrementalReplanning:
    @staticmethod
    def stable(state: MABaseGraphState, **kwargs) -> Prompt:
        parsed_request = state.get("parsed_request", {})
        current_plan = state.get("plan", [])
        replan_request = state.get("replan_request")
        user_feedback = replan_request.content if replan_request else "No feedback"
        conversation_context = _get_conversation_context(state)
        layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])

        request_text = OrchestratorPrompts.Plan._format_parsed_request(parsed_request)
        
        # Formattare il piano in modo leggibile
        plan_text = OrchestratorPrompts.Plan._format_plan_readable(current_plan)
        layers_text = OrchestratorPrompts.Plan._format_layers_summary(layers)

        message = (
            f"User requested modifications to the existing plan.\n"
            f"\n"
            f"## Original Request\n{request_text}\n"
            f"\n"
            f"## Current Plan\n{plan_text}\n"
            f"\n"
            f"## Available Layers\n{layers_text}\n"
            f"\n"
            f"## User Feedback\n{user_feedback}\n"
            f"\n"
            f"Adjust the plan based on user feedback:\n"
            f"- Keep steps not mentioned by the user\n"
            f"- Modify only what's explicitly requested\n"
            f"- If the user refers to a step by number, map it to the correct step above\n"
            f"- If the user mentions using an existing layer, check Available Layers"
        )
        ...
```

Con un nuovo helper per formattare il piano:

```python
@staticmethod
def _format_plan_readable(plan: list) -> str:
    if not plan:
        return "No plan generated."
    lines = []
    for i, step in enumerate(plan, 1):
        agent = step.get("agent", "unknown")
        goal = step.get("goal", "no goal")
        label = {
            "models_subgraph": "Simulazione/Modelli",
            "retriever_subgraph": "Recupero Dati",
        }.get(agent, agent)
        lines.append(f"  Step {i}: [{label}] {goal}")
    return "\n".join(lines)
```

---

## RC-5 — RequestParser non abbastanza ricco per il contesto (trasversale a P3)

### Il problema

Il `RequestParser._summarize_layers()` genera un sommario troppo minimale:

```
• DEM Roma (raster) — Digital Elevation Model [band=0]
```

Non include:
- **`bbox`**: l'estensione spaziale del layer — critica per capire "nell'area del layer X"
- **`src`**: l'URI del layer — necessario per l'agente specializzato quando seleziona tool input
- **Risoluzione/pixelsize**: utile per capire la qualità dei dati
- **CRS/proiezione**: utile per compatibilità

Quando l'utente dice "usa il DEM che ho" o "nell'area del mio layer", il parser non ha abbastanza info per risolvere il riferimento.

### Dove intervenire

| File | Modifica |
|---|---|
| `request_parser.py` → `_summarize_layers()` | Includere bbox, src, metadata dettagliata |
| `request_parser_prompts.py` → `MainContext.stable()` | Aggiungere istruzioni per risolvere riferimenti a layer esistenti |

### Intervento proposto

Il sommario layer deve essere sufficientemente ricco da permettere all'LLM di:
- Identificare un layer menzionato dall'utente per nome o descrizione
- Capire l'area coperta dal layer (bbox)
- Sapere che quel layer può essere usato come input per i tool

```python
@staticmethod
def _summarize_layers(layers: list) -> str:
    if not layers:
        return "No layers available."
    summaries = []
    for l in layers:
        title = l.get("title", "untitled")
        ltype = l.get("type", "unknown")
        src = l.get("src", "")
        desc = l.get("description", "")
        meta = l.get("metadata", {})
        
        line = f"• {title} ({ltype})"
        if desc:
            line += f" — {desc}"
        
        details = []
        if meta:
            bbox = meta.get("bbox")
            if bbox:
                details.append(f"bbox={bbox}")
            band = meta.get("band")
            if band:
                details.append(f"band={band}")
            res = meta.get("pixelsize") or meta.get("resolution")
            if res:
                details.append(f"res={res}m")
        if src:
            details.append(f"src={src}")
        
        if details:
            line += f"\n  [{', '.join(details)}]"
        summaries.append(line)
    return "\n".join(summaries)
```

E nel prompt del parser, aggiungere una regola di risoluzione:

```
"6. When the user references an existing layer by name (or describes a layer), resolve it to the "
"matching layer from the Available Layers list. Include the layer's title and src in the resolved entity.\n"
"7. When the user says 'in the area of layer X' or 'use the bbox of layer X', resolve the bbox from "
"the layer's metadata and include it in the parameters.\n"
```

---

## Mappa degli interventi

### Priorità 1 — Fix critici (impatto immediato sulla qualità)

| ID | Intervento | File | Impatto |
|---|---|---|---|
| **FIX-01** | Layer registry come fallback nel RequestParser | `request_parser.py` | P3: l'agente vede i layer dal primo messaggio |
| **FIX-02** | Layer registry come fallback nel ContextBuilder | `context_builder.py` | P3: il Supervisor pianifica con i layer |
| **FIX-03** | Capability statiche nel FinalResponder | `final_responder_prompts.py` | P1: l'agente sa descrivere cosa fa |
| **FIX-04** | Layer registry nel contesto del FinalResponder | `final_responder_prompts.py`, `final_responder.py` | P2: l'agente sa elencare i layer |

### Priorità 2 — Miglioramenti dell'esperienza

| ID | Intervento | File | Impatto |
|---|---|---|---|
| **FIX-05** | Summary layer più ricco (bbox, src, metadata) | `request_parser.py`, `context_builder.py` | P3: l'agente capisce riferimenti spaziali |
| **FIX-06** | Goal del piano più dettagliati (parametri, motivazioni) | `supervisor_agent_prompts.py` | P4: piano informativo |
| **FIX-07** | Template conferma arricchito con parametri | `templates.py`, `supervisor.py` | P4: l'utente capisce il piano |
| **FIX-08** | Replanning con piano formattato e contesto layer | `supervisor_agent_prompts.py` | P5: l'agente capisce le correzioni |

### Priorità 3 — Risoluzione layer nel parser

| ID | Intervento | File | Impatto |
|---|---|---|---|
| **FIX-09** | Regole di risoluzione layer nel prompt parser | `request_parser_prompts.py` | P3: risolve "usa il layer X" |
| **FIX-10** | Helper di formato piano leggibile per replanning | `supervisor_agent_prompts.py` | P5: il piano non è più un dict raw |

---

## Dipendenze tra fix

```
FIX-01 (layer fallback parser)
  └── FIX-05 (summary layer ricco) ← può essere fatto insieme
  └── FIX-09 (regole risoluzione layer prompt)

FIX-02 (layer fallback context builder)
  └── FIX-05 (summary layer ricco) ← condivide la logica

FIX-03 (capability FinalResponder)   ← indipendente
FIX-04 (layer nel contesto FR)       ← indipendente

FIX-06 (goal dettagliati)            ← indipendente
FIX-07 (template conferma)           ← dipende da FIX-06 per goal ricchi
  └── FIX-08 (replanning formattato) ← riusa _format_plan_readable()

FIX-10 (helper formato piano)        ← prerequisito di FIX-08
```

### Ordine di implementazione raccomandato

```
Batch 1 (layer visibility — risolve P1, P2, P3):
  FIX-01 + FIX-02 + FIX-05  → layer visibili ovunque dal primo messaggio
  FIX-03 + FIX-04            → FinalResponder sa descrivere la piattaforma e i layer
  FIX-09                     → prompt parser risolve riferimenti a layer

Batch 2 (plan quality — risolve P4, P5):  
  FIX-06 + FIX-10            → goal dettagliati + formato piano leggibile
  FIX-07 + FIX-08            → conferma arricchita + replanning con contesto
```

---

## Nota architettonica: il problema del refresh layer

Il design attuale prevede che il `LayersAgent` processi i layer raw in layer "rilevanti" nel `SupervisorRouter`. Questo design ha senso per cicli successivi (il router rivaluta i layer dopo ogni esecuzione), ma crea un **cold start problem** al primo ciclo.

Soluzione:

**Fallback diretto al `layer_registry`** (proposto sopra): più semplice, meno invasivo. Il `layer_registry` è già disponibile fin dall'inizio (caricato da `graph_interface.restore_state()`). Usarlo come fallback quando `relevant_layers` è vuoto è sufficiente per risolvere il cold start senza modificare il grafo.

