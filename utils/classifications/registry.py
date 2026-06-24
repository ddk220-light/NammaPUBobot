"""Collects every classification module under defs/ into REGISTRY (key -> Classification).
Add a new classification by importing its module here and appending its CLASSIFICATION."""
from utils.classifications.defs import archer_rush

_ALL = [
    archer_rush.CLASSIFICATION,
]

REGISTRY = {c.key: c for c in _ALL}
