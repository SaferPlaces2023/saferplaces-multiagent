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
    if "X" in kwargs and kwargs["X"]:
        return kwargs["X"]
    
    # Logica di inferenza basata su altri argomenti
    return valore_inferito
```

**Inferrers probabili**:
- `infer_bbox_from_location()` - Bbox da nome località
- `infer_time_range_from_now()` - Range temporale default (es. prossime 24h)
- `infer_variable_from_goal()` - Variabile meteo da goal testuale
- `infer_resolution_from_bbox()` - Risoluzione appropriata per area

**Uso**: Permettono al LLM di non specificare tutti gli argomenti, rendendo l'invocazione più flessibile.

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
    """Pydantic model per args"""
    bbox: str = Field(description="...")
    time_range: str = Field(description="...")
    # ...
```

### **2. Validation Rules**
```python
def _set_args_validation_rules(self) -> Dict[str, List[Callable]]:
    """Lista validators per ogni argomento"""
    return {
        "arg_name": [validator1, validator2, ...],
    }
```

### **3. Inferenza (opzionale)**
```python
def _apply_inferrers(self, kwargs: Dict) -> Dict:
    """Applica inferrers per riempire valori mancanti"""
    kwargs["bbox"] = infer_bbox_from_location(**kwargs)
    return kwargs
```

### **4. Esecuzione**
```python
def _run(self, **validated_kwargs) -> dict:
    """Business logic del tool"""
    # API call / Processing / Storage
    return result

def _execute(self, **kwargs) -> Any:
    """Wrapper pubblico con inferenza"""
    kwargs = self._apply_inferrers(kwargs)
    return self._run(**kwargs)
```

---

## 🔧 Workflow Completo

```
1. Agent invoca LLM con tools
   ↓
2. LLM genera tool_calls
   ↓
3. InvocationConfirm valida con _validators
   ↓
   ├─ Invalido → User interrupt → Re-invoke
   └─ Valido → Executor
              ↓
4. Executor chiama tool._execute()
   ↓
5. Tool applica _inferrers per args mancanti
   ↓
6. Tool._run() esegue logica business
   ↓
7. Ritorna layer + metadata
   ↓
8. Stato aggiornato (layer_registry, tool_results)
```

---

## 📊 Dipendenze Identificate

```
Tool Base (LangChain BaseTool)
    ↓
┌───────────┴───────────┐
↓                       ↓
_validators.py      _inferrers.py
    ↓                   ↓
    └───────┬───────────┘
            ↓
    ┌───────┴────────┬──────────────┐
    ↓                ↓              ↓
DPCRetrieverTool  MeteoblueRT  SaferRainTool
```

---

## ✅ Prossimi Step per Refactoring

Ti suggerisco di procedere con:

1. **`_validators.py`** - Pulire e documentare validators
2. **`_inferrers.py`** - Pulire e documentare inferrers
3. **`dpc_retriever_tool.py`** - Refactoring tool DPC
4. **`meteoblue_retriever_tool.py`** - Refactoring tool Meteoblue
5. **`safer_rain_tool.py`** - Refactoring tool Safer Rain

Vuoi che inizi con **`_validators.py`**?