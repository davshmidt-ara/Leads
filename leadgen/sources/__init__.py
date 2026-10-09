"""Source adapters. Each exposes collect(cfg, source_cfg) -> iterable of lead dicts."""
from . import apollo, osm, places, reddit

REGISTRY = {"osm": osm, "places": places, "apollo": apollo, "reddit": reddit}
