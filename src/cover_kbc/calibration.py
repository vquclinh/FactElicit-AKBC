"""Which configuration values may ever be fitted to data, and which may not.

A configuration value being *settable* does not make it a calibration degree of
freedom. The proposal requires the number of free parameters to stay small
precisely because relations such as ``awardWonBy`` have ten validation
examples; treating every YAML number as a search dimension would overfit them
instantly.

So every tunable carries a category, and only one category is ever fitted:

``SEMANTIC``
    A schema or programme fact. ``max_objects = 1`` for ``NULL_SINGLE`` is what
    the relation *means*, not a knob.
``GUARD``
    A numerical safety constant - an epsilon guarding a division or a log.
``COST``
    A runtime quantity that should eventually be **measured**, not optimised:
    what an action actually costs in calls and tokens.
``STRUCTURAL``
    A human-designed architecture constant. Changing it changes the
    architecture, and that is a design decision requiring evidence, not a
    coordinate search.
``CALIBRATABLE``
    The only category a calibration run may fit, using train or a documented
    internal split.

This module is the machine-readable inventory. It is small on purpose: a table
plus tests, not a configuration framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    SEMANTIC = "semantic"
    GUARD = "guard"
    COST = "cost"
    STRUCTURAL = "structural"
    CALIBRATABLE = "calibratable"


@dataclass(frozen=True)
class Parameter:
    """One configuration value and what may be done to it."""

    name: str
    owner: str
    category: Category
    meaning: str
    #: The load-bearing *decision* this value controls. Several parameters may
    #: share one - a global fallback and its per-relation override are two
    #: knobs on one decision, not two independent dimensions. Calibration
    #: counts decisions, not knobs.
    decision: str = ""

    @property
    def calibratable(self) -> bool:
        return self.category is Category.CALIBRATABLE


def _p(name, owner, category, meaning, decision=""):
    return Parameter(name=name, owner=owner, category=category, meaning=meaning,
                     decision=decision or f"{owner}.{name}")


#: Splits a calibration run may read. Val is for one final measurement, never
#: for selection; test is never touched at all.
ALLOWED_CALIBRATION_SPLITS = frozenset({"train", "internal"})
FORBIDDEN_CALIBRATION_SPLITS = frozenset({"val", "validation", "test"})


PARAMETERS: tuple[Parameter, ...] = (
    # -- Module 5: evidence and acceptance -----------------------------------
    _p("alpha_support", "ScoringConfig", Category.STRUCTURAL,
       "weight of F(o); the relative shape of S(o) is an architecture choice"),
    _p("beta_logit", "ScoringConfig", Category.STRUCTURAL, "weight of L(o)"),
    _p("gamma_cross_model", "ScoringConfig", Category.STRUCTURAL, "weight of X(o)"),
    _p("delta_contradiction", "ScoringConfig", Category.STRUCTURAL, "weight of C(o)"),
    _p("eta_disagreement", "ScoringConfig", Category.STRUCTURAL, "weight of U(o)"),
    _p("accept_score", "ScoringConfig", Category.CALIBRATABLE,
       "the precision/recall operating point of the whole system",
       decision="acceptance_operating_point"),
    _p("min_valid_prob", "ScoringConfig", Category.CALIBRATABLE,
       "calibrated P(VALID) below which a verified candidate is dropped",
       decision="verifier_operating_point"),
    _p("auto_accept_support", "ScoringConfig", Category.STRUCTURAL,
       "mechanisms needed to skip verification; a policy shape, not a fitted value"),
    _p("verify_max_support", "ScoringConfig", Category.STRUCTURAL, "verification tier boundary"),
    _p("adversarial_disagreement", "ScoringConfig", Category.STRUCTURAL,
       "disagreement that escalates to the adversarial template"),
    _p("adversarial_max_support", "ScoringConfig", Category.STRUCTURAL,
       "support at or below which a near-miss-prone relation escalates"),
    _p("adversarial_on_declared_near_misses", "ScoringConfig", Category.STRUCTURAL,
       "feature flag for that escalation rule"),
    _p("drop_on_unknown", "ScoringConfig", Category.STRUCTURAL,
       "whether UNKNOWN blocks acceptance; an epistemic policy"),
    _p("force_global_verification_policy", "ScoringConfig", Category.STRUCTURAL,
       "named ablation override, off by default"),
    _p("logit_clip", "ScoringConfig", Category.STRUCTURAL,
       "bound on L(o) so one extreme logit cannot dominate S(o)"),
    _p("logit_epsilon", "ScoringConfig", Category.GUARD, "guards log(0) in the log-odds"),
    _p("optional_views_available", "ScoringConfig", Category.SEMANTIC,
       "derived from the run mode; not settable independently"),

    # -- Module 6: RCSE -------------------------------------------------------
    _p("saturation_window", "RCSEConfig", Category.STRUCTURAL, "actions in the saturation window"),
    _p("yield_epsilon", "RCSEConfig", Category.GUARD, "guards the marginal-yield denominator"),
    _p("yield_scale", "RCSEConfig", Category.COST,
       "verified yield per 1k tokens at which the signal saturates; measurable"),
    _p("numeric_dispersion_threshold", "RCSEConfig", Category.STRUCTURAL,
       "rMAD at which a numeric cluster reads as dispersed"),
    _p("competitor_support_ratio", "RCSEConfig", Category.STRUCTURAL,
       "share of the dominant cluster a rival must reach to count"),
    _p("w_yield", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_saturation", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_unresolved", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_facet_gap", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_mechanism_gap", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_disagreement", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_instability", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_inclusion", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_gate", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_competition", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("w_dispersion", "RCSEConfig", Category.STRUCTURAL, "residual component weight"),
    _p("stop_threshold", "RCSEConfig", Category.STRUCTURAL,
       "RCSE's own default; the operative one is the controller's residual_stop"),

    # -- Module 7: controller -------------------------------------------------
    _p("cost_run_view", "ControllerConfig", Category.COST, "measurable action cost prior"),
    _p("cost_reverse_check", "ControllerConfig", Category.COST, "measurable action cost prior"),
    _p("cost_resample", "ControllerConfig", Category.COST, "measurable action cost prior"),
    _p("cost_verify", "ControllerConfig", Category.COST, "measurable action cost prior"),
    _p("cost_adversarial_verify", "ControllerConfig", Category.COST, "measurable action cost prior"),
    _p("cost_cross_model", "ControllerConfig", Category.COST, "measurable action cost prior"),
    _p("untried_yield_prior", "ControllerConfig", Category.COST,
       "expected yield of an untried mechanism; measurable from train logs"),
    _p("mandatory_gap_relevance", "ControllerConfig", Category.STRUCTURAL,
       "mandatory work outranks optional; a policy shape"),
    _p("optional_gap_scale", "ControllerConfig", Category.STRUCTURAL,
       "keeps optional gaps below the mandatory value"),
    _p("covered_mechanism_redundancy", "ControllerConfig", Category.STRUCTURAL,
       "redundancy of a mechanism already covered"),
    _p("resample_redundancy", "ControllerConfig", Category.STRUCTURAL, "redundancy floor of a repeat"),
    _p("repeat_redundancy_step", "ControllerConfig", Category.STRUCTURAL, "growth per repeat"),
    _p("indirect_uncertainty", "ControllerConfig", Category.STRUCTURAL,
       "how much acquisition counts toward resolving uncertainty"),
    _p("adversarial_uncertainty_bonus", "ControllerConfig", Category.STRUCTURAL,
       "extra U_t for the adversarial template"),
    _p("reverify_redundancy", "ControllerConfig", Category.STRUCTURAL, "penalty per prior verification"),
    _p("alpha_yield", "ControllerConfig", Category.STRUCTURAL, "action-score weight"),
    _p("beta_gap", "ControllerConfig", Category.STRUCTURAL, "action-score weight"),
    _p("gamma_uncertainty", "ControllerConfig", Category.STRUCTURAL, "action-score weight"),
    _p("lambda_cost", "ControllerConfig", Category.STRUCTURAL, "action-score weight"),
    _p("rho_redundancy", "ControllerConfig", Category.STRUCTURAL, "action-score weight"),
    _p("residual_stop", "ControllerConfig", Category.CALIBRATABLE,
       "how much residual search need justifies more compute - the adaptive "
       "compute/accuracy trade-off itself", decision="adaptive_stopping_point"),
    _p("saturation_patience", "ControllerConfig", Category.STRUCTURAL,
       "consecutive fruitless actions tolerated"),
    _p("stability_threshold", "ControllerConfig", Category.STRUCTURAL, "Jaccard counted as stable"),
    _p("verify_first_unresolved", "ControllerConfig", Category.STRUCTURAL,
       "unresolved mass at which verification is preferred"),
    _p("verify_first_bonus", "ControllerConfig", Category.STRUCTURAL,
       "score added while that preference holds"),
    _p("honor_contract_stopping", "ControllerConfig", Category.SEMANTIC,
       "the contract owns relation-specific stopping; off is an ablation"),

    # -- Module 8: selector ---------------------------------------------------
    _p("capacity_support_ratio", "SelectionConfig", Category.CALIBRATABLE,
       "how much support a higher capacity cluster needs to beat the dominant "
       "one - hasCapacity's precision/recall trade-off",
       decision="capacity_cluster_preference"),
    _p("capacity_trust_verified", "SelectionConfig", Category.STRUCTURAL,
       "whether a VALID verdict alone qualifies a cluster"),

    # -- Contract-level (Module 0) -------------------------------------------
    _p("max_objects", "SelectionPolicy", Category.SEMANTIC, "programme cardinality"),
    _p("numeric_integer_only", "SelectionPolicy", Category.SEMANTIC, "output schema fact"),
    _p("numeric_target_unit", "SelectionPolicy", Category.SEMANTIC, "relation semantics"),
    _p("numeric_cluster_threshold", "SelectionPolicy", Category.CALIBRATABLE,
       "cluster diameter defining one coherent numeric value",
       decision="numeric_cluster_diameter"),
    _p("min_independent_support", "SelectionPolicy", Category.STRUCTURAL,
       "floor on independent mechanisms before acceptance"),
    _p("accept_valid_prob", "VerificationPolicy", Category.CALIBRATABLE,
       "per-relation refinement of the verifier operating point; relations "
       "genuinely differ here (death and stock want 0.60, borders 0.50)",
       decision="verifier_operating_point"),
    _p("auto_accept_independent_support", "VerificationPolicy", Category.STRUCTURAL,
       "mechanisms that skip verification for this relation"),
    _p("drop_on_unknown", "VerificationPolicy", Category.STRUCTURAL, "relation UNKNOWN policy"),
    _p("adversarial_classes", "VerificationPolicy", Category.SEMANTIC,
       "the relation's declared near misses"),
    _p("max_calls", "StoppingPolicy", Category.COST, "hard neural-call ceiling for this relation"),
    _p("max_generated_tokens", "StoppingPolicy", Category.COST, "hard token ceiling"),
    _p("residual_stop_threshold", "StoppingPolicy", Category.CALIBRATABLE,
       "per-relation refinement of the adaptive stopping point",
       decision="adaptive_stopping_point"),
    _p("saturation_patience", "StoppingPolicy", Category.STRUCTURAL, "relation override"),
    _p("stability_threshold", "StoppingPolicy", Category.STRUCTURAL, "relation override"),
    _p("notes", "StoppingPolicy", Category.SEMANTIC, "documentation"),
)


def by_category(category: Category) -> tuple[Parameter, ...]:
    return tuple(p for p in PARAMETERS if p.category is category)


def calibratable() -> tuple[Parameter, ...]:
    """The only parameters a calibration run may fit.

    Deliberately few, and deliberately *global* wherever the relations do not
    genuinely differ: six degrees of freedom is plausibly estimable from the
    available train data, whereas per-relation variants of each would not be.
    """
    return by_category(Category.CALIBRATABLE)


def calibratable_decisions() -> tuple[str, ...]:
    """The true degrees of freedom: distinct decisions, not knob count.

    A global fallback and its per-relation override are one decision. Counting
    them separately would tell a calibration tool it has more freedom than the
    architecture actually offers - and than the data can support.
    """
    return tuple(sorted({p.decision for p in calibratable()}))


def summary() -> dict[str, int]:
    return {c.value: len(by_category(c)) for c in Category}
