# Layer & Shape Context Injection — Report

> Mappatura di tutti i punti in cui le informazioni sui layer geospaziali e sulle shapes
> vengono estratte dallo stato e iniettate nei prompt degli agenti.

---

## 1. Campi di stato rilevanti

**File:** `common/states.py` — `MABaseGraphState` (TypedDict)

| Campo | Tipo | Contenuto |
|---|---|---|
| `layer_registry` | `Annotated[Sequence[dict], merge_layer_registry]` | Layer geospaziali attivi: `{title, type, src, description, metadata}` |
| `shapes_registry` | `Annotated[Sequence[dict], merge_shape_registry]` | Shapes disegnate dall'utente: `{shape_id, shape_type, geometry, label, metadata}` |

### Metadati layer

| Campo | Contesto |
|---|---|
| `bbox` | `{west, south, east, north}` |
| `surface_type` | es. `"DEM"`, `"flood_depth"` |
| `attributes` | (vector) `{col_name: dtype}` |
| `geometry_type` | (vector) lista di tipi geometrici |
| `min`, `max` | (raster) range di valori |
| `nodata` | (raster) valore no-data |
| `n_bands` | (raster) numero di bande |
| `colormap_name` | (raster) schema colore |

### Metadati shape

| Campo | Contesto |
|---|---|
| `crs` | default `"EPSG:4326"` |
| `lon`, `lat` | coordinate del punto |
| `bbox` | `{west, south, east, north}` |
| `area_km2` | calcolato |
| `length_km` | calcolato |
| `num_features` | per multi-geometrie |

---

## 2. Funzioni di serializzazione centralizzate in `common/states.py`

### `build_layer_registry_system_message(layer_registry: list)`

Produce un `SystemMessage` con header `[LAYER REGISTRY]`.  
Per ogni layer formatta: title, type, src, description e l'intero blocco `metadata` come JSON indentato.

### `build_shapes_registry_system_message(shapes_registry: list)`

Produce un `SystemMessage` con header `[REGISTERED SHAPES]`.  
Per ogni shape formatta: shape_id, shape_type, geometry GeoJSON, e i metadati via `_shape_metadata_lines()`.

### `_shape_metadata_lines(metadata: dict)` (helper privato)

Formatta in output: crs, coordinates (lon/lat), bbox, area_km2, length_km, num_features.

> ⚠️ Queste funzioni sono usate selettivamente — **non** sono l'unica fonte di serializzazione.
> Esistono duplicati parziali nei prompt files (vedi §3).

---

## 3. Punto centrale di composizione: `layers_agent_prompts.py`

**File:** `ma/prompts/layers_agent_promps.py`

Queste due classi sono il **riferimento condiviso** utilizzato da quasi tutti gli altri prompt.

### `LayersAgentPrompts.BasicLayerSummary.stable(state)`

```
Stato letto: state["layer_registry"]

Per ogni layer:
  - title
  - type
  - src
  - description
  - metadata.bbox          → {west, south, east, north}
  - metadata.surface_type

Output: Prompt con header "Available layers in current project"
        Formato: lista puntata
```

### `LayersAgentPrompts.BasicShapesSummary.stable(state)`

```
Stato letto: state["shapes_registry"]

Per ogni shape:
  - label  (fallback a shape_id)
  - shape_type
  - metadata.bbox          → formattato come "bbox: W=... S=... E=... N=..."

Output: Prompt con header "Registered shapes"
        Formato: lista puntata compatta
```

---

## 4. Punti di iniezione per agente

### 4.1 `RequestParserInstructions._GlobalContext.stable(state)`

**File:** `ma/prompts/request_parser_prompts.py`

Compone il contesto globale per il parser della richiesta utente:

```
[CURRENT UTC0 DATETIME]
[AVAILABLE LAYERS]     ← LayersAgentPrompts.BasicLayerSummary.stable(state)
[REGISTERED SHAPES]    ← LayersAgentPrompts.BasicShapesSummary.stable(state)
[MAP CONTEXT]          ← MapAgentPrompts._viewport_context(state)
[CONVERSATION HISTORY] ← ContextBuilder.conversation_history(state, max_messages=5)
```

---

### 4.2 `SupervisorInstructions.PlanGeneration.Prompts._GlobalContext.stable(state)`

**File:** `ma/prompts/supervisor_agent_prompts.py`

```
[CURRENT UTC0 DATETIME]
[PARSED REQUEST]
[AVAILABLE LAYERS]     ← LayersAgentPrompts.BasicLayerSummary.stable(state)
[REGISTERED SHAPES]    ← LayersAgentPrompts.BasicShapesSummary.stable(state)
[CONVERSATION HISTORY]
```

Stesso pattern anche in `SupervisorInstructions.PlanModification.Prompts._GlobalContext`.

---

### 4.3 `ModelsInstructions.InvokeTools.Prompts._GlobalContext.stable(state)`

**File:** `ma/prompts/models_agent_prompts.py`

```
[CURRENT UTC0 DATETIME]
[PARSED REQUEST]
[AVAILABLE LAYERS]     ← LayersAgentPrompts.BasicLayerSummary.stable(state)
[REGISTERED SHAPES]    ← LayersAgentPrompts.BasicShapesSummary.stable(state)
[CONVERSATION HISTORY]
[GOAL]                 ← state['plan'][state['current_step']]['goal']
```

---

### 4.4 `SaferCastInstructions.InvokeTools.Prompts._GlobalContext.stable(state)`

**File:** `ma/prompts/safercast_agent_prompts.py`

Stesso pattern di `ModelsInstructions._GlobalContext` (con `[GOAL]` alla fine).

---

### 4.5 `FinalResponderInstructions._GlobalContext.stable(state)` — (3 varianti)

**File:** `ma/prompts/final_responder_prompts.py`

Usato da `GenerateResponse`, `GenerateInfoResponse`, `GenerateAbortResponse`:

```
[CURRENT UTC0 DATETIME]
[PARSED REQUEST]
[AVAILABLE LAYERS]     ← LayersAgentPrompts.BasicLayerSummary.stable(state)
[REGISTERED SHAPES]    ← LayersAgentPrompts.BasicShapesSummary.stable(state)
[MAP CONTEXT]          ← MapAgentPrompts._viewport_context(state)
[CONVERSATION HISTORY] ← ContextBuilder.conversation_history(state, max_messages=10)
```

---

### 4.6 `LayersInstructions.InvokeTools.Invocation.InvokeOneShot.stable(state)`

**File:** `ma/prompts/layers_agent_promps.py`

Usato da `LayersAgent.run()` per costruire i messaggi di invocazione LLM.  
Include `BasicLayerSummary` e `BasicShapesSummary` come contesto tools.

---

### 4.7 `MapAgentPrompts.ExecutionContext.stable(state)` — formato esteso

**File:** `ma/prompts/map_agent_prompts.py`

Questo è il punto con il formato **più ricco e specifico** per il MapAgent.  
Non usa `BasicLayerSummary` / `BasicShapesSummary` — ha propri formattatori locali.

```
[MAP VIEWPORT]
  - bounds (west, south, east, north)
  - zoom level

[AVAILABLE LAYERS]     ← _format_layer_registry_summary(layer_registry)
  Per layer:
    - title
    - type
    - src
    - metadata.attributes → nomi delle colonne (solo per vector)

[REGISTERED SHAPES]    ← estrazione inline dettagliata
  Per shape:
    - shape_id
    - shape_type
    - geometry_type (da GeoJSON)
    - label
    - metadata: bbox, lon/lat, area_km2, length_km
    - geometria completa via _serialize_geometry_for_context()
      → troncata se > 50 coordinate per ring
```

#### Helper `_serialize_geometry_for_context(geom)` — regole di troncamento

| Tipo | Comportamento |
|---|---|
| `Point` | `[lon=X, lat=Y]` |
| `LineString` ≤ 50 pts | JSON completo |
| `LineString` > 50 pts | `(truncated — N points, bbox=[...])` |
| `Polygon` totale ≤ 50 pts | JSON completo di tutti i ring |
| `Polygon` totale > 50 pts | `(truncated — outer ring has N points, bbox=[...])` |
| `Multi*` | `(multi-geometry, N features, bbox=[...])` |

---

## 5. Iniezione diretta negli strumenti (MapAgent)

**File:** `ma/specialized/map_agent.py`

```python
for tool in self._tools:
    tool.state = state
```

I tool di MapAgent (`LayerSymbologyTool`, `RegisterShapeTool`, `CreateShapeTool`, `MoveMapViewTool`)
ricevono l'intero stato e accedono direttamente a `state["layer_registry"]` e
`state["shapes_registry"]` durante l'esecuzione — **non** tramite prompt.

---

## 6. Tabella riepilogativa

| File | Classe / Metodo | Stato letto | Campi estratti | Troncamento |
|---|---|---|---|---|
| `layers_agent_promps.py` | `BasicLayerSummary.stable()` | `layer_registry` | title, type, src, desc, bbox, surface_type | — |
| `layers_agent_promps.py` | `BasicShapesSummary.stable()` | `shapes_registry` | label, shape_type, bbox | — |
| `map_agent_prompts.py` | `ExecutionContext.stable()` | entrambi | formato esteso + geometry | 50 coords/ring |
| `map_agent_prompts.py` | `_format_layer_registry_summary()` | `layer_registry` | title, type, src, attributes | — |
| `map_agent_prompts.py` | `_serialize_geometry_for_context()` | geometry (da shape) | GeoJSON o bbox summary | 50 pts/ring |
| `request_parser_prompts.py` | `_GlobalContext.stable()` | entrambi | via BasicLayerSummary + BasicShapesSummary | ereditato |
| `supervisor_agent_prompts.py` | `_GlobalContext.stable()` ×2 | entrambi | via BasicLayerSummary + BasicShapesSummary | ereditato |
| `models_agent_prompts.py` | `_GlobalContext.stable()` | entrambi | via BasicLayerSummary + BasicShapesSummary | ereditato |
| `safercast_agent_prompts.py` | `_GlobalContext.stable()` | entrambi | via BasicLayerSummary + BasicShapesSummary | ereditato |
| `final_responder_prompts.py` | `_GlobalContext.stable()` ×3 | entrambi | via BasicLayerSummary + BasicShapesSummary | ereditato |
| `states.py` | `build_layer_registry_system_message()` | `layer_registry` | tutto + metadata JSON | — |
| `states.py` | `build_shapes_registry_system_message()` | `shapes_registry` | shape_id, type, geometry, metadata | — |
| `map_agent.py` | Iniezione diretta in tools | entrambi | accesso diretto a runtime | — |

---

## 7. Pattern architetturali osservati

### ✅ Pattern dominante: composizione tramite `BasicLayerSummary` / `BasicShapesSummary`

6 prompt files su 7 riusano le stesse due funzioni centralizzate in `layers_agent_promps.py`.
Formato compatto, leggibile, senza geometrie.

### ⚠️ Eccezione: `MapAgent` ha il proprio formattatore

`map_agent_prompts.py` ridefinisce localmente la serializzazione di layer e shapes con
un formato più ricco (attributes vettoriali, geometria troncata, viewport). Non riusa
`BasicLayerSummary` né `BasicShapesSummary`.

### ⚠️ Duplicato parziale in `states.py`

`build_layer_registry_system_message()` e `build_shapes_registry_system_message()` producono
un formato JSON/dettagliato diverso da quello dei prompt. Vanno verificati dove sono effettivamente
usati — se non referenziati da nessun agente attivo, sono dead code.

### ℹ️ Differenza di dettaglio tra agenti

| Agente | Layer info | Shape info |
|---|---|---|
| REQUEST_PARSER | compatta | solo bbox |
| SUPERVISOR | compatta | solo bbox |
| MODELS / SAFERCAST | compatta | solo bbox |
| FINAL_RESPONDER | compatta | solo bbox |
| MAP_AGENT | con attributes | geometry troncata + bbox + metriche |
| LAYERS_AGENT (tools) | accesso diretto | accesso diretto |
