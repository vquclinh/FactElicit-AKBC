"""Module 4 - the contract-aware, logit-calibrated blind verifier.

The verifier is *blind*: it sees the subject, the relation contract's exact
rules, and one candidate.  It never sees the generator's reasoning, its draft,
or the other candidates - a verifier that reads the draft anchors on it
(spec section 10.2, following Chain-of-Verification).

Why labels rather than the candidate string's likelihood: sequence likelihood is
dominated by tokenisation, name length and lexical frequency, so P("New York
Stock Exchange") is not an estimate of factual correctness.  Verification is
therefore posed as three-way classification over fixed labels.

**These are calibrated verifier-label distributions, not probabilities of
factual truth.**  Nothing downstream may read ``p_valid`` as "the probability
this fact is true".

Label tokenisation is asserted, never assumed.  If A/B/C are not each a single
token for the active tokenizer, the verifier falls back to full sequence
log-likelihood over the label strings rather than silently comparing first
tokens - which would make "A" and "Australia" indistinguishable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from cover_kbc.contracts.base import RelationContract
from cover_kbc.models.base import (
    LabelScoreRequest,
    LabelScoreResult,
    LMRuntime,
    entropy,
    softmax,
)
from cover_kbc.types import Query, VerificationLabel, VerificationResult

#: Canonical label -> continuation string. Single ASCII letters maximise the
#: chance of single-token encoding across tokenizer families.
LABEL_TOKENS: dict[str, str] = {
    VerificationLabel.VALID.value: "A",
    VerificationLabel.INVALID.value: "B",
    VerificationLabel.UNKNOWN.value: "C",
}

VALID = VerificationLabel.VALID.value
INVALID = VerificationLabel.INVALID.value
UNKNOWN = VerificationLabel.UNKNOWN.value

VERIFIER_SYSTEM_PROMPT = (
    "You verify exactly one candidate against the supplied relation definition. "
    "Do not assume the candidate is correct. Use only your internal parametric "
    "knowledge; you have no access to search or documents. Return exactly one label."
)

#: The content-free control instance for contextual calibration (§10.3).
#: It carries no subject and no candidate, so whatever the model prefers here is
#: template bias rather than knowledge.
CONTENT_FREE_SUBJECT = "N/A"
CONTENT_FREE_CANDIDATE = "N/A"


# --------------------------------------------------------------------------
# Verifier templates (§8: a small set of semantically equivalent phrasings)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifierTemplate:
    """One phrasing of the blind verification question.

    Multiple templates exist so prompt-distribution disagreement can be
    measured (spec section 10.4).  They must be semantically equivalent: any
    difference in the resulting distribution is instability, not new evidence.
    """

    template_id: str
    body: str
    adversarial: bool = False

    def render(self, *, subject: str, relation: str, definition: str, candidate: str) -> str:
        return self.body.format(
            subject=subject, relation=relation, definition=definition, candidate=candidate
        ).strip()


_LABEL_BLOCK = "A = VALID\nB = INVALID\nC = UNKNOWN\n\nReturn exactly one label."

TEMPLATE_STANDARD = VerifierTemplate(
    template_id="verify_standard_v1",
    body=(
        "Subject: {subject}\n"
        "Relation: {relation}\n"
        "Definition:\n{definition}\n\n"
        "Candidate: {candidate}\n\n" + _LABEL_BLOCK
    ),
)

TEMPLATE_QUESTION = VerifierTemplate(
    template_id="verify_question_v1",
    body=(
        "Definition of the relation '{relation}':\n{definition}\n\n"
        "Question: under that definition exactly, is \"{candidate}\" a correct "
        "object for the subject \"{subject}\"?\n\n" + _LABEL_BLOCK
    ),
)

TEMPLATE_ADVERSARIAL = VerifierTemplate(
    template_id="verify_adversarial_v1",
    adversarial=True,
    body=(
        "Subject: {subject}\n"
        "Relation: {relation}\n"
        "Definition:\n{definition}\n\n"
        "Candidate: {candidate}\n\n"
        "This candidate is suspected of being a near miss rather than a correct "
        "object. Check it against every exclusion above before answering. "
        "Answer B if it matches any exclusion, and C if you do not actually know.\n\n"
        + _LABEL_BLOCK
    ),
)

#: Order matters: index 0 is the default single-call template.
TEMPLATES: tuple[VerifierTemplate, ...] = (
    TEMPLATE_STANDARD,
    TEMPLATE_QUESTION,
    TEMPLATE_ADVERSARIAL,
)

TEMPLATES_BY_ID: dict[str, VerifierTemplate] = {t.template_id: t for t in TEMPLATES}

#: Templates used when measuring prompt disagreement. Deliberately two, not
#: many: verification is the expensive part of the budget.
DISAGREEMENT_TEMPLATE_IDS: tuple[str, ...] = (
    TEMPLATE_STANDARD.template_id,
    TEMPLATE_QUESTION.template_id,
)


def build_verifier_prompt(
    query: Query,
    contract: RelationContract,
    candidate: str,
    template: VerifierTemplate = TEMPLATE_STANDARD,
) -> str:
    """Render a blind verifier prompt.

    Carries the contract's positive rules *and* its hard-negative list, because
    the near misses are exactly what a verifier must be told to reject.
    """
    return template.render(
        subject=query.subject,
        relation=contract.relation,
        definition=contract.verifier_definition(),
        candidate=candidate,
    )


# --------------------------------------------------------------------------
# Label tokenisation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelEncoding:
    """How the active tokenizer encodes the verifier's label set.

    ``single_token`` decides the scoring strategy; it is asserted against the
    real tokenizer, never assumed.
    """

    labels: Mapping[str, str]
    token_ids: Mapping[str, tuple[int, ...]]
    single_token: bool

    @property
    def strategy(self) -> str:
        return "next_token_logits" if self.single_token else "sequence_loglikelihood"

    def to_json(self) -> dict:
        return {
            "labels": dict(self.labels),
            "token_ids": {k: list(v) for k, v in self.token_ids.items()},
            "single_token": self.single_token,
            "strategy": self.strategy,
        }


def inspect_label_encoding(tokenizer, labels: Mapping[str, str] = LABEL_TOKENS) -> LabelEncoding:
    """Encode each label and report whether all are single tokens.

    Leading-space variants are *not* tried here; the prompt ends with a newline
    so the model emits the bare letter.  Backends that need a different
    convention should supply their own label map.
    """
    token_ids: dict[str, tuple[int, ...]] = {}
    for label, continuation in labels.items():
        ids = tuple(tokenizer.encode(continuation, add_special_tokens=False))
        if not ids:
            raise ValueError(
                f"Label {label!r} maps to {continuation!r}, which encodes to zero tokens"
            )
        token_ids[label] = ids
    return LabelEncoding(
        labels=dict(labels),
        token_ids=token_ids,
        single_token=all(len(ids) == 1 for ids in token_ids.values()),
    )


# --------------------------------------------------------------------------
# Contextual calibration (§10.3)
# --------------------------------------------------------------------------


def _control_cache_key(
    model_id: str, relation: str, template_id: str, decode_identity: str
) -> tuple[str, str, str, str]:
    return (model_id, relation, template_id, decode_identity)


@dataclass
class ContextualCalibrator:
    """Content-free control logits ``b_j``, cached and deterministic.

    Spec section 10.3: run the *same* verifier template with a content-free
    instance, then subtract the resulting bias logits before the softmax::

        z~_j = z_j - b_j          p~ = softmax(z~ / T)

    ``T = 1`` by default.  No calibrator is fitted - this is arithmetic on
    inference-time outputs, not a trained component, which is what keeps it
    inside the no-training rule.

    The cache is keyed by (model, relation, template, decode identity) so one
    control call is amortised over every candidate for that relation.
    """

    temperature: float = 1.0
    _controls: dict[tuple[str, str, str, str], dict[str, float]] = field(default_factory=dict)
    calls: int = 0

    def control_logits(
        self,
        runtime: LMRuntime,
        contract: RelationContract,
        template: VerifierTemplate,
        *,
        decode_identity: str = "default",
    ) -> dict[str, float]:
        """Fetch (or compute once) the content-free control distribution."""
        key = _control_cache_key(
            runtime.spec.model_id, contract.relation, template.template_id, decode_identity
        )
        if key not in self._controls:
            prompt = template.render(
                subject=CONTENT_FREE_SUBJECT,
                relation=contract.relation,
                definition=contract.verifier_definition(),
                candidate=CONTENT_FREE_CANDIDATE,
            )
            result = runtime.score_labels(
                LabelScoreRequest(
                    prompt=prompt,
                    labels=dict(LABEL_TOKENS),
                    system_prompt=VERIFIER_SYSTEM_PROMPT,
                    metadata={
                        "view_id": "verifier_control",
                        "subject": CONTENT_FREE_SUBJECT,
                        "relation": contract.relation,
                        "template_id": template.template_id,
                    },
                )
            )
            self.calls += 1
            self._controls[key] = dict(result.logits)
        return dict(self._controls[key])

    def gate_control_logits(self, runtime: LMRuntime, relation: str) -> dict[str, float]:
        """Content-free control for the YES/NO/UNKNOWN gate template."""
        key = _control_cache_key(runtime.spec.model_id, relation, "calibrated_gate", "default")
        if key not in self._controls:
            from cover_kbc.verification import (  # local import: same module, avoids cycle at def time
                CONTENT_FREE_GATE_QUESTION,
                GATE_LABELS,
                GATE_SYSTEM_PROMPT,
                GATE_TEMPLATE,
            )

            result = runtime.score_labels(
                LabelScoreRequest(
                    prompt=GATE_TEMPLATE.format(question=CONTENT_FREE_GATE_QUESTION),
                    labels=dict(GATE_LABELS),
                    system_prompt=GATE_SYSTEM_PROMPT,
                    metadata={"view_id": "gate_control", "relation": relation},
                )
            )
            self.calls += 1
            self._controls[key] = dict(result.logits)
        return dict(self._controls[key])

    def apply(
        self, logits: Mapping[str, float], control: Mapping[str, float] | None
    ) -> tuple[dict[str, float], bool]:
        """Return ``(calibrated_logits, calibrated_flag)``."""
        if not control:
            return dict(logits), False
        return {k: v - control.get(k, 0.0) for k, v in logits.items()}, True

    def cached_controls(self) -> dict[str, dict[str, float]]:
        """Snapshot of the control cache, for the run trace."""
        return {"|".join(k): dict(v) for k, v in self._controls.items()}


# --------------------------------------------------------------------------
# Reading label scores
# --------------------------------------------------------------------------


def read_labels(
    result: LabelScoreResult,
    *,
    control: Mapping[str, float] | None = None,
    temperature: float = 1.0,
    calibrator: ContextualCalibrator | None = None,
) -> VerificationResult:
    """Convert raw label logits into a (optionally calibrated) result.

    Reports raw and calibrated logits side by side so a trace can show exactly
    what calibration changed.
    """
    calibrator = calibrator or ContextualCalibrator(temperature=temperature)
    raw = dict(result.logits)
    calibrated_logits, calibrated = calibrator.apply(raw, control)
    probabilities = softmax(calibrated_logits, temperature=calibrator.temperature)

    label = min(probabilities, key=lambda k: (-probabilities[k], k))
    others = [v for k, v in calibrated_logits.items() if k != VALID]
    margin = calibrated_logits[VALID] - max(others) if VALID in calibrated_logits and others else None

    return VerificationResult(
        candidate_key="",
        label=VerificationLabel(label),
        valid_prob=probabilities.get(VALID),
        invalid_prob=probabilities.get(INVALID),
        unknown_prob=probabilities.get(UNKNOWN),
        raw_logits=raw,
        calibrated_logits=calibrated_logits,
        bias_logits=dict(control) if control else None,
        calibrated=calibrated,
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
    *,
    template: VerifierTemplate = TEMPLATE_STANDARD,
    calibrator: ContextualCalibrator | None = None,
    use_calibration: bool = True,
    decode_identity: str = "default",
) -> VerificationResult:
    """Run one blind three-way verification call for one candidate."""
    prompt = build_verifier_prompt(query, contract, candidate_display, template)
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
                "template_id": template.template_id,
            },
        )
    )

    control = None
    if use_calibration and calibrator is not None:
        control = calibrator.control_logits(
            runtime, contract, template, decode_identity=decode_identity
        )

    verification = read_labels(result, control=control, calibrator=calibrator)
    verification.candidate_key = candidate_key
    verification.template_id = template.template_id
    verification.record_id = f"verify:{template.template_id}:{candidate_key}"
    return verification


# --------------------------------------------------------------------------
# Prompt-distribution disagreement (§10.4)
# --------------------------------------------------------------------------


def _kl(p: Mapping[str, float], q: Mapping[str, float], epsilon: float = 1e-12) -> float:
    return sum(
        pv * math.log((pv + epsilon) / (q.get(k, 0.0) + epsilon))
        for k, pv in p.items()
        if pv > 0
    )


def jensen_shannon_divergence(distributions: Sequence[Mapping[str, float]]) -> float:
    """Generalised Jensen-Shannon divergence, in nats.

    Symmetric and bounded by ``log(m)``, unlike the raw mean-KL of spec section
    10.4 - which is why it is preferred here: an unbounded score cannot be
    compared against a fixed configured threshold.
    """
    valid = [d for d in distributions if d]
    if len(valid) < 2:
        return 0.0
    keys = {k for d in valid for k in d}
    mean = {k: sum(d.get(k, 0.0) for d in valid) / len(valid) for k in keys}
    return max(0.0, sum(_kl(d, mean) for d in valid) / len(valid))


def normalized_disagreement(distributions: Sequence[Mapping[str, float]]) -> float:
    """JS divergence rescaled to ``[0, 1]`` by its ``log(m)`` maximum."""
    valid = [d for d in distributions if d]
    if len(valid) < 2:
        return 0.0
    ceiling = math.log(len(valid))
    if ceiling <= 0:
        return 0.0
    return min(1.0, jensen_shannon_divergence(valid) / ceiling)


def verify_multi_template(
    runtime: LMRuntime,
    query: Query,
    contract: RelationContract,
    candidate_key: str,
    candidate_display: str,
    *,
    template_ids: Sequence[str] = DISAGREEMENT_TEMPLATE_IDS,
    calibrator: ContextualCalibrator | None = None,
    use_calibration: bool = True,
) -> tuple[list[VerificationResult], float]:
    """Verify one candidate under several equivalent templates.

    Returns the per-template results and the normalised disagreement.  High
    disagreement leaves a candidate unresolved even when one template greedily
    emits VALID (spec section 10.4).
    """
    results = [
        verify_candidate(
            runtime,
            query,
            contract,
            candidate_key,
            candidate_display,
            template=TEMPLATES_BY_ID[template_id],
            calibrator=calibrator,
            use_calibration=use_calibration,
        )
        for template_id in template_ids
    ]
    distributions = [
        {
            VALID: r.valid_prob or 0.0,
            INVALID: r.invalid_prob or 0.0,
            UNKNOWN: r.unknown_prob or 0.0,
        }
        for r in results
    ]
    disagreement = normalized_disagreement(distributions)
    for result in results:
        result.prompt_disagreement = disagreement
    return results, disagreement


# --------------------------------------------------------------------------
# Calibrated existence gates (Milestone-2 requirement 10)
# --------------------------------------------------------------------------

#: Gate label set. Same A/B/C shape as the verifier so one tokenisation check
#: covers both.
GATE_LABELS: dict[str, str] = {"YES": "A", "NO": "B", "UNKNOWN": "C"}

GATE_SYSTEM_PROMPT = (
    "You answer a single factual yes/no question using only your internal "
    "knowledge. You have no access to search or documents. Answer C = UNKNOWN "
    "whenever you are not sure. Return exactly one label."
)

GATE_TEMPLATE = """Question: {question}

A = YES
B = NO
C = UNKNOWN

Return exactly one label."""

#: Content-free control question for gate calibration.
CONTENT_FREE_GATE_QUESTION = "Is N/A true of N/A?"


@dataclass
class GateResult:
    """Calibrated outcome of an existence gate.

    ``decision`` is deliberately three-valued.  A gate that cannot distinguish
    NO from UNKNOWN must not be allowed to force an empty prediction: that
    turns one uncertain token into a guaranteed zero-recall answer.
    """

    question: str
    p_yes: float | None = None
    p_no: float | None = None
    p_unknown: float | None = None
    raw_logits: dict[str, float] | None = None
    calibrated_logits: dict[str, float] | None = None
    bias_logits: dict[str, float] | None = None
    calibrated: bool = False
    margin: float | None = None
    entropy: float | None = None
    decision: str = "UNKNOWN"
    model_id: str = ""

    @property
    def is_confident_negative(self) -> bool:
        return self.decision == "NO"

    def to_json(self) -> dict:
        return {
            "question": self.question,
            "p_yes": self.p_yes,
            "p_no": self.p_no,
            "p_unknown": self.p_unknown,
            "raw_logits": self.raw_logits,
            "calibrated_logits": self.calibrated_logits,
            "bias_logits": self.bias_logits,
            "calibrated": self.calibrated,
            "margin": self.margin,
            "entropy": self.entropy,
            "decision": self.decision,
            "model_id": self.model_id,
        }


def score_gate(
    runtime: LMRuntime,
    question: str,
    *,
    relation: str = "",
    subject: str = "",
    calibrator: ContextualCalibrator | None = None,
    use_calibration: bool = True,
    min_margin: float = 1.0,
    min_prob: float = 0.5,
) -> GateResult:
    """Ask one yes/no/unknown question and read calibrated label logits.

    ``decision`` becomes ``"NO"`` only when NO is the argmax *and* clears both
    ``min_margin`` (logit gap over the runner-up) and ``min_prob``.  Everything
    else is ``"YES"`` or ``"UNKNOWN"``, both of which let discovery continue.
    """
    prompt = GATE_TEMPLATE.format(question=question)
    result = runtime.score_labels(
        LabelScoreRequest(
            prompt=prompt,
            labels=dict(GATE_LABELS),
            system_prompt=GATE_SYSTEM_PROMPT,
            metadata={
                "view_id": "calibrated_gate",
                "subject": subject,
                "relation": relation,
            },
        )
    )

    control: dict[str, float] | None = None
    if use_calibration and calibrator is not None:
        control = calibrator.gate_control_logits(runtime, relation)

    calibrator = calibrator or ContextualCalibrator()
    calibrated_logits, calibrated = calibrator.apply(dict(result.logits), control)
    probabilities = softmax(calibrated_logits, temperature=calibrator.temperature)

    argmax = min(probabilities, key=lambda k: (-probabilities[k], k))
    others = [v for k, v in calibrated_logits.items() if k != argmax]
    margin = calibrated_logits[argmax] - max(others) if others else 0.0

    decision = argmax
    if argmax == "NO" and not (margin >= min_margin and probabilities.get("NO", 0.0) >= min_prob):
        # Not confident enough to force an empty answer.
        decision = "UNKNOWN"

    return GateResult(
        question=question,
        p_yes=probabilities.get("YES"),
        p_no=probabilities.get("NO"),
        p_unknown=probabilities.get("UNKNOWN"),
        raw_logits=dict(result.logits),
        calibrated_logits=calibrated_logits,
        bias_logits=control,
        calibrated=calibrated,
        margin=margin,
        entropy=entropy(probabilities),
        decision=decision,
        model_id=result.model_id,
    )


def prompt_disagreement(results: Sequence[VerificationResult]) -> float:
    """Normalised prompt-distribution disagreement over existing results.

    ``U_prompt(o)`` in the candidate score.  Zero when fewer than two templates
    were run - absence of a measurement is not evidence of agreement, so
    callers must not read 0.0 as "templates agreed" without checking count.
    """
    distributions = [
        {
            VALID: r.valid_prob or 0.0,
            INVALID: r.invalid_prob or 0.0,
            UNKNOWN: r.unknown_prob or 0.0,
        }
        for r in results
        if r.valid_prob is not None
    ]
    return normalized_disagreement(distributions)


def aggregate_verifications(results: Sequence[VerificationResult]) -> VerificationResult | None:
    """Merge multi-template results into one, by averaging the distributions."""
    usable = [r for r in results if r.valid_prob is not None]
    if not usable:
        return results[0] if results else None
    n = len(usable)
    mean = {
        VALID: sum(r.valid_prob or 0.0 for r in usable) / n,
        INVALID: sum(r.invalid_prob or 0.0 for r in usable) / n,
        UNKNOWN: sum(r.unknown_prob or 0.0 for r in usable) / n,
    }
    label = min(mean, key=lambda k: (-mean[k], k))
    head = usable[0]
    return VerificationResult(
        candidate_key=head.candidate_key,
        label=VerificationLabel(label),
        valid_prob=mean[VALID],
        invalid_prob=mean[INVALID],
        unknown_prob=mean[UNKNOWN],
        raw_logits=head.raw_logits,
        calibrated_logits=head.calibrated_logits,
        bias_logits=head.bias_logits,
        calibrated=all(r.calibrated for r in usable),
        margin=sum(r.margin or 0.0 for r in usable) / n,
        entropy=entropy(mean),
        prompt_disagreement=normalized_disagreement(
            [
                {
                    VALID: r.valid_prob or 0.0,
                    INVALID: r.invalid_prob or 0.0,
                    UNKNOWN: r.unknown_prob or 0.0,
                }
                for r in usable
            ]
        ),
        model_id=head.model_id,
        record_id=head.record_id,
        template_id="aggregate",
        num_templates=n,
    )
