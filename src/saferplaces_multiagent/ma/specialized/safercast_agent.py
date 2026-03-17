from typing import Any, Dict, List, Optional
import json

from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langgraph.types import interrupt

from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager
from ...common.utils import _base_llm
from .tools.dpc_retriever_tool import DPCRetrieverTool
from .tools.meteoblue_retriever_tool import MeteoblueRetrieverTool
from .layers_agent import LayersAgent
from .confirmation_utils import ToolInvocationConfirmationHandler
from .validation_utils import ToolValidationResponseHandler
from ..names import NodeNames
from ..prompts.safercast_agent_prompts import SaferCastPrompts


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
    ]
}


# Invocation confirmation states
INVOCATION_PENDING = "pending"
INVOCATION_ACCEPTED = "accepted"
INVOCATION_REJECTED = "rejected"

# State key constants
STATE_RETRIEVER_INVOCATION = "retriever_invocation"
STATE_RETRIEVER_CONFIRMATION = "retriever_invocation_confirmation"
STATE_RETRIEVER_REINVOCATION_REQUEST = "retriever_reinvocation_request"
STATE_RETRIEVER_CURRENT_STEP = "retriever_current_step"
STATE_TOOL_RESULTS = "tool_results"


# ============================================================================
# Tool Registry
# ============================================================================


class ToolRegistry:
    """Singleton registry for managing retriever tools."""

    _instance: Optional["ToolRegistry"] = None
    _tools: Dict[str, Any] = {}

    def __new__(cls) -> "ToolRegistry":
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

    def get(self, tool_name: str) -> Any:
        """Get a specific tool by name."""
        return self._tools[tool_name]


# ============================================================================
# Data Retriever Agent
# ============================================================================

class DataRetrieverAgent(MultiAgentNode):
    """Specialized agent for data retrieval and tool selection."""

    def __init__(self, name: str = NodeNames.RETRIEVER_AGENT, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.tools = ToolRegistry().tools
        self.llm = _base_llm.bind_tools(list(self.tools.values()))

    # def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
    #     """Execute data retrieval agent."""
    #     return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main retriever execution logic."""
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
        system_prompt = SaferCastPrompts.MainContext.stable()
        system_msg = system_prompt.to(SystemMessage)

        # Choose prompt based on state
        if state.get(STATE_RETRIEVER_CONFIRMATION) == INVOCATION_REJECTED:
            human_prompt = SaferCastPrompts.ToolSelection.ReinvocationRequest.stable(state)
        else:
            human_prompt = SaferCastPrompts.ToolSelection.InitialRequest.stable(state)

        human_msg = human_prompt.to(HumanMessage)

        return [system_msg, human_msg]

    @staticmethod
    def _has_no_tool_calls(invocation: AIMessage) -> bool:
        """Check if invocation has no tool calls."""
        return len(getattr(invocation, "tool_calls", []) or []) == 0

    @staticmethod
    def _handle_no_tool_calls(invocation: AIMessage, state: MABaseGraphState) -> MABaseGraphState:
        """Handle case where LLM didn't generate tool calls."""
        print("[DataRetrieverAgent] ⚠ No tool calls generated")
        state[STATE_RETRIEVER_INVOCATION] = invocation
        state[STATE_RETRIEVER_CONFIRMATION] = None
        state["messages"] = [invocation]
        return state

    @staticmethod
    def _prepare_invocation(invocation: AIMessage, state: MABaseGraphState) -> None:
        """Prepare invocation state for confirmation step."""
        state[STATE_RETRIEVER_INVOCATION] = invocation
        state[STATE_RETRIEVER_CURRENT_STEP] = 0
        state[STATE_RETRIEVER_CONFIRMATION] = INVOCATION_PENDING
        state[STATE_RETRIEVER_REINVOCATION_REQUEST] = None


# ============================================================================
# Data Retriever Invocation Confirmation
# ============================================================================

class DataRetrieverInvocationConfirm(MultiAgentNode):
    """Confirmation and validation checkpoint for tool invocations."""

    def __init__(self, name: str = NodeNames.RETRIEVER_INVOCATION_CONFIRM, enabled: bool = False, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.enabled = enabled
        self.confirmation_handler = ToolInvocationConfirmationHandler()
        self.validation_handler = ToolValidationResponseHandler()

    # def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
    #     """Execute confirmation logic."""
    #     return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main confirmation workflow."""
        invocation = state[STATE_RETRIEVER_INVOCATION]
        if DataRetrieverAgent._has_no_tool_calls(invocation):
            return state
        
        current_step = state[STATE_RETRIEVER_CURRENT_STEP]
        pending_tool_calls = invocation.tool_calls[current_step:]

        # Step 1: VALIDATE tool calls (inference + validation)
        validation_errors = self._validate_tool_calls(pending_tool_calls, state)
        if validation_errors:
            return self._handle_validation_failure(validation_errors, state)

        # Step 2: REQUEST user confirmation (if enabled)
        if self.enabled:
            return self._request_user_confirmation(state)

        # Auto-confirm if disabled
        state[STATE_RETRIEVER_CONFIRMATION] = INVOCATION_ACCEPTED
        state[STATE_RETRIEVER_REINVOCATION_REQUEST] = None
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
        print(f"[{self.name}] ⚠ Validation failed")
        print(f"Errors: {validation_errors}")

        # Generate clear, user-friendly error message using LLM
        error_message = self._generate_validation_error_message(validation_errors)

        # Request user intervention via interrupt
        interruption = interrupt({
            "content": error_message,
            "interrupt_type": "invocation-validation"
        })

        response = interruption.get("response", "User did not provide any response.")

        # Use shared validation handler to classify and process validation response
        user_validation_state = self.validation_handler.process_validation_response(
            state=state,
            user_response=response,
            validation_errors=validation_errors,
            confirmation_key=STATE_RETRIEVER_CONFIRMATION,
            reinvocation_key=STATE_RETRIEVER_REINVOCATION_REQUEST,
            invocation_key=STATE_RETRIEVER_INVOCATION,
            current_step_key=STATE_RETRIEVER_CURRENT_STEP,
            max_clarify_iterations=3
        )
        return user_validation_state

    def _generate_validation_error_message(self, validation_errors: Dict[str, Dict[str, str]]) -> str:
        """Generate a clear, schematic error message for validation failures using LLM."""
        # Format validation errors as a readable list
        errors_text = self._format_validation_errors_for_display(validation_errors)
        
        error_prompt = (
            f"Generate a clear, concise message to inform the user about the following parameter validation failures.\n"
            f"The message should be:\n"
            f"- Schematic and organized (use bullet points or numbering)\n"
            f"- Concise but complete, explaining what's wrong with each parameter\n"
            f"- End with a clear invitation to revise and correct the parameters\n"
            f"\n"
            f"Validation errors:\n{errors_text}\n"
            f"\n"
            f"Generate the error message (be brief and well-formatted):"
        )
        
        messages = [
            SystemMessage(content="You are a helpful assistant that communicates parameter validation errors clearly and concisely."),
            HumanMessage(content=error_prompt)
        ]
        
        try:
            response = _base_llm.invoke(messages)
            return response.content.strip()
        except Exception as e:
            print(f"[{self.name}] ⚠ Error message generation failed: {e}")
            # Fallback to formatted errors
            return f"Some parameters are invalid and need to be corrected:\n{errors_text}"

    @staticmethod
    def _format_validation_errors_for_display(validation_errors: Dict[str, Dict[str, str]]) -> str:
        """Format validation errors into a readable string."""
        formatted_errors = []
        for tool_name, tool_errors in validation_errors.items():
            formatted_errors.append(f"**{tool_name}:**")
            for arg, reason in tool_errors.items():
                formatted_errors.append(f"  - {arg}: {reason}")
        return "\n".join(formatted_errors)

    def _generate_tool_confirmation_message(self, tool_calls: List[ToolCall]) -> str:
        """Generate a clear, schematic confirmation message for tool calls using LLM."""
        # Format tool calls as a readable list
        tool_calls_text = self._format_tool_calls_for_display(tool_calls)
        
        confirmation_prompt = (
            f"Generate a clear, concise confirmation message for the user about executing the following tools.\n"
            f"The message should be:\n"
            f"- Schematic and organized (use bullet points or numbering)\n"
            f"- Concise but complete\n"
            f"- End with a clear question asking if they want to proceed\n"
            f"\n"
            f"Tools to execute:\n{tool_calls_text}\n"
            f"\n"
            f"Generate the confirmation message (be brief and well-formatted):"
        )
        
        messages = [
            SystemMessage(content="You are a helpful assistant that communicates tool invocations clearly and concisely."),
            HumanMessage(content=confirmation_prompt)
        ]
        
        try:
            response = _base_llm.invoke(messages)
            return response.content.strip()
        except Exception as e:
            print(f"[{self.name}] ⚠ Message generation error: {e}")
            # Fallback to formatted tool calls
            return f"Do you want to proceed with the following tool calls?\n{tool_calls_text}"

    @staticmethod
    def _format_tool_calls_for_display(tool_calls: List[ToolCall]) -> str:
        """Format tool calls into a readable string."""
        formatted_calls = []
        for i, tc in enumerate(tool_calls, 1):
            tool_name = tc.get("name", "Unknown")
            tool_args = tc.get("args", {})
            formatted_calls.append(f"{i}. {tool_name}({json.dumps(tool_args)})")
        return "\n".join(formatted_calls)

    def _request_user_confirmation(self, state: MABaseGraphState) -> MABaseGraphState:
        """Request user confirmation for tool invocation."""
        invocation = state.get(STATE_RETRIEVER_INVOCATION)
        confirmation_state = state.get(STATE_RETRIEVER_CONFIRMATION)

        if invocation is None or not invocation.tool_calls or confirmation_state != INVOCATION_PENDING:
            return state

        # Generate clear confirmation message using LLM
        confirmation_message = self._generate_tool_confirmation_message(invocation.tool_calls)
        print(f"Requesting confirmation...\n{confirmation_message}")

        interruption = interrupt({
            "content": confirmation_message,
            "interrupt_type": "invocation-confirmation"
        })

        response = interruption.get("response", "User did not provide any response.")

        # if response == "ok":
        #     state[STATE_RETRIEVER_CONFIRMATION] = INVOCATION_ACCEPTED
        #     state[STATE_RETRIEVER_REINVOCATION_REQUEST] = None
        # else:
        #     state[STATE_RETRIEVER_CURRENT_STEP] = 0
        #     state[STATE_RETRIEVER_CONFIRMATION] = INVOCATION_REJECTED
        #     state[STATE_RETRIEVER_REINVOCATION_REQUEST] = HumanMessage(content=response)
        
        # Use shared confirmation handler to classify and process response
        user_confirmation_state = self.confirmation_handler.process_confirmation(
            state=state,
            user_response=response,
            confirmation_key=STATE_RETRIEVER_CONFIRMATION,
            reinvocation_key=STATE_RETRIEVER_REINVOCATION_REQUEST,
            invocation_key=STATE_RETRIEVER_INVOCATION,
            max_clarify_iterations=3
        )
        return user_confirmation_state


# ============================================================================
# Data Retriever Executor
# ============================================================================

class DataRetrieverExecutor(MultiAgentNode):
    """Executor for retriever tool invocations."""

    def __init__(self, name: str = NodeNames.RETRIEVER_EXECUTOR, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.layers_agent = LayersAgent()

    # def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
    #     """Execute tool calls."""
    #     return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main execution logic."""
        invocation = state[STATE_RETRIEVER_INVOCATION]
        if DataRetrieverAgent._has_no_tool_calls(invocation):
            state["current_step"] += 1
            state["messages"] = [invocation]
            return state

        current_step = state[STATE_RETRIEVER_CURRENT_STEP]
        pending_tool_calls = invocation.tool_calls[current_step:]

        tool_responses = []

        # Execute each pending tool call
        for tool_call in pending_tool_calls:
            print(f"[{self.name}] → Executing: {tool_call['name']}")

            tool_response = self._execute_tool_call(tool_call, state)
            tool_responses.append(tool_response)
            
            # Mark step as complete
            StateManager.mark_agent_step_complete(state, "retriever")

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
        try:
            result = tool._execute(**tool_args)
        except Exception as exc:
            error_msg = f"Tool '{tool_name}' raised an exception: {exc}"
            print(f"[{self.name}] ⚠ {error_msg}")
            self._record_tool_error(tool_name, tool_args, str(exc), state)
            return ToolMessage(content=error_msg, tool_call_id=tool_call["id"])

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
        if tool_name == "dpc_retriever":
            content = self._format_dpc_response(tool_args, result)
        elif tool_name == "meteoblue_retriever":
            content = self._format_meteoblue_response(tool_args, result)
        else:
            # Generic fallback
            content = self._format_generic_response(tool_name, tool_args, result)

        return ToolMessage(content=content, tool_call_id=tool_call["id"])

    @staticmethod
    def _format_dpc_response(tool_args: Dict[str, Any], result: Any) -> str:
        """Format DPC retriever specific response."""
        product = tool_args.get('product', 'unknown')
        uri = result.get('tool_output', {}).get('data', {}).get('uri', 'N/A')
        
        return (
            f"✓ DPC data retrieved successfully\n"
            f"Product: {product}\n"
            f"URI: {uri}\n"
            f"Description: Italian Civil Protection radar/observational data"
        )

    @staticmethod
    def _format_meteoblue_response(tool_args: Dict[str, Any], result: Any) -> str:
        """Format Meteoblue retriever specific response."""
        variable = tool_args.get('variable', 'unknown')
        data_info = result.get('tool_output', {}).get('data', {}).get('collected_data_info', [])
        
        uris = [info.get('ref', 'N/A') for info in data_info] if data_info else ['N/A']
        
        return (
            f"✓ Meteoblue forecast retrieved successfully\n"
            f"Variable: {variable}\n"
            f"URIs: {', '.join(uris)}\n"
            f"Description: Weather forecast data from Meteoblue"
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

    @staticmethod
    def _record_tool_error(
        tool_name: str,
        tool_args: Dict[str, Any],
        error_message: str,
        state: MABaseGraphState
    ) -> None:
        """Record a tool execution failure in state."""
        current_step = state["current_step"]
        step_key = f"step_{current_step}"

        if STATE_TOOL_RESULTS not in state:
            state[STATE_TOOL_RESULTS] = {}

        if step_key not in state[STATE_TOOL_RESULTS]:
            state[STATE_TOOL_RESULTS][step_key] = []

        state[STATE_TOOL_RESULTS][step_key].append({
            "tool": tool_name,
            "args": tool_args,
            "status": "error",
            "message": error_message,
        })