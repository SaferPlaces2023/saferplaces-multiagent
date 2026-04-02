# PLN-016 — Refactor Prompts `supervisor_agent_prompts.py`

**Dipendenze:** PLN-012 (prompt refactor base)
**Branch:** `refactor/pln-016-supervisor-prompts`
**File target:** `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py`

---

## Obiettivo

Correggere un bug critico e allineare tutti i prompt dopo la riga 251 del file `supervisor_agent_prompts.py` al pattern di composizione stabilito nella prima parte del file (righe 1–251).

Il pattern di riferimento è `CreatePlan.stable()`: role declaration → `## INPUT` con blocchi formattati → `## [TASK] PROCEDURE` → `## [DOMAIN] RULES` → `## OUTPUT FORMAT` → `## IMPORTANT`.

---

## Scope / File coinvolti

| File | Stato |
|---|---|
| `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py` | `todo` |

---

## Task

| ID | Descrizione | Priorità | Stato |
|---|---|---|---|
| T-016-01 | **BUG FIX** `IncrementalReplanning.stable()` — sostituire `_format_parsed_request()` (commentata, genera `AttributeError`) con `RequestParserPrompts.FormatRequest.stable(state)` | CRITICA | `[ ]` |
| T-016-02 | **BUG FIX** `TotalReplanning.stable()` — stessa correzione di T-016-01 | CRITICA | `[ ]` |
| T-016-03 | Refactor `IncrementalReplanning.stable()` — aggiungere struttura INPUT / PROCEDURE / RULES / OUTPUT FORMAT / IMPORTANT | alta | `[ ]` |
| T-016-04 | Refactor `TotalReplanning.stable()` — aggiungere struttura INPUT / PROCEDURE / RULES / OUTPUT FORMAT / IMPORTANT | alta | `[ ]` |
| T-016-05 | Refactor `PlanExplanation.ExplainerMainContext.stable()` — espandere con role declaration completa (You are / You do NOT / You ONLY) | media | `[ ]` |
| T-016-06 | Refactor `PlanExplanation.RequestExplanation.stable()` — eliminare dump raw di `parsed_request` dict, usare `FormatRequest`, aggiungere `_format_plan_readable`, struttura INPUT / TASK / RULES | media | `[ ]` |
| T-016-07 | Refactor `PlanConfirmation.RequestMainContext.stable()` — espandere con role declaration completa | media | `[ ]` |
| T-016-08 | Refactor `PlanConfirmation.RequestGenerator.stable()` — aggiungere struttura INPUT / TASK / REQUIREMENTS / OUTPUT FORMAT / IMPORTANT | media | `[ ]` |
| T-016-09 | Refactor `ResponseClassifier.ClassifierContext.stable()` — espandere con role declaration completa + regola tiebreak | media | `[ ]` |
| T-016-10 | Refactor `ResponseClassifier.ZeroShotClassifier.stable()` — sostituire `json.dumps` labels con blocco formattato leggibile, struttura INPUT / LABELS / TASK / OUTPUT FORMAT / IMPORTANT | media | `[ ]` |
| T-016-11 | Refactor `StepCheckpoint.CheckpointContext.stable()` — espandere con role declaration completa + regola tiebreak (prefer continue) | media | `[ ]` |
| T-016-12 | Refactor `StepCheckpoint.CheckpointClassifier.stable()` — stessa struttura di T-016-10 | media | `[ ]` |
| T-016-13 | Refactor `StepCheckpoint.stable()` — sezioni `## COMPLETED STEP` / `## RESULT SUMMARY` / `## REMAINING STEPS` / `## DECISION REQUIRED` | media | `[ ]` |
| T-016-14 | Rimuovere o spostare il metodo commentato `_format_plan_readable` — verificare se usato, potrebbe restare ma va ripulita l'area con il blocco `_format_parsed_request` commentato sopra | bassa | `[ ]` |

---

## Ordine di esecuzione raccomandato

```
T-016-01 → T-016-02          (bug fix critici, priorità assoluta)
T-016-03 → T-016-04          (replanning — stessa area logica)
T-016-05 → T-016-06          (PlanExplanation — stessa classe)
T-016-07 → T-016-08          (PlanConfirmation.Request — stessa classe)
T-016-09 → T-016-10          (ResponseClassifier — stessa classe)
T-016-11 → T-016-12 → T-016-13  (StepCheckpoint — stessa classe)
T-016-14                     (cleanup finale)
```

---

## Note implementative

- Tutti i prompt modificati restano come metodo `stable()` — non rinominarli
- Per i prompt `state`-aware usare sempre `_get_conversation_context(state)` e `RequestParserPrompts.FormatRequest.stable(state)` come già fatto in `CreatePlan`
- Per la formattazione del piano usare `OrchestratorPrompts.Plan._format_plan_readable()` già presente
- I classifier `ZeroShotClassifier` e `CheckpointClassifier` devono avere struttura identica (stesso pattern, labels diverse)
- `StepCheckpoint.stable()` è il message shown to the user — NON è un system prompt LLM, quindi tono diverso (imperativo → descrittivo)

---

## Acceptance Criteria

- [ ] SC-016-01 — Nessuna chiamata a `_format_parsed_request` nel file (metodo inesistente a runtime)
- [ ] SC-016-02 — Tutti i prompt `state`-aware usano `RequestParserPrompts.FormatRequest.stable(state)` o `LayersAgentPrompts.LayerSummary.with_geospatial_metadata(state)` per i dati di input
- [ ] SC-016-03 — Tutti i prompt LLM (system + user) seguono il pattern: role declaration → INPUT → PROCEDURE/TASK → RULES → OUTPUT FORMAT → IMPORTANT
- [ ] SC-016-04 — I due classifier (`ZeroShotClassifier`, `CheckpointClassifier`) hanno struttura speculare
- [ ] SC-016-05 — `ruff check` passa senza errori E/F/I sul file modificato
