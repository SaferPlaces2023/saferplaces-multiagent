from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import HumanMessage, AIMessage

from ..common.names import NN
from ..common.states import BaseGraphState, MABaseGraphState
from ..common.utils import _base_llm



class Prompts:

    specialized_tool_selection = '\n'.join((
        "You are a specialized agent for data model retrieval.",
        "Choose the best tool to accomplish the goal.",
        "Only call tools that are provided.",
        "If needed info is missing, still propose the most likely tool call with best-effort args.",
    ))
    specialized_request = lambda goal, parsed_request: '\n'.join((
        f"Goal: {goal}",
        f"Parsed: {parsed_request}"
    ))


class DataRetrieverAgent():
    
    def __init__(self):
        self.name = 'DataRetrieverAgent'
        self.llm = _base_llm.bind_tools([
            # if you're using LangChain tools objects, pass those instead
            # Here we assume tool calling schema is handled by bind_tools in your stack
        ])

    
    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        step = state["plan"][state["current_step"]]
        goal = step.get("goal") or ""
        parsed_request = state.get("parsed_request") or ""

        messages = [
            {"role": "system", "content": Prompts.specialized_tool_selection},
            {"role": "user", "content": Prompts.specialized_request(goal, parsed_request)}
        ]

        resp = self.llm.invoke(messages)

        # If model didn't propose tool calls, just log and return (optional)
        if not getattr(resp, "tool_calls", None):
            state["tool_results"][f"step_{state['current_step']}"] = {"status": "no_tool_call", "text": getattr(resp, "content", "")}
            return state

        tool_call = resp.tool_calls[0]          # take first for minimal skeleton
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args", {}) or {}

        # --- Validate tool name ---
        if tool_name not in TOOLS:
            state["tool_results"][f"step_{state['current_step']}"] = {"status": "unknown_tool", "tool": tool_name, "args": tool_args}
            # ask user (or fallback)
            state["awaiting_user"] = True
            state["messages"] = state["messages"] + [
                AIMessage(content=f"Non riconosco il tool richiesto ({tool_name}). Puoi riformulare cosa vuoi ottenere?")
            ]
            return state
        
        # --- Validate args BEFORE writing AI tool-call message into state ---
        err = validate_tool_args(tool_name, tool_args)
        if err:
            state["awaiting_user"] = True
            # domanda mirata: qui puoi estrarre i missing field dall'errore pydantic se vuoi
            state["messages"] = state["messages"] + [
                AIMessage(content=f"Mi manca/incoerente qualche parametro per procedere ({tool_name}). Dettaglio: {err}\n"
                                  f"Puoi fornirmi i valori mancanti?")
            ]
            # NON appendere resp (che contiene tool_call) -> eviti contratto tool-response
            return state


        # --- Execute tool ---
        result = TOOLS[tool_name]["fn"](**tool_args)

        # Persist results
        state["tool_results"][f"step_{state['current_step']}"] = {
            "tool": tool_name,
            "args": tool_args,
            "result": result
        }

        # Now it's safe to append the AIMessage with tool_call AND the ToolMessage
        state["messages"] = state["messages"] + [
            resp,
            ToolMessage(content=json.dumps(result), tool_call_id=tool_call["id"])
        ]

        return state


class GraphNodes:
    pass

    
GN = GraphNodes()


graph_builder = StateGraph(MABaseGraphState)


graph_builder.add_node(GN.initial_chat_agent.name, GN.initial_chat_agent)

graph_builder.add_edge(START, GN.initial_chat_agent.name)


graph = graph_builder.compile()
graph.name = NN.safercast_agent