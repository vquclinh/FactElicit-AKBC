"""V3 verification modes layered on Module 17's score-label kernel."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from cover_kbc.types import ModelRole


class V3VerificationMode(str, Enum):
    """The V3 verifier questions; labels remain score-label based."""

    UNARY = "UNARY"
    SEMANTIC = "SEMANTIC"
    CONTRAST = "CONTRAST"


class ContrastOutcome(str, Enum):
    """Typed contrastive verifier label space."""

    H1 = "H1"
    H2 = "H2"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class V3VerificationRequest:
    """A blind V3 verifier request.

    It carries the factual/semantic hypothesis and the relation contract
    question. It deliberately omits generator confidence, support counts and
    "the enumerator believes..." rationales.
    """

    relation: str
    subject: str
    mode: V3VerificationMode
    target_id: str
    target_text: str
    relation_definition: str
    comparison_id: str = ""
    comparison_text: str = ""
    semantic_qualifier: str = ""
    rejection_first: bool = False
    verifier_role: ModelRole = ModelRole.VERIFIER
    #: A relation-level hard-negative *class* boundary (audit 0077). It names
    #: the attribute classes a wrong answer would belong to; it never says
    #: anything about the candidate being verified, so the blindness invariant
    #: this class exists to protect is untouched. Empty unless a V3.1 Class-B
    #: feature supplies one.
    relation_boundary: str = ""

    @property
    def request_id(self) -> str:
        raw = "|".join((
            "v3ver", self.relation, self.subject, self.mode.value,
            self.target_id, self.comparison_id, self.semantic_qualifier,
            "reject" if self.rejection_first else "normal",
        ))
        # Appended only when a boundary is actually present. A request with no
        # boundary must hash exactly as it did before the field existed, or
        # every V3 verification edge id would change the moment the field was
        # added - including on runs with no Class-B feature enabled, where the
        # prompt is byte-identical. Two requests that differ only by boundary
        # still differ here, which is the property the id has to carry.
        if self.relation_boundary:
            raw = f"{raw}|{self.relation_boundary}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def label_schema(self) -> tuple[str, ...]:
        if self.mode is V3VerificationMode.CONTRAST:
            return (ContrastOutcome.H1.value, ContrastOutcome.H2.value,
                    ContrastOutcome.UNKNOWN.value)
        return ("VALID", "INVALID", "UNKNOWN")

    def to_json(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "Relation": self.relation,
            "SubjectEntity": self.subject,
            "mode": self.mode.value,
            "target_id": self.target_id,
            "target_text": self.target_text,
            "comparison_id": self.comparison_id,
            "comparison_text": self.comparison_text,
            "semantic_qualifier": self.semantic_qualifier,
            "relation_definition": self.relation_definition,
            "rejection_first": self.rejection_first,
            "relation_boundary": self.relation_boundary,
            "label_schema": list(self.label_schema),
            "verifier_role": self.verifier_role.value,
        }


def render_v3_verification_prompt(request: V3VerificationRequest) -> str:
    """Render the verifier question without acquisition-side confidence."""

    qualifier = (
        f"\nSemantic qualifier: {request.semantic_qualifier}"
        if request.semantic_qualifier else ""
    )
    # Placed immediately before the label block in every frame, which is where
    # M17 puts its own boundary: the verifier reads the rule that separates the
    # classes right before it is asked to choose between them.
    boundary = f"{request.relation_boundary}\n" if request.relation_boundary else ""
    if request.mode is V3VerificationMode.CONTRAST:
        return (
            "Decide which hypothesis is better supported for the relation.\n"
            f"Subject: {request.subject}\n"
            f"Relation: {request.relation}\n"
            f"Definition: {request.relation_definition}\n"
            f"H1: {request.target_text}\n"
            f"H2: {request.comparison_text}{qualifier}\n"
            f"{boundary}"
            "Labels: H1, H2, UNKNOWN."
        )
    if request.rejection_first:
        return (
            "Identify the strongest reason this candidate should NOT be a "
            "direct answer to the relation. If no such reason is supported, "
            "treat it as potentially valid.\n"
            f"Subject: {request.subject}\n"
            f"Relation: {request.relation}\n"
            f"Definition: {request.relation_definition}\n"
            f"Candidate: {request.target_text}{qualifier}\n"
            f"{boundary}"
            "Labels: VALID, INVALID, UNKNOWN."
        )
    question = (
        "Does the candidate answer precisely this relation, rather than a "
        "nearby semantic slot?"
        if request.mode is V3VerificationMode.SEMANTIC
        else "Is the candidate a valid answer to this relation?"
    )
    return (
        f"{question}\n"
        f"Subject: {request.subject}\n"
        f"Relation: {request.relation}\n"
        f"Definition: {request.relation_definition}\n"
        f"Candidate: {request.target_text}{qualifier}\n"
        f"{boundary}"
        "Labels: VALID, INVALID, UNKNOWN."
    )


def stock_rejection_first_request(
    *, relation: str, subject: str, target_id: str, target_text: str,
    relation_definition: str,
) -> V3VerificationRequest:
    """The stock-specific rejection-first verifier request."""

    return V3VerificationRequest(
        relation=relation,
        subject=subject,
        mode=V3VerificationMode.SEMANTIC,
        target_id=target_id,
        target_text=target_text,
        relation_definition=relation_definition,
        rejection_first=True,
    )


def select_contrast_pair(
    hypotheses: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    """Choose one deterministic contrast pair instead of O(n^2) comparisons."""

    material = [
        h for h in hypotheses
        if h.get("status") not in {"DROPPED"} and h.get("hypothesis_id")
    ]
    if len(material) < 2:
        return None
    ordered = sorted(
        material,
        key=lambda h: (
            -int(h.get("independent_support_count", 0)),
            -int(h.get("raw_support_count", 0)),
            str(h.get("hypothesis_id")),
        ),
    )
    top = ordered[0]
    for other in ordered[1:]:
        if str(other.get("normalized_value")) != str(top.get("normalized_value")):
            return (str(top["hypothesis_id"]), str(other["hypothesis_id"]))
    return None


__all__ = [
    "ContrastOutcome",
    "V3VerificationMode",
    "V3VerificationRequest",
    "render_v3_verification_prompt",
    "select_contrast_pair",
    "stock_rejection_first_request",
]
