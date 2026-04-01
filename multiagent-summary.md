# SaferPlaces Multiagent — Architecture & Component Summary

> Analisi del pacchetto `src/saferplaces_multiagent/`.  
> Cartella `nodes/` esclusa dall'analisi.  
> Data: Marzo 2026

---

## Indice

1. [Panoramica generale](#1-panoramica-generale)
2. [Topologia del grafo](#2-topologia-del-grafo)
3. [Gestione dello stato](#3-gestione-dello-stato)
4. [Classe base MultiAgentNode](#4-classe-base-multiagentnode)
5. [Parsing e ingresso dell'utente](#5-parsing-e-ingresso-dellutente)
6. [Supervisore e pianificazione](#6-supervisore-e-pianificazione)
7. [Subgraph specializzati](#7-subgraph-specializzati)
8. [Tool catalog](#8-tool-catalog)
9. [Sistema dei prompt](#9-sistema-dei-prompt)
10. [HITL — Human-in-the-Loop](#10-hitl--human-in-the-loop)
11. [Agent Interface](#11-agent-interface)
12. [Moduli common](#12-moduli-common)
13. [Componenti legacy](#13-componenti-legacy)

---

## 1. Panoramica generale

SaferPlaces Multiagent è un sistema **multi-agent AI gerarchico** costruito su **LangGraph**. Orchestrata da un Supervisore centrale, la pipeline interpreta le richieste in linguaggio naturale, genera un piano di esecuzione multi-step, e coordina agenti specializzati che eseguono tool geospaziali, modelli di alluvione e recupero dati meteorologici.

### 1.1 Stack tecnologico

| Componente | Tecnologia |
|---|---|
| Orchestrazione grafo | LangGraph (`StateGraph`, `InMemorySaver`) |
| LLM backbone | OpenAI (via `ChatOpenAI`) — `common/utils._base_llm` |
| Dati geospaziali | GDAL, COG (Cloud-Optimized GeoTIFF), GeoJSON WGS84 |
| Storage | AWS S3 (`common/s3_utils.py`) |
| API server | Flask + Gunicorn (prod) |
| Geocodifica | Nominatim / OpenStreetMap |
| Validazione schema | Pydantic v2 |

### 1.2 Ciclo di vita di una richiesta

```
HumanMessage
    → REQUEST_PARSER      (analisi intento + entità strutturate)
    → SUPERVISOR_SUBGRAPH (piano, conferma HITL, routing)
    → [RETRIEVER | MODELS | MAP_AGENT]*  (uno o più cicli)
    → FINAL_RESPONDER     (risposta contestuale)
```

Ogni subgraph specializzato **ritorna al Supervisor** al termine, che decide se avviare lo step successivo del piano o procedere al `FINAL_RESPONDER`.

---

## 2. Topologia del grafo

### 2.1 Entry point: `multiagent_graph.py`

Il modulo assembla e compila il grafo LangGraph principale tramite la funzione `build_multiagent_graph()`. Il grafo viene istanziato alla prima importazione del pacchetto come variabile globale `graph = build_multiagent_graph()`.

Il modulo espone anche tre funzioni di factory per i sottografi interni:
- `build_supervisor_subgraph()`
- `build_specialized_retriever_subgraph()`
- `build_specialized_models_subgraph()`

### 2.2 Grafo principale

```
START
  │
  ▼
REQUEST_PARSER
  │
  ▼
SUPERVISOR_SUBGRAPH ◄──────────────────────────────────┐
  │                                                     │
  │ (supervisor_next_node)                              │
  ├──► RETRIEVER_SUBGRAPH ──────────────────────────────┤
  ├──► MODELS_SUBGRAPH ────────────────────────────────┤
  ├──► MAP_AGENT ──────────────────────────────────────┘
  ├──► FINAL_RESPONDER ──► END
  └──► END
```

Il routing in uscita dal Supervisor è **condizionale** tramite il campo `supervisor_next_node` dello stato. Tutti i subgraph specializzati terminano con un arco fisso verso `SUPERVISOR_SUBGRAPH`, che esegue il prossimo step del piano o decide di chiudere con `FINAL_RESPONDER`.

Il checkpointer è `InMemorySaver`, che garantisce persistenza della conversazione per thread.

### 2.3 Supervisor subgraph

```
START → SupervisorAgent → SupervisorPlannerConfirm
                                    │ (plan_confirmation in {modify, rejected})?
                                    ├── True  → SupervisorAgent  (loop di replanning)
                                    └── False → SupervisorRouter → END
```

### 2.4 Subgraph specializzati (Retriever e Models)

Entrambi seguono lo stesso pattern a tre nodi:

```
START → Agent → InvocationConfirm
                      │ (invocation_confirmation == rejected)?
                      ├── True  → Agent  (re-invocazione)
                      └── False → Executor → END
```

`InvocationConfirm` è attualmente configurato con `enabled=False` (auto-accept senza interrupt).

### 2.5 Costanti NodeNames

Tutti i nomi dei nodi sono centralizzati nella classe `NodeNames` in `ma/names.py`:

| Costante | Valore stringa |
|---|---|
| `REQUEST_PARSER` | `"request_parser"` |
| `SUPERVISOR_SUBGRAPH` | `"supervisor_subgraph"` |
| `SUPERVISOR_AGENT` | `"supervisor_agent"` |
| `SUPERVISOR_PLANNER_CONFIRM` | `"supervisor_planner_confirm"` |
| `SUPERVISOR_ROUTER` | `"supervisor_router"` |
| `RETRIEVER_SUBGRAPH` | `"retriever_subgraph"` |
| `MODELS_SUBGRAPH` | `"models_subgraph"` |
| `MAP_AGENT` | `"map_agent"` |
| `LAYERS_AGENT` | `"layers_agent"` |
| `FINAL_RESPONDER` | `"final_responder"` |

---

## 3. Gestione dello stato

### 3.1 `MABaseGraphState` (TypedDict)

Lo stato è il **singolo oggetto condiviso** tra tutti i nodi del grafo. È definito in `common/states.py`.

#### Campi di sessione

| Campo | Tipo | Descrizione |
|---|---|---|
| `user_id` | `str` | Identificatore utente |
| `project_id` | `str` | Identificatore progetto |
| `messages` | `Annotated[list[AnyMessage], add_messages]` | Storia conversazione (reducer LangGraph `add_messages`) |
| `interaction_count` | `int` | Numero di interrupt HITL nel ciclo corrente |
| `interaction_budget` | `int` | Budget massimo HITL (default: 8) |

#### Campi geospaziali

| Campo | Tipo | Reducer |
|---|---|---|
| `layer_registry` | `Sequence[dict]` | `merge_layer_registry` (overwrite per `src`) |
| `user_drawn_shapes` | `Sequence[dict]` | `merge_user_drawn_shapes` (overwrite per `collection_id`) |
| `map_view` | `Optional[MapView]` | Overwrite diretto |
| `map_commands` | `List[dict]` | `merge_map_commands` (concatenazione, accumula tutto) |

#### Campi di pianificazione

| Campo | Tipo | Valori possibili |
|---|---|---|
| `parsed_request` | `Dict[str, Any]` | Output di `ParsedRequest` |
| `additional_context` | `AdditionalContext` | Contiene `relevant_layers` |
| `plan` | `Optional[List[dict]]` | Lista `{agent, goal}` |
| `plan_confirmation` | `PlanConfirmationStatus` | `pending / accepted / modify / rejected / aborted` |
| `replan_request` | vario | Feedback utente per replanning |
| `replan_type` | str | `"incremental"` / `"total"` |
| `supervisor_next_node` | `str` | Destinazione routing |
| `current_step` | `int` | Step corrente del piano |
| `tool_results` | `dict` | Accumulo risultati tool |
| `execution_narrative` | `Optional[ExecutionNarrative]` | Struttura narrativa esecuzione |

#### Campi per agenti specializzati

Ogni agente specializzato ha tre chiavi di stato dedicate con prefisso `{prefix}`:

| Chiave | Tipo | Scopo |
|---|---|---|
| `{prefix}_invocation` | `AIMessage` | Output LLM con tool call proposti |
| `{prefix}_invocation_confirmation` | `str` | `"pending"` / `"accepted"` / `"rejected"` |
| `{prefix}_reinvocation_request` | `AnyMessage` | Feedback per re-invocazione |

Prefissi attivi: `retriever`, `models`, `layers`, `map`.

### 3.2 Reducer functions

#### `merge_layer_registry`
Aggiorna i layer esistenti per chiave `src`. Layer con stesso `src` vengono sostituiti; i nuovi vengono aggiunti in coda. Garantisce unicità per sorgente.

#### `merge_user_drawn_shapes`
Merging per `collection_id`. Sovrascrive le collection con stesso ID, aggiunge le nuove.

#### `merge_map_commands`
Semplice concatenazione. Tutti i comandi emessi durante il ciclo si accumulano e vengono letti dal frontend al termine.

### 3.3 `StateManager`

`StateManager` in `common/states.py` è l'**unico punto autorizzato** per le transizioni di stato di ciclo. Non manipolare direttamente i campi che gestisce.

| Metodo | Quando chiamarlo | Nodo di riferimento |
|---|---|---|
| `initialize_new_cycle(state)` | Inizio di ogni nuova richiesta utente | `REQUEST_PARSER` |
| `initialize_specialized_agent_cycle(state, agent_type)` | Prima di ogni subgraph specializzato | `SUPERVISOR_ROUTER` |
| `mark_agent_step_complete(state, agent_type)` | Al termine dell'esecuzione di un tool | Executor nodes |
| `cleanup_on_final_response(state)` | Prima della risposta finale | `FINAL_RESPONDER` |
| `is_plan_complete(state)` | Check fine piano | `SUPERVISOR_ROUTER` |

`cleanup_on_final_response` preserva solo `layer_registry`, `user_drawn_shapes`, `user_id`, `project_id` — pulisce tutto il contesto di esecuzione.

### 3.4 Modelli di base (`common/base_models.py`)

| Modello | Tipo | Descrizione |
|---|---|---|
| `ParsedRequest` | Pydantic | `intent`, `request_type`, `entities`, `parameters_json`, `implicit_requirements`, `raw_text` |
| `Entity` | Pydantic | Entità estratta: `name`, `entity_type`, `resolved: ResolvedMetadata` |
| `ResolvedMetadata` | Pydantic | Metadati risolti: location, date, layer, model, product, parameter |
| `Layer` | dataclass | Record layer: `title`, `type`, `src`, `description`, `metadata`, `style` |
| `MapView` | Pydantic | Viewport cartografico: `center_lon`, `center_lat`, `zoom`, `bbox` |
| `MapCommand` | Pydantic | Comando frontend: `type` (`move_view`/`set_layer_style`), `payload`, `timestamp` |
| `DrawnShape` | Pydantic | Geometria utente: `shape_id`, `shape_type`, `geometry`, `label` |
| `BBox` | Pydantic | Bounding box WGS84: `west`, `south`, `east`, `north` + `to_list()`, `draw_feature_collection()` |
| `PlanConfirmationStatus` | Literal | `"pending"/"accepted"/"modify"/"rejected"/"aborted"` |
| `ConfirmationState` | Literal | `"accepted"/"rejected"/"pending"` |

---

## 4. Classe base MultiAgentNode

`MultiAgentNode` in `multiagent_node.py` è la **classe base obbligatoria** per tutti i nodi del grafo.

### 4.1 Pattern di esecuzione

```
__call__(state)
    │
    ├─► _pre_run(state)    # snapshot messaggi per delta logging
    ├─► run(state)         # logica del nodo (astratto, da implementare)
    └─► _post_run(state)   # scrittura log JSON su disco
```

### 4.2 Logging differenziale

`_post_run` scrive ogni esecuzione come riga JSON nel file `__state_log__user_id=X__project_id=Y.json` (gitignored). Il log include:
- I **nuovi messaggi** (delta rispetto allo snapshot pre-run)
- Tutti i campi non-nulli dello stato, serializzati con `pandas`

Questo permette il debug post-hoc di ogni ciclo senza inquinare il repository.

### 4.3 Implementare un nuovo nodo

```python
from saferplaces_multiagent.multiagent_node import MultiAgentNode
from saferplaces_multiagent.common.states import MABaseGraphState

class MyNode(MultiAgentNode):
    def __init__(self):
        super().__init__(name="my_node")

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        # logica qui
        return state
```

---

## 5. Parsing e ingresso dell'utente

### 5.1 `RequestParser` (`ma/chat/request_parser.py`)

Primo nodo del grafo. Analizza il messaggio utente e lo converte in una struttura dati `ParsedRequest`.

**Processo:**
1. Chiama `StateManager.initialize_new_cycle(state)` — pulisce il ciclo precedente
2. Estrae l'ultimo `HumanMessage`
3. Costruisce un sommario dei layer disponibili (`_summarize_layers`)
4. Invoca `_base_llm.with_structured_output(ParsedRequest)` con il prompt di sistema `RequestParserPrompts.MainContext.stable(layer_summary=...)`
5. Popola `state["parsed_request"]` con i campi: `intent`, `request_type`, `entities`, `parameters_json`, `implicit_requirements`, `raw_text`

**Risoluzione entità**: il prompt guida l'LLM a risolvere i riferimenti nelle entità — nomi di luogo → bounding box WGS84, riferimenti a layer → `src` URI, date relative → datetime assoluti.

### 5.2 `FinalResponder` (`ma/chat/final_responder.py`)

Ultimo nodo prima di `END`. Genera la risposta testuale all'utente.

**Processo:**
1. Seleziona la variante di prompt (`_select_variant`) in base alla `ExecutionNarrative`:
   - `_completed_plan`: piano completato con successo → report risultati + guida mappa
   - `_info_response`: risposta informativa (no tool eseguiti)
   - `_aborted_plan`: piano abortito dall'utente
   - `_error_response`: errori critici → report errori + recovery suggestions
2. Filtra la conversation history mantenendo solo `HumanMessage` e `AIMessage` senza `tool_calls`
3. Invoca l'LLM: `[SystemMessage, HumanMessage(context), *history]`
4. Segna `execution_narrative.completed_at`
5. Chiama `StateManager.cleanup_on_final_response(state)`

---

## 6. Supervisore e pianificazione

### 6.1 `SupervisorAgent` (`ma/orchestrator/supervisor.py`)

Cuore della pianificazione. Genera un `ExecutionPlan` strutturato che lista gli step da eseguire.

**Guard `_should_skip_planning()`**: il Supervisor **salta la rigenerazione del piano** se è già in esecuzione (`plan_confirmation == "accepted"` e `current_step < len(plan)`), oppure se il piano è stato abortito.

**Processo di pianificazione `_generate_plan()`:**
1. Guard anti-loop (max 5 iterazioni di replanning)
2. `ContextBuilder.build(state)` → `PlanningContext` arricchito
3. Selezione del prompt in base a `plan_confirmation`:
   - Prima generazione → `CreatePlan.stable(state)`
   - `"modify"` → `IncrementalReplanning.stable(state)`
   - `"rejected"` → `TotalReplanning.stable(state)`
4. `_base_llm.with_structured_output(ExecutionPlan)` → lista `PlanStep(agent, goal)`
5. Validazione: ogni `agent` deve essere nell'`AGENT_REGISTRY`
6. Aggiorna `state["plan"]`, `current_step=0`, `plan_confirmation="pending"`
7. Inizializza `ExecutionNarrative`

**`ExecutionPlan` (Pydantic):** `steps: List[PlanStep]` dove `PlanStep` ha `agent` (nome agente) e `goal` (obiettivo dello step).

**`AGENT_REGISTRY`**: dizionario definito in `supervisor_agent_prompts.py` che mappa nomi di agente → descrizione capabilities. Agenti attivi: `models`, `retriever`, `map`.

### 6.2 `SupervisorPlannerConfirm`

Nodo HITL che interrompe il grafo per chiedere conferma del piano all'utente.

**Modalità:**
- `enabled=False` → auto-accept immediato, nessun interrupt
- `enabled=True` → `interrupt()` con messaggio piano formattato (`templates.format_plan_confirmation`)

**Classificazione risposta** via `ResponseClassifier.classify_plan_response()`:

| Intent classificato | Azione |
|---|---|
| `"accept"` | `plan_confirmation = "accepted"` |
| `"modify"` | `plan_confirmation = "modify"` + `replan_type = "incremental"` |
| `"reject"` | `plan_confirmation = "rejected"` + `replan_type = "total"` |
| `"abort"` | `plan_confirmation = "aborted"` |
| `"clarify"` | Loop: spiega il piano via LLM + re-interrupt (budget-aware) |

Il loop di `clarify` usa `interaction_count` / `interaction_budget` per evitare loop infiniti.

### 6.3 `SupervisorRouter`

Legge `supervisor_next_node` dallo stato e determina la destinazione del routing. Chiama `StateManager.initialize_specialized_agent_cycle(state, agent_type)` prima di instradare verso un subgraph. Se il piano è completo, imposta `supervisor_next_node = FINAL_RESPONDER`.

Il `LayersAgent` è un caso speciale: viene invocato **direttamente** da `SupervisorRouter._update_additional_context()` per il context refresh (non fa parte del piano multi-step e non è nell'`AGENT_REGISTRY`).

### 6.4 `ContextBuilder` (`common/context_builder.py`)

Costruisce un `PlanningContext` arricchito usato da `SupervisorAgent` e altri nodi.

**Pipeline a 4 fasi:**
1. Estrae `intent` e `request_type` da `parsed_request`
2. Build **layers summary**: usa `relevant_layers` da `additional_context` se disponibile, fallback su `layer_registry` grezzo
3. Build **previous results summary**: step già completati (per replanning incrementale)
4. Filtra la **conversation history**: solo `HumanMessage` e `AIMessage` finali (esclude `ToolMessage`, `SystemMessage`)

`format_for_prompt(context)` → stringa formattata da iniettare nel prompt.

---

## 7. Subgraph specializzati

### 7.1 Pattern comune

Tutti i subgraph specializzati (`Retriever`, `Models`) seguono il pattern:

```
Agent          → seleziona e propone tool call (LLM bound con tools)
InvocationConfirm → inference + validation + HITL opzionale
Executor       → esegue i tool call + salva risultati in tool_results
```

### 7.2 DataRetriever subgraph (`ma/specialized/safercast_agent.py`)

**Tool disponibili:** `DPCRetrieverTool`, `MeteoblueRetrieverTool`.

**`DataRetrieverAgent`:**
- LLM bound con `[DPCRetrieverTool, MeteoblueRetrieverTool]`
- Prompt: `SaferCastPrompts.MainContext.stable()` + `SaferCastPrompts.ToolSelection.InitialRequest.stable(state)`
- Memorizza `AIMessage` con tool_calls in `state["retriever_invocation"]`

**`DataRetrieverInvocationConfirm`:**
1. Applica inference rules (`_apply_inference_to_args`) — riempie valori mancanti con defaults
2. Applica validation rules (`_validate_args`) — verifica argomenti invalidi
3. Se errori → `interrupt()` con messaggio errori formattato (`templates.format_validation_errors`)
4. Se `enabled=True` e no errori → `interrupt()` con conferma standard

**Tool registry**: singleton con i due tool registrati.

**Re-invocazione**: se `retriever_invocation_confirmation == "rejected"` nel grafo condizionale, il nodo `DataRetrieverAgent` viene rieseguito con il messaggio di reinvocazione.

### 7.3 Models subgraph (`ma/specialized/models_agent.py`)

**Tool disponibili:** `DigitalTwinTool`, `SaferRainTool`, `SaferBuildingsTool`, `SaferFireTool`.

Stessa struttura del Retriever subgraph. Il prompt `ModelsPrompts.MainContext.stable()` documenta regole dettagliate per ogni tool (parametri obbligatori, dipendenze tra tool, outputs attesi, prerequisiti).

**`MODELS_AGENT_DESCRIPTION`** (registrata nel supervisor): include una sezione `implicit_step_rules` che codifica le best practice operative — es.: "esegui sempre DigitalTwin prima di SaferRain per generare il DEM".

### 7.4 `MapAgent` (`ma/specialized/map_agent.py`)

Agente specializzato per operazioni sulla mappa. **Non usa il pattern Agent/Confirm/Executor** — esegue direttamente in un loop sincrono.

**Tool disponibili:** `MoveMapViewTool`, `LayerSymbologyTool`.

**Architettura di esecuzione:**
1. Costruisce context message con `map_view_desc` (viewport corrente) e `layer_summary` (registry)
2. Invoca l'LLM con `MapAgentPrompts.SystemPrompt.stable()` per ottenere tool_calls
3. Itera su tutti i `tool_calls` nella risposta LLM:
   - Inietta `tool.state = state` prima dell'esecuzione (i tool scrivono direttamente nello stato)
   - Esegue ogni tool, raccoglie risultati
4. Nessun interrupt: esecuzione sempre automatica

I tool del `MapAgent` sono **stateful**: accedono e modificano `state["map_view"]` e `state["map_commands"]` direttamente.

### 7.5 `LayersAgent` (`ma/specialized/layers_agent.py`)

Agente on-demand per il context refresh del `LayerRegistry`. **Non è nel flusso principale del piano** — viene invocato direttamente da `SupervisorRouter._update_additional_context()`.

**`LayersRegistry`** (singleton in-memory):
- Dati strutturati: `dict[title → Layer]`
- Metodi: `list_layers()`, `get_layer()`, `add_layer()`, `remove_layer()`, `update_layer()`, `search_by_type()`
- `_ensure_metadata_exists()`: calcola automaticamente `raster_specs`/`vector_specs` se assenti

**Tool LangChain registrati**: `ListLayersTool`, `GetLayerTool`, `AddLayerTool`, `RemoveLayerTool`, `UpdateLayerTool`, `SearchByTypeTool`, `ChooseLayerTool`, `BuildLayerFromPromptTool`.

**`BuildLayerFromPromptTool`**: usa LLM per inferire `title`, `type`, `description`, `metadata` da una descrizione naturale + URI sorgente — permette di aggiungere layer senza compilare manualmente i metadati.

---

## 8. Tool catalog

Tutti i tool risiedono in `ma/specialized/tools/`. Estendono `BaseTool` e definiscono regole di inference e validazione.

### 8.1 `DigitalTwinTool`

| Attributo | Valore |
|---|---|
| Funzione | Generazione Digital Twin geospaziale (DEM, idrografia, edifici, land cover, suolo) |
| API target | Terra-Twin API |
| Input required | `bbox` (BBox), `layers` (List[TerraTwinLayerName]) |
| Input opzionali | `pixelsize`, `out_format`, `region_name`, `clip_geometry`, `t_srs`, `building_extrude_height`, `dem_dataset`, `debug` |
| Layer categories | `elevation` (dem, dsm, ndsm), `hydrology` (rivers, floodplains, catchments, …), `constructions` (buildings, roads, railways), `landcover` (corine, esacci, …), `soil` (soil_type, …) |
| Layer default | `['dem']` per richieste generiche |
| Output | Layer registrati automaticamente nel `layer_registry` |

### 8.2 `SaferRainTool`

| Attributo | Valore |
|---|---|
| Funzione | Simulazione inondazione pluviale |
| API target | SaferRain (AWS Lambda o Batch) |
| Input required | `dem` (URI raster o layer reference), `rain` (URI rain raster o valore mm scalare) |
| Input opzionali | `water` (output URI), `band`, `to_band`, `t_srs`, `mode` (lambda/batch), `debug` |
| Dipendenza | Richiede DEM preventivamente generato da `DigitalTwinTool` |
| Output | Raster WD (water depth) |

### 8.3 `SaferBuildingsTool`

| Attributo | Valore |
|---|---|
| Funzione | Analisi impatto alluvione sugli edifici |
| Input required | `water` (raster WD da SaferRain) + (`buildings` XOR `provider`) |
| Provider built-in | `OVERTURE` (globale), `RER-REST/*`, `VENEZIA-WFS/*`, `VENEZIA-WFS-CRITICAL-SITES` |
| Parametri chiave | `wd_thresh` (default 0.5m), `flood_mode` (BUFFER/IN-AREA/ALL), `only_flood`, `stats` |
| Dipendenza | Richiede output di `SaferRainTool` |

### 8.4 `SaferFireTool`

| Attributo | Valore |
|---|---|
| Funzione | Simulazione propagazione incendio |
| Input required | `dem`, `ignitions` (punto/poligono ignizione) |
| Input chiave | `wind_speed` (m/s), `wind_direction` (gradi meteorologici) |
| LandUse providers | `ESA/LANDUSE/V100`, `RER/LANDUSE`, `CUSTOM/LANDUSE/FBVI`, `CUSTOM/LANDUSE/RER/AIB` |
| Parametri simulazione | `time_max` (default 7200s), `time_step_interval` (default 900s), `moisture` (default 0.15) |

### 8.5 `DPCRetrieverTool`

| Attributo | Valore |
|---|---|
| Funzione | Recupero dati radar/satellite DPC (Dipartimento Protezione Civile) |
| Input required | `product` (DPCProductCode), `bbox` (BBox), `time_start`, `time_end` |
| Copertura geografica | **Solo Italia** |
| Tipo di dati | **Storici/recenti** (delay ~10 min) |
| Prodotti | VMI, SRI, SRT1/3/6/12/24, IR108, TEMP, LTG, AMV, HRD, RADAR_STATUS, CAPPI1–8 |
| Inference rules | `time_start` ← last 1h, `time_end` ← now (con delay cap 10 min) |
| Validation rules | bbox inside Italy, `time_start < time_end`, entrambi entro 7 giorni |

### 8.6 `MeteoblueRetrieverTool`

| Attributo | Valore |
|---|---|
| Funzione | Recupero previsioni meteo Meteoblue |
| Input required | `variable` (MeteoblueVariable), `bbox`, `time_start`, `time_end` |
| Copertura geografica | **Globale** |
| Tipo di dati | **Previsioni future** (max 14 giorni) |
| Variabili | PRECIPITATION, TEMPERATURE, WINDSPEED, WINDDIRECTION, RELATIVEHUMIDITY, PRECIPITATION_PROBABILITY, + 8 altre |

### 8.7 `LayerSymbologyTool`

| Attributo | Valore |
|---|---|
| Funzione | Impostazione stile cartografico layer via linguaggio naturale |
| Input | `layer_id` (title nel registry), `user_request` (descrizione stile in linguaggio naturale) |
| Processo | Lookup layer → estrae metadata → LLM genera JSON MapLibre GL JS `{paint, filter?, layout?}` |
| Retry | Auto-retry se il JSON ritornato dall'LLM non è parseable |
| Output | Aggiorna `layer.style` nel registry + `MapCommand(type="set_layer_style")` |

### 8.8 `MoveMapViewTool`

| Attributo | Valore |
|---|---|
| Funzione | Spostamento viewport cartografico |
| Input (priorità 1) | `location_name` → geocodifica via **Nominatim/OSM** (timeout 3s) |
| Input (priorità 2) | `bbox` → calcola centro automaticamente |
| Input (priorità 3) | `center_lon`, `center_lat` diretti |
| Output | Aggiorna `state["map_view"]` + `MapCommand(type="move_view")` |

### 8.9 Inference e Validation rules (`_inferrers.py`, `_validators.py`)

I tool dichiarano regole di inference e validazione come factory functions che restituiscono callables. Vengono applicate sistematicamente da `InvocationConfirm` prima dell'esecuzione.

**Inference factory functions:**
- `infer_time_start(default_hours_back, fallback_field, delay_minutes)`
- `infer_time_end(fallback_field, delay_minutes)`
- `infer_time_range(default_hours_back, delay_minutes)`
- `apply_delay_cap()` — limita a `now - delay_minutes` (per DPC con 10 min delay)

**Validation factory functions:**
- `value_in_list(field, allowed)` — valore in lista consentita
- `bbox_inside(field, reference)` — bbox contenuta in BBox di riferimento
- `time_within_days(field, days)` — non oltre N giorni fa
- `time_before(field, other_field)` / `time_after(field, other_field)` — ordinamento temporale
- `time_before_datetime(field, ref)` / `time_after_datetime(field, ref)` — confronto con datetime fisso

---

## 9. Sistema dei prompt

### 9.1 Struttura generale

I prompt sono organizzati in classi gerarchiche nidificate in `ma/prompts/`. Ogni classe principale corrisponde a un agente (`OrchestratorPrompts`, `SaferCastPrompts`, `ModelsPrompts`, ecc.).

La classe `Prompt` (dataclass in `ma/prompts/__init__.py`) standardizza tutti i prompt:

```python
@dataclass
class Prompt:
    title: str
    description: str
    command: str
    message: str

    def to(self, message_type) -> SystemMessage | HumanMessage | AIMessage:
        return message_type(content=self.message)
```

### 9.2 Convenzione di versionamento

| Metodo | Uso |
|---|---|
| `stable()` | Versione in produzione — mai sovrascrivere |
| `v001()`, `v002()`, … | Versioni alternative per A/B test — override con `unittest.mock.patch.object` |

I metodi vengono chiamati **a runtime** (non all'import), consentendo patch dinamici nei test senza modificare il sorgente.

### 9.3 Prompt per agente

#### `OrchestratorPrompts` (Supervisor)

| Sezione | Metodo | Tipo output |
|---|---|---|
| `MainContext.stable()` | System role con regole operative per tutti gli agenti | SystemMessage |
| `Plan.CreatePlan.stable(state)` | HumanMessage con request + context per generare piano | HumanMessage |
| `Plan.IncrementalReplanning.stable(state)` | Piano attuale + feedback per modifica parziale | HumanMessage |
| `Plan.TotalReplanning.stable(state)` | Request + feedback per piano da zero | HumanMessage |
| `Plan.PlanExplanation.RequestExplanation.stable(state, question)` | Spiegazione piano per intent "clarify" | SystemMessage |

Il `MainContext` include: regole operative per ogni agente, processo di ragionamento, errori comuni da evitare, formato output con esempi completi.

#### `SaferCastPrompts` (DataRetriever)

| Sezione | Contenuto |
|---|---|
| `MainContext.stable()` | Guide: DPC (Italia/passato) vs Meteoblue (globale/futuro) |
| `ToolSelection.InitialRequest.stable(state)` | goal, parsed_request, relevant_layers, conversation context |
| `ToolSelection.ReinvocationRequest.stable(state)` | goal + invocation attuale + user_feedback |

#### `ModelsPrompts` (Models)

| Sezione | Contenuto |
|---|---|
| `MainContext.stable()` | Guide per digital_twin, safer_rain, saferbuildings, safer_fire — regole parameter-by-parameter |
| `ToolSelection.InitialRequest.stable(state)` | goal, relevant_layers, conversation context |
| `ToolSelection.ReinvocationRequest.stable(state)` | idem con feedback utente |

#### `MapAgentPrompts`

| Sezione | Contenuto |
|---|---|
| `SystemPrompt.stable()` | Regole per `move_map_view` e `set_layer_style` |
| `MAPLIBRE_STYLE_PROMPT` | Prompt expert per MapLibre GL JS: operatori expression, paint properties per geometry type, guidelines per scelta expression type |

#### `RequestParserPrompts`

`MainContext.stable(layer_summary)`: guida per estrarre `ParsedRequest` strutturato con risoluzione entità, elenco capabilities piattaforma, lista layer disponibili formattata.

#### `FinalResponderPrompts`

`Response.stable(state)`: selezione automatica variante in base a `execution_narrative`:
- `_completed_plan` → report risultati, guida mappa, next steps suggeriti
- `_info_response` → risposta informativa senza tool
- `_aborted_plan` → chiusura educata post-abort
- `_error_response` → report errori + recovery suggestions

---

## 10. HITL — Human-in-the-Loop

### 10.1 Interrupt points

Il sistema usa `interrupt()` di LangGraph esclusivamente nei nodi `*InvocationConfirm` e `*PlannerConfirm`. Ogni interrupt restituisce un campo `interrupt_type` che segue la convenzione `{sostantivo}-{verbo}`.

| `interrupt_type` | Nodo | Condizione |
|---|---|---|
| `plan-confirmation` | `SUPERVISOR_PLANNER_CONFIRM` | Piano non vuoto, `enabled=True` |
| `plan-clarification` | `SUPERVISOR_PLANNER_CONFIRM` | Clarify loop attivo |
| `invocation-validation` | `{AGENT}_INVOCATION_CONFIRM` | Argomenti non validi |
| `invocation-confirmation` | `{AGENT}_INVOCATION_CONFIRM` | `enabled=True`, validazione OK |

### 10.2 `ResponseClassifier` (`common/response_classifier.py`)

Classificatore ibrido a 2 livelli:

**Livello 1 (rule-based):** pattern regex per intent comuni (accept, abort, skip, reject, autocorrect, clarify, modify). Rapido, deterministico.

**Livello 2 (LLM fallback):** zero-shot classification con lista label valide. Attivato solo se il livello 1 non produce match.

**Metodi pubblici:**

| Metodo | Label possibili |
|---|---|
| `classify_plan_response()` | `accept / modify / clarify / reject / abort` |
| `classify_invocation_response()` | stesse label |
| `classify_validation_response()` | `provide_corrections / clarify_requirements / auto_correct / acknowledge / skip_tool / abort` |

### 10.3 `ToolInvocationConfirmationHandler` (`ma/specialized/confirmation_utils.py`)

Centralizza la logica HITL per l'invocazione tool. `process_confirmation()` dispatcha su:

| Intent | Azione |
|---|---|
| `accept` | `{prefix}_invocation_confirmation = "accepted"` |
| `modify` / `reject` | `"rejected"` + salva `{prefix}_reinvocation_request` |
| `abort` | Salta step successivo o marca piano completo |
| `clarify` | Loop: spiega via LLM + re-interrupt (budget-aware con `interaction_budget`) |

### 10.4 `ToolValidationResponseHandler` (`ma/specialized/validation_utils.py`)

Gestisce le risposte post-errore di validazione. `process_validation_response()` dispatcha su:

| Intent | Azione |
|---|---|
| `provide_corrections` | Re-invoking con correzioni utente |
| `clarify_requirements` | Loop spiegazione regole + re-interrupt |
| `auto_correct` / `acknowledge` | Auto-correction via LLM |
| `skip_tool` | Rimuove il tool call problematico dall'invocation |
| `abort` | Cancella l'intera operazione |

### 10.5 Budget HITL

Il sistema usa `interaction_count` / `interaction_budget` (default: 8) per prevenire loop infiniti. I nodi incrementano il contatore a ogni interrupt; se il budget è esaurito, il loop si interrompe automaticamente.

### 10.6 Template messaggi deterministici (`common/templates.py`)

Funzioni per generare messaggi di conferma **senza LLM**, deterministici e formattati per l'utente:

- `format_plan_confirmation(plan, parsed_request)` → lista numerata degli step + istruzioni risposta
- `format_tool_confirmation(tool_calls)` → elenco tool e argomenti (troncati a 120 chars)
- `format_validation_errors(validation_errors)` → report per tool/argomento + opzioni

`AGENT_LABELS`: mapping nomi tecnici → label human-readable italiane.

---

## 11. Agent Interface

### 11.1 `GraphInterface` (`agent_interface/graph_interface.py`)

Wrappa il grafo compilato con un'interfaccia orientata alla sessione.

**Costruzione:**
- Crea una coppia `(thread_id, {user_id, project_id})`
- `restore_state()`: carica `layer_registry` da S3 (`layer_registry.json`) all'init, se esiste
- Supporto opzionale per `LeafmapInterface` (Jupyter) e `CesiumHandler` (3D viewer)

**API principale:**

| Metodo | Descrizione |
|---|---|
| `user_prompt(prompt, state_updates)` | Invia `HumanMessage`, gestisce interrupt e resume, ritorna `ConversationHandler` |
| `get_state(key)` | Lettura campo stato |
| `set_state(dict)` | Scrittura campo stato |
| `register_layer(layer)` | Aggiunge layer al registry + persiste su S3 |

**Gestione interrupt**: il metodo `user_prompt` cicla sui `StreamEvent` del grafo. Se incontra un `Interrupt`, serializza il tipo e il payload, aspetta la risposta utente, e riprende l'esecuzione con `Command(resume=response)`.

### 11.2 `GraphRegistry` (`agent_interface/graph_interface.py`)

Singleton che gestisce `dict[thread_id → GraphInterface]`.

| Metodo | Descrizione |
|---|---|
| `register(thread_id, ...)` | Crea e registra una nuova sessione |
| `get(thread_id)` | Recupera sessione esistente |
| `remove(thread_id)` | Rimuove sessione |

Il registro globale `__GRAPH_REGISTRY__` è esposto a livello di package da `__init__.py` e montato sull'app Flask in `app.__GRAPH_REGISTRY__`.

### 11.3 Flask Server (`agent_interface/flask_server/`)

#### `app.py` — `create_app()`
Factory Flask con CORS configurato, static/template folders da variabili d'ambiente, e montaggio del `GraphRegistry`.

#### `routes.py` — Endpoint REST

| Route | Metodo | Descrizione |
|---|---|---|
| `GET /` | GET | Render `index.html` |
| `/user` | POST | Lista `{user_id, projects}` da prefissi S3 |
| `/t` | POST | Crea o recupera una `GraphInterface` (thread_id, user_id, project_id) |
| `/t/<thread_id>/state` | POST | Get o Set campo stato del grafo |
| `/t/<thread_id>` | POST | Invia `prompt`; opzione `stream=true` per SSE (Server-Sent Events) |
| `/t/<thread_id>/layers` | POST | Ritorna `layer_registry` corrente |
| `/t/<thread_id>/shapes` | POST | Ritorna `user_drawn_shapes` |
| `/t/<thread_id>/render` | POST | Converti layer in COG/GeoJSON, registrazione opzionale |
| `/cesium-viewer` | POST | Serve Cesium 3D viewer |
| `/cesium-viewer/api/load-wds` | POST | Preprocessa WD layer per visualizzazione Cesium 3D |
| `/get-layer-url` | POST | Ritorna download URL per sorgente S3 |

**Sicurezza**: i prompt utente vengono sanitizzati con `escape()` (Markupsafe) prima dell'elaborazione, prevenendo injection HTML.

### 11.4 `ConversationHandler`

Accumula `events: list[AnyMessage | Interrupt]`. `chat2json()` serializza ogni evento in un dict standardizzato con campo `role` (`"human"`, `"ai"`, `"tool"`, `"interrupt"`).

### 11.5 `ChatMarkdownHandler` (`agent_interface/chat_handler.py`)

Utility per uso in Jupyter Notebook / dev tools. Converte la chat in Markdown formattato con:
- TOC con anchor
- Blocchi per ruolo: 👤 User / 🤖 AI / 🛠️ Tool / ⏸️ Interrupt
- Pretty-print JSON per i tool output
- Gestione fence conflict con i blocchi di codice

---

## 12. Moduli common

### 12.1 `common/utils.py` — Utility generali

| Categoria | Funzioni chiave |
|---|---|
| ID / path | `guid()`, `b64uuid()`, `random_id8()`, `hash_string()`, `normpath()` |
| Path manipulation | `juststem()`, `justpath()`, `justfname()`, `justext()`, `forceext()` |
| S3 / URL | `download_url()`, `s3uri_to_https()`, `s3https_to_s3uri()`, `s3uri_to_vsis3()` |
| Raster | `raster_specs()`, `raster_ts_specs()`, `tif_to_cog3857()`, `is_cog()`, `is_raster_3857()` |
| Vector | `vector_specs()`, `vector_to_geojson4326()` |
| State merge | `merge_sequences()`, `merge_dictionaries()`, `merge_dict_sequences()` |
| LLM | `_base_llm` (istanza globale `ChatOpenAI`) |
| Conversation | `get_conversation_context()` |

**Geospatial pipeline**: conversione automatica vector → GeoJSON WGS84 4326, raster → COG EPSG:3857, con check S3 per evitare riconversioni inutili.

### 12.2 `common/s3_utils.py` — AWS S3

| Variabile | Descrizione |
|---|---|
| `_BASE_BUCKET_` | Bucket principale (env `BUCKET_NAME`/`BUCKET_OUT_DIR`) |
| `_STATE_BUCKET_` | Lambda per path user/project specifici |

| Funzione | Descrizione |
|---|---|
| `get_bucket_name_key(uri)` | Parsing URI S3 (formati: `s3://`, `/vsis3/`, HTTPS) |
| `s3_download(uri, fileout)` | Download con cache locale via etag comparison |
| `s3_upload(filename, uri)` | Upload con skip se hash identico |
| `s3_exists(uri)` | Verifica esistenza oggetto |
| `etag(filename)` | Calcolo hash multipart-compatible |
| `s3_equals(file1, file2)` | Confronto via etag |

### 12.3 `common/execution_narrative.py`

Struttura dati per tracciare la storia di esecuzione dell'intero ciclo.

**`ExecutionNarrative`** (dataclass):

| Campo | Tipo |
|---|---|
| `request_summary`, `request_type` | `str` |
| `plan_summary`, `total_steps` | `str`, `int` |
| `steps_executed` | `List[StepResult]` |
| `layers_created`, `layers_used` | `List[LayerSummary]` |
| `errors` | `List[StepError]` |
| `user_interactions` | `List[str]` |
| `suggestions` | `List[str]` |
| `started_at`, `completed_at` | `datetime` |

**`get_completion_status()`** → `"pending" / "completed" / "partial" / "failed"` basato sugli `outcome` degli step.

**`StepResult`**: `step_index`, `agent`, `goal`, `tool_name`, `outcome` (`success/partial/error/skipped`).
**`StepError`**: `step_index`, `tool_name`, `error_type`, `message`, `recovery_suggestion`.
**`LayerSummary`**: `layer_id`, `name`, `layer_type`, `source_uri`, `description`, `metadata`.

---

## 13. Componenti legacy

### 13.1 `graph.py`

Grafo **legacy** non più attivo. Assembla un'architettura precedente con:
- Nodi chatbot: `chatbot`, `chatbot_update_messages`, `fix_orphan_tool_calls`
- Subgraph legacy: `saferplaces_api_subgraph`, `safercast_api_subgraph`
- Stato: `BaseGraphState` (non `MABaseGraphState`)
- Nomi: `common/names.py` legacy (non `ma/names.py`)

**Non è il grafo attivo** — scopo: backward compatibility o riferimento storico.

### 13.2 `common/names.py`

File di costanti per l'architettura originale. Contiene la classe `NodeNames` legacy (`GRAPH`, `initial_chat_agent`, `safercast_agent`) e costanti globali per tutti i subgraph legacy (`CHATBOT`, `FLOODING_RAINFALL_SUBGRAPH`, `SAFERPLACES_API_SUBGRAPH`, ecc.).

**Da non confondere** con `ma/names.py` che è la fonte di verità per il sistema attuale.

### 13.3 `nodes/`

Cartella esclusa dalla presente analisi.

---

## Appendice — Schema architetturale completo

```
HTTP Request (Flask /t/<thread_id>)
         │
         ▼
  GraphInterface.user_prompt()
         │
         ▼
  LangGraph: MABaseGraphState
         │
         ├──► REQUEST_PARSER
         │        └─ LLM → ParsedRequest (structured output)
         │
         ├──► SUPERVISOR_SUBGRAPH
         │    ├─ SupervisorAgent
         │    │       ├─ ContextBuilder.build(state)
         │    │       └─ LLM → ExecutionPlan (List[PlanStep])
         │    ├─ SupervisorPlannerConfirm
         │    │       ├─ templates.format_plan_confirmation()
         │    │       ├─ interrupt() ← [HITL]
         │    │       └─ ResponseClassifier → accept/modify/reject/abort/clarify
         │    └─ SupervisorRouter
         │            └─ supervisor_next_node → routing decision
         │
         ├──► RETRIEVER_SUBGRAPH
         │    ├─ DataRetrieverAgent (DPC + Meteoblue tools)
         │    ├─ DataRetrieverInvocationConfirm
         │    │       ├─ inference rules (_inferrers.py)
         │    │       ├─ validation rules (_validators.py)
         │    │       └─ [interrupt() HITL se enabled=True]
         │    └─ DataRetrieverExecutor
         │
         ├──► MODELS_SUBGRAPH
         │    ├─ ModelsAgent (DigitalTwin + SaferRain + SaferBuildings + SaferFire)
         │    ├─ ModelsInvocationConfirm
         │    │       ├─ inference/validation rules
         │    │       └─ [interrupt() HITL se enabled=True]
         │    └─ ModelsExecutor
         │
         ├──► MAP_AGENT
         │    ├─ LLM → tool_calls (MapLibre style / Nominatim geocoding)
         │    ├─ MoveMapViewTool → state["map_view"] + state["map_commands"]
         │    └─ LayerSymbologyTool → layer.style + state["map_commands"]
         │
         └──► FINAL_RESPONDER
                  ├─ Seleziona variante (completed/info/aborted/error)
                  ├─ LLM → risposta testuale contestuale
                  └─ StateManager.cleanup_on_final_response()
                                │
                                ▼
                        HTTP Response (SSE / JSON)
```
