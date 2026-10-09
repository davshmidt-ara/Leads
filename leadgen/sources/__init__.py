"""Source adapters. Each exposes collect(cfg, source_cfg) -> iterable of lead dicts."""
from . import apollo, places, reddit

REGISTRY = {"places": places, "apollo": apollo, "reddit": reddit}
