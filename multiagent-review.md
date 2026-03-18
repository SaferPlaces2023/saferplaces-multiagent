# Multiagent System — Review Critica Completa

**Data review**: 2026-03-18  
**Scope**: Analisi architetturale, funzionale, e qualitativa dell'intero sistema multiagente.

---

## Sommario

Il sistema ha un'architettura ad alto livello plausibile (plan → confirm → execute → respond), ma soffre di problemi strutturali profondi che lo rendono **rigido, fragile, e "stupido" nella pratica**. I problemi si concentrano in 5 macro-aree:

1. **L'agente non capisce il contesto** — il Supervisor pianifica senza memoria e senza contesto sufficiente
2. **Duplicazione massiva di codice** — gli agenti specializzati sono cloni copia-incolla
3. **L'interazione umana è meccanica e innaturale** — interrupt troppi, poco intelligenti, mal gestiti
4. **Il grafo è rigido e non estensibile** — aggiungere un agente richiede modifiche in 5+ file
5. **Prompts generici e deboli** — l'LLM riceve istruzioni vaghe e produce piani scadenti

---

## 1. ARCHITETTURA GENERALE — Problemi di Design

### 1.1 SupervisorAgent: pianifica al buio

**Problema critico**: Il `SupervisorAgent` genera il piano usando solo `parsed_request` e `relevant_layers`, ma non ha accesso a:
- **Risultati dei tool precedenti** — non sa cosa è stato prodotto nei cicli passati
- **Conversation history adeguata** — usa solo ultimi 5 messaggi Human/AI filtrati
- **Contesto semantico del dominio** — non sa cosa significano concretamente i tool o i loro output
- **Stato dei layer nel dettaglio** — riceve solo un dump JSON di `relevant_layers`, non una comprensione semantica

**Perché "sembra stupido"**: Quando l'utente dice "simula allagamento a Roma", il Supervisor riceve:
```
parsed_request: {intent: "flood simulation", entities: ["Roma"], raw_text: "..."}
relevant_layers: []
agent_registry: [JSON blob di ~100 righe con nomi tecnici]
```
L'LLM deve dedurre: Roma → bbox → niente DEM → servono 2 step (DigitalTwin + SaferRain). Questo funziona solo se il prompt è molto preciso e il modello è potente. Attualmente il prompt è generico.

**Fix necessario**: Il Supervisor deve ricevere un contesto strutturato e ricco, non JSON raw.

### 1.2 RequestParser: troppo minimale

Il `RequestParser` estrae solo `{intent, entities, raw_text}` — **niente di azionabile**. Non:
- Risolve entità geografiche (Roma → bbox)
- Classifica il tipo di richiesta (info vs. azione vs. analisi)
- Rileva ambiguità da chiarire PRIMA del planning
- Identifica vincoli temporali, spaziali, qualitativi

**Conseguenza**: Tutto il "capire la richiesta" viene delegato implicitamente al Supervisor, che è un planner, non un analista. Il planning fallisce perché le basi sono deboli.

### 1.3 FinalResponder: cieco e generico

Il `FinalResponder` riceve in prompt:
```
- User intent: flood simulation
- Plan: [lista step]
- Tool results: {step_0: [...]}  
- Error: None
```
Ma NON ha:
- I messaggi intermedi dell'interazione (confirm, clarify, validation)
- Un riassunto di cosa è successo durante l'esecuzione
- I layer prodotti con i loro URI/metadati
- La capacità di suggerire azioni successive

**Risultato**: La risposta finale è generica e poco utile. L'utente non capisce cosa è stato fatto.

---

## 2. DUPLICAZIONE MASSIVA DI CODICE

### 2.1 safercast_agent.py ≈ models_agent.py (circa 90% identici)

Questi due file sono **sostanzialmente lo stesso codice** con nomi diversi. Condividono:
- `ToolRegistry` (identico pattern, duplicato)
- `*Agent.run()` (identico flusso: build_messages → llm.invoke → has_no_tool_calls → prepare_invocation)
- `*InvocationConfirm.run()` (identico: validate → handle_validation_failure → request_confirmation)
- `*Executor.run()` (identico: iterate tool_calls → execute → format → add_layer → record_result)
- `_add_layer_to_registry()` (identico, copia-incolla)
- `_record_tool_result()` / `_record_tool_error()` (identici)
- `_generate_validation_error_message()` (identico)
- `_generate_tool_confirmation_message()` (identico)
- `_format_tool_calls_for_display()` (identico)

**L'unica differenza reale**: i nomi delle chiavi di stato (`retriever_*` vs `models_*`) e i tool registrati.

**Impatto**:
- Ogni fix va applicato in 2+ file → bug ricorrenti
- Aggiungere un nuovo agent = copiare ~400 righe → errori garantiti
- La `confirmation_utils.py` e `validation_utils.py` già esistono come astrazione parziale, ma il grosso della duplicazione resta negli agent file

### 2.2 Due classi ToolRegistry identiche ma incompatibili

C'è un `ToolRegistry` singleton in `safercast_agent.py` E un altro in `models_agent.py`. Sono **la stessa classe** ma con scope diversi. Un tool non può appartenere a più agenti. Non esiste un registry globale.

### 2.3 _get_conversation_context() triplicata

Questa helper è definita identica in:
- `supervisor_agent_prompts.py`
- `models_agent_prompts.py`
- `safercast_agent_prompts.py`

### 2.4 Formattazione tool response hardcoded per tool name

In `DataRetrieverExecutor._format_tool_response()`:
```python
if tool_name == "dpc_retriever":
    content = self._format_dpc_response(tool_args, result)
elif tool_name == "meteoblue_retriever":
    content = self._format_meteoblue_response(tool_args, result)
```

In `ModelsExecutor._format_tool_response()`:
```python
if tool_name == "safer_rain":
    content = self._format_safer_rain_response(tool_args, result)
```

Ogni nuovo tool richiede un nuovo `elif` nell'executor. Questo dovrebbe essere responsabilità del tool stesso (pattern `tool.format_response(result)`).

---

## 3. INTERAZIONE UMANA — Meccanica e Frustrante

### 3.1 Troppi LLM call per classificare risposte banali

Il sistema usa **LLM call zero-shot per classificare ogni risposta utente**. Quando l'utente dice "ok" alla conferma del piano, il flusso è:

1. `SupervisorPlannerConfirm` → LLM call per generare messaggio di conferma
2. `interrupt()` → attende risposta utente
3. `_classify_user_response()` → **LLM call** per classificare "ok" → "accept"

Per classificare "ok" come "accept" serve un intero round-trip LLM. Questo è:
- **Lento** (300-1000ms per ogni classificazione)
- **Fragile** (l'LLM può sbagliare la classificazione)
- **Costoso** (token spesi per classificare "ok")

Lo stesso pattern si ripete in `ToolInvocationConfirmationHandler` e `ToolValidationResponseHandler`. In una singola esecuzione multi-step con confirm abilitato, ci possono essere **6-10 LLM call solo per classificare risposte utente**.

**Fix**: Rule-based classification per risposte ovvie, LLM solo per casi ambigui.

### 3.2 Conferma del piano: confusing e verbose

Il `SupervisorPlannerConfirm` genera il messaggio di conferma con una **LLM call dedicata**. Il messaggio risultante è spesso:
- Troppo lungo
- Riformulato rispetto al piano originale (l'utente confronta 2 versioni diverse)
- In lingua diversa dalla richiesta

E la cosa peggiore: quando il piano è vuoto (`steps: []`), il nodo fa **auto-confirm** senza comunicare nulla all'utente — silenzio totale, poi FINAL_RESPONDER risponde. L'utente non sa cosa è successo.

### 3.3 Validation loop infiniti potenziali

`ToolValidationResponseHandler.process_validation_response()` è ricorsivo:
```python
# Recursive call with new response
return self.process_validation_response(
    state, new_response, validation_errors, ...
)
```

Anche se c'è un `max_clarify_iterations`, il contatore `validation_clarify_iteration_count` è diverso da `clarify_iteration_count` di `ConfirmationHandler`. Ci sono **due contatori diversi** per la stessa famiglia di loop causando confusione.

### 3.4 Abort del piano: stato inconsistente

Quando l'utente fa "abort" nel `SupervisorPlannerConfirm._handle_abort()`:
```python
state["plan"] = []
state["plan_aborted"] = True  
state["plan_confirmation"] = PLAN_REJECTED  # ← BUG: dovrebbe essere ACCEPTED o altro
state["supervisor_next_node"] = NodeNames.FINAL_RESPONDER
```

Ma il conditional edge nel grafo è:
```python
lambda state: state.get('plan_confirmation') == 'rejected' and not state.get('plan_aborted')
```

Questo "funziona" ma è un logic coupling fragile — `plan_confirmation = REJECTED` + `plan_aborted = True` è un pattern anti-intuitivo. Lo stato dovrebbe avere un valore semantico chiaro (es. `plan_confirmation = "aborted"`).

### 3.5 Nessuna conversazione pre-azione

Il sistema non ha modo di **chiedere chiarimenti all'utente PRIMA del planning**. Se l'utente dice "simula un allagamento", il sistema deve:
1. Decidere dove (bbox?)
2. Decidere con quanta pioggia
3. Decidere quale DEM usare

Ma NON chiede — fa tutto autonomamente e poi chiede conferma di un piano che potrebbe essere completamente sbagliato. L'utente è costretto a rigettare e ripianificare.

---

## 4. RIGIDITÀ DEL GRAFO

### 4.1 Aggiungere un nuovo agent = modificare 5+ file

Per aggiungere un nuovo agente specializzato (es. `AnalyticsAgent`):

1. Creare `ma/specialized/analytics_agent.py` (~400 righe copia-incolla)
2. Aggiungere nomi in `ma/names.py` (3 nuove costanti)
3. Aggiungere chiavi di stato in `common/states.py` (4 nuove field)
4. Aggiungere cleanup in `StateManager` (2 metodi)
5. Aggiungere subgraph builder in `multiagent_graph.py`
6. Aggiungere conditional edge nel grafo principale
7. Aggiungere descrizione in `AGENT_REGISTRY` dei prompts
8. Aggiungere prompt file in `ma/prompts/`

Questo è **irragionevolmente costoso e error-prone**.

### 4.2 Agent routing hardcoded nel SupervisorRouter

```python
agent_type = "models" if agent_name == NodeNames.MODELS_SUBGRAPH else "retriever"
```

Qualsiasi agente che non è `models_subgraph` viene trattato come `retriever`. Aggiungere un terzo tipo richiede un `elif` manuale.

### 4.3 Conditional edges statici

```python
graph_builder.add_conditional_edges(
    NodeNames.SUPERVISOR_SUBGRAPH,
    lambda state: state.get("supervisor_next_node", END),
    {
        NodeNames.RETRIEVER_SUBGRAPH: NodeNames.RETRIEVER_SUBGRAPH,
        NodeNames.MODELS_SUBGRAPH: NodeNames.MODELS_SUBGRAPH,
        NodeNames.FINAL_RESPONDER: NodeNames.FINAL_RESPONDER,
        END: END,
    }
)
```

Ogni nuovo agente richiede una nuova entry nella mappa. Non c'è auto-discovery o registry dinamico.

### 4.4 State explosion

`MABaseGraphState` ha **30+ campi**, molti dei quali sono varianti per agente (`retriever_invocation`, `models_invocation`, ecc.). Ogni nuovo agente aggiunge 4 campi. Questo non scala. Serve uno state nesting pattern: `state["agents"]["retriever"]["invocation"]`.

---

## 5. PROMPTS — Generici e Deboli

### 5.1 SupervisorAgent prompt: troppo procedurale

Il prompt `OrchestratorPrompts.MainContext.stable()` è lungo ma **generico**:
```
"You are an expert multi-step planning agent for a geospatial AI platform."
```

Spiega il procedimento razionale ma non:
- Descrive i TIPI di richiesta che sa gestire (info, simulazione, recupero dati, analisi)
- Fornisce euristiche chiare (if x → use agent Y)
- Guida il ragionamento sulle dipendenze in modo strutturato
- Spiega cosa fare quando la richiesta è ambigua

Il piano viene fuori dalla "creatività" dell'LLM, non da una guida strutturata.

### 5.2 AGENT_REGISTRY inviato come JSON blob

L'`AGENT_REGISTRY` viene serializzato con `json.dumps()` e passato al Supervisor come testo:
```python
agent_registry_str = json.dumps(OrchestratorPrompts.Plan.AGENT_REGISTRY, ensure_ascii=False, indent=2)
```

Questo produce un blob JSON di ~100 righe che l'LLM deve interpretare. Include campi come `implicit_step_rules` e `prerequisites` che sono testo naturale embedded in un JSON — **doppia codifica** che riduce la comprensione dell'LLM.

### 5.3 Specialized agent prompts: non guidano l'LLM

I prompt degli agenti specializzati dicono "scegli il tool migliore" ma non:
- Spiegano i casi d'uso specifici di ogni tool
- Forniscono esempi concreti di invocazione
- Chiariscono le relazioni tra parametri (es. `band`/`to_band` in SaferRain)
- Guidano l'inferenza dei parametri mancanti

### 5.4 FinalResponder: prompt context non filtrato

Il `FinalResponderPrompts.Context.Formatted.stable()` costruisce il contesto così:
```python
f"- Tool results: {tool_results}\n"
```

`tool_results` è un dict potenzialmente enorme con risultati raw delle API (URL S3, metadata, etc.). Questo è:
- **Troppo grande** per il contesto LLM
- **Non filtrato** — contiene dettagli tecnici irrilevanti per l'utente
- **Non strutturato** — l'LLM deve parsare un blob per capire cosa comunicare

Poi aggiunge IN CODA l'intera conversazione:
```python
invoke_messages = [
    SystemMessage(content=prompt_response.message),
    HumanMessage(content=prompt_context.message),
    *state["messages"],  # ← INTERA STORIA MESSAGGI
]
```

In una sessione lunga, `state["messages"]` può contenere centinaia di messaggi (inclusi ToolMessage, AIMessage interni degli agenti), causando context overflow.

---

## 6. PROBLEMI PUNTUALI

### 6.1 Import errato in multiagent_graph.py

```python
from turtle import pd  # ←  ??
```

Import di `pd` dal modulo `turtle` — chiaramente un errore/residuo.

### 6.2 Typo persistente: `avaliable_tools`

In `MABaseGraphState`:
```python
avaliable_tools: list[str] | None = []
```

Dovrebbe essere `available_tools`. Questo typo è propagato ovunque (graph_interface, chat_handler).

### 6.3 BaseGraphState vs MABaseGraphState

Esiste una `BaseGraphState` (class) oltre a `MABaseGraphState` (TypedDict) in `states.py`. La prima sembra un residuo del vecchio sistema che non viene più usata ma non è stata rimossa.

### 6.4 common/names.py (NN) — legacy dead code

L'intero file `common/names.py` contiene nomi di nodi del vecchio sistema (`chatbot`, `demo_subgraph`, `create_project_subgraph`, ecc.) che non esistono più nel grafo attuale. È dead code che genera confusione.

### 6.5 LayersAgent: prompt non strutturato

```python
invoke_messages = [
    SystemMessage(content="You are a specialized agent for managing geospatial layers. Use the available tools to accomplish the goal."),
    HumanMessage(content=f"Goal: {state['layers_request']}")
]
```

Il LayersAgent usa un prompt inline hardcoded, non il sistema versionato. La `layers_request` è una stringa libera costruita ad-hoc nell'executor/router. Nessuna consistenza.

### 6.6 ConversationHandler: eventi come class variable condivisa

```python
class ConversationHandler:
    title = None
    events: list[AnyMessage | Interrupt] = []
    new_events: list[AnyMessage | Interrupt] = []
```

`events` e `new_events` sono **class variables mutabili** — condivise tra tutte le istanze! Questo è un bug classico Python che causa cross-contamination tra thread/conversazioni.

### 6.7 graph_interface.restore_state passa messaggi vuoti

```python
event_value = { 
    'messages': [],
    'layer_registry': restored_layer_registry,
    'user_id': self.user_id,
    'project_id': self.project_id
}
_ = list( self.G.stream(input=event_value, config=self.config, stream_mode='updates') )
```

Questo fa passare TUTTO il grafo (REQUEST_PARSER → ... → FINAL_RESPONDER) in modalità setup, potenzialmente causando effetti collaterali nei nodi.

### 6.8 user_prompt passa `state_updates` con default mutabile

```python
def user_prompt(self, prompt: str, state_updates: dict = dict()):
```

`dict()` come default argument mutabile — bug Python classico. Ogni chiamata condivide lo stesso dict.

---

## 7. MAPPA DEI PROBLEMI PER GRAVITÀ

### Critici (l'agente "non funziona bene")
| # | Problema | File | 
|---|----------|------|
| C1 | Supervisor pianifica senza contesto sufficiente | supervisor.py, supervisor_agent_prompts.py |
| C2 | RequestParser non risolve entità/ambiguità | request_parser.py |
| C3 | FinalResponder cieco ai risultati reali | final_responder.py |
| C4 | Prompts generici — l'LLM "indovina" invece di ragionare | tutti i prompt files |
| C5 | Nessun meccanismo di chiarimento pre-planning | assente nel grafo |

### Strutturali (impediscono l'evoluzione)
| # | Problema | File |
|---|----------|------|
| S1 | Duplicazione ~90% tra safercast_agent.py e models_agent.py | entrambi |
| S2 | Aggiungere un agente richiede modifiche in 5+ file | grafo, stato, nomi, prompts |
| S3 | State explosion — 30+ campi con pattern ripetitivo | states.py |
| S4 | Agent routing hardcoded nel SupervisorRouter | supervisor.py |
| S5 | ToolRegistry duplicati e non estensibili | safercast/models_agent.py |

### Interazione umana (UX scadente)
| # | Problema | File |
|---|----------|------|
| H1 | LLM call per classificare "ok" → lento/costoso | confirmation_utils.py |
| H2 | Nessun dialogo naturale pre-azione | assente |
| H3 | Confirm message generato da LLM → incoerente | supervisor.py, *InvocationConfirm |
| H4 | abort=rejected+aborted flag — pattern fragile | supervisor.py |
| H5 | Contatori clarify separati e inconsistenti | confirmation_utils.py, validation_utils.py |

### Bug/Technical Debt
| # | Problema | File |
|---|----------|------|
| B1 | `from turtle import pd` — import errato | multiagent_graph.py |
| B2 | `avaliable_tools` typo propagato ovunque | states.py, graph_interface.py |
| B3 | ConversationHandler con class variable mutabili condivise | graph_interface.py |
| B4 | `state_updates: dict = dict()` mutable default | graph_interface.py |
| B5 | common/names.py (NN) dead code | common/names.py |
| B6 | BaseGraphState residuo non rimosso | states.py |
| B7 | restore_state esegue il grafo intero per setup | graph_interface.py |
| B8 | format_tool_response hardcoded per tool name | executor in safercast/models_agent.py |

---

## 8. DIREZIONE DI RIFORMA

### Principi guida

1. **DRY**: Un solo `SpecializedAgent` base parametrizzato per tutti gli agenti
2. **Registry-driven**: Tool registry globale → subgraph auto-generati
3. **Smart parsing**: RequestParser che risolve entità, rileva ambiguità, ask-before-plan
4. **Rich context**: Supervisor che vede contesto strutturato, non JSON raw
5. **Natural interaction**: Dialogo pre-azione, classificazione rule-based, conferme sintetiche
6. **Flat state**: State nesting per agenti specializzati, non field explosion

### Architettura target (sketch)

```
USER MESSAGE
    ↓
REQUEST_ANALYZER (parse + disambigua + chiedi chiarimenti se servono)
    ↓
PLANNER (piano strutturato con dependency graph, non lista piatta)
    ↓ 
PLAN_CONFIRM (sintetico, interattivo, non verbose)
    ↓
EXECUTOR_LOOP (generico, parametrizzato per tool type)
  ├── per ogni step:
  │   ├── resolve dependencies (layer/output precedenti)
  │   ├── call tool (validate → infer → execute)
  │   ├── mini-checkpoint (opzionale, breve)
  │   └── record results  
  ↓
RESPONSE_SYNTHESIZER (contesto filtrato, suggerisce next steps)
    ↓
END
```

### Priorità di intervento

1. **Refactor Agent Base** — eliminare duplicazione safercast/models → `BaseSpecializedAgent`
2. **Potenziare RequestParser** → entity resolution, ambiguity detection
3. **Ristrutturare Prompts** → specifici per dominio, con esempi, euristiche chiare
4. **Semplificare State** → nesting, auto-registration di agent state
5. **Natural confirm flow** → rule-based classification, messaggi sintetici
6. **Dynamic graph building** → tool registry → subgraph auto-generation
