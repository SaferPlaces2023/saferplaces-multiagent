
from pydantic import BaseModel, Field
from typing import List

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage
from langgraph.types import Command, interrupt

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames, NodeNames
from ..specialized.safercast_agent import SAFERCAST_AGENT_DESCRIPTION
from ..specialized.models_agent import MODELS_AGENT_DESCRIPTION
from ..specialized.layers_agent import Prompts as LayersPrompts


AGENT_REGISTRY = [
    # {
    #     "name": NodeNames.DIGITAL_TWIN_AGENT,
    #     "description": (
    #         "Build a geospatial digital twin."
    #     ),
    #     "examples": [
    #         "Elevation in Milan",
    #         "Twin for Rome",
    #         "Activate area in Paris"
    #     ]
    # },

    # DOC: Models agent — flood models, fire propagation, structural impact analyses
    {
        "name": NodeNames.MODELS_SUBGRAPH,
        "description": MODELS_AGENT_DESCRIPTION["description"],
        "examples": MODELS_AGENT_DESCRIPTION["examples"]
    },


    # DOC: Safercast agent — meteo / clima data retriever
    {
        "name": NodeNames.RETRIEVER_SUBGRAPH,
        "description": SAFERCAST_AGENT_DESCRIPTION["description"],
        "examples": SAFERCAST_AGENT_DESCRIPTION["examples"]
    },

    # {
    #     "name": NodeNames.OPERATIONAL_AGENT,
    #     "description": (
    #         "Perform a geospatial operation between layers."
    #     ),
    #     "examples": [
    #         "Cut flood map where its level is above 1 meter.",
    #         "Get buildings that are in 100 meters radious from river network",
    #         "Which is the average value of water depth in a bounding box"
    #     ]
    # }
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

    PLANNING_PROMPT = staticmethod(lambda state: '\n'.join((
        "Parsed request:",
        f"{state.get('parsed_request', 'No parsed request available')}",
        "",
        "Available layers in current session:",
        LayersPrompts.format_layers_description(state.get("layers_registry", [])),
        "",
        "Available agents:",
        f"{AGENT_REGISTRY}"
    )))

    REPLANNING_PROMPT = staticmethod(lambda state: '\n'.join((
        "Parsed request:",
        f"{state.get('parsed_request') or 'No parsed request available.'}",
        "",
        "Available layers in current session:",
        LayersPrompts.format_layers_description(state.get("layers_registry", [])),
        "",
        "User asked to revise the proposed plan",
        "Here is the current plan:",
        f"{state.get('plan') or 'No plan available.'}",
        "",
        f"User requirements: {state['replan_request'].content}",
        "Produce a new plan that satisfies the user's requirements. You can modify, reorder, adding or remove steps and their goals."
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
        self.name = NodeNames.SUPERVISOR_AGENT
        self.llm = _base_llm.with_structured_output(ExecutionPlan)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:

        if state.get("awaiting_user"):
            return state

        if state.get("plan") is not None and state.get("plan_confirmation") == 'accepted' and state.get("current_step") is not None:
            print(f"[{NodeNames.SUPERVISOR_AGENT}] → Step {state['current_step']}/{len(state['plan'])}")
            return state

        if "parsed_request" not in state:
            return state

        print(f"[{NodeNames.SUPERVISOR_AGENT}] → Planning...")
        
        if state.get("plan_confirmation") != 'rejected':
            invoke_messages = [
                SystemMessage(content=Prompts.SUPERVISOR_PROMPT),
                HumanMessage(content=Prompts.PLANNING_PROMPT(state))
            ]
        else:
            invoke_messages = [
                SystemMessage(content=Prompts.SUPERVISOR_PROMPT),
                SystemMessage(content=Prompts.REPLANNING_PROMPT(state))
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
        state["plan_confirmation"] = 'pending'
        state['replan_request'] = None
        
        if len(validated_steps) > 0:
            print(f"[{NodeNames.SUPERVISOR_AGENT}] ✓ Plan: {len(validated_steps)} steps")
        else:
            print(f"[{NodeNames.SUPERVISOR_AGENT}] ✓ No action needed (general query)")

        return state
    
    
class SupervisorPlannerConfirm:
    
    def __init__(self):
        self.name = NodeNames.SUPERVISOR_PLANNER_CONFIRM
        self.enabled = False

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        if self.enabled:
            return self.run(state)
        state["plan_confirmation"] = 'accepted'
        state['replan_request'] = None
        return state
    
    def run(self, state: MABaseGraphState) -> MABaseGraphState:    
        plan = state.get('plan') or []
        plan_confirmed = state.get('plan_confirmation')
        if len(plan) > 0 and plan_confirmed == 'pending':
            print(f"Do you want to proceed with the plan: {plan}?",)
            interruption = interrupt({
                "content": f"Do you want to proceed with the plan: {plan}?",
                "interrupt_type": "plan-confirmation"
            })
            print('solve interruption', interruption)
            response = interruption.get('response', 'User did not provide any response.')
            if response == 'ok':
                state["plan_confirmation"] = 'accepted'
                state["replan_request"] = None
            else:
                state["current_step"] = None
                state["awaiting_user"] = False
                state['messages'] = []
                state["plan_confirmation"] = 'rejected'
                state["replan_request"] = HumanMessage(content=response)
                
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