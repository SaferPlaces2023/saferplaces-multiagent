from typing import Any, Dict, List, Optional
import json

from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langgraph.types import interrupt

from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager, build_nowtime_system_message
from ...common.utils import _base_llm
from ...common.response_classifier import ResponseClassifier
from .tools.dpc_retriever_tool import DPCRetrieverTool
from .tools.meteoblue_retriever_tool import MeteoblueRetrieverTool
from .layers_agent import LayersAgent
from ..names import NodeNames
from ..prompts.safercast_agent_prompts import SaferCastInstructions


# ============================================================================
# Constants
# ============================================================================

# Agent registry description
SAFERCAST_AGENT_TOOLS = [
    DPCRetrieverTool,
    MeteoblueRetrieverTool
]
SAFERCAST_AGENT_DESCRIPTION = {
    "name": NodeNames.RETRIEVER_AGENT,
    "description": (
        "Specialized agent that retrieves meteorological and observational datasets "
        "and prepares them as layers for downstream processing and analysis.\n"
        "\n"
        "Available tools:\n"
        + "\n".join(
            f"  • {cls.__name__}: {cls.short_description}"
            for cls in SAFERCAST_AGENT_TOOLS
            if hasattr(cls, "short_description")
        )
    ),
    "examples": [
        "Retrieve precipitation forecast for Milan for the next 24 hours",
        "Get radar rainfall intensity (SRI) for northern Italy in the last 6 hours",
        "Download temperature map from DPC for a specified bbox and time range",
    ],
    "outputs": [
        "Meteorological raster layer — DPC product (SRI, SRT1/3/6/12/24, VMI, TEMP, LTG, …)",
        "Weather forecast raster layer — Meteoblue (precipitation, temperature, wind, …)",
    ],
    "prerequisites": {
        "DPCRetrieverTool": (
            "None — requires product name, bbox, and time range. Italy coverage only."
        ),
        "MeteoblueRetrieverTool": (
            "None — requires variable, bbox, and forecast time range. Global coverage."
        ),
    },
    "implicit_step_rules": [
        (
            "IMPLICIT STEP: use this agent as a preliminary step when a flood simulation requires "
            "observed or forecast rainfall raster input and no such layer exists in context."
        ),
    ],
}


# Invocation confirmation states
INVOCATION_PENDING = "pending"
INVOCATION_ACCEPTED = "accepted"
INVOCATION_REJECTED = "rejected"
INVOCATION_ABORT = "abort"

# State key constants
STATE_RETRIEVER_INVOCATION = "retriever_invocation"
STATE_RETRIEVER_CONFIRMATION = "retriever_invocation_confirmation"
STATE_RETRIEVER_REINVOCATION_REQUEST = "retriever_reinvocation_request"
STATE_RETRIEVER_CURRENT_STEP = "retriever_current_step"
STATE_TOOL_RESULTS = "tool_results"


# ============================================================================
# Tool Registry
# ============================================================================


class RetrieverAgentTools:
    """Singleton registry for managing retriever tools."""

    _instance: Optional["RetrieverAgentTools"] = None
    _tools: Dict[str, Any] = {}

    def __new__(cls) -> "RetrieverAgentTools":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_tools()
        return cls._instance

    def _initialize_tools(self) -> None:
        """Initialize all available retriever tools."""
        active_tools = [tool() for tool in SAFERCAST_AGENT_TOOLS]
        self._tools = {tool.name: tool for tool in active_tools}

    @property
    def tools(self) -> Dict[str, Any]:
        """Get all registered tools."""
        return self._tools

    @property
    def tools_instances(self) -> list:
        """Get all tool instances."""
        return list(self._tools.values())

    def get(self, tool_name: str, graph_state: MABaseGraphState = None) -> Any:
        """Get a specific tool by name."""
        tool = self._tools[tool_name]
        if graph_state is not None and hasattr(tool, "_set_graph_state"):
            tool._set_graph_state(graph_state)
        return tool


# ============================================================================
# Data Retriever Agent
# ============================================================================

class DataRetrieverAgent(MultiAgentNode):
    """Specialized agent for data retrieval and tool selection."""

    def __init__(self, name: str = NodeNames.RETRIEVER_AGENT, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.tools_registry = RetrieverAgentTools()
        self.llm = _base_llm.bind_tools(self.tools_registry.tools_instances)

    @staticmethod
    def _has_no_tool_calls(invocation: AIMessage) -> bool:
        """Check if invocation has no tool calls."""
        return len(getattr(invocation, "tool_calls", []) or []) == 0

    @staticmethod
    def _has_tool_calls(invocation: AIMessage) -> bool:
        """Check if invocation has tool calls."""
        return len(getattr(invocation, "tool_calls", []) or []) > 0

    @staticmethod
    def _get_tool_calls(invocation: AIMessage) -> list:
        """Get tool calls from an invocation."""
        return getattr(invocation, "tool_calls", []) or []

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main retriever execution logic."""

        # DOC: Switch case (from which situation i'm coming)
        invocation_reason = state.get("retriever_invocation_reason", "new_invocation")
        state["retriever_invocation_reason"] = None

        print(f"[{self.name}] → Invocation reason: {invocation_reason}")

        # DOC: From Supervisor plan step
        if invocation_reason == "new_invocation":
            tool_invocation = SaferCastInstructions.InvokeTools.Invocation.InvokeOneShot.stable(state)
            invocation: AIMessage = self.llm.invoke(tool_invocation)
            if DataRetrieverAgent._has_tool_calls(invocation):
                state["retriever_invocation"] = invocation
                state["retriever_current_step"] = 0
                state["retriever_invocation_confirmation"] = INVOCATION_PENDING

        # DOC: From InvocationConfirm [INVALID] (correct)
        elif invocation_reason == "invocation_provide_corrections":
            correct_tool_invocation = SaferCastInstructions.CorrectToolsInvocation.Invocation.ReInvokeOneShot.stable(state)
            invocation: AIMessage = self.llm.invoke(correct_tool_invocation)
            if DataRetrieverAgent._has_tool_calls(invocation):
                state["retriever_invocation"] = invocation
                state["retriever_current_step"] = 0
                state["retriever_invocation_confirmation"] = INVOCATION_PENDING

        # DOC: From InvocationConfirm [INVALID] (auto correct)
        elif invocation_reason == "invocation_auto_correct":
            auto_correct_tool_invocation = SaferCastInstructions.AutoCorrectToolsInvocation.Invocation.AutoReInvokeOneShot.stable(state)
            invocation: AIMessage = self.llm.invoke(auto_correct_tool_invocation)
            if DataRetrieverAgent._has_tool_calls(invocation):
                state["retriever_invocation"] = invocation
                state["retriever_current_step"] = 0
                state["retriever_invocation_confirmation"] = INVOCATION_PENDING

        return state


# ============================================================================
# Data Retriever Invocation Confirmation
# ============================================================================

class DataRetrieverInvocationConfirm(MultiAgentNode):
    """Confirmation and validation checkpoint for retriever tool invocations."""

    def __init__(self, name: str = NodeNames.RETRIEVER_INVOCATION_CONFIRM, enabled: bool = False, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.enabled = enabled
        self.llm = _base_llm
        self._classifier = ResponseClassifier(_base_llm)

    def _validate_invocation(self, tool_call: ToolCall, state: MABaseGraphState) -> list:
        """Validate a single tool call (inference + validation)."""
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or {}
        tool = RetrieverAgentTools().get(tool_name, state)

        tool_call["args"] = DataRetrieverInvocationConfirm._apply_inference_to_args(tool, tool_args, state)

        invocation_errors = DataRetrieverInvocationConfirm._validate_args(tool, tool_call["args"])
        if invocation_errors:
            return [{"tool_name": tool_name, "error_args": invocation_errors}]

        return []

    @staticmethod
    def _apply_inference_to_args(tool: Any, tool_args: Dict[str, Any], graph_state: MABaseGraphState) -> Dict[str, Any]:
        """Apply inference rules to get complete arguments."""
        inference_rules = tool._set_args_inference_rules()
        tool_args_with_state = {**tool_args, "_graph_state": graph_state}
        for arg_name, inferrer_fn in inference_rules.items():
            if arg_name not in tool_args or tool_args[arg_name] is None:
                tool_args[arg_name] = inferrer_fn(**tool_args_with_state)
        return tool_args

    @staticmethod
    def _validate_args(tool: Any, tool_args: Dict[str, Any]) -> Dict[str, str]:
        """Validate all arguments against rules."""
        errors = {}
        validation_rules = tool._set_args_validation_rules()
        for arg_name, validators_list in validation_rules.items():
            for validator_fn in validators_list:
                error = validator_fn(**tool_args)
                if error:
                    errors[arg_name] = error
        return errors

    def _handle_intent(self, state: MABaseGraphState, intent: str) -> MABaseGraphState:
        """Handle user intent after validation response."""
        intent_handler_map = {
            "provide_corrections": self._handle_provide_corrections,
            "auto_correct": self._handle_auto_correct,
            # "clarify_requirements": self._handle_clarify_requirements,
            # "acknowledge": self._handle_acknowledge,
            # "skip_tool": self._handle_skip_tool,
            "abort": self._handle_abort,
        }
        handler = intent_handler_map.get(intent, self._handle_auto_correct)
        return handler(state)

    @staticmethod
    def _handle_provide_corrections(state: MABaseGraphState) -> MABaseGraphState:
        state["retriever_invocation_confirmation"] = INVOCATION_PENDING
        state["retriever_invocation_reason"] = "invocation_provide_corrections"
        state["retriever_reinvocation_count"] = (state.get("retriever_reinvocation_count") or 0) + 1
        return state

    @staticmethod
    def _handle_auto_correct(state: MABaseGraphState) -> MABaseGraphState:
        state["retriever_invocation_confirmation"] = INVOCATION_PENDING
        state["retriever_invocation_reason"] = "invocation_auto_correct"
        state["retriever_reinvocation_count"] = (state.get("retriever_reinvocation_count") or 0) + 1
        return state

    @staticmethod
    def _handle_abort(state: MABaseGraphState) -> MABaseGraphState:
        state["retriever_invocation_confirmation"] = INVOCATION_ABORT
        return state

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main confirmation workflow."""

        # DOC: Get current invocation
        invocation = state.get("retriever_invocation")

        print(f"[{self.name}] → Current invocation: {invocation}")

        # DOC: If no tools have been called
        if not invocation or DataRetrieverAgent._has_no_tool_calls(invocation):
            return state

        # DOC: Get current tool call
        tool_call = DataRetrieverAgent._get_tool_calls(invocation)[0]

        # DOC: Validate tool call
        invocation_errors = self._validate_invocation(tool_call, state)

        # DOC: Interrupt for invalid tool call
        if invocation_errors:

            print(f"[{self.name}] → Invocation errors: {invocation_errors}")

            # DOC: Record validation errors in state
            state["retriever_invocation_errors"] = invocation_errors

            # DOC: Prepare interrupt
            invalid_invocation_message = self.llm.invoke(
                SaferCastInstructions.InvalidInvocationInterrupt.LLMInvalidInvocationInterrupt.Invocation.NotifyOneShot.stable(state)
            ).content
            interruption = interrupt({
                "content": invalid_invocation_message,
                "interrupt_type": "invalid-invocation",
            })

            # DOC: Solve interrupt for user response
            user_response = interruption.get("response", "User did not provide any response.")

            # DOC: Record user response in conversation history
            state["messages"] = [
                SystemMessage(content=invalid_invocation_message),
                HumanMessage(content=user_response),
            ]

            # DOC: Classify user intent
            intent = self._classifier.classify_validation_response(user_response)

            return self._handle_intent(state, intent)

        else:
            # DOC: No validation errors — accept invocation
            state["retriever_invocation_errors"] = None
            state["retriever_invocation_confirmation"] = INVOCATION_ACCEPTED
            return state

        # TODO: Confirmation enabled
        if self.enabled:
            raise NotImplementedError("Invocation confirmation not implemented yet.")


# ============================================================================
# Data Retriever Executor
# ============================================================================

class DataRetrieverExecutor(MultiAgentNode):
    """Executor for retriever tool invocations."""

    def __init__(self, name: str = NodeNames.RETRIEVER_EXECUTOR, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.layers_agent = LayersAgent()

    def _execute_tool_call(self, tool_name: str, tool_args: Dict[str, Any], state: MABaseGraphState) -> Any:
        """Execute a single tool call and return result."""
        tool = RetrieverAgentTools().get(tool_name, state)
        try:
            tool_result = tool._execute(**tool_args)
        except Exception as exc:
            tool_result = {"status": "error", "message": str(exc)}
        return tool_result

    def _add_layer_to_registry(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        result: Any,
        state: MABaseGraphState,
    ) -> None:
        """Add created layer to layer registry via layers agent."""
        if result.get("status") != "success":
            return

        state["layers_request"] = (
            f"Add a new layer from the following tool execution:\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {json.dumps(tool_args, indent=2)}\n"
            f"Result: {json.dumps(result, indent=2)}\n\n"
            f"Extract the layer URI from the result and create a descriptive title "
            f"and description based on the tool name and arguments."
        )

        print(f"[{self.name}] → Adding layer to registry...")
        layer_agent_state = self.layers_agent(state)
        state["layer_registry"] = layer_agent_state.get("layer_registry", state.get("layer_registry", []))

        if "additional_context" not in state:
            state["additional_context"] = {}
        if "relevant_layers" not in state["additional_context"]:
            state["additional_context"]["relevant_layers"] = {}
        state["additional_context"]["relevant_layers"]["is_dirty"] = True

        print(f"[{self.name}] ✓ Layer added to registry")

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main execution logic."""

        # DOC: get current invocation
        invocation = state.get("retriever_invocation")

        # DOC: Empty invocation
        if not DataRetrieverAgent._has_tool_calls(invocation):
            state["messages"] = [invocation]
            state["retriever_invocation"] = None
            state["retriever_current_step"] = None
            state["retriever_invocation_confirmation"] = None
            state["retriever_invocation_reason"] = None
            state["supervisor_invocation_reason"] = "step_no_tools"
            return state

        # DOC: get invocation confirmation status
        invocation_confirmation = state.get("retriever_invocation_confirmation")

        # DOC: Aborted invocation
        if invocation_confirmation == INVOCATION_ABORT:
            state["retriever_invocation"] = None
            state["retriever_current_step"] = None
            state["retriever_invocation_confirmation"] = None
            state["retriever_invocation_reason"] = None
            state["supervisor_invocation_reason"] = "step_skip"
            return state

        # DOC: get current step — mandatory valued if invocation exists
        invocation_current_step = state["retriever_current_step"]

        tool_responses = []
        step_error = False

        for tool_call in invocation.tool_calls[invocation_current_step:]:

            tool_call_id = tool_call["id"]
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args") or {}

            print(f"[{self.name}] → Executing: {tool_name}")

            tool_result = self._execute_tool_call(tool_name, tool_args, state)

            tool_response = ToolMessage(
                content=json.dumps(tool_result, indent=2),
                tool_call_id=tool_call_id,
                name=tool_name,
            )
            tool_responses.append(tool_response)

            if tool_result.get("status") == "error":
                step_error = True
                break

            self._add_layer_to_registry(
                tool_name=tool_name,
                tool_args=tool_args,
                result=tool_result,
                state=state,
            )

            state["retriever_current_step"] = state["retriever_current_step"] + 1

        if step_error:
            solved_tool_calls = invocation.tool_calls[: state["retriever_current_step"] + 1]
            invocation = AIMessage(content=invocation.content, tool_calls=solved_tool_calls)
            state["supervisor_invocation_reason"] = "step_error"
        else:
            state["supervisor_invocation_reason"] = "step_done"

        state["retriever_invocation"] = None
        state["retriever_current_step"] = None
        state["retriever_invocation_confirmation"] = None
        state["retriever_invocation_reason"] = None

        state["messages"] = [invocation, *tool_responses]

        return state