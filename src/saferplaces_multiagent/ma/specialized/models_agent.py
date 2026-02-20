from typing import Any, Dict, List, Optional
import json

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langgraph.types import interrupt

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames
from .tools.safer_rain_tool import SaferRainTool


# ============================================================================
# Constants
# ============================================================================

# Agent registry description
MODELS_AGENT_DESCRIPTION = {
    "name": NodeNames.MODELS_AGENT,
    "description": (
        "Specialized agent that executes environmental models via APIs: flood (rain or storm-surge), "
        "fire propagation, structural impact analyses and similar scenarios. "
        "It exposes tools that run models and returns generated layers or reports for downstream processing."
    ),
    "examples": [
        "Run flood propagation for a heavy-rain scenario on a bbox",
        "Simulate fire spread given ignition points and wind conditions",
        "Estimate compromised structures after a flood event"
    ]
}

# Invocation confirmation states
INVOCATION_PENDING = "pending"
INVOCATION_ACCEPTED = "accepted"
INVOCATION_REJECTED = "rejected"

# State key constants
STATE_MODELS_INVOCATION = "models_invocation"
STATE_MODELS_CONFIRMATION = "models_invocation_confirmation"
STATE_MODELS_REINVOCATION_REQUEST = "models_reinvocation_request"
STATE_MODELS_CURRENT_STEP = "models_current_step"
STATE_TOOL_RESULTS = "tool_results"


# ============================================================================
# Prompts
# ============================================================================

class ModelsPrompts:
    """Prompts for specialized models/simulations agent."""

    TOOL_SELECTION_SYSTEM = (
        "You are a specialized simulations agent.\n"
        "Choose the best model/tool to accomplish the goal.\n"
        "Only call provided tools and propose reasonable args if missing.\n"
        "If a tool requires a layer input, select it from Relevant layers when available.\n"
        "If no suitable layer exists, do not invent one; state what layer is missing."
    )

    @staticmethod
    def initial_request(state: MABaseGraphState) -> str:
        """Generate initial tool invocation prompt."""
        goal = state["plan"][state["current_step"]].get("goal", "N/A")
        parsed_request = state.get("parsed_request", "")
        relevant_layers = (
            state.get("additional_context", {})
            .get("relevant_layers", {})
            .get("layers", [])
        )

        return (
            f"Goal: {goal}\n"
            f"Parsed: {parsed_request}\n"
            "\n"
            "Relevant layers (use these as inputs if needed):\n"
            f"{json.dumps(relevant_layers, ensure_ascii=False)}"
        )

    @staticmethod
    def reinvocation_request(state: MABaseGraphState) -> str:
        """Generate re-invocation prompt after user feedback."""
        goal = state["plan"][state["current_step"]].get("goal", "N/A")
        invocation = state[STATE_MODELS_INVOCATION]
        tool_calls_str = "\n".join(
            f"  - {tc['name']}: {tc['args']}"
            for tc in invocation.tool_calls
        )
        user_response = state[STATE_MODELS_REINVOCATION_REQUEST].content

        return (
            f"Goal: {goal}\n"
            "\n"
            "Some tools need to be reviewed or corrected.\n"
            "Here is the current invocation:\n"
            f"{tool_calls_str}\n"
            "\n"
            f"User feedback: {user_response}\n"
            "Produce a new sequence of tool calls based on the user's feedback. "
            "You can modify arguments, order, add, or delete tool calls."
        )


# ============================================================================
# Tool Registry
# ============================================================================

class ToolRegistry:
    """Singleton registry for managing models tools."""

    _instance: Optional["ToolRegistry"] = None
    _tools: Dict[str, Any] = {}

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_tools()
        return cls._instance

    def _initialize_tools(self) -> None:
        """Initialize all available models tools."""
        active_tools = [tool() for tool in [SaferRainTool]]
        self._tools = {tool.name: tool for tool in active_tools}

    @property
    def tools(self) -> Dict[str, Any]:
        """Get all registered tools."""
        return self._tools

    def get(self, tool_name: str) -> Any:
        """Get a specific tool by name."""
        return self._tools[tool_name]


# ============================================================================
# Models Agent
# ============================================================================

class ModelsAgent:
    """Agent that executes environmental models using tools backed by APIs."""

    def __init__(self) -> None:
        self.name = NodeNames.MODELS_AGENT
        self.tools = ToolRegistry().tools
        self.llm = _base_llm.bind_tools(list(self.tools.values()))

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute models agent."""
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main models execution logic."""
        print(f"[{self.name}] → Invoking tools...")

        # Build messages based on confirmation state
        messages = self._build_invocation_messages(state)

        # Invoke LLM with tools
        invocation = self.llm.invoke(messages)

        # Check if no tool calls were generated
        if self._has_no_tool_calls(invocation):
            return self._handle_no_tool_calls(invocation, state)

        # Store invocation and prepare for confirmation
        self._prepare_invocation(invocation, state)

        print(
            f"[{self.name}] → Tool calls: "
            f"[{len(invocation.tool_calls)}]: "
            f"{[call['name'] for call in invocation.tool_calls]}"
        )

        return state

    def _build_invocation_messages(self, state: MABaseGraphState) -> List[Any]:
        """Build messages for LLM invocation."""
        system_msg = SystemMessage(content=ModelsPrompts.TOOL_SELECTION_SYSTEM)

        # Choose prompt based on state
        if state.get(STATE_MODELS_CONFIRMATION) == INVOCATION_REJECTED:
            human_msg = HumanMessage(content=ModelsPrompts.reinvocation_request(state))
        else:
            human_msg = HumanMessage(content=ModelsPrompts.initial_request(state))

        return [system_msg, human_msg]

    @staticmethod
    def _has_no_tool_calls(invocation: AIMessage) -> bool:
        """Check if invocation has no tool calls."""
        return len(getattr(invocation, "tool_calls", []) or []) == 0

    @staticmethod
    def _handle_no_tool_calls(invocation: AIMessage, state: MABaseGraphState) -> MABaseGraphState:
        """Handle case where LLM didn't generate tool calls."""
        print("[ModelsAgent] ⚠ No tool calls generated")
        state["current_step"] += 1
        state[STATE_MODELS_INVOCATION] = invocation
        state[STATE_MODELS_CONFIRMATION] = None
        state["messages"] = invocation
        return state

    @staticmethod
    def _prepare_invocation(invocation: AIMessage, state: MABaseGraphState) -> None:
        """Prepare invocation state for confirmation step."""
        state[STATE_MODELS_INVOCATION] = invocation
        state[STATE_MODELS_CURRENT_STEP] = 0
        state[STATE_MODELS_CONFIRMATION] = INVOCATION_PENDING
        state[STATE_MODELS_REINVOCATION_REQUEST] = None


# ============================================================================
# Models Invocation Confirmation
# ============================================================================

class ModelsInvocationConfirm:
    """Confirmation and validation checkpoint for models tool invocations."""

    def __init__(self, enabled: bool = False) -> None:
        self.name = NodeNames.MODELS_INVOCATION_CONFIRM
        self.enabled = enabled

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute confirmation logic."""
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main confirmation workflow."""
        # Step 1: Validate tool calls
        validation_state = self._validate_tool_calls(state)
        if validation_state is not None:
            return validation_state

        # Step 2: Request user confirmation (if enabled)
        if self.enabled:
            return self._request_user_confirmation(state)

        # Auto-confirm if disabled
        state[STATE_MODELS_CONFIRMATION] = INVOCATION_ACCEPTED
        state[STATE_MODELS_REINVOCATION_REQUEST] = None
        return state

    def _validate_tool_calls(self, state: MABaseGraphState) -> Optional[MABaseGraphState]:
        """Validate all pending tool calls."""
        invocation = state[STATE_MODELS_INVOCATION]
        if ModelsAgent._has_no_tool_calls(invocation):
            return None
        current_step = state[STATE_MODELS_CURRENT_STEP]

        invalid_messages = []
        for tool_call in invocation.tool_calls[current_step:]:
            invalid_reason = self._validate_single_tool_call(tool_call, state)
            if invalid_reason is not None:
                invalid_messages.append(invalid_reason)

        if not invalid_messages:
            return None

        # Validation failed: request user intervention
        print(f"[{self.name}] ⚠ Validation failed")
        print(f"Invalid tool calls: {[m.content for m in invalid_messages]}")

        return self._handle_validation_failure(invalid_messages, state)

    @staticmethod
    def _validate_single_tool_call(
        tool_call: ToolCall, state: MABaseGraphState
    ) -> Optional[AIMessage]:
        """Validate a single tool call against its schema."""
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or {}
        tool = ToolRegistry().get(tool_name)

        # Get validation rules for this tool
        validation_rules = tool._set_args_validation_rules()

        # Check each argument
        invalid_args = {}
        for arg, rules in validation_rules.items():
            for rule in rules:
                invalid_reason = rule(**tool_args)
                if invalid_reason is not None:
                    invalid_args[arg] = invalid_reason
                    break

        if not invalid_args:
            return None

        # Format validation error message
        error_msg = f"Parameters for '{tool_name}' are invalid:\n"
        error_msg += "\n".join(f"  {arg}: {reason}" for arg, reason in invalid_args.items())

        return AIMessage(content=error_msg)

    @staticmethod
    def _handle_validation_failure(
        invalid_messages: List[AIMessage], state: MABaseGraphState
    ) -> MABaseGraphState:
        """Handle validation failure with user interrupt."""
        error_content = "\n".join(m.content for m in invalid_messages)

        interruption = interrupt({
            "content": f"Some tool calls need to be reviewed or corrected:\n{error_content}",
            "interrupt_type": "invocation-validation"
        })

        response = interruption.get("response", "User did not provide any response.")

        # Prepare for re-invocation
        state[STATE_MODELS_CURRENT_STEP] = 0
        state[STATE_MODELS_CONFIRMATION] = INVOCATION_REJECTED
        state[STATE_MODELS_REINVOCATION_REQUEST] = HumanMessage(content=response)

        return state

    def _request_user_confirmation(self, state: MABaseGraphState) -> MABaseGraphState:
        """Request user confirmation for tool invocation."""
        invocation = state.get(STATE_MODELS_INVOCATION)
        confirmation_state = state.get(STATE_MODELS_CONFIRMATION)

        if invocation is None or not invocation.tool_calls or confirmation_state != INVOCATION_PENDING:
            return state

        # Format tool calls for display
        tool_calls_display = "\n".join(
            f"  - {tc['name']}({tc.get('args')})"
            for tc in invocation.tool_calls
        )

        print(f"Do you want to proceed with these tool calls?\n{tool_calls_display}")

        interruption = interrupt({
            "content": f"Do you want to proceed with the tool calls?\n{tool_calls_display}",
            "interrupt_type": "invocation-confirmation"
        })

        response = interruption.get("response", "User did not provide any response.")

        if response == "ok":
            state[STATE_MODELS_CONFIRMATION] = INVOCATION_ACCEPTED
            state[STATE_MODELS_REINVOCATION_REQUEST] = None
        else:
            state[STATE_MODELS_CURRENT_STEP] = 0
            state[STATE_MODELS_CONFIRMATION] = INVOCATION_REJECTED
            state[STATE_MODELS_REINVOCATION_REQUEST] = HumanMessage(content=response)

        return state


# ============================================================================
# Models Executor
# ============================================================================

class ModelsExecutor:
    """Executor for models tool invocations."""

    def __init__(self) -> None:
        self.name = NodeNames.MODELS_EXECUTOR

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute tool calls."""
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main execution logic."""
        invocation = state[STATE_MODELS_INVOCATION]
        if ModelsAgent._has_no_tool_calls(invocation):
            state["current_step"] += 1
            state["messages"] = [invocation]
            return state

        current_step = state[STATE_MODELS_CURRENT_STEP]

        tool_responses = []

        # Execute each pending tool call
        for tool_call in invocation.tool_calls[current_step:]:
            print(f"[{self.name}] → Executing: {tool_call['name']}")

            tool_response = self._execute_tool_call(tool_call, state)
            tool_responses.append(tool_response)

            print(f"[{self.name}] ✓ Response: {tool_response.content[:100]}...")

        # Update state with results
        state["current_step"] += 1
        state["messages"] = [invocation, *tool_responses]

        print(f"[{self.name}] ✓ Execution complete")

        return state

    def _execute_tool_call(self, tool_call: ToolCall, state: MABaseGraphState) -> ToolMessage:
        """Execute a single tool call and return response."""
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or {}
        tool = ToolRegistry().get(tool_name)

        # Execute tool
        result = tool._execute(**tool_args)

        # Record result
        self._record_tool_result(tool_name, tool_args, result, state)

        # Format tool response message
        tool_response = ToolMessage(
            content=(
                f"Layer generated:\n"
                f"- Title: {tool_name.replace('_', ' ').title()} models simulation layer\n"
                f"- URI: s3://example-bucket/{tool_name}-out/{tool_args.get('variable', 'data')}.tif\n"
                f"- Parameters: {tool_args}"
            ),
            tool_call_id=tool_call["id"]
        )

        return tool_response

    @staticmethod
    def _record_tool_result(
        tool_name: str,
        tool_args: Dict[str, Any],
        result: Any,
        state: MABaseGraphState
    ) -> None:
        """Record tool execution result in state."""
        current_step = state["current_step"]
        step_key = f"step_{current_step}"

        if STATE_TOOL_RESULTS not in state:
            state[STATE_TOOL_RESULTS] = {}

        if step_key not in state[STATE_TOOL_RESULTS]:
            state[STATE_TOOL_RESULTS][step_key] = []

        state[STATE_TOOL_RESULTS][step_key].append({
            "tool": tool_name,
            "args": tool_args,
            "result": result
        })

        state[STATE_MODELS_CURRENT_STEP] += 1