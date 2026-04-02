# Stato del Refactor dei Prompt — SaferPlaces Multiagent

## Contesto

Il sistema sta migrando da prompt monolitici (`_old_prompts/`) a un'architettura gerarchica e composita (`structured_prompts/`).

Il paradigma nuovo non replica i vecchi prompt: li **sostituisce** con una gerarchia di sotto-prompt specializzati per livello di astrazione, composti a runtime. Ogni tool/agente espone viste dedicate al livello di ragionamento del consumer:

```
Tool (es. SaferRainTool)
├── ContextForOrchestrator  → sintesi per l'orchestratore (chi delega)
├── ContextForPlanner       → regole di pianificazione per il planner
└── ContextForSpecialized   → contratto di esecuzione per il nodo esecutore
```

Il planner/orchestratore vede le descrizioni composte **dal basso verso l'alto**:
`ContextForPlannerAgentView` → aggrega `ContextForPlanner` di ogni tool → che aggrega `_identity_routing + _decision_policy + _planner_facing + _tool_guardrails`

---

## Stato attuale degli import

| File | Import attuale | Note |
|---|---|---|
| `ma/chat/request_parser.py` | `structured_prompts.request_parser_prompts` | ✅ già migrato |
| `ma/orchestrator/supervisor.py` | `_old_prompts.supervisor_agent_prompts` | ⚠️ da migrare |
| `ma/chat/final_responder.py` | `_old_prompts.final_responder_prompts` | ⚠️ da migrare |
| `ma/specialized/models_agent.py` | `_old_prompts.models_agent_prompts` | ⚠️ da migrare |
| `ma/specialized/safercast_agent.py` | `_old_prompts.safercast_agent_prompts` | ⚠️ da migrare |
| `ma/specialized/map_agent.py` | `_old_prompts.map_agent_prompts` | ⚠️ da migrare |

---

## Stato dei moduli in `structured_prompts/`

| Modulo | Stato | Note |
|---|---|---|
| `request_parser_prompts.py` | ✅ completo e in uso | Signature `stable(state)` — rimuove la dipendenza da `shapes_summary` esterno |
| `layers_agent_promps.py` | ✅ completo | `LayerSummary.with_geospatial_metadata(state)` |
| `models_agent_prompts.py` | ✅ completo | Tool views a 3 livelli per tutti e 4 i tool; `ContextForOrchestratorAgentView` e `ContextForPlannerAgentView` presenti |
| `safercast_agent_prompts.py` | ✅ completo | Tool views per DPC e Meteoblue su 3 livelli |
| `map_agent_prompts.py` | ✅ completo | `ContextForPlannerAgentView` (nuovo), `MoveMapViewToolPrompts`, `LayerSymbologyToolPrompts`, `ShapesSummary`, `ExecutionContext` |
| `supervisor_agent_prompts.py` | ✅ implementato, non in uso | Importa già da `structured_prompts.*`; contiene `PlannerContext` (nuovo), `CreatePlan`, replanning, `PlanExplanation`, `StepCheckpoint` |
| `final_responder_prompts.py` | ✅ implementato, non in uso | Commentato nell' `__init__.py` di `structured_prompts` |

> `structured_prompts/__init__.py`: `final_responder_prompts` è ancora **commentato** nell'import — da abilitare contestualmente alla migrazione di `final_responder.py`.

---

## Differenza chiave old → new (paradigma)

### Paradigma vecchio — `_old_prompts`

`supervisor.py` usa `OrchestratorPrompts.MainContext.stable()` come SystemMessage.
Questo è un prompt monolitico scritto a mano (~150 righe) che elenca domini operativi, regole, agenti, esempi — tutto in un unico blocco statico.

### Paradigma nuovo — `structured_prompts`

Il nuovo archivio offre **tre punti di composizione** per il supervisore:

#### 1. `MainContext.structured_prompt()` — Orchestrator View (alto livello)
Composto da:
- Ruolo orchestratore (non esegue tool, solo delega)
- `ModelsPrompts.ContextForOrchestratorAgentView.stable()` → sintesi dei 4 tool
- `SaferCastAgentPrompts.ContextForOrchestratorAgentView.stable()` → sintesi DPC + Meteoblue
- `MapAgentPrompts.MoveMapViewToolPrompts.ContextForOrchestrator.stable()`
- `MapAgentPrompts.LayerSymbologyToolPrompts.ContextForOrchestrator.stable()`
- Policy di pianificazione + Goal Writing Rules

Ogni componente è mantenuta **dal team del tool/agente** — il supervisore non deve conoscere i dettagli.

#### 2. `Plan.PlannerContext.stable()` — Planner View (livello medio)
Versione più ricca: compone le view `ContextForPlannerAgentView` degli agenti specializzati, che a loro volta aggregano per ogni tool:
`_identity_routing + _decision_policy + _planner_facing + _tool_guardrails`

Questo è il livello più appropriato per il `SupervisorAgent` nel planning: ha visibilità sulle regole di dipendenza e prerequisiti di ogni tool senza che il supervisore debba gestirle inline.

#### 3. `Plan.CreatePlan.structured(state)` — Payload con procedura esplicita
Versione strutturata del messaggio Human per la generazione del piano: include procedura di planning step-by-step, dependency rules, minimization rules oltre al contesto di stato.

---

## Analisi del `SupervisorAgent` — cosa cambia

### `SupervisorAgent._generate_plan()` — prompt di planning

**Ora** (da `_old_prompts`):
```python
main_prompt = OrchestratorPrompts.MainContext.stable().to(SystemMessage)
planning_prompt = OrchestratorPrompts.Plan.CreatePlan.stable(state).to(HumanMessage)
# oppure IncrementalReplanning / TotalReplanning
```

**Con il nuovo paradigma**, la relazione più coerente è:
```python
# System: ruolo planner con vista composita sugli agenti
main_prompt = OrchestratorPrompts.Plan.PlannerContext.stable().to(SystemMessage)
# Human: payload standard (invariato — già in structured_prompts)
planning_prompt = OrchestratorPrompts.Plan.CreatePlan.stable(state).to(HumanMessage)
```

`PlannerContext.stable()` sostituisce `MainContext.stable()` perché:
- Compone la vista agenti da sotto-prompt dedicati (manutenzione distribuita)
- Separa il ruolo del planner (`PlannerContext`) dalla vista di orchestrazione ad alto livello (`MainContext.structured_prompt`)
- Le regole di dipendenza sono estratte dai tool direttamente, non replicate inline

`CreatePlan.stable(state)` e le varianti di replanning (`IncrementalReplanning`, `TotalReplanning`) **rimangono invariate** nella struttura — sono già presenti nel nuovo file e corrette.

### `SupervisorPlannerConfirm._generate_plan_explanation()` — spiegazione piano

**Ora**: `OrchestratorPrompts.Plan.PlanExplanation.ExplainerMainContext.stable()` e `RequestExplanation.stable(state, user_question)`

**Nuovo**: stessa struttura, stessi prompt — **nessuna modifica di contenuto richiesta**, solo cambio import.

### `SupervisorRouter._maybe_checkpoint_interrupt()` — step checkpoint

**Ora**: `OrchestratorPrompts.Plan.StepCheckpoint.stable(state, completed_step)`

**Nuovo**: identico — solo cambio import.

---

## Piano di migrazione — `supervisor.py`

### Step 1 — Cambio import (1 riga)

```python
# prima
from .._old_prompts.supervisor_agent_prompts import OrchestratorPrompts

# dopo
from ..structured_prompts.supervisor_agent_prompts import OrchestratorPrompts
```

Questo è sufficiente a ottenere un supervisore che usa i prompt nuovi per `CreatePlan`, `IncrementalReplanning`, `TotalReplanning`, `PlanExplanation`, `StepCheckpoint`.

### Step 2 — Sostituire il SystemMessage di planning (1 riga)

In `_generate_plan()`, sostituire:
```python
main_prompt = OrchestratorPrompts.MainContext.stable().to(SystemMessage)
```
con:
```python
main_prompt = OrchestratorPrompts.Plan.PlannerContext.stable().to(SystemMessage)
```

`PlannerContext` è il prompt di sistema composto che rappresenta il nuovo paradigma per il `SupervisorAgent` nella fase di planning. Contiene la vista sugli agenti via composizione da sotto-prompt di tool.

### Step 3 (opzionale / test A/B) — CreatePlan strutturato

Per testare la versione con procedura esplicita:
```python
planning_prompt = OrchestratorPrompts.Plan.CreatePlan.structured(state).to(HumanMessage)
```
vs. il `stable()` standard. Usare `patch.object` per il test (pattern esistente in `tests/T006_prompt_override.py`).

---

## Piano di migrazione — altri nodi

### `models_agent.py`
```python
# prima
from .._old_prompts.models_agent_prompts import ModelsPrompts
# dopo
from ..structured_prompts.models_agent_prompts import ModelsPrompts
```
I prompt usati (`MainContext.stable()`, `ToolSelection.InitialRequest.stable(state)`, `ToolSelection.ReinvocationRequest.stable(state)`) esistono già in `structured_prompts`.

### `safercast_agent.py`
```python
from ..structured_prompts.safercast_agent_prompts import SaferCastAgentPrompts
```

### `map_agent.py`
```python
from ..structured_prompts.map_agent_prompts import MapAgentPrompts
```

### `final_responder.py`
```python
from ..structured_prompts.final_responder_prompts import FinalResponderPrompts
```
Contestualmente: rimuovere il commento su `final_responder_prompts` in `structured_prompts/__init__.py`.

---

## Prompt mancanti o da implementare

### 1. `OrchestratorPrompts.Plan.PlanExplanation` — verifica signature

Nel nuovo file, `ExplainerMainContext` esiste ma occorre verificare che `RequestExplanation.stable(state, user_question)` accetti esattamente quei parametri (la signature è cambiata rispetto all'old che aveva solo `state` + `user_question`). ✅ Da verificare prima del cambio import.

### 2. `MapAgentPrompts.ContextForPlannerAgentView` — presenza nel nuovo file

Nella `Plan.PlannerContext.stable()` del nuovo supervisore si usa `MapAgentPrompts.ContextForPlannerAgentView.stable()`. Verificare che questa classe esista in `structured_prompts/map_agent_prompts.py` (non era direttamente visibile nel search).

### 3. Prompt inline non ancora strutturati (bassa priorità)

Dall'inventario, restano prompt inline non strutturati che potrebbero essere migrati in futuro:

| Prompt | Posizione | Note |
|---|---|---|
| `"You are a specialized agent for managing geospatial layers..."` | `LayersAgent.run()` inline | Candidato per `LayersAgentPrompts.AgentRole.stable()` |
| `f"User has this request:\n{parsed_request}..."` | `SupervisorRouter._build_layers_request()` inline | Candidato per `OrchestratorPrompts.LayersRefresh.stable(state)` |
| `"You are a helpful assistant explaining tool invocations."` | `confirmation_utils.py` inline | Bassa priorità |
| `"You are a helpful assistant explaining validation requirements."` | `validation_utils.py` inline | Bassa priorità |
| ToolMessage content strings (SaferRain, DPC, Meteoblue) | Executor nodes inline | Bassa priorità — messaggi di sistema, non di LLM |

---

## Architettura prompt finale target (Supervisor)

```
SupervisorAgent._generate_plan()
│
├── [SystemMessage] OrchestratorPrompts.Plan.PlannerContext.stable()
│   ├── ruolo planner + policy globali
│   ├── ModelsPrompts.ContextForPlannerAgentView.stable()
│   │   ├── DigitalTwinToolPrompts.ContextForPlanner   [_identity + _decision + _planner + _guardrails]
│   │   ├── SaferRainToolPrompts.ContextForPlanner     [idem]
│   │   ├── SaferBuildingsToolPrompts.ContextForPlanner [idem]
│   │   └── SaferFireToolPrompts.ContextForPlanner     [idem]
│   ├── SaferCastAgentPrompts.ContextForPlannerAgentView.stable()
│   │   ├── DPCRetrieverToolPrompts.ContextForPlanner  [idem]
│   │   └── MeteoblueRetrieverToolPrompts.ContextForPlanner [idem]
│   ├── MapAgentPrompts.ContextForPlannerAgentView.stable()
│   └── goal writing rules + output contract
│
└── [HumanMessage] OrchestratorPrompts.Plan.CreatePlan.stable(state)
    ├── Parsed Request (formattato)
    └── Available Layers (via LayersAgentPrompts.LayerSummary)
```

Questa architettura garantisce che:
- Ogni team di tool/agente mantiene la propria vista planner senza toccare il supervisore
- Il supervisore compone automaticamente un contesto aggiornato a ogni aggiunta di tool
- La testabilità rimane piena via `patch.object` sui metodi `stable()` a qualsiasi livello
