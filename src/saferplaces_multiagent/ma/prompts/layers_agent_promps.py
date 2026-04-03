from langchain_core.messages import SystemMessage

from . import Prompt
from ...common.states import MABaseGraphState



class LayersAgentPrompts:

    class BasicLayerSummary:

        @staticmethod
        def stable(state: MABaseGraphState) -> Prompt:

            def write_message(layers):
                if not layers:
                    return "No layers available."
                
                all_lines = []
                for lidx, layer in enumerate(layers):
                    lines = []

                    title = layer.get("title", "untitled")
                    ltype = layer.get("type", "unknown")
                    src = layer.get("src", "")
                    desc = layer.get("description", "")
                    meta = layer.get("metadata", {})

                    lines.append(f"• {title} ({ltype})")
                    lines.append(f"  - Description: {desc}")
                    lines.append(f"  - Source: {src}")

                    if meta:
                        lines.append(f"  - Metadata:")
                        bbox = meta.get("bbox")
                        if bbox:
                            lines.append(f"    - bbox: {bbox}")
                        surface_type = meta.get("surface_type")
                        if surface_type:
                            lines.append(f"    - surface_type: {surface_type}")

                    all_lines.extend(lines)
                    if lidx < len(layers) - 1:
                        all_lines.append('---')
                
                message = '\n'.join(all_lines)
                return message
            
            layers = state.get("layer_registry", [])
            
            # FIXME: Minipatch - don't know why but sometimes relevant_layers_list is encapsulated in a list
            if isinstance(layers, list) and len(layers) == 1 and isinstance(layers[0], list):
                layers = layers[0]

            return Prompt(dict(
                title = "LayerSummaryWithGeospatialMetadata",
                description = "System prompt for summarizing layers with geospatial metadata",
                command = "",
                header = "Available layers in current project",
                message = write_message(layers)
            ))


class LayersInstructions:

    class InvokeTools:

        class Prompts:

            class _RoleAndScope:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    message = (
                        "You are a geospatial layer registry manager for the SaferPlaces platform.\n"
                        "Your task is to use the available tools to accomplish the requested layer operation.\n"
                        "\n"
                        "Available operations:\n"
                        "- list_layers: list all layers in the registry\n"
                        "- get_layer: retrieve a specific layer by title\n"
                        "- add_layer: add a new layer record\n"
                        "- remove_layer: delete a layer by title\n"
                        "- update_layer: modify an existing layer's properties\n"
                        "- search_by_type: filter layers by type (raster | vector)\n"
                        "- build_layer_from_prompt: infer missing layer fields from a natural language description and src URI\n"
                        "- choose_layer: select the most appropriate layer for a natural language request\n"
                        "\n"
                        "RULES:\n"
                        "- Produce tool calls only — do not generate narratives or communicate with the user.\n"
                        "- Use build_layer_from_prompt when adding a layer and title/type/description must be inferred.\n"
                        "- Do not add a layer if an equivalent one already exists in the registry (same src).\n"
                        "- Use the minimal number of tool calls to satisfy the request.\n"
                    )
                    return Prompt(dict(
                        header="[ROLE and SCOPE]",
                        message=message,
                    ))

            class _Request:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    request = state.get("layers_request", "")
                    return Prompt(dict(
                        header="[REQUEST]",
                        message=str(request),
                    ))

            class _LayerContext:

                @staticmethod
                def stable(state: MABaseGraphState) -> Prompt:
                    return LayersAgentPrompts.BasicLayerSummary.stable(state)

        class Invocation:

            class InvokeOneShot:

                @staticmethod
                def stable(state: MABaseGraphState) -> list:
                    role = LayersInstructions.InvokeTools.Prompts._RoleAndScope.stable(state)
                    layer_ctx = LayersInstructions.InvokeTools.Prompts._LayerContext.stable(state)
                    request = LayersInstructions.InvokeTools.Prompts._Request.stable(state)

                    message = (
                        f"{role.header}\n"
                        f"{role.message}\n"
                        "\n"
                        f"{layer_ctx.header}\n"
                        f"{layer_ctx.message}\n"
                        "\n"
                        f"{request.header}\n"
                        f"{request.message}\n"
                    )

                    return [SystemMessage(content=message)]