# DOC: Chatbot node and router

from typing_extensions import Literal

from langgraph.graph import END
from langgraph.types import Command

from langgraph.prebuilt import ToolNode
from langchain_core.messages import BaseMessage, RemoveMessage, ToolMessage, AIMessage
from langchain_core.runnables import Runnable
from langchain_core.language_models import LanguageModelInput

from ..common import utils
from ..common import names as N
from ..common.states import BaseGraphState
from ..nodes.tools import (

    DigitalTwinTool,
    SaferRainTool,
    SaferBuildingsTool,

    ICON2IIngestorTool,
    ICON2IRetrieverTool,
    DPCRetrieverTool,
    
    GeospatialOpsTool
)
# from agent.nodes.tools import DemoWeatherTool
# from agent.nodes.subgraphs.create_project import create_project_subgraph_interface_tool
# from agent.nodes.subgraphs.flooding_rainfall import flooding_rainfall_subgraph_interface_tool

tools_map = dict()

# INFO: ↓↓↓ Demo subgraph tools
# demo_weather_tool = DemoWeatherTool()
# tool_map[demo_weather_tool.name] = demo_weather_tool
# INFO: ↓↓↓ CREATE_PROJECT_SUBGRAPH_INTERFACE_TOOL and FLOODING_RAINFALL_SUBGRAPH_INTERFACE_TOOL are not used in the chatbot, but they are still defined here for potential future use
# tools_map[N.CREATE_PROJECT_SUBGRAPH_INTERFACE_TOOL] = create_project_subgraph_interface_tool
# tools_map[N.FLOODING_RAINFALL_SUBGRAPH_INTERFACE_TOOL] = flooding_rainfall_subgraph_interface_tool

# DOC: ↓↓↓ SaferPlaces API tools
tools_map[N.DIGITAL_TWIN_TOOL] = DigitalTwinTool()
tools_map[N.SAFER_RAIN_TOOL] = SaferRainTool()
tools_map[N.SAFERBUILDINGS_TOOL] = SaferBuildingsTool()
# DOC: ↓↓↓ SaferCast API tools
# tools_map[N.ICON2I_INGESTOR_TOOL] = ICON2IIngestorTool() # ???: It is not needed in the agent (?)
tools_map[N.ICON2I_RETRIEVER_TOOL] = ICON2IRetrieverTool()
tools_map[N.DPC_RETRIEVER_TOOL] = DPCRetrieverTool()
# DOC: ↓↓↓ Auxiliary tools
tools_map[N.GEOSPATIAL_OPS_TOOL] = GeospatialOpsTool()


tool_node = ToolNode([tool for tool in tools_map.values()])

llm_with_tools = utils._base_llm.bind_tools([tool for tool in tools_map.values()])


def set_tool_choice(tool_choice: list[str] | None = None) -> Runnable[LanguageModelInput, BaseMessage]:
    if tool_choice is None:
        llm_with_tools = utils._base_llm.bind_tools([])
    elif len(tool_choice) == 0:
        llm_with_tools = utils._base_llm.bind_tools([tool for tool in tools_map.values()])
    else:
        tool_choice = [tools_map[tool_name] for tool_name in tool_choice if tool_name in tools_map]
        llm_with_tools = utils._base_llm.bind_tools(tool_choice)
    return llm_with_tools


def chatbot_update_messages(state: BaseGraphState):
    """Update the messages in the state with the new messages."""
    messages = state.get("node_params", dict()).get(N.CHATBOT_UPDATE_MESSAGES, dict()).get("update_messages", [])
    return {'messages': messages, 'node_params': {N.CHATBOT_UPDATE_MESSAGES: { 'update_messages': None }}}

# !!!: openai.BadRequestError: Error code: 400 - {'error': {'message': "An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id' .. } }
def get_orphan_tool_calls(state: BaseGraphState):
    """If the last by one has tool_cals and last message is not a tool message, remove the last by one."""
    if len(state["messages"]) < 2:
        return []
    orpahn_tool_messages = []
    for mi,message in enumerate(state["messages"][:-1]):
        if not (hasattr(message, "tool_calls") and len(message.tool_calls) > 0):
            continue
        if type(state["messages"][mi + 1]) is not ToolMessage:
            orpahn_tool_messages.append(message)
    # if hasattr(state["messages"][-1], "tool_calls") and len(state["messages"][-1].tool_calls) > 0:
        # orpahn_tool_messages.append(state["messages"][-1])
    return orpahn_tool_messages
def fix_orphan_tool_calls(state: BaseGraphState):
    """If the last by one has tool_cals and last message is not a tool message, remove the last by one."""
    orphan_tool_messages = get_orphan_tool_calls(state)
    ai_tool_message = AIMessage(content='', tool_calls=orphan_tool_messages[-1].tool_calls)
    update_state = { 'messages': 
        [ RemoveMessage(id=m.id) for m in orphan_tool_messages ] +
        [ ai_tool_message ]
    } if len(orphan_tool_messages) > 0 else dict()
    return Command(goto=N.CHATBOT, update=update_state)


_chatbot_ending_edges = Literal[
    END,
    N.CHATBOT_UPDATE_MESSAGES, 
    N.FIX_ORPHAN_TOOL_CALLS,
    # N.DEMO_SUBGRAPH, 
    # N.CREATE_PROJECT_SUBGRAPH, 
    # N.FLOODING_RAINFALL_SUBGRAPH, 
    N.SAFERPLACES_API_SUBGRAPH, 
    N.SAFERCAST_API_SUBGRAPH
]
def chatbot(state: BaseGraphState) -> Command[_chatbot_ending_edges]:     # type: ignore
    state["messages"] = state.get("messages", [])

    if len(state["messages"]) > 0:
        
        if state.get("node_params", dict()).get(N.CHATBOT_UPDATE_MESSAGES, dict()).get("update_messages", None) is not None:
            return Command(goto=N.CHATBOT_UPDATE_MESSAGES)
        
        llm_with_tools = set_tool_choice(tool_choice = state.get("avaliable_tools", list()))

        # !!!: HACK: If the last message has tool calls and the next message is not a tool message, remove the last message
        # DOC: This is a workaround for the OpenAI API error: "An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id' .."
        print('\n-------- BEFORE FIX ------------\n', state['messages'], '\n--------------------------\n')
        orphan_tool_calls = get_orphan_tool_calls(state)
        if len(orphan_tool_calls) > 0:
            print(f"[DEBUG] chatbot: found orphan tool calls, fixing them: {get_orphan_tool_calls(state)}")
            return Command(goto=N.FIX_ORPHAN_TOOL_CALLS)
        
        print('\n-------- AFTER FIX ------------\n', state['messages'], '\n--------------------------\n')
        
        if isinstance(state["messages"][-1], AIMessage):
            ai_message = state["messages"][-1]
        else:
            ai_message = llm_with_tools.invoke(state["messages"])
        
        if hasattr(ai_message, "tool_calls") and len(ai_message.tool_calls) > 0:
            
            # DOC: get the first tool call, discard others (this is ugly asf) - edit: this works btw → user "get this and that" and the tool calls are "get-this-tool-call" and when it finishes "get-that-tool-call"
            tool_call = ai_message.tool_calls[0]
            ai_message.tool_calls = [tool_call] 
            
            # if tool_call['name'] == demo_weather_tool.name:
            #     return Command(goto = N.DEMO_SUBGRAPH, update = { "messages": [ ai_message ], "node_history": [N.CHATBOT, N.DEMO_SUBGRAPH] })
            
            # INFO: ↓↓↓ CREATE_PROJECT_SUBGRAPH_INTERFACE_TOOL and FLOODING_RAINFALL_SUBGRAPH_INTERFACE_TOOL are not used in the chatbot, but they are still defined here for potential future use
            # elif tool_call['name'] == N.CREATE_PROJECT_SUBGRAPH_INTERFACE_TOOL:
            #     return Command(goto = N.CREATE_PROJECT_SUBGRAPH, update = { "messages": [], "node_history": [N.CHATBOT, N.CREATE_PROJECT_SUBGRAPH] })
            # elif tool_call['name'] == N.FLOODING_RAINFALL_SUBGRAPH_INTERFACE_TOOL:
            #     return Command(goto = N.FLOODING_RAINFALL_SUBGRAPH, update = { "messages": [], "node_history": [N.CHATBOT, N.FLOODING_RAINFALL_SUBGRAPH] })
            
            if tool_call['name'] in (N.DIGITAL_TWIN_TOOL, N.SAFER_RAIN_TOOL, N.SAFERBUILDINGS_TOOL):
                return Command(goto = N.SAFERPLACES_API_SUBGRAPH, update = { "messages": [ai_message], "node_history": [N.CHATBOT, N.SAFERPLACES_API_SUBGRAPH] })
            
            elif tool_call['name'] in (N.ICON2I_INGESTOR_TOOL, N.ICON2I_RETRIEVER_TOOL, N.DPC_RETRIEVER_TOOL):
                return Command(goto = N.SAFERCAST_API_SUBGRAPH, update = { "messages": [ai_message], "node_history": [N.CHATBOT, N.SAFERCAST_API_SUBGRAPH] })
            
            elif tool_call['name'] == N.GEOSPATIAL_OPS_TOOL:
                return Command(goto = N.SAFERPLACES_API_SUBGRAPH, update = { "messages": [ai_message], "node_history": [N.CHATBOT, N.SAFERPLACES_API_SUBGRAPH] })
            
    
        return Command(goto = END, update = { "messages": [ ai_message ], "requested_agent": None, "node_params": dict(), "node_history": [N.CHATBOT] })