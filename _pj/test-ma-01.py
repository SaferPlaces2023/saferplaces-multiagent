import argparse
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
#  PROMPT CATALOG — add new test phrases here
# =========================================================================
PROMPTS = {
    "weather":      "Weather in milano?",
    "elevation":    "What is the elevation of Rome?",
    "twin":         "Build a digital twin for Paris",
    "rain":         "Simulate 100mm of rain in Berlin",
    "buildings":    "Count how many buildings are affected by flood in London",
    "temperature":  "What is the current temperature in New York?",
    "forecast":     "Get rainfall forecast for the next 3 hours in Tokyo",
}

DEFAULT_PROMPT_KEY = "weather"


# =========================================================================
#  CLI ARGUMENT PARSING
# =========================================================================
parser = argparse.ArgumentParser(
    description="Run a single multiagent test prompt.",
    formatter_class=argparse.RawTextHelpFormatter,
)
parser.add_argument(
    "prompt_key",
    nargs="?",
    default=DEFAULT_PROMPT_KEY,
    choices=list(PROMPTS.keys()),
    help="Key of the prompt to test (default: %(default)s).\n"
         "Available:\n" + "\n".join(f"  {k:14s} -> {v}" for k, v in PROMPTS.items()),
)
parser.add_argument(
    "--custom",
    type=str,
    default=None,
    help="Use a custom prompt string instead of one from the catalog.",
)
parser.add_argument(
    "--list", "-l",
    action="store_true",
    dest="list_prompts",
    help="List all available prompt keys and exit.",
)
args = parser.parse_args()

if args.list_prompts:
    print("\nAvailable prompt keys:")
    for k, v in PROMPTS.items():
        marker = " (default)" if k == DEFAULT_PROMPT_KEY else ""
        print(f"  {k:14s} -> {v}{marker}")
    raise SystemExit(0)


# =========================================================================
#  SESSION SETUP
# =========================================================================
thread_id = str(uuid.uuid4())
GI = __GRAPH_REGISTRY__.register(thread_id=thread_id, user_id='tommaso', project_id='mao')

prompt = args.custom if args.custom else PROMPTS[args.prompt_key]
print(f"[INFO] Prompt ({args.prompt_key if not args.custom else 'custom'}): {prompt}")


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
#  EXPORT JSON to _pj/result/ — flat pipeline array
# =========================================================================
import os
from datetime import datetime, timezone

result_dir = os.path.join(os.path.dirname(__file__), "result")
os.makedirs(result_dir, exist_ok=True)

pipeline = []

# Step 0: session context
pipeline.append({
    "step": 0,
    "type": "session",
    "data": {
        "thread_id": thread_id,
        "user_id": "tommaso",
        "project_id": "mao",
        "prompt": prompt,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    },
})

# Steps 1..N: node executions
for step_num, (node_name, ev) in enumerate(_node_trace, 1):
    info = NODE_INFO.get(node_name, {})
    has_llm = info.get("llm", False)
    llm_key = info.get("llm_key")
    writes  = info.get("writes", [])

    # Input
    if step_num == 1:
        step_input = {"type": "user_prompt", "value": prompt}
    else:
        prev_node = _node_trace[step_num - 2][0]
        prev_writes = NODE_INFO.get(prev_node, {}).get("writes", [])
        step_input = {"type": "state", "from": prev_node, "keys": prev_writes or None}

    # LLM
    llm_data = None
    if has_llm and llm_key:
        meta = llm_metadata.get(llm_key, {})
        llm_input = meta.get("input", meta.get("messages", {}))
        llm_output = meta.get("output", {})
        llm_data = {
            "model": meta.get("model", "gpt-4o-mini"),
            "token_usage": {
                "input": meta.get("input_tokens", 0),
                "output": meta.get("output_tokens", 0),
                "total": meta.get("total_tokens", 0),
            },
            "input": {
                "system": llm_input.get("system"),
                "user": llm_input.get("user"),
            },
            "output": {
                "raw": llm_output.get("raw", llm_input.get("assistant")),
                "parsed": llm_output.get("parsed", meta.get("parsed_output")),
            },
        }

    # State updates
    updates = None
    if writes:
        updates = {}
        for key in writes:
            updates[key] = ev.get(key, state.get(key))

    node_data = {
        "node": node_name,
        "callable": info.get("ref", "?"),
        "node_input": step_input,
        "llm_call": llm_data,
        "state_updates": updates,
        "output_keys": writes if writes else None,
    }
    pipeline.append({
        "step": step_num,
        "type": "node",
        "data": node_data,
    })

# Write JSON file (overwrite, named after test script)
json_path = os.path.join(result_dir, "test-ma-01.json")
with open(json_path, "w", encoding="utf-8") as f:
    json.dump(pipeline, f, indent=2, ensure_ascii=False, default=str)

print(f"[OK] JSON exported -> {os.path.relpath(json_path)}")