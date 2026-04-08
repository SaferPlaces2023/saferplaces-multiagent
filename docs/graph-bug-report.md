# SaferPlaces Multiagent — Graph Bug Report

> Analisi statica del grafo LangGraph, dei nodi specializzati e dei prompt di orchestrazione.  
> Data: 2026-04-07

---

## Indice

1. [Panoramica del ciclo di vita](#1-panoramica-del-ciclo-di-vita)
2. [Percorsi possibili](#2-percorsi-possibili)
3. [Bug confermati](#3-bug-confermati)
4. [Inconsistenze e code smells](#4-inconsistenze-e-code-smells)
5. [Casi non gestiti](#5-casi-non-gestiti)
6. [Problemi nei prompt](#6-problemi-nei-prompt)
7. [Riepilogo priorità](#7-riepilogo-priorità)

---

## 1. Panoramica del ciclo di vita

```
HumanMessage?
    YES → REQUEST_PARSER → initialize_new_cycle → supervisor_invocation_reason = "new_request"
    NO  → STATE_PROCESSOR → END

SUPERVISOR_SUBGRAPH (loop):
    SupervisorAgent          ← analizza invocation_reason, genera/aggiorna il piano
    SupervisorPlannerConfirm ← interrupt? → classifica intent utente
    SupervisorRouter         ← supervisor_next_node = prossimo agente o FINAL_RESPONDER

Subgraph specializzato (loop su step):
    {Agent} → {InvocationConfirm} → {Executor}
    Executor setta supervisor_invocation_reason = "step_done" | "step_error" | "step_skip" | "step_no_tools"
    → torna a SUPERVISOR_SUBGRAPH

FINAL_RESPONDER → cleanup_on_final_response → END
```

---

## 2. Percorsi possibili

| Percorso | Trigger | Note |
|---|---|---|
| Conversazionale | piano vuoto `[]` | `SupervisorAgent` non imposta `plan`, `PlanConfirm` → `END` via condizionale |
| Piano monostep | un singolo agente | ciclo supervisor → subgraph → supervisor → final |
| Piano multistep | n agenti | `current_step` avanza a ogni `step_done` |
| Replan (modify) | utente richiede modifica | `supervisor_invocation_reason = "modify"` |
| Replan (no tools) | agente non trova tool | rigenera piano, reset `current_step = 0` |
| Replan (skip) | utente annulla invocation | rigenera piano, reset `current_step = 0` |
| Replan (error) | tool execution error | rigenera piano, reset `current_step = 0` |
| Abort | utente annulla piano | `plan_confirmation = "aborted"` → FINAL_RESPONDER |
| Reinvocazione tool | argomenti invalidi | loop interno al subgraph (InvocationConfirm → Agent → InvocationConfirm) |

---

## 3. Bug confermati

### BUG-01 — Typo chiave stato in `SupervisorRouter.run()` ★★★

**File:** `src/saferplaces_multiagent/ma/orchestrator/supervisor.py:375`  
**Gravità: Alta**

```python
# SupervisorRouter.run() — ramo abort
state["invocation_reason"] = None   # ← SBAGLIATO
```

La chiave corretta è `supervisor_invocation_reason`. Il reset usa una chiave inesistente, lasciando `supervisor_invocation_reason` col valore precedente (`PLAN_ABORTED`). Alla prossima invocazione di `SupervisorAgent` (dopo la risposta dell'utente), `invocation_reason` potrebbe essere `PLAN_ABORTED` e far eseguire il ramo sbagliato (`step_skip` non esiste, quindi fallback silenzioso con piano invariato).

**Fix:**
```python
state["supervisor_invocation_reason"] = None
```

---

### BUG-02 — Loop infinito nella reinvocazione tool senza guardia ★★★

**File:** `safercast_agent.py`, `models_agent.py`  
**Gravità: Alta**

I metodi `_handle_provide_corrections` e `_handle_auto_correct` incrementano `retriever_reinvocation_count` / `models_reinvocation_count` ma **nessun nodo controlla mai questo contatore**. Non esiste un `_MAX_REINVOCATION_ITERATIONS` equivalente a `SupervisorAgent._MAX_REPLAN_ITERATIONS`. Se l'LLM continua a proporre argomenti invalidi e l'utente risponde con "auto_correct", il subgraph cicla indefinitamente.

**Fix:** aggiungere un guard in `DataRetrieverInvocationConfirm.run()` e `ModelsInvocationConfirm.run()`:
```python
MAX_REINVOCATION = 3
if (state.get("retriever_reinvocation_count") or 0) >= MAX_REINVOCATION:
    return self._handle_abort(state)
```

---

### BUG-03 — Routing del retriever subgraph non gestisce `INVOCATION_ABORT` ★★★

**File:** `multiagent_graph.py:69`  
**Gravità: Alta**

```python
# build_specialized_retriever_subgraph
retriever_builder.add_conditional_edges(
    retriever_invocation_confirm.name,
    lambda state: state.get('retriever_invocation_confirmation') == 'rejected',
    {
        True: retriever_agent.name,
        False: retriever_executor.name,  # ← intercetta anche "abort"
    }
)
```

Quando `retriever_invocation_confirmation == "abort"`, la condizione restituisce `False` e il grafo indirizza comunque all'executor. Il `DataRetrieverExecutor.run()` gestisce l'abort correttamente al suo interno **solo per coincidenza** — il routing non è semanticamente corretto. Se la logica dell'executor cambiasse, l'abort silenzioso diventerebbe esecuzione non voluta.

Il models subgraph usa invece un routing semantico a tre vie (accepted / modify / aborted) — necessario allineare i due.

---

### BUG-04 — `_has_tool_calls(None)` non crasha ma è fragile ★★

**File:** `models_agent.py:480`, `safercast_agent.py` (executor)  
**Gravità: Media**

```python
invocation = state.get('models_invocation')
if not ModelsAgent._has_tool_calls(invocation):   # invocation può essere None
```

`_has_tool_calls` fa `getattr(invocation, "tool_calls", [])`. Se `invocation` è `None`, `getattr(None, ...)` restituisce il default `[]` → la condizione è soddisfatta. Non crasha, ma l'intenzione semantica non è chiara e il codice è fragile a modifiche future. Sarebbe necessario un check esplicito su `None` prima della chiamata.

---

### BUG-05 — `plan_confirmation = "pending"` non mappata nel conditional edge del supervisor subgraph ★★

**File:** `multiagent_graph.py:39-46`  
**Gravità: Media**

```python
lambda state: state.get('plan_confirmation') if state.get('plan') else END,
{
    PlanConfirmationLabels.ACCEPTED: supervisor_router.name,   # "accepted"
    PlanConfirmationLabels.MODIFY:   supervisor_agent.name,    # "modify"
    PlanConfirmationLabels.ABORTED:  supervisor_router.name,   # "aborted"
    END: END
    # mancano: "pending", "rejected"
}
```

Il valore `"pending"` non è nel mapping dei conditional edges. Se per qualsiasi motivo `plan_confirmation` non venisse risolta (es. bug in `_auto_confirm` o `_handle_intent`), LangGraph lancerebbe `ValueError: Invalid edge key`. Lo stesso vale per `"rejected"` che è definita in `PlanConfirmationLabels` ma mai usata — se un futuro percorso la impostasse, il grafo si rompe.

---

### BUG-06 — `state['plan'][state['current_step']]` senza bounds check nei prompt ★★

**File:** `supervisor_agent_prompts.py` (3 classi `_TaskInstruction`), `models_agent_prompts.py:97`, `safercast_agent_prompts.py:77`  
**Gravità: Media**

Le classi `_TaskInstruction` di `PlanModificationDueStepNoTools`, `PlanModificationDueStepSkip`, `PlanModificationDueStepError` accedono direttamente a:

```python
state['plan'][state['current_step']]['agent']
state['plan'][state['current_step']]['goal']
```

Se `current_step >= len(plan)` (es. dopo `step_done` sul penultimo step, prima che il supervisor aggiorni il piano), si ottiene `IndexError` durante la costruzione del prompt — prima ancora di invocare l'LLM. Il crash è quindi in fase di costruzione del messaggio, non di esecuzione.

---

## 4. Inconsistenze e code smells

### INC-01 — `@staticmethod` mancanti nei prompt `RequestParserInstructions` ★★

**File:** `request_parser_prompts.py`

Tutte le classi interne di `RequestParserInstructions.Prompts` (`_RoleAndScope`, `_GlobalContext`, `_TaskInstruction`, `_ParsedRequest`) e di `Invocations` (`ParseOneShot`, `ParseMultiPrompt`) definiscono i metodi **senza `@staticmethod`**, diversamente da ogni altro modulo prompt del progetto. In pratica funziona (Python non applica il descriptor mantenendo la funzione non legata), ma:

- Rompe il contratto di design e la consistenza del codebase.
- Impedisce il patching via `unittest.mock.patch.object` — i test con prompt override non funzionano per questi metodi.
- Genera warning Pylance/mypy.

---

### INC-02 — Asimmetria strutturale routing retriever vs models subgraph ★★

Il **retriever subgraph** usa routing booleano:
```python
lambda state: state.get('retriever_invocation_confirmation') == 'rejected',
{ True: retriever_agent.name, False: retriever_executor.name }
```

Il **models subgraph** usa routing semantico a tre vie:
```python
lambda state: state.get('models_invocation_confirmation') if state.get('models_invocation') else END,
{ 'accepted': ..., 'modify': ..., 'aborted': ..., END: END }
```

Il models subgraph è il pattern più robusto e dovrebbe essere replicato nel retriever.

---

### INC-03 — `SupervisorAgent.run()` non ha ramo `else` (fallback silenzioso) ★

**File:** `supervisor.py:97-173`

Il `switch` su `invocation_reason` non ha `else`. Se `invocation_reason` è `None` o un valore non previsto, il nodo restituisce lo stato invariato senza log di warning. Il piano non viene generato, il grafo va silenziosamente a `PlannerConfirm` con piano nullo → END senza risposta utente significativa.

---

### INC-04 — `models_invocation_reason` / `retriever_invocation_reason` non dichiarate in `MABaseGraphState` ★

**File:** `common/states.py`

Entrambe le chiavi sono lette e scritte ma non presenti in `MABaseGraphState`. I campi dichiarati sono `retriever_invocation_confirmation`, `retriever_reinvocation_request`, ecc. ma non `*_invocation_reason` né `*_reinvocation_count`. Causa warning di tipo e comportamento indefinito con strumenti di ispezione dello stato.

---

### INC-05 — `StateManager._clear_specialized_agent_state` non pulisce `*_invocation_reason` né `*_reinvocation_count` ★

**File:** `common/states.py`

```python
@staticmethod
def _clear_specialized_agent_state(state, agent_type):
    prefix = agent_type
    state[f'{prefix}_invocation'] = None
    state[f'{prefix}_current_step'] = 0
    state[f'{prefix}_invocation_confirmation'] = None
    state[f'{prefix}_reinvocation_request'] = None
    # mancano: {prefix}_invocation_reason, {prefix}_reinvocation_count
```

Tra cicli, `retriever_reinvocation_count` e `models_reinvocation_count` non vengono azzerati. Una seconda richiesta dell'utente parte con il contatore già a un valore positivo, avvicinando prematuramente la soglia di abort (quando BUG-02 sarà fixato).

---

### INC-06 — Accesso diretto `state['models_current_step']` senza `.get()` nell'executor ★

**File:** `models_agent.py:506`, `safercast_agent.py` (executor)

```python
invocation_current_step = state['models_current_step']   # KeyError se None
```

Se `models_current_step` è `None` (es. nodo non inizializzato), si ottiene problemi con `invocation.tool_calls[None:]` che lancia `TypeError`. Meglio usare `state.get('models_current_step') or 0`.

---

### INC-07 — Blocco `TODO: Confirmation enabled` irraggiungibile ★

**File:** `safercast_agent.py:end`, `models_agent.py:end`

```python
        else:
            state["retriever_invocation_confirmation"] = INVOCATION_ACCEPTED
            return state          # ← return

        # TODO: Confirmation enabled    ← IRRAGGIUNGIBILE
        if self.enabled:
            raise NotImplementedError(...)
```

Il blocco non viene mai eseguito. Se `enabled=True` viene impostato, il comportamento atteso (NotImplementedError) non viene mai lanciato — l'agente prosegue come se `enabled=False`.

---

## 5. Casi non gestiti

### UC-01 — `invocation_reason = None` senza ramo fallback ★

Se `SupervisorAgent` viene invocato con `supervisor_invocation_reason = None` (es. resume da checkpoint, test diretto del subgraph), tutti i rami `if/elif` sono saltati. Il piano non viene né generato né aggiornato, nessun log viene emesso.

---

### UC-02 — Piano con tutti agenti allucinati nel ramo `new_request` ★

```python
plan_steps = [step for step in plan.steps if step.agent in self.specialized_agents]
if len(plan_steps) > 0:
    state["plan"] = plan_steps
    # se plan_steps è vuoto, state["plan"] rimane quello precedente
```

Se l'LLM produce un piano in cui ogni agente ha un nome non valido, `plan_steps` è vuoto e il piano precedente rimane attivo → il vecchio piano viene eseguito invece di fallire esplicitamente.

Nei rami `step_no_tools`, `step_skip`, `step_error` invece `state["plan"] = plan_steps` viene sempre assegnato (anche vuoto), quindi solo in quei rami il piano vuoto è gestito.

---

### UC-03 — `SupervisorRouter.run()` potenziale `TypeError` su `plan = None` ★★

**File:** `supervisor.py`

```python
current_step = state["current_step"]   # accesso diretto
if current_step < len(plan):           # TypeError se plan è None
```

Se `plan_confirmation != PLAN_ABORTED` ma `plan` è `None` (es. dopo un replan che produce lista vuota nel ramo `step_no_tools` che assegna `state["plan"] = []` e poi controlla `if len > 0` separatamente), `len(None)` lancia `TypeError`.

---

### UC-04 — `SupervisorPlannerConfirm._unnecessary_confirmation` TypeError su `current_step = None` ★★

**File:** `supervisor.py:199`

```python
if current_step >= len(plan):    # TypeError se current_step è None
    return True
```

`current_step` è `Optional[int]` nello stato. Se è `None` prima che `SupervisorAgent` lo inizializzi, il confronto lancia `TypeError`.

---

### UC-05 — Nessuna uscita di sicurezza nella reinvocazione tool ★★★

Il ciclo `InvocationConfirm → Agent → InvocationConfirm` non ha limite di iterazioni (vedi BUG-02). L'unico modo di uscire è che l'utente risponda "abort" esplicitamente o che l'LLM produca finalmente argomenti validi. Un LLM bloccato in un corner case può saturare il contesto e generare costi illimitati.

---

### UC-06 — `MapAgent` non cattura eccezioni nei tool ★

**File:** `map_agent.py:75`

```python
result = tool_obj._run(**tool_args)
```

Se `_run` lancia un'eccezione (tool non trovato, errore I/O, errore rete), si propaga non catturata fuori dal nodo `MapAgent`. Il `supervisor_invocation_reason` non viene impostato → `SupervisorAgent` riceve `invocation_reason = None` → ramo non gestito → piano non aggiornato, ciclo silenziosamente interrotto.

---

## 6. Problemi nei prompt

### PR-01 — Viewport navigation citata nel prompt del supervisor ma non implementata ★★

**File:** `supervisor_agent_prompts.py`

Il prompt descrive `map_agent` come:
> "moves the viewport, generates layer symbology styles, registers shapes drawn by the user"

Ma `MapAgent` ha solo due tool: `LayerSymbologyTool` e `RegisterShapeTool`. Il viewport navigation **non è implementato**. Il rischio è che il supervisor pianifichi un passo map_agent con goal "zoom to area X" → l'LLM del MapAgent non trova tool adeguato → `step_no_tools` → replan loop.

---

### PR-02 — Limite temporale DPC non comunicato nel prompt `_TaskInstruction` ★

**File:** `safercast_agent_prompts.py`

Il prompt `_RoleAndScope` cita "up to 7 days back" per DPC, ma la `_TaskInstruction` non lo ribadisce e non istruisce a non usare `dpc_retriever` per date oltre 7 giorni nel passato. Un LLM può proporre query storiche fuori range che falliscono solo in esecuzione.

---

### PR-03 — Metodi `generic()` non conformi alle coding standards ★

**File:** `supervisor_agent_prompts.py` (multiple classi)

Lo standard definisce le versioni alternative come `v001()`, `v002()`, ecc. I metodi `generic()` non seguono questa convenzione:
- Non sono testabili via `patch.object` con lo stesso schema dei test standard.
- Non è chiaro se siano "versioni in test" o "fallback legacy".

---

### PR-04 — Contratto nome-agente LLM → filtro → router non documentato ★

Il `SupervisorAgent` filtra i passi del piano mantenendo solo agenti in `specialized_agents`. Il router principale ha alias (es. `"retriever_agent"` → `RETRIEVER_SUBGRAPH`). Questo contratto implicito non è documentato nel prompt né nel codice, rendendo difficile diagnosticare perché certi passi scompaiono silenziosamente.

---

## 7. Riepilogo priorità

| ID | Categoria | Gravità | Descrizione breve | File |
|---|---|---|---|---|
| BUG-01 | Bug | 🔴 Alta | Typo `invocation_reason` vs `supervisor_invocation_reason` in abort | `supervisor.py:375` |
| BUG-02 | Bug | 🔴 Alta | Nessun limite iterazioni loop reinvocazione tool | `safercast_agent.py`, `models_agent.py` |
| BUG-03 | Bug | 🔴 Alta | Routing retriever non distingue `abort` da `accepted` | `multiagent_graph.py:69` |
| UC-05 | Caso non gestito | 🔴 Alta | Nessuna uscita di sicurezza nel ciclo reinvocazione | idem BUG-02 |
| BUG-05 | Bug | 🟠 Media | `plan_confirmation = "pending"` / `"rejected"` non mappate → `ValueError` LangGraph | `multiagent_graph.py:39` |
| BUG-06 | Bug | 🟠 Media | Accesso `plan[current_step]` OOB nei prompt del supervisor | `supervisor_agent_prompts.py` |
| UC-03 | Caso non gestito | 🟠 Media | `SupervisorRouter` `TypeError` se `plan = None` e non aborted | `supervisor.py` |
| UC-04 | Caso non gestito | 🟠 Media | `_unnecessary_confirmation` `TypeError` se `current_step = None` | `supervisor.py:199` |
| INC-01 | Inconsistenza | 🟠 Media | `@staticmethod` mancanti in `RequestParserInstructions` — rompe `patch.object` | `request_parser_prompts.py` |
| PR-01 | Prompt | 🟠 Media | Viewport navigation citata ma non implementata — induce replan loop | `supervisor_agent_prompts.py` |
| INC-02 | Inconsistenza | 🟡 Bassa | Routing booleano retriever vs semantico models — asimmetria | `multiagent_graph.py` |
| INC-05 | Inconsistenza | 🟡 Bassa | `StateManager` non pulisce `*_invocation_reason` e `*_reinvocation_count` | `common/states.py` |
| INC-07 | Inconsistenza | 🟡 Bassa | Blocco `TODO: Confirmation enabled` irraggiungibile dopo `return` | `safercast_agent.py`, `models_agent.py` |
| BUG-04 | Bug | 🟡 Bassa | `_has_tool_calls(None)` silenziosamente corretto ma fragile | executor nodes |
| INC-06 | Inconsistenza | 🟡 Bassa | `state['models_current_step']` accesso diretto senza `.get()` | `models_agent.py:506` |
| UC-01 | Caso non gestito | 🟡 Bassa | `invocation_reason = None` non ha ramo fallback con log | `supervisor.py` |
| UC-02 | Caso non gestito | 🟡 Bassa | Piano con tutti agenti allucinati non genera errore esplicito | `supervisor.py` |
| UC-06 | Caso non gestito | 🟡 Bassa | `MapAgent` non cattura eccezioni nei tool | `map_agent.py` |
| INC-03 | Inconsistenza | 🟡 Bassa | Nessun ramo `else` in `SupervisorAgent.run()` | `supervisor.py` |
| INC-04 | Inconsistenza | 🟡 Bassa | `*_invocation_reason` / `*_reinvocation_count` non dichiarate in `MABaseGraphState` | `common/states.py` |
| PR-02 | Prompt | 🟡 Bassa | Limite temporale DPC 7gg non ribadito in `_TaskInstruction` | `safercast_agent_prompts.py` |
| PR-03 | Prompt | 🟡 Bassa | Metodi `generic()` non conformi alle coding standards (dovrebbero essere `v001()`) | `supervisor_agent_prompts.py` |
| PR-04 | Prompt | 🟡 Bassa | Contratto nome-agente LLM → filtro → router implicito, non documentato | vari |
