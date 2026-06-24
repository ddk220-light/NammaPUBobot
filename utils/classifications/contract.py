"""The classification contract: each classification is a trigger predicate + a factors
function + a static data-requirements ledger, all keyed under a stable string `key`."""
from collections.abc import Callable
from dataclasses import dataclass, field


def req(field_name, source, status, note=""):
    """One data-requirement row. status is 'available' or 'missing'."""
    if status not in ("available", "missing"):
        raise ValueError(f"req() status must be 'available' or 'missing', got {status!r}")
    return {"field": field_name, "source": source, "status": status, "note": note}


@dataclass
class Classification:
    key: str
    title: str
    version: int
    trigger_spec: str                  # human-readable description of the trigger
    requirements: list                 # list of req() dicts
    trigger: Callable                  # (game, pnum) -> bool   (pure)
    factors: Callable                  # (game, pnum) -> dict[str, float|None]  (pure)
    status: str = "active"             # 'active' or 'draft'
    factor_specs: list = field(default_factory=list)  # ordered [{metric,label,kind}] for reports
