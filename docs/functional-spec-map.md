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
