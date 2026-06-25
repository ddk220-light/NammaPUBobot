"""Late-castle cav archers: >30 Cavalry Archers in the late-castle window
[3rd additional TC, Imperial click). Excludes feudal rushers and early-castle pushers."""
from utils.classifications.defs._late_castle import make_late
from utils.classifications.defs.cav_archer_rush import _is_cav_archer

CLASSIFICATION = make_late(
    key="late_cav_archer", title="Late Cav Archers",
    count_label="Cav Archers (late)",
    unit_pred=_is_cav_archer, threshold=30,
    trigger_spec="a player who made more than 30 Cavalry Archers between building their 3rd additional Town Center and clicking Imperial Age (excluding players already doing a feudal or early-castle rush)",
    unit_source=("cav_archer_queue_events", "extract.events[category=cav_archer]"),
)
