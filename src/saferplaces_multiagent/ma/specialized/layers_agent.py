from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import BaseTool
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage

from ...common.states import MABaseGraphState
from ...common.utils import _base_llm
from ...nodes.base.base_models import Layer
from ..names import NodeNames


# Registry-friendly description for the Layers agent.
# Use this to populate the supervisor agent registry.
LAYERS_AGENT_DESCRIPTION = {
    "name": NodeNames.LAYERS_AGENT,
    "description": (
        "Agent that manages a registry/list of geospatial layers. Each layer is a simple "
        "record describing title, type (raster|vector), source and optional metadata.\n"
        "This agent can list, select, add, remove, and update layers."
    ),
    "examples": [
        "List available layers",
        "Add a raster layer with src pointing to a tile service",
        "Get metadata for a vector layer by title",
    ]
}


class LayersRegistry:
    """In-memory registry of geospatial layers, scoped to a single conversation."""
    
    def __init__(self, layers: Optional[List[Layer]] = None, from_state: Optional[MABaseGraphState] = None):
        """Initialize with an optional list of layers.
        
        Args:
            layers: List of Layer objects. If None, creates empty registry.
            from_state: Optional state to initialize the registry from.
        """
        if from_state is not None:
            layers = [Layer(**l) for l in from_state.get("layer_registry", [])]
        self._layers = {l.title: l for l in (layers or [])}

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

class BuildLayerFromPromptInput(BaseModel):
    """Input for build_layer_from_prompt tool."""
    src: str = Field(description="Source URI or path to the layer data (required)")
    prompt: str = Field(description="Natural language description to help build the layer")
    title: Optional[str] = Field(default=None, description="Optional layer title")
    type: Optional[str] = Field(default=None, description="Optional layer type: 'raster' or 'vector'")
    description: Optional[str] = Field(default=None, description="Optional layer description")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata dictionary")


class LayerOutput(BaseModel):
    """Structured output for layer generation via LLM."""
    title: str = Field(description="Inferred or provided layer title")
    type: str = Field(description="Inferred or provided layer type: 'raster' or 'vector'")
    description: Optional[str] = Field(default=None, description="Inferred or provided layer description")
    metadata: Optional[Dict[str, Any]] = Field(default=None, description="Inferred or provided layer metadata")


# Prompts templates
class Prompts:
    """Centralized prompts for layers agent tools."""
    
    @staticmethod
    def format_layers_description(layers_data: List[Dict[str, Any]]) -> str:
        """Format layers list into a readable description.
        
        Args:
            layers_data: List of layer dictionaries from state.
            
        Returns:
            Formatted string describing available layers.
        """
        if not layers_data:
            return "No layers available"
        return "\n".join([
            f"- {l.get('title', 'N/A')} (type: {l.get('type', 'N/A')}, src: {l.get('src', 'N/A')})"
            for l in layers_data
        ])
    
    @staticmethod
    def choose_layer(layers_description: str, request: str) -> str:
        """Prompt for selecting the most appropriate layer."""
        return (
            f"Given the following available layers:\n"
            f"{layers_description}\n"
            f"Select the MOST appropriate layer for this request: \"{request}\"\n"
            f"Respond with ONLY the exact title of the chosen layer, nothing else.\n"
        )
    
    @staticmethod
    def build_layer_from_prompt(src: str, prompt: str, title: Optional[str] = None, 
                                type: Optional[str] = None, description: Optional[str] = None,
                                metadata: Optional[Dict[str, Any]] = None) -> str:
        """Prompt for inferring missing layer fields from a natural language description."""
        return (
            f"Given the following layer information and source URI, infer the missing details:\n"
            f"Source URI: {src}\n"
            f"User Request: {prompt}\n"
            f"\n"
            f"Current values:\n"
            f"- Title: {title if title else 'NOT PROVIDED'}\n"
            f"- Type: {type if type else 'NOT PROVIDED'} (must be 'raster' or 'vector')\n"
            f"- Description: {description if description else 'NOT PROVIDED'}\n"
            f"- Metadata: {metadata if metadata else 'NOT PROVIDED'}\n"
            f"\n"
            f"For any field marked \"NOT PROVIDED\", infer an appropriate value based on the source URI and user request.\n"
            f"Respond in JSON format with all fields filled in:\n"
            f"{{\n"
            f"  \"title\": \"...\",\n"
            f"  \"type\": \"raster or vector\",\n"
            f"  \"description\": \"...\",\n"
            f"  \"metadata\": {{...}} or null\n"
            f"}}\n"
        )


# Tool implementations
class ListLayersTool(BaseTool):
    name: str = "list_layers"
    description: str = "List all layers in the registry"
    args_schema: type[BaseModel] = ListLayersInput
    registry: LayersRegistry = None

    def _run(self) -> List[Dict[str, Any]]:
        if self.registry is None:
            return []
        layers = self.registry.list_layers()
        return [l.to_dict() for l in layers]


class GetLayerTool(BaseTool):
    name: str = "get_layer"
    description: str = "Get a specific layer by title"
    args_schema: type[BaseModel] = GetLayerInput
    registry: LayersRegistry = None

    def _run(self, title: str) -> Dict[str, Any] | str:
        if self.registry is None:
            return f"Layer '{title}' not found"
        layer = self.registry.get_layer(title)
        return layer.to_dict() if layer else f"Layer '{title}' not found"


class AddLayerTool(BaseTool):
    name: str = "add_layer"
    description: str = "Add a new layer to the registry"
    args_schema: type[BaseModel] = AddLayerInput
    registry: LayersRegistry = None

    def _run(self, title: str, type: str, src: str, description: Optional[str] = None, 
             metadata: Optional[Dict[str, Any]] = None) -> str:
        if self.registry is None:
            return "Error adding layer: registry not initialized"
        try:
            layer = self.registry.add_layer(Layer(title, type, src, description, metadata))
            return f"Layer '{layer.title}' added successfully"
        except Exception as e:
            return f"Error adding layer: {str(e)}"


class RemoveLayerTool(BaseTool):
    name: str = "remove_layer"
    description: str = "Remove a layer from the registry by title"
    args_schema: type[BaseModel] = RemoveLayerInput
    registry: LayersRegistry = None

    def _run(self, title: str) -> str:
        if self.registry is None:
            return f"Layer '{title}' not found"
        removed = self.registry.remove_layer(title)
        return f"Layer '{title}' removed" if removed else f"Layer '{title}' not found"


class UpdateLayerTool(BaseTool):
    name: str = "update_layer"
    description: str = "Update an existing layer's properties"
    args_schema: type[BaseModel] = UpdateLayerInput
    registry: LayersRegistry = None

    def _run(self, title: str, type: Optional[str] = None, src: Optional[str] = None,
             description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        if self.registry is None:
            return "Error updating layer: registry not initialized"
        try:
            kwargs = {k: v for k, v in {"type": type, "src": src, "description": description, "metadata": metadata}.items() if v is not None}
            layer = self.registry.update_layer(title, **kwargs)
            return f"Layer '{layer.title}' updated successfully"
        except Exception as e:
            return f"Error updating layer: {str(e)}"


class SearchByTypeTool(BaseTool):
    name: str = "search_by_type"
    description: str = "Search layers by type (raster or vector)"
    args_schema: type[BaseModel] = SearchByTypeInput
    registry: LayersRegistry = None

    def _run(self, layer_type: str) -> List[Dict[str, Any]] | str:
        if self.registry is None:
            return []
        try:
            layers = self.registry.search_by_type(layer_type)
            return [l.to_dict() for l in layers]
        except Exception as e:
            return f"Error searching layers: {str(e)}"


class BuildLayerFromPromptTool(BaseTool):
    name: str = "build_layer_from_prompt"
    description: str = "Build a layer by inferring missing fields from a natural language prompt"
    args_schema: type[BaseModel] = BuildLayerFromPromptInput
    registry: LayersRegistry = None

    def _run(self, src: str, prompt: str, title: Optional[str] = None, type: Optional[str] = None,
             description: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        
        if self.registry is None:
            return "Error building layer from prompt: registry not initialized"
        
        # Build prompt for LLM to infer missing fields
        inference_prompt = Prompts.build_layer_from_prompt(src, prompt, title, type, description, metadata)

        try:
            # Use structured output to get a LayerOutput object
            llm_with_output = _base_llm.with_structured_output(LayerOutput)
            layer_output = llm_with_output.invoke([HumanMessage(content=inference_prompt)])
            
            # Use provided values, fall back to inferred values from structured output
            final_title = title or layer_output.title
            final_type = type or layer_output.type
            final_description = description or layer_output.description
            final_metadata = metadata or layer_output.metadata
            
            # Validate type (should already be validated by Pydantic, but just in case)
            if final_type not in ("raster", "vector"):
                final_type = "raster"
            
            # Add layer to registry
            layer = self.registry.add_layer(Layer(
                title=final_title,
                type=final_type,
                src=src,
                description=final_description,
                metadata=final_metadata
            ))
            
            return f"Layer '{layer.title}' created successfully from prompt"
        except Exception as e:
            return f"Error building layer from prompt: {str(e)}"
        

class ChooseLayerTool(BaseTool):
    name: str = "choose_layer"
    description: str = "One-shot classification: find the most appropriate layer based on a natural language request"
    args_schema: type[BaseModel] = ChooseLayerInput
    registry: LayersRegistry = None

    def _run(self, request: str) -> str:
        if self.registry is None:
            return "No layers available in the registry"
        layers = self.registry.list_layers()
        
        if not layers:
            return "No layers available in the registry"
        
        # Build prompt for LLM to choose best matching layer
        layers_description = "\n".join([
            f"{i+1}. Title: {l.title}\n   Type: {l.type}\n   Source: {l.src}\n   Description: {l.description or 'N/A'}"
            for i, l in enumerate(layers)
        ])
        
        prompt = Prompts.choose_layer(layers_description, request)

        try:
            response = _base_llm.invoke([HumanMessage(content=prompt)])
            chosen_title = response.content.strip().strip('"').strip("'")
            
            # Find the layer
            chosen_layer = self.registry.get_layer(chosen_title)
            
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
        self.name = NodeNames.LAYERS_AGENT
        self.tools = [
            ListLayersTool(),
            GetLayerTool(),
            AddLayerTool(),
            RemoveLayerTool(),
            UpdateLayerTool(),
            SearchByTypeTool(),
            BuildLayerFromPromptTool(),
            ChooseLayerTool()
        ]
        self.llm = _base_llm.bind_tools(self.tools)

    def __call__(self, state: MABaseGraphState) -> MABaseGraphState:
        return self.run(state)

    def run(self, state: MABaseGraphState) -> MABaseGraphState:
        print(f"[{self.name}] → Processing layer operations...")
        
        registry = LayersRegistry(from_state=state)
        
        # Inject registry into all tools
        for tool in self.tools:
            tool.registry = registry

        # Invoke LLM with tools
        invoke_messages = [
            SystemMessage(content="You are a specialized agent for managing geospatial layers. Use the available tools to accomplish the goal."),
            HumanMessage(content=f"Goal: {state['layers_request']}")
        ]

        invocation = self.llm.invoke(invoke_messages)
        
        # No tool calls - just return message
        if not hasattr(invocation, "tool_calls") or len(invocation.tool_calls) == 0:
            print(f"[{self.name}] ✓ No tool calls")
            # state['messages'] = invocation
            state["layer_registry"] = [l.to_dict() for l in registry.list_layers()]
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
                
                tool_responses.append(ToolMessage(
                    content=result,
                    tool_call_id=tool_call["id"]
                ))
            else:
                tool_responses.append(ToolMessage(
                    content=f"Tool {tool_name} not found",
                    tool_call_id=tool_call["id"]
                ))

        state["layers_invocation"] = invocation
        state["layers_response"] = tool_responses
        state["layer_registry"] = [l.to_dict() for l in registry.list_layers()]
        
        print(f"[{self.name}] ✓ Done")
        return state
