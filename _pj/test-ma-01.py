import json
import uuid
from pprint import pprint

import saferplaces_multiagent.ma
import importlib
importlib.reload(saferplaces_multiagent.ma)

from saferplaces_multiagent import __GRAPH_REGISTRY__, GraphInterface


SEP  = "=" * 80
SEP2 = "-" * 60


# =========================================================================
#  SESSION SETUP
# =========================================================================
thread_id = str(uuid.uuid4())
GI = __GRAPH_REGISTRY__.register(thread_id=thread_id, user_id='tommaso', project_id='mao')

prompt = 'Weather in milano?'


# =========================================================================
#  Monkey-patch G.stream to capture node-level trace
# =========================================================================
_orig_stream = GI.G.stream
_node_trace = []  # list of (node_name, event_value_dict)

def _patched_stream(*args, **kwargs):
    for event in _orig_stream(*args, **kwargs):
        for node_name, event_value in event.items():
            _node_trace.append((node_name, event_value if isinstance(event_value, dict) else {}))
        yield event

GI.G.stream = _patched_stream
out = list(GI.user_prompt(prompt))
state = GI.graph_state


# =========================================================================
#  NODE REGISTRY: map node_name -> metadata
# =========================================================================
NODE_INFO = {
    "chat_agent": {
        "label":    "Chat Agent (NLU Parser)",
        "ref":      "ma.chat_agent.ChatAgent.run()",
        "async":    False,
        "llm":      True,
        "llm_key":  "chat_agent",
        "writes":   ["parsed_request"],
    },
    "supervisor_subgraph": {
        "label":    "Supervisor (Planner)",
        "ref":      "ma.supervisor_agent.SupervisorAgent.run()  +  SupervisorRouterNode.route()",
        "async":    False,
        "llm":      True,
        "llm_key":  "supervisor_agent",
        "writes":   ["plan", "current_step", "awaiting_user"],
    },
    "retrieval_agent": {
        "label":    "Retrieval Agent  [TODO: placeholder]",
        "ref":      "ma_graph.retrieval_agent()  # passthrough",
        "async":    False,
        "llm":      False,
        "llm_key":  None,
        "writes":   [],
    },
}


# =========================================================================
#  PIPELINE EXECUTION — collect data silently, print only status
# =========================================================================
llm_metadata = state.get("llm_metadata", {})
chat_json = GI.conversation_handler.chat2json(GI.conversation_events)

total_input_tokens = 0
total_output_tokens = 0
warnings = []

for step_num, (node_name, ev) in enumerate(_node_trace, 1):
    info = NODE_INFO.get(node_name, {})
    has_llm  = info.get("llm", False)
    llm_key  = info.get("llm_key")
    writes   = info.get("writes", [])

    if has_llm and llm_key:
        meta = llm_metadata.get(llm_key, {})
        inp_t = meta.get("input_tokens", 0)
        out_t = meta.get("output_tokens", 0)
        if isinstance(inp_t, int): total_input_tokens  += inp_t
        if isinstance(out_t, int): total_output_tokens += out_t
        if meta.get("error"):
            warnings.append(f"[WARN] {node_name}: LLM error -> {meta['error']}")

    if not writes and node_name != _node_trace[0][0]:
        warnings.append(f"[WARN] {node_name}: passthrough (no state changes)")

flow = " -> ".join(["START"] + [t[0] for t in _node_trace] + ["END"])

# Print compact status
print(f"\n[OK] Pipeline completed: {len(_node_trace)} steps | {total_input_tokens + total_output_tokens} tokens")
print(f"     {flow}")
for w in warnings:
    print(f"     {w}")


# =========================================================================
#  EXPORT JSON to _pj/result/
# =========================================================================
import os
from datetime import datetime

result_dir = os.path.join(os.path.dirname(__file__), "result")
os.makedirs(result_dir, exist_ok=True)

# Build the JSON structure mirroring the terminal output
pipeline_steps = []
for step_num, (node_name, ev) in enumerate(_node_trace, 1):
    info = NODE_INFO.get(node_name, {})
    label    = info.get("label", node_name)
    ref      = info.get("ref", "?")
    is_async = info.get("async", False)
    has_llm  = info.get("llm", False)
    llm_key  = info.get("llm_key")
    writes   = info.get("writes", [])

    # Input
    if step_num == 1:
        step_input = {"type": "user_prompt", "value": prompt}
    else:
        prev_node = _node_trace[step_num - 2][0]
        prev_info = NODE_INFO.get(prev_node, {})
        prev_writes = prev_info.get("writes", [])
        step_input = {"type": "state", "from_node": prev_node, "keys": prev_writes if prev_writes else None}

    # LLM info
    llm_info = None
    if has_llm and llm_key:
        meta = llm_metadata.get(llm_key, {})
        llm_info = {
            "model": meta.get("model", "gpt-4o-mini"),
            "input_tokens": meta.get("input_tokens", 0),
            "output_tokens": meta.get("output_tokens", 0),
            "total_tokens": meta.get("total_tokens", 0),
            "messages": meta.get("messages", {}),
            "parsed_output": meta.get("parsed_output"),
        }

    # State updates
    state_updates = {}
    if writes:
        for key in writes:
            val = ev.get(key, state.get(key))
            state_updates[key] = val

    pipeline_steps.append({
        "step": step_num,
        "node": node_name,
        "label": label,
        "ref": ref,
        "async": is_async,
        "input": step_input,
        "process": {"ref": ref, "type": "sync" if not is_async else "async"},
        "llm": llm_info,
        "state_updates": state_updates if state_updates else None,
        "output": {"updated_keys": writes} if writes else {"passthrough": True},
    })

result_json = {
    "session": {
        "thread_id": thread_id,
        "user_id": "tommaso",
        "project_id": "mao",
        "prompt": prompt,
        "timestamp": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
    },
    "pipeline_steps": pipeline_steps,
    "flow_summary": {
        "flow": ["START"] + [t[0] for t in _node_trace] + ["END"],
        "tokens": {
            "input": total_input_tokens,
            "output": total_output_tokens,
            "total": total_input_tokens + total_output_tokens,
        },
    },
    "chat_messages": chat_json,
    "final_response": {
        "final_message": state.get("final_message"),
        "retrieval_result": state.get("retrieval_result"),
    },
    "llm_exchanges": {},
}

# LLM exchanges per node
for node_name, info in NODE_INFO.items():
    if info.get('llm') and info.get('llm_key'):
        meta = llm_metadata.get(info['llm_key'], {})
        result_json["llm_exchanges"][node_name] = {
            "messages": meta.get("messages", {}),
            "parsed_output": meta.get("parsed_output"),
            "tokens": {
                "input": meta.get("input_tokens", 0),
                "output": meta.get("output_tokens", 0),
                "total": meta.get("total_tokens", 0),
            },
        }

# Write JSON file (overwrite, named after test script)
json_path = os.path.join(result_dir, "test-ma-01.json")
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(result_json, f, indent=2, ensure_ascii=False, default=str)

print(f"[OK] JSON exported -> {os.path.relpath(json_path)}")