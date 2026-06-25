"""Late-castle unique units: >30 Castle-trained unique units in the late-castle window
[3rd additional TC, Imperial click). "Unique unit" = the extractor's unique_other category (a
trained military unit that isn't a generic line) -- e.g. Fire Lancer, Steppe Lancer, Conquistador,
Mangudai, Huskarl, Shotel Warrior, Konnik. The #1 late-castle army in the corpus. Excludes feudal
rushers and early-castle pushers."""
from utils.classifications.defs._late_castle import make_late


def _is_unique(e):
    return e.get("category") == "unique_other"


CLASSIFICATION = make_late(
    key="late_unique", title="Late Unique Units",
    count_label="Unique units (late)",
    unit_pred=_is_unique, threshold=30,
    trigger_spec="a player who made more than 30 Castle-trained unique units between building their 3rd additional Town Center and clicking Imperial Age (excluding players already doing a feudal or early-castle rush)",
    unit_source=("unique_unit_queue_events", "extract.events[category=unique_other]"),
)
