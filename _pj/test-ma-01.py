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
print(f"\n{SEP}")
print(f"  Session : {thread_id}")
print(f"  User    : tommaso  |  Project : mao")
print(f"  Prompt  : \"{prompt}\"")
print(SEP)


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
#  PIPELINE STEPS
# =========================================================================
print(f"\n{'PIPELINE STEPS':^80}")
print(SEP)

llm_metadata = state.get("llm_metadata", {})
prev_output = {"messages": "[HumanMessage] user prompt"}  # initial input = the user message

total_input_tokens = 0
total_output_tokens = 0

for step_num, (node_name, ev) in enumerate(_node_trace, 1):
    info = NODE_INFO.get(node_name, {})
    label    = info.get("label", node_name)
    ref      = info.get("ref", "?")
    is_async = info.get("async", False)
    has_llm  = info.get("llm", False)
    llm_key  = info.get("llm_key")
    writes   = info.get("writes", [])

    # --- Header ---
    print(f"\n{step_num}. {label}")
    print(f"   ref: {ref}")
    print(f"   {SEP2}")

    # --- INPUT ---
    if step_num == 1:
        print(f"   INPUT    : user prompt -> \"{prompt}\"")
    else:
        prev_node = _node_trace[step_num - 2][0]
        prev_info = NODE_INFO.get(prev_node, {})
        prev_writes = prev_info.get("writes", [])
        if prev_writes:
            print(f"   INPUT    : state from [{prev_node}] -> {', '.join(prev_writes)}")
        else:
            print(f"   INPUT    : state from [{prev_node}] (unchanged)")

    # --- PROCESS ---
    call_type = "sync" if not is_async else "async"
    print(f"   PROCESS  : {ref}  ({call_type})")

    # --- LLM CALL ---
    if has_llm and llm_key:
        meta = llm_metadata.get(llm_key, {})
        model  = meta.get("model", "gpt-4o-mini")
        inp_t  = meta.get("input_tokens", "?")
        out_t  = meta.get("output_tokens", "?")
        tot_t  = meta.get("total_tokens", "?")
        if isinstance(inp_t, int): total_input_tokens  += inp_t
        if isinstance(out_t, int): total_output_tokens += out_t
        print(f"   LLM      : ** {model} **")
        print(f"              input_tokens={inp_t}  output_tokens={out_t}  total={tot_t}")
    else:
        print(f"   LLM      : (none)")

    # --- STATE UPDATES ---
    if writes:
        print(f"   STATE    :")
        for key in writes:
            val = ev.get(key, state.get(key))
            val_str = str(val)
            if len(val_str) > 120:
                val_str = val_str[:120] + "..."
            print(f"              {key} = {val_str}")
    else:
        print(f"   STATE    : (no changes)")

    # --- OUTPUT ---
    if writes:
        print(f"   OUTPUT   : updated -> {', '.join(writes)}")
    else:
        print(f"   OUTPUT   : passthrough (state unchanged)")


# =========================================================================
#  FLOW SUMMARY
# =========================================================================
print(f"\n{SEP}")
flow = " -> ".join([t[0] for t in _node_trace])
print(f"  FLOW : START -> {flow} -> END")

# Token totals
if total_input_tokens or total_output_tokens:
    print(f"  TOKENS : input={total_input_tokens}  output={total_output_tokens}  total={total_input_tokens + total_output_tokens}")

# Conversation (should be clean now)
chat_json = GI.conversation_handler.chat2json(GI.conversation_events)
print(f"  CHAT MESSAGES : {len(chat_json)}")
for msg in chat_json:
    role = msg.get('role', '?').upper()
    content = str(msg.get('content', ''))[:120]
    print(f"    [{role}] {content}")

print(SEP)


