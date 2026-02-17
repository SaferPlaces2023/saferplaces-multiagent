
from pydantic import BaseModel, Field
from typing import List

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm



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
            "Count how the buildings were saffected by flood"
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
]


class Prompts:
    """Prompts for orchestration."""

    SUPERVISOR_PROMPT = '\n'.join((
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
        self.llm = _base_llm.with_structured_output(ExecutionPlan)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:

        if state.get("awaiting_user"):
            return state

        if state.get("plan") is not None and state.get("current_step") is not None:
            state["current_step"] += 1
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

        return state
    

class SupervisorRouter:

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        def supervisor_next_node(state: MABaseGraphState) -> str:

            if state.get("awaiting_user"):
                return END

            plan = state.get("plan")
            step = state.get("current_step")

            if not plan:
                return "chat_final"

            if step is not None and step < len(plan):
                return plan[step]["agent"]

            return "chat_final"
        
        state['supervisor_next_node'] = supervisor_next_node(state)
        
        return state