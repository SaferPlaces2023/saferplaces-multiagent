# Functional Spec — Servizi Esterni e Tool

> **Tipo**: Vivente — aggiornare quando si aggiunge/modifica un tool o si cambia un'integrazione esterna.
> **Namespace**: `S###` (vedere [`docs/index.md`](index.md) per il registro completo).

---

## S001 — DPC Radar Retriever

**Descrizione**: Recupero prodotti radar dal DPC (Dipartimento Protezione Civile italiano).

**Componente**: `ma/specialized/tools/dpc_retriever_tool.py` → `DPCRetrieverTool`

**Argomenti**:
| Argomento | Tipo | Validatori | Inferrers |
|---|---|---|---|
| `bbox` | `str` | `validate_bbox`, `validate_bbox_in_italy` | `infer_bbox_from_location` |
| `time_range` | `str` | `validate_time_range`, `validate_recent_time` | `infer_time_range_from_now` |
| `product` | `str` | `validate_dpc_product` | — |

**Prodotti disponibili**: `SRI` (Surface Rainfall Intensity), `PAC` (Accumulated Precipitation), `VMI` (Maximum Instantaneous Wind)

**Output**: `{"layer": {...}, "uri": "s3://...", "metadata": {...}}`

---

## S002 — Meteoblue Forecast Retriever

**Descrizione**: Recupero previsioni meteo da Meteoblue API.

**Componente**: `ma/specialized/tools/meteoblue_retriever_tool.py` → `MeteoblueRetrieverTool`

**Argomenti**:
| Argomento | Tipo | Validatori | Inferrers |
|---|---|---|---|
| `bbox` | `str` | `validate_bbox` | `infer_bbox_from_location` |
| `time_range` | `str` | `validate_time_range`, `validate_future_time` | `infer_time_range_from_now` |
| `variable` | `str` | `validate_meteoblue_variable` | `infer_variable_from_goal` |
| `resolution` | `str` | `validate_resolution` | `infer_resolution_from_bbox` |

**Variabili tipiche**: `temperature_2m`, `precipitation`, `wind_speed`, `relative_humidity`, `cloud_cover`

**Output**: layer meteorologico raster/vector + metadata

---

## S003 — SaferRain Flood Simulation

**Descrizione**: Simulazione flood con modello SAFER Rain.

**Componente**: `ma/specialized/tools/safer_rain_tool.py` → `SaferRainTool`

**Argomenti**:
| Argomento | Tipo | Validatori | Inferrers |
|---|---|---|---|
| `dem_layer` | `str` | `validate_layer_exists`, `validate_dem_format` | `infer_dem_from_registry` |
| `rainfall_mm` | `float` | `validate_positive_number`, `validate_realistic_rainfall` | — |
| `duration_hours` | `float` | `validate_positive_number` | — |
| `bbox` | `str` | `validate_bbox` | `infer_bbox_from_location` |

**Output**: `{"output_layer": "s3://.../flood_extent.tif", "statistics": {...}, "report": "..."}`

**Parametri avanzati**: `infiltration_rate`, `manning_coefficient`

---

## S004 — Tool Validation + Inference System

**Descrizione**: Infrastruttura comune di validation e inference per tutti i tool.

**Componenti**:
- `ma/specialized/tools/_validators.py` — funzioni di validazione riutilizzabili
- `ma/specialized/tools/_inferrers.py` — funzioni di inferenza context-aware

**Pattern di registrazione su ogni tool**:
```python
def _set_args_validation_rules(self) -> Dict[str, List[Callable]]:
    return {"arg_name": [validator1, validator2]}

def _set_args_inference_rules(self) -> Dict[str, Callable]:
    return {"arg_name": infer_arg_function}
```

**Flusso di applicazione** (nel nodo `InvocationConfirm`):
1. Per ogni `tool_call` proposto dall'LLM:
   - Aggiunge `_graph_state` ai kwargs
   - Applica inference → riempie args mancanti
   - Applica validation → blocca args invalidi
2. Se OK → `state[*_confirmation] = "accepted"` → Executor
3. Se KO → interrupt per correzione utente → re-invoke Agent

**Graph state disponibile agli inferrer** (via `kwargs.pop('_graph_state', None)`):
- `layer_registry` — layer disponibili
- `parsed_request` — intent e contesto della richiesta
- `additional_context.relevant_layers` — layer rilevanti per il piano
- `project_id`, `user_id` — per determinazione S3 path

---

## S005 — Storage Output (S3)

**Descrizione**: Pattern per la destinazione degli output dei tool su storage S3.

**Inferrer**: `infer_bucket_destination(**kwargs)` in `_inferrers.py`

**Path convention**: `s3://bucket/{project_id}/{user_id}/data/`

**Usato da**: `DPCRetrieverTool`, `MeteoblueRetrieverTool`, `SaferRainTool`
