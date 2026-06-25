"""Camel rush (Early-Castle family): >20 combat Camels in the early-Castle window
[Castle click, 2nd additional TC). Combat camels = Camel Rider / Heavy Camel Rider / Imperial
Camel (and Flaming Camel); the feudal Camel Scout and the ranged Camel Archer are excluded.
Signature upgrade: Bloodlines. NOTE: combat camels are absent from the current local corpus (only
Camel Scout/Archer/Flaming Camel are present) -- this populates if/when such games are in the data;
see the no-download corpus rule. Definition is correct and ready regardless."""
from utils.classifications.defs._early_castle import make


def _is_combat_camel(e):
    n = (e.get("name") or "").lower()
    return "camel" in n and "scout" not in n and "archer" not in n


CLASSIFICATION = make(
    key="camel_rush", title="Camel Rush",
    count_label="Camels in window",
    unit_pred=_is_combat_camel, threshold=20,
    sig_tech="Bloodlines",
    trigger_spec="a player who made more than 20 combat Camels (Camel Rider/Heavy Camel/Imperial Camel; Camel Scout and Camel Archer excluded) between clicking Castle Age and building a 2nd additional Town Center",
    unit_source=("camel_queue_events", "extract.events[name~camel, excludes Scout/Archer]"),
)
