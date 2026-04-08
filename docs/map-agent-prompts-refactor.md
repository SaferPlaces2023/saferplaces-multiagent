# map_agent_prompts.py — Analisi e Piano di Refactor

> Confronto strutturale con il pattern standard del progetto e proposte di allineamento.

---

## 1. Pattern standard (tutti gli altri file di prompt)

Gli altri file del progetto seguono una gerarchia coerente:

```
<Agent>Instructions
  └── <Contesto> (es. PlanGeneration, InvokeTools)
       ├── Prompts
       │    ├── _RoleAndScope.stable(state)   → Prompt  (ruolo statico)
       │    ├── _GlobalContext.stable(state)  → Prompt  (assembler del contesto dinamico)
       │    └── _TaskInstruction.stable(state)→ Prompt  (istruzione specifica)
       │
       └── Invocation / Invocations
            └── InvokeOneShot.stable(state)  → list[BaseMessage]  (assembler finale → LLM)
```

### Regole del pattern standard

| Regola | Descrizione |
|---|---|
| Un solo top-level class per file | `SupervisorInstructions`, `ModelsInstructions`, ecc. |
| `_GlobalContext` è obbligatorio | Compone i sub-prompt (layers, shapes, request, storia) |
| Helper privati dentro le classi | Nessuna funzione a livello di modulo |
| `stable()` + varianti versionabili | Supporta A/B test con `v001()`, `generic()` |
| Separazione netta Prompts/Invocation | `Prompts.*` → singoli `Prompt`; `Invocation.*` → `list[BaseMessage]` |

---

## 2. Struttura attuale di `map_agent_prompts.py`

### Inventario completo

```
map_agent_prompts.py
│
├── class MapAgentPrompts                          ← usata da TOOLS e da altri prompt files
│    ├── class ContextPrompt
│    │    └── stable() → Prompt                   ← wrap di InvokeOneShot
│    ├── class GenerateMaplibreStylePrompt
│    │    └── stable() → Prompt                   ← usata da LayerSymbologyTool
│    ├── class ExecutionContext
│    │    └── stable(state, include_shapes) → Prompt  ← assembla viewport + layers + shapes
│    ├── class GenerateShapePrompt
│    │    └── stable() → Prompt                   ← usata da CreateShapeTool
│    ├── class GenerateMoveViewPrompt
│    │    └── stable() → Prompt                   ← usata da MoveMapViewTool
│    └── _viewport_context(state) → str           ← usata da RequestParser e FinalResponder
│
├── def _format_layer_registry_summary(layer_registry) → str   ← funzione di modulo
├── def _serialize_geometry_for_context(geom) → str            ← funzione di modulo
│
└── class MapAgentInstructions                     ← usata da MapAgent.run()
     └── InvokeTools
          ├── Prompts
          │    ├── _RoleAndScope.stable(state)
          │    ├── _ExecutionContext.stable(state)
          │    └── _Request.stable(state)
          └── Invocation
               └── InvokeOneShot.stable(state) → list[BaseMessage]
```

### Mappa degli utilizzi

| Classe / Funzione | Usata da | Note |
|---|---|---|
| `MapAgentPrompts.ContextPrompt.stable()` | `MapAgentInstructions.InvokeTools.Prompts._RoleAndScope` | Indirizione inutile |
| `MapAgentPrompts.GenerateMaplibreStylePrompt.stable()` | `LayerSymbologyTool._run()` | LLM call nel tool |
| `MapAgentPrompts.ExecutionContext.stable()` | `MapAgentInstructions.InvokeTools.Prompts._ExecutionContext` | |
| `MapAgentPrompts.GenerateShapePrompt.stable()` | `CreateShapeTool._run()` | LLM call nel tool |
| `MapAgentPrompts.GenerateMoveViewPrompt.stable()` | `MoveMapViewTool._run()` | LLM call nel tool |
| `MapAgentPrompts._viewport_context(state)` | `RequestParserInstructions._GlobalContext`, `FinalResponderInstructions._GlobalContext` | Cross-file |
| `_format_layer_registry_summary()` | `ExecutionContext.stable()` (interno) | Modulo-level |
| `_serialize_geometry_for_context()` | `ExecutionContext.stable()` (interno) | Modulo-level |
| `MapAgentInstructions.InvokeTools.Invocation.InvokeOneShot.stable()` | `MapAgent.run()` | Entry point agent |

**Nessun dead code** — tutte le classi sono attivamente usate.

---

## 3. Differenze strutturali rispetto al pattern standard

### D1 — Due top-level class invece di una

Il file definisce **due classi parallele** `MapAgentPrompts` e `MapAgentInstructions` che servono scopi diversi, ma coesistono nello stesso modulo senza una gerarchia comune.

Gli altri file: un solo top-level (`SupervisorInstructions`, `ModelsInstructions`, …).

### D2 — Nessun `_GlobalContext` esplicito in `MapAgentInstructions`

In tutti gli altri agenti `_GlobalContext.stable(state)` aggrega i sub-prompt (layers, shapes, request, ecc.) e viene passato a `InvokeOneShot`.

In `MapAgentInstructions`: il ruolo di assembler è distribuito tra `_ExecutionContext` (contesto mappa) e `_Request` (goal), senza un nodo intermedio `_GlobalContext` che li unisca.

### D3 — Prompt per i tool nella stessa classe agent

`GenerateMaplibreStylePrompt`, `GenerateShapePrompt`, `GenerateMoveViewPrompt` sono prompt usati dai **tool** (con propri LLM call), non dall'agente. Condividono però lo stesso namespace `MapAgentPrompts` con `ContextPrompt` ed `ExecutionContext` che appartengono all'agente.

Negli altri file: i prompt sono sempre riferiti all'agente (o al suo contesto), i tool non hanno prompt separati in questo file.

### D4 — Helper a livello di modulo

`_format_layer_registry_summary()` e `_serialize_geometry_for_context()` sono funzioni standalone a livello di modulo Python.

Pattern standard: gli helper sono metodi privati interni alle classi che li usano.

### D5 — `ContextPrompt` è un wrapper ridondante

`MapAgentInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)` si limita a chiamare `MapAgentPrompts.ContextPrompt.stable()`, che a sua volta non fa altro che restituire un `Prompt` statico. Due livelli di indirizione per un messaggio statico.

### D6 — Nessuna variante versionata

Nessun metodo `v001()`, `generic()` o alternativo. Il pattern standard li prevede per A/B test con `unittest.mock.patch.object`.

---

## 4. Perché il file è così

L'agente MapAgent ha una caratteristica unica: **i suoi tool fanno LLM call autonome** (LayerSymbologyTool, CreateShapeTool, MoveMapViewTool chiamano ciascuno l'LLM internamente). Ogni tool porta il proprio prompt esperto.

Di conseguenza il file è diventato una **libreria di prompt per tool** (70%) più le istruzioni dell'agente (30%), mentre negli altri file è sempre l'agente il soggetto principale.

---

## 5. Piano di refactor

> Obiettivo: allineare il file al pattern standard senza cambiare il comportamento.

### 5.1 Separare i prompt dei tool in un namespace dedicato

Rinominare `MapAgentPrompts` in `MapAgentToolPrompts` per segnalare chiaramente che quei prompt appartengono ai tool, non all'agente.

```python
# Prima
class MapAgentPrompts:
    class GenerateMaplibreStylePrompt: ...
    class GenerateShapePrompt: ...
    class GenerateMoveViewPrompt: ...
    class ContextPrompt: ...
    class ExecutionContext: ...

# Dopo
class MapAgentToolPrompts:
    """Prompt usati dai tool con LLM call autonome."""
    class LayerSymbology:
        @staticmethod
        def stable() -> Prompt: ...   # era GenerateMaplibreStylePrompt

    class CreateShape:
        @staticmethod
        def stable() -> Prompt: ...   # era GenerateShapePrompt

    class MoveView:
        @staticmethod
        def stable() -> Prompt: ...   # era GenerateMoveViewPrompt
```

I tre tool devono essere aggiornati di conseguenza:
- `LayerSymbologyTool` → `MapAgentToolPrompts.LayerSymbology.stable()`
- `CreateShapeTool` → `MapAgentToolPrompts.CreateShape.stable()`
- `MoveMapViewTool` → `MapAgentToolPrompts.MoveView.stable()`

### 5.2 Aggiungere `_GlobalContext` in `MapAgentInstructions`

Introdurre il nodo di assemblaggio mancante:

```python
class MapAgentInstructions:
    class InvokeTools:
        class Prompts:
            class _RoleAndScope:
                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    # contenuto attuale di MapAgentPrompts.ContextPrompt
                    ...

            class _GlobalContext:
                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    # assembla: ExecutionContext + Request
                    exec_ctx = MapAgentInstructions.InvokeTools.Prompts._ExecutionContext.stable(state)
                    request = MapAgentInstructions.InvokeTools.Prompts._Request.stable(state)
                    message = f"{exec_ctx.message}\n\n[REQUEST]\n{request.message}"
                    return Prompt({"title": "MapAgentGlobalContext", "message": message, ...})

            class _ExecutionContext: ...  # come ora
            class _Request: ...          # come ora

        class Invocation:
            class InvokeOneShot:
                @staticmethod
                def stable(state: MABaseGraphState) -> list:
                    role = MapAgentInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
                    ctx = MapAgentInstructions.InvokeTools.Prompts._GlobalContext.stable(state)
                    return [SystemMessage(content=role.message), HumanMessage(content=ctx.message)]
```

### 5.3 Internalizzare gli helper di modulo

Spostare `_format_layer_registry_summary` e `_serialize_geometry_for_context` come metodi privati statici di `_ExecutionContext` (o di `MapAgentInstructions.InvokeTools.Prompts._ExecutionContext`):

```python
class _ExecutionContext:
    @staticmethod
    def stable(state, *, include_shapes=True) -> Prompt:
        ...
        parts.append(_ExecutionContext._format_layer_registry_summary(layer_registry))
        ...

    @staticmethod
    def _format_layer_registry_summary(layer_registry: list) -> str: ...

    @staticmethod
    def _serialize_geometry_for_context(geom: dict) -> str: ...
```

### 5.4 Eliminare `ContextPrompt` come classe separata

`ContextPrompt.stable()` è wrapper di un solo Prompt statico chiamato da `_RoleAndScope`.  
Il messaggio può essere scritto direttamente in `_RoleAndScope.stable()`, eliminando la doppia indirizione.

### 5.5 Esporre `_viewport_context` come metodo di `_ExecutionContext`

`_viewport_context` è oggi una funzione del namespace `MapAgentPrompts` usata da altri file di prompt. Dopo il rinomino di `MapAgentPrompts`, i file che la usano devono importarla dal nuovo percorso. Una possibilità alternativa è spostarla in `common/context_builder.py` dato che è cross-agente.

### 5.6 Aggiungere varianti versionabili (opzionale)

Per abilitare A/B test sui prompt dei tool:

```python
class MapAgentToolPrompts:
    class LayerSymbology:
        @staticmethod
        def stable() -> Prompt: ...
        @staticmethod
        def v001() -> Prompt: ...   # variante alternativa
```

---

## 6. Tabella riepilogativa — prima/dopo

| Aspetto | Attuale | Dopo refactor |
|---|---|---|
| Top-level classes | `MapAgentPrompts` + `MapAgentInstructions` | `MapAgentToolPrompts` + `MapAgentInstructions` |
| Prompt per tool | In `MapAgentPrompts` (misto agente+tool) | In `MapAgentToolPrompts` (namespace dedicato) |
| Assembler del contesto | Assente (distribuito su _ExecutionContext + _Request) | `_GlobalContext.stable(state)` esplicito |
| Helper functions | Modulo-level (`_format_...`, `_serialize_...`) | Metodi privati dentro `_ExecutionContext` |
| `ContextPrompt` | Wrapper ridondante | Rimosso, inlineato in `_RoleAndScope` |
| Versioning | Solo `stable()` | `stable()` + `v001()` (almeno per LayerSymbology) |
| Compatibilità import | — | Aggiornare 3 tool + 2 prompt files per rinomino |

---

## 7. File da modificare nel refactor

| File | Modifica |
|---|---|
| `ma/prompts/map_agent_prompts.py` | Ristrutturazione completa (vedi §5) |
| `ma/specialized/tools/layer_symbology_tool.py` | Import: `MapAgentToolPrompts.LayerSymbology` |
| `ma/specialized/tools/create_shape_tool.py` | Import: `MapAgentToolPrompts.CreateShape` |
| `ma/specialized/tools/move_map_view_tool.py` | Import: `MapAgentToolPrompts.MoveView` |
| `ma/prompts/request_parser_prompts.py` | Aggiornare import di `_viewport_context` |
| `ma/prompts/final_responder_prompts.py` | Aggiornare import di `_viewport_context` |
