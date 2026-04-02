class ModelsPrompts:
    """Prompts for specialized models/simulations agent.
    
    Follows the F009 pattern with hierarchical organization and static method versioning.
    """

    class DigitalTwinTool:

        @staticmethod
        def _identity_routing() -> Prompt:
            return Prompt(dict(
                message = (
                    "DigitalTwinTool generates a geospatial Digital Twin for an Area of Interest (AOI) by creating "
                    "spatially aligned base layers from global open data. "
                    "Use it as the first modeling step when no DEM or foundational geospatial layers exist for the target area."
                )
            ))

        @staticmethod
        def _decision_policy() -> Prompt:
            return Prompt(dict(
                message = (
                    "Select DigitalTwinTool only when foundational geospatial layers for the AOI are not already available in context. "
                    "Do not use it if an adequate DEM already exists and no additional layers are needed. "
                    "For generic or underspecified requests, default to layers=['dem']; add further layers only if the user explicitly names them "
                    "or a downstream model strictly requires them."
                )
            ))

        @staticmethod
        def _argument_contract() -> Prompt:
            return Prompt(dict(
                message = (
                    "DigitalTwinTool argument contract:\n"
                    "- bbox (required): AOI bounding box in EPSG:4326 ordered as [west, south, east, north]\n"
                    "- layers (required): flat list of layer identifiers to generate\n"
                    "- pixelsize (optional): output resolution in meters, default 10\n"
                    "- out_format (optional): GTiff or COG, default GTiff\n"
                    "- region_name (optional): descriptive label for the AOI\n"
                    "- clip_geometry (optional): geometry to clip outputs\n"
                    "\n"
                    "Valid layer names:\n"
                    "dem, valleydepth, tri, tpi, slope, dem_filled, flow_dir, flow_accum, streams, hand, twi, "
                    "river_network, river_distance, buildings, dem_buildings, dem_filled_buildings, roads, "
                    "landuse, manning, ndvi, ndwi, ndbi, sea_mask, sand, clay\n"
                    "\n"
                    "Constraints:\n"
                    "- layers must be a flat list, not grouped by category\n"
                    "- bbox must be in EPSG:4326\n"
                    "- if layers are not specified, default to ['dem']\n"
                )
            ))

        @staticmethod
        def _tool_guardrails() -> Prompt:
            return Prompt(dict(
                message = (
                    "DigitalTwinTool guardrails:\n"
                    "- Do not invoke without a valid bbox in EPSG:4326.\n"
                    "- Use only the allowed layer identifiers; do not invent unsupported names.\n"
                    "- Do not request all layers unless explicitly asked for a comprehensive Digital Twin.\n"
                    "- This tool generates base layers only; do not treat it as a simulator.\n"
                )
            ))

        @staticmethod
        def _execution_facing() -> Prompt:
            return Prompt(dict(
                message = (
                    "To execute DigitalTwinTool: express the AOI as bbox=[west, south, east, north] in EPSG:4326, "
                    "then select the minimal layer set for the user goal. "
                    "Default to ['dem'] for generic requests; add terrain, hydrology, construction, land-cover, or soil layers "
                    "only when explicitly requested or strictly required downstream. "
                    "Return the produced S3 URIs and track each layer's category for later workflow steps."
                )
            ))

        class ContextForOrchestrator():

            @staticmethod
            def stable() -> Prompt:
                return Prompt(dict(
                    message = (
                        f"{ModelsPrompts.DigitalTwinTool._identity_routing().message}\n"
                        f"{ModelsPrompts.DigitalTwinTool._decision_policy().message}"
                    )
                ))

        class ContextForPlanner():

            @staticmethod
            def stable() -> Prompt:
                return Prompt(dict(
                    message = (
                        f"{ModelsPrompts.DigitalTwinTool._identity_routing().message}\n"
                        f"{ModelsPrompts.DigitalTwinTool._decision_policy().message}\n"
                        f"{ModelsPrompts.DigitalTwinTool._tool_guardrails().message}"
                    )
                ))

        class ContextForSpecialized():

            @staticmethod
            def stable() -> Prompt:
                return Prompt(dict(
                    message = (
                        f"{ModelsPrompts.DigitalTwinTool._argument_contract().message}\n"
                        f"{ModelsPrompts.DigitalTwinTool._tool_guardrails().message}\n"
                        f"{ModelsPrompts.DigitalTwinTool._execution_facing().message}"
                    )
                ))