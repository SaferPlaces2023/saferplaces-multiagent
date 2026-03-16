"""
tests/_utils.py
---------------
Shared helpers for all T### test scripts.
"""

import sys
import uuid
import contextlib
import io
import re
import json

from pathlib import Path

# ---------------------------------------------------------------------------
# JSON config loader
# ---------------------------------------------------------------------------
_TESTS_JSON = Path(__file__).parent / "tests.json"

def load_test_config(test_id: str) -> dict:
    """Return the config block for *test_id* (e.g. 'T001') from tests.json."""
    with _TESTS_JSON.open(encoding="utf-8") as f:
        all_tests = json.load(f)
    if test_id not in all_tests:
        raise KeyError(f"Test '{test_id}' not found in {_TESTS_JSON}")
    return all_tests[test_id]

# ---------------------------------------------------------------------------
# Bootstrap – add src/ to sys.path and load .env
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
SRC  = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Silence helper
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def silence():
    """Suppress all stdout/stderr inside the block."""
    devnull = io.StringIO()
    with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        yield


# ---------------------------------------------------------------------------
# Lazy project imports (silenced)
# ---------------------------------------------------------------------------
with silence():
    from saferplaces_multiagent.agent_interface import GraphInterface, __GRAPH_REGISTRY__
    from saferplaces_multiagent.ma.chat.request_parser import Prompts as _RPPrompts
    from saferplaces_multiagent.ma.prompts.supervisor_agent_prompts import OrchestratorPrompts as _SPPrompts
    from saferplaces_multiagent.ma.chat.final_responder import Prompts as _FRPrompts
    from saferplaces_multiagent.ma.specialized.safercast_agent import RetrieverPrompts as _RetrieverPrompts
    from saferplaces_multiagent.ma.specialized.models_agent import ModelsPrompts as _ModelsPrompts


# ---------------------------------------------------------------------------
# Agent → system prompt registry
# Keyed by the prefix that appears in the agent's print() output, e.g. [request_parser]
# ---------------------------------------------------------------------------
AGENT_REGISTRY = {
    # ── Chat ────────────────────────────────────────────────────────────────
    "request_parser": (
        "RequestParser",
        _RPPrompts.SYSTEM_REQUEST_PROMPT,
    ),
    "final_responder": (
        "FinalResponder",
        _FRPrompts.FINAL_RESPONSE_PROMPT,
    ),
    # ── Orchestrator (supervisor subgraph) ──────────────────────────────────
    "supervisor_agent": (
        "SupervisorAgent",
        _SPPrompts.MainContext.stable().message,
    ),
    "supervisor_planner_confirm": (
        "SupervisorPlannerConfirm",
        "(no LLM — auto-confirms when plan is empty; interrupts user when plan has steps)",
    ),
    "supervisor_router": (
        "SupervisorRouter",
        "(no LLM — pure routing logic based on plan state and supervisor_next_node)",
    ),
    # ── Retriever subgraph ──────────────────────────────────────────────────
    "retriever_agent": (
        "DataRetrieverAgent",
        _RetrieverPrompts.TOOL_SELECTION_SYSTEM,
    ),
    "retriever_invocation_confirm": (
        "DataRetrieverInvocationConfirm",
        "(no LLM — validates args via inference rules; interrupts user on validation error or when confirmation is enabled)",
    ),
    "retriever_executor": (
        "DataRetrieverExecutor",
        "(no LLM — executes validated tool calls, adds result layer to registry)",
    ),
    # ── Models subgraph ─────────────────────────────────────────────────────
    "models_agent": (
        "ModelsAgent",
        _ModelsPrompts.TOOL_SELECTION_SYSTEM,
    ),
    "models_invocation_confirm": (
        "ModelsInvocationConfirm",
        "(no LLM — validates args via inference rules; interrupts user on validation error or when confirmation is enabled)",
    ),
    "models_executor": (
        "ModelsExecutor",
        "(no LLM — executes validated model tool calls, adds result layer to registry)",
    ),
    # ── Layers agent (called inline by executors) ────────────────────────────
    "layers_agent": (
        "LayersAgent",
        "(no LLM for routing — uses LLM only inside choose_layer / build_layer_from_prompt tools; manages geospatial layer registry)",
    ),
}

# Regex to match lines like: [supervisor_agent] → Planning...
_AGENT_LINE_RE = re.compile(r"^\[(\w+)\]")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
ROLE_PREFIX = {
    "user":      "👤 USER     ",
    "ai":        "🤖 AI       ",
    "tool":      "🔧 TOOL     ",
    "interrupt": "⏸️  INTERRUPT",
}

def print_msg(msg: dict):
    role    = msg.get("role", "?")
    prefix  = ROLE_PREFIX.get(role, f"[{role}]")
    content = msg.get("content", "")
    print(f"\n{prefix}: {content}")
    if role == "tool":
        print(f"           name={msg.get('name')}  id={msg.get('id')}")
    if role == "interrupt":
        print(f"           type={msg.get('interrupt_type')}")
    if role == "ai" and msg.get("tool_calls"):
        for tc in msg["tool_calls"]:
            print(f"           ↳ tool_call: {tc.get('name')}  args={tc.get('args')}")


def print_agent_chain(invoked: list[str]):
    """Print each agent in the chain with its system prompt."""
    print()
    for key in invoked:
        name, prompt = AGENT_REGISTRY[key]
        print(f"  ┌─ [{name}]")
        for line in prompt.splitlines():
            print(f"  │  {line}")
        print(f"  └─")


# ---------------------------------------------------------------------------
# Core send / run helpers
# ---------------------------------------------------------------------------
def send(gi: GraphInterface, user_message: str) -> list[dict]:
    """Send *user_message* through the graph, trace agents, and print events."""

    # Run the graph, capturing internal print() output to detect agents
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(io.StringIO()):
        batches = list(gi.user_prompt(prompt=user_message))

    # Parse which agents were invoked (in order, deduplicated)
    invoked_agents: list[str] = []
    seen_agents: set[str] = set()
    for line in captured.getvalue().splitlines():
        m = _AGENT_LINE_RE.match(line.strip())
        if m:
            key = m.group(1)
            if key in AGENT_REGISTRY and key not in seen_agents:
                seen_agents.add(key)
                invoked_agents.append(key)

    # Collect all messages across batches, deduplicated by (role, content)
    all_events: list[dict] = []
    seen_msgs: set[tuple] = set()
    for batch in batches:
        for msg in gi.conversation_handler.chat2json(chat=batch):
            dedup_key = (msg.get("role"), str(msg.get("content", ""))[:200])
            if dedup_key not in seen_msgs:
                seen_msgs.add(dedup_key)
                all_events.append(msg)

    # Print: USER → agent chain → AI / INTERRUPT
    user_printed = False
    for msg in all_events:
        print_msg(msg)
        if msg.get("role") == "user" and not user_printed:
            user_printed = True
            print_agent_chain(invoked_agents)

    return all_events


def run_tests(
    messages: list,
    user_id: str = "test_user",
    project_id: str = "test_project",
    result_file: str | Path | None = None,
):
    """Create a fresh GraphInterface session and send each message.

    Each item in *messages* can be:
      - a ``str``        → single-message conversation in its own thread
      - a ``list[str]``  → multi-turn conversation in its own thread

    If *result_file* is given the output is written there (overwrite) instead
    of the terminal.  The parent directory is created automatically.
    """
    # Normalise: every item becomes a conversation group (list of turns)
    groups: list[list[str]] = [
        m if isinstance(m, list) else [m] for m in messages
    ]

    def _run(out):
        for group in groups:
            thread_id = str(uuid.uuid4())
            out.write(f"\n{'='*60}\n")
            out.write(f"  THREAD  : {thread_id}\n")
            out.write(f"  USER    : {user_id}\n")
            out.write(f"  PROJECT : {project_id}\n")
            out.write(f"{'='*60}\n")

            with silence():
                gi: GraphInterface = __GRAPH_REGISTRY__.register(
                    thread_id  = thread_id,
                    user_id    = user_id,
                    project_id = project_id,
                    map_handler= None,
                )

            with contextlib.redirect_stdout(out):
                for i, msg in enumerate(group, 1):
                    print(f"\nmessage {i} {'─' * 40}")
                    send(gi, msg)

    if result_file is None:
        # Write directly to real stdout
        class _PassThrough:
            def write(self, s): sys.stdout.write(s)
            def flush(self): sys.stdout.flush()
        _run(_PassThrough())
    else:
        result_path = Path(result_file)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        with result_path.open("w", encoding="utf-8") as f:
            _run(f)
        # Also tell the user where the file landed
        sys.stdout.write(f"Results written to {result_path.resolve()}\n")
