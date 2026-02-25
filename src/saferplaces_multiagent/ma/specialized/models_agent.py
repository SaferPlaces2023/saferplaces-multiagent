from typing import Any, Dict, List, Optional
import json

from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langgraph.types import interrupt

from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from ..names import NodeNames
from .tools.safer_rain_tool import SaferRainTool
from .layers_agent import LayersAgent
from .confirmation_utils import ToolInvocationConfirmationHandler
from .validation_utils import ToolValidationResponseHandler


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
        self.confirmation_handler = ToolInvocationConfirmationHandler()
        self.validation_handler = ToolValidationResponseHandler()

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        """Execute confirmation logic."""
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main confirmation workflow."""
        invocation = state[STATE_MODELS_INVOCATION]
        if ModelsAgent._has_no_tool_calls(invocation):
            return state
        
        current_step = state[STATE_MODELS_CURRENT_STEP]
        pending_tool_calls = invocation.tool_calls[current_step:]

        # Step 1: VALIDATE tool calls (inference + validation)
        validation_errors = self._validate_tool_calls(pending_tool_calls, state)
        if validation_errors:
            return self._handle_validation_failure(validation_errors, state)

        # Step 2: REQUEST user confirmation (if enabled)
        if self.enabled:
            return self._request_user_confirmation(state)

        # Auto-confirm if disabled
        state[STATE_MODELS_CONFIRMATION] = INVOCATION_ACCEPTED
        state[STATE_MODELS_REINVOCATION_REQUEST] = None
        return state

    def _validate_tool_calls(self, tool_calls: List[ToolCall], state: MABaseGraphState) -> Dict[str, Dict[str, str]]:
        """Validate all tool calls (inference + validation)."""
        all_errors = {}
        
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args") or {}
            tool = ToolRegistry().get(tool_name)
            
            # Apply inference first and update tool_call args
            tool_call["args"] = self._apply_inference_to_args(tool, tool_args, state)
            
            # Validate arguments (now complete)
            validation_errors = self._validate_args(tool, tool_call["args"])
            if validation_errors:
                all_errors[tool_name] = validation_errors
        
        return all_errors

    @staticmethod
    def _apply_inference_to_args(tool: Any, tool_args: Dict[str, Any], graph_state: MABaseGraphState) -> Dict[str, Any]:
        """Apply inference rules to get complete arguments."""
        inference_rules = tool._set_args_inference_rules()
        
        # Add graph state to kwargs so inferrer functions can access it
        tool_args_with_state = {**tool_args, '_graph_state': graph_state}
        
        for arg_name, inferrer_fn in inference_rules.items():
            if arg_name not in tool_args or tool_args[arg_name] is None:
                tool_args[arg_name] = inferrer_fn(**tool_args_with_state)
        
        return tool_args

    @staticmethod
    def _validate_args(tool: Any, tool_args: Dict[str, Any]) -> Dict[str, str]:
        """Validate all arguments against rules."""
        validation_rules = tool._set_args_validation_rules()
        errors = {}
        
        for arg_name, validators_list in validation_rules.items():
            for validator_fn in validators_list:
                error = validator_fn(**tool_args)
                if error:
                    errors[arg_name] = error
                    break
        
        return errors

    def _handle_validation_failure(
        self,
        validation_errors: Dict[str, Dict[str, str]],
        state: MABaseGraphState
    ) -> MABaseGraphState:
        """Handle validation failure with user interrupt."""
        error_content = "Parameters validation failed:\n\n"
        for tool_name, tool_errors in validation_errors.items():
            error_content += f"  {tool_name}:\n"
            for arg, reason in tool_errors.items():
                error_content += f"    - {arg}: {reason}\n"
        
        print(f"[{self.name}] ⚠ Validation failed")
        print(f"Errors: {validation_errors}")

        # Request user intervention via interrupt
        interruption = interrupt({
            "content": f"Some tool calls need to be reviewed or corrected:\n{error_content}",
            "interrupt_type": "invocation-validation"
        })

        response = interruption.get("response", "User did not provide any response.")

        # Use shared validation handler to classify and process validation response
        user_validation_state = self.validation_handler.process_validation_response(
            state=state,
            user_response=response,
            validation_errors=validation_errors,
            confirmation_key=STATE_MODELS_CONFIRMATION,
            reinvocation_key=STATE_MODELS_REINVOCATION_REQUEST,
            invocation_key=STATE_MODELS_INVOCATION,
            current_step_key=STATE_MODELS_CURRENT_STEP,
            max_clarify_iterations=3
        )
        return user_validation_state

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

        # if response == "ok":
        #     state[STATE_MODELS_CONFIRMATION] = INVOCATION_ACCEPTED
        #     state[STATE_MODELS_REINVOCATION_REQUEST] = None
        # else:
        #     state[STATE_MODELS_CURRENT_STEP] = 0
        #     state[STATE_MODELS_CONFIRMATION] = INVOCATION_REJECTED
        #     state[STATE_MODELS_REINVOCATION_REQUEST] = HumanMessage(content=response)
        # Use shared confirmation handler to classify and process response
        return self.confirmation_handler.process_confirmation(
            state=state,
            user_response=response,
            confirmation_key=STATE_MODELS_CONFIRMATION,
            reinvocation_key=STATE_MODELS_REINVOCATION_REQUEST,
            invocation_key=STATE_MODELS_INVOCATION,
            max_clarify_iterations=3
        )


# ============================================================================
# Models Executor
# ============================================================================

class ModelsExecutor:
    """Executor for models tool invocations."""

    def __init__(self) -> None:
        self.name = NodeNames.MODELS_EXECUTOR
        self.layers_agent = LayersAgent()

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
            
            # Mark step as complete
            StateManager.mark_agent_step_complete(state, "models")

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

        # Execute tool (arguments already complete and validated from Confirm step)
        result = tool._execute(**tool_args)

        # Format tool response message (tool-specific)
        tool_response = self._format_tool_response(tool_call, tool_name, tool_args, result)

        # Add created layer to registry
        self._add_layer_to_registry(tool_name, tool_args, result, state)

        # Record result (common logic)
        self._record_tool_result(tool_name, tool_args, result, state)

        return tool_response

    def _format_tool_response(
        self,
        tool_call: ToolCall,
        tool_name: str,
        tool_args: Dict[str, Any],
        result: Any
    ) -> ToolMessage:
        """Format tool response message with tool-specific logic."""
        
        # Tool-specific formatting
        if tool_name == "safer_rain":
            content = self._format_safer_rain_response(tool_args, result)
        else:
            # Generic fallback
            content = self._format_generic_response(tool_name, tool_args, result)

        return ToolMessage(content=content, tool_call_id=tool_call["id"])

    @staticmethod
    def _format_safer_rain_response(tool_args: Dict[str, Any], result: Any) -> str:
        """Format SaferRain specific response."""
        dem = tool_args.get('dem', 'unknown')
        rain = tool_args.get('rain', 'unknown')
        uri = result.get('tool_output', {}).get('data', {}).get('uri', 'N/A')
        
        return (
            f"✓ Flood simulation completed successfully\n"
            f"Model: SaferRain\n"
            f"DEM: {dem}\n"
            f"Rainfall: {rain}\n"
            f"Output URI: {uri}\n"
            f"Description: Water depth raster from flood propagation simulation"
        )

    @staticmethod
    def _format_generic_response(tool_name: str, tool_args: Dict[str, Any], result: Any) -> str:
        """Format generic tool response."""
        return (
            f"✓ Tool '{tool_name}' executed successfully\n"
            f"Arguments: {json.dumps(tool_args, indent=2)}\n"
            f"Result: {json.dumps(result, indent=2)}"
        )

    def _add_layer_to_registry(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        result: Any,
        state: MABaseGraphState
    ) -> None:
        """Add created layer to layer registry via layers agent."""
        # Check if result was successful
        if result.get('status') != 'success':
            return

        # Build request for layers agent with all context
        state["layers_request"] = (
            f"Add a new layer from the following tool execution:\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {json.dumps(tool_args, indent=2)}\n"
            f"Result: {json.dumps(result, indent=2)}\n\n"
            f"Extract the layer URI from the result and create a descriptive title "
            f"and description based on the tool name and arguments."
        )

        # Execute layers agent
        print(f"[{self.name}] → Adding layer to registry...")
        layer_agent_state = self.layers_agent(state)
        
        # Update state with new layer registry
        state["layer_registry"] = layer_agent_state.get("layer_registry", state.get("layer_registry", []))

        # Mark additional_context as dirty since registry changed
        if "additional_context" not in state:
            state["additional_context"] = {}
        if "relevant_layers" not in state["additional_context"]:
            state["additional_context"]["relevant_layers"] = {}
        
        state["additional_context"]["relevant_layers"]["is_dirty"] = True

        print(f"[{self.name}] ✓ Layer added to registry")

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