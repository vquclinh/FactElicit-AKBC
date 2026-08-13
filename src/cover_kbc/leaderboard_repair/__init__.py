"""Current downstream leaderboard repair stack.

The package is intentionally downstream of M1-M21.  When disabled it is not
constructed; when enabled it receives the completed prediction rows and may
rewrite only the final `ObjectEntities`, recording separate repair accounting.
Profile F1 extends the E3 stack with Mistral stock empty-row rescue.
"""

from cover_kbc.leaderboard_repair.config import LeaderboardRepairConfig
from cover_kbc.leaderboard_repair.stack import LeaderboardRepairStack, build_repair_stack

__all__ = [
    "LeaderboardRepairConfig",
    "LeaderboardRepairStack",
    "build_repair_stack",
]
