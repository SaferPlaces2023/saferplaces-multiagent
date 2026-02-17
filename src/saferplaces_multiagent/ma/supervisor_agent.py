import json

from pydantic import BaseModel, Field
from typing import List

from langgraph.graph import StateGraph, START, END

from ..common.states import MABaseGraphState
from ..common.utils import _base_llm



AGENT_REGISTRY = [
    {
        "name": "digital_twin_agent",
        "description": (
            "Build a geospatial digital twin."
        ),
        "examples": [
            "Elevation in Milan",
            "Twin for Rome",
            "Activate area in Paris"
        ]
    },

    {
        "name": "simulations_agent",
        "description": (
            "Run simulation models"
        ),
        "examples": [
            "Simulate 100mm of rain",
            "Count how the buildings were affected by flood"
        ]
    },



    {
        "name": "retrieval_agent",
        "description": (
            "Retrieve data from third-part provider."
        ),
        "examples": [
            "How is the current temperature in Milan?",
            "Get rainfall value for next 3 hours"
        ]
    },

    {
        "name": "operational_agent",
        "description": (
            "Perform a geospatial operation between layers."
        ),
        "examples": [
            "Cut flood map where its level is above 1 meter.",
            "Get buildings that are in 100 meters radious from river network",
            "Which is the average value of water depth in a bounding box"
        ]
    }

    # ... cambio simblogia
    # ... spostati a new york 
]


class ExecutionPlan(BaseModel):

    class PlanStep(BaseModel):
        agent: str = Field(description="Name of the specialized agent to execute this step")
        goal: str = Field(description="High-level description of what this step should accomplish")
        
    steps: List[PlanStep]



class Prompts:

    supervisor_prompt = '\n'.join((
        "You are a high-level orchestration agent.",
        "",
        "Your task:",
        "- Analyze the parsed user request.",
        "- Decide which specialized agents must execute the task.",
        "- Break the task into ordered execution steps.",
        "- Each step must specify:",
        "  - the agent name",
        "  - the goal of that step",
        "",
        "Rules:",
        "- Only use agents from the provided registry.",
        "- Do NOT invent new agents.",
        "- Do NOT execute tools.",
        "- Do NOT ask the user questions.",
        "- Focus only on execution planning.",
        "- Keep the plan minimal and logically ordered.",
    ))

    planning_prompt = lambda parsed_request: '\n'.join((
        "Parsed request:",
        f"{parsed_request}",
        "",
        "Avaliable agents:"
        f"{AGENT_REGISTRY}"
    ))


class SupervisorAgent:

    def __init__(self):
        self.llm = _base_llm.with_structured_output(ExecutionPlan, include_raw=True)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:

        if "parsed_request" not in state:
            return state

        parsed_request = state["parsed_request"]

        result = self.llm.invoke([
            {
                "role": "system",
                "content": Prompts.supervisor_prompt
            },
            {
                "role": "user",
                "content": Prompts.planning_prompt(parsed_request)
            }
        ])
        
        response: ExecutionPlan = result["parsed"]
        raw_msg = result["raw"]  # AIMessage with response_metadata
        
        # Extract token usage from the raw AIMessage
        usage = getattr(raw_msg, 'usage_metadata', None) or {}
        llm_meta = state.get("llm_metadata", {})
        llm_meta["supervisor_agent"] = {
            "model": raw_msg.response_metadata.get("model_name", "gpt-4o-mini"),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        state["llm_metadata"] = llm_meta

        valid_agent_names = {agent["name"] for agent in AGENT_REGISTRY}

        validated_steps = []
        for step in response.steps:
            if step.agent in valid_agent_names:
                validated_steps.append(step.model_dump())

        state["plan"] = validated_steps
        state["current_step"] = 0
        state["awaiting_user"] = False

        return state


class SupervisorRouterNode:

    def __call__(self, state: MABaseGraphState):
        return state

    @staticmethod
    def route(state: MABaseGraphState):

        if state.get("awaiting_user"):
            return END

        plan = state.get("plan")
        step = state.get("current_step")

        if not plan:
            return "chat_final"

        if step is not None and step < len(plan):
            return plan[step]["agent"]

        return "chat_final"


def build_supervisor_subgraph():

    subgraph = StateGraph(MABaseGraphState)

    supervisor_node = SupervisorAgent()
    router_node = SupervisorRouterNode()

    subgraph.add_node("supervisor", supervisor_node)
    subgraph.add_node("router", router_node)

    subgraph.add_edge("supervisor", "router")

    subgraph.add_conditional_edges(
        "router",
        SupervisorRouterNode.route,
        {
            
            # "digital_twin_agent": "digital_twin_agent",
            # "simulations_agent": "simulations_agent",
            "retrieval_agent": END,
            # "operational_agent": "operational_agent",

            "chat_final": END,
            
            END: END,
        }
    )

    subgraph.set_entry_point("supervisor")

    return subgraph.compile()