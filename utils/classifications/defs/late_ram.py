"""Late-castle rams: >10 Rams in the late-castle window [3rd additional TC, Imperial click).
Reuses the ram predicate (Battering/Capped/Siege Ram by name). Higher bar than the early ram push
(>10 vs >3) for a committed late siege army. Excludes feudal rushers and early-castle pushers."""
from utils.classifications.defs._late_castle import make_late
from utils.classifications.defs.ram_push import _is_ram

CLASSIFICATION = make_late(
    key="late_ram", title="Late Rams",
    count_label="Rams (late)",
    unit_pred=_is_ram, threshold=10,
    trigger_spec="a player who made more than 10 Rams (Battering/Capped/Siege Ram) between building their 3rd additional Town Center and clicking Imperial Age (excluding players already doing a feudal or early-castle rush)",
    unit_source=("ram_queue_events", "extract.events[name in Battering/Capped/Siege Ram]"),
)
