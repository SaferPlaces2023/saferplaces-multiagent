"""
GEO QUERY TOOL — LangGraph Graph Implementation
================================================
Stessa architettura Plan→Inspect→Code→Reflect, riscritta come grafo LangGraph.

Legenda:
  [REAL CODE]        → logica chiara, usabile quasi as-is
  [YOUR BASE]        → dipende dalla tua infrastruttura (LLM client, sandbox, storage)
  [YOUR SCHEMA]      → dipende dai tuoi modelli/tipi esistenti
  [PLACEHOLDER: ...] → logica da implementare con dettagli che non conosco

Struttura del grafo:
  ┌─────────┐     ┌─────────┐     ┌──────────┐     ┌─────────┐     ┌────────┐
  │  PLAN   │────▶│ INSPECT │────▶│   CODE   │────▶│ REFLECT │────▶│ OUTPUT │
  └─────────┘     └─────────┘     └──────────┘     └─────────┘     └────────┘
                       ▲               ▲                 │
                       └───────────────┴─── replan ──────┘
"""

from __future__ import annotations
from typing import Any, Literal, Annotated

import os
import json
import operator

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

import geopandas as gpd
import rasterio

from ...common.states import GeoOpsAgentState, MABaseGraphState
from ...common import utils, s3_utils

# [YOUR BASE] importa il tuo LLM client wrappato come LangChain BaseChatModel
# from langchain_anthropic import ChatAnthropic
# from langchain_openai import ChatOpenAI


# ─────────────────────────────────────────────────────────────────────────────
# STATE — il cuore del grafo
# Tutto ciò che i nodi leggono e scrivono passa da qui.
# ─────────────────────────────────────────────────────────────────────────────



def _initial_state(task: str, layers: list[dict], output_hint: str) -> dict:
    """[REAL CODE] Costruisce lo stato iniziale vuoto."""
    return {
        "task": task,
        "layers": layers,
        "output_hint": output_hint,
        "plan_steps": [],
        "output_format": "auto",
        "declared_uncertainties": [],
        "layer_schemas": {},
        "sampled_values": {},
        "spatial_relations": {},
        "scalar_results": {},
        "derived_layer_refs": {},
        "inspect_findings": {},
        "code_history": [],
        "error_log": [],
        "current_step_ids": [],
        "completed_step_ids": [],
        "iteration_count": 0,
        "reflect_decision": "",
        "reflect_reason": "",
        "result": None,
    }


def _get_agent_state(state: MABaseGraphState) -> GeoOpsAgentState:
    """Extracts the GeoOpsAgentState from the MABaseGraphState."""
    return state["geo_ops_agent_state"]

def _update_agent_state(agent_state_updates: GeoOpsAgentState) -> MABaseGraphState:
    state = { "geo_ops_agent_state": agent_state_updates }
    return state


# ─────────────────────────────────────────────────────────────────────────────
# NODO 1 — PLAN
# Input:  task, layers, output_hint
# Output: plan_steps, output_format, declared_uncertainties, current_step_ids
# ─────────────────────────────────────────────────────────────────────────────

def node_plan(state: MABaseGraphState) -> dict:
    """
    Pianifica i passi necessari per rispondere al task.
    Dichiara esplicitamente le incertezze che richiedono uno step inspect.
    Viene eseguito UNA sola volta (all'inizio), salvo replan da Reflect.
    """

    agent_state = _get_agent_state(state)

    system_prompt = """
    Sei un geo-planner. Pianifica i passi per rispondere a una richiesta geospaziale.
    Rispondi SOLO in JSON con questo schema esatto:
    {
      "steps": [
        {
          "id": <int>,
          "type": "inspect" | "code",
          "layer_ids": ["<layer_id>", ...],
          "depends_on": [<step_id>, ...],
          "reason": "<perché questo step>",
          "uncertainty": "<cosa non sai ancora, o null>"
        }
      ],
      "output_format": "info" | "layer" | "both",
      "declared_uncertainties": ["<stringa>", ...]
    }

    Regole:
    - Se non sei certo della struttura interna di un layer (nomi colonne,
      valori categorici, CRS), aggiungi uno step 'inspect' PRIMA del relativo 'code'.
    - Gli step 'code' che usano output di altri step devono dichiararli in depends_on.
    - Sii conservativo: un inspect in più è meglio di codice scritto alla cieca.
    - Non includere step di tipo 'output': quello è un nodo separato del grafo.
    """

    user_prompt = f"""
    Task: {agent_state["task"]}
    Output hint: {agent_state["output_hint"]}

    Layer disponibili:
    {json.dumps(agent_state["layers"], indent=2)}
    """

    # [YOUR BASE] Chiama il tuo LLM
    raw = utils._base_llm.invoke(system_prompt, user_prompt)
    plan_dict = json.loads(raw)

    # [REAL CODE] Identifica i primi step eseguibili (quelli senza depends_on)
    first_steps = [
        s["id"] for s in plan_dict["steps"]
        if not s.get("depends_on")
    ]

    agent_state_updates = {
        "plan_steps": plan_dict["steps"],
        "output_format": plan_dict["output_format"],
        "declared_uncertainties": plan_dict.get("declared_uncertainties", []),
        "current_step_ids": first_steps,
    }

    return _update_agent_state(agent_state_updates)


# ─────────────────────────────────────────────────────────────────────────────
# NODO 2 — INSPECT
# Input:  current_step_ids, plan_steps, layers, + tutto il context
# Output: layer_schemas, sampled_values, spatial_relations, inspect_findings,
#         completed_step_ids, current_step_ids (aggiornato al prossimo batch)
# ─────────────────────────────────────────────────────────────────────────────

def node_inspect(state: GeoOpsAgentState) -> dict:
    """
    Esegue gli step di tipo 'inspect' presenti in current_step_ids.
    Popola il context con informazioni fattuali sui layer reali.
    Non genera codice, non modifica dati.
    """
    agent_state = _get_agent_state(state)


    # [REAL CODE] Filtra solo gli step inspect tra quelli correnti
    inspect_steps = [
        s for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"] and s["type"] == "inspect"
    ]

    # Copie mutabili delle sezioni di context che questo nodo modifica
    layer_schemas     = dict(agent_state["layer_schemas"])
    sampled_values    = dict(agent_state["sampled_values"])
    spatial_relations = dict(agent_state["spatial_relations"])
    inspect_findings  = dict(agent_state["inspect_findings"])
    newly_completed   = []

    for step in inspect_steps:
        for layer_id in step["layer_ids"]:

            if layer_id in layer_schemas:
                continue   # già ispezionato in iterazione precedente

            meta = _get_layer_meta(layer_id, agent_state["layers"])

            if meta.get("type") == "vector":
                schema, samples, findings = _inspect_vector(meta)
                layer_schemas[layer_id]  = schema
                sampled_values.update({f"{layer_id}.{k}": v for k, v in samples.items()})
                inspect_findings[layer_id] = findings

            elif meta.get("type") == "raster":
                schema, findings = _inspect_raster(meta)
                layer_schemas[layer_id]    = schema
                inspect_findings[layer_id] = findings

        # [REAL CODE] Verifica sovrapposizioni spaziali tra i layer di questo step
        for i, lid_a in enumerate(step["layer_ids"]):
            for lid_b in step["layer_ids"][i+1:]:
                key = f"{lid_a}_x_{lid_b}"
                if key not in spatial_relations:
                    spatial_relations[key] = check_bbox_overlap(
                        _get_layer_meta(lid_a, agent_state["layers"]),
                        _get_layer_meta(lid_b, agent_state["layers"]),
                    )

        newly_completed.append(step["id"])

    # [REAL CODE] Aggiorna current_step_ids: rimuovi i completati, aggiungi i sbloccati
    updated_completed = agent_state["completed_step_ids"] + newly_completed
    next_steps = _compute_next_steps(agent_state["plan_steps"], updated_completed)

    agent_state_updates = {
        "layer_schemas":     layer_schemas,
        "sampled_values":    sampled_values,
        "spatial_relations": spatial_relations,
        "inspect_findings":  inspect_findings,
        "completed_step_ids": updated_completed,
        "current_step_ids":  next_steps,
    }
    return _update_agent_state(agent_state_updates)


def _inspect_vector(meta: dict) -> tuple[dict, dict, dict]:
    """
    [YOUR BASE] Apri il layer vettoriale in modo lazy (solo schema, no dati).
    Esempio con geopandas + pyogrio:
        gdf_empty = gpd.read_file(url, rows=0)
        schema = gdf_empty.dtypes.to_dict()

    Restituisce: (schema, {col: [valori_campionati]}, findings_summary)
    """
    url        = meta["metadata"]["download_url"]
    attributes = meta["metadata"].get("attributes", {})

    # [PLACEHOLDER: apri schema con gpd.read_file(url, rows=0)]
    schema = open_vector_schema(url)

    # [PLACEHOLDER: campiona valori categorici con query SQL lazy]
    samples = {}
    for col, info in attributes.items():
        if info.get("type") == "categorical":
            samples[col] = PLACEHOLDER_sample_column_values(url, col, limit=50)

    findings = {
        "columns":       list(schema.keys()),
        "n_features":    meta["metadata"].get("n_features"),
        "geometry_type": meta["metadata"].get("geometry_type"),
        "crs":           meta["metadata"].get("crs"),
        "sampled_categories": samples,
    }
    return schema, samples, findings


def _inspect_raster(meta: dict) -> tuple[dict, dict]:
    """
    [YOUR BASE] Apri il raster e leggi solo i metadati (niente pixel).
    Esempio con rasterio:
        with rasterio.open(url) as src:
            schema = {"crs": str(src.crs), "res": src.res, "bounds": src.bounds}

    Restituisce: (schema, findings_summary)
    """
    # [PLACEHOLDER: apri metadati raster con rasterio]
    schema = open_raster_schema(meta["metadata"]["download_url"])

    findings = {
        "crs":          meta["metadata"].get("crs"),
        "min":          meta["metadata"].get("min"),
        "max":          meta["metadata"].get("max"),
        "nodata":       meta["metadata"].get("nodata"),
        "surface_type": meta["metadata"].get("surface_type"),
    }
    return schema, findings


# ─────────────────────────────────────────────────────────────────────────────
# NODO 3 — CODE
# Input:  current_step_ids, plan_steps, layers, tutto il context
# Output: scalar_results, derived_layer_refs, code_history (append), error_log (append),
#         completed_step_ids, current_step_ids
# ─────────────────────────────────────────────────────────────────────────────

def node_code(state: MABaseGraphState) -> dict:
    """
    Per ogni step 'code' in current_step_ids:
      1. Genera codice Python geospaziale via LLM (con context pieno)
      2. Esegue in sandbox
      3. Archivia risultato nel context
    """

    agent_state = _get_agent_state(state)

    code_steps = [
        s for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"] and s["type"] == "code"
    ]

    scalar_results     = dict(agent_state["scalar_results"])
    derived_layer_refs = dict(agent_state["derived_layer_refs"])
    new_code_history   = []   # accumulato localmente, poi append via operator.add
    new_error_log      = []
    newly_completed    = []

    for step in code_steps:

        # ── Genera codice ─────────────────────────────────────────────────────
        generated_code = _generate_code_for_step(step, agent_state)

        # ── Esegui in sandbox ─────────────────────────────────────────────────
        sandbox_namespace = {
            "layers_meta": {
                lid: _get_layer_meta(lid, agent_state["layers"])
                for lid in step["layer_ids"]
            },
            "context": {
                "layer_schemas":      agent_state["layer_schemas"],
                "inspect_findings":   agent_state["inspect_findings"],
                "sampled_values":     agent_state["sampled_values"],
                "scalar_results":     scalar_results,
                "derived_layer_refs": derived_layer_refs,
            },
            "derived": derived_layer_refs,
        }

        exec_result = _execute_in_sandbox(generated_code, sandbox_namespace, timeout_seconds=60)

        # ── Registra nel log ──────────────────────────────────────────────────
        history_entry = {
            "step_id": step["id"],
            "code":    generated_code,
            "result":  exec_result.get("result"),
            "error":   exec_result.get("error"),
        }
        new_code_history.append(history_entry)

        if exec_result.get("error"):
            new_error_log.append({
                "step_id":   step["id"],
                "error":     exec_result["error"],
                "traceback": exec_result.get("traceback"),
            })
            # Non aggiungere a newly_completed: Reflect deciderà se ripianificare
            continue

        # ── Archivia output nel context ───────────────────────────────────────
        result      = exec_result["result"]
        result_type = result.get("type") if result else None

        if result_type == "scalar":
            scalar_results[result["name"]] = result["data"]

        elif result_type == "geodataframe":
            # [PLACEHOLDER: salva GDF intermedio su file temporaneo]
            temp_path = save_temp_geodataframe(result["data"], result["name"])
            derived_layer_refs[result["name"]] = temp_path

        elif result_type == "raster_path":
            derived_layer_refs[result["name"]] = result["data"]

        newly_completed.append(step["id"])

    # [REAL CODE] Aggiorna completed e calcola prossimo batch
    updated_completed = agent_state["completed_step_ids"] + newly_completed
    next_steps = _compute_next_steps(agent_state["plan_steps"], updated_completed)

    agent_state_updates = {
        "scalar_results":     scalar_results,
        "derived_layer_refs": derived_layer_refs,
        "code_history":       new_code_history,    # operator.add → append
        "error_log":          new_error_log,       # operator.add → append
        "completed_step_ids": updated_completed,
        "current_step_ids":   next_steps,
        "iteration_count":    agent_state["iteration_count"] + 1,
    }

    return _update_agent_state(agent_state_updates)


def _generate_code_for_step(step: dict, state: MABaseGraphState) -> str:
    """Chiama l'LLM per generare codice Python geospaziale per questo step."""

    agent_state = _get_agent_state(state)

    context_summary = _serialize_context_for_llm(agent_state)

    system_prompt = """
    Sei un esperto di geopandas e rasterio. Scrivi codice Python ATOMICO che
    esegue UNA sola operazione geospaziale ben definita.

    Variabili disponibili nel namespace di esecuzione:
    - `layers_meta`: dict {layer_id: metadati completi dal layer registry}
    - `context`: dict con layer_schemas, inspect_findings, sampled_values,
                 scalar_results, derived_layer_refs
    - `derived`: dict {nome_logico: path_file} — output di step precedenti

    Il codice DEVE terminare assegnando a `result`:
    {
      "type": "geodataframe" | "scalar" | "raster_path",
      "data": <GeoDataFrame | valore | path_stringa>,
      "name": "<nome_logico_dell_output>"
    }

    Vincoli:
    - Usa sempre download_url per aprire i layer, non src S3 diretto
    - Riproietta in CRS comune prima di ogni operazione spaziale
    - Non fare assunzioni sui nomi colonne: leggili da context["inspect_findings"]
    - Non scrivere su disco (usa buffer in memoria o restituisci il path se raster)
    """

    user_prompt = f"""
    Task originale: {agent_state["task"]}

    Step da eseguire:
    - Motivo: {step["reason"]}
    - Layer coinvolti: {step["layer_ids"]}
    - Dipende dagli step: {step.get("depends_on", [])}

    Context disponibile (schema, findings, valori campionati):
    {context_summary}

    Metadati layer coinvolti:
    {json.dumps(
        {lid: _get_layer_meta(lid, agent_state["layers"]) for lid in step["layer_ids"]},
        indent=2
    )}

    Scrivi SOLO il codice Python. Nessuna spiegazione, nessun markdown.
    """

    # [YOUR BASE] Chiama il tuo LLM client
    return utils._base_llm.invoke(system_prompt, user_prompt)


def _execute_in_sandbox(code: str, namespace: dict, timeout_seconds: int) -> dict:
    """
    [YOUR BASE] Esegui il codice in modo isolato.
    Vedi commento esteso nel file precedente per le opzioni di sandbox.
    Restituisce sempre {"result": <valore o None>, "error": <stringa o None>}
    """
    try:
        exec_globals = { **namespace }
        exec(code, exec_globals)    # [YOUR BASE] sostituisci con sandbox reale
        return {"result": exec_globals.get("result"), "error": None}
    except Exception as e:
        import traceback as tb
        return {"result": None, "error": str(e), "traceback": tb.format_exc()}


# ─────────────────────────────────────────────────────────────────────────────
# NODO 4 — REFLECT
# Input:  tutto lo stato
# Output: reflect_decision, reflect_reason
# ─────────────────────────────────────────────────────────────────────────────

def node_reflect(state: MABaseGraphState) -> dict:
    """
    Valuta se il task è completato o se serve ripianificare.
    Imposta reflect_decision: "complete" | "continue" | "replan"

    - "complete"  → tutti gli step sono fatti e il risultato è sufficiente
    - "continue"  → ci sono ancora step pending nel piano corrente
    - "replan"    → errori o risultati anomali richiedono nuovi step
    """
    agent_state = _get_agent_state(state)

    # [REAL CODE] Step pending = nel piano ma non ancora completati
    non_output_steps = [s for s in agent_state["plan_steps"] if s["type"] != "output"]
    pending_ids = [
        s["id"] for s in non_output_steps
        if s["id"] not in agent_state["completed_step_ids"]
    ]
    unresolved_errors = _get_unresolved_errors(agent_state)

    # Fast-path: step pending senza errori bloccanti → continue senza LLM call
    if pending_ids and not unresolved_errors:
        return {
            "reflect_decision": "continue",
            "reflect_reason":   f"Step ancora da eseguire: {pending_ids}",
        }

    # Altrimenti chiedi all'LLM di valutare
    system_prompt = """
    Valuta se il task geospaziale è stato completato con successo.
    Rispondi SOLO in JSON: {
      "decision": "complete" | "continue" | "replan",
      "reason": "<motivazione breve>"
    }

    Usa "replan" se:
    - Ci sono errori non recuperati che bloccano la risposta
    - I risultati sono anomali (es: count=0 quando non dovrebbe essere)
    - Mancano passaggi per raggiungere l'output richiesto

    Usa "complete" se il task è risposto con sufficiente confidenza.
    Usa "continue" se ci sono step pending nel piano che non hanno ancora girato.
    """

    user_prompt = f"""
    Task originale: {agent_state["task"]}
    Output atteso: {agent_state["output_format"]}
    Iterazione corrente: {agent_state["iteration_count"]}

    Step completati: {agent_state["completed_step_ids"]}
    Step pending: {pending_ids}

    Risultati scalari: {json.dumps(agent_state["scalar_results"], indent=2)}
    Layer derivati prodotti: {list(agent_state["derived_layer_refs"].keys())}

    Errori non risolti:
    {json.dumps(unresolved_errors, indent=2)}

    Ultimi step eseguiti:
    {json.dumps(agent_state["code_history"][-3:], indent=2, default=str)}
    """

    # [YOUR BASE] Chiama LLM
    raw = utils._base_llm.invoke(system_prompt, user_prompt)
    decision_dict = json.loads(raw)

    agent_state_updates = {
        "reflect_decision": decision_dict["decision"],
        "reflect_reason":   decision_dict["reason"],
    }

    return _update_agent_state(agent_state_updates)


# ─────────────────────────────────────────────────────────────────────────────
# NODO 4b — REPLAN  (raggiunto solo se Reflect decide "replan")
# Input:  tutto lo stato + reflect_reason
# Output: plan_steps (con nuovi step aggiunti), current_step_ids
# ─────────────────────────────────────────────────────────────────────────────

def node_replan(state: MABaseGraphState) -> dict:
    """
    Genera nuovi step da aggiungere al piano esistente.
    Non riscrive il piano: appende step con id progressivi.
    """

    agent_state = _get_agent_state(state)

    max_existing_id = max((s["id"] for s in agent_state["plan_steps"]), default=0)

    system_prompt = """
    Sei un geo-replanner. Il piano corrente ha incontrato problemi o è incompleto.
    Genera SOLO i nuovi step mancanti in JSON array (stesso schema del planner).
    Non ripetere step già completati con successo.
    """

    user_prompt = f"""
    Task originale: {agent_state["task"]}
    Motivo del replan: {agent_state["reflect_reason"]}

    Step già completati: {agent_state["completed_step_ids"]}
    Piano corrente: {json.dumps(agent_state["plan_steps"], indent=2)}

    Context disponibile:
    {_serialize_context_for_llm(agent_state)}

    Errori da correggere:
    {json.dumps(_get_unresolved_errors(agent_state), indent=2)}
    """

    # [YOUR BASE] Chiama LLM
    raw = utils._base_llm.invoke(system_prompt, user_prompt)
    new_steps_raw = json.loads(raw)

    # [REAL CODE] Rinumera id per non collidere con quelli esistenti
    new_steps = [
        {**s, "id": s["id"] + max_existing_id}
        for s in new_steps_raw
    ]

    updated_plan = agent_state["plan_steps"] + new_steps

    # [REAL CODE] Calcola i nuovi step immediatamente eseguibili
    next_steps = _compute_next_steps(updated_plan, agent_state["completed_step_ids"])

    agent_state_updates = {
        "plan_steps":       updated_plan,
        "current_step_ids": next_steps,
    }

    return _update_agent_state(agent_state_updates)


# ─────────────────────────────────────────────────────────────────────────────
# NODO 5 — OUTPUT
# Input:  tutto lo stato finale
# Output: result (GeoToolResult serializzato)
# ─────────────────────────────────────────────────────────────────────────────

def node_output(state: MABaseGraphState) -> dict:
    """
    Assembla il risultato finale:
    - Genera summary in linguaggio naturale
    - Persiste i layer derivati su storage e li converte in formato registry
    - Valuta confidence
    """

    agent_state = _get_agent_state(state)    

    # ── Summary in linguaggio naturale ────────────────────────────────────────
    summary_prompt = f"""
    Sintetizza in modo chiaro e conciso il risultato di questa analisi geospaziale.
    Sii diretto: dai i numeri, i nomi dei layer prodotti, le conclusioni.

    Task: {agent_state["task"]}
    Risultati scalari: {json.dumps(agent_state["scalar_results"])}
    Layer derivati prodotti: {list(agent_state["derived_layer_refs"].keys())}
    Errori incontrati: {len(agent_state["error_log"])}
    """

    # [YOUR BASE] Chiama LLM
    summary = utils._base_llm.invoke("Sei conciso e preciso.", summary_prompt)

    # ── Persisti layer derivati e converti in formato registry ────────────────
    new_layers = []
    for logical_name, temp_path in agent_state["derived_layer_refs"].items():
        # [PLACEHOLDER: upload su S3, costruisci dict nel formato layer registry]
        layer_entry = persist_derived_layer(logical_name, temp_path, state)
        new_layers.append(layer_entry)

    # ── Confidence ────────────────────────────────────────────────────────────
    # [REAL CODE] Euristica semplice
    n_errors   = len(agent_state["error_log"])
    confidence = "high" if n_errors == 0 else ("medium" if n_errors <= 1 else "low")

    result = {
        "answer_type":               agent_state["output_format"],
        "natural_language_summary":  summary,
        "scalar_data":               agent_state["scalar_results"],
        "new_layers":                new_layers,
        "steps_executed": [
            {"step_id": h["step_id"], "had_error": bool(h.get("error"))}
            for h in agent_state["code_history"]
        ],
        "confidence": confidence,
    }

    agent_state_updates = {"result": result}

    return _update_agent_state(agent_state_updates)


# ─────────────────────────────────────────────────────────────────────────────
# EDGE CONDITIONS — decidono il percorso nel grafo dopo ogni nodo
# ─────────────────────────────────────────────────────────────────────────────

def edge_after_plan(state: MABaseGraphState) -> str:
    """Dopo il piano: vai a inspect se ci sono step inspect, altrimenti a code."""
    # [REAL CODE]
    agent_state = _get_agent_state(state)
    current_types = {
        s["type"]
        for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"]
    }
    return "inspect" if "inspect" in current_types else "code"


def edge_after_inspect(state: MABaseGraphState) -> str:
    """Dopo inspect: controlla i prossimi step sbloccati."""
    # [REAL CODE]
    agent_state = _get_agent_state(state)
    if not agent_state["current_step_ids"]:
        return "reflect"
    current_types = {
        s["type"]
        for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"]
    }
    if "code"    in current_types: return "code"
    if "inspect" in current_types: return "inspect"   # inspect in cascata (raro)
    return "reflect"


def edge_after_code(state: MABaseGraphState) -> str:
    """Dopo code: se ci sono ancora step pending torna nel loop, altrimenti reflect."""
    agent_state = _get_agent_state(state)
    # [REAL CODE]
    if not agent_state["current_step_ids"]:
        return "reflect"
    current_types = {
        s["type"]
        for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"]
    }
    if "inspect" in current_types: return "inspect"
    if "code"    in current_types: return "code"
    return "reflect"


def edge_after_reflect(state: MABaseGraphState) -> str:
    """
    Cuore del ciclo condizionale.
    "complete"  → output (fine)
    "replan"    → nodo replan (poi torna nel loop)
    "continue"  → torna a inspect o code (step pending nel piano corrente)
    Guardrail:  se superiamo max_iterations → output forzato
    """
    agent_state = _get_agent_state(state)
    # [REAL CODE]
    MAX_ITERATIONS = 5   # [YOUR BASE] rendi configurabile

    if agent_state["iteration_count"] >= MAX_ITERATIONS:
        return "output"   # forza uscita anche se incompleto

    decision = agent_state["reflect_decision"]

    if decision == "complete":
        return "output"
    if decision == "replan":
        return "replan"

    # "continue" → segui i step pending
    current_types = {
        s["type"]
        for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"]
    }
    return "inspect" if "inspect" in current_types else "code"


def edge_after_replan(state: MABaseGraphState) -> str:
    """Dopo replan: vai al primo tipo di step aggiunto."""
    
    agent_state = _get_agent_state(state)

    current_types = {
        s["type"]
        for s in agent_state["plan_steps"]
        if s["id"] in agent_state["current_step_ids"]
    }
    return "inspect" if "inspect" in current_types else "code"


# ─────────────────────────────────────────────────────────────────────────────
# COSTRUZIONE DEL GRAFO
# ─────────────────────────────────────────────────────────────────────────────

def build_geo_agent_graph() -> CompiledStateGraph:
    """
    [REAL CODE] Assembla e compila il grafo LangGraph.
    Chiamato UNA volta all'avvio (il grafo compilato è riusabile).
    """

    builder = StateGraph(GeoOpsAgentState)

    # ── Nodi ──────────────────────────────────────────────────────────────────
    builder.add_node("plan",    node_plan)
    builder.add_node("inspect", node_inspect)
    builder.add_node("code",    node_code)
    builder.add_node("reflect", node_reflect)
    builder.add_node("replan",  node_replan)
    builder.add_node("output",  node_output)

    # ── Entry point ───────────────────────────────────────────────────────────
    builder.set_entry_point("plan")

    # ── Edge condizionali ─────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "plan",
        edge_after_plan,
        {"inspect": "inspect", "code": "code"},
    )
    builder.add_conditional_edges(
        "inspect",
        edge_after_inspect,
        {"inspect": "inspect", "code": "code", "reflect": "reflect"},
    )
    builder.add_conditional_edges(
        "code",
        edge_after_code,
        {"inspect": "inspect", "code": "code", "reflect": "reflect"},
    )
    builder.add_conditional_edges(
        "reflect",
        edge_after_reflect,
        {"output": "output", "replan": "replan", "inspect": "inspect", "code": "code"},
    )
    builder.add_conditional_edges(
        "replan",
        edge_after_replan,
        {"inspect": "inspect", "code": "code"},
    )

    # ── Edge finale ───────────────────────────────────────────────────────────
    builder.add_edge("output", END)

    return builder.compile()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT — chiamato dall'agente LangGraph principale
# ─────────────────────────────────────────────────────────────────────────────

# [REAL CODE] Compila il grafo una volta sola (singleton a livello di modulo)
geo_agent_graph: CompiledStateGraph = build_geo_agent_graph()


def geo_query_tool(
    task: str,
    layers: list[dict],
    output_hint: Literal["info", "layer", "both", "auto"] = "auto",
) -> dict:
    """
    Entry point chiamato dall'agente LangGraph principale.
    Interfaccia identica al file precedente — l'interno è ora un grafo.
    """
    initial_state = _initial_state(task, layers, output_hint)
    final_state   = geo_agent_graph.invoke(initial_state)
    return final_state["result"]


# ─────────────────────────────────────────────────────────────────────────────
# HELPER CONDIVISI TRA I NODI
# ─────────────────────────────────────────────────────────────────────────────

def _compute_next_steps(plan_steps: list[dict], completed_ids: list[int]) -> list[int]:
    """
    [REAL CODE] Step ora eseguibili: non completati, non 'output',
    con tutti i depends_on già in completed_ids.
    """
    completed_set = set(completed_ids)
    return [
        s["id"]
        for s in plan_steps
        if s["id"] not in completed_set
        and s["type"] != "output"
        and all(dep in completed_set for dep in s.get("depends_on", []))
    ]


def _get_unresolved_errors(state: GeoOpsAgentState) -> list[dict]:
    """
    [REAL CODE] Errori per step che non sono stati recuperati
    (= nessuna esecuzione successiva dello stesso step_id senza errore).
    """
    recovered_ids = {
        h["step_id"]
        for h in state["code_history"]
        if not h.get("error")
    }
    return [e for e in state["error_log"] if e["step_id"] not in recovered_ids]


def _get_layer_meta(layer_id: str, layers: list[dict]) -> dict:
    """[REAL CODE] Lookup nel registry per layer_id."""
    return next((l for l in layers if l["id"] == layer_id), {})


def _serialize_context_for_llm(state: GeoOpsAgentState) -> str:
    """
    [REAL CODE] Versione compatta del context per i prompt LLM.
    Esclude dati voluminosi (GeoDataFrame, array pixel).
    """
    return json.dumps({
        "layer_schemas":       state["layer_schemas"],
        "inspect_findings":    state["inspect_findings"],
        "sampled_values":      state["sampled_values"],
        "scalar_results":      state["scalar_results"],
        "derived_layer_names": list(state["derived_layer_refs"].keys()),
        "errors_so_far":       state["error_log"],
    }, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# PLACEHOLDER (dipendono dalla tua base — stessi del file precedente)
# ─────────────────────────────────────────────────────────────────────────────

def open_vector_schema(url: str) -> dict:
    """gpd.read_file(url, rows=0).dtypes.to_dict()"""
    raise gpd.read_file(url, rows=0).dtypes.to_dict()

def open_raster_schema(url: str) -> dict:
    """with rasterio.open(url) as src: return dict(src.meta)"""
    with rasterio.open(url) as src:
        return dict(src.meta)


def PLACEHOLDER_sample_column_values(url: str, col: str, limit: int) -> list:
    """Query SQL lazy: SELECT DISTINCT col FROM layer LIMIT N"""
    raise NotImplementedError

def check_bbox_overlap(meta_a: dict, meta_b: dict) -> bool:
    """Confronta bounding-box-wgs84 dei due layer — niente I/O."""
    bbox_a = meta_a['metadata']['bounding-box-wgs84']
    bbox_b = meta_b['metadata']['bounding-box-wgs84']
    return not (
        bbox_a['maxx'] < bbox_b['minx'] or
        bbox_a['minx'] > bbox_b['maxx'] or
        bbox_a['maxy'] < bbox_b['miny'] or
        bbox_a['miny'] > bbox_b['maxy']
    )

def save_temp_geodataframe(gdf: Any, name: str) -> str:
    """Salva GDF in file temporaneo, restituisce path."""
    temp_file = os.path.join(utils._temp_dir, f"{name}.gpkg")
    gdf.to_file(temp_file)
    return temp_file

def persist_derived_layer(name: str, temp_path: str, state: MABaseGraphState) -> dict:
    """Upload su storage, restituisce dict nel formato layer registry."""
    ext = os.path.splitext(temp_path)[1]
    uri = f"{s3_utils._STATE_BUCKET_(state)}/geo-ops-out/{name}{ext}"
    s3_utils.s3_upload(
        temp_path,
        uri = uri
    )
    if ext.endswith('tif'):
        return utils.raster_specs(src=uri)
    else:
        return utils.vector_specs(src=uri)