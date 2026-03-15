---
applyTo: "implementations/**"
---

# Planning — SaferPlaces Multiagent

## ID Namespace

La fonte di verità per tutti i prefissi identificatori è `docs/index.md`.

| Prefisso | Scope |
|---|---|
| `F###` | Feature / scenario / dati |
| `U###` | UI / layout |
| `S###` | Servizi esterni |
| `M###` | Mappa |
| `PLN-###` | Piani di implementazione |

Prima di creare un nuovo piano, verificare l'ultimo ID usato in `docs/index.md`.

## Workflow

```
Functional (F/U/S/M)  →  _plan-todo.md (Open)  →  PLN-### attivo  →  completato  →  archive/  →  Functional diventa "current"
```

1. Definire la feature in `docs/functional-spec*.md` con ID appropriato (registrare in `docs/index.md`)
2. Aprire un item in `implementations/_plan-todo.md`
3. Creare `implementations/PLN-###-<titolo>.md` (piano descrittivo — vedi struttura sotto)
4. Codice pronto in `implementations/PLN-###-files/` (mirroring della struttura `src/`)
5. Pseudocodice/pattern in `implementations/PLN-###-sample/`
6. A completamento, spostare il piano in `implementations/archive/` e aggiornare la functional spec

## `_plan-todo.md`

Contiene **solo** Open items e Active Plans. I completed non compaiono: stanno solo in `implementations/archive/`.

Formato entry:

```
- [ ] PLN-### — <titolo breve> — <stato: draft | active | blocked>
```

## Functional Specs (`docs/`)

- I documenti sono *viventi*: non cancellare righe — usare `status: ❌ removed` e testo ~~barrato~~
- Quando una feature viene implementata, aggiungere `Implementata con: PLN-###` nella riga della tabella e nella sezione di dettaglio
- Quando una feature cambia con un piano, aggiungere `> Aggiornata con PLN-###` nella sezione di dettaglio

## Struttura di un piano `PLN-###-*.md`

Struttura obbligatoria: header con dipendenze → obiettivo → file pronti (tabella) → task (tabella) → sezioni tematiche → checklist

| Sezione | Contenuto |
|---|---|
| **Header / Dipendenze** | PLN-### precedenti da cui dipende, branch di riferimento |
| **Obiettivo** | Cosa si vuole ottenere e perché |
| **Scope / File pronti** | Tabella file coinvolti con stato (`ready` / `todo`) |
| **Task** | Tabella task `T-###-NN` ordinati |
| **Acceptance Criteria** | Come verificare che il piano è completato (checklist `SC-###-NN`) |
| **Note / Rischi** | Dipendenze, edge case, decisioni aperte |

I piani sono documenti **descrittivi**: nessun blocco di codice funzionante inline.  
Eccezioni ammesse: alberi directory testuali, blocchi `diff` ≤ 5 righe a scopo illustrativo.

## Archiviazione

1. Spostare `implementations/PLN-###-*.md` in `implementations/archive/`
2. Aggiornare la functional spec coinvolta (aggiungere `Implementata con: PLN-###`)
3. Rimuovere il piano da Active Plans in `_plan-todo.md`
4. **Non** aggiungere liste di completed a `_plan-todo.md`
