"""Execution Narrative — Structured story of execution flow.

The execution narrative is a structured object that accumulates incrementally
during plan execution. It tracks:
- What was requested (parsed_request summary)
- What was planned (execution plan)
- What happened during execution (step results, tool calls, errors)
- What was produced (layers created, data obtained)
- How the user interacted (confirmations, modifications, clarifications)
- What comes next (suggestions for follow-up actions)
"""

from __future__ import annotations

from typing import Optional, List, Literal, Dict, Any
from dataclasses import dataclass, field, asdict
import datetime


@dataclass
class LayerSummary:
    """Summary of a layer (input or output) in the execution."""
    layer_id: str
    name: str
    layer_type: Literal["raster", "vector"]
    source_uri: Optional[str] = None
    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StepResult:
    """Result of executing a single step in the execution plan."""
    step_index: int
    agent: str  # e.g. "models_subgraph", "retriever_subgraph"
    goal: str
    tool_name: Optional[str] = None
    outcome: Literal["success", "partial", "error", "skipped"] = "pending"
    output_summary: Optional[str] = None
    error_message: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StepError:
    """Error detail from an execution step."""
    step_index: int
    tool_name: str
    error_type: str
    message: str
    recovery_suggestion: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionNarrative:
    """Complete narrative of request execution."""
    
    # What was requested
    request_summary: Optional[str] = None
    request_type: Optional[str] = None  # "action", "info", "analysis"
    
    # What was planned
    plan_summary: Optional[str] = None
    total_steps: int = 0
    
    # What happened (accumulated during execution)
    steps_executed: List[StepResult] = field(default_factory=list)
    layers_created: List[LayerSummary] = field(default_factory=list)
    layers_used: List[LayerSummary] = field(default_factory=list)
    errors: List[StepError] = field(default_factory=list)
    
    # User interactions
    user_interactions: List[str] = field(default_factory=list)
    
    # Suggestions for next steps
    suggestions: List[str] = field(default_factory=list)
    
    # Timeline
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def add_step_result(self, step: StepResult) -> None:
        """Add a step result to the narrative."""
        step.timestamp = datetime.datetime.utcnow().isoformat()
        self.steps_executed.append(step)

    def add_error(self, error: StepError) -> None:
        """Add an error to the narrative."""
        error.timestamp = datetime.datetime.utcnow().isoformat()
        self.errors.append(error)

    def add_layer_created(self, layer: LayerSummary) -> None:
        """Record a layer created during execution."""
        self.layers_created.append(layer)

    def add_layer_used(self, layer: LayerSummary) -> None:
        """Record a layer used as input."""
        self.layers_used.append(layer)

    def add_user_interaction(self, interaction_desc: str) -> None:
        """Record a user interaction (clarification, modification, etc.)."""
        self.user_interactions.append(interaction_desc)

    def add_suggestion(self, suggestion: str) -> None:
        """Add a suggestion for next steps."""
        self.suggestions.append(suggestion)

    def get_completion_status(self) -> Literal["pending", "completed", "partial", "failed"]:
        """Determine overall completion status based on step outcomes."""
        if not self.steps_executed:
            return "pending"
        
        outcomes = [step.outcome for step in self.steps_executed]
        
        if all(o == "success" for o in outcomes):
            return "completed"
        elif any(o == "error" for o in outcomes):
            return "failed"
        elif any(o in ("partial", "skipped") for o in outcomes):
            return "partial"
        else:
            return "pending"

    def to_dict(self) -> dict:
        """Convert to dict representation."""
        return {
            "request_summary": self.request_summary,
            "request_type": self.request_type,
            "plan_summary": self.plan_summary,
            "total_steps": self.total_steps,
            "steps_executed": [s.to_dict() for s in self.steps_executed],
            "layers_created": [l.to_dict() for l in self.layers_created],
            "layers_used": [l.to_dict() for l in self.layers_used],
            "errors": [e.to_dict() for e in self.errors],
            "user_interactions": self.user_interactions,
            "suggestions": self.suggestions,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "completion_status": self.get_completion_status(),
        }
