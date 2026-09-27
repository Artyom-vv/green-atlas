"""Pure helpers for automatic DXF/map address-control discovery."""
from __future__ import annotations

import re
from typing import Any


_ADDRESS = re.compile(
    r"^(?P<number>\d+[а-яa-z]?)"
    r"(?:\s*(?:к|корпус|корп\.?|корп)\s*(?P<block>\d+[а-яa-z]?))?"
    r"(?:\s*(?:с|стр|строение)\s*(?P<structure>\d+[а-яa-z]?))?$",
    re.IGNORECASE,
)


def normalize_address(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = str(value).casefold().replace("ё", "е")
    cleaned = cleaned.replace("\\p", " ").replace("\\P", " ")
    cleaned = re.sub(r"[,_;:]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    match = _ADDRESS.fullmatch(cleaned)
    if not match:
        return None
    result = match.group("number")
    if match.group("block"):
        result += " к" + match.group("block")
    if match.group("structure"):
        result += " с" + match.group("structure")
    return result


def normalize_street(value: str | None) -> str:
    if not value:
        return ""
    result = str(value).casefold().replace("ё", "е")
    result = re.sub(r"\b(улица|ул\.)\b", "", result)
    return re.sub(r"\s+", " ", result).strip(" ,")


def pair_unique_addresses(dxf_candidates: list[dict[str, Any]],
                          map_candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pair only unambiguous normalized addresses on both sides."""
    dxf_by_address: dict[str, list[dict[str, Any]]] = {}
    map_by_address: dict[str, list[dict[str, Any]]] = {}
    for row in dxf_candidates:
        dxf_by_address.setdefault(row["normalized_address"], []).append(row)
    for row in map_candidates:
        map_by_address.setdefault(row["normalized_address"], []).append(row)
    common = sorted(set(dxf_by_address) & set(map_by_address))
    pairs = []
    ambiguous = []
    for address in common:
        left, right = dxf_by_address[address], map_by_address[address]
        if len(left) != 1 or len(right) != 1:
            ambiguous.append({"address": address, "dxf": len(left), "map": len(right)})
            continue
        pairs.append({"address": address, "dxf": left[0], "map": right[0]})
    return pairs, {
        "dxf_candidates": len(dxf_candidates),
        "map_candidates": len(map_candidates),
        "common_addresses": len(common),
        "unique_pairs": len(pairs),
        "ambiguous": ambiguous,
    }
