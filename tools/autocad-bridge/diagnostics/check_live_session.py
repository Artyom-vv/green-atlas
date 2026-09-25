"""Replay the production live-session protocol in a real AutoCAD document.

This checks the DXF/session/XREF/mutation boundary, not planting acceptance.
It changes only an unsaved test-copy point, then quits without saving.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery, NativeTarget
from app.native_query.live_client import LIVE_PROTOCOL, LiveReply
from app.native_query.protocol import decode_reply, encode_request
from run_direct_queries import CORE, digest, quoted


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("Output must be empty")
    drawing = args.source.resolve(strict=True)
    if drawing.suffix.lower() != ".dxf":
        parser.error("This acceptance step requires a DXF")
    source_hash = digest(drawing)
    bundle = args.bundle.resolve(strict=True)
    if not str(root).isascii():
        parser.error(
            "Core script paths must be ASCII; use a private /tmp run directory"
        )
    staged_bundle = root / "LiveQuery.dbx"
    shutil.copytree(bundle, staged_bundle)
    ids = {name: uuid4().hex for name in ("open", "query", "inspect", "changed")}
    query = NativeObjectQuery(
        request_id=ids["query"],
        source_sha256=source_hash,
        units_code=6,
        targets=(NativeTarget(route="6E16/7BF", capability="area"),),
        points=((15935, -4960, 0), (15880, -4960, 0)),
    )
    script = root / "session.scr"
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f"(arxload {quoted(staged_bundle)})\n"
        + "".join(
            f"GALIVESESSION\n{ids[name]}\n" for name in ("open", "query", "inspect")
        )
        + '(entmake (list (cons 0 "POINT") (cons 10 (list 0.0 0.0 0.0))))\n'
        + f"GALIVESESSION\n{ids['changed']}\n"
        + f'(setq gaDone (open {quoted(root / "completed")} "w")) (write-line "complete" gaDone) (close gaDone)\n'
        + "_QUIT\n_Y\n",  # Core asks whether to DISCARD changes, not whether to save.
        encoding="utf-8",
    )
    (root / "profile").mkdir()
    start = time.monotonic()
    receipt = {
        "source": str(drawing),
        "source_sha256": source_hash,
        "bundle": str(bundle),
    }
    with (root / "core.log").open("wb") as log:
        child = subprocess.Popen(
            [
                "/usr/bin/arch",
                "-x86_64",
                str(CORE),
                "/i",
                str(drawing),
                "/s",
                str(script),
                "/isolate",
                "ga-live-" + ids["open"],
                str(root / "profile"),
            ],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        queue = Path(f"/tmp/green-atlas-live-query-{os.getuid()}-{child.pid}")
        queue.mkdir(mode=0o700, exist_ok=False)
        output = queue / f"measurements-{ids['query']}.json"
        request_bytes = encode_request(query, output)
        (queue / f"objects-{ids['query']}.txt").write_bytes(request_bytes)
        for name, identity in ids.items():
            operation = "inspect" if name == "changed" else name
            (queue / f"request-{identity}.txt").write_text(
                f"{LIVE_PROTOCOL}\n{operation}\n{ids['open']}\n{int(time.time() + 240)}\n"
            )
        try:
            child.wait(timeout=240)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            receipt["timeout"] = True
        receipt.update(
            exit_code=child.returncode,
            seconds=time.monotonic() - start,
            queue=str(queue),
        )
    try:
        replies = {
            name: LiveReply.model_validate_json(
                (queue / f"reply-{identity}.json").read_bytes()
            )
            for name, identity in ids.items()
        }
        receipt["replies"] = {
            name: reply.model_dump(mode="json", by_alias=True)
            for name, reply in replies.items()
        }
        if output.exists():
            measured = decode_reply(
                output.read_bytes(), query, request_bytes=request_bytes
            )
            receipt["measurements"] = measured.model_dump(mode="json", by_alias=True)
        opened = replies["open"].session
        if opened:
            with Path(opened.snapshot_path).open("rb") as stream:
                receipt["map_hash_matches"] = (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    == opened.snapshot_sha256
                )
        receipt["session_contract_passed"] = (
            all(replies[name].ok for name in ("open", "query", "inspect"))
            and not replies["changed"].ok
            and replies["changed"].error == "live_source_changed_or_inactive"
            and receipt.get("map_hash_matches", False)
            and "measurements" in receipt
        )
    except (OSError, ValueError, KeyError) as error:
        receipt["error"] = str(error)
    receipt["source_unchanged"] = digest(drawing) == source_hash
    (root / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in receipt.items() if k not in {"replies", "measurements"}},
            ensure_ascii=False,
        )
    )
    return int(
        not receipt.get("session_contract_passed")
        or not receipt["source_unchanged"]
        or child.returncode != 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
