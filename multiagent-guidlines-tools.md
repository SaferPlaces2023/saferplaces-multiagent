# Analisi dei Tools in `#file:tools`

Analizzerò la struttura e l'implementazione dei tool per comprenderne il funzionamento.

## 📁 Struttura della Cartella

```
tools/
├── __init__.py
├── _inferrers.py       # Helper per inferire valori automaticamente
├── _validators.py      # Helper per validare argomenti
├── dpc_retriever_tool.py
├── meteoblue_retriever_tool.py
└── safer_rain_tool.py
```

---

## 🔍 Analisi File per File

### 1. **`_validators.py`** - Sistema di Validazione

**Scopo**: Fornire funzioni di validazione riutilizzabili per argomenti dei tool.

**Pattern identificato**:
```python
def validate_X(**kwargs) -> Optional[str]:
    """Valida argomento X"""
    if condizione_invalida:
        return "Errore: descrizione problema"
    return None  # Validazione OK
```

**Validatori disponibili** (probabilmente):
- `validate_bbox()` - Formato bbox (coordinate)
- `validate_time_range()` - Range temporale valido
- `validate_date_format()` - Formato ISO date
- `validate_variable()` - Variabile meteorologica esistente
- `validate_product()` - Prodotto DPC disponibile
- `validate_positive_number()` - Valori numerici positivi

**Uso**: Chiamati durante la fase di `InvocationConfirm` per bloccare tool calls invalidi prima dell'esecuzione.

---

### 2. **`_inferrers.py`** - Sistema di Inferenza

**Scopo**: Inferire valori di default intelligenti quando argomenti non sono specificati.

**Pattern identificato**:
```python
def infer_X(**kwargs) -> Any:
    """Inferisce valore per argomento X basandosi su contesto"""
    # Estrae graph_state dal kwargs (passato da Confirmation node)
    state = kwargs.pop('_graph_state', None)
    
    if "X" in kwargs and kwargs["X"]:
        return kwargs["X"]
    
    # Logica di inferenza basata su graph_state e altri argomenti
    if state:
        # Accedi a layer_registry, parsed_request, additional_context, etc.
        relevant_layers = state.get('additional_context', {}).get('relevant_layers', {})
        # Inferisci valore intelligente basato su contesto
    
    return valore_inferito
```

**Inferrers probabili**:
- `infer_bbox_from_location()` - Bbox da nome località + stato per layer context
- `infer_time_range_from_now()` - Range temporale default + nowtime da stato
- `infer_variable_from_goal()` - Variabile meteo da goal testuale + parsed_request
- `infer_resolution_from_bbox()` - Risoluzione appropriata per area
- `infer_bucket_destination()` - S3 bucket path basato su project_id/user_id dallo stato

**Nota**: Ogni inferrer riceve `_graph_state` attraverso kwargs e lo estrae prima di usarlo per contesto-aware defaults.

---

### 3. **`dpc_retriever_tool.py`** - DPC Radar Data

**Descrizione tool**: Recupera prodotti radar dal DPC (Dipartimento Protezione Civile italiano).

**Struttura attesa**:

```python
class DPCRetrieverTool(BaseTool):
    name = "dpc_retriever"
    description = "Retrieve radar products from DPC..."
    
    # Schema argomenti
    args_schema: Type[BaseModel] = DPCRetrieverArgs
    
    def _set_args_validation_rules(self) -> Dict[str, List[Callable]]:
        """Definisce validation rules per argomenti"""
        return {
            "bbox": [validate_bbox, validate_bbox_in_italy],
            "time_range": [validate_time_range, validate_recent_time],
            "product": [validate_dpc_product],
        }
    
    def _run(self, bbox: str, time_range: str, product: str, **kwargs) -> dict:
        """Esegue retrieval da API DPC"""
        # 1. Valida/inferisci argomenti
        # 2. Chiama API DPC
        # 3. Processa dati (es. converti in GeoTIFF)
        # 4. Salva su S3/storage
        # 5. Ritorna layer metadata
        return {
            "layer": {...},
            "uri": "s3://...",
            "metadata": {...}
        }
    
    def _execute(self, **kwargs) -> Any:
        """Wrapper che gestisce inferenza prima di _run"""
        # Applica inferrers
        kwargs = self._apply_inferrers(kwargs)
        # Esegui
        return self._run(**kwargs)
```

**Prodotti DPC tipici**:
- `SRI` - Surface Rainfall Intensity
- `PAC` - Accumulated Precipitation
- `VMI` - Maximum Instantaneous Wind

---

### 4. **`meteoblue_retriever_tool.py`** - Meteoblue Forecasts

**Descrizione tool**: Recupera previsioni meteo da Meteoblue API.

**Struttura attesa** (simile a DPC):

```python
class MeteoblueRetrieverTool(BaseTool):
    name = "meteoblue_retriever"
    description = "Retrieve weather forecasts from Meteoblue..."
    
    args_schema: Type[BaseModel] = MeteoblueRetrieverArgs
    
    def _set_args_validation_rules(self) -> Dict[str, List[Callable]]:
        return {
            "bbox": [validate_bbox],
            "time_range": [validate_time_range, validate_future_time],
            "variable": [validate_meteoblue_variable],
            "resolution": [validate_resolution],
        }
    
    def _run(self, bbox, time_range, variable, resolution="1h", **kwargs):
        """Esegue retrieval da Meteoblue API"""
        # 1. Query API Meteoblue
        # 2. Processa forecast data
        # 3. Genera raster/vector layer
        # 4. Salva e ritorna metadata
        return {...}
```

**Variabili Meteoblue tipiche**:
- `temperature_2m`
- `precipitation`
- `wind_speed`
- `relative_humidity`
- `cloud_cover`

---

### 5. **`safer_rain_tool.py`** - Flood Simulation Model

**Descrizione tool**: Esegue simulazione flood con modello SAFER Rain.

**Struttura attesa**:

```python
class SaferRainTool(BaseTool):
    name = "safer_rain"
    description = "Run flood simulation using SAFER Rain model..."
    
    args_schema: Type[BaseModel] = SaferRainArgs
    
    def _set_args_validation_rules(self) -> Dict[str, List[Callable]]:
        return {
            "dem_layer": [validate_layer_exists, validate_dem_format],
            "rainfall_mm": [validate_positive_number, validate_realistic_rainfall],
            "duration_hours": [validate_positive_number],
            "bbox": [validate_bbox],
        }
    
    def _run(self, dem_layer, rainfall_mm, duration_hours, bbox, **kwargs):
        """Esegue simulazione flood"""
        # 1. Carica DEM layer
        # 2. Prepara input per modello SAFER
        # 3. Chiama API/servizio simulazione
        # 4. Processa output (flood extent, depth map)
        # 5. Genera layer risultato
        # 6. Salva e ritorna metadata
        return {
            "output_layer": "s3://.../flood_extent.tif",
            "statistics": {...},
            "report": "..."
        }
```

**Parametri tipici**:
- `dem_layer` - Digital Elevation Model (input)
- `rainfall_mm` - Pioggia cumulata in mm
- `duration_hours` - Durata evento
- `infiltration_rate` - Tasso infiltrazione
- `manning_coefficient` - Coefficiente rugosità

---

## 🎯 Pattern Architetturale Comune

Tutti i tool seguono questo pattern:

### **1. Definizione Schema**
```python
class ToolArgs(BaseModel):
    """Pydantic model per args validati da LLM"""
    bbox: str = Field(description="Geographic bounding box")
    time_range: str = Field(description="ISO date range")
    # ...
```

### **2. Validation Rules**
```python
def _set_args_validation_rules(self) -> Dict[str, List[Callable]]:
    """Lista validators per ogni argomento"""
    return {
        "arg_name": [validator1, validator2, ...],
    }

# Signature validator
def validator_X(**kwargs) -> Optional[str]:
    """Ritorna None se valido, stringa errore se invalido"""
    if error_condition:
        return "Errore: descrizione"
    return None
```

### **3. Inferenza Rules** (opzionale, con graph_state)
```python
def _set_args_inference_rules(self) -> Dict[str, Callable]:
    """Mapping: arg_name → inferrer_function"""
    return {
        "arg_name": infer_arg_function,
    }

# Signature inferrer (riceve _graph_state)
def infer_arg_function(**kwargs) -> Any:
    """Estrae graph_state per contesto, ritorna valore inferito"""
    state = kwargs.pop('_graph_state', None)  # Estrai stato
    # Usa state per contesto-aware inference
    return valore_inferito
```

### **4. Flusso Confirmation → Executor (Inferenza PRIMA Validazione)**
```python
# InvocationConfirm node (safercast_agent.py)
def _validate_tool_calls(self, tool_calls, state):
    for tool_call in tool_calls:
        tool_args = tool_call["args"]
        tool = ToolRegistry().get(tool_call["name"])
        
        # STEP 1: Apply INFERENCE first
        # Aggiungi graph_state ai kwargs
        tool_args_with_state = {**tool_args, '_graph_state': state}
        
        # Chiama inferrer per ogni arg incompleto
        for arg_name, inferrer_fn in tool._set_args_inference_rules().items():
            if arg_name not in tool_args or tool_args[arg_name] is None:
                tool_args[arg_name] = inferrer_fn(**tool_args_with_state)
        
        # STEP 2: Validate COMPLETE args
        validation_errors = self._validate_args(tool, tool_args)
        if validation_errors:
            # Handle error
            return validation_errors

# Executor node (safercast_agent.py)
def _execute_tool_call(self, tool_call, state):
    tool_args = tool_call["args"]  # Already inferred + validated
    tool = ToolRegistry().get(tool_call["name"])
    
    # Esegui direttamente, args sono già pronti
    result = tool._execute(**tool_args)
    return result
```

### **5. Esecuzione**
```python
def _run(self, **validated_kwargs) -> dict:
    """Business logic del tool - args già validati!"""
    # API call / Processing / Storage
    return result
```

---

## 🔧 Workflow Completo (Tool Lifecycle)

```
1. AGENT (safercast_agent / models_agent):
   ├─ Invoca LLM con tools + system context
   ├─ LLM genera tool_calls (args possono essere incompleti/parziali)
   └─ Ritorna AIMessage con tool_calls

2. INVOCATION_CONFIRM node:
   ├─ Accede a tool_calls da state['*_invocation']
   ├─ Per ogni tool_call:
   │  ├─ Step A: INFERENCE (applica _set_args_inference_rules)
   │  │         → Passa _graph_state in kwargs | {'_graph_state': state}
   │  │         → Inferrer estrae state: state = kwargs.pop('_graph_state')
   │  │         → Riempie args mancanti con contesto-aware defaults
   │  │         → Modifica tool_call["args"] in-place
   │  │
   │  └─ Step B: VALIDATION (applica _set_args_validation_rules)
   │           → Valida args COMPLETI
   │           → Se valido: continua
   │           → Se invalido: interrupt per user correction
   │
   ├─ Se validators OK: state[*_confirmation] = "accepted"
   ├─ Se user rejection: state[*_confirmation] = "rejected"
   │                     state[*_reinvocation_request] = user_response
   │                     → Torna a AGENT per re-invoke
   └─ Se interrupts disabilitati: auto-accept → EXECUTOR

3. EXECUTOR node:
   ├─ Accede a tool_calls da state['*_invocation']
   ├─ Per ogni tool_call (già validated + inferred):
   │  ├─ Recupera tool dalla registry
   │  ├─ Esegue: tool._execute(**tool_call["args"])
   │  │         (args già completi, niente inferenza ripetuta)
   │  ├─ Formatta: tool-specific response message
   │  ├─ Aggiorna: layer_registry (via LayersAgent)
   │  ├─ Registra: result in tool_results[step_X]
   │  └─ Marca: StateManager.mark_agent_step_complete(*_agent_type)
   │
   └─ Ritorna a SUPERVISOR_SUBGRAPH

4. SUPERVISOR_ROUTER:
   ├─ Calcola prossimo step
   └─ Inizializza nuovo agent cycle O va a FINAL_RESPONDER
```

### Sequenza Corretta: Inference → Validation → Execution

```
Tool call da LLM: {name: "meteoblue", args: {bbox: "45,7,-45,-7", variable: None}}
  ↓
CONFIRM node riceve tool_call
  ↓
INFERENCE:
  ├─ kwargs = {bbox: "45,7", variable: None, _graph_state: state}
  ├─ infer_variable(**kwargs) estrae state
  ├─ Usa parsed_request dal state per inferire variable = "precipitation"
  └─ tool_call["args"]["variable"] = "precipitation"
  ↓
VALIDATION:
  ├─ validate_bbox(**tool_args) ✓ OK
  ├─ validate_meteoblue_variable(**tool_args) ✓ OK (ora ha valore)
  └─ Prosegue a EXECUTOR
  ↓
EXECUTOR:
  ├─ tool._execute(bbox="45,7", variable="precipitation")
  └─ Niente re-inferenza, args già pronti!
```

---

## 🌐 Graph State Awareness

**Innovazione**: I tool sono **context-aware** attraverso `_graph_state`:

| Componente | Riceve graph_state | Utilizza per |
|---|---|---|
| **Inference Rules** | Sì (via kwargs) | Contesto per defaults intelligenti |
| **Validation Rules** | No (valida solo args) | Controlli strutturali/semantici |
| **Tool._execute()** | No (args già pronti) | Esecuzione business logic |
| **LayersAgent** | Sì (da Executor) | Creare layer metadata |

### Esempio: Meteoblue Inferrer con Graph State

```python
# In _inferrers.py
def infer_bucket_destination(**kwargs) -> str:
    """S3 bucket path basato su project_id dallo stato"""
    state = kwargs.pop('_graph_state', None)
    
    if state:
        project_id = state.get('project_id')
        user_id = state.get('user_id')
        return f"s3://bucket/{project_id}/{user_id}/data"
    
    return "s3://bucket/default"
```

---

## 📊 Dipendenze Identificate

```
Agent Confirm Node (safercast_agent / models_agent)
    ↓ (passa kwargs + _graph_state)
    ├─ Inference Rules (_inferrers.py)
    │  └─ Usa graph_state per contesto
    │
    └─ Validation Rules (_validators.py)
       └─ Valida args completi

Tool Registry (safercast_agent / models_agent)
    ↓
    ├─ DPCRetrieverTool
    ├─ MeteoblueRetrieverTool
    └─ SaferRainTool
       ├─ _set_args_validation_rules()
       └─ _set_args_inference_rules()

Executor Node
    ↓ (invoca tool)
    and
    ↓ (crea layers)
    LayersAgent
```

---

## ✅ Implementation Checklist

- [x] **Validation Rules**: Separati in _validators.py, ritornano Optional[str]
- [x] **Inference Rules**: Separati in _inferrers.py, ricevono _graph_state via kwargs
- [x] **Confirmation Pattern**: Inference PRIMA di Validation in Confirm node
- [x] **Graph State Passing**: _graph_state propagato a inferrer functions
- [x] **Tool-Specific Formatting**: Ogni tool ha custom response formatting
- [x] **Layer Registry Integration**: Executor aggiorna layer_registry via LayersAgent
- [x] **Result Tracking**: tool_results accumulano snapshot di ogni tool execution

---

## 🔗 File Correlation

| Logica | File |
|--------|------|
| Inference + Validation orchestration | `safercast_agent.py` → DataRetrieverInvocationConfirm._validate_tool_calls() |
| | `models_agent.py` → ModelsInvocationConfirm._validate_tool_calls() |
| Validation function implementations | `ma/specialized/tools/_validators.py` |
| Inference function implementations | `ma/specialized/tools/_inferrers.py` |
| DPC Tool | `ma/specialized/tools/dpc_retriever_tool.py` |
| Meteoblue Tool | `ma/specialized/tools/meteoblue_retriever_tool.py` |
| SaferRain Tool | `ma/specialized/tools/safer_rain_tool.py` |