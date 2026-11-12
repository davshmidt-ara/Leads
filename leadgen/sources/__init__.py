"""Source adapters. Each exposes collect(cfg, source_cfg) -> iterable of lead dicts."""
from . import apollo, osm, overture, places, reddit

REGISTRY = {"overture": overture, "osm": osm, "places": places, "apollo": apollo, "reddit": reddit}
