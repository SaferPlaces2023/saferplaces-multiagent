# PLN-011 — Graph Bug Fixes & Robustezza Conversazionale

> **Dipendenze:** PLN-008 (`plan_aborted`), PLN-009 (step-checkpoint)
> **Branch:** main
> **Functional spec di riferimento:** [`docs/functional-spec-graph.md`](../docs/functional-spec-graph.md)

---

## Obiettivo

Correggere i difetti aperti identificati in `docs/functional-spec-graph.md` (sezione G007)
post PLN-008/PLN-009. I fix coprono quattro aree:

1. **Bug critici immediati** (D11, D14) — comportamenti errati su percorsi già attivi in produzione
2. **Error handling** (D16) — assenza totale di gestione eccezioni nell'esecuzione dei tool
3. **Contesto conversazionale** (D12, D13) — agenti e supervisor non vedono la cronologia dei messaggi
4. **Robustezza loop e cleanup** (D6, D8, D15) — guardie difensive, limite replan, dead code

---

## Scope / File coinvolti

| File | Difetti | Stato |
|------|---------|-------|
| `ma/specialized/safercast_agent.py` | D11, D14, D16 | ✅ done |
| `ma/specialized/models_agent.py` | D11, D14, D16 | ✅ done |
| `ma/orchestrator/supervisor.py` | D15 | ✅ done |
| `ma/prompts/safercast_agent_prompts.py` | D12 | ✅ done |
| `ma/prompts/models_agent_prompts.py` | D12 | ✅ done |
| `ma/prompts/supervisor_agent_prompts.py` | D13 | ✅ done |
| `common/states.py` | D6, D15 (nuovo campo `replan_iteration_count`) | ✅ done |

---

## Task

| ID | Difetto | Descrizione | Priorità |
|----|---------|-------------|----------|
| T-011-01 | D11 | In `_handle_no_tool_calls()` di entrambi gli agenti: `state["messages"]` deve ricevere una lista `[invocation]`, non il singolo oggetto `invocation` | 🔴 Alta |
| T-011-02 | D14 | Eliminare il double-increment di `current_step` nel percorso no-tool-calls: rimuovere `state["current_step"] += 1` da `_handle_no_tool_calls()` nell'Agent; conservare solo l'incremento nell'Executor | 🔴 Alta |
| T-011-03 | D16 | Wrappare `tool._execute(**tool_args)` in try/except in `DataRetrieverExecutor._execute_tool_call()` e `ModelsExecutor._execute_tool_call()`. In caso di eccezione: costruire un `ToolMessage` con contenuto di errore, registrare il fallimento in `tool_results`, continuare senza crashare il grafo | 🔴 Alta |
| T-011-04 | D12 | Modificare `InitialRequest.stable(state)` e `ReinvocationRequest.stable(state)` in entrambi i prompt degli agenti specializzati (SaferCast, Models) per includere gli ultimi N messaggi della conversazione (N ≤ 5, configurabile) come contesto aggiuntivo | 🟡 Media |
| T-011-05 | D13 | Modificare `CreatePlan.stable(state)` e `IncrementalReplanning.stable(state)` in `supervisor_agent_prompts.py` per includere gli ultimi N messaggi della conversazione come contesto di pianificazione | 🟡 Media |
| T-011-06 | D15 | Aggiungere `replan_iteration_count: Optional[int]` a `MABaseGraphState`; aggiornare `StateManager.initialize_new_cycle()` e `cleanup_on_final_response()`; incrementarlo in `_handle_modify()` e `_handle_reject()`; aggiungere limite (es. 5) in `_generate_plan()` con fallback su abort verso `FINAL_RESPONDER` | ⚪ Bassa |
| T-011-07 | D6 | Rimuovere da `_determine_next_node()` il blocco commentato `awaiting_user → END`; se il campo non ha uso futuro documentato, rimuoverlo dal TypedDict `MABaseGraphState` e da `StateManager` | ⚪ Bassa |
| T-011-08 | D8 | Verificare che il guard `current_step is None → 0` esistente sia sufficiente post-D14-fix; se ridondante, rimuoverlo; altrimenti mantenerlo documentato | ⚪ Bassa |

---

## Acceptance Criteria

| ID | Criterio verificabile |
|----|----------------------|
| SC-011-01 | Nel percorso no-tool-calls, `state["messages"]` riceve sempre una lista — verificabile con test unitario su `_handle_no_tool_calls` |
| SC-011-02 | Nel percorso no-tool-calls, `current_step` viene incrementato esattamente una volta — verificabile tracciando il valore prima e dopo il ciclo Agent → Confirm → Executor |
| SC-011-03 | Un'eccezione sollevata da `tool._execute()` non propaga fuori dall'executor; il grafo continua; `tool_results` contiene una chiave `error` per lo step fallito |
| SC-011-04 | I prompt degli agenti specializzati includono almeno l'ultimo messaggio utente rilevante; una richiesta di follow-up come "usa la stessa area del turno precedente" viene gestita correttamente |
| SC-011-05 | Il supervisor genera un piano coerente per richieste di follow-up senza che il parser debba sintetizzare tutto il contesto in `intent` (es. "fai lo stesso per domani" → piano con data corretta) |
| SC-011-06 | Dopo 5 cicli modify/reject consecutivi, il sistema produce una risposta finale invece di ciclare indefinitamente |
| SC-011-07 | Nessun riferimento a `awaiting_user` come punto di controllo del flusso rimane nel codice senza commento esplicito sul suo scopo futuro |

---

## Note / Rischi

### T-011-01 e T-011-02 — Atomicità

Le due fix (D11 e D14) devono essere applicate insieme nella stessa modifica a `_handle_no_tool_calls()`,
altrimenti il codice intermedio tra le due fix è in uno stato inconsistente: lista corretta ma
double-increment ancora presente.

### T-011-03 — Strategia error handling (decisione aperta)

Prima di implementare, scegliere il comportamento in caso di errore:

- **Opzione A — Continua** (consigliata): il tool si considera "eseguito con errore", il `ToolMessage`
  contiene la descrizione dell'eccezione, `tool_results` registra `{status: "error", message: "..."}`,
  `current_step` avanza normalmente. Il `FINAL_RESPONDER` spiega il fallimento all'utente.
- **Opzione B — Interrupt**: emette `interrupt({interrupt_type: "tool-error"})` con il messaggio
  di errore, chiedendo all'utente come procedere. Introduce un nuovo `interrupt_type` non ancora
  gestito dal frontend.
- **Opzione C — Abort step**: salta il passo silenziosamente, lascia la spiegazione al `FINAL_RESPONDER`.

L'Opzione A è la meno invasiva sulla topologia del grafo e non richiede modifiche al frontend.

### T-011-04 e T-011-05 — Numero di messaggi da includere

L'inclusione di `state["messages"]` aumenta il token count per ogni invocazione LLM.
Raccomandazione: usare `state["messages"][-N:]` con `N=5` come default, reso configurabile
tramite parametro del nodo (es. `context_window_size: int = 5`).
Filtrare solo `HumanMessage` e `AIMessage` senza `tool_calls` (escludere `ToolMessage` — verbose
e non utili al contesto conversazionale).

### T-011-06 — `replan_iteration_count` nello stato

Il nuovo campo va aggiunto al TypedDict `MABaseGraphState` con tipo `Optional[int]`.
Va incluso nelle operazioni di `StateManager`:

- `initialize_new_cycle()`: reset a `0`
- `cleanup_on_final_response()`: reset a `0`
- Non viene toccato da `initialize_specialized_agent_cycle()`

### T-011-08 — Guard post-fix

Dopo T-011-02, `current_step` è garantito non-None prima dell'Executor perché
`initialize_specialized_agent_cycle()` lo imposta a `0` nel router (post-D3 risolto).
Il guard in `_handle_no_tool_calls()` diventa ridondante e può essere rimosso.

### Ordine di implementazione consigliato

```
T-011-01 + T-011-02  (atomici — stesso metodo)
T-011-03             (executor error handling)
T-011-07             (dead code cleanup)
T-011-08             (verifica guard)
T-011-04             (prompt agenti — richiede test empirico)
T-011-05             (prompt supervisor — richiede test empirico)
T-011-06             (stato + looping limit)
```

I task 01–03 sono fix puri: zero impatto su prompt e topologia.
I task 04–05 modificano i prompt: richiedono validazione empirica end-to-end.
Il task 06 tocca lo stato: aggiornamenti in più file.
