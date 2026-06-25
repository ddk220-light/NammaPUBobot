"""Safe castle: a player who built a Castle in Castle Age as their PRIMARY building (before any
additional TC of the Castle Age), placed CLOSER to their own home TC than to any opponent's --
a defensive/economic castle. See _castle_placement for the shared logic."""
from utils.classifications.defs._castle_placement import make_castle

CLASSIFICATION = make_castle(
    key="safe_castle", title="Safe Castle",
    want_forward=False,
    trigger_spec="a player whose first Castle (built in Castle Age before any additional Town Center) was placed closer to their own home Town Center than to any opponent's",
)
