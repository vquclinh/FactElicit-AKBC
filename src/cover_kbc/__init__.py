"""COVER-KBC v2 - Coverage-guided Open-set Verification and Elicitation.

Implementation of the architecture specified in
``COVER_KBC_V2_ARCHITECTURE_SPEC.pdf`` for the AKBC Shared Task 2026.

Milestone 1 scope: reproducible benchmark foundation plus the core typed
interfaces (contracts, evidence graph, elicitation views, model runtime).
Advanced inference logic (logit calibration, RCSE, adaptive control) is
deliberately left as interfaces.
"""

__version__ = "0.1.0"

RELATIONS = (
    "countryLandBordersCountry",
    "personHasCityOfDeath",
    "hasCapacity",
    "awardWonBy",
    "companyTradesAtStockExchange",
    "hasArea",
)
