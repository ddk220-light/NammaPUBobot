"""Ram push (Early-Castle family): >3 Rams in the early-Castle window [Castle click, 2nd additional
TC). Rams are matched by exact name (Battering Ram / Capped Ram / Siege Ram) so the substring
"ram" does not catch e.g. Karambit Warrior. Lower threshold (>3) than the unit rushes (>20), since
a handful of rams is already a committed push. Signature upgrade: Capped Ram."""
from utils.classifications.defs._early_castle import make

_RAMS = {"battering ram", "capped ram", "siege ram"}


def _is_ram(e):
    return (e.get("name") or "").lower() in _RAMS


CLASSIFICATION = make(
    key="ram_push", title="Ram Push",
    count_label="Rams in window",
    unit_pred=_is_ram, threshold=3,
    sig_tech="Capped Ram",
    trigger_spec="a player who made more than 3 Rams (Battering/Capped/Siege Ram) between clicking Castle Age and building a 2nd additional Town Center",
    unit_source=("ram_queue_events", "extract.events[name in Battering/Capped/Siege Ram]"),
)
