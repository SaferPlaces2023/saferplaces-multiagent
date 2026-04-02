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