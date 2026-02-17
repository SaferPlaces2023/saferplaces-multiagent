import json

from langgraph.types import Command
from langgraph.graph import StateGraph
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from ..common.names import NN
from ..common.states import BaseGraphState, MABaseGraphState
from ..common.utils import _base_llm

from .dpc_retriever_tool import DPCRetrieverTool, DPCRetrieverSchema

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
        
        self.TOOLS = dict(
            dpc_retriever_tool = DPCRetrieverTool()
        )
        
        self.llm = _base_llm.bind_tools(list(self.TOOLS.values()))

    
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

        invocation = self.llm.invoke(messages)
        
        print(invocation.content)

        # If model didn't propose tool calls, just log and return (optional)
        if not getattr(invocation, "tool_calls", None):
            state.setdefault("tool_results", {})
            state["tool_results"][f"step_{state['current_step']}"] = {"status": "no_tool_call", "text": getattr(invocation, "content", "")}
            return state

        tool_call = invocation.tool_calls[0]          # take first for minimal skeleton
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args", {}) or {}

        # --- Validate tool name ---
        if tool_name not in self.TOOLS:
            state.setdefault("tool_results", {})
            state["tool_results"][f"step_{state['current_step']}"] = {"status": "unknown_tool", "tool": tool_name, "args": tool_args}
            # ask user (or fallback)
            state["awaiting_user"] = True
            state["messages"] = [
                AIMessage(content=f"Non riconosco il tool richiesto ({tool_name}). Puoi riformulare cosa vuoi ottenere?")
            ]
            return state
        
        # --- Validate args BEFORE writing AI tool-call message into state ---
        err = False
        # err = "Il time range deve essere dentro la settimana corrente" #validate_tool_args(tool_name, tool_args)
        if err:
            state["awaiting_user"] = True
            # domanda mirata: qui puoi estrarre i missing field dall'errore pydantic se vuoi
            state["messages"] = [
                AIMessage(content=f"Mi manca/incoerente qualche parametro per procedere ({tool_name}). Dettaglio: {err}\n"
                                  f"Puoi fornirmi i valori mancanti?")
            ]
            # NON appendere resp (che contiene tool_call) -> eviti contratto tool-response
            return state


        # --- Execute tool ---
        result = self.TOOLS[tool_name]._execute(**tool_args)
        # Persist results
        state.setdefault("tool_results", {})
        state["tool_results"][f"step_{state['current_step']}"] = {
            "tool": tool_name,
            "args": tool_args,
            "result": result
        }

        
        
        # tool_response = ToolMessage(content=f"Output generated from tool: {result}", tool_call_id=tool_call["id"])
        tool_response = ToolMessage(
            content=f"""
        Layer generated:
        - Title: DPC retrieved data layer.
        - URI: 's3://example-bucket/dpc-out/dpc-temperature.tif',
        - Parameters: {tool_args}
        """,
        tool_call_id=tool_call["id"]
        )


        print([tc["id"] for tc in invocation.tool_calls])
        ai_message_out = self.llm.invoke([
            invocation,
            tool_response
        ])
        # Now it's safe to append the AIMessage with tool_call AND the ToolMessage
        state["messages"] = [
            invocation,
            tool_response,
            ai_message_out
        ]
        # print('>>>>> ', ai_message_out.content)

        return state


class GraphNodes:
    pass

    
GN = GraphNodes()


graph_builder = StateGraph(MABaseGraphState)


graph_builder.add_node("retrieval_agent", DataRetrieverAgent())

graph_builder.add_edge(START, "retrieval_agent")


graph = graph_builder.compile()
graph.name = NN.safercast_agent