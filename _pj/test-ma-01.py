import json
import uuid
from pprint import pprint
import saferplaces_multiagent.ma

import importlib
importlib.reload(saferplaces_multiagent.ma)

from saferplaces_multiagent import __GRAPH_REGISTRY__, GraphInterface


# --- STEP 1: Create session ---
thread_id = str(uuid.uuid4())
print(f"Thread ID : {thread_id}")
print(f"User      : tommaso")
print(f"Project   : mao")

GI = __GRAPH_REGISTRY__.register(thread_id=thread_id, user_id='tommaso', project_id='mao')
print("Session created.")


# --- STEP 2: Send prompt ---
prompt = 'Weather in milano?'
print(f'Prompt: "{prompt}"')
out = list(GI.user_prompt(prompt))
print(f"Pipeline returned {len(out)} event batches.")


# --- STEP 3: Full graph state ---
pprint(GI.graph_state, width=120)


# --- STEP 4: Parsed Request ---
state = GI.graph_state
parsed = state.get('parsed_request')
if parsed:
    print(f"Intent   : {parsed.get('intent', 'N/A')}")
    print(f"Entities : {parsed.get('entities', [])}")
    print(f"Raw text : {parsed.get('raw_text', 'N/A')}")
else:
    print("No parsed request found.")


# --- STEP 5: Execution Plan ---
plan = state.get('plan')
if plan:
    for i, step in enumerate(plan, 1):
        print(f"Step {i}: [{step.get('agent', '?')}] -> {step.get('goal', '?')}")
else:
    print("No execution plan found.")


# --- STEP 6: Conversation Events ---
for i, evt_batch in enumerate(out, 1):
    if evt_batch:
        for evt in evt_batch:
            evt_type = type(evt).__name__
            content = getattr(evt, 'content', str(evt))
            preview = (content[:120] + '...') if len(str(content)) > 120 else content
            print(f"[{i}] {evt_type}: {preview}")


# --- STEP 7: Layer Registry ---
layers = state.get('layer_registry', [])
if layers:
    for layer in layers:
        print(f"- {layer.get('title', '?')} ({layer.get('type', '?')}) -> {layer.get('src', '?')}")
else:
    print("No layers registered.")


