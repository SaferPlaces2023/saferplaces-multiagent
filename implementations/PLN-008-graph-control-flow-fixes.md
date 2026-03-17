# PLN-008 — Correzioni Flusso di Controllo (D1, D4, D5, D6)

> **Dipendenze:** PLN-007 (D3 deve essere risolto prima — i nomi delle chiavi di stato devono essere corretti per coerenza con i metodi `StateManager` che vengono modificati qui)
> **Blocca:** PLN-009
> **Feature modificata:** [`docs/functional-spec-graph.md`](../docs/functional-spec-graph.md) — G007 (D1, D4, D5, D6), G002 (topologia supervisor subgraph), G004 (mappa mutazioni), G009 (nuova chiave `plan_aborted`)

---

## Obiettivo

Correggere quattro difetti che riguardano il flusso di controllo del grafo: un bug di routing critico (D1) che fa reiterare il supervisor dopo un abort, un problema di ordinamento dei messaggi nel `FinalResponder` (D4), un pattern di ricorsione instabile con LangGraph checkpoint (D5), e dead code nel `SupervisorRouter` (D6).

Il difetto D1 richiede l'introduzione di un nuovo campo nello stato (`plan_aborted: bool`) e la modifica dell'arco condizionale del supervisor subgraph. Gli altri tre difetti restano confinati a singoli metodi.

---

## File coinvolti

| File | Difetti | Descrizione intervento |
|---|---|---|
| `src/saferplaces_multiagent/common/states.py` | D1 | Aggiungere `plan_aborted: bool` a `MABaseGraphState`; inizializzare a `False` in `initialize_new_cycle()`; azzerare in `cleanup_on_final_response()` |
| `src/saferplaces_multiagent/multiagent_graph.py` | D1 | Modificare l'arco condizionale del supervisor subgraph per escludere il re-enter su `SUPERVISOR_AGENT` quando `plan_aborted == True` |
| `src/saferplaces_multiagent/ma/orchestrator/supervisor.py` | D1, D5, D6 | D1: `_handle_abort()` imposta `plan_aborted = True`; `_should_skip_planning()` legge `plan_aborted`; D5: sostituire ricorsione in `_handle_clarify()` con loop iterativo; D6: rimuovere o documentare il ramo `awaiting_user == True → END` |
| `src/saferplaces_multiagent/ma/chat/final_responder.py` | D4 | Riordinare la lista `invoke_messages`: `SystemMessage` di prompt e contesto prima di `state["messages"]` |

---

## Task

| ID | Scope | Sezione |
|---|---|---|
| T-008-01 | **D1** — Aggiungere `plan_aborted` a `MABaseGraphState`, aggiornare `StateManager`, modificare `_handle_abort()` e `_should_skip_planning()` in `supervisor.py`, aggiornare l'arco condizionale in `multiagent_graph.py` | §1 |
| T-008-02 | **D4** — Correggere l'ordinamento di `invoke_messages` in `FinalResponder.run()` di `final_responder.py` | §2 |
| T-008-03 | **D5** — Sostituire la ricorsione di `_handle_clarify()` con un loop iterativo in `supervisor.py` | §3 |
| T-008-04 | **D6** — Rimuovere o annotare il dead code `awaiting_user → END` in `_determine_next_node()` di `supervisor.py` | §4 |

---

## §1 — D1: Flag `plan_aborted` e arco condizionale

### Problema

`SupervisorPlannerConfirm._handle_abort()` imposta `plan_confirmation = "rejected"` come segnale di fine operazione. L'arco condizionale del supervisor subgraph (in `build_supervisor_subgraph()` di `multiagent_graph.py`) valuta `plan_confirmation == "rejected"` → `SUPERVISOR_AGENT`. Il re-enter in `SUPERVISOR_AGENT` con `replan_type = None` causa un `TotalReplanning`, ignorando la richiesta di annullamento e chiedendo di nuovo conferma all'utente.

### Struttura della soluzione

Il fix introduce un flag booleano dedicato `plan_aborted` che distingue "rifiuto per modifica/replan" da "abort per cancellazione":

**Modifica a `MABaseGraphState` (in `states.py`):**
Aggiungere il campo:
```
plan_aborted: bool
```
con valore iniziale `False`.

**Modifica a `StateManager` (in `states.py`):**
- `initialize_new_cycle()`: imposta `plan_aborted = False`
- `cleanup_on_final_response()`: imposta `plan_aborted = False`

**Modifica a `supervisor.py`:**
- `_handle_abort()`: oltre a `plan = []`, impostare `plan_aborted = True`. Non toccare `plan_confirmation` — lasciarlo come effetto secondario (o spiegare nel commento perché viene ancora impostato se necessario per compatibilità).
- `_should_skip_planning()`: aggiungere come prima condizione di skip `state.get("plan_aborted") == True`. Questo impedisce al `SUPERVISOR_AGENT` di ripianificare se l'utente ha abortito.

**Modifica all'arco condizionale del supervisor subgraph (in `multiagent_graph.py`):**
L'arco che valuta `plan_confirmation == "rejected"` deve essere esteso per escludere il re-enter quando `plan_aborted == True`:

```diff
- lambda state: state.get("plan_confirmation") == "rejected"
+ lambda state: state.get("plan_confirmation") == "rejected" and not state.get("plan_aborted")
```

In questo modo, con `plan_aborted = True`, l'arco non porta a `SUPERVISOR_AGENT` e il subgraph termina normalmente, lasciando che `SUPERVISOR_ROUTER` instradi verso `FINAL_RESPONDER` (perché `plan` è vuoto).

### Impatto sulla tabella G009

Aggiungere la riga `plan_aborted` nella tabella delle chiavi di stato in `functional-spec-graph.md`:

| Chiave | Tipo | Fase | Persistenza |
|---|---|---|---|
| `plan_aborted` | `bool` | `SUPERVISOR_PLANNER_CONFIRM` → `SUPERVISOR_SUBGRAPH` | Ciclo |

### Impatto su G002 (topologia supervisor subgraph)

Il diagramma del supervisor subgraph in G002 va aggiornato per mostrare che l'arco condizionale `plan_confirmation == "rejected"` ha ora una condizione composta (`plan_confirmation == "rejected" AND NOT plan_aborted`).

---

## §2 — D4: Ordinamento messaggi in `FinalResponder.run()`

### Problema

`FinalResponder.run()` costruisce:
```
invoke_messages = [*state["messages"], SystemMessage(response_prompt), AIMessage(context)]
```
Il `SystemMessage` con le istruzioni di risposta arriva dopo l'intera cronologia, posizione anomala per i provider LLM che si aspettano i messaggi di sistema in testa. Il contesto strutturato viene iniettato come `AIMessage`, che il modello tratta come un proprio messaggio precedente, potenzialmente causando allucinazioni o incoerenze.

### Fix

Riordinare la lista mettendo le istruzioni di sistema in testa:
```
invoke_messages = [SystemMessage(response_prompt), SystemMessage(context), *state["messages"]]
```
Entrambi i messaggi di sistema vanno in posizione iniziale, prima della cronologia conversazionale. Il contesto viene promosso da `AIMessage` a `SystemMessage`.

Verificare che la variabile `context` (prodotta da `FinalResponderPrompts.Context.Formatted.stable(state)`) sia compatibile con un `SystemMessage` in termini di lunghezza e formattazione.

---

## §3 — D5: Loop iterativo in `_handle_clarify()`

### Problema

`SupervisorPlannerConfirm._handle_clarify()` chiama se stessa ricorsivamente. A ogni iterazione viene emesso un nuovo `interrupt()`. LangGraph serializza il checkpoint nel mid-frame della chiamata: al resume, LangGraph ri-esegue il frame ma la ricorsione non viene ripristinata correttamente, potendo causare un frame errato o una `RecursionError` non catturata.

### Fix

Trasformare il metodo in un loop `while` esplicito con un contatore. La struttura logica è:

```
while clarify_count < max_clarify_iterations:
    genera spiegazione via LLM
    emetti interrupt con spiegazione
    attendi risposta utente
    riclassifica risposta
    if intent != "clarify":
        dispatch all'handler corretto
        return
    clarify_count += 1

# fallback dopo il limite
_handle_accept(state)
```

Il contatore deve essere letto e scritto su `state["clarify_iteration_count"]` (già presente in `MABaseGraphState`). Non è necessario introdurre nuovi campi.

Un solo `interrupt()` per iterazione, non annidato all'interno di una chiamata ricorsiva.

---

## §4 — D6: Rimozione dead code `awaiting_user → END`

### Problema

In `SupervisorRouter._determine_next_node()`:
```python
if state.get("awaiting_user"):
    return "END"
```
Nessun nodo nel grafo imposta `awaiting_user = True` in modo stabile prima di questo punto. Il ramo non viene mai eseguito.

### Fix

Due opzioni, entrambe accettabili — scegliere la più coerente con le intenzioni future del codice:

1. **Rimuovere il ramo** — se `awaiting_user` non ha finalità pianificate, eliminare le tre righe.
2. **Annotare come intenzionale** — aggiungere un commento esplicito che spiega il ruolo futuro del flag (es. `# Reserved for future mid-plan user interrupt — currently unused, see G007-D6`).

In entrambi i casi, aggiornare la nota su `awaiting_user` nella tabella G009 di `functional-spec-graph.md` per riflettere la decisione presa.

---

## Acceptance Criteria

- [ ] SC-008-01 — Con `enabled=True`, una risposta "annulla tutto" porta il grafo a `FINAL_RESPONDER` senza rientrare in `SUPERVISOR_AGENT`
- [ ] SC-008-02 — `state["plan_aborted"]` è `True` dopo `_handle_abort()` e `False` dopo `initialize_new_cycle()` e `cleanup_on_final_response()`
- [ ] SC-008-03 — `FinalResponder.run()` costruisce `invoke_messages` con `SystemMessage` in posizione 0 e 1, seguito da `state["messages"]`
- [ ] SC-008-04 — `_handle_clarify()` non usa ricorsione; il frame `interrupt()` è un unico livello per iterazione; nessuna `RecursionError` su 3 iterazioni consecutive di clarify
- [ ] SC-008-05 — Il ramo `awaiting_user → END` è rimosso o annotato con commento esplicito; la tabella G009 riflette la decisione
- [ ] SC-008-06 — I test esistenti per percorso 3 (modify), percorso 4 (reject), percorso 6 (clarify) non regrediscono

---

## Note e rischi

- **Rischio principale di D1:** l'arco condizionale in `multiagent_graph.py` è un'espressione lambda. Verificare che la sintassi della condizione composta sia corretta nella versione di LangGraph in uso (alcune versioni non supportano condizioni multi-arco con lambda).
- **D4 — lunghezza contesto:** se `context` è molto lungo, posizionarlo come `SystemMessage` prima della cronologia potrebbe superare la finestra di contesto del modello. Valutare se troncare o riassumere il contesto nel prompt.
- **D5 — contatore di ciclo:** `clarify_iteration_count` viene già azzerato da `StateManager`? Verificare che `initialize_new_cycle()` lo imposti a `0`.
- Il supervisor subgraph in G002 e la tabella G009 in `functional-spec-graph.md` devono essere aggiornati al completamento di questo piano.
