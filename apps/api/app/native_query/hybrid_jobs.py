"""Capture-bound progress for buffered search, separate from mask calculation."""

from collections import defaultdict
from dataclasses import dataclass, field

from shapely.geometry import mapping, shape


@dataclass
class Job:
    items: tuple
    processed: int = 0
    blocked: list = field(default_factory=list)
    sites: list = field(default_factory=list)
    uncertain_sites: list = field(default_factory=list)
    reviews: dict = field(default_factory=lambda: defaultdict(list))
    issues: list = field(default_factory=list)
    elapsed: float = 0
    cache_hits: int = 0
    result: dict | None = None

    def checkpoint(self):
        return {
            "processed": self.processed, "total": len(self.items),
            "blocked": [mapping(g) for g in self.blocked],
            "sites": [mapping(g) for g in self.sites],
            "uncertain_sites": [mapping(g) for g in self.uncertain_sites],
            "reviews": {reason: [mapping(g) for g in parts] for reason, parts in self.reviews.items()},
            "issues": self.issues, "elapsed": self.elapsed, "cache_hits": self.cache_hits,
        }

    def restore(self, value):
        if value["total"] != len(self.items) or not 0 <= value["processed"] <= len(self.items):
            raise ValueError("Сохранённый ход проверки не соответствует объектам захвата")
        self.processed = value["processed"]
        self.blocked = [shape(g) for g in value["blocked"]]
        self.sites = [shape(g) for g in value["sites"]]
        self.uncertain_sites = [shape(g) for g in value["uncertain_sites"]]
        self.reviews.update({reason: [shape(g) for g in parts] for reason, parts in value["reviews"].items()})
        self.issues = value["issues"]
        self.elapsed = value["elapsed"]
        self.cache_hits = value["cache_hits"]
