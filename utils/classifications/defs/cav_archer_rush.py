"""Cav-archer rush (Early-Castle family): >20 Cavalry Archers in the early-Castle window
[Castle click, 2nd additional TC). Cavalry Archers queue as "Cavalry Archer"; Heavy Cavalry Archer
is an upgrade. Signature upgrade: Thumb Ring (the defining cav-archer accuracy/fire-rate tech)."""
from utils.classifications.defs._early_castle import make


def _is_cav_archer(e):
    return e.get("category") == "cav_archer"


CLASSIFICATION = make(
    key="cav_archer_rush", title="Cav Archer Rush",
    count_label="Cav Archers in window",
    unit_pred=_is_cav_archer, threshold=20,
    sig_tech="Thumb Ring",
    trigger_spec="a player who made more than 20 Cavalry Archers between clicking Castle Age and building a 2nd additional Town Center",
    unit_source=("cav_archer_queue_events", "extract.events[category=cav_archer]"),
)
