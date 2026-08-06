"""The verification layer.

Module 4 - the contract-aware, logit-calibrated **blind** verifier kernel, in
:mod:`cover_kbc.verification.blind`. Unchanged: it was moved into this package
without a byte of its content altered, and its prompt surface is still pinned
by sha256.

Module 17 - the **Specialist Verifier Suite**, in the ``specialist_*`` modules.
Proposal §13: *"A single generic A/B/C prompt is too coarse for all relations.
M17 preserves CoVe's blind-verification invariant, but each relation gets its
own verifier contract."* M17 owns the contracts; Module 4 remains the low-level
calibrated scoring engine, and M17 calls into it rather than beside it.

Every Module 4 name is re-exported here, so ``from cover_kbc.verification
import ...`` continues to mean exactly what it meant before the package split.
M17's names are re-exported lazily (PEP 562), so importing the kernel does not
pull in the specialist registry.
"""

from typing import Any

from cover_kbc.verification.blind import CONTENT_FREE_CANDIDATE as CONTENT_FREE_CANDIDATE
from cover_kbc.verification.blind import CONTENT_FREE_GATE_QUESTION as CONTENT_FREE_GATE_QUESTION
from cover_kbc.verification.blind import CONTENT_FREE_SUBJECT as CONTENT_FREE_SUBJECT
from cover_kbc.verification.blind import DISAGREEMENT_TEMPLATE_IDS as DISAGREEMENT_TEMPLATE_IDS
from cover_kbc.verification.blind import GATE_LABELS as GATE_LABELS
from cover_kbc.verification.blind import GATE_SYSTEM_PROMPT as GATE_SYSTEM_PROMPT
from cover_kbc.verification.blind import GATE_TEMPLATE as GATE_TEMPLATE
from cover_kbc.verification.blind import INVALID as INVALID
from cover_kbc.verification.blind import LABEL_TOKENS as LABEL_TOKENS
from cover_kbc.verification.blind import TEMPLATE_ADVERSARIAL as TEMPLATE_ADVERSARIAL
from cover_kbc.verification.blind import TEMPLATE_QUESTION as TEMPLATE_QUESTION
from cover_kbc.verification.blind import TEMPLATE_STANDARD as TEMPLATE_STANDARD
from cover_kbc.verification.blind import TEMPLATES as TEMPLATES
from cover_kbc.verification.blind import TEMPLATES_BY_ID as TEMPLATES_BY_ID
from cover_kbc.verification.blind import UNKNOWN as UNKNOWN
from cover_kbc.verification.blind import VALID as VALID
from cover_kbc.verification.blind import VERIFIER_SYSTEM_PROMPT as VERIFIER_SYSTEM_PROMPT
from cover_kbc.verification.blind import ContextualCalibrator as ContextualCalibrator
from cover_kbc.verification.blind import GateResult as GateResult
from cover_kbc.verification.blind import LabelEncoding as LabelEncoding
from cover_kbc.verification.blind import VerifierTemplate as VerifierTemplate
from cover_kbc.verification.blind import _control_cache_key as _control_cache_key
from cover_kbc.verification.blind import _label_signature as _label_signature
from cover_kbc.verification.blind import aggregate_verifications as aggregate_verifications
from cover_kbc.verification.blind import build_verifier_prompt as build_verifier_prompt
from cover_kbc.verification.blind import inspect_label_encoding as inspect_label_encoding
from cover_kbc.verification.blind import jensen_shannon_divergence as jensen_shannon_divergence
from cover_kbc.verification.blind import normalized_disagreement as normalized_disagreement
from cover_kbc.verification.blind import prompt_disagreement as prompt_disagreement
from cover_kbc.verification.blind import read_labels as read_labels
from cover_kbc.verification.blind import score_gate as score_gate
from cover_kbc.verification.blind import verify_candidate as verify_candidate
from cover_kbc.verification.blind import verify_multi_template as verify_multi_template

_SPECIALIST_TYPES = {
    "LabelOrder", "SpecialistVerificationRequest", "SpecialistVerificationResult",
    "SpecialistVerifierError", "SpecialistVerifierFamily", "SpecialistTemplateResult",
    "QuerySpecialistVerificationResult", "VerificationTargetKind",
    "VerifierBiasDiagnostics", "VerificationTarget", "VERIFICATION_VERSION",
    "TargetIneligible",
}
_SPECIALIST_CONTRACTS = {
    "SPECIALIST_CONTRACTS", "SpecialistVerifierContract", "specialist_contract",
    "specialist_family", "check_specialist_registry_consistency",
    "UnsupportedSpecialistRelation",
}
_SPECIALIST_PROMPTS = {
    "SPECIALIST_TEMPLATES", "SpecialistTemplateSpec", "render_label_block",
    "specialist_template", "specialist_template_ids",
}
_SPECIALIST_VERIFIER = {
    "SpecialistVerifier", "SpecialistVerifierConfig", "build_specialist_verifier",
    "verifiable_targets",
}

__all__ = [
    "CONTENT_FREE_CANDIDATE", "CONTENT_FREE_GATE_QUESTION",
    "CONTENT_FREE_SUBJECT", "ContextualCalibrator",
    "DISAGREEMENT_TEMPLATE_IDS", "GATE_LABELS", "GATE_SYSTEM_PROMPT",
    "GATE_TEMPLATE", "GateResult", "INVALID", "LABEL_TOKENS", "LabelEncoding",
    "LabelOrder", "QuerySpecialistVerificationResult", "SPECIALIST_CONTRACTS",
    "SPECIALIST_TEMPLATES", "SpecialistTemplateResult",
    "SpecialistTemplateSpec", "SpecialistVerificationRequest",
    "SpecialistVerificationResult", "SpecialistVerifier",
    "SpecialistVerifierConfig", "SpecialistVerifierContract",
    "SpecialistVerifierError", "SpecialistVerifierFamily", "TEMPLATES",
    "TEMPLATES_BY_ID", "TEMPLATE_ADVERSARIAL", "TEMPLATE_QUESTION",
    "TEMPLATE_STANDARD", "TargetIneligible", "UNKNOWN",
    "UnsupportedSpecialistRelation", "VALID", "VERIFICATION_VERSION",
    "VERIFIER_SYSTEM_PROMPT", "VerificationTarget", "VerificationTargetKind",
    "VerifierBiasDiagnostics", "VerifierTemplate", "_control_cache_key",
    "_label_signature", "aggregate_verifications",
    "build_specialist_verifier", "build_verifier_prompt",
    "check_specialist_registry_consistency", "inspect_label_encoding",
    "jensen_shannon_divergence", "normalized_disagreement",
    "prompt_disagreement", "read_labels", "render_label_block", "score_gate",
    "specialist_contract", "specialist_family", "specialist_template",
    "specialist_template_ids", "verifiable_targets", "verify_candidate",
    "verify_multi_template"
]


def __getattr__(name: str) -> Any:
    for names, module in (
        (_SPECIALIST_TYPES, "specialist_types"),
        (_SPECIALIST_CONTRACTS, "specialist_contracts"),
        (_SPECIALIST_PROMPTS, "specialist_prompts"),
        (_SPECIALIST_VERIFIER, "specialist_verifier"),
    ):
        if name in names:
            import importlib

            return getattr(
                importlib.import_module(f"cover_kbc.verification.{module}"), name
            )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
