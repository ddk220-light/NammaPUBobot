"""Late-castle camels: >30 Camels (Camel Scout/Camel Rider line) in the late-castle window
[3rd additional TC, Imperial click). Reuses the camel predicate (Mameluke/Flaming Camel/Camel
Archer excluded). Excludes feudal rushers and early-castle pushers."""
from utils.classifications.defs._late_castle import make_late
from utils.classifications.defs.camel_rush import _is_camel

CLASSIFICATION = make_late(
    key="late_camel", title="Late Camels",
    count_label="Camels (late)",
    unit_pred=_is_camel, threshold=30,
    trigger_spec="a player who made more than 30 Camels (Camel Scout/Camel Rider line) between building their 3rd additional Town Center and clicking Imperial Age (excluding players already doing a feudal or early-castle rush)",
    unit_source=("camel_queue_events", "extract.events[name in Camel Scout/Rider/Heavy Camel/Imperial Camel]"),
)
