"""
Naming rules engine.
Rules are defined per state and applied deterministically before any LLM call.
Add new states by extending RULES below.
"""
import re
from dataclasses import dataclass


@dataclass
class NamingRule:
    pattern: str           # regex pattern (case-insensitive)
    message: str           # human-readable flag message
    severity: str          # "block" | "warning" | "info"
    entity_types: list[str] | None = None  # None = applies to all types


# ---------------------------------------------------------------------------
# State rules registry
# ---------------------------------------------------------------------------

RULES: dict[str, list[NamingRule]] = {
    "DE": [
        NamingRule(
            pattern=r"\bbank\b|\bbanking\b|\bbankers\b",
            message="Delaware requires approval from the Office of the State Bank Commissioner to use 'bank' or 'banking'.",
            severity="warning",
        ),
        NamingRule(
            pattern=r"\btrust\b",
            message="Delaware restricts use of 'trust' in entity names. Approval may be required.",
            severity="warning",
        ),
        NamingRule(
            pattern=r"\binsurance\b|\bassurance\b",
            message="Delaware restricts use of 'insurance' — may require Dept. of Insurance approval.",
            severity="warning",
        ),
        NamingRule(
            pattern=r"\buniversity\b|\bcollege\b|\bacademy\b",
            message="Using 'university', 'college', or 'academy' may require educational licensing.",
            severity="warning",
        ),
        NamingRule(
            pattern=r"\bcooperative\b|\bco-op\b|\bcoop\b",
            message="Delaware cooperative entities have specific formation requirements.",
            severity="info",
        ),
        # Entity-type suffix requirements
        NamingRule(
            pattern=r"\b(inc|corp|incorporated|corporation)\b",
            message="Suffix 'Inc.' / 'Corp.' is appropriate for Corporations, not LLCs.",
            severity="warning",
            entity_types=["LLC", "LP", "LLP"],
        ),
        NamingRule(
            pattern=r"\bllc\b|\bl\.l\.c\b",
            message="Suffix 'LLC' is appropriate for LLCs, not Corporations.",
            severity="warning",
            entity_types=["Corporation", "LP", "LLP"],
        ),
    ],
    # Add more states here as adapters are built
}

# Delaware-specific rules summary for LLM calls (avoids re-building each time)
STATE_RULES_SUMMARIES: dict[str, str] = {
    "DE": (
        "Delaware prohibits or restricts: 'bank', 'banking', 'trust', 'insurance', 'university', 'college'. "
        "LLCs must include 'LLC' or 'Limited Liability Company' in their name. "
        "Corporations must include 'Inc.', 'Corp.', 'Incorporated', or 'Corporation'. "
        "Names must be distinguishable from all existing Delaware entities."
    ),
}


def apply_rules(name: str, entity_type: str, state_code: str) -> list[str]:
    """
    Run deterministic naming rules against the searched name.
    Returns a list of flag messages (may be empty).
    """
    state_rules = RULES.get(state_code.upper(), [])
    flags: list[str] = []

    for rule in state_rules:
        # Skip rules that don't apply to this entity type
        if rule.entity_types and entity_type not in rule.entity_types:
            continue
        if re.search(rule.pattern, name, re.IGNORECASE):
            flags.append(f"[{rule.severity.upper()}] {rule.message}")

    return flags


def get_rules_summary(state_code: str) -> str:
    return STATE_RULES_SUMMARIES.get(state_code.upper(), "No specific rules encoded for this state.")
