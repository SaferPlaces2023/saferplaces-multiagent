# PLN-013 — Riforma dell'Intelligenza dell'Agente e dell'Interazione Umana

> **Dipendenze**: nessun PLN precedente richiesto (questo piano è il punto di partenza della riforma)
> **Branch**: da definire
> **Stato**: draft

---

## Obiettivo

Risolvere le criticità **C1–C5** (agente che non funziona bene) e **H1–H5** (interazione umana scadente) identificate nella review del 2026-03-18. L'intervento è unificato perché le due famiglie di problemi sono interconnesse: un agente che capisce meglio il contesto ha bisogno di meno interruzioni inutili, e un'interazione umana più intelligente alimenta un planning migliore.

### Principi guida

1. **L'agente deve capire prima di agire** — il contesto deve essere ricco, strutturato, e disponibile in ogni fase
2. **L'interazione umana è un dialogo, non un quiz** — chiarimenti pre-azione, conferme sintetiche, classificazione rapida
3. **Ogni LLM call deve avere uno scopo chiaro** — eliminare round-trip inutili per classificare risposte ovvie
4. **Lo stato racconta una storia** — non è un contenitore di flag, ma una narrazione strutturata dell'esecuzione

---

## Mappa del problema → intervento

| Criticità | ID Review | Intervento architetturale | Sezione |
|---|---|---|---|
| Supervisor pianifica senza contesto | C1 | Context Enrichment Pipeline | §1 |
| RequestParser troppo minimale | C2 | Request Analyzer con risoluzione entità | §2 |
| FinalResponder cieco ai risultati | C3 | Execution Narrative + Response Synthesis | §3 |
| Prompts generici e deboli | C4 | Domain-Specific Prompt Restructuring | §4 |
| Nessun chiarimento pre-planning | C5 | Clarification Gate nel Request Analyzer | §2 |
| LLM call per classificare "ok" | H1 | Classificazione ibrida rule-based + LLM | §5 |
| Nessun dialogo naturale pre-azione | H2 | Clarification Gate (confluisce in §2) | §2 |
| Confirm message incoerente | H3 | Conferme strutturate, non generate da LLM | §6 |
| abort=rejected+aborted fragile | H4 | Enum semantico di stato del piano | §7 |
| Contatori clarify inconsistenti | H5 | Unificazione contatori interazione | §7 |

---

## §1 — Context Enrichment Pipeline

### Problema attuale

Il `SupervisorAgent` riceve un contesto impoverito:
- `parsed_request` contiene solo `{intent, entities, raw_text}` — informazioni grezze
- `relevant_layers` è un JSON dump non interpretato
- `AGENT_REGISTRY` è un blob JSON di ~100 righe con regole implicite embedded
- La conversation history è troncata a 5 messaggi senza filtro semantico
- I risultati dei tool precedenti NON sono visibili al Supervisor durante il re-routing

### Intervento

Introdurre un **Context Enrichment Pipeline** che costruisce un oggetto `PlanningContext` strutturato prima di ogni invocazione del Supervisor. Il pipeline opera in 3 fasi:

#### Fase 1 — Risoluzione del contesto geospaziale

Quando il `SupervisorRouter` (o il nuovo Request Analyzer) rileva che il layer registry contiene dati, il sistema deve costruire un **sommario interpretato** dei layer disponibili, non un JSON dump:

```
Attualmente il Supervisor vede:
  [{"src": "s3://...", "type": "raster", "metadata": {"band": "SRI", ...}}]

Dovrebbe vedere:
  Layer disponibili:
  - DEM 30m per area Roma (bbox: [12.3, 41.7, 12.6, 42.0]) — raster, creato nel ciclo precedente
  - Precipitazioni DPC SRI ultime 6h — raster, aggiornato 10 min fa
  
  Prerequisiti soddisfatti: DEM ✓, Pioggia ✓
  Prerequisiti mancanti per SaferRain: nessuno
```

Questo richiede che il **LayersAgent** (o un nuovo helper) generi un sommario human-readable dei layer, inclusa la verifica dei prerequisiti rispetto alle capabilities degli agenti.

#### Fase 2 — Tool Results History

Dopo ogni esecuzione di un agente specializzato, il `SupervisorRouter` deve costruire un **sommario dei risultati** del ciclo precedente e iniettarlo nel contesto del Supervisor:

```
Ciclo precedente:
  ✓ models_subgraph: Digital Twin creato per Roma (DEM 30m + edifici)
     Output: 2 layer aggiunti al registry (dem_roma, buildings_roma)
  
Ciclo corrente: step 2 di 2
  → models_subgraph: SaferRain con pioggia 50mm
```

Questo rompe il pattern "il Supervisor pianifica al buio" (C1) e gli dà visibilità su cosa è successo.

#### Fase 3 — Conversazione filtrata semanticamente

Invece di prendere gli ultimi N messaggi Human/AI, il context builder deve filtrare:
- **Includere**: messaggi dell'utente, risposte finali, feedback espliciti (modify/reject)
- **Escludere**: ToolMessage interni, AIMessage con tool_calls, messaggi di sistema
- **Riassumere**: se la storia supera un threshold, generare un sommario della conversazione precedente

### Dove intervenire nel codice

| Componente | Modifica |
|---|---|
| `supervisor.py` → `SupervisorAgent._generate_plan()` | Sostituire la costruzione manuale di messaggi con il nuovo `PlanningContext` |
| `supervisor.py` → `SupervisorRouter._update_additional_context()` | Estendere per costruire il sommario dei risultati precedenti |
| `supervisor_agent_prompts.py` → `CreatePlan.stable()` | Ricevere e formattare il `PlanningContext` strutturato |
| Nuovo modulo: `ma/context/` o `common/context_builder.py` | Ospitare la logica di enrichment indipendente dal nodo |

### Impatto atteso

Il Supervisor genererà piani **consapevoli** del contesto reale: layer esistenti, risultati precedenti, storia semantica della conversazione. La qualità del planning migliora drasticamente perché l'LLM non deve più "indovinare" da un JSON grezzo.

---

## §2 — Request Analyzer con Clarification Gate

### Problema attuale

Il `RequestParser` estrae solo `{intent, entities, raw_text}` senza nessuna interpretazione:
- Non risolve le entità geografiche (es. "Roma" → bbox approssimativo + paese)
- Non identifica ambiguità ("simula un allagamento" → dove? quanta pioggia? quale DEM?)
- Non classifica la tipologia di richiesta (info vs. azione vs. analisi)
- Non rileva dipendenze implicite
- **C5**: Non c'è nessun meccanismo di chiarimento pre-planning — l'utente è costretto a rigettare piani sbagliati

### Intervento

Evolvere il `RequestParser` in un **Request Analyzer** a due stadi:

#### Stadio 1 — Analisi strutturata arricchita

Il nuovo `ParsedRequest` deve contenere campi più ricchi:

| Campo | Tipo | Scopo |
|---|---|---|
| `intent` | `str` | Intent ad alto livello (mantenuto) |
| `request_type` | `enum` | `"action"` · `"info"` · `"analysis"` · `"clarification"` |
| `entities` | `list[Entity]` | Entità tipizzate con risoluzione (`{name, type, resolved}`) |
| `parameters` | `dict` | Parametri espliciti estratti (bbox, durata pioggia, prodotto, ecc.) |
| `implicit_requirements` | `list[str]` | Requisiti impliciti dedotti (es. "necessita DEM", "necessita bbox") |
| `ambiguities` | `list[str]` | Ambiguità rilevate (es. "bbox non specificato", "intensità pioggia non indicata") |
| `raw_text` | `str` | Testo originale (mantenuto) |

La risoluzione delle entità non è un geocoding completo — è un'**annotazione semantica** che aiuta il Supervisor. Se l'utente dice "Roma", la risoluzione produrrebbe:

```
Entity(name="Roma", type="location", resolved={"country": "IT", "approx_bbox": [12.2, 41.6, 12.7, 42.1]})
```

Questo consente al Supervisor di sapere DOVE operare senza dover interpretare il testo grezzo.

#### Stadio 2 — Clarification Gate (risolve C5 + H2)

Se `ambiguities` non è vuoto E `request_type == "action"`, il Request Analyzer emette un **interrupt di chiarimento** prima che il flusso raggiunga il Supervisor:

```
Ho capito che vuoi simulare un allagamento a Roma.
Però ho bisogno di alcuni chiarimenti:

1. Area esatta: vuoi usare il centro città o un'area più ampia?
2. Intensità pioggia: quanti mm di pioggia vuoi simulare?
3. DEM: non esiste un DEM per quest'area nel tuo progetto. Ne creo uno automaticamente?

Rispondi ai punti che preferisci, oppure scrivi "procedi" per far decidere all'agente.
```

Questo dialogo **naturale pre-azione** risolve il problema C5 e H2: l'utente collabora con l'agente nella definizione della richiesta, non subisce un piano sbagliato per poi rigettarlo.

#### Condizioni per il chiarimento

Il chiarimento viene richiesto SOLO quando:
- `request_type == "action"` (le richieste informative procedono direttamente)
- Le ambiguità sono **critiche** (mancano parametri senza cui il piano non può funzionare)
- Non è un follow-up in una conversazione dove i parametri sono già stati chiariti

Se le ambiguità sono **risolvibili dal contesto** (es. bbox deducibile da layer esistenti), il sistema risolve autonomamente e procede, annotando nel `ParsedRequest` cosa ha inferito.

### Integrazione nel grafo

Il Request Analyzer rimane un singolo nodo (`REQUEST_PARSER`), ma la sua logica interna diventa:

```
Analisi → Risoluzione entità → Check ambiguità 
    → [ambiguità critiche?] → Sì → interrupt("request-clarification") → Arricchisci con risposte
    → No (o risolte) → ParsedRequest arricchito → Supervisor
```

Il nodo gestisce internamente il loop di chiarimento (max 2 iterazioni), poi passa il `ParsedRequest` arricchito al Supervisor.

### Dove intervenire

| Componente | Modifica |
|---|---|
| `request_parser.py` | Riscrivere `run()` con analisi a due stadi |
| `common/base_models.py` o nuovo file | Definire il nuovo `ParsedRequest` Pydantic model con `Entity`, `request_type`, ecc. |
| `ma/prompts/` | Nuovo prompt file per il Request Analyzer (o estensione del `request_parser_prompts.py`) |
| `supervisor_agent_prompts.py` | `CreatePlan.stable()` deve usare il `ParsedRequest` strutturato, non il dump grezzo |

---

## §3 — Execution Narrative e Response Synthesis

### Problema attuale

Il `FinalResponder` riceve:
- `tool_results`: un dict potenzialmente enorme con risultati raw (URL S3, metadata JSON annidati)
- `state["messages"]`: l'**intera** storia messaggi (include ToolMessage interni, AIMessage con tool_calls, messaggi di sistema) — rischio di context overflow
- Nessun sommario di cosa è successo, quali step hanno avuto successo, quali errori

L'LLM deve parsare un blob per capire cosa comunicare → risposta generica e poco utile (C3).

### Intervento

Introdurre il concetto di **Execution Narrative**: un oggetto strutturato che racconta la storia dell'esecuzione, costruito incrementalmente durante il flusso e disponibile per il FinalResponder.

#### Struttura della Execution Narrative

| Campo | Tipo | Scopo |
|---|---|---|
| `request_summary` | `str` | Cosa ha chiesto l'utente (dal ParsedRequest arricchito) |
| `plan_summary` | `str` | Cosa è stato pianificato (sintetico) |
| `steps_executed` | `list[StepResult]` | Per ogni step: agente, goal, esito, output chiave |
| `layers_created` | `list[LayerSummary]` | Layer prodotti con nome, tipo, descrizione |
| `layers_used` | `list[LayerSummary]` | Layer usati come input |
| `errors` | `list[StepError]` | Errori con contesto (quale step, quale tool, messaggio) |
| `user_interactions` | `list[str]` | Chiarimenti/modifiche richiesti dall'utente durante l'esecuzione |
| `suggestions` | `list[str]` | Azioni successive suggerite (derivate dal contesto) |

Dove `StepResult`:

| Campo | Tipo | Scopo |
|---|---|---|
| `step_index` | `int` | Quale step del piano |
| `agent` | `str` | Agente che ha eseguito |
| `goal` | `str` | Obiettivo dello step |
| `outcome` | `enum` | `"success"` · `"partial"` · `"error"` · `"skipped"` |
| `output_summary` | `str` | Descrizione sintetica del risultato (non JSON raw) |
| `tool_name` | `str` | Tool eseguito |

#### Costruzione incrementale

La narrative viene costruita **durante l'esecuzione**, non ricostruita a posteriori:

1. **SupervisorAgent** → inizializza `execution_narrative` con `request_summary` + `plan_summary`
2. **Ogni Executor** (retriever/models) → dopo ogni tool call, aggiunge uno `StepResult` con `outcome` e `output_summary`
3. **SupervisorRouter** → al rientro da un subgraph, registra `layers_created/used`
4. **FinalResponder** → riceve la narrative completa e genera la risposta basandosi su dati strutturati, non su JSON raw

#### Impatto sul FinalResponder

Il prompt del FinalResponder cambia radicalmente:

```
Attualmente vede:
  "Tool results: {step_0: [{tool: 'safer_rain', args: {...}, result: {status: 'success', tool_output: {data: {uri: 's3://...', ...}}}}]}"

Dovrebbe vedere:
  "Esecuzione completata:
   1. ✓ Digital Twin per Roma — DEM 30m e edifici generati (2 layer nel registro)
   2. ✓ SaferRain 50mm su DEM Roma — simulazione completata, WD max 2.3m
   
   Layer creati: dem_roma (raster), buildings_roma (vector), flood_roma_50mm (raster)
   
   Suggerimenti: 
   - Puoi visualizzare il layer 'flood_roma_50mm' sulla mappa
   - Per confrontare scenari, prova con pioggia diversa (es. 100mm)"
```

### Dove intervenire

| Componente | Modifica |
|---|---|
| `common/states.py` | Aggiungere `execution_narrative` come campo di stato |
| Nuovo modulo: `common/narrative.py` o `common/execution_narrative.py` | Definire `ExecutionNarrative`, `StepResult`, `LayerSummary`, `StepError` |
| Ogni Executor (`safercast_agent.py`, `models_agent.py`) | Dopo `_execute_tool_call()`, popolare lo `StepResult` nella narrative |
| `supervisor.py` → `SupervisorRouter` | Al re-routing, aggiornare `layers_created/used` |
| `final_responder.py` | Usare `execution_narrative` come contesto primario, non `tool_results` raw |
| `final_responder_prompts.py` | Nuovo prompt `Context.Narrative.stable()` che formatta la narrative |

---

## §4 — Domain-Specific Prompt Restructuring

### Problema attuale

I prompt sono strutturalmente corretti (architettura versionata `stable()/v001()` con dataclass `Prompt`) ma **semanticamente deboli**:
- `MainContext.stable()` spiega il "come" del ragionamento ma non i "cosa" del dominio
- `AGENT_REGISTRY` è un JSON blob con `implicit_step_rules` in testo naturale embedded in JSON — doppia codifica
- I prompt degli agenti specializzati dicono "scegli il tool migliore" senza spiegare quando usare quale tool
- Nessun prompt ha esempi di errori da evitare (negative examples)
- Il FinalResponder ha un prompt generico per qualsiasi tipo di risposta

### Intervento

Ristrutturare i prompt su 4 assi:

#### A — MainContext con Euristiche di Dominio

Il prompt del Supervisor deve contenere **regole operative concrete**, non istruzioni procedurabili generiche:

```
Attualmente:
  "Check its prerequisites. If a prerequisite is missing, add the producing agent."

Dovrebbe diventare:
  "Regole operative:
  
  1. SIMULAZIONE ALLAGAMENTO richiede sempre:
     - DEM per l'area target (se non presente → step Digital Twin)
     - Parametri pioggia (durata, intensità) — se non specificati, chiedere
     - Output: raster WD (water depth)
  
  2. RECUPERO DATI METEO:
     - DPC: solo Italia, dati ultimi 30 giorni, prodotti SRI/SRT/VMI
     - Meteoblue: previsioni globali, fino a 7 giorni futuri
     - Se l'area è fuori Italia → usare solo Meteoblue
  
  3. DIGITAL TWIN:
     - Input: bbox dell'area + risoluzione (default 30m)
     - Output: DEM + buildings → 2 layer
     - Prerequisito per qualsiasi simulazione SaferRain"
```

#### B — Agent Registry de-jsonificato

Sostituire il JSON blob serializzato con **sezioni strutturate in testo naturale** all'interno del prompt, una per agente:

```
Attualmente:
  json.dumps(AGENT_REGISTRY)  →  blob JSON di ~100 righe

Dovrebbe diventare (nel prompt, non come JSON):
  "## Agenti disponibili
  
  ### models_subgraph — Simulazioni e modelli geospaziali
  **Tool**: digital_twin, safer_rain
  **Quando usarlo**: per creare DEM, edifici, simulazioni allagamento
  **Prerequisiti**: bbox dell'area target (da ParsedRequest o layer esistenti)
  **Output tipico**: layer raster (DEM, WD) + layer vettoriali (edifici)
  
  ### retriever_subgraph — Recupero dati meteorologici/ambientali
  **Tool**: dpc_retriever, meteoblue_retriever
  **Quando usarlo**: per scaricare dati radar, precipitazioni, previsioni meteo
  **Prerequisiti**: area + periodo temporale (inferibili dal contesto)
  **Output tipico**: layer raster (precipitazioni, radar)"
```

Questo elimina la doppia codifica (testo in JSON in prompt) e rende l'informazione direttamente leggibile dall'LLM.

#### C — Prompt specializzati con guide per tool

Ogni agente specializzato deve ricevere una **guida tool-specific** che spiega parametri, relazioni, e casi d'uso:

```
Attualmente (ModelsPrompts.MainContext):
  "Choose the best model or tool to execute the required simulation"

Dovrebbe includere:
  "## Tool: safer_rain
  Simula propagazione di acqua su DEM.
  
  Parametri chiave:
  - dem_layer: URI del DEM (obbligatorio — seleziona dal layer registry)
  - rainfall_mm: mm di pioggia totale (obbligatorio — chiedere se non specificato)
  - duration_hours: durata evento (default: 1h se non specificato)
  - band / to_band: range di intensità — usare solo se l'utente specifica variabilità
  
  Note:
  - SaferRain NON crea il DEM — se non esiste, serviva prima un Digital Twin
  - Il risultato è un raster WD (water depth) in metri"
```

#### D — Prompt FinalResponder context-aware

Invece di un prompt unico per tutte le risposte, creare varianti basate sul tipo di esecuzione:

| Tipo di esecuzione | Prompt variante |
|---|---|
| Piano con step completati | Template narrativo con risultati e suggerimenti |
| Piano vuoto (query informativa) | Template conversazionale diretto |
| Piano abortito dall'utente | Template di chiusura con riepilogo parziale |
| Errori durante l'esecuzione | Template di error reporting con suggerimenti di recovery |

### Dove intervenire

| Componente | Modifica |
|---|---|
| `supervisor_agent_prompts.py` | Riscrivere `MainContext.stable()` con euristiche di dominio |
| `supervisor_agent_prompts.py` | Riscrivere `AGENT_REGISTRY` → sezioni di testo strutturato |
| `supervisor_agent_prompts.py` | `CreatePlan.stable()` deve formattare il `PlanningContext` arricchito |
| `models_agent_prompts.py` | `MainContext.stable()` con guida tool-specific |
| `safercast_agent_prompts.py` | `MainContext.stable()` con guida tool-specific |
| `final_responder_prompts.py` | Nuove varianti di prompt basate sul tipo di esecuzione |

---

## §5 — Classificazione Ibrida Rule-Based + LLM

### Problema attuale

Ogni risposta dell'utente a un interrupt (conferma piano, conferma tool, risposta a validazione) viene classificata con una **LLM call zero-shot**. Questo è:

- **Lento**: 300–1000ms per classificare "ok"
- **Costoso**: token spesi per risposte ovvie
- **Fragile**: l'LLM può sbagliare (e il fallback è "reject" — pericoloso)
- **Cumulativo**: in un'esecuzione multi-step con conferme, ci sono 6–10 classificazioni LLM

In una singola sessione di lavoro:
1. `SupervisorPlannerConfirm._classify_user_response("ok")` → LLM call
2. `ToolInvocationConfirmationHandler._classify_user_response("yes")` → LLM call
3. `ToolValidationResponseHandler._classify_user_response("fix it")` → LLM call
4. ... ripetuto per ogni step del piano

### Intervento

Introdurre un **classificatore a due livelli**:

#### Livello 1 — Rule-based (keyword matching con punteggio di confidenza)

Un classificatore deterministico che copre i casi ovvi senza LLM call:

| Pattern | Label | Confidenza |
|---|---|---|
| `^(ok|sì|yes|si|proceed|vai|go|do it|fai|perfect|bene)$` | `accept` | alta |
| `^(no|stop|cancel|annulla|abort|basta|nevermind)$` | `abort` o `reject` | alta |
| `^(skip|salta|skippa)$` | `skip_tool` | alta |
| contiene `?` e len < 100 e starts with `(what|cosa|perché|why|come|how|explain|spiega)` | `clarify` | media |
| contiene `cambia|change|modify|modifica|al posto di|instead` | `modify` | media |

Regole:
- Se la confidenza è **alta** → restituisci direttamente senza LLM
- Se la confidenza è **media** → restituisci direttamente ma logga per monitoraggio
- Se nessun match → fallback al Livello 2

#### Livello 2 — LLM classification (solo casi ambigui)

Invocato solo quando il rule-based non trova match. Identico all'attuale classificazione zero-shot, ma con un prompt migliorato che include:
- Il contesto dell'interrupt (cosa è stato chiesto)
- La risposta dell'utente
- La **lingua** della conversazione (per evitare mismatch)

#### Fallback sicuro

Quando l'LLM restituisce un label non valido:
- **Non defaultare a "reject"** (attuale comportamento — pericoloso)
- Invece: **re-interrupt** con un messaggio di chiarimento: "Non ho capito la tua risposta. Vuoi procedere (sì/no) o hai bisogno di modifiche?"
- Max 1 re-interrupt per fallback, poi default a "accept" (principio di minimo danno: meglio eseguire che rifiutare silenziosamente)

### Dove intervenire

| Componente | Modifica |
|---|---|
| Nuovo modulo: `common/response_classifier.py` | Classificatore ibrido con API unificata |
| `confirmation_utils.py` | Sostituire `_classify_user_response()` con il nuovo classificatore |
| `validation_utils.py` | Sostituire `_classify_user_response()` con il nuovo classificatore |
| `supervisor.py` → `_classify_user_response()` | Sostituire con il nuovo classificatore |

### Impatto atteso

- **Latenza**: classificazione di "ok" → ~0ms (rule-based) invece di ~500ms (LLM)
- **Costo**: eliminazione del 70–80% delle LLM call di classificazione
- **Robustezza**: fallback sicuro invece di default a "reject"

---

## §6 — Conferme Strutturate (non generate da LLM)

### Problema attuale

Il `SupervisorPlannerConfirm` genera il messaggio di conferma con una **LLM call dedicata** (`_generate_confirmation_message()`). Il messaggio risultante è:
- Riformulato rispetto al piano originale (l'utente confronta 2 versioni diverse)
- In lingua diversa dalla richiesta (il Supervisor può rispondere in inglese a una richiesta italiana)
- Troppo lungo e verbose
- Inconsistente tra esecuzioni diverse

Lo stesso pattern si ripete per `_generate_tool_confirmation_message()` negli agenti specializzati.

### Intervento

Sostituire la generazione LLM con **template strutturati** compilati programmaticamente:

#### Template per conferma piano

```
📋 Piano di esecuzione ({n} step):

{per ogni step:}
  {i}. [{agent_label}] {goal}

Rispondi:
  ✓ "ok" per procedere
  ✏️ descrivi le modifiche
  ❌ "annulla" per cancellare
```

Dove `agent_label` è un nome human-readable mappato dal nome tecnico:
- `models_subgraph` → "Simulazione"
- `retriever_subgraph` → "Recupero dati"

#### Template per conferma tool invocation

```
🔧 Tool da eseguire:

{per ogni tool_call:}
  • {tool_label}: 
    {per ogni arg significativo:}
      - {arg_name}: {arg_value}

Rispondi:
  ✓ "ok" per eseguire
  ✏️ descrivi le modifiche agli argomenti
  ❌ "salta" per saltare questo step
```

#### Vantaggi

- **Zero LLM call** per generare conferme (risparmio 1–3 call per esecuzione)
- **Consistenza**: formato identico ogni volta
- **Lingua**: template localizzati o match con lingua della richiesta
- **Compattezza**: nessun testo superfluo

### Dove intervenire

| Componente | Modifica |
|---|---|
| `supervisor.py` → `_generate_confirmation_message()` | Sostituire LLM call con template |
| `safercast_agent.py` e `models_agent.py` → `_generate_tool_confirmation_message()` | Sostituire LLM call con template |
| Nuovo: `common/templates.py` o `ma/prompts/templates.py` | Template di conferma condivisi |

---

## §7 — Stato Semantico e Contatori Unificati

### Problema H4 — abort=rejected+aborted è fragile

Attualmente un piano abortito viene rappresentato con:
```python
state["plan_confirmation"] = PLAN_REJECTED  # ← semanticamente sbagliato
state["plan_aborted"] = True                # ← flag separato per disambiguare
```

E il conditional edge del grafo deve controllare ENTRAMBI:
```python
lambda state: state.get('plan_confirmation') == 'rejected' and not state.get('plan_aborted')
```

### Intervento

Sostituire il pattern con un **enum semantico** per lo stato del piano:

| Valore | Significato | Flusso |
|---|---|---|
| `"pending"` | Piano generato, in attesa di conferma | → interrupt utente |
| `"accepted"` | Piano approvato, pronto per esecuzione | → router → esecuzione |
| `"modify"` | Utente vuole modifiche incrementali | → loop back al Supervisor con feedback |
| `"rejected"` | Utente vuole ripianificazione totale | → loop back al Supervisor da zero |
| `"aborted"` | Utente ha annullato l'intera operazione | → FINAL_RESPONDER direttamente |

Questo elimina il flag `plan_aborted` e rende il conditional edge semplice:
```python
lambda state: state.get('plan_confirmation') in ('modify', 'rejected')
# True → loop back al Supervisor
# False (accepted, aborted, pending) → avanti
```

Per `"aborted"`, il router rileva il valore e salta direttamente a FINAL_RESPONDER.

### Problema H5 — Contatori clarify separati e inconsistenti

Attualmente esistono:
- `clarify_iteration_count` (usato in `SupervisorPlannerConfirm` e `ToolInvocationConfirmationHandler`)
- `validation_clarify_iteration_count` (usato in `ToolValidationResponseHandler`)

Sono due contatori separati che tracciano concetti simili ma con naming e lifecycle diversi. Il `clarify_iteration_count` viene resettato in modo inconsistente tra confirmation e validation.

### Intervento

Unificare i contatori in un singolo meccanismo di **interaction budget**:

| Campo | Tipo | Scopo |
|---|---|---|
| `interaction_budget` | `int` | Numero massimo di interazioni human-in-the-loop per ciclo (default: 5) |
| `interaction_count` | `int` | Interazioni consumate nel ciclo corrente |

Ogni interrupt che riceve una risposta utente consuma 1 punto dal budget, indipendentemente dal tipo (clarify, validation, confirmation). Quando il budget si esaurisce:
- Se siamo in confirmation → auto-accept
- Se siamo in validation → auto-correct
- Logga un warning per monitoraggio

Questo semplifica la gestione, elimina i due contatori separati, e dà una semantica chiara: "l'agente non chiede più di N volte per ciclo".

### Dove intervenire

| Componente | Modifica |
|---|---|
| `common/states.py` | Sostituire `plan_aborted` + `clarify_iteration_count` + `validation_clarify_iteration_count` con `plan_confirmation` enum + `interaction_count` + `interaction_budget` |
| `supervisor.py` | Aggiornare conditional edges e handler per usare il nuovo enum |
| `confirmation_utils.py` | Usare `interaction_count` unificato |
| `validation_utils.py` | Usare `interaction_count` unificato |
| `multiagent_graph.py` | Semplificare conditional edge del supervisor subgraph |

---

## Sequenza di implementazione raccomandata

Gli interventi sono interdipendenti. L'ordine raccomandato minimizza le dipendenze circolari e massimizza il valore incrementale:

```
Fase 0 — Preparazione (nessun impatto funzionale)
├── §7: Stato semantico + contatori unificati
│   → Cambia la struttura dello stato senza cambiare il comportamento
│   → Tutti gli altri interventi useranno il nuovo schema
│
Fase 1 — Foundation (miglioramento immediato della qualità)
├── §5: Classificazione ibrida rule-based + LLM
│   → Indipendente, testabile isolatamente
│   → Riduce latenza e costi immediatamente
├── §6: Conferme strutturate (template)
│   → Indipendente, testabile isolatamente
│   → Elimina LLM call per conferme
│
Fase 2 — Intelligence Core (il salto qualitativo)
├── §2: Request Analyzer con Clarification Gate
│   → Richiede §7 per lo stato semantico
│   → Produce ParsedRequest arricchito per §1 e §4
├── §4: Domain-Specific Prompt Restructuring
│   → Richiede il ParsedRequest arricchito da §2
│   → Richiede il nuovo formato AGENT_REGISTRY (non più JSON blob)
│
Fase 3 — Full Context (completamento)
├── §1: Context Enrichment Pipeline
│   → Richiede §2 (ParsedRequest arricchito) come input
│   → Richiede §4 (nuovi prompt) come consumatore
├── §3: Execution Narrative
│   → Richiede §1 (context pipeline) per l'inizializzazione
│   → Richiede §4 (prompt FinalResponder context-aware) come consumatore
│   → È l'ultimo pezzo: il FinalResponder diventa finalmente "intelligente"
```

### Dipendenze tra sezioni

```
§7 (Stato)
 ↓
§5 (Classificazione) ──── §6 (Template conferme) ──── indipendenti
 ↓
§2 (Request Analyzer)
 ↓
§4 (Prompt Restructuring)
 ↓
§1 (Context Pipeline) ← §2
 ↓
§3 (Execution Narrative) ← §1 + §4
```

---

## Acceptance Criteria

- [ ] **SC-013-01**: Il Supervisor genera piani corretti per scenari con prerequisiti (es. "simula allagamento a Roma" genera 2 step quando non c'è DEM)
- [ ] **SC-013-02**: Il Request Analyzer chiede chiarimenti quando la richiesta è ambigua (bbox mancante, intensità pioggia non specificata)
- [ ] **SC-013-03**: La classificazione di "ok", "sì", "yes" non invoca LLM (latenza < 10ms)
- [ ] **SC-013-04**: Il FinalResponder produce risposte con narrative strutturate (non JSON raw)
- [ ] **SC-013-05**: Le conferme del piano usano template deterministici (non generati da LLM)
- [ ] **SC-013-06**: Lo stato del piano usa enum semantico (nessun flag `plan_aborted` separato)
- [ ] **SC-013-07**: Un singolo contatore `interaction_count` traccia tutti i tipi di chiarimento
- [ ] **SC-013-08**: Il Supervisor vede i risultati dei tool precedenti durante il re-routing

---

## Rischi e Decisioni Aperte

| Rischio | Mitigazione |
|---|---|
| Il Request Analyzer con clarification gate potrebbe rallentare le richieste semplici | Gate attivato SOLO per `request_type == "action"` con ambiguità critiche |
| La classificazione rule-based potrebbe non coprire risposte in lingue diverse | Espandere i pattern per IT/EN/FR e usare LLM come fallback |
| L'Execution Narrative potrebbe diventare troppo grande in sessioni lunghe | Limitare a max 10 step; per sessioni più lunghe, riassumere gli step precedenti |
| I template di conferma potrebbero essere troppo rigidi | Prevedere un campo `notes` per annotazioni one-off dal piano |
| Il refactor dei prompt potrebbe degradare il comportamento in scenari non testati | Usare test con prompt override (`T006` pattern) per validare ogni variante prima del deploy |

---

## Note

- Questo piano **non** affronta la duplicazione del codice tra `safercast_agent.py` e `models_agent.py` (problema S1). Quella è una riforma strutturale separata che beneficerà degli interventi fatti qui (specialmente §3 e §6 che standardizzano formati), ma va pianificata come PLN separato.
- Questo piano **non** affronta la rigidità del grafo (problema S2–S5). La dinamicizzazione del grafo è un intervento ortogonale che richiede prima la stabilizzazione dell'intelligenza dell'agente.
- Il piano assume che i test `T001`–`T006` esistenti continuino a passare dopo ogni fase. Aggiungere test specifici per i nuovi comportamenti (clarification gate, classificazione rule-based, narrative).
