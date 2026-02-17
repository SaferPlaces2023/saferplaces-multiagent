import json
import uuid
from pprint import pprint

import saferplaces_multiagent.ma
import importlib
importlib.reload(saferplaces_multiagent.ma)

from saferplaces_multiagent import __GRAPH_REGISTRY__, GraphInterface


SEP = "=" * 80


# --- STEP 1: Create session ---
print(f"\n{SEP}")
print("STEP 1: CREATE SESSION")
print(SEP)
thread_id = str(uuid.uuid4())
print(f"Thread ID : {thread_id}")
print(f"User      : tommaso")
print(f"Project   : mao")

GI = __GRAPH_REGISTRY__.register(thread_id=thread_id, user_id='tommaso', project_id='mao')
print("Session created.")


# --- STEP 2: Send prompt & capture node-level stream ---
print(f"\n{SEP}")
print("STEP 2: SEND PROMPT")
print(SEP)
prompt = 'Weather in milano?'
print(f'Prompt: "{prompt}"')

# Monkey-patch G.stream to intercept node names from the raw LangGraph stream
_orig_stream = GI.G.stream
_node_trace = []  # list of (node_name, state_keys, event_value)

def _patched_stream(*args, **kwargs):
    for event in _orig_stream(*args, **kwargs):
        for node_name, event_value in event.items():
            keys = list(event_value.keys()) if isinstance(event_value, dict) else []
            _node_trace.append((node_name, keys, event_value))
        yield event

GI.G.stream = _patched_stream

out = list(GI.user_prompt(prompt))
print(f"Pipeline returned {len(out)} event batches (= {len(_node_trace)} graph nodes fired).")


# --- STEP 3: Pipeline Flow (INPUT -> PROCESS -> OUTPUT per node) ---
print(f"\n{SEP}")
print("STEP 3: PIPELINE FLOW")
print(SEP)
state = GI.graph_state

prev_state = {}
for i, (node_name, keys, event_value) in enumerate(_node_trace, 1):
    ev = event_value if isinstance(event_value, dict) else {}

    # Determine what's NEW in this node's output vs. previous accumulated state
    new_keys = []
    changed_keys = []
    for k, v in ev.items():
        if k == 'message':
            continue  # skip internal serialized message
        if k not in prev_state:
            new_keys.append(k)
        elif str(prev_state.get(k)) != str(v):
            changed_keys.append(k)

    # Build input summary (what it received)
    if i == 1:
        input_summary = "user prompt (HumanMessage)"
    else:
        prev_node = _node_trace[i-2][0]
        prev_out_keys = [k for k in _node_trace[i-2][1] if k != 'message']
        input_summary = f"state from '{prev_node}' ({', '.join(prev_out_keys)})" if prev_out_keys else f"state from '{prev_node}'"

    # Build output summary (what it produced/changed)
    output_items = {}
    for k in new_keys + changed_keys:
        val = ev.get(k)
        val_str = str(val)
        if len(val_str) > 100:
            val_str = val_str[:100] + '...'
        output_items[k] = val_str

    # Print the flow
    node_sep = "-" * 60
    print(f"\n  {node_sep}")
    print(f"  [{i}] {node_name.upper()}")
    print(f"  {node_sep}")
    print(f"  INPUT   : {input_summary}")

    # Process description based on node name
    if 'chat_agent' in node_name:
        print(f"  PROCESS : ** LLM CALL (gpt-4o-mini) ** -> structured output -> ParsedRequest (intent, entities, raw_text)")
    elif 'supervisor' in node_name:
        print(f"  PROCESS : ** LLM CALL (gpt-4o-mini) ** -> structured output -> ExecutionPlan (plan steps with agent + goal)")
    elif 'retrieval_agent' in node_name:
        print(f"  PROCESS : passthrough (no LLM call)")
    else:
        print(f"  PROCESS : {node_name}")

    if output_items:
        print(f"  OUTPUT  :")
        for k, v in output_items.items():
            tag = "(NEW)" if k in new_keys else "(CHANGED)"
            print(f"            {tag} {k}: {v}")
    else:
        print(f"  OUTPUT  : (no state changes)")

    # Update accumulated state
    prev_state.update(ev)

# Print the final flow summary
print(f"\n  {'-' * 60}")
print(f"  FLOW SUMMARY:")
print(f"  {'-' * 60}")
flow_nodes = [t[0] for t in _node_trace]
print(f"  START -> {' -> '.join(flow_nodes)} -> END")


# --- STEP 4: Available Tools ---
print(f"\n{SEP}")
print("STEP 4: AVAILABLE TOOLS")
print(SEP)
avail_tools = state.get('avaliable_tools')
if avail_tools:
    for t in avail_tools:
        print(f"  - {t}")
else:
    print("All tools available (no whitelist set).")


# --- STEP 5: Conversation Events (detailed) ---
print(f"\n{SEP}")
print("STEP 5: CONVERSATION EVENTS (detailed)")
print(SEP)
for i, evt_batch in enumerate(out, 1):
    # Match to the node name from our intercepted trace
    node_label = _node_trace[i-1][0] if i-1 < len(_node_trace) else '?'
    print(f"  --- Batch {i} (node: {node_label}) ---")
    if evt_batch:
        for evt in evt_batch:
            evt_type = type(evt).__name__
            content = getattr(evt, 'content', str(evt))

            # Show tool calls if present (AIMessage with tool_calls)
            tool_calls = getattr(evt, 'tool_calls', None)
            tool_name = getattr(evt, 'name', None)
            tool_call_id = getattr(evt, 'tool_call_id', None)

            preview = (str(content)[:200] + '...') if len(str(content)) > 200 else str(content)
            print(f"  [{i}] {evt_type}:")
            if tool_name:
                print(f"       Tool name    : {tool_name}")
            if tool_call_id:
                print(f"       Tool call ID : {tool_call_id}")
            if tool_calls:
                for tc in tool_calls:
                    tc_name = tc.get('name', tc.get('function', {}).get('name', '?')) if isinstance(tc, dict) else getattr(tc, 'name', '?')
                    tc_args = tc.get('args', tc.get('function', {}).get('arguments', '')) if isinstance(tc, dict) else getattr(tc, 'args', '')
                    print(f"       Tool call    : {tc_name}({json.dumps(tc_args) if isinstance(tc_args, dict) else tc_args})")
            print(f"       Content      : {preview}")
            print()
    else:
        print(f"  (empty batch)\n")


# --- STEP 6: Serialized Chat (JSON) ---
print(f"\n{SEP}")
print("STEP 6: SERIALIZED CHAT (JSON)")
print(SEP)
try:
    chat_json = GI.conversation_handler.chat2json(GI.conversation_events)
    for msg in chat_json:
        role = msg.get('role', '?')
        content = str(msg.get('content', ''))
        preview = (content[:150] + '...') if len(content) > 150 else content
        extras = []
        if msg.get('tool_calls'):
            extras.append(f"tool_calls={len(msg['tool_calls'])}")
        if msg.get('name'):
            extras.append(f"tool={msg['name']}")
        if msg.get('interrupt_type'):
            extras.append(f"interrupt_type={msg['interrupt_type']}")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        print(f"  [{role.upper()}]{extra_str}: {preview}")
except Exception as e:
    print(f"Could not serialize chat: {e}")


# --- STEP 7: Interrupt Status ---
print(f"\n{SEP}")
print("STEP 7: INTERRUPT STATUS")
print(SEP)
interrupt = GI.interrupt
if interrupt:
    print(f"  Interrupt active!")
    if hasattr(interrupt, 'value'):
        val = interrupt.value
        if hasattr(val, 'as_dict'):
            pprint(val.as_dict, width=120)
        else:
            pprint(val, width=120)
else:
    print("No active interrupt.")

confirm_tool = state.get('confirm_tool_execution', False)
print(f"  Confirm before tool execution: {confirm_tool}")


# --- STEP 8: Node Params ---
print(f"\n{SEP}")
print("STEP 8: NODE PARAMS")
print(SEP)
node_params = state.get('node_params', {})
if node_params:
    pprint(node_params, width=120)
else:
    print("No node params recorded.")


# --- STEP 9: Layer Registry ---
print(f"\n{SEP}")
print("STEP 9: LAYER REGISTRY")
print(SEP)
layers = state.get('layer_registry', [])
if layers:
    for layer in layers:
        print(f"  - {layer.get('title', '?')} ({layer.get('type', '?')})")
        print(f"    Source      : {layer.get('src', '?')}")
        print(f"    Description : {layer.get('description', 'N/A')}")
        if layer.get('metadata'):
            print(f"    Metadata    : {json.dumps(layer['metadata'], indent=2)}")
else:
    print("No layers registered.")


# --- STEP 10: User Drawn Shapes ---
print(f"\n{SEP}")
print("STEP 10: USER DRAWN SHAPES")
print(SEP)
shapes = state.get('user_drawn_shapes', [])
if shapes:
    for shape in shapes:
        meta = shape.get('metadata', {})
        print(f"  - Collection: {shape.get('collection_id', '?')}")
        print(f"    Type       : {meta.get('feature_type', '?')}")
        print(f"    Name       : {meta.get('name', 'N/A')}")
        print(f"    Bounds     : {meta.get('bounds', 'N/A')}")
        print(f"    Features   : {len(shape.get('features', []))} feature(s)")
else:
    print("No user drawn shapes.")


# --- STEP 11: Full Graph State (raw) ---
print(f"\n{SEP}")
print("STEP 11: FULL GRAPH STATE (raw)")
print(SEP)
pprint(GI.graph_state, width=120)


