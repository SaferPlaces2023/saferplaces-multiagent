from typing import Any, Dict, List, Optional
import ast
import datetime

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, BaseMessage
from langgraph.types import interrupt

from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager
from ...common import utils
from ...common.utils import _base_llm
from ...common.response_classifier import ResponseClassifier
from ...common.templates import format_plan_confirmation
from ...common.context_builder import ContextBuilder, PlanningContext
from ...common.execution_narrative import ExecutionNarrative
from ..names import NodeNames
from ..prompts.supervisor_agent_prompts import OrchestratorPrompts
from ..specialized.layers_agent import LayersAgent
from ..specialized.models_agent import MODELS_AGENT_DESCRIPTION
from ..specialized.safercast_agent import SAFERCAST_AGENT_DESCRIPTION


# ============================================================================
# Constants & Data Models
# ============================================================================

# Plan confirmation states (semantic enum values from PlanConfirmationStatus)
PLAN_PENDING = "pending"
PLAN_ACCEPTED = "accepted"
PLAN_REJECTED = "rejected"
PLAN_MODIFY = "modify"
PLAN_ABORTED = "aborted"


class ExecutionPlan(BaseModel):
    """Execution plan with ordered steps for agent orchestration."""

    class PlanStep(BaseModel):
        agent: str = Field(description="Name of the specialized agent to execute this step")
        goal: str = Field(description="High-level description of what this step should accomplish")

    steps: List[PlanStep]


# ============================================================================
# Supervisor Agents
# ============================================================================

class SupervisorAgent(MultiAgentNode):
    """Agent responsible for planning and orchestrating execution steps."""

    def __init__(self, name: str = NodeNames.SUPERVISOR_AGENT, log_state: bool = True):
        super().__init__(name, log_state)
        self.llm = _base_llm.with_structured_output(ExecutionPlan)
        self.layer_agent = LayersAgent()

    # def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
    #     """Execute supervisor planning."""
    #     return self.run(state)

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
        # Skip if user aborted
        if state.get("plan_confirmation") == PLAN_ABORTED:
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
        """Generate execution plan using LLM with enriched context (§1 PLN-013)."""

        # Guard: abort if replan loop has exceeded the maximum allowed iterations
        MAX_REPLAN_ITERATIONS = 5
        replan_count = state.get("replan_iteration_count") or 0
        if replan_count >= MAX_REPLAN_ITERATIONS:
            print(f"[{self.name}] ⚠ Max replan iterations ({MAX_REPLAN_ITERATIONS}) reached — aborting")
            state["plan"] = []
            state["plan_confirmation"] = PLAN_ABORTED
            state["supervisor_next_node"] = NodeNames.FINAL_RESPONDER
            return state

        # PHASE 1: Build enriched PlanningContext (§1 Context Enrichment Pipeline)
        planning_context = ContextBuilder.build(state)
        context_str = ContextBuilder.format_for_prompt(planning_context)
        
        # PHASE 2: Initialize ExecutionNarrative if first planning (§3 Execution Narrative)
        if not state.get("execution_narrative"):
            state["execution_narrative"] = ExecutionNarrative()
        
        narrative = state["execution_narrative"]
        narrative.started_at = datetime.datetime.utcnow().isoformat()
        narrative.request_summary = planning_context.request_intent
        narrative.request_type = planning_context.request_type

        main_prompt = OrchestratorPrompts.MainContext.stable().to(SystemMessage)
        
        if state.get("plan_confirmation") in (PLAN_REJECTED, PLAN_MODIFY):
            replan_type = state.get("replan_type")
            if replan_type == "modify":
                planning_prompt = OrchestratorPrompts.Plan.IncrementalReplanning.stable(state).to(HumanMessage)
            elif replan_type == "reject":
                planning_prompt = OrchestratorPrompts.Plan.TotalReplanning.stable(state).to(HumanMessage)
            else:
                planning_prompt = OrchestratorPrompts.Plan.TotalReplanning.stable(state).to(HumanMessage)
        else:
            planning_prompt = OrchestratorPrompts.Plan.CreatePlan.stable(state).to(HumanMessage)

        # Add enriched context to messages
        context_message = HumanMessage(content=context_str)
        
        messages = [
            main_prompt,
            context_message,
            planning_prompt
        ]

        # Invoke LLM for plan
        response: ExecutionPlan = self.llm.invoke(messages)

        # Validate and store plan
        validated_steps = [
            step.model_dump()
            for step in response.steps
            if step.agent in [agent['name'] for agent in OrchestratorPrompts.Plan.AGENT_REGISTRY]
        ]

        state["plan"] = validated_steps
        state["current_step"] = 0
        state["plan_confirmation"] = PLAN_PENDING
        state["replan_request"] = None
        state["replan_type"] = None  # Reset after use
        
        # Update narrative with plan summary
        narrative.total_steps = len(validated_steps)
        if validated_steps:
            steps_desc = "; ".join([f"{s.get('agent')}: {s.get('goal')}" for s in validated_steps])
            narrative.plan_summary = steps_desc
            print(f"[{self.name}] ✓ Plan: {len(validated_steps)} steps")
        else:
            narrative.plan_summary = "(Nessun'azione necessaria)"
            print(f"[{self.name}] ✓ No action needed (general query)")

        return state


class SupervisorPlannerConfirm(MultiAgentNode):
    """Confirmation checkpoint for user approval of execution plan."""

    def __init__(self, name: str = NodeNames.SUPERVISOR_PLANNER_CONFIRM, enabled: bool = False, log_state: bool = True):
        super().__init__(name, log_state)
        self.enabled = enabled
        self.llm = _base_llm
        self._classifier = ResponseClassifier(self.llm)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute confirmation logic."""
        if not self.enabled:
            return self._auto_confirm(state)

        # return self.run(state)
        return super().__call__(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Interactive plan confirmation with user."""
        plan = state.get("plan", [])
        plan_confirmed = state.get("plan_confirmation")

        if not plan:
            return self._auto_confirm(state)
        if plan_confirmed != PLAN_PENDING:
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

        # Record user response in conversation history so all downstream LLMs can see it
        state["messages"] = [HumanMessage(content=user_response)]

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
        """Classify user intent using hybrid rule-based + LLM classification."""
        return self._classifier.classify_plan_response(user_response)

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
        state["plan_confirmation"] = PLAN_MODIFY
        state["replan_request"] = HumanMessage(content=user_response)
        state["replan_type"] = "modify"
        state["current_step"] = None
        state["replan_iteration_count"] = (state.get("replan_iteration_count") or 0) + 1
        print("[SupervisorPlannerConfirm] ↻ Requesting incremental modifications")
        return state

    @staticmethod
    def _handle_reject(state: MABaseGraphState, user_response: str) -> MABaseGraphState:
        """Handle reject: complete replanning with different approach."""
        state["plan_confirmation"] = PLAN_REJECTED
        state["replan_request"] = HumanMessage(content=user_response)
        state["replan_type"] = "reject"
        state["current_step"] = None
        state["replan_iteration_count"] = (state.get("replan_iteration_count") or 0) + 1
        print("[SupervisorPlannerConfirm] ↻ Requesting total replanning")
        return state

    @staticmethod
    def _handle_abort(state: MABaseGraphState) -> MABaseGraphState:
        """Handle abort: cancel operation entirely."""
        state["plan"] = []
        state["plan_confirmation"] = PLAN_ABORTED
        state["replan_request"] = None
        state["replan_type"] = None
        state["current_step"] = None
        state["supervisor_next_node"] = NodeNames.FINAL_RESPONDER
        print("[SupervisorPlannerConfirm] ✕ Operation aborted by user")
        return state

    def _handle_clarify(self, state: MABaseGraphState, user_question: str) -> MABaseGraphState:
        """Handle clarify: explain plan and re-interrupt using an iterative loop (no recursion)."""
        current_question = user_question

        while True:
            interaction_count = state.get("interaction_count", 0)
            interaction_budget = state.get("interaction_budget", 8)

            # Prevent exceeding interaction budget
            if interaction_count >= interaction_budget:
                print(f"[{self.name}] ⚠ Interaction budget exhausted ({interaction_budget}), auto-accepting")
                return self._handle_accept(state)

            state["interaction_count"] = interaction_count + 1

            # Generate explanation
            explanation = self._generate_plan_explanation(state, current_question)
            print(f"[{self.name}] → Providing explanation...")

            # Single interrupt per iteration — no nesting
            interruption = interrupt({
                "content": f"{explanation}\n\nDo you want to proceed with this plan?",
                "interrupt_type": "plan-clarification",
            })

            new_response = interruption.get("response", "User did not provide any response.")
            print(f"[{self.name}] → User response after clarification: {new_response}")

            # Record clarification response in conversation history
            state["messages"] = [HumanMessage(content=new_response)]

            intent = self._classify_user_response(new_response)
            print(f"[{self.name}] → Classified intent: {intent}")

            if intent == "accept":
                return self._handle_accept(state)
            elif intent == "modify":
                return self._handle_modify(state, new_response)
            elif intent == "clarify":
                # Continue loop instead of recursing
                current_question = new_response
            elif intent == "reject":
                return self._handle_reject(state, new_response)
            elif intent == "abort":
                return self._handle_abort(state)
            else:
                return self._handle_reject(state, new_response)

    def _generate_plan_explanation(self, state: MABaseGraphState, user_question: str) -> str:
        """Generate explanation of the plan using LLM."""
        # explanation_prompt = SupervisorPrompts.plan_explanation_prompt(state, user_question)
        
        messages = [
            # SystemMessage(content="You are a helpful assistant explaining an execution plan."),
            # HumanMessage(content=explanation_prompt)
            OrchestratorPrompts.Plan.PlanExplanation.ExplainerMainContext.stable().to(SystemMessage),
            OrchestratorPrompts.Plan.PlanExplanation.RequestExplanation.stable(state, user_question).to(HumanMessage),
        ]
        
        try:
            response = self.llm.invoke(messages)
            return response.content.strip()
        except Exception as e:
            print(f"[{self.name}] ⚠ Explanation generation error: {e}")
            return "I apologize, I couldn't generate the explanation. Please accept or reject the plan."

    def _generate_confirmation_message(self, state: MABaseGraphState, plan: List[Dict]) -> str:
        """Generate a structured confirmation message using deterministic template."""
        parsed_request = state.get("parsed_request", {})
        return format_plan_confirmation(plan, parsed_request=parsed_request)

    @staticmethod
    def _auto_confirm(state: MABaseGraphState) -> MABaseGraphState:
        """Auto-confirm plan without user interaction."""
        state["plan_confirmation"] = PLAN_ACCEPTED
        state["replan_request"] = None
        state["replan_type"] = None
        return state


class SupervisorRouter(MultiAgentNode):
    """Router that determines the next execution node based on plan state."""

    def __init__(self, name: str = NodeNames.SUPERVISOR_ROUTER, enabled: bool = False, log_state: bool = True):
        super().__init__(name, log_state)
        self.enabled = enabled
        self.llm = _base_llm
        self._classifier = ResponseClassifier(self.llm)
        self.layer_agent = LayersAgent()

    # def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
    #     """Execute routing logic."""
    #     return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Determine next node in execution graph."""
        # PHASE 1: Update execution narrative (§3 PLN-013)
        # If we're here with plan + current_step > 0, the previous step just completed
        plan = state.get("plan", [])
        current_step = state.get("current_step", 0)
        
        if plan and current_step > 0:
            self._update_execution_narrative(state, plan, current_step - 1)
        
        # Check and update context if dirty (happens between any steps)
        self._update_additional_context(state)

        # Optional mid-plan checkpoint interrupt
        if self.enabled:
            abort_requested = self._maybe_checkpoint_interrupt(state)
            if abort_requested:
                state["supervisor_next_node"] = NodeNames.FINAL_RESPONDER
                print(f"[{self.name}] × Mid-plan abort by user → FINAL_RESPONDER")
                return state

        # Then determine next node
        next_node = self._determine_next_node(state)
        state["supervisor_next_node"] = next_node
        print(f"[{self.name}] → Next: {next_node}")
        return state

    def _update_execution_narrative(self, state: MABaseGraphState, plan: List[dict], completed_step_index: int) -> None:
        """
        Update execution narrative with results from a completed step (§3 PLN-013).
        
        Called after a specialized agent finishes execution.
        Records the step result, any layers created, and tool results.
        """
        narrative = state.get("execution_narrative")
        if not narrative:
            return
        
        step = plan[completed_step_index]
        agent_name = step.get("agent", "unknown")
        goal = step.get("goal", "")
        
        # Try to extract tool results for this step
        tool_results = state.get("tool_results", {})
        step_result_key = f"step_{completed_step_index}"
        
        from ...common.execution_narrative import StepResult
        
        step_result = StepResult(
            step_index=completed_step_index,
            agent=agent_name,
            goal=goal,
            outcome="success" if step_result_key in tool_results else "pending",
            output_summary=f"Step completato: {agent_name}"
        )
        
        narrative.add_step_result(step_result)
        print(f"[{self.name}] ✓ Narrative updated: {agent_name}")

    def _maybe_checkpoint_interrupt(self, state: MABaseGraphState) -> bool:
        """Emit a step-checkpoint interrupt if mid-plan conditions are met.

        Returns True if the user requested abort, False otherwise.
        """
        plan = state.get("plan")
        current_step = state.get("current_step")

        # Only interrupt after at least one step has been executed and plan is not exhausted
        if not plan or current_step is None or current_step == 0 or current_step >= len(plan):
            return False

        # The step that was just completed is at index current_step - 1
        completed_step = plan[current_step - 1]

        checkpoint_message = OrchestratorPrompts.Plan.StepCheckpoint.stable(state, completed_step)
        print(f"[{self.name}] → Emitting step-checkpoint interrupt (step {current_step}/{len(plan)})...")

        interruption = interrupt({
            "content": checkpoint_message.message,
            "interrupt_type": "step-checkpoint",
        })

        user_response = interruption.get("response", "")
        print(f"[{self.name}] → Checkpoint response: {user_response}")

        intent = self._classify_checkpoint_response(user_response)
        print(f"[{self.name}] → Checkpoint intent: {intent}")

        if intent == "abort":
            state["plan"] = []
            state["plan_confirmation"] = PLAN_ABORTED
            return True

        # Default: continue (also used as fallback for unknown intents)
        return False

    def _classify_checkpoint_response(self, user_response: str) -> str:
        """Classify user checkpoint response using hybrid classifier."""
        # Checkpoint has only two outcomes: continue or abort
        # Use plan classifier and map non-abort labels to "continue"
        label = self._classifier.classify_plan_response(user_response)
        if label == "abort":
            return "abort"
        return "continue"

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

        # Minipatch - don't know why but sometimes relevant_layers_list is encapsulated in a list
        if isinstance(relevant_layers_list, list) and len(relevant_layers_list) == 1 and isinstance(relevant_layers_list[0], list):
            relevant_layers_list = relevant_layers_list[0]

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