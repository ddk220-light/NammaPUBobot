"""Late-castle knights: >30 Knights in the late-castle window [3rd additional TC, Imperial click).
Reuses the knight predicate (queued "Knight"; Teutonic Knight excluded). Excludes feudal rushers
and early-castle pushers -- their late army is a continuation, not a fresh late-castle push."""
from utils.classifications.defs._late_castle import make_late
from utils.classifications.defs.knight_rush import _is_knight

CLASSIFICATION = make_late(
    key="late_knight", title="Late Knights",
    count_label="Knights (late)",
    unit_pred=_is_knight, threshold=30,
    trigger_spec="a player who made more than 30 Knights between building their 3rd additional Town Center and clicking Imperial Age (excluding players already doing a feudal or early-castle rush)",
    unit_source=("knight_queue_events", "extract.events[name=Knight]"),
)
