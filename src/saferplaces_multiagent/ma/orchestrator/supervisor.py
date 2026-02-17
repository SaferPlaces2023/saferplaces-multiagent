
from pydantic import BaseModel, Field
from typing import List

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames, AgentNames


AGENT_REGISTRY = [
    {
        "name": NodeNames.DIGITAL_TWIN_AGENT,
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
        "name": NodeNames.SIMULATIONS_AGENT,
        "description": (
            "Run simulation models"
        ),
        "examples": [
            "Simulate 100mm of rain",
            "Count how the buildings were saffected by flood"
        ]
    },



    {
        "name": NodeNames.RETRIEVAL_AGENT,
        "description": (
            "Retrieve data from third-part provider."
        ),
        "examples": [
            "How is the current temperature in Milan?",
            "Get rainfall value for next 3 hours"
        ]
    },

    {
        "name": NodeNames.OPERATIONAL_AGENT,
        "description": (
            "Perform a geospatial operation between layers."
        ),
        "examples": [
            "Cut flood map where its level is above 1 meter.",
            "Get buildings that are in 100 meters radious from river network",
            "Which is the average value of water depth in a bounding box"
        ]
    }
]


class Prompts:
    """Prompts for orchestration."""

    SUPERVISOR_PROMPT = '\n'.join((
        "You are a high-level orchestration agent.",
        "",
        "Your task:",
        "- Analyze the parsed user request.",
        "- Decide if specialized agents are needed to execute the task.",
        "- If agents are needed, break the task into ordered execution steps.",
        "- If the request is a general question or doesn't require actions, return an empty plan.",
        "- Each step (if any) must specify:",
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
        "- Empty plan is valid for informational queries.",
    ))

    PLANNING_PROMPT = staticmethod(lambda parsed_request: '\n'.join((
        "Parsed request:",
        f"{parsed_request}",
        "",
        "Available agents:",
        f"{AGENT_REGISTRY}"
    )))


class ExecutionPlan(BaseModel):
    """Execution plan with ordered steps for agent orchestration."""

    class PlanStep(BaseModel):
        agent: str = Field(description="Name of the specialized agent to execute this step")
        goal: str = Field(description="High-level description of what this step should accomplish")

    steps: List[PlanStep]



class SupervisorAgent:
    """Agent responsible for planning and orchestrating execution steps."""

    def __init__(self):
        self.name = AgentNames.SUPERVISOR_AGENT
        self.llm = _base_llm.with_structured_output(ExecutionPlan)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{AgentNames.SUPERVISOR_AGENT}] → Planning...")

        if state.get("awaiting_user"):
            return state

        if state.get("plan") is not None and state.get("current_step") is not None:
            # state["current_step"] += 1
            print(f"[{AgentNames.SUPERVISOR_AGENT}] → Step {state['current_step']}/{len(state['plan'])}")
            return state

        if "parsed_request" not in state:
            return state

        parsed_request = state["parsed_request"]

        invoke_messages = [
            SystemMessage(content=Prompts.SUPERVISOR_PROMPT),
            HumanMessage(content=Prompts.PLANNING_PROMPT(parsed_request))
        ]
        response: ExecutionPlan = self.llm.invoke(invoke_messages)

        valid_agent_names = {agent["name"] for agent in AGENT_REGISTRY}

        validated_steps = []
        for step in response.steps:
            if step.agent in valid_agent_names:
                validated_steps.append(step.model_dump())

        state["plan"] = validated_steps
        state["current_step"] = 0
        state["awaiting_user"] = False
        
        if len(validated_steps) > 0:
            print(f"[{AgentNames.SUPERVISOR_AGENT}] ✓ Plan: {len(validated_steps)} steps")
        else:
            print(f"[{AgentNames.SUPERVISOR_AGENT}] ✓ No action needed (general query)")

        return state
    

class SupervisorRouter:
    """Router that determines the next node based on execution plan."""

    def __init__(self):
        self.name = NodeNames.SUPERVISOR_ROUTER

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        def supervisor_next_node(state: MABaseGraphState) -> str:

            if state.get("awaiting_user"):
                return END

            plan = state.get("plan")
            step = state.get("current_step")

            if not plan:
                return NodeNames.FINAL_RESPONDER

            if step is not None and step < len(plan):
                return plan[step]["agent"]

            return NodeNames.FINAL_RESPONDER
        
        next_node = supervisor_next_node(state)
        state['supervisor_next_node'] = next_node
        print(f"[{NodeNames.SUPERVISOR_ROUTER}] → Next: {next_node}")
        
        return state