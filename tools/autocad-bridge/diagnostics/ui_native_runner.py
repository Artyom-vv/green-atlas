"""Bounded experimental connection of the real UI to the native window query.

Not a release/capture adapter. A complete native result followed by exit254 is
retained as an explicit pilot lifecycle warning, as requested by the user.
No result, changed source or missing points still fails; there is no fallback.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from run_direct_queries import digest, quoted, run_core


class PilotWindowRunner:
    def __init__(self, baseline: Path, bundle: Path, root: Path):
        receipt = json.loads(baseline.read_text())
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.package = self.root / "package"
        source_root = Path(receipt["package"])
        self.files = []
        for record in receipt["files"]:
            source = Path(record["source"])
            if digest(source) != record["sha256"]:
                raise ValueError("Dataset no longer matches native baseline")
            destination = self.package / source.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                subprocess.run(["/bin/cp", "-c", "-p", str(source), str(destination)], check=True)
            self.files.append((destination, record["sha256"]))
        self.drawing = self.package / Path(receipt["source"]).relative_to(source_root)
        binary_sha = digest(bundle / "Contents/MacOS/GreenAtlasBridge")
        self.bundle = self.root / f"NativeWindow-{binary_sha[:12]}.dbx"
        if not self.bundle.exists():
            shutil.copytree(bundle, self.bundle)
        subprocess.run(["codesign", "--verify", "--strict", str(self.bundle)], check=True)
        self.binary_sha = digest(self.bundle / "Contents/MacOS/GreenAtlasBridge")
        self.identity = digest(self.drawing)
        self.calls = []
        self._verify()

    def _verify(self):
        if any(digest(path) != expected for path, expected in self.files):
            raise ValueError("Native pilot archive changed; recreate the test session")
        if digest(self.bundle / "Contents/MacOS/GreenAtlasBridge") != self.binary_sha:
            raise ValueError("Native pilot binary changed")

    @staticmethod
    def rules(project):
        """Keep existing reviewed mapping. No new name classifier.

        The saved display was captured from the project-solutions document;
        the full host addresses those exact layers with this explicit XREF prefix.
        This is a declared Kustanayskaya test binding, not fuzzy layer matching.
        """
        result = {}
        for layer in project.layers:
            kind = str(layer.mapped_kind or "unknown")
            if layer.mapping_confirmed is False:
                kind = "unknown"
            mode, clearance = {
                "building": ("area", 5.0), "road": ("area", 2.0),
                "utility": ("curve", 1.0), "existing_green": ("curve", 1.0),
                "water": ("area", 1.0), "restricted": ("area", 1.0),
                "site_border": ("context", 0.0), "ignore": ("context", 0.0),
            }.get(kind, ("unknown", 0.0))
            names = [layer.source_name]
            if "|" not in layer.source_name:
                names.append("03_10004141_Проектные решения|" + layer.source_name)
            for name in names:
                if "\n" in name or "\r" in name:
                    raise ValueError("Invalid native layer name")
                result[name] = (mode, clearance)
        return result

    def __call__(self, project, points):
        if not points or len(points) > 2000 or any(not math.isfinite(v) for p in points for v in p):
            raise ValueError("Invalid native pilot point batch")
        self._verify()
        root = Path(tempfile.mkdtemp(prefix="query-", dir=self.root))
        xs, ys = zip(*points, strict=True)
        window = [min(xs) - .01, min(ys) - .01, max(xs) + .01, max(ys) + .01]
        rules = self.rules(project)
        output, request = root / "native.json", root / "request.txt"
        lines = [str(output), "window", " ".join(map(str, [*window, 5])),
                 "evaluate-points", str(len(points)),
                 *[f"{x:.17g} {y:.17g} 0" for x, y in points],
                 "6E16/16020", str(len(rules))]
        for name, (mode, clearance) in rules.items():
            lines.extend([name, f"{mode} {clearance}"])
        request.write_text("\n".join(lines) + "\n")
        script = root / "query.scr"
        script.write_text(
            f'(setvar "TRUSTEDPATHS" {quoted(str(self.root) + "/")})\n'
            '(setvar "FILEDIA" 0)\n'
            f'(arxload {quoted(self.bundle)})\nGAXREFLEDGER\n{request}\n'
            f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
            '_QUIT\n_Y\n\n')
        engine = run_core(root, self.drawing, script, 180)
        self._verify()
        if not engine["commands_completed"] or engine["exit_code"] not in (0, 254):
            raise ValueError(f"AutoCAD native query failed: {engine}; {root}")
        if not output.is_file() or output.stat().st_size > 96 * 1024**2:
            raise ValueError(f"AutoCAD returned no bounded native result: {root}")
        report = json.loads(output.read_text())
        if report.get("source") != str(self.drawing) or report.get("units") != 6:
            raise ValueError("Native source/units mismatch")
        inventory = report["inventory"]
        window_report = inventory["window_inventory"]
        calculation = window_report["calculation"]
        answers = calculation["answers"]
        if len(answers) != len(points):
            raise ValueError("Native point batch is incomplete")
        ledger = {row["route"]: row for row in calculation["object_ledger"]}
        for point, answer in zip(points, answers, strict=True):
            if answer["point"] != [*point, 0]:
                raise ValueError("Native point identity mismatch")
            answer["source_layer"] = ledger.get(answer.get("blocker"), {}).get("layer")
        # Missing XREFs and non-geometric unlocated attributes stay in receipt;
        # every nonblocked result is a draft, never complete-source safety.
        receipt = {"project_id": project.id, "point_count": len(points),
                   "source_sha256": self.identity, "native_binary_sha256": self.binary_sha,
                   "engine": engine, "window": window, "xref_issues": inventory["issues"],
                   "available_xref_instances": len(inventory["xref_instances"]),
                   "near_objects": window_report["near_instances"],
                   "calculation": {k: v for k, v in calculation.items() if k not in {"object_ledger", "answers"}},
                   "answers": answers, "source_unchanged": True,
                   "status": "experimental_native_ui; no release certification"}
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        self.calls.append(str(root / "receipt.json"))
        print(json.dumps({"native_ui_query": str(root), "points": len(points),
                          "results": {v: sum(a["result"] == v for a in answers) for v in ("blocked", "unknown", "draft")},
                          "seconds": engine["wall_seconds"]}), flush=True)
        return answers
