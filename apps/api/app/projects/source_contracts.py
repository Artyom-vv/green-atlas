"""Small source inspection independent of the stored Project geometry."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceContentInfo:
    size: int
    prefix: bytes
