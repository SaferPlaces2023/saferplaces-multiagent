# PLN-007 — Correzioni Triviali Grafo (D2, D3, D8, D9, D10)

> **Dipendenze:** nessuna
> **Blocca:** PLN-008 (D3 deve essere risolto prima per coerenza con i nomi delle chiavi)
> **Feature modificata:** [`docs/functional-spec-graph.md`](../docs/functional-spec-graph.md) — G007 (D2, D3, D8, D9, D10)

---

## Obiettivo

Correggere cinque difetti a bassa o media rischio presenti nel grafo multi-agente. Nessuno di questi difetti richiede nuovi campi nello stato né modifiche alla topologia del grafo. Sono tutti riducibili a sostituzioni di una o due righe in file esistenti. L'ordine di esecuzione rispetta le dipendenze interne: D3 prima di D8 (D8 è aggravato da D3).

---

## File coinvolti

| File | Difetti | Descrizione intervento |
|---|---|---|
| `src/saferplaces_multiagent/common/states.py` | D3 | Correggere `{prefix}_confirmation` → `{prefix}_invocation_confirmation` in `_clear_specialized_agent_state()` e `initialize_specialized_agent_cycle()` |
| `src/saferplaces_multiagent/ma/specialized/safercast_agent.py` | D8 | Aggiungere guard `if state.get("current_step") is not None` prima di `current_step += 1` in `_handle_no_tool_calls()` |
| `src/saferplaces_multiagent/ma/specialized/models_agent.py` | D8 | Stessa guard di cui sopra per `ModelsAgent._handle_no_tool_calls()` |
| `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py` | D2 | Sostituire `state.get("plan_additional_context", ...)` con la lettura corretta da `state["additional_context"]` |
| `src/saferplaces_multiagent/ma/orchestrator/supervisor.py` | D9, D10 | D9: aggiungere chiamata a `_auto_confirm()` se `not plan` in `SupervisorPlannerConfirm.run()`; D10: convertire il secondo argomento del classifier da `SystemMessage` a `HumanMessage` |

---

## Task

| ID | Scope | Sezione |
|---|---|---|
| T-007-01 | **D3** — Correggere le chiavi di stato in `StateManager._clear_specialized_agent_state()` e `initialize_specialized_agent_cycle()` in `states.py` | §1 |
| T-007-02 | **D8** — Aggiungere guard su `current_step is None` in `_handle_no_tool_calls()` di `safercast_agent.py` e `models_agent.py` | §2 |
| T-007-03 | **D2** — Correggere la lettura del contesto layer in `CreatePlan.stable()` di `supervisor_agent_prompts.py` | §3 |
| T-007-04 | **D9** — Aggiungere `_auto_confirm()` per piano vuoto in `SupervisorPlannerConfirm.run()` di `supervisor.py` | §4 |
| T-007-05 | **D10** — Convertire il secondo prompt del classifier in `HumanMessage` in `_classify_user_response()` di `supervisor.py` | §5 |

---

## §1 — D3: Chiave errata in StateManager

### Problema

`StateManager._clear_specialized_agent_state()` e `initialize_specialized_agent_cycle()` scrivono `state[f'{prefix}_confirmation'] = None`. La chiave reale nel TypedDict `MABaseGraphState` è `{prefix}_invocation_confirmation` (per esempio `retriever_invocation_confirmation`). La chiave scritta non esiste e non viene mai letta: il cleanup è silenziosamente inefficace.

### Fix

In `common/states.py`, nei due metodi citati, sostituire ogni occorrenza di `f'{prefix}_confirmation'` con `f'{prefix}_invocation_confirmation'`.

La modifica è circonscritta alle due righe che coinvolgono la chiave di conferma in ciascun metodo. Nessun altro metodo di `StateManager` è coinvolto.

---

## §2 — D8: Guard su `current_step` in `_handle_no_tool_calls`

### Problema

In `DataRetrieverAgent._handle_no_tool_calls()` e `ModelsAgent._handle_no_tool_calls()`, l'istruzione `state["current_step"] += 1` non verifica se `current_step` è `None`. Se D3 non ha ancora azzerato correttamente il valore (oppure in scenari di re-entry inattesi), si ottiene `TypeError`.

### Fix

Aggiungere una guard esplicita: se `state.get("current_step") is None`, inizializzare `current_step = 0` prima di incrementarlo, oppure gestire il caso come errore di stato con un log di warning.

La guard deve essere applicata identicamente in entrambi i file (`safercast_agent.py` e `models_agent.py`).

---

## §3 — D2: Lettura contesto layer in `CreatePlan.stable()`

### Problema

`OrchestratorPrompts.Plan.CreatePlan.stable(state)` in `supervisor_agent_prompts.py` legge:
```python
additional_context = state.get("plan_additional_context", "No additional context available")
```
La chiave `"plan_additional_context"` non esiste in `MABaseGraphState` e non viene mai scritta. Il contesto dei layer geospaziali — aggiornato da `SupervisorRouter._update_additional_context()` — è in `state["additional_context"]["relevant_layers"]`.

### Fix

Sostituire la lettura con un accesso corretto alla struttura annidata:
```python
layers = state.get("additional_context", {}).get("relevant_layers", {}).get("layers", [])
```
La variabile `layers` (o un formato stringa derivato) va poi passata al template del prompt come contesto di planning.

Il fix è confinato al metodo `CreatePlan.stable()` in `supervisor_agent_prompts.py`. Non si toccano altri metodi del modulo.

---

## §4 — D9: Auto-confirm per piano vuoto con `enabled=True`

### Problema

Con `SupervisorPlannerConfirm(enabled=True)`, se il piano generato è vuoto, `run()` ritorna immediatamente senza impostare `plan_confirmation`. La chiave rimane `"pending"`. Il routing verso `FINAL_RESPONDER` funziona ugualmente (perché `not plan` in `_determine_next_node` ha priorità), ma lo stato è logicamente inconsistente durante il ciclo.

### Fix

All'inizio di `SupervisorPlannerConfirm.run()`, aggiungere il controllo:
```
if not plan → _auto_confirm() e ritorna
```
In questo modo il comportamento con `enabled=True` e piano vuoto è identico a `enabled=False`: `plan_confirmation` viene portato a `"accepted"` prima di proseguire.

---

## §5 — D10: Secondo messaggio del classifier come `HumanMessage`

### Problema

In `_classify_user_response()` di `supervisor.py`, la lista di messaggi passata all'LLM contiene due `SystemMessage`. Molti provider LangChain/LLM accettano un solo `SystemMessage` iniziale; i successivi possono essere ignorati, aggregati o trattati in modo non deterministico.

### Fix

Convertire il secondo elemento della lista — `ZeroShotClassifier.stable(user_response).to(SystemMessage)` — in `HumanMessage`. Il primo elemento (contesto del classificatore) rimane `SystemMessage`:

```diff
 messages = [
     ClassifierContext.stable().to(SystemMessage),
-    ZeroShotClassifier.stable(user_response).to(SystemMessage)
+    ZeroShotClassifier.stable(user_response).to(HumanMessage)
 ]
```

Verificare che `HumanMessage` sia importato nel modulo (di solito già presente).

---

## Acceptance Criteria

- [ ] SC-007-01 — Dopo un ciclo completo con agente retriever, `state["retriever_invocation_confirmation"]` è `None` al termine del ciclo (non esiste una chiave `retriever_confirmation` scritta)
- [ ] SC-007-02 — `_handle_no_tool_calls()` non lancia `TypeError` quando `current_step` è `None` all'ingresso
- [ ] SC-007-03 — Il prompt di `CreatePlan` riceve la lista dei layer disponibili quando `additional_context` è popolato, invece di `"No additional context available"`
- [ ] SC-007-04 — Con `enabled=True` e piano vuoto, `plan_confirmation` vale `"accepted"` subito dopo `SupervisorPlannerConfirm.run()`
- [ ] SC-007-05 — Il secondo messaggio nel classifier è `HumanMessage`; nessuna regressione nei test di classificazione esistenti

---

## Note

- I fix D3 e D8 sono interdipendenti: risolvere D3 prima garantisce che D8 si manifesti meno facilmente in produzione, anche se entrambe le fix sono indipendenti a livello di codice.
- Non sono necessari nuovi campi in `MABaseGraphState` per questo piano.
- Non sono necessari file in `PLN-007-files/`: tutte le modifiche sono singole righe o guard in file esistenti già identificati.
- Dopo il completamento di questo piano, aggiornare G007 in `docs/functional-spec-graph.md` contrassegnando D2, D3, D8, D9, D10 come risolti con `Implementata con: PLN-007`.
