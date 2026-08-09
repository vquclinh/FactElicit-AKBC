"""V3 prompt-family vocabulary and independence accounting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class PromptFamily(str, Enum):
    """Semantically distinct V3 acquisition/verification prompt families."""

    DIRECT = "DIRECT"
    SEMANTIC_PARAPHRASE = "SEMANTIC_PARAPHRASE"
    DEFINITION = "DEFINITION"
    CONTRAST = "CONTRAST"
    ALTERNATIVE = "ALTERNATIVE"
    DECOMPOSITION = "DECOMPOSITION"


@dataclass(frozen=True)
class PromptSupportUnit:
    """One support source after reducing same-family repeats."""

    prompt_family: PromptFamily
    independence_group: str
    model_role: str = ""
    model_id: str = ""

    @property
    def independence_key(self) -> tuple[str, str, str]:
        """The deterministic key V3 treats as one independent support.

        Repeated samples from the same prompt family and independence group do
        not become independent by repetition. A different model role is allowed
        to contribute a separate mechanism because the architecture assigns
        enumerator and verifier different jobs.
        """
        return (self.prompt_family.value, self.independence_group, self.model_role)


def prompt_family_for_group(group_key: str, facets: Iterable[str] = ()) -> PromptFamily:
    """Map existing provenance names onto the V3 prompt-family vocabulary."""

    text = " ".join((group_key, *facets)).lower()
    if "contrast" in text or "counterfactual" in text:
        return PromptFamily.CONTRAST
    if "definition" in text or "quantity" in text or "semantic" in text:
        return PromptFamily.DEFINITION
    if (
        "alternative" in text
        or "missingness" in text
        or "candidate_free" in text
        or "candidate-free" in text
        or "reelicitation" in text
    ):
        return PromptFamily.ALTERNATIVE
    if (
        "decomposition" in text
        or "structural" in text
        or "biography" in text
        or "facet" in text
        or "geographic" in text
        or "life_dates" in text
    ):
        return PromptFamily.DECOMPOSITION
    if "direct" in text or "exact" in text or "seed" in text:
        return PromptFamily.DIRECT
    return PromptFamily.SEMANTIC_PARAPHRASE


def independent_prompt_support(units: Iterable[PromptSupportUnit]) -> int:
    """Count independent support without raw-sample inflation."""

    return len({unit.independence_key for unit in units})


__all__ = [
    "PromptFamily",
    "PromptSupportUnit",
    "independent_prompt_support",
    "prompt_family_for_group",
]
