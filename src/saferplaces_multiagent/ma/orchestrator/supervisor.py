from typing import Any, Dict, List, Optional
import ast
import datetime

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, BaseMessage
from langgraph.types import interrupt

from ...common.base_models import Thought
from ...multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager
from ...common import utils
from ...common.utils import _base_llm
from ...common.response_classifier import ResponseClassifier
from ..names import NodeNames
from ..prompts.supervisor_agent_prompts import SupervisorInstructions
from ..specialized.layers_agent import LayersAgent


# ============================================================================
# Constants & Data Models
# ============================================================================

# Plan confirmation states (semantic enum values from PlanConfirmationStatus)
class PlanConfirmationLabels:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFY = "modify"
    ABORTED = "aborted"

PLAN_PENDING = PlanConfirmationLabels.PENDING
PLAN_ACCEPTED = PlanConfirmationLabels.ACCEPTED
PLAN_REJECTED = PlanConfirmationLabels.REJECTED
PLAN_MODIFY = PlanConfirmationLabels.MODIFY
PLAN_ABORTED = PlanConfirmationLabels.ABORTED



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

    _MAX_REPLAN_ITERATIONS = 5

    def __init__(
        self,
        name: str = NodeNames.SUPERVISOR_AGENT,
        log_state: bool = True
    ):
        super().__init__(name, log_state, update_CoT=True)
        self.llm = _base_llm.with_structured_output(ExecutionPlan)
        self.layer_agent = LayersAgent()
        self.specialized_agents = [
            NodeNames.MODELS_AGENT,
            NodeNames.RETRIEVER_AGENT,
            NodeNames.LAYERS_AGENT,
            NodeNames.MAP_AGENT,
        ]

    def _define_CoT(self, state: MABaseGraphState) -> list[Thought]:
        if state['plan']:
            return [
                Thought(
                    owner=self.name,
                    message=f"Planned {len(state['plan'])} steps",
                    payload=state['plan']
                )
            ]

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        
        # ???: [GUARD] → abort if replan loop has exceeded the maximum allowed iterations
        replan_count = state.get("replan_iteration_count") or 0
        if replan_count >= self._MAX_REPLAN_ITERATIONS:
            print(f"[{self.name}] ⚠ Max replan iterations ({self._MAX_REPLAN_ITERATIONS}) reached — aborting")
            state["plan"] = None
            state["current_step"] = None
            state["plan_confirmation"] = None
            return state

        # DOC: Switch case (from which situation i'm coming)
        invocation_reason = state.get("supervisor_invocation_reason")
        state['supervisor_invocation_reason'] = None

        print(f"[{self.name}] → Invocation reason: {invocation_reason}")
        
        # DOC: From RequestParser (new request)
        if invocation_reason == "new_request":
            state["replan_iteration_count"] = 0
            create_plan_invocation = SupervisorInstructions.PlanGeneration.Invocations.PlanOneShot.stable(state)
            plan: ExecutionPlan = self.llm.invoke(create_plan_invocation)
            print(f"[{self.name}] → Generated plan: {plan}")
            plan_steps = [ step.model_dump() for step in plan.steps if step.agent in self.specialized_agents ]
            if len(plan_steps) > 0:
                state["plan"] = plan_steps
                state["current_step"] = 0
                state["plan_confirmation"] = PLAN_PENDING
             
        # DOC: From PlannerConfirm (modify)
        elif invocation_reason == PLAN_MODIFY:
            state["replan_iteration_count"] = (state.get("replan_iteration_count") or 0) + 1
            modify_plan_invocation = SupervisorInstructions.PlanModification.Invocations.ReplanOneShot.stable(state)
            plan: ExecutionPlan = self.llm.invoke(modify_plan_invocation)
            plan_steps = [ step.model_dump() for step in plan.steps if step.agent in self.specialized_agents ]
            if len(plan_steps) > 0:
                state["plan"] = plan_steps
                state["current_step"] = 0
                state["plan_confirmation"] = PLAN_PENDING

        # DOC: From Specialized agent step (with no tool calls)
        elif invocation_reason == "step_no_tools":
            _current_step = state.get("current_step") or 0
            _plan = state.get("plan") or []
            _is_support_agent = (
                _current_step < len(_plan)
                and _plan[_current_step]["agent"] in [NodeNames.LAYERS_AGENT, NodeNames.MAP_AGENT]
            )
            if _is_support_agent:
                # DOC: Support agents (map/layers): no tools = nothing to do → advance silently
                print(f"[{self.name}] → Support agent step_no_tools — advancing step")
                state["current_step"] = _current_step + 1
            else:
                state["replan_iteration_count"] = (state.get("replan_iteration_count") or 0) + 1
                modify_plan_invocation = SupervisorInstructions.PlanModificationDueStepNoTools.Invocations.ReplanDueNoToolsOneShot.stable(state)
                plan: ExecutionPlan = self.llm.invoke(modify_plan_invocation)
                plan_steps = [ step.model_dump() for step in plan.steps if step.agent in self.specialized_agents ]
                state["plan"] = plan_steps
                if len(plan_steps) > 0:
                    state["current_step"] = 0
                    state["plan_confirmation"] = PLAN_PENDING
        
        # DOC: From Specialized agent InvocationConfirm (abort)
        elif invocation_reason == "step_skip":
            state["replan_iteration_count"] = (state.get("replan_iteration_count") or 0) + 1
            modify_plan_invocation = SupervisorInstructions.PlanModificationDueStepSkip.Invocations.ReplanDueSkipOneShot.stable(state)
            plan: ExecutionPlan = self.llm.invoke(modify_plan_invocation)
            plan_steps = [ step.model_dump() for step in plan.steps if step.agent in self.specialized_agents ]
            state["plan"] = plan_steps
            if len(plan_steps) > 0:
                state["current_step"] = 0
                state["plan_confirmation"] = PLAN_PENDING

        # DOC: From Specialized agent ToolExecutor (error)
        elif invocation_reason == "step_error":
            state["replan_iteration_count"] = (state.get("replan_iteration_count") or 0) + 1
            modify_plan_invocation = SupervisorInstructions.PlanModificationDueStepError.Invocations.ReplanDueErrorOneShot.stable(state)
            plan: ExecutionPlan = self.llm.invoke(modify_plan_invocation)
            plan_steps = [ step.model_dump() for step in plan.steps if step.agent in self.specialized_agents ]
            state["plan"] = plan_steps
            if len(plan_steps) > 0:
                state["current_step"] = 0
                state["plan_confirmation"] = PLAN_PENDING

        # DOC: From Specialized agent step done (success)
        elif invocation_reason == "step_done":
            state["current_step"] = (state.get("current_step") or 0) + 1

        print(f"[{self.name}] → Plan state: {state.get('plan')}")
        return state


class SupervisorPlannerConfirm(MultiAgentNode):
    """Confirmation checkpoint for user approval of execution plan."""

    def __init__(
        self,
        name: str = NodeNames.SUPERVISOR_PLANNER_CONFIRM,
        enabled: bool = False,
        log_state: bool = True
    ):
        super().__init__(name, log_state)
        self.enabled = enabled
        self.llm = _base_llm
        self._classifier = ResponseClassifier(self.llm)

    @staticmethod
    def _unnecessary_confirmation(state: MABaseGraphState) -> bool:
        
        plan = state.get('plan')
        current_step = state.get('current_step') or 0

        ignore_agents = [NodeNames.LAYERS_AGENT, NodeNames.MAP_AGENT]

        if plan and len(plan) > current_step and plan[current_step]['agent'] in ignore_agents:
            return True

        if current_step >= len(plan):
            return True

        return False

    @staticmethod
    def _auto_confirm(state: MABaseGraphState) -> MABaseGraphState:
        """Auto-confirm plan without user interaction."""
        return SupervisorPlannerConfirm._handle_accept(state)
    
    def _handle_intent(self, state: MABaseGraphState, intent: str) -> MABaseGraphState:
        """Handle user intent after plan confirmation."""
        intent_handler_map = {
            "accept": self._handle_accept,
            "modify": self._handle_modify,
            "clarify": self._handle_clarify,
            "reject": self._handle_abort,
            "abort": self._handle_abort
        }
        handler = intent_handler_map.get(intent, self._handle_clarify)
        return handler(state)
    
    @staticmethod
    def _handle_accept(state: MABaseGraphState) -> MABaseGraphState:
        """Handle accept: proceed with execution."""
        state["plan_confirmation"] = PLAN_ACCEPTED
        return state
    
    @staticmethod
    def _handle_modify(state: MABaseGraphState) -> MABaseGraphState:
        """Handle modify: incremental replanning."""
        state["plan_confirmation"] = PLAN_MODIFY
        state["supervisor_invocation_reason"] = PLAN_MODIFY
        return state
    
    @staticmethod
    def _handle_abort(state: MABaseGraphState) -> MABaseGraphState:
        """Handle abort: cancel operation entirely."""
        state["plan_confirmation"] = PLAN_ABORTED
        return state
    
    def _handle_clarify(self, state: MABaseGraphState) -> MABaseGraphState:
        """Handle clarify: explain plan and re-interrupt using an iterative loop (no recursion)."""
        
        # DOC: [Guard] → Check interaction budget to prevent excessive user interruptions
        interaction_count = state.get("interaction_count", 0)
        interaction_budget = state.get("interaction_budget", 8)
        if interaction_count >= interaction_budget:
            print(f"[{self.name}] ⚠ Interaction budget exhausted ({interaction_budget}), auto-aborting")
            return self._handle_abort(state)

        # DOC: Increment action count
        state["interaction_count"] = interaction_count + 1

        # DOC: Interrupt for plan clarification
        clarify_invocation = SupervisorInstructions.PlanClarification.Invocations.PlanClarifyOneShot.stable(state)
        clarify_message = (
            f"{self.llm.invoke(clarify_invocation).content}\n\n"
            "Do you want to proceed with this plan?"
        )
        interruption = interrupt({
            "content": clarify_message,
            "interrupt_type": "plan-clarification",
        })

        # DOC: Solve interrupt for user response
        user_response = interruption.get("response", "User did not provide any response.")

        # DOC: Record user response in conversation history so all downstream LLMs can see it
        state["messages"] = [
            SystemMessage(content=clarify_message),
            HumanMessage(content=user_response)
        ]
        # DOC: Classify user intent
        intent = self._classifier.classify_plan_response(user_response)
        print(f"[{self.name}] → Classified intent: {intent}")
        
        return self._handle_intent(state, intent)


    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Interactive plan confirmation with user."""

        # DOC: Get current plan
        plan = state.get("plan")

        print(f"[{self.name}] → Current plan: {plan}")

        # DOC: If no plan was generated
        if plan is None or len(plan) == 0:
            state['supervisor_next_node'] = NodeNames.FINAL_RESPONDER
            return state
        
        # DOC: If confirmation is not enabled
        if not self.enabled:
            return self._auto_confirm(state)
        
        # DOC: If confirmation is not needed
        if self._unnecessary_confirmation(state):
            return self._auto_confirm(state)

        # DOC: Get confirmation state
        plan_confirmation = state.get("plan_confirmation")

        # DOC: Confirmation already given - no need to ask
        print(f"[{self.name}] → Plan confirmation state: {plan_confirmation}")
        if plan_confirmation != PLAN_PENDING:
            return state

        # DOC: Interrupt for request user confirmation
        confirmation_message = self.llm.invoke(SupervisorInstructions.PlanConfirmation.LLMConfirmationInterrupt.Invocation.ConfirmOneShot.stable(state)).content
        # confirmation_message = SupervisorInstructions.PlanConfirmation.ConfirmationInterrupt.StaticMessage.stable(state).message
        interruption = interrupt({
            "content": confirmation_message,
            "interrupt_type": "plan-confirmation",
        })

        # DOC: Solve interrupt for user response
        user_response = interruption.get("response", "User did not provide any response.")

        # DOC: Record user response in conversation history so all downstream LLMs can see it
        state["messages"] = [
            SystemMessage(content=confirmation_message),
            HumanMessage(content=user_response)
        ]

        # DOC: Classify user intent
        intent = self._classifier.classify_plan_response(user_response)
        print(f"[{self.name}] → Classified intent: {intent}")

        return self._handle_intent(state, intent)
        


class SupervisorRouter(MultiAgentNode):
    """Router that determines the next execution node based on plan state."""

    def __init__(
        self,
        name: str = NodeNames.SUPERVISOR_ROUTER,
        enabled: bool = False,
        log_state: bool = True
    ):
        super().__init__(name, log_state, update_CoT=True)
        self.enabled = enabled
        self.llm = _base_llm
        self._classifier = ResponseClassifier(self.llm)
        self.layer_agent = LayersAgent()


    def _define_CoT(self, state: MABaseGraphState) -> List[Thought]:
        if state['supervisor_next_node']:
            return [
                Thought(
                    owner=self.name,
                    message=f'Call for next step → {state["supervisor_next_node"]}'
                )
            ]

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Determine next node in execution graph."""
        
        # DOC: Determine next node based on current plan and execution state
        plan = state.get('plan')

        # DOC: Get plan confirmation status
        plan_confirmation = state.get('plan_confirmation')

        # DOC: abort
        if plan_confirmation == PLAN_ABORTED:
            state["plan"] = None
            state["current_step"] = None
            state["plan_confirmation"] = None
            state["supervisor_invocation_reason"] = None
            state['supervisor_next_node'] = NodeNames.FINAL_RESPONDER
            return state

        # DOC: get current step — mandatory valued if plan exists
        current_step = state["current_step"]

        if current_step < len(plan):
            # DOC: Next steps in plan
            state['supervisor_next_node'] = plan[current_step]['agent']
        else:
            # DOC: Plan completed
            state['supervisor_next_node'] = NodeNames.FINAL_RESPONDER

        return state

            