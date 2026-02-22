from dataclasses import dataclass
from typing import List


@dataclass
class Branch:
    name: str
    weekly_hours: float
    emoji: str = "🔵"


BRANCHES: List[Branch] = [
    Branch("MIT",                2,  "🎓"),
    Branch("Intervia.ai",       10,  "🤖"),
    Branch("AION Growth Studio", 15, "🚀"),
    Branch("Marca Personal",     4,  "⭐"),
    Branch("Buscar trabajo",    15,  "💼"),
    Branch("Networking",         4,  "🤝"),
    Branch("Personal",           4,  "🏠"),
]

BRANCH_NAMES: List[str] = [b.name for b in BRANCHES]
BRANCH_HOURS: dict = {b.name: b.weekly_hours for b in BRANCHES}
BRANCH_EMOJI: dict = {b.name: b.emoji for b in BRANCHES}

TOTAL_WEEKLY_HOURS: float = sum(b.weekly_hours for b in BRANCHES)  # 54h
