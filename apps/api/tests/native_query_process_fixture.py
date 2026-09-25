"""Executed by test_native_query_process as a fake Core; no Autodesk dependency."""

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path

# MODE and OPTIONS are provided by the test executable's preamble.
MODE = globals()["MODE"]
OPTIONS = globals()["OPTIONS"]
job = Path.cwd()
drawing = Path(sys.argv[sys.argv.index("/i") + 1])
request = job / "request.txt"
raw = request.read_bytes()
output = job / "native.json"
# The parent test provides a contract-shaped reply. This subprocess never
# implements a second request parser alongside the production codec.
reply = OPTIONS["reply"]
reply["request_sha256"] = hashlib.sha256(raw).hexdigest()
(job / "observed.json").write_text(json.dumps({
    "argv": sys.argv, "cwd": str(job), "pid": os.getpid(),
    "pgid": os.getpgrp(), "sid": os.getsid(0), "drawing": str(drawing),
}))
(job / "ready").touch()
print("fake Core diagnostic", flush=True)

if MODE == "missing":
    sys.exit(0)
if MODE == "malformed":
    output.write_bytes(b"{broken json")
    sys.exit(0)
if MODE == "identity":
    reply["request_id"] = "f" * 32
if MODE == "version":
    reply["plugin_version"] = "wrong-worker"
if MODE == "dbmod":
    reply["database_modified_flags"] = 1
if MODE == "request_mutation":
    request.write_bytes(raw + b"extra\n")
if MODE == "staged_mutation":
    (job / "package" / "refs" / "reference.dwg").write_bytes(b"changed")
if MODE == "bootstrap_mutation":
    drawing.write_bytes(b"saved bootstrap")
if MODE == "source_mutation":
    Path(OPTIONS["source"]).write_bytes(b"changed")
if MODE == "symlink_output":
    output.symlink_to(OPTIONS["source"])
    sys.exit(0)
if MODE == "linked_log":
    (job / "core.log").symlink_to(OPTIONS["source"])
if MODE == "temporary_reply":
    Path(str(output) + ".tmp").write_text(json.dumps(reply))
    sys.exit(0)
if MODE in {"output_flood", "temporary_flood"}:
    path = output if MODE == "output_flood" else Path(str(output) + ".tmp")
    path.write_bytes(b"x" * OPTIONS["bytes"])
    time.sleep(10)
if MODE == "log_flood":
    os.write(1, b"L" * OPTIONS["bytes"])
    time.sleep(10)

output.write_text(json.dumps(reply))
lifecycle = {
    "schema": "green-atlas.native-query-lifecycle/1",
    "request_sha256": hashlib.sha256(raw).hexdigest(),
    "source_sha256": reply["source_sha256"],
    "reply_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    "database_destroyed": True, "working_database_restored": True,
    "field_evaluation_restored": True,
}
if MODE == "wrong_cleanup":
    lifecycle["reply_sha256"] = "b" * 64
if MODE == "false_cleanup":
    lifecycle["field_evaluation_restored"] = False
if MODE == "numeric_cleanup":
    lifecycle["database_destroyed"] = 1
if MODE != "missing_cleanup":
    (job / "lifecycle.json").write_text(json.dumps(lifecycle))
if MODE == "exit254":
    sys.exit(254)
if MODE == "exit7":
    sys.exit(7)
if MODE in {"hang", "ignore_term", "child", "orphan"}:
    if MODE in {"ignore_term", "child", "orphan"}:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if MODE in {"child", "orphan"}:
        pid = os.fork()
        if pid == 0:
            while True:
                time.sleep(0.1)
        (job / "child.pid").write_text(str(pid))
        if MODE == "orphan":
            sys.exit(0)
    while True:
        time.sleep(0.1)
