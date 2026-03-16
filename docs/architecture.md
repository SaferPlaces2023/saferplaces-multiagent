# Architecture — SaferPlaces Multiagent

> **Tipo**: Vivente — aggiornare al completamento di ogni PLN che modifica DB/API/infra.
> **Ultima modifica**: N/A (template iniziale)

---

## Schema Database

*Da completare quando implementeranno storage persistente.*

### Tabelle

| Tabella | Scopo | Campi principali |
|---|---|---|
| *N/A* | — | — |

---

## API Routes

*Da completare quando la topologia API sarà definita.*

### Endpoints protetti

| Metodo | Endpoint | Autenticazione | Descrizione |
|---|---|---|---|
| *N/A* | — | — | — |

---

## Variabili d'Ambiente

*Da completare quando gli env vars saranno centralizzati.*

| Variabile | Valore default | Scopo | Modificabile |
|---|---|---|---|
| `LANGGRAPH_SERVER` | `http://localhost:8100` | Server LangGraph | ✅ Dev |
| `FLASK_SERVER` | `http://localhost:5000` | Flask API | ✅ Dev |
| S3 bucket | `saferplaces.co` | Storage utente | ✅ Config |

---

## Topologia Infra

### Deployment locale (dev)

```
LangGraph Server (port 8100)
    ↓ (invoca)
Flask Server (port 5000)
    ↓ (salva su)
S3 bucket (saferplaces.co)
```

### Deployment production

*Da completare quando sarà definita la topologia prod.*

---

## Versioni Dipendenze Critiche

*Documentare le versioni critiche qui quando necessario.*

| Dipendenza | Versione | Motivazione |
|---|---|---|
| — | — | — |

