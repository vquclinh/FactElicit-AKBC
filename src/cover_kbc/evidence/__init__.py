"""The evidence plane.

Module 3 - atomic normalisation and the Candidate-Facet Evidence Graph.
Module 16 - the Atomic Consensus Engine, a read-only projection of it.

M16's names are re-exported lazily so that importing the graph does not drag in
the consensus engine, its adapters, or the four specialist result types they
read - the same PEP 562 pattern Module 11 uses in ``query_intelligence``.
"""

from typing import Any

from cover_kbc.evidence.graph import EvidenceGraph, apply_hard_contract_rules, build_graph

__all__ = [
    "AtomicConsensusEngine",
    "CandidateConsensusState",
    "ConsensusConfig",
    "ConsensusError",
    "ConsensusEvidenceEvent",
    "ConsensusProvenanceError",
    "EvidenceGraph",
    "EvidencePlane",
    "EvidenceRole",
    "GroupSupport",
    "NullConsensusState",
    "NumericClusterConsensus",
    "QueryConsensusResult",
    "apply_hard_contract_rules",
    "build_consensus_engine",
    "build_graph",
]

_CONSENSUS = {"AtomicConsensusEngine", "ConsensusConfig", "build_consensus_engine"}
_CONSENSUS_TYPES = {
    "CandidateConsensusState", "ConsensusError", "ConsensusEvidenceEvent",
    "ConsensusProvenanceError", "EvidencePlane", "EvidenceRole", "GroupSupport",
    "NullConsensusState", "NumericClusterConsensus", "QueryConsensusResult",
}


def __getattr__(name: str) -> Any:
    if name in _CONSENSUS:
        from cover_kbc.evidence import consensus

        return getattr(consensus, name)
    if name in _CONSENSUS_TYPES:
        from cover_kbc.evidence import consensus_types

        return getattr(consensus_types, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
