# Map of Content — SaferPlaces Multiagent

> Navigazione hub per le specifiche funzionali e **fonte di verità per il namespace degli identificatori**.
> Per la cronologia dei piani vedere [`implementations/_plan-todo.md`](../implementations/_plan-todo.md).

---

## ID Namespace Registry

Tutti gli identificatori del progetto seguono il formato `PREFIX-###` o `PREFIX-###-NN`.
Nessun prefisso è condiviso tra categorie diverse.

### Prefissi riservati

| Prefisso | Categoria | Formato | Esempio | Note |
|---|---|---|---|---|
| `F` | Feature — agenti, tool, flussi del grafo | `F###` | `F001` | `docs/functional-spec*.md` |
| `PLN` | Piano di implementazione | `PLN-###` | `PLN-001` | `implementations/PLN-###-*.md` |
| `T` | Task interno a un piano | `T-###-NN` | `T-001-01` | `###` = numero piano, `NN` = sequenza |
| `SC` | Success Criteria di un piano | `SC-###-NN` | `SC-001-01` | `###` = numero piano, `NN` = sequenza |

### Lettere disponibili per nuovi domini feature

`A` `B` `C` `D` `E` `G` `H` `I` `J` `K` `L` `M` `N` `O` `Q` `R` `S` `U` `V` `W` `X` `Y` `Z`

> `P` è **non assegnabile** a feature (collide visivamente con `PLN`).
> `T` e `SC` sono riservati ai task e criteri interni ai piani.
