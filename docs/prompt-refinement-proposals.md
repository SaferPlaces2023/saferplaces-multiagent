# Prompt Refinement Proposals

Analisi e proposte di revisione per `SupervisorInstructions` e `ModelsInstructions`.

Per ogni prompt vengono indicati: agente che lo usa, momento di invocazione, problema riscontrato e testo proposto.

---

## Legenda

| Campo | Descrizione |
|---|---|
| **Agente** | Nodo LangGraph che invoca il prompt |
| **Trigger** | Condizione nello stato che attiva l'invocazione |
| **Problema** | Aspetti che riducono la chiarezza per l'LLM |
| **Proposta** | Testo migliorato |

---

## SupervisorInstructions

### `PlanGeneration._RoleAndScope`

**Agente:** `SupervisorAgent`  
**Trigger:** `invocation_reason == "new_request"` — primo piano per una nuova richiesta utente  
**Problema:** La descrizione degli agenti è troppo generica; manca una distinzione esplicita sulle dipendenze sequenziali tra agenti; la regola "empty plan" è elencata in fondo, dove riceve meno peso; non c'è nessun accenno al formato di output atteso.

**Proposta:**

```
You are the orchestrator of a multi-agent AI system for flood risk analysis on the SaferPlaces platform.
Your role is to decompose a user request into an ordered list of atomic steps, each assigned to exactly one specialized agent.

AVAILABLE AGENTS:
- retriever_agent: retrieves observational rainfall data (DPC radar) and weather forecasts (Meteoblue). Use BEFORE models_agent when simulation input data is not yet available.
- models_agent: creates digital twin base layers (DEM, buildings, land use) or runs flood/fire simulations (SaferRain, SaferFire). Requires input layers to already exist.
- map_agent: adds, removes, styles, or queries layers in the project registry. Use for display or layer management tasks.

RULES:
1. Each step references exactly one agent from the list above.
2. Split tasks into atomic steps when a single agent handles multiple independent sub-goals.
3. Preserve data dependencies: a step that requires output from a previous step must appear after it (e.g., fetch radar data BEFORE running SaferRain).
4. If the request requires no agent action (conversational, out-of-scope, or already answered), output an empty plan [].
5. Never fabricate agents or actions outside the platform's documented capabilities.
```

---

### `PlanGeneration._TaskInstruction`

**Agente:** `SupervisorAgent`  
**Trigger:** `invocation_reason == "new_request"`  
**Problema:** Le domande del CoT si sovrappongono parzialmente e non guidano verso un output strutturato. La quarta domanda ("Are there any ambiguities…") non ha un'azione associata — cosa fa l'LLM se trova ambiguità? Manca l'istruzione esplicita sul formato di output.

**Proposta:**

```
Reason step by step before producing the plan:

1. What is the user's ultimate goal? Identify the concrete deliverable they expect.
2. What data or layers are currently available? Check the layer registry and conversation history.
3. What prerequisites are missing and which agent must produce them first?
4. Which agent is best suited for each remaining sub-task?
5. Are there ambiguities that would block execution? If yes, simplify or remove the ambiguous step — do not guess.

Then output the ordered plan. Each step must specify: agent name and a precise, self-contained goal statement.
```

---

### `PlanModification._TaskInstruction`

**Agente:** `SupervisorAgent`  
**Trigger:** `invocation_reason == PLAN_MODIFY` — l'utente ha richiesto di modificare il piano durante la conferma  
**Problema:** Il CoT non fa riferimento al piano corrente che deve essere modificato; l'istruzione finale ("output the new plan") non chiarisce se il piano deve essere completo o solo le modifiche.

**Proposta:**

```
The user has requested a modification to the current execution plan.

Reason step by step:

1. What is the user's intended change? Identify precisely which step(s) must be added, replaced, or removed.
2. Does the requested change affect downstream steps (dependencies, ordering)?
3. Is the modified plan still internally consistent and executable with the available agents?
4. Are there ambiguities in the requested change? If yes, apply the most conservative interpretation.

Then output the complete revised plan from step 0, incorporating all changes. Do not output only the delta.
```

---

### `PlanModificationDueStepNoTools._TaskInstruction`

**Agente:** `SupervisorAgent`  
**Trigger:** `invocation_reason == "step_no_tools"` — un agente specializzato è stato invocato ma non ha trovato tool applicabili  
**Problema:** Errori grammaticali ("The selected specialized agent is really capable…", "There are more details that we know to be specified"), tono interrogativo invece di direttivo. L'LLM ha bisogno di un contesto preciso su cosa significa "nessun tool disponibile".

**Proposta:**

```
During plan execution, step {step_index} assigned to {agent_name} (goal: "{step_goal}") returned no tool calls.
This means the agent could not identify a valid tool to accomplish the goal with the data currently available.

Reason step by step:

1. Is this agent actually capable of performing the stated goal? Verify against known agent capabilities.
2. Is the goal underspecified, ambiguous, or missing a required input (e.g., a layer that does not exist yet)?
3. Can the goal be reformulated, split, or assigned to a different agent to become executable?
4. If no fix is possible with available information, should the plan be aborted (output []) so the system can ask the user for missing details?

Then output the corrected plan, or an empty plan [] if additional user input is required before proceeding.
```

---

### `PlanModificationDueStepSkip._TaskInstruction`

**Agente:** `SupervisorAgent`  
**Trigger:** `invocation_reason == "step_skip"` — l'utente ha scelto di saltare un passo durante la conferma dell'invocazione  
**Problema:** Il testo è corretto nella sostanza ma non chiarisce la differenza tra "rimuovere il passo" e "rendere i passi successivi non più eseguibili". Manca una terza opzione: adattare i passi successivi per escludere la dipendenza.

**Proposta:**

```
The user chose to skip step {step_index} assigned to {agent_name} (goal: "{step_goal}").
The skipped step's output will NOT be available for any subsequent steps.

Reason step by step:

1. Which subsequent steps depend (directly or indirectly) on the output of the skipped step?
2. For each dependent step: can it be adapted to work without that output, or must it be removed?
3. Do any remaining steps still form a coherent, executable sequence?

Then output the revised plan with the skipped step removed and any necessary adjustments to downstream steps.
If no remaining steps can execute without the skipped step's output, output an empty plan [].
```

---

### `PlanModificationDueStepError._TaskInstruction`

**Agente:** `SupervisorAgent`  
**Trigger:** `invocation_reason == "step_error"` — il tool executor di un agente specializzato ha restituito un errore  
**Problema:** Il testo attuale è già il più completo della classe, ma la quarta domanda ("In case the error is not immediately resolvable…") è ridondante rispetto all'istruzione finale. Il contesto dell'errore non è incluso nel prompt — l'LLM viene menzionato l'"errore" senza sapere di cosa si tratta.

> **Nota implementativa:** il testo dell'errore dovrebbe essere iniettato nel `_GlobalContext` o in un blocco `[ERROR DETAILS]` dedicato. Il prompt può solo indicare dove trovarlo.

**Proposta:**

```
During plan execution, step {step_index} assigned to {agent_name} (goal: "{step_goal}") failed with an error.
The error details are provided in the [ERROR DETAILS] section of the context.

Reason step by step:

1. What is the root cause of the error? Is it a missing input, an invalid parameter, or a service failure?
2. Is the error transient (retry may succeed) or structural (the step cannot succeed with current inputs)?
3. Do subsequent steps depend on the output of this failed step?
4. What is the best recovery action: fix and retry the step, replace it with an alternative, remove the step, or abort the plan?

Then output the revised plan that accounts for the error. If recovery is not possible, output an empty plan [].
```

---

### `PlanClarification._TaskInstruction`

**Agente:** `SupervisorPlannerConfirm`  
**Trigger:** `_handle_clarify()` — l'utente ha posto una domanda sul piano invece di accettarlo/modificarlo  
**Problema:** Il CoT chiede "What the current plan is about?" che è ridondante (l'LLM ha già il piano in contesto). L'istruzione finale ("explain the requested details and provide any additional context") non chiarisce che la risposta sarà mostrata all'utente (non a un altro LLM).

**Proposta:**

```
The user has asked a question or requested clarification about the proposed execution plan.
Your response will be shown directly to the user — write in clear, concise language without technical jargon.

Reason step by step:

1. What specific aspect of the plan is the user asking about?
2. Why was each relevant step included? What outcome does it produce?
3. Are there alternative approaches? If yes, briefly note why the current plan was chosen.

Then provide a direct, user-facing explanation that answers the question and ends by asking whether they wish to proceed with the plan.
```

---

### `PlanConfirmation.ConfirmationInterrupt.StaticMessage`

**Agente:** `SupervisorPlannerConfirm`  
**Trigger:** Interrupt mostrato all'utente prima dell'esecuzione del piano  
**Nota:** Questo non è un prompt LLM — è un messaggio deterministico mostrato all'utente. Non è incluso in questa analisi di raffinamento LLM.

---

---

## ModelsInstructions

### `InvokeTools._RoleAndScope`

**Agente:** `ModelsAgent`  
**Trigger:** `invocation_reason == "new_invocation"` — primo tentativo di invocazione dei tool  
**Problema:** "propose one tool call" è incoerente con `_TaskInstruction` che dice "the necessary tool calls" (plurale). "You do NOT interpret results or communicate with the user" è utile ma potrebbe essere più preciso. La lista dei prerequisiti è incompleta rispetto ai tool disponibili (SaferRain, DigitalTwin, SaferBuildings, SaferFire).

**Proposta:**

```
You are a simulation specialist operating within the SaferPlaces platform.
Your task is to select and configure the correct tool(s) to accomplish a specific simulation goal.
You produce tool call arguments only — you do not interpret results, generate narratives, or communicate directly with the user.

AVAILABLE TOOLS (summary):
- digital_twin: generates base geospatial layers (DEM, buildings, land use, etc.) for a bounding box.
- safer_rain: runs a flood depth simulation on a DEM using a rainfall input (uniform mm or raster).
- saferbuildings_tool: detects flooded buildings by intersecting a water depth raster with building footprints.
- safer_fire_tool: simulates wildland fire propagation over a DEM using wind and ignition inputs.

PRECONDITION RULES:
- safer_rain requires an existing DEM layer. If none is available, call digital_twin first.
- saferbuildings_tool requires an existing water depth raster. If none is available, call safer_rain first.
- safer_fire_tool requires an existing DEM and an ignitions layer.
- Always use the `src` value from the layer registry when referencing existing layers.
- If a required input is unavailable and cannot be inferred, do not fabricate it — propose no tool call.
```

---

### `InvokeTools._TaskInstruction`

**Agente:** `ModelsAgent`  
**Trigger:** `invocation_reason == "new_invocation"`  
**Problema:** Il testo è troppo breve e non chiarisce le aspettative sull'output. Non specifica cosa fare se i prerequisiti mancano (due righe di contesto isolate non sono sufficienti).

**Proposta:**

```
Propose the minimal set of tool calls required to accomplish the goal.

Decision rules:
- If all required inputs are available in the layer registry: propose the target tool directly.
- If a prerequisite layer is missing: propose the preparation tool first, then the target tool.
- If multiple required inputs are missing and cannot all be produced in one step: propose only the first missing prerequisite and let the orchestrator schedule subsequent steps.
- If the goal cannot be accomplished with the available tools and inputs: propose no tool call.

For each tool call, populate all required parameters. Leave optional parameters unset unless the goal explicitly specifies them.
```

---

### `CorrectToolsInvocation._TaskInstruction`

**Agente:** `ModelsAgent`  
**Trigger:** `invocation_reason == "invocation_provide_corrections"` — l'utente ha fornito correzioni manuali a parametri non validi  
**Problema:** "according user provided indications" è grammaticalmente scorretto. Non si fa riferimento a dove trovare le correzioni dell'utente (sono nei messaggi di conversazione). L'LLM non sa se correggere solo i campi errati o rigenerare tutto.

**Proposta:**

```
The previous tool call contained invalid or incomplete arguments. The user has provided corrections in the conversation history.

Your task:
1. Retrieve the user's corrections from the most recent messages.
2. Apply those corrections to the failing argument(s) only — keep all other arguments unchanged.
3. Re-validate preconditions: if a required input layer is still missing after corrections, call the appropriate preparation tool first.
4. Do not run the simulation if required inputs remain incomplete after applying corrections.

Propose the corrected tool call(s).
```

---

### `AutoCorrectToolsInvocation._TaskInstruction`

**Agente:** `ModelsAgent`  
**Trigger:** `invocation_reason == "invocation_auto_correct"` — l'utente ha richiesto correzione automatica (comando "correggi")  
**Problema:** "basing on your knowledge according the user desire" è grammaticalmente scorretto e semanticamente vago. Non è chiaro da quali fonti l'LLM deve desumere la correzione.

**Proposta:**

```
The previous tool call contained invalid or incomplete arguments. The user has requested automatic correction.

Your task:
1. Identify which arguments failed validation (listed in the error context).
2. Infer the most plausible correct values using: the goal statement, the layer registry, the parsed user request, and conversation history — in that priority order.
3. Apply corrections to the failing arguments only — keep all other arguments unchanged.
4. Re-validate preconditions: if a required input layer is missing, call the appropriate preparation tool first.
5. Do not fabricate layer references or numerical values that cannot be reasonably inferred.

Propose the auto-corrected tool call(s).
```

---

### `InvalidInvocationInterrupt.StaticMessage`

**Agente:** `ModelsInvocationConfirm`  
**Trigger:** Interrupt mostrato all'utente quando la validazione dell'invocazione fallisce  
**Nota:** Questo è un messaggio deterministico mostrato all'utente (interrupt), non un prompt LLM. Il testo è già in italiano e orientato all'utente. Non è incluso in questa analisi di raffinamento LLM.  
**Osservazione minore:** Il testo presenta `⚠️ Errori di validazione per il tool {tool_name}` ma il termine "il tool" potrebbe essere reso più user-friendly con il nome del tool tra virgolette. Da considerare in una revisione UX separata.

---

## Note trasversali

### Coerenza `_RoleAndScope` tra classi

Per `ModelsInstructions`, le classi `CorrectToolsInvocation` e `AutoCorrectToolsInvocation` delegano `_RoleAndScope` e `_GlobalContext` alle versioni di `InvokeTools`. Questo è corretto e mantiene consistenza, ma significa che il ruolo non distingue tra "prima invocazione" e "re-invocazione correttiva". Se il comportamento dovesse divergere in futuro (es. aggiungere una nota "This is a correction attempt"), la delega andrà spezzata.

### Contesto errore mancante in `PlanModificationDueStepError`

Il `_TaskInstruction` fa riferimento a un errore senza che il testo dell'errore sia iniettato nel prompt. Attualmente `_GlobalContext` delega a `PlanGeneration._GlobalContext` che non include il dettaglio dell'errore. Per rendere questa classe efficace serve un blocco `[ERROR DETAILS]` aggiuntivo nel `_GlobalContext` che legga da `state['step_error_details']` o equivalente.

### Output format implicito

Nessuno dei prompt specifica esplicitamente il formato di output (es. lista JSON di step, singolo tool call). Dato che l'LLM è vincolato via `with_structured_output(ExecutionPlan)` o `.bind_tools(...)`, il formato è imposto a livello codice. I prompt non devono quindi descrivere la struttura JSON, ma è utile lasciare frasi come "output the ordered plan" o "propose the corrected tool call(s)" per orientare l'LLM senza duplicare la schema definition.
