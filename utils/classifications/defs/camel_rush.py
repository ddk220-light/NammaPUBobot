"""Camel rush (Early-Castle family): >20 combat Camels in the early-Castle window
[Castle click, 2nd additional TC).

RESEARCH NOTE (the camel-ID question): nothing is dropped by the parser -- camel unit IDs all
resolve (Camel Rider=329, Heavy Camel Rider=330, Imperial Camel=207, Mameluke=282, Camel
Scout=1755, Camel Archer=1007). In THIS corpus the standard stable Camel Rider line is never
produced; the camels actually massed are the **Mameluke** (Saracen camel-class UU, by far the most
common) and the feudal Camel Scout. So "combat camels" here = the Camel Rider line + Mameluke +
Flaming Camel (all camel-armor-class melee units). The feudal Camel Scout (a scout-line unit) and
the ranged Camel Archer are excluded -- Camel Scout belongs to scout play, Camel Archer is an
archer. Signature upgrade: Bloodlines (camels are cavalry)."""
from utils.classifications.defs._early_castle import make

# camel-class melee combat units matched by name (the Mameluke carries no "camel" in its name, so
# it is listed explicitly; the Camel Rider line is also covered by the substring rule below).
_COMBAT_CAMELS = {"camel rider", "heavy camel rider", "imperial camel rider",
                  "mameluke", "elite mameluke", "flaming camel"}


def _is_combat_camel(e):
    n = (e.get("name") or "").lower()
    if n in _COMBAT_CAMELS:
        return True
    # generic Camel Rider line (future-proof), excluding the feudal Camel Scout and ranged Camel Archer
    return "camel" in n and "scout" not in n and "archer" not in n


CLASSIFICATION = make(
    key="camel_rush", title="Camel Rush",
    count_label="Camels in window",
    unit_pred=_is_combat_camel, threshold=20,
    sig_tech="Bloodlines",
    trigger_spec="a player who made more than 20 combat Camels (Camel Rider line or the Mameluke camel UU; feudal Camel Scout and ranged Camel Archer excluded) between clicking Castle Age and building a 2nd additional Town Center",
    unit_source=("camel_queue_events", "extract.events[Camel Rider line + Mameluke + Flaming Camel; excl Camel Scout/Archer]"),
)
