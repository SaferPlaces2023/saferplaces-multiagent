from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ..names import NodeNames


# Registry-friendly description for the Layers agent.
# Use this to populate the supervisor agent registry.
LAYERS_AGENT_DESCRIPTION = {
    "name": NodeNames.LAYERS_AGENT if hasattr(NodeNames, 'LAYERS_AGENT') else "layers_agent",
    "description": (
        "Agent that manages a registry/list of geospatial layers. Each layer is a simple "
        "record describing title, type (raster|vector), source and optional metadata."
    ),
    "examples": [
        "List available layers",
        "Add a raster layer with src pointing to a tile service",
        "Get metadata for a vector layer by title",
    ]
}


@dataclass
class Layer:
    title: str
    type: str  # "raster" or "vector"
    src: str
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.type not in ("raster", "vector"):
            raise ValueError("Layer.type must be 'raster' or 'vector'")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LayersRegistry:
    """Singleton in-memory registry of geospatial layers."""
    
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LayersRegistry, cls).__new__(cls)
            cls._instance._layers = {}
        return cls._instance

    def list_layers(self) -> List[Layer]:
        return list(self._layers.values())

    def get_layer(self, title: str) -> Optional[Layer]:
        return self._layers.get(title)

    def add_layer(self, layer: Layer | Dict[str, Any]) -> Layer:
        if isinstance(layer, dict):
            layer = Layer(**layer)
        if layer.title in self._layers:
            raise KeyError(f"Layer '{layer.title}' already exists")
        self._layers[layer.title] = layer
        return layer

    def remove_layer(self, title: str) -> bool:
        return self._layers.pop(title, None) is not None

    def update_layer(self, title: str, **kwargs) -> Layer:
        existing = self.get_layer(title)
        if existing is None:
            raise KeyError(f"Layer '{title}' not found")
        updated = Layer(
            title=existing.title,
            type=kwargs.get("type", existing.type),
            src=kwargs.get("src", existing.src),
            description=kwargs.get("description", existing.description),
            metadata=kwargs.get("metadata", existing.metadata),
        )
        self._layers[title] = updated
        return updated

    def search_by_type(self, layer_type: str) -> List[Layer]:
        if layer_type not in ("raster", "vector"):
            raise ValueError("layer_type must be 'raster' or 'vector'")
        return [l for l in self._layers.values() if l.type == layer_type]


# Tool schemas
class ListLayersInput(BaseModel):
    """Input for list_layers tool."""
    pass


class GetLayerInput(BaseModel):
    """Input for get_layer tool."""
    title: str = Field(description="The title of the layer to retrieve")


class AddLayerInput(BaseModel):
    """Input for add_layer tool."""
    title: str = Field(description="The title of the layer")
    type: str = Field(description="Layer type: 'raster' or 'vector'")
    src: str = Field(description="Source URI or path to the layer data")
    description: Optional[str] = Field(default=None, description="Optional description")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata dictionary")


class RemoveLayerInput(BaseModel):
    """Input for remove_layer tool."""
    title: str = Field(description="The title of the layer to remove")


class UpdateLayerInput(BaseModel):
    """Input for update_layer tool."""
    title: str = Field(description="The title of the layer to update")
    type: Optional[str] = Field(default=None, description="New layer type: 'raster' or 'vector'")
    src: Optional[str] = Field(default=None, description="New source URI")
    description: Optional[str] = Field(default=None, description="New description")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="New metadata")


class SearchByTypeInput(BaseModel):
    """Input for search_by_type tool."""
    layer_type: str = Field(description="Layer type to search for: 'raster' or 'vector'")


class ChooseLayerInput(BaseModel):
    """Input for choose_layer tool."""
    request: str = Field(description="Natural language description of the desired layer")


# Tool implementations
class ListLayersTool(BaseTool):
    name: str = "list_layers"
    description: str = "List all layers in the registry"
    args_schema: type[BaseModel] = ListLayersInput

    def _run(self) -> List[Dict[str, Any]]:
        registry = LayersRegistry()
        layers = registry.list_layers()
        return [l.to_dict() for l in layers]


class GetLayerTool(BaseTool):
    name: str = "get_layer"
    description: str = "Get a specific layer by title"
    args_schema: type[BaseModel] = GetLayerInput

    def _run(self, title: str) -> Dict[str, Any] | str:
        registry = LayersRegistry()
        layer = registry.get_layer(title)
        return layer.to_dict() if layer else f"Layer '{title}' not found"


class AddLayerTool(BaseTool):
    name: str = "add_layer"
    description: str = "Add a new layer to the registry"
    args_schema: type[BaseModel] = AddLayerInput

    def _run(self, title: str, type: str, src: str, description: Optional[str] = None, 
             metadata: Optional[Dict[str, Any]] = None) -> str:
        registry = LayersRegistry()
        try:
            layer = registry.add_layer(Layer(title, type, src, description, metadata))
            return f"Layer '{layer.title}' added successfully"
        except Exception as e:
            return f"Error adding layer: {str(e)}"


class RemoveLayerTool(BaseTool):
    name: str = "remove_layer"
    description: str = "Remove a layer from the registry by title"
    args_schema: type[BaseModel] = RemoveLayerInput

    def _run(self, title: str) -> str:
        registry = LayersRegistry()
        removed = registry.remove_layer(title)
        return f"Layer '{title}' removed" if removed else f"Layer '{title}' not found"


class UpdateLayerTool(BaseTool):
    name: str = "update_layer"
    description: str = "Update an existing layer's properties"
    args_schema: type[BaseModel] = UpdateLayerInput

    def _run(self, title: str, type: Optional[str] = None, src: Optional[str] = None,
             description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        registry = LayersRegistry()
        try:
            kwargs = {k: v for k, v in {"type": type, "src": src, "description": description, "metadata": metadata}.items() if v is not None}
            layer = registry.update_layer(title, **kwargs)
            return f"Layer '{layer.title}' updated successfully"
        except Exception as e:
            return f"Error updating layer: {str(e)}"


class SearchByTypeTool(BaseTool):
    name: str = "search_by_type"
    description: str = "Search layers by type (raster or vector)"
    args_schema: type[BaseModel] = SearchByTypeInput

    def _run(self, layer_type: str) -> List[Dict[str, Any]] | str:
        registry = LayersRegistry()
        try:
            layers = registry.search_by_type(layer_type)
            return [l.to_dict() for l in layers]
        except Exception as e:
            return f"Error searching layers: {str(e)}"


class ChooseLayerTool(BaseTool):
    name: str = "choose_layer"
    description: str = "One-shot classification: find the most appropriate layer based on a natural language request"
    args_schema: type[BaseModel] = ChooseLayerInput

    def _run(self, request: str) -> str:
        registry = LayersRegistry()
        layers = registry.list_layers()
        
        if not layers:
            return "No layers available in the registry"
        
        # Build prompt for LLM to choose best matching layer
        layers_description = "\n".join([
            f"{i+1}. Title: {l.title}\n   Type: {l.type}\n   Source: {l.src}\n   Description: {l.description or 'N/A'}"
            for i, l in enumerate(layers)
        ])
        
        prompt = f"""Given the following available layers:

{layers_description}

Select the MOST appropriate layer for this request: "{request}"

Respond with ONLY the exact title of the chosen layer, nothing else."""

        try:
            response = _base_llm.invoke([HumanMessage(content=prompt)])
            chosen_title = response.content.strip().strip('"').strip("'")
            
            # Find the layer
            chosen_layer = registry.get_layer(chosen_title)
            
            if chosen_layer:
                return chosen_layer.to_dict()
            else:
                # Fallback: try partial match
                for layer in layers:
                    if chosen_title.lower() in layer.title.lower() or layer.title.lower() in chosen_title.lower():
                        return layer.to_dict()
                
                return f"Could not match request to any layer. LLM suggested: {chosen_title}"
        except Exception as e:
            return f"Error choosing layer: {str(e)}"


class LayersAgent:
    """Fast agent for managing geospatial layers - executes tools immediately."""
    
    def __init__(self):
        self.name = NodeNames.LAYERS_AGENT if hasattr(NodeNames, 'LAYERS_AGENT') else "layers_agent"
        self.tools = [
            ListLayersTool(),
            GetLayerTool(),
            AddLayerTool(),
            RemoveLayerTool(),
            UpdateLayerTool(),
            SearchByTypeTool()
        ]
        self.llm = _base_llm.bind_tools(self.tools)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{self.name}] → Processing layer operations...")

        # Invoke LLM with tools
        invoke_messages = [
            SystemMessage(content="You are a specialized agent for managing geospatial layers. Use the available tools to accomplish the goal."),
            HumanMessage(content=f"Goal: {state['plan'][state['current_step']].get('goal', 'N/A')}\nParsed: {state.get('parsed_request', '')}")
        ]

        invocation = self.llm.invoke(invoke_messages)
        
        # No tool calls - just return message
        if not hasattr(invocation, "tool_calls") or len(invocation.tool_calls) == 0:
            print(f"[{self.name}] ✓ No tool calls")
            state["current_step"] += 1
            state['messages'] = invocation
            return state

        # Execute tools immediately
        print(f"[{self.name}] → Executing {len(invocation.tool_calls)} tool(s): {[tc['name'] for tc in invocation.tool_calls]}")
        
        tool_responses = []
        for tool_call in invocation.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args", {})
            
            # Find and execute tool
            tool = next((t for t in self.tools if t.name == tool_name), None)
            if tool:
                print(f"[{self.name}]   → {tool_name}({tool_args})")
                result = tool._run(**tool_args)
                
                # Store result in state
                state.setdefault("tool_results", {})
                state["tool_results"][f"step_{state['current_step']}"] = state["tool_results"].get(f"step_{state['current_step']}", [])
                state["tool_results"][f"step_{state['current_step']}"].append({
                    "tool": tool_name,
                    "args": tool_args,
                    "result": result
                })
                
                tool_responses.append(ToolMessage(
                    content=result,
                    tool_call_id=tool_call["id"]
                ))
            else:
                tool_responses.append(ToolMessage(
                    content=f"Tool {tool_name} not found",
                    tool_call_id=tool_call["id"]
                ))

        state["current_step"] += 1
        state["messages"] = [invocation, *tool_responses]
        
        print(f"[{self.name}] ✓ Done")
        return state
