"""Structured confirmation templates — deterministic, no LLM call needed."""

from typing import Dict, List, Any


# ============================================================================
# Agent label mapping (technical name → human-readable)
# ============================================================================

AGENT_LABELS = {
    "models_subgraph": "Simulazione",
    "retriever_subgraph": "Recupero dati",
    "layers_agent": "Gestione layer",
    "digital_twin_agent": "Digital Twin",
    "operational_agent": "Operazioni",
}


def _agent_label(agent_name: str) -> str:
    return AGENT_LABELS.get(agent_name, agent_name)


# ============================================================================
# Plan confirmation template
# ============================================================================

def format_plan_confirmation(plan: List[Dict[str, Any]], parsed_request: dict = None) -> str:
    """Build a deterministic confirmation message for an execution plan.
    
    Args:
        plan: List of dicts with keys 'agent' and 'goal'.
        parsed_request: Optional parsed request dict with parameters.
        
    Returns:
        Formatted confirmation string ready for the user.
    """
    n = len(plan)
    lines = [f"📋 Piano di esecuzione ({n} step):", ""]

    for i, step in enumerate(plan, 1):
        label = _agent_label(step.get("agent", "unknown"))
        goal = step.get("goal", "")
        lines.append(f"  {i}. [{label}] {goal}")

    # Show extracted parameters if available
    if parsed_request:
        params = parsed_request.get("parameters", {})
        if params:
            non_null = {k: v for k, v in params.items() if v is not None}
            if non_null:
                lines.append("")
                lines.append("📎 Parametri rilevati:")
                for k, v in non_null.items():
                    lines.append(f"  • {k}: {v}")

    lines.append("")
    lines.append("Rispondi:")
    lines.append('  ✓ "ok" per procedere')
    lines.append("  ✏️ descrivi le modifiche desiderate")
    lines.append('  ❌ "annulla" per cancellare')

    return "\n".join(lines)


# ============================================================================
# Tool invocation confirmation template
# ============================================================================

# Arguments that are too noisy for user display
_HIDDEN_ARGS = {"_graph_state"}


def format_tool_confirmation(tool_calls: List[Dict[str, Any]]) -> str:
    """Build a deterministic confirmation message for tool invocations.
    
    Args:
        tool_calls: List of dicts with keys 'name' and 'args'.
        
    Returns:
        Formatted confirmation string ready for the user.
    """
    lines = ["🔧 Tool da eseguire:", ""]

    for tc in tool_calls:
        tool_name = tc.get("name", "unknown")
        tool_args = tc.get("args", {})

        lines.append(f"  • {tool_name}:")
        for arg_name, arg_value in tool_args.items():
            if arg_name in _HIDDEN_ARGS:
                continue
            # Truncate very long values
            display_val = str(arg_value)
            if len(display_val) > 120:
                display_val = display_val[:117] + "..."
            lines.append(f"    - {arg_name}: {display_val}")

    lines.append("")
    lines.append("Rispondi:")
    lines.append('  ✓ "ok" per eseguire')
    lines.append("  ✏️ descrivi le modifiche agli argomenti")
    lines.append('  ❌ "salta" per saltare questo step')

    return "\n".join(lines)


# ============================================================================
# Validation error template
# ============================================================================

def format_validation_errors(
    validation_errors: Dict[str, Dict[str, str]],
) -> str:
    """Build a deterministic message showing validation errors to the user.
    
    Args:
        validation_errors: {tool_name: {arg_name: error_message}}
        
    Returns:
        Formatted error report string.
    """
    lines = ["⚠️ Errori di validazione:", ""]

    for tool_name, arg_errors in validation_errors.items():
        lines.append(f"  • {tool_name}:")
        for arg_name, error_msg in arg_errors.items():
            lines.append(f"    - {arg_name}: {error_msg}")

    lines.append("")
    lines.append("Rispondi:")
    lines.append("  ✏️ fornisci i valori corretti")
    lines.append('  🔧 "correggi" per correzione automatica')
    lines.append('  ⏭️ "salta" per rimuovere il tool problematico')
    lines.append('  ❌ "annulla" per cancellare')

    return "\n".join(lines)
