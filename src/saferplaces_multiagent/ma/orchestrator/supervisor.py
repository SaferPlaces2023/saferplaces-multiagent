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

# User response classification labels for plan confirmation
PLAN_RESPONSE_LABELS = {
    "accept": (
        "User accepts the plan and wants to proceed immediately. "
        "Examples: 'ok', 'yes', 'proceed', 'looks good', 'go ahead', 'do it', 'perfect'"
    ),
    "modify": (
        "User wants changes to the plan but still intends to execute something. "
        "Examples: 'change step 2', 'skip retriever', 'add more detail', 'swap order', "
        "'do only step 1', 'remove the last step'"
    ),
    "clarify": (
        "User needs more information before deciding (asking questions, not rejecting). "
        "Examples: 'what does step 1 do?', 'explain retriever', 'why two steps?', "
        "'what is DPC?', 'how long will this take?'"
    ),
    "reject": (
        "User rejects the plan approach and wants a completely different strategy. "
        "Examples: 'no that's wrong', 'different approach please', 'not what I meant', "
        "'try another way', 'that won't work'"
    ),
    "abort": (
        "User wants to cancel the entire operation without alternatives. "
        "Examples: 'cancel', 'stop', 'nevermind', 'forget it', 'abort', 'no thanks'"
    )
}


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
    def incremental_replanning_prompt(state: MABaseGraphState) -> str:
        """Generate prompt for incremental modifications (modify label)."""
        parsed_request = state.get("parsed_request", "No parsed request available")
        current_plan = state.get("plan", "No plan available")
        replan_request = state.get("replan_request")
        user_feedback = replan_request.content if replan_request else "No feedback"

        return (
            f"User requested modifications to the existing plan.\n"
            f"\n"
            f"Original request:\n{parsed_request}\n"
            f"\n"
            f"Current plan:\n{current_plan}\n"
            f"\n"
            f"User feedback:\n{user_feedback}\n"
            f"\n"
            f"Adjust the plan incrementally based on user feedback. "
            f"Keep what works and is not mentioned, modify only what's explicitly requested. "
            f"Minimize disruption to the overall approach."
        )

    @staticmethod
    def total_replanning_prompt(state: MABaseGraphState) -> str:
        """Generate prompt for total replanning (reject label)."""
        parsed_request = state.get("parsed_request", "No parsed request available")
        previous_plan = state.get("plan", "No plan available")
        replan_request = state.get("replan_request")
        user_feedback = replan_request.content if replan_request else "No feedback"

        return (
            f"User rejected the entire plan approach and wants a different strategy.\n"
            f"\n"
            f"Original request:\n{parsed_request}\n"
            f"\n"
            f"Previous plan (REJECTED):\n{previous_plan}\n"
            f"\n"
            f"User feedback:\n{user_feedback}\n"
            f"\n"
            f"Create a completely new plan from scratch. "
            f"Take a fundamentally different approach based on user requirements. "
            f"Do not repeat the rejected strategy."
        )

    @staticmethod
    def plan_explanation_prompt(state: MABaseGraphState, user_question: str) -> str:
        """Generate prompt to explain the plan (clarify label)."""
        plan = state.get("plan", [])
        parsed_request = state.get("parsed_request", {})
        
        return (
            f"User asked about the execution plan: '{user_question}'\n"
            f"\n"
            f"Original request:\n{parsed_request}\n"
            f"\n"
            f"Current plan:\n{plan}\n"
            f"\n"
            f"Provide a clear, concise explanation that answers the user's specific question. "
            f"Focus on helping them understand the plan without changing it. "
            f"Be informative but brief."
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
        # Choose prompt based on replan_type
        replan_type = state.get("replan_type")
        
        if state.get("plan_confirmation") == PLAN_REJECTED:
            if replan_type == "modify":
                # Incremental changes to existing plan
                human_prompt = SupervisorPrompts.incremental_replanning_prompt(state)
            elif replan_type == "reject":
                # Complete replanning with different approach
                human_prompt = SupervisorPrompts.total_replanning_prompt(state)
            else:
                # Fallback to total replanning
                human_prompt = SupervisorPrompts.total_replanning_prompt(state)
        else:
            # First time planning
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
        state["replan_type"] = None  # Reset after use

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
        self.llm = _base_llm
        self.max_clarify_iterations = 3  # Prevent infinite clarify loops

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
        print(f"[{self.name}] → Requesting plan confirmation... \n {plan}")
        confirmation_message = self._generate_confirmation_message(state, plan)
        interruption = interrupt({
            "content": confirmation_message,
            "interrupt_type": "plan-confirmation",
        })

        user_response = interruption.get("response", "User did not provide any response.")
        print(f"[{self.name}] → User response: {user_response}")

        # Classify user intent
        intent = self._classify_user_response(user_response)
        print(f"[{self.name}] → Classified intent: {intent}")

        # Dispatch based on classification
        if intent == "accept":
            return self._handle_accept(state)
        elif intent == "modify":
            return self._handle_modify(state, user_response)
        elif intent == "clarify":
            return self._handle_clarify(state, user_response)
        elif intent == "reject":
            return self._handle_reject(state, user_response)
        elif intent == "abort":
            return self._handle_abort(state)
        else:
            # Fallback: treat as reject
            print(f"[{self.name}] ⚠ Unknown intent, defaulting to reject")
            return self._handle_reject(state, user_response)

    def _classify_user_response(self, user_response: str) -> str:
        """Classify user intent using zero-shot classification."""
        import json
        
        classification_prompt = (
            "Classify the user's response into ONE of these categories:\n\n"
            f"{json.dumps(PLAN_RESPONSE_LABELS, indent=2)}\n\n"
            f"User response: '{user_response}'\n\n"
            "Return ONLY the label name (accept/modify/clarify/reject/abort) as a single word."
        )

        messages = [
            SystemMessage(content="You are a precise intent classifier. Return only the label name."),
            HumanMessage(content=classification_prompt)
        ]

        try:
            response = self.llm.invoke(messages)
            label = response.content.strip().lower()
            
            # Validate label
            if label in PLAN_RESPONSE_LABELS:
                return label
            else:
                print(f"[{self.name}] ⚠ Invalid label '{label}', defaulting to 'reject'")
                return "reject"
        except Exception as e:
            print(f"[{self.name}] ⚠ Classification error: {e}, defaulting to 'reject'")
            return "reject"

    @staticmethod
    def _handle_accept(state: MABaseGraphState) -> MABaseGraphState:
        """Handle accept: proceed with execution."""
        state["plan_confirmation"] = PLAN_ACCEPTED
        state["replan_request"] = None
        state["replan_type"] = None
        print("[SupervisorPlannerConfirm] ✓ Plan accepted")
        return state

    @staticmethod
    def _handle_modify(state: MABaseGraphState, user_response: str) -> MABaseGraphState:
        """Handle modify: incremental replanning."""
        state["plan_confirmation"] = PLAN_REJECTED
        state["replan_request"] = HumanMessage(content=user_response)
        state["replan_type"] = "modify"
        state["current_step"] = None
        state["awaiting_user"] = False
        print("[SupervisorPlannerConfirm] ↻ Requesting incremental modifications")
        return state

    @staticmethod
    def _handle_reject(state: MABaseGraphState, user_response: str) -> MABaseGraphState:
        """Handle reject: complete replanning with different approach."""
        state["plan_confirmation"] = PLAN_REJECTED
        state["replan_request"] = HumanMessage(content=user_response)
        state["replan_type"] = "reject"
        state["current_step"] = None
        state["awaiting_user"] = False
        print("[SupervisorPlannerConfirm] ↻ Requesting total replanning")
        return state

    @staticmethod
    def _handle_abort(state: MABaseGraphState) -> MABaseGraphState:
        """Handle abort: cancel operation entirely."""
        state["plan"] = []
        state["plan_confirmation"] = PLAN_REJECTED
        state["replan_request"] = None
        state["replan_type"] = None
        state["current_step"] = None
        state["supervisor_next_node"] = NodeNames.FINAL_RESPONDER
        print("[SupervisorPlannerConfirm] ✕ Operation aborted by user")
        return state

    def _handle_clarify(self, state: MABaseGraphState, user_question: str) -> MABaseGraphState:
        """Handle clarify: explain plan and re-interrupt."""
        clarify_count = state.get("clarify_iteration_count", 0)
        
        # Prevent infinite clarify loops
        if clarify_count >= self.max_clarify_iterations:
            print(f"[{self.name}] ⚠ Max clarify iterations reached, auto-accepting")
            return self._handle_accept(state)
        
        state["clarify_iteration_count"] = clarify_count + 1
        
        # Generate explanation
        explanation = self._generate_plan_explanation(state, user_question)
        print(f"[{self.name}] → Providing explanation...")
        
        # New interrupt with explanation
        interruption = interrupt({
            "content": f"{explanation}\n\nDo you want to proceed with this plan?",
            "interrupt_type": "plan-clarification",
        })
        
        new_response = interruption.get("response", "User did not provide any response.")
        print(f"[{self.name}] → User response after clarification: {new_response}")
        
        # Recursive: classify the new response
        intent = self._classify_user_response(new_response)
        print(f"[{self.name}] → Classified intent: {intent}")
        
        # Dispatch again (recursive resolution)
        if intent == "accept":
            state["clarify_iteration_count"] = 0  # Reset counter
            return self._handle_accept(state)
        elif intent == "modify":
            state["clarify_iteration_count"] = 0
            return self._handle_modify(state, new_response)
        elif intent == "clarify":
            # Recursive clarify (will check max iterations next time)
            return self._handle_clarify(state, new_response)
        elif intent == "reject":
            state["clarify_iteration_count"] = 0
            return self._handle_reject(state, new_response)
        elif intent == "abort":
            state["clarify_iteration_count"] = 0
            return self._handle_abort(state)
        else:
            state["clarify_iteration_count"] = 0
            return self._handle_reject(state, new_response)

    def _generate_plan_explanation(self, state: MABaseGraphState, user_question: str) -> str:
        """Generate explanation of the plan using LLM."""
        explanation_prompt = SupervisorPrompts.plan_explanation_prompt(state, user_question)
        
        messages = [
            SystemMessage(content="You are a helpful assistant explaining an execution plan."),
            HumanMessage(content=explanation_prompt)
        ]
        
        try:
            response = self.llm.invoke(messages)
            return response.content.strip()
        except Exception as e:
            print(f"[{self.name}] ⚠ Explanation generation error: {e}")
            return "I apologize, I couldn't generate the explanation. Please accept or reject the plan."

    def _generate_confirmation_message(self, state: MABaseGraphState, plan: List[Dict]) -> str:
        """Generate a clear, schematic confirmation message using LLM."""
        # Format plan as a readable list
        plan_text = self._format_plan_for_display(plan)
        
        confirmation_prompt = (
            f"Generate a clear, concise confirmation message for the user about the following execution plan.\n"
            f"The message should be:\n"
            f"- Schematic and organized (use bullet points or numbering)\n"
            f"- Concise but complete\n"
            f"- End with a clear question asking if they want to proceed\n"
            f"\n"
            f"Plan:\n{plan_text}\n"
            f"\n"
            f"Generate the confirmation message (be brief and well-formatted):"
        )
        
        messages = [
            SystemMessage(content="You are a helpful assistant that communicates execution plans clearly and concisely."),
            HumanMessage(content=confirmation_prompt)
        ]
        
        try:
            response = self.llm.invoke(messages)
            return response.content.strip()
        except Exception as e:
            print(f"[{self.name}] ⚠ Message generation error: {e}")
            # Fallback to formatted plan
            return f"Do you want to proceed with the following plan?\n{plan_text}"

    @staticmethod
    def _format_plan_for_display(plan: List[Dict]) -> str:
        """Format plan steps into a readable string."""
        formatted_steps = []
        for i, step in enumerate(plan, 1):
            agent = step.get("agent", "Unknown")
            goal = step.get("goal", "No description")
            formatted_steps.append(f"{i}. [{agent}] {goal}")
        return "\n".join(formatted_steps)

    @staticmethod
    def _auto_confirm(state: MABaseGraphState) -> MABaseGraphState:
        """Auto-confirm plan without user interaction."""
        state["plan_confirmation"] = PLAN_ACCEPTED
        state["replan_request"] = None
        state["replan_type"] = None
        return state


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