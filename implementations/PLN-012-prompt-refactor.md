# PLN-012 — Prompt Refactor: Forma e Contenuto

> **Dipendenze**: —
> **Branch**: `feat/PLN-012-prompt-refactor`
> **Prossimo numero disponibile**: PLN-013

---

## Obiettivo

Migliorare la qualità, prevedibilità e manutenibilità dei prompt LLM del sistema multi-agente applicando le best practice del prompt engineering. I prompt attuali hanno ridondanze, istruzioni vaghe, serializzazione errata del contesto e mancanza di specifiche dell'output atteso.

---

## Best Practice di Riferimento

Le seguenti regole guidano ogni task di questo piano:

### BP-1 — Struttura standard di un system prompt
```
[Role]       → 1 frase, imperativa e specifica al dominio
[Task]       → lista numerata (ordine = priorità implicita per l'LLM)
[Output]     → schema / formato atteso, con nomi dei campi dove applicabile
[Constraints]→ lista puntata, solo per comportamenti che l'LLM tende a violare spontaneamente
```
Il section header `## Output format` è rilevante specialmente quando il nodo usa `with_structured_output()`:
anche se il modello è forzato a restituire un JSON schema, nominarli nel prompt riduce allucinazioni.

### BP-2 — Regole: affermative > negative, senza ridondanze
Preferire "Use only tools from the provided list" a "Do NOT invent tools" — stessa semantica, tono più prescrittivo.  
Non ripetere lo stesso concetto in forma diversa: "Only use agents from the provided registry" e "Do NOT invent new agents" sono equivalenti — tenere uno solo.

### BP-3 — Serializzazione contesto: `json.dumps()`, non `str()`
`str(python_dict)` produce Python repr (`'single quotes'`, `True/False`), non JSON valido (`"double quotes"`, `true/false`). L'LLM riconosce meglio il JSON standard.

### BP-4 — Istruzioni vaghe → formulazioni actionable
| Vago | Actionable |
|---|---|
| "Keep the plan minimal" | "Return the minimum number of steps required; no steps for informational queries" |
| "Be precise, concise, and execution-oriented" | "Extract only information explicitly stated; do not infer or add" |
| "Focus only on execution planning" | Eliminare — ridondante con il ruolo già definito |

### BP-5 — Output format hint nei prompt con `with_structured_output`
Anche con schema forzato, il prompt dovrebbe citare i campi attesi:
```
Expected output fields:
- intent: short phrase describing the main goal
- entities: list of named entities explicitly mentioned
- raw_text: verbatim copy of the user's message
```
Questo riduce le allucinazioni nei campi a testo libero e rende il comportamento più stabile tra modelli.

### BP-6 — Separazione system/human coretta nel FinalResponder
Il contesto di stato (tool_results, plan, etc.) appartiene al `HumanMessage`, non a un secondo `SystemMessage`.
Due `SystemMessage` in sequenza sono tecnicamente supportati dalla maggior parte dei provider ma è anomalo e riduce la leggibilità della catena.

### BP-7 — Few-shot per output strutturati complessi (SupervisorAgent)
Un esempio in-context di piano valido vs. piano vuoto stabilizza il comportamento nelle situazioni border:
```
Esempio piano non vuoto:
steps: [{"agent": "models_subgraph", "goal": "Run SaferRain simulation with DEM layer X"}]

Esempio piano vuoto (query informativa):
steps: []
```

### BP-8 — Differenziare SaferCast vs Models prompt nei punti che li distinguono
I due agenti hanno system prompt quasi identici. La differenza semantica chiave (retriever = best-effort inference; models = no invented layers) deve emergere nel testo, non perdersi nell'omogeneo del template condiviso.

---

## Scope / File coinvolti

| File | Stato |
|---|---|
| `src/saferplaces_multiagent/ma/prompts/request_parser_prompts.py` | todo |
| `src/saferplaces_multiagent/ma/prompts/supervisor_agent_prompts.py` | todo |
| `src/saferplaces_multiagent/ma/prompts/safercast_agent_prompts.py` | todo |
| `src/saferplaces_multiagent/ma/prompts/models_agent_prompts.py` | todo |
| `src/saferplaces_multiagent/ma/prompts/final_responder_prompts.py` | todo |
| `src/saferplaces_multiagent/ma/chat/final_responder.py` | todo |

---

## Task

| Task | File | Descrizione | Priorità |
|---|---|---|---|
| T-012-01 | `supervisor_agent_prompts.py` | Sostituire `str(AGENT_REGISTRY)` con `json.dumps(AGENT_REGISTRY)` in `CreatePlan.stable()` | alta |
| T-012-02 | `supervisor_agent_prompts.py` | Eliminare ridondanze nelle rules di `MainContext.stable()`: unificare "Only use agents…" e "Do NOT invent new agents" in una sola regola affermativa; rimuovere "Focus only on execution planning" | alta |
| T-012-03 | `supervisor_agent_prompts.py` | Aggiungere sezione `## Output format` in `MainContext.stable()` con i campi di `ExecutionPlan` (`steps[].agent`, `steps[].goal`) e l'esempio few-shot (BP-5, BP-7) | media |
| T-012-04 | `supervisor_agent_prompts.py` | Sostituire "Keep the plan minimal and logically ordered" con formulazione actionable (BP-4) | media |
| T-012-05 | `request_parser_prompts.py` | Aggiungere sezione `## Output format` con i campi di `ParsedRequest` (`intent`, `entities`, `raw_text`) e le istruzioni semantiche per ciascuno (unifica con IMP-001) | alta |
| T-012-06 | `request_parser_prompts.py` | Sostituire "Be precise, concise, and execution-oriented" con formulazione actionable (BP-4) | media |
| T-012-07 | `safercast_agent_prompts.py` | Riscrivere system prompt per evidenziare la caratteristica differenziante: l'inferenza best-effort degli argomenti mancanti è accettata e attesa (BP-8) | media |
| T-012-08 | `models_agent_prompts.py` | Riscrivere system prompt per evidenziare la caratteristica differenziante: il layer di input deve esistere nello stato, mai inventato — se mancante descrivere esattamente cosa manca (BP-8) | media |
| T-012-09 | `final_responder_prompts.py` + `final_responder.py` | Rimuovere `Context.Structured` (dead code — solo `Formatted` è usato nel nodo); spostare `Context.Formatted` da secondo SystemMessage a HumanMessage in `final_responder.py` (BP-6) | media |
| T-012-10 | tutti | Per ogni sistema prompt ristrutturato, creare `v001()` come alias dell'attuale versione prima di sovrascrivere `stable()`, a preservare la possibilità di override nei test | alta |

---

## Acceptance Criteria

- [ ] SC-012-01 — Il test T001 e T002 (run esistenti) passano senza regressioni dopo il refactor
- [ ] SC-012-02 — `str(AGENT_REGISTRY)` non compare più nella codebase (grep)
- [ ] SC-012-03 — Ogni `stable()` modificato ha il corrispondente `v001()` con il testo precedente
- [ ] SC-012-04 — Il `FinalResponder` usa un solo `SystemMessage` + uno o più `HumanMessage`
- [ ] SC-012-05 — `Context.Structured` non compare più nei file di produzione (solo test se necessario)
- [ ] SC-012-06 — I campi di `ParsedRequest` ed `ExecutionPlan` compaiono esplicitamente nei rispettivi system prompt

---

## Note / Rischi

- **IMP-001** in `_plan-todo.md` è un sottoinsieme di T-012-05: se si implementa T-012-05, IMP-001 va chiuso.
- I test con prompt override (`T006_prompt_override.py`) si basano su `unittest.mock.patch.object` — non sono impattati dall'aggiornamento di `stable()`, a patto che i `v001()` siano creati prima.
- `PlanConfirmation.RequestGenerator.stable()` genera il testo di conferma come meta-prompt ("genera un messaggio...") — questo schema è accettabile e intenzionale; non è in scope di questo piano.
- Il refactor è **solo testuale** (prompt strings): nessuna modifica alla topologia del grafo, allo stato, o ai modelli Pydantic.
