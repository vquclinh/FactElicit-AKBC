"""Module 4 - the logit-calibrated blind verifier.

**Milestone 1 status: interface only.**

The prompt construction and the label read-out are implemented, because they
define the shape the rest of the system codes against.  The parts the spec
lists as Milestone 2/3 work are deliberately *not* implemented and raise
``NotImplementedError`` rather than silently returning a plausible number:

* contextual calibration against a content-free control instance (section 10.3);
* multi-template Jensen-Shannon / KL prompt disagreement (section 10.4);
* the verification tiering policy (section 10.5).

Why the verifier sees so little: sequence likelihood of a candidate string is
dominated by tokenisation, name length and lexical frequency, so verification
is posed as a three-way classification over fixed labels instead.  The
generator's reasoning is withheld on purpose - a verifier that reads the draft
anchors on it.
"""

from __future__ import annotations

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import (
    LabelScoreRequest,
    LabelScoreResult,
    LMRuntime,
    entropy,
    softmax,
)
from cover_kbc.types import Query, VerificationLabel, VerificationResult

#: Label -> the continuation token whose logit is read.
LABEL_TOKENS = {
    VerificationLabel.VALID.value: "A",
    VerificationLabel.INVALID.value: "B",
    VerificationLabel.UNKNOWN.value: "C",
}

VERIFIER_SYSTEM_PROMPT = (
    "You verify exactly one candidate against the supplied relation definition. "
    "Do not assume the candidate is correct. Use only your internal parametric "
    "knowledge; you have no access to search or documents. Return exactly one label."
)

VERIFIER_TEMPLATE = """Subject: {subject}
Relation: {relation}
Definition:
{definition}

Candidate: {candidate}

A = VALID
B = INVALID
C = UNKNOWN

Return exactly one label."""

#: Content-free control instance for contextual calibration (spec section 10.3).
#: Kept here so the calibration implementation has a fixed, versioned probe.
CONTENT_FREE_CANDIDATE = "N/A"


def build_verifier_prompt(query: Query, contract: RelationContract, candidate: str) -> str:
    """Render the blind verifier prompt for one candidate.

    Carries the contract's definition *and* its hard-negative list, because the
    near misses are exactly what a verifier has to be told to reject.
    """
    return VERIFIER_TEMPLATE.format(
        subject=query.subject,
        relation=contract.relation,
        definition=contract.verifier_definition(),
        candidate=candidate,
    )


def read_labels(result: LabelScoreResult, *, temperature: float = 1.0) -> VerificationResult:
    """Convert raw label logits into an uncalibrated verification result.

    ``calibrated`` stays ``False``: this is the raw ``p_j`` of spec section
    10.2.  Logits are evidence, not truth, and nothing downstream should read
    this as a probability of correctness.
    """
    probabilities = softmax(result.logits, temperature=temperature)
    label = result.argmax_label()
    valid = probabilities.get(VerificationLabel.VALID.value)
    invalid = probabilities.get(VerificationLabel.INVALID.value)
    unknown = probabilities.get(VerificationLabel.UNKNOWN.value)

    others = [
        v
        for k, v in result.logits.items()
        if k != VerificationLabel.VALID.value
    ]
    margin = (
        result.logits[VerificationLabel.VALID.value] - max(others)
        if VerificationLabel.VALID.value in result.logits and others
        else None
    )

    return VerificationResult(
        candidate_key="",
        label=VerificationLabel(label),
        valid_prob=valid,
        invalid_prob=invalid,
        unknown_prob=unknown,
        raw_logits=dict(result.logits),
        calibrated=False,
        margin=margin,
        entropy=entropy(probabilities),
        model_id=result.model_id,
    )


def verify_candidate(
    runtime: LMRuntime,
    query: Query,
    contract: RelationContract,
    candidate_key: str,
    candidate_display: str,
) -> VerificationResult:
    """Run one blind three-way verification call.

    Returns an *uncalibrated* result.  Applying it to accept or reject a
    candidate is Milestone 2 work; Milestone 1 only records it on the graph.
    """
    prompt = build_verifier_prompt(query, contract, candidate_display)
    result = runtime.score_labels(
        LabelScoreRequest(
            prompt=prompt,
            labels=dict(LABEL_TOKENS),
            system_prompt=VERIFIER_SYSTEM_PROMPT,
            metadata={
                "view_id": "blind_verifier",
                "subject": query.subject,
                "relation": query.relation,
                "candidate": candidate_key,
            },
        )
    )
    verification = read_labels(result)
    verification.candidate_key = candidate_key
    return verification


def calibrate(*args, **kwargs):  # noqa: D401 - placeholder by design
    """Contextual calibration - **not implemented in Milestone 1**.

    Would subtract content-free control logits ``b_j`` from ``z_j`` before the
    softmax (spec section 10.3, default T = 1).
    """
    raise NotImplementedError(
        "Contextual calibration is Milestone 2 work; see spec section 10.3."
    )


def prompt_disagreement(*args, **kwargs):  # noqa: D401 - placeholder by design
    """Multi-template disagreement ``U_prompt(o)`` - **not implemented**.

    Would average KL divergences of per-template verifier distributions against
    their mean (spec section 10.4).
    """
    raise NotImplementedError(
        "Prompt-distribution disagreement is Milestone 2 work; see spec section 10.4."
    )
