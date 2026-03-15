---
applyTo: "docs/**"
---

# Docs — SaferPlaces Multiagent

## Tipi di documento

| Documento | Tipo | Regola |
|---|---|---|
| `docs/index.md` | **Riferimento** | Hub di navigazione e fonte di verità del namespace ID. Aggiornare aggiungendo nuovi prefissi, mai rimuovere righe esistenti. |
| `docs/functional-spec.md` | **Vivente** | Feature `F###` (scenario, dati, utility). Modificare quando una feature cambia stato. |
| `docs/functional-spec-services.md` | **Vivente** | Feature `S###` (servizi esterni, API, env vars). |
| `docs/functional-spec-map.md` | **Vivente** | Feature `M###` (mappa, layer geospaziali: Cesium 3D, Leafmap, geospatial ops). |
| `docs/architecture.md` | **Vivente** | Schema DB corrente, route API con protezione, variabili d'ambiente, topologia infra. Aggiornato al completamento di ogni PLN che tocca DB/API/infra. |
| `docs/multiagent-guidlines*.md` | **Riferimento** | Linee guida di design. Non modificare senza una decisione architetturale esplicita. |

## `docs/index.md` — Namespace ID

Ogni ID segue il formato `<PREFISSO>###` (es. `F001`, `PLN-001`).  
Prima di assegnare un nuovo ID, leggere `docs/index.md` per determinare il prossimo numero disponibile.  
Aggiornare `docs/index.md` immediatamente dopo aver assegnato un nuovo ID.

## Stile della documentazione

- Usare tabelle Markdown per elenchi di proprietà/campi
- I riferimenti a file del repo vanno come link relativi (es. `[states.py](../src/saferplaces_multiagent/common/states.py)`)
- Non includere blocchi di codice inline nei documenti di piano (`PLN-###`) — il codice va nei file `PLN-###-files/`
- Lingua: italiano per testi descrittivi; inglese per nomi di variabili, classi, path

## Markdown Conventions

Follow established community standards:

- [CommonMark](https://commonmark.org/) - Specification compliance
- [Google Markdown Style Guide](https://google.github.io/styleguide/docguide/style.html) - Style rules
- [Prettier](https://prettier.io/docs/en/options.html#prose-wrap) - Formatting defaults
