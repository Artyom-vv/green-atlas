from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("compose_neural_finish.py")
SPEC = importlib.util.spec_from_file_location("compose_neural_finish", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


@unittest.skipUnless(shutil.which("magick"), "ImageMagick is required")
class ComposeNeuralFinishTests(unittest.TestCase):
    def test_candidate_is_copied_only_inside_renderer_mask(self) -> None:
        magick = shutil.which("magick")
        assert magick
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base.png"
            candidate = root / "candidate.png"
            mask = root / "mask.png"
            output = root / "out"
            subprocess.run([magick, "-size", "8x8", "xc:red", str(base)], check=True)
            subprocess.run([magick, "-size", "8x8", "xc:blue", str(candidate)], check=True)
            subprocess.run([
                magick, "-size", "8x8", "xc:black", "-fill", "white",
                "-draw", "rectangle 0,0 3,7", str(mask),
            ], check=True)
            contract = MODULE.build_contract(base, [mask], output)
            result = MODULE.composite_candidate(
                contract, candidate, output, "test-model", "test-revision", 7
            )
            final = Path(result["final"]["path"])
            left = subprocess.run(
                [magick, str(final), "-format", "%[pixel:p{1,1}]", "info:"],
                check=True, text=True, capture_output=True,
            ).stdout
            right = subprocess.run(
                [magick, str(final), "-format", "%[pixel:p{6,1}]", "info:"],
                check=True, text=True, capture_output=True,
            ).stdout
            self.assertIn("0,0,255", left)
            self.assertIn("255,0,0", right)
            self.assertEqual(result["protected_changed_pixels"], 0)


if __name__ == "__main__":
    unittest.main()
