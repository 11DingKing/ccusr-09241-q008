"""组合求解引擎。"""

from .sensitivity import ScenarioResult, sensitivity
from .solver import InfeasibleError, ItemReason, Plan, Selection, solve

__all__ = [
    "InfeasibleError",
    "ItemReason",
    "Plan",
    "ScenarioResult",
    "Selection",
    "sensitivity",
    "solve",
]
