# Functional Spec — Layer e Geospaziale

> **Tipo**: Vivente — aggiornare quando si modifica il layer registry, i tool geospaziali o l'integrazione con Cesium/Leafmap.
> **Namespace**: `M###` (vedere [`docs/index.md`](index.md) per il registro completo).

---

## M001 — Layer Registry

**Descrizione**: Registry centralizzato dei layer geospaziali, persistente across i turn della conversazione.

**Componente**: `specialized/layers_agent.py` → `LayersAgent`

**Struttura del registry** (in `MABaseGraphState`):
```python
layer_registry: Dict[str, LayerMetadata]
# chiave: layer_id univoco
# valore: metadati (tipo, URI, bbox, timestamp, is_dirty, ecc.)
```

**Persistenza**: `layer_registry` sopravvive al `cleanup_on_final_response()` — è lo stato condiviso tra richieste successive.

---

## M002 — LayersAgent Tool Set

**Descrizione**: Set di tool per la gestione del layer registry, eseguiti senza interrupt.

**Componente**: `specialized/layers_agent.py`

**Tool disponibili**:
| Tool | Scopo |
|---|---|
| `ListLayersTool` | Elenca tutti i layer disponibili nel registry |
| `GetLayerTool` | Recupera dettaglio di un layer specifico |
| `AddLayerTool` | Aggiunge un layer manualmente |
| `RemoveLayerTool` | Rimuove un layer dal registry |
| `UpdateLayerTool` | Aggiorna metadati di un layer esistente |
| `SearchByTypeTool` | Filtra layer per tipo (DEM, radar, forecast, flood, ecc.) |
| `BuildLayerFromPromptTool` | Genera metadata di un layer da una richiesta testuale |
| `ChooseLayerTool` | Seleziona il layer più adatto come input per un'operazione |

**Caratteristica chiave**: Esecuzione IMMEDIATA (non interrompibile), aggiorna `layer_registry` inline.

---

## M003 — Context Refresh (is_dirty Pattern)

**Descrizione**: Meccanismo per segnalare che il context dei layer rilevanti per il piano deve essere aggiornato.

**Campo di stato**: `additional_context.relevant_layers.is_dirty`

**Flusso**:
1. Ogni Executor node imposta `is_dirty = True` dopo aver aggiunto un nuovo layer
2. `SupervisorRouter` controlla `is_dirty` prima di pianificare il passo successivo
3. Se `True` → chiama `LayersAgent` per refresh del context → aggiorna `additional_context`
4. `is_dirty` viene resettato da `StateManager.cleanup_on_final_response()`

---

## M004 — User Drawn Shapes

**Descrizione**: Layer disegnati dall'utente sulla mappa, persistenti tra le sessioni.

**Campo di stato**: `user_drawn_shapes`

**Persistenza**: mantenuto da `cleanup_on_final_response()` (come `layer_registry`).

**Uso**: fornisce geometrie di riferimento (AOI) agli agenti per bounded operations (bbox, clipping, ecc.).

---

## M005 — Layer come Input per Simulazioni

**Descrizione**: Meccanismo con cui i layer prodotti da un agente diventano input per il successivo.

**Flusso tipico**:
```
DPCRetrieverTool → layer radar (step_0)
      ↓ (layer_registry aggiornato, is_dirty=True)
SupervisorRouter → context refresh via LayersAgent
      ↓
ModelsAgent → ChooseLayerTool seleziona DEM dal registry
      ↓
SaferRainTool (dem_layer = DEM scelto, rainfall da radar)
      ↓ → output flood_extent layer → registry
```

**Tool chiave per la selezione**: `ChooseLayerTool` + inferrer `infer_dem_from_registry` (in `_inferrers.py`)

---

## M006 — Map View State

**Stato**: 🚧 in progress — Implementata con: PLN-014

**Descrizione**: Campo `map_view` in `MABaseGraphState` che descrive lo stato corrente della viewport della mappa frontend (MapLibre GL JS). Permette agli agenti di conoscere il punto di vista dell'utente e di modificarlo programmaticamente.

**Campo di stato**:
```
map_view: Optional[MapView]
```

**Struttura `MapView`** (modello Pydantic in `base_models.py`):

| Campo | Tipo | Descrizione |
|---|---|---|
| `center_lon` | `float` | Longitudine del centro della vista |
| `center_lat` | `float` | Latitudine del centro della vista |
| `zoom` | `float` | Livello di zoom MapLibre |
| `bbox` | `Optional[List[float]]` | Bounding box corrente `[west, south, east, north]` — calcolato dal frontend o inferito |

**Persistenza**: `map_view` sopravvive al `cleanup_on_final_response()` — viene aggiornato dai tool del `MapAgent` e letto dal supervisor per contestualizzare la richiesta.

**Aggiornamento**: il campo viene aggiornato sia dall'evento frontend (al mount della mappa) sia dall'esecuzione di `MoveMapViewTool` (M009).

---

## M007 — Map Commands Queue

**Stato**: 🚧 in progress — Implementata con: PLN-014

**Descrizione**: Meccanismo di comunicazione unidirezionale dal grafo al frontend per trasmettere comandi di mappa (spostamento viewport, aggiornamento stile layer, ecc.) al termine di ogni ciclo di esecuzione.

**Campo di stato**:
```
map_commands: Annotated[List[MapCommand], merge_map_commands]
```

**Struttura `MapCommand`** (modello Pydantic in `base_models.py`):

| Campo | Tipo | Descrizione |
|---|---|---|
| `type` | `str` | Tipo di comando: `"move_view"`, `"set_layer_style"`, `"draw_shape"`, ecc. |
| `payload` | `Dict[str, Any]` | Dati specifici del comando (dipendenti dal type) |
| `timestamp` | `str` | ISO timestamp di creazione del comando |

**Flusso di consumo**:
1. Il `MapAgent` esegue un tool (es. `MoveMapViewTool`) che appende un `MapCommand` alla lista
2. Il `FinalResponder` include i `map_commands` nella risposta strutturata
3. Il frontend (Flask SSE o API response) consuma la lista e la applica alla mappa
4. La lista viene azzerata da `cleanup_on_final_response()` a ogni nuovo ciclo

**Merge policy**: `merge_map_commands` concatena le liste — ogni tool può appendere comandi; non si sovrascrivono.

---

## M008 — Map Agent

**Stato**: 🚧 in progress — Implementata con: PLN-014

**Descrizione**: Agente specializzato per la gestione delle interazioni visive con la mappa frontend. Gestisce lo spostamento della viewport e la modifica della simbologia dei layer.

**Componente**: `ma/specialized/map_agent.py` → `MapAgent`

**Pattern di esecuzione**: Esecuzione IMMEDIATA (no interrupt), analogo a `LayersAgent`. Le azioni visive (spostamento mappa, cambio stile) non richiedono conferma esplicita — il piano approvato dall'utente è sufficiente.

**Razionale del pattern senza confirm**: a differenza di `ModelsAgent` e `RetrieverAgent` (che attivano operazioni costose o esterne), `MapAgent` esegue solo modifiche locali allo stato e comandi visivi. Il rischio è basso e la conferma aggiungerebbe latenza senza valore.

**Tool gesiti**:
| Tool | Scopo |
|---|---|
| `MoveMapViewTool` | Sposta la viewport della mappa (M009) |
| `LayerSymbologyTool` | Modifica la simbologia di un layer (M010) |

**Campi di stato dedicati** in `MABaseGraphState`:
| Campo | Tipo | Descrizione |
|---|---|---|
| `map_request` | `AIMessage` | Invocazione corrente del MapAgent |
| `map_invocation` | `AIMessage` | Messaggio strutturato con tool calls |

**Invocazione dal Supervisor**: il `SupervisorRouter` instrada verso `MAP_AGENT` quando il piano contiene un passo con `agent: "map_agent"`. Il `MAPS_AGENT_DESCRIPTION` nel registry del supervisor descrive i casi d'uso (spostare la mappa, colorare un layer, ecc.).

**Prompts**: definiti in `ma/prompts/map_agent_prompts.py`.

---

## M009 — MoveMapViewTool

**Stato**: 🚧 in progress — Implementata con: PLN-014

**Descrizione**: Tool del `MapAgent` per spostare la viewport della mappa frontend verso una posizione o bounding box specificata.

**Componente**: `ma/specialized/tools/move_map_view_tool.py`

**Argomenti di input**:
| Argomento | Tipo | Obbligatorio | Descrizione |
|---|---|---|---|
| `center_lon` | `Optional[float]` | No | Longitudine centro destinazione |
| `center_lat` | `Optional[float]` | No | Latitudine centro destinazione |
| `zoom` | `Optional[float]` | No | Zoom MapLibre destinazione (default: mantiene zoom corrente) |
| `bbox` | `Optional[List[float]]` | No | Bbox `[W,S,E,N]` — la viewport si adatta per contenere il bbox (fit-bounds) |
| `location_name` | `Optional[str]` | No | Nome di luogo da risolvere via geocoding (inferrer) |

**Logica di risoluzione (inferrers)**:
1. Se `location_name` è fornito → chiama geocoder (Nominatim o simile) per ottenere `center_lon`, `center_lat`, `bbox`
2. Se `bbox` è fornito e `center_lon`/`center_lat` assenti → calcola il centro dal bbox
3. Se né `bbox` né `center_lon`/`center_lat` → usa il `map_view` corrente dallo stato (no-op o logging)

**Validatori**: almeno uno tra `center_lon`+`center_lat`, `bbox`, `location_name` deve essere presente.

**Output**:
- Aggiorna `map_view` in stato con il nuovo centro/zoom
- Appende un `MapCommand` di tipo `"move_view"` alla lista `map_commands` con payload `{center_lon, center_lat, zoom, bbox}`

---

## M010 — LayerSymbologyTool

**Stato**: 🚧 in progress — Implementata con: PLN-014

**Descrizione**: Tool del `MapAgent` per modificare la simbologia (stile visivo) di un layer già presente nel `layer_registry`, tramite una richiesta in linguaggio naturale.

**Componente**: `ma/specialized/tools/layer_symbology_tool.py`

**Argomenti di input**:
| Argomento | Tipo | Obbligatorio | Descrizione |
|---|---|---|---|
| `layer_id` | `str` | Sì | ID del layer nel `layer_registry` |
| `user_request` | `str` | Sì | Richiesta in linguaggio naturale (es. "coloralo in rosso dove il valore supera 10") |

**Logica interna**:
1. Recupera i metadati del layer dal `layer_registry` tramite `layer_id`
2. Estrae `layer_type` (`"vector"` / `"raster"`), `geometry_subtype` e `layer_metadata` (attributi, min/max, ecc.)
3. Invoca un LLM interno con `MAPLIBRE_STYLE_PROMPT` (prompt specializzato, definito in `map_agent_prompts.py`) passando il contesto del layer e la `user_request`
4. Parsa la risposta JSON dell'LLM come oggetto stile MapLibre GL JS
5. Aggiorna il campo `style` nei metadati del layer nel `layer_registry`
6. Appende un `MapCommand` di tipo `"set_layer_style"` con payload `{layer_id, style}`

**Output**:
- Metadati aggiornati in `layer_registry[layer_id].style`
- `MapCommand` di tipo `"set_layer_style"` in `map_commands`

**Prompt specializzato**: `MAPLIBRE_STYLE_PROMPT` in `map_agent_prompts.py` — istruzioni complete per la generazione di espressioni MapLibre valide, con riferimento a operatori `interpolate`, `match`, `step`, `case`, proprietà `paint`/`filter`/`layout` per tipo (fill, line, circle, symbol, raster). Il prompt restituisce esclusivamente JSON, senza markdown né spiegazioni.

**Risposta LLM attesa**:
```
{ "paint": {...}, "filter": [...], "layout": {...} }
```
Solo `paint` è obbligatorio; `filter` e `layout` sono opzionali.
