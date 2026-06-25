"""Collects every classification module under defs/ into REGISTRY (key -> Classification).
Add a new classification by importing its module here and appending its CLASSIFICATION."""
from utils.classifications.defs import archer_rush, maa_rush, scout_rush

_ALL = [
    archer_rush.CLASSIFICATION,
    scout_rush.CLASSIFICATION,
    maa_rush.CLASSIFICATION,
]

REGISTRY = {c.key: c for c in _ALL}
