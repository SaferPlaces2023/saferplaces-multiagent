from typing import Any, Dict, List, Optional
import ast

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, BaseMessage
from langgraph.types import interrupt

from ...common.states import MABaseGraphState, StateManager
from ...common import utils
from ...common.utils import _base_llm
from ..names import NodeNames
from ..specialized.layers_agent import LayersAgent
from ..specialized.models_agent import MODELS_AGENT_DESCRIPTION
from ..specialized.safercast_agent import SAFERCAST_AGENT_DESCRIPTION


# ============================================================================
# Constants
# ============================================================================

AGENT_REGISTRY = [
    {
        "name": NodeNames.MODELS_SUBGRAPH,
        "description": MODELS_AGENT_DESCRIPTION["description"],
        "examples": MODELS_AGENT_DESCRIPTION["examples"],
    },
    {
        "name": NodeNames.RETRIEVER_SUBGRAPH,
        "description": SAFERCAST_AGENT_DESCRIPTION["description"],
        "examples": SAFERCAST_AGENT_DESCRIPTION["examples"],
    },
]

VALID_AGENT_NAMES = {agent["name"] for agent in AGENT_REGISTRY}

# Plan confirmation states
PLAN_PENDING = "pending"
PLAN_ACCEPTED = "accepted"
PLAN_REJECTED = "rejected"


# ============================================================================
# Prompts
# ============================================================================

class SupervisorPrompts:
    """Centralized prompts for orchestration tasks."""

    SYSTEM_PROMPT = (
        "You are a high-level orchestration agent.\n"
        "\n"
        "Your task:\n"
        "- Analyze the parsed user request.\n"
        "- Decide if specialized agents are needed to execute the task.\n"
        "- If agents are needed, break the task into ordered execution steps.\n"
        "- If the request is a general question or doesn't require actions, return an empty plan.\n"
        "- Each step (if any) must specify:\n"
        "  - the agent name\n"
        "  - the goal of that step\n"
        "\n"
        "Rules:\n"
        "- Only use agents from the provided registry.\n"
        "- Do NOT invent new agents.\n"
        "- Do NOT execute tools.\n"
        "- Do NOT ask the user questions.\n"
        "- Focus only on execution planning.\n"
        "- Keep the plan minimal and logically ordered.\n"
        "- Empty plan is valid for informational queries."
    )

    @staticmethod
    def planning_prompt(state: MABaseGraphState) -> str:
        """Generate initial planning prompt."""
        parsed_request = state.get("parsed_request", "No parsed request available")
        additional_context = state.get("plan_additional_context", "No additional context available")
        agent_registry_str = str(AGENT_REGISTRY)

        return (
            f"Parsed request:\n{parsed_request}\n"
            f"\n"
            f"Additional context:\n{additional_context}\n"
            f"\n"
            f"Available agents:\n{agent_registry_str}"
        )

    @staticmethod
    def replanning_prompt(state: MABaseGraphState) -> str:
        """Generate replanning prompt after user rejection."""
        parsed_request = state.get("parsed_request", "No parsed request available")
        current_plan = state.get("plan", "No plan available")
        replan_request = state.get("replan_request")
        user_requirements = replan_request.content if replan_request else "No requirements"

        return (
            f"Parsed request:\n{parsed_request}\n"
            f"\n"
            f"User asked to revise the proposed plan.\n"
            f"Here is the current plan:\n{current_plan}\n"
            f"\n"
            f"User requirements: {user_requirements}\n"
            f"\n"
            f"Produce a new plan that satisfies the user's requirements. "
            f"You can modify, reorder, add or remove steps and their goals."
        )


# ============================================================================
# Data Models
# ============================================================================

class ExecutionPlan(BaseModel):
    """Execution plan with ordered steps for agent orchestration."""

    class PlanStep(BaseModel):
        agent: str = Field(description="Name of the specialized agent to execute this step")
        goal: str = Field(description="High-level description of what this step should accomplish")

    steps: List[PlanStep]


# ============================================================================
# Supervisor Agents
# ============================================================================

class SupervisorAgent:
    """Agent responsible for planning and orchestrating execution steps."""

    def __init__(self):
        self.name = NodeNames.SUPERVISOR_AGENT
        self.llm = _base_llm.with_structured_output(ExecutionPlan)
        self.layer_agent = LayersAgent()

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute supervisor planning."""
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main planning logic."""
        # Early exits for non-planning states
        if self._should_skip_planning(state):
            return state

        # Ensure required data is present
        if "parsed_request" not in state:
            return state

        print(f"[{self.name}] → Planning...")

        # Generate execution plan
        state = self._generate_plan(state)

        return state

    def _should_skip_planning(self, state: MABaseGraphState) -> bool:
        """Check if planning should be skipped."""
        if state.get("awaiting_user"):
            return True

        # Skip if already planning a confirmed step
        if (
            state.get("plan") is not None
            and state.get("plan_confirmation") == PLAN_ACCEPTED
            and state.get("current_step") is not None
        ):
            step_num = state["current_step"]
            total_steps = len(state["plan"])
            print(f"[{self.name}] → Step {step_num}/{total_steps}")
            return True

        return False

    def _generate_plan(self, state: MABaseGraphState) -> MABaseGraphState:
        """Generate execution plan using LLM."""
        # Choose prompt based on planning or replanning
        if state.get("plan_confirmation") == PLAN_REJECTED:
            human_prompt = SupervisorPrompts.replanning_prompt(state)
        else:
            human_prompt = SupervisorPrompts.planning_prompt(state)

        messages = [
            SystemMessage(content=SupervisorPrompts.SYSTEM_PROMPT),
            HumanMessage(content=human_prompt),
        ]

        # Invoke LLM for plan
        response: ExecutionPlan = self.llm.invoke(messages)

        # Validate and store plan
        validated_steps = [
            step.model_dump()
            for step in response.steps
            if step.agent in VALID_AGENT_NAMES
        ]

        state["plan"] = validated_steps
        state["current_step"] = 0
        state["awaiting_user"] = False
        state["plan_confirmation"] = PLAN_PENDING
        state["replan_request"] = None

        # Log plan
        if validated_steps:
            print(f"[{self.name}] ✓ Plan: {len(validated_steps)} steps")
        else:
            print(f"[{self.name}] ✓ No action needed (general query)")

        return state


class SupervisorPlannerConfirm:
    """Confirmation checkpoint for user approval of execution plan."""

    def __init__(self, enabled: bool = False):
        self.name = NodeNames.SUPERVISOR_PLANNER_CONFIRM
        self.enabled = enabled

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute confirmation logic."""
        if not self.enabled:
            return self._auto_confirm(state)

        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Interactive plan confirmation with user."""
        plan = state.get("plan", [])
        plan_confirmed = state.get("plan_confirmation")

        if not plan or plan_confirmed != PLAN_PENDING:
            return state

        # Request user confirmation
        print(f"Do you want to proceed with the plan: {plan}?")
        interruption = interrupt({
            "content": f"Do you want to proceed with the plan: {plan}?",
            "interrupt_type": "plan-confirmation",
        })

        response = interruption.get("response", "User did not provide any response.")

        if response == "ok":
            state["plan_confirmation"] = PLAN_ACCEPTED
            state["replan_request"] = None
        else:
            # User rejected: prepare for replanning
            self._handle_rejection(state, response)

        return state

    @staticmethod
    def _auto_confirm(state: MABaseGraphState) -> MABaseGraphState:
        """Auto-confirm plan without user interaction."""
        state["plan_confirmation"] = PLAN_ACCEPTED
        state["replan_request"] = None
        return state

    @staticmethod
    def _handle_rejection(state: MABaseGraphState, response: str) -> None:
        """Handle plan rejection and prepare for replanning."""
        state["current_step"] = None
        state["awaiting_user"] = False
        state["plan_confirmation"] = PLAN_REJECTED
        state["replan_request"] = HumanMessage(content=response)


class SupervisorRouter:
    """Router that determines the next execution node based on plan state."""

    def __init__(self):
        self.name = NodeNames.SUPERVISOR_ROUTER
        self.layer_agent = LayersAgent()

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute routing logic."""
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Determine next node in execution graph."""
        # Check and update context if dirty (happens between any steps)
        self._update_additional_context(state)
        
        # Then determine next node
        next_node = self._determine_next_node(state)
        state["supervisor_next_node"] = next_node
        print(f"[{self.name}] → Next: {next_node}")
        return state

    def _update_additional_context(self, state: MABaseGraphState) -> None:
        """Retrieve relevant layers for planning context."""
        layer_registry = state.get("layer_registry", [])
        additional_context = state.get("additional_context", {})
        relevant_layers = additional_context.get("relevant_layers", {})

        # Check if context refresh is needed
        needs_refresh = (
            layer_registry
            and (
                len(relevant_layers) == 0
                or relevant_layers.get("is_dirty", False)
            )
        )

        if not needs_refresh:
            return

        print(f"[{self.name}] → Refreshing relevant layers...")

        # Query layer agent for relevant layers
        state["layers_request"] = self._build_layers_request(state)
        layer_agent_state = self.layer_agent(state)

        # Update state with layer agent response
        state["layer_registry"] = layer_agent_state.get("layer_registry", layer_registry)
        state["layers_invocation"] = layer_agent_state.get("layers_invocation")
        state["layers_response"] = layer_agent_state.get("layers_response")

        # Parse and store relevant layers
        relevant_layers_list = [
            utils.try_default(lambda: ast.literal_eval(lr.content), lr.content) if isinstance(lr, BaseMessage) else lr
            for lr in (layer_agent_state.get("layers_response") or [])
        ]

        state["additional_context"] = {
            "relevant_layers": {
                "layers": relevant_layers_list,
                "is_dirty": False,
            }
        }
        print(f"[{self.name}] ✓ Context refreshed")

    @staticmethod
    def _build_layers_request(state: MABaseGraphState) -> str:
        """Build request for layer agent."""
        parsed_request = state.get("parsed_request", "No parsed request available")
        return (
            f"User has this request:\n{parsed_request}\n"
            "Retrieve the relevant layers from available layers."
        )

    @staticmethod
    def _determine_next_node(state: MABaseGraphState) -> str:
        """Compute the next node based on execution state."""
        if state.get("awaiting_user"):
            return "END"

        plan = state.get("plan")
        current_step = state.get("current_step")

        # No plan: proceed to final response
        if not plan:
            return NodeNames.FINAL_RESPONDER

        # Execute next step from plan
        if current_step is not None and current_step < len(plan):
            agent_name = plan[current_step]["agent"]
            
            # Initialize specialized agent cycle
            agent_type = "models" if agent_name == NodeNames.MODELS_SUBGRAPH else "retriever"
            StateManager.initialize_specialized_agent_cycle(state, agent_type)
            
            return agent_name

        # Plan exhausted: finalize response
        return NodeNames.FINAL_RESPONDER