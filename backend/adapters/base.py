from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class EntityMatch:
    """A single entity found on the state's search results page."""
    name: str
    entity_type: str
    status: str          # active | inactive | dissolved | unknown
    file_number: str = ""
    registered: str = "" # date string if available


@dataclass
class AdapterResult:
    """Structured output from a state adapter."""
    state_code: str
    state_name: str

    # Core availability signal
    availability: str           # available | taken | similar | unknown | error
    confidence: float           # 0.0 – 1.0

    # Supporting data
    raw_matches: list[EntityMatch] = field(default_factory=list)
    similar_names: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)  # naming rule warnings
    notes: str = ""

    # Extraction metadata (used for confidence calculation)
    extraction_method: str = "primary"  # primary | fallback | llm
    source_type: str = "web_form"       # web_form | api


class BaseStateAdapter(ABC):
    state_code: str
    state_name: str

    @abstractmethod
    async def search(self, name: str, entity_type: str) -> AdapterResult:
        """Run a search on the state's SOS website and return structured results."""
        ...

    def _build_confidence(
        self,
        extraction_method: str,
        result_clarity: str,  # clear | inferred | ambiguous
    ) -> float:
        extraction_scores = {"primary": 1.0, "fallback": 0.7, "llm": 0.4, "failed": 0.1}
        clarity_scores = {"clear": 1.0, "inferred": 0.7, "ambiguous": 0.4}

        extraction = extraction_scores.get(extraction_method, 0.4)
        clarity = clarity_scores.get(result_clarity, 0.4)
        source = 0.85  # web_form baseline; subclasses can override

        # Weighted formula from architecture doc
        return round(extraction * 0.40 + source * 0.25 + clarity * 0.25 + 1.0 * 0.10, 2)
