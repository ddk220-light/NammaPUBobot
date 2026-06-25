"""Late-castle crossbows: >30 foot Archer-line units in the late-castle window
[3rd additional TC, Imperial click). Reuses the foot-archer predicate (Camel Archer excluded).
Excludes feudal rushers and early-castle pushers (their late army is a continuation)."""
from utils.classifications.defs._late_castle import make_late
from utils.classifications.defs.crossbow_rush import _is_foot_archer

CLASSIFICATION = make_late(
    key="late_crossbow", title="Late Crossbows",
    count_label="Crossbows/Archers (late)",
    unit_pred=_is_foot_archer, threshold=30,
    trigger_spec="a player who made more than 30 foot Archer-line units between building their 3rd additional Town Center and clicking Imperial Age (excluding players already doing a feudal or early-castle rush)",
    unit_source=("foot_archer_queue_events", "extract.events[category=archer_line minus Camel Archer]"),
)
