"""Forward castle: a player who built a Castle in Castle Age as their PRIMARY building (before any
additional TC of the Castle Age), placed CLOSER to the nearest opponent's home TC than to their
own home TC -- an aggressive forward castle. See _castle_placement for the shared logic."""
from utils.classifications.defs._castle_placement import make_castle

CLASSIFICATION = make_castle(
    key="forward_castle", title="Forward Castle",
    want_forward=True,
    trigger_spec="a player whose first Castle (built in Castle Age before any additional Town Center) was placed closer to an opponent's home Town Center than to their own",
)
