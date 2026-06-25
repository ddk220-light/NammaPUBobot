"""Collects every classification module under defs/ into REGISTRY (key -> Classification).
Add a new classification by importing its module here and appending its CLASSIFICATION."""
from utils.classifications.defs import (
    archer_rush,
    cav_archer_rush,
    camel_rush,
    crossbow_rush,
    knight_rush,
    maa_rush,
    ram_push,
    scout_rush,
)

_ALL = [
    archer_rush.CLASSIFICATION,
    scout_rush.CLASSIFICATION,
    maa_rush.CLASSIFICATION,
    knight_rush.CLASSIFICATION,
    crossbow_rush.CLASSIFICATION,
    cav_archer_rush.CLASSIFICATION,
    camel_rush.CLASSIFICATION,
    ram_push.CLASSIFICATION,
]

REGISTRY = {c.key: c for c in _ALL}
