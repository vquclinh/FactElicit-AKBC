"""Current relation refinement stack.

The package is an optional final inference stage.  When disabled it is not
constructed; when enabled it receives the completed prediction rows and may
refine the final `ObjectEntities`, recording separate refinement accounting.
Profile F1 is the unified active relation-refinement stack for the public
system.
"""

from cover_kbc.relation_refinement.config import RelationRefinementConfig
from cover_kbc.relation_refinement.stack import RelationRefinementStack, build_refinement_stack

__all__ = [
    "RelationRefinementConfig",
    "RelationRefinementStack",
    "build_refinement_stack",
]
