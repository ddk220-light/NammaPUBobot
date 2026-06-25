"""Crossbow rush (Early-Castle family): >20 foot Archer-line units in the early-Castle window
[Castle click, 2nd additional TC). Foot archers queue as "Archer"; Crossbowman/Arbalester are
upgrades. The Berber Camel Archer (also archer_line, but mounted) is excluded. Signature upgrade:
Crossbowman."""
from utils.classifications.defs._early_castle import make


def _is_foot_archer(e):
    return e.get("category") == "archer_line" and (e.get("name") or "") != "Camel Archer"


CLASSIFICATION = make(
    key="crossbow_rush", title="Crossbow Rush",
    count_label="Crossbows/Archers in window",
    unit_pred=_is_foot_archer, threshold=20,
    sig_tech="Crossbowman",
    trigger_spec="a player who made more than 20 foot Archer-line units (crossbows; the mounted Camel Archer excluded) between clicking Castle Age and building a 2nd additional Town Center",
    unit_source=("foot_archer_queue_events", "extract.events[category=archer_line minus Camel Archer]"),
)
