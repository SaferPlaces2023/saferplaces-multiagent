# PLN-016 — Architettura composita dei prompt del SupervisorAgent

**Dipendenze:** PLN-013 (ContextBuilder, ExecutionNarrative)
**Branch:** `refactor/pln-016-supervisor-prompts`
**File target:**
- `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py`
- `src/saferplaces_multiagent/ma/orchestrator/supervisor.py`

---

## Obiettivo

Ridisegnare il sistema di prompt del `SupervisorAgent` come un set di componenti compositi e complementari, eliminando le ridondanze semantiche tra prompt usati nella stessa catena di messaggi. Contestualmente allineare `supervisor.py` al nuovo schema di invocazione.

Il principio guida è: **ogni prompt ha una responsabilità unica e non ripete ciò che altri prompt nella stessa catena già dichiarano**.

---

## Analisi del problema

### Catene di invocazione LLM correnti

In `_generate_plan` (supervisor.py) la catena è sempre:

```
[SystemMessage: MainContext]
[HumanMessage: context_str  ← ContextBuilder.format_for_prompt()]
[HumanMessage: CreatePlan | IncrementalReplanning | TotalReplanning]
```

In `_generate_plan_explanation`:

```
[SystemMessage: ExplainerMainContext]
[HumanMessage: RequestExplanation]
```

### Ridondanze identificate

| Coppia | Contenuto duplicato |
|---|---|
| `MainContext` ←→ `CreatePlan` | planning policy, agent selection logic, goal writing rules, output format |
| `context_str` (ContextBuilder) ←→ `CreatePlan` | request intent, layer summary, conversation history |
| `MainContext` ←→ `PlannerContext` | ruolo, constraints, agent list, planning rules (quasi identici) |

### Prompt attualmente inutilizzati (dead code)

| Prompt | Motivo |
|---|---|
| `Plan.PlannerContext` | Mai invocato da supervisor.py — contenuto quasi identico a `MainContext` |
| `PlanConfirmation.RequestMainContext` | La conferma è deterministica (`format_plan_confirmation`) |
| `PlanConfirmation.RequestGenerator` | Come sopra |
| `ResponseClassifier.ClassifierContext` | La classificazione avviene in `ResponseClassifier._llm_classify` con prompt inline |
| `ResponseClassifier.ZeroShotClassifier` | Come sopra |
| `StepCheckpoint.CheckpointContext` | `_classify_checkpoint_response` delega a `classify_plan_response` |
| `StepCheckpoint.CheckpointClassifier` | Come sopra |

`StepCheckpoint.stable()` è invece usato correttamente come messaggio user-facing (non invocazione LLM) — va mantenuto.

---

## Architettura target dei prompt

### Principio di separazione

```
SystemMessage  → "Chi sono e cosa NON faccio"  (stabile tra le invocazioni)
HumanMessage   → "Questi sono i dati e questo è il task specifico"  (dinamico)
```

### Schema di composizione per `_generate_plan`

```
[System: MainContext]          ← role declaration (slim, ~10 righe)
                               ← agent names + one-liner (NO descrizioni complete)
                               ← "do NOT / ONLY" constraints

[Human: CreatePlan             ← ## INPUT (request + layers)
     | IncrementalReplanning   ← ## TASK  (delta directives, non ripete il sistema)
     | TotalReplanning]        ← ## RULES (dipendenze, minimizzazione, goal format)
                               ← ## OUTPUT FORMAT
                               ← ## IMPORTANT
```

`context_str` (da `ContextBuilder`) viene **rimosso** dalla catena — il suo contenuto è già coperto dal messaggio Human.

### Schema di composizione per `_generate_plan_explanation`

```
[System: ExplainerMainContext]  ← role declaration espanso (You are / do NOT / ONLY)
[Human: RequestExplanation]     ← FormatRequest + _format_plan_readable + user_question
```

---

## Scope / File coinvolti

| File | Stato |
|---|---|
| `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py` | `todo` |
| `src/saferplaces_multiagent/ma/orchestrator/supervisor.py` | `todo` |

---

## Task

| ID | Descrizione | File | Priorità | Stato |
|---|---|---|---|---|
| T-016-01 | **DONE** BUG FIX `IncrementalReplanning.stable()` — `_format_parsed_request` → `RequestParserPrompts.FormatRequest.stable(state)` | prompts | CRITICA | `[x]` |
| T-016-02 | **DONE** BUG FIX `TotalReplanning.stable()` — stessa correzione di T-016-01 | prompts | CRITICA | `[x]` |
| T-016-03 | Ridisegnare `MainContext` come slim system prompt: role declaration + "do NOT/ONLY" + lista agenti (nome + one-liner). Rimuovere: planning policy, agent descriptions estese, goal writing rules, output format. | prompts | alta | `[ ]` |
| T-016-04 | Refactoring `CreatePlan`: rimuovere la re-dichiarazione del ruolo ("You are generating…") e le sezioni che ora vivono in `MainContext`; mantieni INPUT + TASK + RULES + OUTPUT FORMAT + IMPORTANT. Le regole di pianificazione rimangono QUI (nel Human message), non nel system. | prompts | alta | `[ ]` |
| T-016-05 | Rimuovere `context_message = HumanMessage(content=context_str)` dalla catena in `_generate_plan` — il contenuto è ridondante con ciò che `CreatePlan` / `IncrementalReplanning` / `TotalReplanning` iniettano. | supervisor.py | alta | `[ ]` |
| T-016-06 | Refactoring `IncrementalReplanning`: struttura INPUT (FormattedRequest + CurrentPlan + Layers + UserFeedback) + TASK (solo delta; non ripetere policy già in `MainContext`) + OUTPUT FORMAT + IMPORTANT. | prompts | alta | `[ ]` |
| T-016-07 | Refactoring `TotalReplanning`: stessa struttura di T-016-06 con TASK per replanning totale. | prompts | alta | `[ ]` |
| T-016-08 | Rimuovere `PlannerContext` — mai usato, contenuto quasi identico a `MainContext`. | prompts | media | `[ ]` |
| T-016-09 | Rimuovere i prompt morti: `PlanConfirmation.RequestMainContext`, `PlanConfirmation.RequestGenerator`, `ResponseClassifier.ClassifierContext`, `ResponseClassifier.ZeroShotClassifier`, `StepCheckpoint.CheckpointContext`, `StepCheckpoint.CheckpointClassifier`. Mantenere `StepCheckpoint.stable()` e `PlanConfirmation.ResponseClassifier.PLAN_RESPONSE_LABELS`. | prompts | media | `[ ]` |
| T-016-10 | Refactoring `PlanExplanation`: `ExplainerMainContext` espande con role declaration (You are / do NOT / ONLY); `RequestExplanation` sostituisce `parsed_request` raw dict con `FormatRequest` e piano raw con `_format_plan_readable`. | prompts | media | `[ ]` |
| T-016-11 | Cleanup: rimuovere il blocco commentato `_format_parsed_request` dal file (già rimosso funzionalmente con T-016-01/02). | prompts | bassa | `[ ]` |

---

## Ordine di esecuzione raccomandato

```
T-016-01 + T-016-02  (già completati)

T-016-03 + T-016-04  (atomici: slim MainContext + adattamento CreatePlan — devono essere coerenti)
T-016-05             (dipende da T-016-04 — rimuovere context_message solo dopo che CreatePlan è completo)

T-016-06 + T-016-07  (IncrementalReplanning + TotalReplanning — dopo T-016-03 per non re-dichiarare il ruolo)

T-016-08             (rimozione PlannerContext — indipendente)
T-016-09             (rimozione dead prompts — indipendente)
T-016-10             (PlanExplanation — indipendente)
T-016-11             (cleanup finale)
```

---

## Note implementative

### T-016-03: `MainContext` target

Il system prompt deve essere stabile e compatto. Le regole operative (planning procedure, dependency rules, minimization) appartengono al Human message perché sono task-specific.

Struttura target:
```
You are the orchestration agent of a multi-agent geospatial AI platform.
You do NOT execute tools / simulate outputs / produce tool-call arguments.
You ONLY plan and delegate goals to specialized agents.

Available specialized agents:
- models_subgraph: flood simulations, digital twin, impact analysis
- retriever_subgraph: meteorological and environmental data retrieval  
- map_agent: map visualization and layer interaction
```

### T-016-04: `CreatePlan` target

Il Human message porta tutta la logica operativa. Non ri-dichiarare il ruolo (già nel system).

Struttura target:
```
## INPUT
### Parsed Request
...
### Available Layers
...

## TASK
Generate a minimal execution plan as a sequence of delegated steps.

## PLANNING RULES       ← dependency order, agent selection, minimization, goal format
...

## OUTPUT FORMAT
...

## IMPORTANT
Return ONLY the plan.
```

### T-016-05: rimozione `context_message`

In `_generate_plan`, rimuovere `context_message` dall'array `messages` e la sua costruzione. Verificare che `CreatePlan`, `IncrementalReplanning`, `TotalReplanning` coprono tutti i campi che `ContextBuilder.format_for_prompt` produceva: request intent, layer summary, previous results, conversation history.

Il campo `previous_results_summary` di `ContextBuilder` potrebbe non essere coperto da `CreatePlan` attuale — verificare prima di rimuovere.

### T-016-06/07: replanning prompts

Dopo T-016-03, il system già dichiara il ruolo e i vincoli. I prompt di replanning devono portare solo:
- Il delta rispetto a `CreatePlan` (cosa c'è di diverso: piano esistente, feedback utente)
- NON ripetere: "you are the orchestration agent", planning policy, agent descriptions

### T-016-09: prompt da rimuovere vs. tenere

Mantenere nella classe `PlanConfirmation.ResponseClassifier`:
- `PLAN_RESPONSE_LABELS` — usato da `ResponseClassifier` (verificare prima di rimuovere)

Rimuovere le classi:
- `PlanConfirmation.RequestMainContext`
- `PlanConfirmation.RequestGenerator`
- `ResponseClassifier.ClassifierContext`
- `ResponseClassifier.ZeroShotClassifier`
- `StepCheckpoint.CheckpointContext`
- `StepCheckpoint.CheckpointClassifier`

---

## Acceptance Criteria

- [ ] SC-016-01 — Nessuna chiamata a `_format_parsed_request` nel file (T-016-01/02 già soddisfatti)
- [ ] SC-016-02 — `MainContext` ≤ 20 righe; contiene solo role declaration + agent names, nessuna planning policy
- [ ] SC-016-03 — `CreatePlan`, `IncrementalReplanning`, `TotalReplanning` non ri-dichiarano il ruolo ("You are…")
- [ ] SC-016-04 — `context_message` rimosso da `_generate_plan`; nessuna regressione nei dati di input disponibili all'LLM
- [ ] SC-016-05 — Nessun prompt dead code nel file (rimossi tutti i prompt in tabella "inutilizzati")
- [ ] SC-016-06 — `PlanExplanation.RequestExplanation` usa `FormatRequest` e `_format_plan_readable` (no raw dict dump)
- [ ] SC-016-07 — `ruff check` passa senza errori E/F/I su entrambi i file modificati
