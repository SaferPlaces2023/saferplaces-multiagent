# PLN-009 — Checkpoint Interrupt Mid-Plan (D7)

> **Dipendenze:** PLN-008 (fix D1 dell'abort — il nuovo campo `plan_aborted` e il routing corretto del supervisor subgraph devono essere presenti prima di aggiungere un nuovo checkpoint nel `SUPERVISOR_ROUTER`)
> **Feature modificata:** [`docs/functional-spec-graph.md`](../docs/functional-spec-graph.md) — G007 (risolto D7), G001 (topologia grafo principale), G002 (topologia supervisor subgraph), G003 (descrizione `SUPERVISOR_ROUTER`), G008 (tabella interrupt points)

---

## Obiettivo

Introdurre un meccanismo di checkpoint interrupt nel `SUPERVISOR_ROUTER` che permetta all'utente di intervenire tra uno step e il successivo di un piano multi-step in esecuzione. Attualmente non esiste alcun punto di interruzione tra `{AGENT}_EXECUTOR` e il re-enter in `SUPERVISOR_SUBGRAPH`, rendendo impossibile per l'utente richiedere una modifica o un abort durante l'esecuzione.

Il checkpoint è opzionale (controllato da un flag costruttore, come tutti gli altri confirm del sistema) e non altera il percorso di esecuzione nominale quando disabilitato.

---

## Scope / File coinvolti

| File | Intervento | Stato |
|---|---|---|
| `src/saferplaces_multiagent/ma/orchestrator/supervisor.py` | Aggiungere logica checkpoint in `SupervisorRouter._determine_next_node()` | todo |
| `src/saferplaces_multiagent/multiagent_graph.py` | Nessuna modifica topologica — il checkpoint è interno al nodo `SUPERVISOR_ROUTER` già presente | ready |
| `src/saferplaces_multiagent/common/states.py` | Valutare se aggiungere `mid_plan_interrupt_enabled: bool` a `MABaseGraphState` o gestirlo solo come attributo del nodo | todo |
| `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py` | Aggiungere prompt per il messaggio di checkpoint (es. `OrchestratorPrompts.Plan.StepCheckpoint.stable(state, completed_step)`) | todo |
| `docs/functional-spec-graph.md` | Aggiornare G001, G002, G003, G007 (D7 risolto), G008 | todo |

---

## Task

| ID | Scope | Sezione |
|---|---|---|
| T-009-01 | **Spec** — Aggiornare `functional-spec-graph.md`: G001 (percorso con checkpoint), G003 (nuova logica `SUPERVISOR_ROUTER`), G008 (nuova riga interrupt `step-checkpoint`), G007-D7 (aggiornare stato a risolto) | §1 |
| T-009-02 | **Prompt** — Aggiungere `OrchestratorPrompts.Plan.StepCheckpoint.stable(state, completed_step)` in `supervisor_agent_prompts.py` | §2 |
| T-009-03 | **Implementazione** — Aggiungere logica checkpoint in `SupervisorRouter` con flag `enabled` e interrupt `"step-checkpoint"` | §3 |
| T-009-04 | **Test** — Verificare manualmente il percorso con checkpoint attivo: step 1 eseguito → checkpoint → utente risponde "stop" → FINAL_RESPONDER | §4 |

---

## §1 — Aggiornamento spec `functional-spec-graph.md`

Prima di implementare, aggiornare la documentazione:

- **G001:** Aggiungere una nota al diagramma del grafo principale che il `SUPERVISOR_ROUTER` può emettere un interrupt opzionale `"step-checkpoint"` tra un step e il successivo.
- **G003 — SUPERVISOR_ROUTER:** Espandere la logica di esecuzione con la nuova fase: dopo aver determinato il prossimo nodo (ma prima di impostare `supervisor_next_node`), se `enabled=True` e `current_step > 0` (non al primo step), emette interrupt `"step-checkpoint"` con il riepilogo dello step appena completato e il prossimo step pianificato. L'utente può rispondere `continue | abort` (classificato con `ZeroShotClassifier` già esistente).
- **G008:** Aggiungere la riga per `interrupt_type = "step-checkpoint"`.
- **G007 — D7:** Aggiornare il difetto come risolto con riferimento a PLN-009.

---

## §2 — Prompt per il checkpoint

Aggiungere in `OrchestratorPrompts.Plan`:

```
class StepCheckpoint:
    @staticmethod
    def stable(state, completed_step) -> Prompt:
        # Descrive il risultato dell'ultimo step completato
        # e mostra il piano rimanente
        # Chiede all'utente "Procedere?" con opzioni: sì / stop
```

Il messaggio deve includere:
- Cosa è stato fatto nello step appena completato (nome agente, obiettivo, risultato sintetico da `tool_results`)
- Quanti step rimangono (dal piano corrente)
- Una domanda chiara all'utente

Non usare blocchi di codice nel prompt: solo testo strutturato.

---

## §3 — Logica checkpoint in `SupervisorRouter`

### Struttura del checkpoint

Il checkpoint è controllato da un attributo `enabled` del costruttore `SupervisorRouter(enabled=False)`, coerente con il pattern del resto del sistema.

**Posizione nel flusso di `_determine_next_node()`:**

```
1. _update_additional_context(state)
2. if enabled AND current_step > 0 AND plan non vuoto:
       emetti interrupt("step-checkpoint", riepilogo)
       attendi risposta utente
       classifica: "continue" → procedi normalmente
                   "abort"    → _handle_abort(state) e ritorna "final_responder"
3. _determine_next_node(state) — calcolo normale del prossimo nodo
```

Il checkpoint avviene DOPO il context refresh e PRIMA del routing, così il contesto dei layer aggiornato è già disponibile per il messaggio all'utente.

### Classificazione risposta checkpoint

Usare `ZeroShotClassifier` con due label: `continue` e `abort`. Non è necessario supportare `modify` a questo livello — l'utente che vuole modificare il piano deve prima abortire e poi re-inviare la richiesta.

### Compatibilità con `plan_aborted`

Se l'utente risponde `abort`, chiamare `StateManager._handle_abort_state(state)` (metodo da creare in `StateManager` che centralizza la logica di `_handle_abort()` di `SupervisorPlannerConfirm`) oppure replicare la stessa logica: `plan = []`, `plan_aborted = True`.

---

## §4 — Verifica manuale

Percorso di test da eseguire con `enabled=True` su `SupervisorRouter`:

1. **Inviare una richiesta multi-step** (es. "Recupera dati DPC e poi simula")
2. **Confermare il piano** (se `SupervisorPlannerConfirm.enabled=True`)
3. **Attendere il completamento del primo step** (`RETRIEVER_EXECUTOR`)
4. **Verificare che il checkpoint interrupt sia emesso** con riepilogo step 1 e piano rimanente
5. **Rispondere "stop"** — verificare che il grafo raggiunga `FINAL_RESPONDER` senza eseguire il secondo step
6. **Verificare la risposta finale** — deve riflettere i risultati parziali (solo step 1)

---

## Acceptance Criteria

| ID | Criterio |
|---|---|
| SC-009-01 | Con `SupervisorRouter(enabled=True)` e piano a 2+ step, dopo il completamento del primo step viene emesso un interrupt con `interrupt_type = "step-checkpoint"` |
| SC-009-02 | Con risposta "continue", il piano prosegue normalmente al passo successivo |
| SC-009-03 | Con risposta "abort" al checkpoint, `FINAL_RESPONDER` viene raggiunto senza eseguire step successivi |
| SC-009-04 | Con `SupervisorRouter(enabled=False)` (default), il comportamento è identico a prima — nessun checkpoint emesso |
| SC-009-05 | `functional-spec-graph.md` G008 contiene la riga `step-checkpoint` con la corretta descrizione del handler |

---

## Note / Rischi

- **Complessità architetturale:** Questo è il difetto più complesso dell'elenco G007. Richiede un cambiamento nel nodo `SUPERVISOR_ROUTER` che finora era puramente deterministico (nessun LLM call, nessun interrupt). Procedere con attenzione ai side effect sul loop multi-step.
- **Default `enabled=False`:** Il checkpoint DEVE restare disabilitato di default per non rompere il comportamento attuale in produzione. Abilitarlo solo nei test e nelle sessioni interattive.
- **Rischio di loop:** Se la classificazione della risposta checkpoint fallisce e ritorna un intent sconosciuto, definire un fallback esplicito (`continue` come default sicuro).
- **Dipendenza da PLN-008/D1:** Il campo `plan_aborted` introdotto in PLN-008 è necessario per gestire correttamente l'abort dal checkpoint. Se PLN-008 non è completato, D7 non può essere implementato senza replicare la logica.
- **Test:** PLN-009 richiede un test manuale interattivo (con LangGraph `interrupt` attivo). Non è testabile con il meccanismo `tests/tests.json` attuale.
