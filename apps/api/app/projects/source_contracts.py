"""Компактные сведения о сохранённом исходнике без загрузки геометрии проекта."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceContentInfo:
    size: int
    prefix: bytes
