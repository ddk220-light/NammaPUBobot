"""Camel rush (Early-Castle family): >20 Camels in the early-Castle window
[Castle click, 3rd additional TC).

GROUNDED IN THE MECHANIC: the camel cavalry line is *trained* as the "Camel Scout" -- the only
camel unit before Castle Age, which upgrades automatically/free into the Camel Rider on reaching
Castle Age (and the Stable then trains Camel Riders). So, exactly like Scout Cavalry->Light Cav,
Militia->MAA and Archer->Crossbow, the queue always records the BASE unit ("Camel Scout"); the
Camel Rider / Heavy Camel / Imperial Camel are upgrade transforms. The data confirms it: of ~23.4k
Camel Scout queue events, 99.8% are made AFTER the Castle click -- i.e. they are Camel Riders.

So we count the camel cavalry line (Camel Scout + Camel Rider/Heavy/Imperial). Per request, the
Flaming Camel (a Tatar suicide unit) and the Mameluke (a Castle-trained camel UU) are NOT counted;
the ranged Camel Archer is also excluded. Signature upgrade: Bloodlines (camels are cavalry)."""
from utils.classifications.defs._early_castle import make

# camel cavalry line, matched by name. "Camel Scout" is the trained base (= Camel Rider in Castle);
# the rider-line names are included for civs/games that record them directly.
_CAMEL_LINE = {"camel scout", "camel rider", "heavy camel rider", "imperial camel rider"}


def _is_camel(e):
    return (e.get("name") or "").lower() in _CAMEL_LINE


CLASSIFICATION = make(
    key="camel_rush", title="Camel Rush",
    count_label="Camels in window",
    unit_pred=_is_camel, threshold=20,
    sig_tech="Bloodlines",
    trigger_spec="a player who made more than 20 Camels (the Camel Scout / Camel Rider line; Flaming Camel, Mameluke and Camel Archer excluded) between clicking Castle Age and building a 3rd additional Town Center",
    unit_source=("camel_queue_events", "extract.events[name in Camel Scout/Rider/Heavy Camel/Imperial Camel]"),
)
