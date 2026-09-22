from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("automatic_address_controls.py")
SPEC = importlib.util.spec_from_file_location("automatic_address_controls", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AutomaticAddressControlsTest(unittest.TestCase):
    def test_normalization(self) -> None:
        self.assertEqual(MODULE.normalize_address("10 к1"), "10 к1")
        self.assertEqual(MODULE.normalize_address("14 корпус 1 строение 2"), "14 к1 с2")
        self.assertEqual(MODULE.normalize_address("10А"), "10а")
        self.assertIsNone(MODULE.normalize_address("161.25"))
        self.assertIsNone(MODULE.normalize_address("Кустанайская улица"))

    def test_pairs_only_unique_addresses(self) -> None:
        dxf = [
            {"normalized_address": "10 к1", "id": "a"},
            {"normalized_address": "10а", "id": "b"},
            {"normalized_address": "10а", "id": "c"},
        ]
        map_rows = [
            {"normalized_address": "10 к1", "id": "x"},
            {"normalized_address": "10а", "id": "y"},
        ]
        pairs, audit = MODULE.pair_unique_addresses(dxf, map_rows)
        self.assertEqual([row["address"] for row in pairs], ["10 к1"])
        self.assertEqual(audit["unique_pairs"], 1)
        self.assertEqual(audit["ambiguous"], [{"address": "10а", "dxf": 2, "map": 1}])


if __name__ == "__main__":
    unittest.main()
