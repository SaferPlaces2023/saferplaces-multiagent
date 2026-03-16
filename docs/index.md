# Map of Content — SaferPlaces Multiagent

> Navigazione hub per le specifiche funzionali e **fonte di verità per il namespace degli identificatori**.
> Per la cronologia dei piani vedere [`implementations/_plan-todo.md`](../implementations/_plan-todo.md).

---

## ID Namespace Registry

Tutti gli identificatori del progetto seguono il formato `PREFIX-###` o `PREFIX-###-NN`.
Nessun prefisso è condiviso tra categorie diverse.

### Prefissi riservati

| Prefisso | Categoria | Formato | Esempio | File di specifica |
|---|---|---|---|---|
| `F` | Feature — scenario, dati, utility | `F###` | `F001`–`F009` | `docs/functional-spec.md` |
| `S` | Feature — servizi esterni, API, env vars | `S###` | `S001` | `docs/functional-spec-services.md` |
| `M` | Feature — mappa, layer geospaziali (Cesium, Leafmap, geospatial ops) | `M###` | `M001` | `docs/functional-spec-map.md` |
| `PLN` | Piano di implementazione | `PLN-###` | `PLN-001` | `implementations/PLN-###-*.md` |
| `T` | Task interno a un piano | `T-###-NN` | `T-001-01` | `###` = numero piano, `NN` = sequenza |
| `SC` | Success Criteria di un piano | `SC-###-NN` | `SC-001-01` | `###` = numero piano, `NN` = sequenza |

### Lettere disponibili per nuovi domini feature

`A` `B` `C` `D` `E` `G` `H` `I` `J` `K` `L` `N` `O` `Q` `R` `U` `V` `W` `X` `Y` `Z`

> `P` è **non assegnabile** a feature (collide visivamente con `PLN`).
> `T` e `SC` sono riservati ai task e criteri interni ai piani.
> `F`, `S`, `M` sono già assegnati — vedi tabella sopra.
> `U` è disponibile: la UI frontend vive nel repo esterno `saferplaces-multiagent-frontend`.

---

## Flusso di implementazione

```
INSTRUCTIONS → PLAN → DOCS → INSTRUCTIONS → (loop …)
```

| Fase | Chi | Artefatto |
|---|---|---|
| **INSTRUCTIONS** | Coding Agent | `.github/copilot-instructions.md`, `.github/instructions/*.instructions.md` |
| **PLAN** | PM | `implementations/PLN-###-*.md`, `implementations/_plan-todo.md` |
| **DOCS** | Sviluppatore + Coding Agent | `docs/functional-spec*.md`, `docs/architecture.md` |

I `docs/functional-spec*.md` sono la fonte di verità **vivente**: vengono aggiornati sia dallo sviluppatore sia dal Coding Agent al termine di ogni piano che modifica una feature.

---

## Convenzione Documentazione

| Documento | Tipo | Scopo |
|---|---|---|
| `docs/functional-spec*.md` | **Vivente** | Stato attuale delle funzionalità (`F###`, `M###`, `S###`). Si modifica quando una feature cambia. |
| `docs/index.md` | **Riferimento** | Hub di navigazione e fonte di verità per il namespace degli identificatori. |
| `docs/architecture.md` | **Vivente** | Schema DB corrente, route API con protezione, variabili d'ambiente, topologia infra. Aggiornato al completamento di ogni PLN che tocca DB/API/infra. |
| `implementations/_plan-todo.md` | **Vivente** | Solo Open items e Piani attivi. I completati stanno in `archive/`. |
| `implementations/PLN-###-*.md` | **Attivo** | Piano in corso — descrittivo, nessun codice inline. Codice in `PLN-###-files/`. |
| `implementations/archive/PLN-###-*.md` | **Storico** | Piani completati — sola lettura. |
