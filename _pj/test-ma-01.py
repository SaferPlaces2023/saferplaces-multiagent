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
print()
print("  Node trace:")
for i, (node_name, keys, _) in enumerate(_node_trace, 1):
    print(f"    {i}. {node_name}  (state keys: {', '.join(keys) if keys else 'none'})")


# --- STEP 3: Node History (pipeline trace) ---
print(f"\n{SEP}")
print("STEP 3: NODE HISTORY (Pipeline Trace)")
print(SEP)
state = GI.graph_state
node_history = state.get('node_history', [])
if node_history:
    for i, node_name in enumerate(node_history, 1):
        print(f"  {i}. {node_name}")
else:
    print("No node history recorded.")


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


# --- STEP 5: Parsed Request (NLU) ---
print(f"\n{SEP}")
print("STEP 5: PARSED REQUEST (NLU)")
print(SEP)
parsed = state.get('parsed_request')
if parsed:
    print(f"  Intent   : {parsed.get('intent', 'N/A')}")
    print(f"  Entities : {parsed.get('entities', [])}")
    print(f"  Raw text : {parsed.get('raw_text', 'N/A')}")
else:
    print("No parsed request found (active graph does not use MA pipeline).")


# --- STEP 6: Execution Plan ---
print(f"\n{SEP}")
print("STEP 6: EXECUTION PLAN")
print(SEP)
plan = state.get('plan')
current_step = state.get('current_step')
if plan:
    for i, step in enumerate(plan, 1):
        marker = " <-- current" if current_step is not None and i - 1 == current_step else ""
        print(f"  Step {i}: [{step.get('agent', '?')}] -> {step.get('goal', '?')}{marker}")
else:
    print("No execution plan found.")


# --- STEP 7: Stream Events (node-by-node, raw) ---
print(f"\n{SEP}")
print("STEP 7: STREAM EVENTS (node-by-node, raw)")
print(SEP)
for i, (node_name, keys, event_value) in enumerate(_node_trace, 1):
    print(f"  Batch {i}: Node = '{node_name}'")
    if event_value is None:
        print(f"           (no data)")
    elif isinstance(event_value, dict):
        for key, val in event_value.items():
            val_str = str(val)
            preview = (val_str[:200] + '...') if len(val_str) > 200 else val_str
            print(f"           {key}: {preview}")
    else:
        print(f"           {str(event_value)[:200]}")
    print()


# --- STEP 8: Conversation Events (detailed) ---
print(f"\n{SEP}")
print("STEP 8: CONVERSATION EVENTS (detailed)")
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


# --- STEP 9: Serialized Chat (JSON) ---
print(f"\n{SEP}")
print("STEP 9: SERIALIZED CHAT (JSON)")
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


# --- STEP 10: Interrupt Status ---
print(f"\n{SEP}")
print("STEP 10: INTERRUPT STATUS")
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


# --- STEP 11: Node Params ---
print(f"\n{SEP}")
print("STEP 11: NODE PARAMS")
print(SEP)
node_params = state.get('node_params', {})
if node_params:
    pprint(node_params, width=120)
else:
    print("No node params recorded.")


# --- STEP 12: Layer Registry ---
print(f"\n{SEP}")
print("STEP 12: LAYER REGISTRY")
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


# --- STEP 13: User Drawn Shapes ---
print(f"\n{SEP}")
print("STEP 13: USER DRAWN SHAPES")
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


# --- STEP 14: Full Graph State (raw) ---
print(f"\n{SEP}")
print("STEP 14: FULL GRAPH STATE (raw)")
print(SEP)
pprint(GI.graph_state, width=120)


