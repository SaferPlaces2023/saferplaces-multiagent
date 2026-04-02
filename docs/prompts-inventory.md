# Prompt Inventory — SaferPlaces Multiagent

Inventario organizzato per **consumer**: per ogni nodo/agente/tool, quali messaggi usa, chi li produce e da dove vengono.

Legenda colonne:
- **Tipo** — `System` / `Human` / `AI` / `Tool` / `interrupt` / `zero-shot label`
- **Prodotto da** — file e classe/funzione che generano la stringa
- **Costruzione** — `static` (stringa fissa), `f-string` (runtime), `dispatch` (selezione tra varianti a runtime)
- **Versionato** — se esiste il pattern `stable/v001` nel modulo `ma/prompts/`

---

## 1. `RequestParser` — `ma/chat/request_parser.py`

Analizza il messaggio utente e produce un `ParsedRequest` strutturato.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo e schema output JSON del parser (intent, entities, parameters, implicit_requirements…); contiene layer/shapes correnti del progetto | System | `RequestParserPrompts.MainContext.stable(layer_summary, shapes_summary)` — `request_parser_prompts.py` | f-string parametrizzata; `layer_summary` e `shapes_summary` buildate in `RequestParser._summarize_layers/_summarize_shapes` | Sì (`v001` senza layer/shapes injection) |

---

## 2. `SupervisorAgent` — `ma/orchestrator/supervisor.py`

Genera l`ExecutionPlan` (lista di step agent+goal).

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Dominio di pianificazione: 6 aree operative, 3 agenti disponibili, regole di ragionamento, errori comuni, formato output, few-shot examples | System | `OrchestratorPrompts.MainContext.stable()` — `supervisor_agent_prompts.py` | static | Sì (`v001` minimale) |
| Payload generazione iniziale piano: `parsed_request`, layer disponibili, history conversazione | Human | `OrchestratorPrompts.Plan.CreatePlan.stable(state)` — `supervisor_agent_prompts.py` | f-string da state | Sì |
| Payload replanning incrementale (`plan_confirmation == "modify"`): piano corrente + feedback utente | Human | `OrchestratorPrompts.Plan.IncrementalReplanning.stable(state)` — `supervisor_agent_prompts.py` | f-string da state | Sì |
| Payload replanning totale (`plan_confirmation == "rejected"`): piano rifiutato + feedback | Human | `OrchestratorPrompts.Plan.TotalReplanning.stable(state)` — `supervisor_agent_prompts.py` | f-string da state | Sì |

---

## 3. `SupervisorPlannerConfirm` — `ma/orchestrator/supervisor.py`

Nodo human-in-the-loop per approvazione del piano; gestisce anche le domande di spiegazione.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo del plan-explainer: assistente che spiega piani di esecuzione | System | `OrchestratorPrompts.Plan.PlanExplanation.ExplainerMainContext.stable()` — `supervisor_agent_prompts.py` | static | Sì |
| Risposta a una domanda dell'utente su un passo del piano (inietta piano + parsed_request) | Human | `OrchestratorPrompts.Plan.PlanExplanation.RequestExplanation.stable(state, user_question)` — `supervisor_agent_prompts.py` | f-string da state + domanda | Sì |
| Testo interrupt di fallback in caso di eccezione durante la spiegazione | interrupt | `"I apologize, I couldn't generate the explanation. Please accept or reject the plan."` — inline in `_generate_plan_explanation` except | static inline | No |

---

## 4. `SupervisorRouter` — `ma/orchestrator/supervisor.py`

Instrada il grafo verso il subgraph corretto e aggiorna l`ExecutionNarrative`.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Goal per `LayersAgent` (context refresh tra passi): recupera layer rilevanti per la richiesta | Human -> `state["layers_request"]` | `f"User has this request:\n{parsed_request}\nRetrieve the relevant layers from available layers."` — inline in `_build_layers_request` | f-string inline | No |
| Output summary del passo completato, iniettato in `ExecutionNarrative.StepResult` | narrative | `f"Step completato: {agent_name}"` — inline in `_update_execution_narrative` | f-string inline | No |

---

## 5. `ModelsAgent` / `ModelsExecutor` — `ma/specialized/models_agent.py`

Seleziona e invoca i tool di simulazione (SaferRain, DigitalTwin, SaferBuildings, SaferFire).

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo agente simulazione: dettaglio 4 tool, catalogue 28 layer DigitalTwin, errori comuni | System | `ModelsPrompts.MainContext.stable()` — `models_agent_prompts.py` | static | Sì (`v001` minimale) |
| Payload selezione iniziale tool: goal passo, `parsed_request`, `relevant_layers`, history (ultimi 8 msg non-tool) | Human | `ModelsPrompts.ToolSelection.InitialRequest.stable(state)` — `models_agent_prompts.py` | f-string da state | Sì |
| Payload re-invocation (`models_invocation_confirmation == "rejected"`): tool call list + feedback | Human | `ModelsPrompts.ToolSelection.ReinvocationRequest.stable(state)` — `models_agent_prompts.py` | f-string da state | Sì |
| Goal per `LayersAgent` post-simulazione: registra layer prodotti con istruzioni metadati | Human -> `state["layers_request"]` | multiline f-string inline in `ModelsExecutor._add_layer_to_registry` | f-string inline | No |

### ToolMessage content (history LLM dopo ogni tool call)

| Messaggio | Tool | Prodotto da | Costruzione |
|---|---|---|---|
| `"Flood simulation completed successfully\nModel: SaferRain\nDEM: …"` | safer_rain | `ModelsExecutor._format_safer_rain_response` — inline | multiline f-string |
| `"SaferBuildings analysis completed successfully\n…"` | safer_buildings | `ModelsExecutor._format_safer_buildings_response` — inline | multiline f-string |
| `"SaferFire simulation completed successfully\n…"` | safer_fire | `ModelsExecutor._format_safer_fire_response` — inline | multiline f-string |
| `"Tool '{tool_name}' executed successfully\nArguments: …\nResult: …"` | qualsiasi altro | `ModelsExecutor._format_generic_response` — inline | f-string |

### Stringhe di narrativa (accumulate in `ExecutionNarrative`, lette dal FinalResponder)

| Stringa | Prodotto da | Trigger |
|---|---|---|
| `"SaferRain: Water depth raster creato…"` / `"DigitalTwin: Environment created for…"` / ecc. | `_execute_tool_call` — `output_desc` inline f-string per ogni tool | Dopo ogni esecuzione |
| `f"Verifica i parametri del tool {tool_name}"` (`recovery_suggestion`) | `_execute_tool_call` — inline | In caso di errore |

---

## 6. `DataRetrieverAgent` / `DataRetrieverExecutor` — `ma/specialized/safercast_agent.py`

Seleziona e invoca i tool di retrieval dati (DPC, Meteoblue).

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo agente retrieval: DPC (Italia, ≤7 gg) e Meteoblue (globale, ≤14 gg), guida scelta tool, errori comuni | System | `SaferCastPrompts.MainContext.stable()` — `safercast_agent_prompts.py` | static | Sì (`v001` minimale) |
| Payload selezione iniziale tool: goal, `parsed_request`, `relevant_layers`, history conversazione | Human | `SaferCastPrompts.ToolSelection.InitialRequest.stable(state)` — `safercast_agent_prompts.py` | f-string da state | Sì |
| Payload re-invocation (`retriever_invocation_confirmation == "rejected"`): tool call list + feedback | Human | `SaferCastPrompts.ToolSelection.ReinvocationRequest.stable(state)` — `safercast_agent_prompts.py` | f-string da state | Sì |
| Goal per `LayersAgent` post-retrieval: registra layer recuperati | Human -> `state["layers_request"]` | multiline f-string inline in `DataRetrieverExecutor._add_layer_to_registry` | f-string inline | No |

### ToolMessage content

| Messaggio | Tool | Prodotto da | Costruzione |
|---|---|---|---|
| `"DPC data retrieved successfully\nProduct:…\nURI:…"` | dpc_retriever | `DataRetrieverExecutor._format_dpc_response` — inline | multiline f-string |
| `"Meteoblue forecast retrieved successfully\nVariable:…\nURIs:…"` | meteoblue_retriever | `DataRetrieverExecutor._format_meteoblue_response` — inline | multiline f-string |
| `"Tool '{tool_name}' executed successfully\n…"` (duplicato di MO) | qualsiasi altro | `DataRetrieverExecutor._format_generic_response` — inline | f-string |

### Stringhe di narrativa

| Stringa | Prodotto da |
|---|---|
| `f"Verifica i parametri del tool {tool_name}"` | `_execute_tool_call` — inline |

---

## 7. `MapAgent` — `ma/specialized/map_agent.py`

Gestisce styling layer e registrazione shapes sulla mappa.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo MapAgent: capabilities `set_layer_style` e `register_shape`, regole layer_id, no simulazioni | System | `MapAgentPrompts.ContextPrompt.stable()` — `map_agent_prompts.py` | static | Sì |
| Snapshot mappa (viewport, layer registry, shapes registry) + goal `map_request` | Human | `MapAgentPrompts.ExecutionContext.stable(state, include_shapes)` — `map_agent_prompts.py` + goal appeso inline con `f"\nGoal: {state.get('map_request')}"` in `MapAgent.run` | f-string da state + append inline | Sì (solo ExecutionContext) |

---

## 8. `LayersAgent` — `ma/specialized/layers_agent.py`

Gestisce il layer registry. Invocato da SupervisorRouter, ModelsExecutor, DataRetrieverExecutor, StateProcessor.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo agente layer management | System | `"You are a specialized agent for managing geospatial layers. Use the available tools to accomplish the goal."` — inline in `LayersAgent.run` | static inline (NO modulo prompts) | No |
| Goal corrente | Human | `f"Goal: {state['layers_request']}"` — inline in `LayersAgent.run` | f-string inline | No |

`state["layers_request"]` e' prodotto da:

| Produttore | Contesto |
|---|---|
| `SupervisorRouter._build_layers_request` | Context refresh tra passi del piano |
| `ModelsExecutor._add_layer_to_registry` | Registrazione layer dopo simulazione |
| `DataRetrieverExecutor._add_layer_to_registry` | Registrazione layer dopo retrieval |
| `StateProcessor.run` | Registrazione shapes disegnate dall'utente |

### Tool descriptions (function-calling payload interno al LayersAgent)

| Tool | Descrizione | Sorgente |
|---|---|---|
| `ListLayersTool` | `"List all layers in the registry"` | class attribute inline |
| `GetLayerTool` | `"Get a specific layer by title"` | class attribute inline |
| `AddLayerTool` | `"Add a new layer to the registry"` | class attribute inline |
| `ChooseLayerTool` | prompt per selezione layer per titolo da lista | `Prompts.choose_layer(layers_description, request)` — in-file class |
| `BuildLayerFromPromptTool` | prompt per inferenza metadati layer da URI + NL | `Prompts.build_layer_from_prompt(src, prompt, …)` — in-file class |

---

## 9. `FinalResponder` — `ma/chat/final_responder.py`

Sintetizza la risposta finale all'utente dopo l'esecuzione del piano.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Framing risposta — variante selezionata in base all'esito (4 opzioni) | System | `FinalResponderPrompts.Response.stable(state)` — `final_responder_prompts.py`, dispatcher `_select_variant` | dispatch su static | Sì (`v001` minimale) |
| → piano completato: riassumi risultati, menziona nuovi layer, suggerisci next steps | System | `_completed_plan()` | static | |
| → query informativa (nessun piano): rispondi su capacita'/layer/shapes | System | `_info_response()` | static | |
| → piano annullato: riconosci cancellazione, riassumi risultati parziali | System | `_aborted_plan()` | static | |
| → errore: spiega errori in linguaggio semplice, suggerisci recovery | System | `_error_response()` | static | |
| Contesto dati: execution summary, `layer_registry`, `shapes_registry` | Human | `FinalResponderPrompts.Context.Formatted.stable(state)` — `final_responder_prompts.py`; usa `ExecutionNarrative` se presente, altrimenti fallback raw | f-string da state | Sì |

`_BASE_RULES` (stringa statica in `final_responder_prompts.py`): "rispondi nella lingua dell'utente, non inventare, sii conciso" — concatenata a ognuna delle 4 varianti System.

---

## 10. `LayerSymbologyTool` — `ma/specialized/tools/layer_symbology_tool.py`

Tool del MapAgent per generare stili MapLibre GL JS via LLM.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Expert prompt MapLibre GL JS: espressioni, paint properties per fill/line/circle/symbol/raster, guida scelta tipo espressione per dtype. Output JSON puro | System | `MapAgentPrompts.GenerateMaplibreStylePrompt.stable()` — `map_agent_prompts.py` | static | Sì |
| Retry JSON non valido: `"Your previous response was not valid JSON. Reply ONLY with a valid JSON object…"` | Human | f-string inline in `LayerSymbologyTool._run` retry path | f-string inline | No |

---

## 11. `ToolInvocationConfirmationHandler` — `ma/specialized/confirmation_utils.py`

Gestisce il ciclo di approvazione human-in-the-loop delle tool call.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Ruolo LLM per generare la spiegazione delle tool call | System | `"You are a helpful assistant explaining tool invocations."` — inline in `_generate_tool_call_explanation` | static inline | No |
| Payload per LLM: spiega le tool call proposte all'utente | Human | f-string inline in `_generate_tool_call_explanation` | f-string inline | No |
| Testo interrupt: `"{explanation}\n\nDo you want to proceed with these tool calls?"` | interrupt | f-string inline in `_handle_clarify` | f-string inline | No |
| Label descriptions per zero-shot classification risposta utente (approve / reject / modify / explain) | zero-shot label | `INVOCATION_RESPONSE_LABELS` dict — module-level `confirmation_utils.py` | static dict | No |

---

## 12. `ToolValidationResponseHandler` — `ma/specialized/validation_utils.py`

Gestisce errori di validazione dei parametri tool (auto-correct o human-in-the-loop).

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Re-invocation automatico: `"Please automatically correct these validation errors:\n{error_summary}\n\nUse reasonable defaults…"` | Human | f-string inline in `_handle_validation_auto_correct` | f-string inline | No |
| Ruolo LLM per spiegare i requisiti di validazione | System | `"You are a helpful assistant explaining validation requirements."` — inline in `_generate_validation_explanation` | static inline | No |
| Payload per LLM: spiega gli errori di validazione all'utente | Human | f-string inline in `_generate_validation_explanation` | f-string inline | No |
| Testo interrupt: `"{explanation}\n\nHow would you like to proceed?"` | interrupt | f-string inline in `_handle_clarify_requirements` | f-string inline | No |
| Label descriptions per zero-shot classification risposta utente (auto-correct / clarify / reject) | zero-shot label | `VALIDATION_RESPONSE_LABELS` dict — module-level `validation_utils.py` | static dict | No |

### Messaggi di errore di validazione (`_validators.py` -> `format_validation_errors()` -> handler sopra)

| Validator | Messaggio | File |
|---|---|---|
| `value_in_list` | `"Invalid {field} '{value}'. {label}: …"` | `_validators.py` |
| `bbox_inside` | `"Bounding box {bbox} exceeds reference {reference}"` | `_validators.py` |
| `time_within_days` | `"{field_label} {time_str} too old. Data available for last {days} days only"` | `_validators.py` |
| `time_before` | `"{field_label} {time_str} must be before {other_label} {other_time_str}"` | `_validators.py` |
| `time_before_datetime` | `"{field_label} {time_str} cannot be after {label}"` | `_validators.py` |
| `time_after` | `"{field_label} {time_str} must be after {other_label} {other_time_str}"` | `_validators.py` |
| `time_after_datetime` | `"{field_label} {time_str} must be after {label} {reference_date}"` | `_validators.py` |

---

## 13. `StateProcessor` — `ma/chat/state_processor.py`

Prepara lo stato per il passo successivo; avvia la registrazione automatica delle shapes disegnate.

| Messaggio | Tipo | Prodotto da | Costruzione | Versionato |
|---|---|---|---|---|
| Goal per `LayersAgent`: `"Register the following newly drawn shapes…: {ids}. Call register_shape once for each collection_id."` | Human -> `state["layers_request"]` | multiline f-string inline in `StateProcessor.run` | f-string inline | No |

---

## 14. Tool schemas — function-calling payload

Questi testi vengono iniettati automaticamente da LangChain nel function-calling prompt di ogni chiamata LLM.

| Tool | Cosa viene iniettato | Sorgente nel codice | Consumer (agente) |
|---|---|---|---|
| `digital_twin` | Descrizione tool + ogni argomento (bbox, layers, pixelsize, out_format, region_name, clip_geometry, dem_reference…) | `DigitalTwinInputSchema` docstring + `Field(description=…)` — `digital_twin_tool.py` | `ModelsAgent` |
| `safer_rain` | Descrizione + argomenti (dem, rain, water, band, to_band, t_srs, mode, debug) | `SaferRainInputSchema` docstring + `Field(description=…)` — `safer_rain_tool.py` | `ModelsAgent` |
| `saferbuildings_tool` | Descrizione + argomenti (water, buildings, provider, bbox, t_srs, wd_thresh, flood_mode, only_flood, stats…) | `SaferBuildingsInputSchema` docstring + `Field(description=…)` — `safer_buildings_tool.py` | `ModelsAgent` |
| `safer_fire_tool` | Descrizione + argomenti (dem, ignitions, wind_speed, wind_direction, bbox, landuse, landuse_provider, start_datetime, time_max…) | `SaferFireInputSchema` docstring + `Field(description=…)` — `safer_fire_tool.py` | `ModelsAgent` |
| `dpc_retriever` | Descrizione + argomenti (product, bbox, time_start, time_end, out, out_format, bucket_destination, debug) | `DPCRetrieverSchema` docstring + `Field(description=…)` — `dpc_retriever_tool.py` | `DataRetrieverAgent` |
| `meteoblue_retriever` | Descrizione + argomenti (variable, bbox, time_start, time_end, out, out_format, bucket_source, bucket_destination, debug) | `MeteoblueRetrieverSchema` docstring + `Field(description=…)` — `meteoblue_retriever_tool.py` | `DataRetrieverAgent` |
| `set_layer_style` | `Tool.description` class attribute | `layer_symbology_tool.py` | `MapAgent` |
| `register_shape` | `Tool.description` class attribute | `register_shape_tool.py` | `MapAgent` |

### `short_description` ClassVar — anche nel planning prompt

Ogni tool ha un `short_description: ClassVar[str]` incluso nel dict `*_AGENT_DESCRIPTION` del proprio agente. Questo dict viene iniettato in `OrchestratorPrompts.MainContext` al costruzione del grafo, quindi arriva al planning LLM del `SupervisorAgent`.

| Tool | Dict di destinazione | File |
|---|---|---|
| `digital_twin`, `safer_rain`, `safer_buildings`, `safer_fire` | `MODELS_AGENT_DESCRIPTION` | `models_agent.py` |
| `dpc_retriever`, `meteoblue_retriever` | `SAFERCAST_AGENT_DESCRIPTION` | `safercast_agent.py` |

---

## Note trasversali

### Pattern `stable / v001` (solo modulo `ma/prompts/`)
Ogni classe prompt versionata ha `stable()` (production) e `v001()` (versione precedente pinned per test). La selezione e' manuale nei call site; nessun A/B switching runtime.

### Flusso delle stringhe inline verso il FinalResponder
Le stringhe narrative inline (MO-3/4, SC-3, SV-3) non vanno direttamente all'LLM: vengono accumulate in `ExecutionNarrative` e lette da `FinalResponderPrompts.Context.Formatted` solo alla fine.

### `state["layers_request"]` come canale di accoppiamento
Quattro componenti diversi scrivono su questa stessa chiave di stato per guidare `LayersAgent`. Tutti usano f-string inline; nessuno usa il modulo `ma/prompts/`.
