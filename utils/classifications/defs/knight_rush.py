"""Knight rush (Early-Castle family): >20 Knights in the early-Castle window
[Castle click, 3rd additional TC). Knights queue as "Knight"; Cavalier/Paladin are upgrades, and
the infantry Teutonic Knight (a different queued name) is excluded. Signature upgrade: Cavalier."""
from utils.classifications.defs._early_castle import make


def _is_knight(e):
    return (e.get("name") or "").lower() == "knight"


CLASSIFICATION = make(
    key="knight_rush", title="Knight Rush",
    count_label="Knights in window",
    unit_pred=_is_knight, threshold=20,
    sig_tech="Cavalier",
    trigger_spec="a player who made more than 20 Knights (Teutonic Knight excluded) between clicking Castle Age and building a 3rd additional Town Center",
    unit_source=("knight_queue_events", "extract.events[name=Knight]"),
)
