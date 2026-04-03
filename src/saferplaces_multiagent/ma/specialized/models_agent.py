from pyexpat.errors import messages
from typing import Any, Dict, List, Optional
import json
import datetime

# from pydantic import BaseModel, Field
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage, ToolCall
from langchain_core.tools import BaseTool
from langgraph.types import interrupt

from saferplaces_multiagent.common.response_classifier import ResponseClassifier
from saferplaces_multiagent.multiagent_node import MultiAgentNode

from ...common.states import MABaseGraphState, StateManager, build_nowtime_system_message
from ...common.utils import _base_llm
from ...common.templates import format_tool_confirmation, format_validation_errors
# from ...common.execution_narrative import StepResult, LayerSummary
from ...common import names as N
from ..names import NodeNames
from .tools.safer_rain_tool import SaferRainTool
from .tools.digital_twin_tool import DigitalTwinTool
from .tools.safer_buildings_tool import SaferBuildingsTool
from .tools.safer_fire_tool import SaferFireTool
from .layers_agent import LayersAgent
# from .confirmation_utils import ToolInvocationConfirmationHandler
# from .validation_utils import ToolValidationResponseHandler
from ..prompts.models_agent_prompts import ModelsInstructions


# ============================================================================
# Constants
# ============================================================================

# Agent registry description
MODELS_AGENT_TOOLS = [
    DigitalTwinTool,
    SaferRainTool,
    SaferBuildingsTool,
    SaferFireTool,
]
MODELS_AGENT_DESCRIPTION = {
    "name": NodeNames.MODELS_AGENT,
    "description": (
        "Specialized agent that executes environmental models and geospatial analyses via APIs: "
        "flood propagation simulations, digital twin generation, and similar spatial scenarios. "
        "It creates base geospatial layers for new areas and runs model simulations, "
        "returning output rasters or reports for downstream processing.\n"
        "\n"
        "Available tools:\n"
        + "\n".join(
            f"  • {cls.__name__}: {cls.short_description}"
            for cls in MODELS_AGENT_TOOLS
            if hasattr(cls, "short_description")
        )
    ),
    "examples": [
        "Run flood propagation for a 50mm rainfall scenario on a bounding box",
        "Create a minimal Digital Twin (DEM only) for a new area of interest",
        "Generate full Digital Twin with elevation, hydrology, buildings and land-use layers for an AOI",
        "Simulate flood extent and water depth using a multiband radar rainfall raster and an existing DEM",
        "Identify flooded buildings from the latest flood simulation water depth raster",
        "Show which buildings in Rimini are flooded above 50 cm water depth",
        "Simulate wildfire propagation from ignition points with southerly wind at 8 m/s",
        "Run fire spread simulation for 6 hours using DEM and ESA land use",
    ],
    "outputs": [
        "Up to 25 spatially-aligned raster/vector layers across 5 categories (from DigitalTwinTool)",
        "  - Elevation: DEM, valley depth, TRI, TPI",
        "  - Hydrology: slope, HAND, TWI, flow dir/accum, streams, river distance, river network",
        "  - Constructions: buildings (vector), roads (vector), DEM with buildings, filled DEM with buildings",
        "  - Land Cover: land-use, Manning roughness, NDVI, NDWI, NDBI, sea mask",
        "  - Soil: sand, clay",
        "Water depth raster — flood simulation output (from SaferRainTool)",
        "Flooded buildings vector layer — per-building flood status with optional stats (from SaferBuildingsTool)",
        "Fire spread rasters — burned area and fire arrival time at multiple time steps (from SaferFireTool)",
    ],
    "prerequisites": {
        "DigitalTwinTool": (
            "None — only requires a bounding box (AOI). "
            "Use as the FIRST step when no DEM or base layers exist. "
            "Default layers for generic requests (new project, digital twin, DEM only): layers=['dem']. "
            "Only specify additional layers if the user explicitly requests them."
        ),
        "SaferRainTool": (
            "Requires a DEM/DTM raster. "
            "If no DEM is available in the context layers, add a DigitalTwinTool step (via models_subgraph) BEFORE this step."
        ),
        "SaferBuildingsTool": (
            "Requires a water depth raster from a prior flood simulation. "
            "If no water depth layer is available in context, add a SaferRainTool step BEFORE this step. "
            "Building geometries can be fetched automatically via provider (default: OVERTURE) if not already available."
        ),
        "SaferFireTool": (
            "Requires a DEM/DTM raster and ignition sources (vector file or layer reference). "
            "Also requires wind_speed (m/s) and wind_direction (meteorological degrees). "
            "If no DEM is available, add a DigitalTwinTool step BEFORE this step."
        ),
    },
    "implicit_step_rules": [
        (
            "IMPLICIT STEP: if the user asks for a flood simulation and no DEM layer is present "
            "in the available context layers, prepend a models_subgraph step to create the Digital Twin first."
        ),
        (
            "IMPLICIT STEP: if the user asks for a flood simulation using a rainfall raster (not a constant value) "
            "and no rainfall raster layer exists in context, consider prepending a retriever_subgraph step to retrieve it."
        ),
        (
            "IMPLICIT STEP: if the user asks for flooded buildings analysis and no water depth layer is present "
            "in the available context layers, prepend a models_subgraph step to run a flood simulation first."
        ),
        (
            "IMPLICIT STEP: if the user asks for wildfire simulation and no DEM layer is present "
            "in the available context layers, prepend a models_subgraph step to create the Digital Twin first."
        ),
    ],
}




INVOCATION_PENDING = "pending"
INVOCATION_ACCEPTED = "accepted"
INVOCATION_REJECTED = "rejected"
INVOCATION_ABORT = "abort"

# State key constants
STATE_MODELS_INVOCATION = "models_invocation"
STATE_MODELS_CONFIRMATION = "models_invocation_confirmation"
STATE_MODELS_REINVOCATION_REQUEST = "models_reinvocation_request"
STATE_MODELS_CURRENT_STEP = "models_current_step"
STATE_TOOL_RESULTS = "tool_results"


# ============================================================================
# Tool Registry
# ============================================================================


class ModelsAgentTools:
    """Singleton registry for managing models tools."""

    _instance: Optional["ModelsAgentTools"] = None
    _tools: Dict[str, Any] = {}

    def __new__(cls) -> "ModelsAgentTools":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_tools()
        return cls._instance

    def _initialize_tools(self) -> None:
        """Initialize all available models tools."""
        active_tools = [tool() for tool in MODELS_AGENT_TOOLS]
        self._tools = {tool.name: tool for tool in active_tools}

    @property
    def tools(self) -> Dict[str, Any]:
        """Get all registered tools."""
        return self._tools

    @property
    def tools_instances(self) -> list[BaseTool]:
        """Get all tool instances."""
        return list(self._tools.values())

    def get(self, tool_name: str, graph_state: MABaseGraphState = None) -> Any:
        """Get a specific tool by name."""
        tool = self._tools[tool_name]
        if graph_state is not None:
            tool._set_graph_state(graph_state)
        return tool


# ============================================================================
# Models Agent
# ============================================================================

class ModelsAgent(MultiAgentNode):
    """Agent that executes environmental models using tools backed by APIs."""

    def __init__(self, name: str = NodeNames.MODELS_AGENT, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.tools_registry = ModelsAgentTools()
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
        """Main models execution logic."""

        # DOC: Switch case (from which situation i'm coming)
        invocation_reason = state.get("models_invocation_reason", "new_invocation")
        state['models_invocation_reason'] = None

        print(f"[{self.name}] → Invocation reason: {invocation_reason}")

        # DOC: From Supervisor plan step
        if invocation_reason == "new_invocation":
            tool_invocation = ModelsInstructions.InvokeTools.Invocation.InvokeOneShot.stable(state)
            invocation: AIMessage = self.llm.invoke(tool_invocation)
            if ModelsAgent._has_tool_calls(invocation):
                state['models_invocation'] = invocation
                state['models_current_step'] = 0
                state['models_invocation_confirmation'] = INVOCATION_PENDING
        
        # DOC: From InvocationConfirm [INVALID] (correct)
        elif invocation_reason == "invocation_provide_corrections":
            correct_tool_invocation = ModelsInstructions.CorrectToolsInvocation.Invocation.ReInvokeOneShot.stable(state)
            invocation: AIMessage = self.llm.invoke(correct_tool_invocation)
            if ModelsAgent._has_tool_calls(invocation):
                state['models_invocation'] = invocation
                state['models_current_step'] = 0
                state['models_invocation_confirmation'] = INVOCATION_PENDING

        # DOC: From InvocationConfirm [INVALID] (auto correct)
        elif invocation_reason == "invocation_auto_correct":
            auto_correct_tool_invocation = ModelsInstructions.AutoCorrectToolsInvocation.Invocation.AutoReInvokeOneShot.stable(state)
            invocation: AIMessage = self.llm.invoke(auto_correct_tool_invocation)
            if ModelsAgent._has_tool_calls(invocation):
                state['models_invocation'] = invocation
                state['models_current_step'] = 0
                state['models_invocation_confirmation'] = INVOCATION_PENDING
        
        return state



# ============================================================================
# Models Invocation Confirmation
# ============================================================================

class ModelsInvocationConfirm(MultiAgentNode):
    """Confirmation and validation checkpoint for models tool invocations."""

    def __init__(self, name: str = NodeNames.MODELS_INVOCATION_CONFIRM, enabled: bool = False, log_state: bool = True) -> None:
        super().__init__(name, log_state)
        self.enabled = enabled
        self.llm = _base_llm
        self._classifier = ResponseClassifier(_base_llm)


    def _validate_invocation(self, tool_call: ToolCall, state: MABaseGraphState) -> Dict[str, Dict[str, str]]:
        """Validate a single tool call (inference + validation)."""
        all_errors = []
        
        tool_name = tool_call["name"]
        tool_args = tool_call.get("args") or {}
        tool = ModelsAgentTools().get(tool_name, state)
        
        tool_call["args"] = ModelsInvocationConfirm._apply_inference_to_args(tool, tool_args, state)
        
        invocation_errors = ModelsInvocationConfirm._validate_args(tool, tool_call["args"])
        if invocation_errors:
            all_errors = [
                {
                    'tool_name': tool_name,
                    'error_args': invocation_errors
                }
            ]
        
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
        
        errors = dict()

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
        """Handle provide corrections: incremental replanning."""
        state["models_invocation_confirmation"] = INVOCATION_PENDING
        state["models_invocation_reason"] = "invocation_provide_corrections"
        state["models_reinvocation_count"] = (state.get("models_reinvocation_count") or 0) + 1
        return state
    
    @staticmethod
    def _handle_auto_correct(state: MABaseGraphState) -> MABaseGraphState:
        """Handle auto-correct: incremental replanning."""
        state["models_invocation_confirmation"] = INVOCATION_PENDING
        state["models_invocation_reason"] = "invocation_auto_correct"
        state["models_reinvocation_count"] = (state.get("models_reinvocation_count") or 0) + 1
        return state
    
    @staticmethod
    def _handle_abort(state: MABaseGraphState) -> MABaseGraphState:
        """Handle abort: incremental replanning."""
        state["models_invocation_confirmation"] = INVOCATION_ABORT
        return state


    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main confirmation workflow."""

        # DOC: Get current invocation
        invocation = state.get('models_invocation')

        print(f"[{self.name}] → Current invocation: {invocation}")

        # DOC: If no tools has been called
        if not invocation or ModelsAgent._has_no_tool_calls(invocation):
            return state
        
        # DOC: Get current tool calls
        tool_call = ModelsAgent._get_tool_calls(invocation)[0]
        
        # DOC: Validate tool calls
        invocation_errors = self._validate_invocation(tool_call, state)

        # DOC: Interrupt for invalid tool call
        if invocation_errors:

            print(f"[{self.name}] → Invocation errors: {invocation_errors}")

            # DOC: Record validation errors in state
            state['models_invocation_errors'] = invocation_errors

            # DOC: Prepare interrupt
            invalid_invocation_message = self.llm.invoke(ModelsInstructions.InvalidInvocationInterrupt.LLMInvalidInvocationInterrupt.Invocation.NotifyOneShot.stable(state)).content
            # invalid_invocation_message = ModelsInstructions.InvalidInvocationInterrupt.StaticMessage.stable(state).message
            interruption = interrupt({
                "content": invalid_invocation_message,
                "interrupt_type": "invalid-invocation",
            })

            # DOC: Solve interrupt for user response
            user_response = interruption.get("response", "User did not provide any response.")

            # DOC: Record user response in conversation history so all downstream LLMs can see it
            state["messages"] = [
                SystemMessage(content=invalid_invocation_message),
                HumanMessage(content=user_response)
            ]

            # DOC: Classify user intent
            intent = self._classifier.classify_validation_response(user_response)
            
            return self._handle_intent(state, intent)
        
        else:

            # DOC: No validation errors, accept invocation [CONFIRMATION NOT IMPLEMENTED — need specific node per fare le cose bene]
            state['models_invocation_errors'] = None
            state['models_invocation_confirmation'] = INVOCATION_ACCEPTED

            return state


        # TODO: Confirmation enabled
        if self.enabled:
            raise NotImplementedError("Invocation confirmation not implemented yet (need specific node per fare le cose bene).")


# ============================================================================
# Models Executor
# ============================================================================

class ModelsExecutor(MultiAgentNode):
    """Executor for models tool invocations."""

    def __init__(
        self,
        name: str = NodeNames.MODELS_EXECUTOR,
        log_state: bool = True
    ):
        super().__init__(name, log_state)
        self.layers_agent = LayersAgent()

    
    def _execute_tool_call(self, tool_name:str, tool_args: Dict[str, Any], state: MABaseGraphState) -> ToolMessage:
        """Execute a single tool call and return response."""
        
        tool = ModelsAgentTools().get(tool_name, state)        
        try:
            tool_result = tool._execute(**tool_args)
        except Exception as exc:
            tool_result = dict(status = "error", message = str(exc))
        return tool_result

    
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
            f"and description based on the tool name and arguments.\n"
            f"\n"
            f"[METADATA HANDLING]\n"
            f"- Metadata may or may not be available\n"
            f"- If metadata are present, output them verbatim and in full\n"
            f"- Preserve exact content and structure\n"
            f"- Do not modify, paraphrase, summarize, truncate, or reorder\n"
            f"- Do not infer or add missing metadata\n"
            f"- If metadata are absent, do not generate any"
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
            

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        """Main execution logic."""

        # DOC: get current invocation
        invocation = state.get('models_invocation')

        # DOC: Empty invocation
        if not ModelsAgent._has_tool_calls(invocation):
            state['messages'] = [ invocation ]
            state['models_invocation'] = None
            state['models_current_step'] = None
            state['models_invocation_confirmation'] = None
            state['models_invocation_reason'] = None
            state['supervisor_invocation_reason'] = "step_no_tools"
            return state

        # DOC: get invocation confirmation status
        invocation_confirmation = state.get('models_invocation_confirmation')
        
        # DOC: Aborted invocation
        if invocation_confirmation == INVOCATION_ABORT:
            state['models_invocation'] = None
            state['models_current_step'] = None
            state['models_invocation_confirmation'] = None
            state['models_invocation_reason'] = None
            state['supervisor_invocation_reason'] = "step_skip"
            return state
        
        # DOC: get current step — mandatory valued if invocation exists
        invocation_current_step = state['models_current_step']

        tool_responses = [] 
        step_error = False

        for tool_call in invocation.tool_calls[invocation_current_step:]:

            tool_call_id = tool_call["id"]
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args") or {}

            print(f"[{self.name}] → Executing: {tool_name}")

            tool_result = self._execute_tool_call(tool_name, tool_args, state)

            tool_response = ToolMessage(
                content = json.dumps(tool_result, indent=2),
                tool_call_id = tool_call_id,
                name = tool_name
            )

            tool_responses.append(tool_response)

            if tool_result.get("status") == "error":
                step_error = True
                break
            
            self._add_layer_to_registry(
                tool_name=tool_name,
                tool_args=tool_args,
                result=tool_result,
                state=state
            )
            
            state['models_current_step'] = state['models_current_step'] + 1

        if step_error:
            # Keep tool_calls up to and including the failing step so the
            # invocation remains visible in the conversation history.
            solved_tool_calls = invocation.tool_calls[:state['models_current_step'] + 1]
            invocation = AIMessage(content=invocation.content, tool_calls=solved_tool_calls)
            state['supervisor_invocation_reason'] = "step_error"
        else:
            state['supervisor_invocation_reason'] = "step_done"
        
        state['models_invocation'] = None
        state['models_current_step'] = None
        state['models_invocation_confirmation'] = None
        state['models_invocation_reason'] = None

        state['messages'] = [invocation, *tool_responses]

        return state