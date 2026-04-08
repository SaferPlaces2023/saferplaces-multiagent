"""Hybrid response classifier: rule-based for obvious cases, LLM fallback for ambiguous ones."""

import re
import json
from typing import Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from .utils import _base_llm


# ============================================================================
# Rule-Based Patterns
# ============================================================================

# Pattern → (label, confidence)  where confidence is "high" or "medium"
# Patterns are compiled for IT/EN common responses.

_ACCEPT_PATTERN = re.compile(
    r"^(ok|okay|sì|si|yes|y|proceed|vai|go|do it|fai|perfect|perfetto|bene|va bene|"
    r"looks good|lgtm|sounds good|sure|certo|d'accordo|procedi|esegui|approvo|approve)$",
    re.IGNORECASE,
)

_ABORT_PATTERN = re.compile(
    r"^(cancel|annulla|stop|abort|basta|nevermind|non importa|forget it|lascia stare|"
    r"lascia perdere|no grazie|no thanks)$",
    re.IGNORECASE,
)

_SKIP_PATTERN = re.compile(
    r"^(skip|salta|skippa|skip this|salta questo)$",
    re.IGNORECASE,
)

_REJECT_PATTERN = re.compile(
    r"^(no|nope|sbagliato|wrong|non va|that's wrong|assolutamente no)$",
    re.IGNORECASE,
)

_CLARIFY_INDICATORS = re.compile(
    r"^(what|cosa|perché|why|come|how|explain|spiega|spiegami|dimmi|tell me|"
    r"che significa|what does|what is|cos'è)",
    re.IGNORECASE,
)

_MODIFY_INDICATORS = re.compile(
    r"(cambia|change|modify|modifica|al posto di|instead|sostituisci|replace|swap|"
    r"usa .+ invece|use .+ instead|diverso|different|aggiorna|update)",
    re.IGNORECASE,
)

# Validation-specific patterns
_AUTOCORRECT_PATTERN = re.compile(
    r"^(fix it|fixalo|aggiusta|correggi|auto.?correct|make it work|fallo funzionare|"
    r"you decide|decidi tu|sistematelo|fallo tu)$",
    re.IGNORECASE,
)


# ============================================================================
# Hybrid Classifier
# ============================================================================

class ResponseClassifier:
    """Two-level classifier: rule-based for obvious intents, LLM for ambiguous ones.
    
    Level 1 (rule-based): keyword/regex matching with high/medium confidence.
    Level 2 (LLM): zero-shot classification for unmatched responses.
    
    On LLM failure: re-interrupts user once, then defaults to safe fallback.
    """

    def __init__(self, llm=None):
        self.llm = llm or _base_llm

    def classify_plan_response(self, user_response: str) -> str:
        """Classify user response to a plan confirmation.
        
        Returns: "accept" | "modify" | "clarify" | "reject" | "abort"
        """
        labels = {"accept", "modify", "clarify", "reject", "abort"}
        return self._classify(user_response, labels, "plan")

    def classify_invocation_response(self, user_response: str) -> str:
        """Classify user response to a tool invocation confirmation.
        
        Returns: "accept" | "modify" | "clarify" | "reject" | "abort"
        """
        labels = {"accept", "modify", "clarify", "reject", "abort"}
        return self._classify(user_response, labels, "invocation")

    def classify_validation_response(
        self, user_response: str, is_post_clarification: bool = False
    ) -> str:
        """Classify user response to a validation failure.
        
        Returns: "provide_corrections" | "clarify_requirements" | "auto_correct" | 
                 "acknowledge" | "skip_tool" | "abort"
        """
        labels = {
            "provide_corrections", "clarify_requirements", "auto_correct",
            "acknowledge", "skip_tool", "abort",
        }
        return self._classify(
            user_response, labels, "validation",
            is_post_clarification=is_post_clarification,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _classify(
        self,
        user_response: str,
        valid_labels: set,
        context: str,
        is_post_clarification: bool = False,
    ) -> str:
        """Two-level classification pipeline."""
        text = user_response.strip()

        # Level 1: rule-based
        label = self._rule_based(text, context, is_post_clarification)
        if label and label in valid_labels:
            return label

        # Level 2: LLM fallback
        label = self._llm_classify(text, valid_labels, context, is_post_clarification)
        if label and label in valid_labels:
            return label

        # Safe fallback — depends on context
        fallback = self._safe_fallback(context)
        print(f"[ResponseClassifier] ⚠ LLM returned invalid label, falling back to '{fallback}'")
        return fallback

    def _rule_based(
        self,
        text: str,
        context: str,
        is_post_clarification: bool,
    ) -> Optional[str]:
        """Level 1: deterministic keyword matching."""
        # Accept
        if _ACCEPT_PATTERN.match(text):
            if context == "validation" and is_post_clarification:
                return "acknowledge"
            return "accept"

        # Abort / Cancel
        if _ABORT_PATTERN.match(text):
            return "abort"

        # Skip (validation only)
        if context == "validation" and _SKIP_PATTERN.match(text):
            return "skip_tool"

        # Reject (short, definitive "no")
        if _REJECT_PATTERN.match(text):
            return "reject" if context != "validation" else "abort"

        # Auto-correct (validation only)
        if context == "validation" and _AUTOCORRECT_PATTERN.match(text):
            return "auto_correct"

        # Modify — contains change/modify keywords (medium confidence)
        if _MODIFY_INDICATORS.search(text):
            return "modify" if context != "validation" else "provide_corrections"

        # Clarify — question-like patterns (medium confidence)
        if "?" in text and len(text) < 150 and _CLARIFY_INDICATORS.match(text):
            return "clarify" if context != "validation" else "clarify_requirements"

        return None  # No rule matched → fall through to LLM

    def _llm_classify(
        self,
        text: str,
        valid_labels: set,
        context: str,
        is_post_clarification: bool,
    ) -> Optional[str]:
        """Level 2: LLM zero-shot classification."""
        labels_str = " / ".join(sorted(valid_labels))

        # classification_prompt = (
        #     f"Classify the user's response into ONE of these categories: {labels_str}\n\n"
        #     f"User response: \"{text}\"\n\n"
        # )
        # if is_post_clarification:
        #     classification_prompt += (
        #         "Note: this is a response AFTER the user received an explanation. "
        #         "If the user simply acknowledges (e.g., 'ok', 'yes'), classify as 'acknowledge'.\n\n"
        #     )
        # classification_prompt += f"Return ONLY the label name ({labels_str})."

        classification_prompt = (
            "You are classifying a user reply to a proposed task resolution procedure.\n"
            "\n"
            "Labels:\n"
            "- accept → user agrees\n"
            "- modify → user wants changes (even if phrased as a question)\n"
            "- clarify → user asks for explanation, not change\n"
            "- reject → user says it's wrong\n"
            "- abort → user cancels\n"
            "\n"
        )
        if is_post_clarification:
            classification_prompt += (
                "Note: this is a response AFTER the user received an explanation. "
                "If the user simply acknowledges (e.g., 'ok', 'yes'), classify as 'acknowledge'.\n"
                "\n"
            )
        classification_prompt += (
            "IMPORTANT:\n"
            "If the user suggests ANY change, EVEN AS A QUESTION → classify as \"modify\".\n"
            "\n"
            f"User response: \"{text}\"\n"
            "\n"
            "Return ONLY the label.\n"
        )

        messages = [
            SystemMessage(content="You are a precise intent classifier. Return only the label name, nothing else."),
            HumanMessage(content=classification_prompt),
        ]

        try:
            response = self.llm.invoke(messages)
            label = response.content.strip().lower()
            if label in valid_labels:
                return label
            print(f"[ResponseClassifier] ⚠ LLM returned '{label}', not in {valid_labels}")
            return None
        except Exception as e:
            print(f"[ResponseClassifier] ⚠ LLM classification error: {e}")
            return None

    @staticmethod
    def _safe_fallback(context: str) -> str:
        """Context-dependent safe fallback when both levels fail."""
        if context == "plan":
            return "reject"
        if context == "invocation":
            return "reject"
        # validation: safest is auto_correct (agent tries to fix)
        return "auto_correct"
