---
applyTo: "src/**"
---

# Services — SaferPlaces Multiagent

## Storage S3

I file utente sono salvati in:
```
s3://saferplaces.co/SaferPlaces-Agent/dev/user=<USER_ID>/project=<PROJECT_ID>/
```

- Usare **sempre** `common/s3_utils.py` per tutte le operazioni S3
- Non hardcodare mai bucket names o prefissi di path
- Il bucket è configurabile — leggerlo dalla configurazione, non dal codice

## LangGraph

- Entry point del grafo: `src/saferplaces_multiagent/multiagent_graph.py`
- Configurazione server: `src/saferplaces_multiagent/langgraph.json`
- Avvio: `langgraph dev --config src/saferplaces_multiagent/langgraph.json`
- Ogni subgraph segue il pattern: `Agent → InvocationConfirm → Executor`

## Flask

- App: `src/saferplaces_multiagent/agent_interface/flask_server/app.py`
- Routes: `src/saferplaces_multiagent/agent_interface/flask_server/routes.py`
- Avvio dev: `flask --app src/saferplaces_multiagent/agent_interface/flask_server/app.py run --debug`
- Chat handler: `agent_interface/chat_handler.py`
- Interfaccia grafo: `agent_interface/graph_interface.py`

## Modelli LLM

Non hardcodare mai il nome del modello — usare `common/utils._base_llm()`.

## Testing

### Test standard (tests.json)

I test sono definiti in `tests/tests.json` con ID sequenziali (`T001`, `T002`, …).
Ogni test invia una sequenza di messaggi al grafo e confronta il risultato.

```bash
python -m tests.run T001
```

- Risultati in `tests/result/`
- Helper disponibili in `tests/_utils.py`
- Per aggiungere un test: registrarlo in `tests/tests.json` e aggiungere il risultato atteso in `tests/result/`

### Test con prompt override (monkeypatch)

Per testare il comportamento del grafo al variare di un prompt LLM senza modificare il codice sorgente, usare un file `tests/T###_<nome>.py` autonomo (non registrato in `tests.json`).

Il meccanismo sfrutta `unittest.mock.patch.object` sui metodi `@staticmethod` di `OrchestratorPrompts`. Il patch è attivo solo durante l'esecuzione del `with` block — nessun effetto collaterale sugli altri test.

```python
from unittest.mock import patch
from tests._utils import run_tests, silence

with silence():
    from saferplaces_multiagent.ma.prompts.supervisor_agent_prompts import OrchestratorPrompts
    from saferplaces_multiagent.ma.prompts import Prompt

def _my_prompt_override() -> Prompt:
    return Prompt({
        "title": "...",
        "description": "...",
        "command": "",
        "message": "...",
    })

with patch.object(OrchestratorPrompts.MainContext, "stable", _my_prompt_override):
    run_tests(MESSAGES, result_file=result_file)
```

Per metodi che accettano `state` come parametro, la firma del mock deve rispettarla:

```python
def _my_override(state) -> Prompt:
    return Prompt({ ... })

with patch.object(OrchestratorPrompts.Plan.CreatePlan, "stable", _my_override):
    run_tests(MESSAGES, result_file=result_file)
```

Esecuzione:

```bash
python -m tests.T006_prompt_override
```

- Risultati in `tests/result/T###.md`
- Vedere `tests/T006_prompt_override.py` come esempio di riferimento
