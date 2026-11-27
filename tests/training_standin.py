"""A stand-in for ``python -m ligandbench``, obeying the ``train`` CLI contract.

Not a test module (pytest does not collect it) and not science: it writes the
files and the progress a real run writes, so the server's training job can be
tested without ligandbench's own training. ``tests/training_helpers.py`` puts it
behind a fake interpreter, so the real ``Ligandbench`` adapter launches it.

How it behaves is chosen per dataset, by ``behavior.json`` in the dataset
directory it is given -- no environment, no global state, so jobs in one test can
behave differently:

    mode     ok (default) | fail | bad_input | missing | unavailable | hang | crash
             | garbage | no_run_json | bad_run_json | wrong_status | noisy
    gate     true: stop after the first progress and wait for a ``release`` file
             in the dataset directory, so a test can hold a job running

It records what it was called with in ``argv.json`` in the dataset directory, and
a grandchild's pid in ``grandchild.pid`` when it hangs.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

INFO = globals().get("INFO") or {}


def write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, path)


def progress(run: Path, state: str, stage: str, completed: int, total: int) -> None:
    write_json(
        run / "progress.json",
        {"state": state, "stage": stage, "completed": completed, "total": total},
    )


def main(argv: list[str]) -> int:
    assert argv[:2] == ["-m", "ligandbench"], argv
    command = argv[2]
    if command == "info":
        print(json.dumps({"version": "0.0-standin", "rdkit": "0", **INFO}))
        return 0
    assert command == "train", command
    dataset = Path(argv[3])
    opts = dict(zip(argv[4::2], argv[5::2]))
    run = Path(opts["--out"])
    seed, strategy, profile = (
        int(opts["--seed"]),
        opts["--production-strategy"],
        opts.get("--profile", "full"),
    )
    (dataset / "argv.json").write_text(json.dumps({"argv": argv, "cwd": os.getcwd()}))
    behavior_file = dataset / "behavior.json"
    behavior = json.loads(behavior_file.read_text()) if behavior_file.is_file() else {}
    mode = behavior.get("mode", "ok")

    if mode == "fail":
        print("Traceback: boom in the tuner", file=sys.stderr)
        return 1
    if mode == "bad_input":
        print(json.dumps({"error": "the target has one class"}))
        return 2
    if mode == "missing":
        print(json.dumps({"error": "XGBoost cannot be imported here"}))
        return 3
    if mode == "unavailable":
        print(
            json.dumps(
                {
                    "status": "production_strategy_unavailable",
                    "strategy": strategy,
                    "unavailable": {"reason": "too few scaffolds", "strategy": strategy},
                }
            )
        )
        return 0
    if mode == "garbage":
        print("this is not json")
        return 0

    progress(run, "splitting", "splitting", 0, 10)
    if mode == "hang":
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        (dataset / "grandchild.pid").write_text(str(grandchild.pid))
        progress(run, "tuning", "tuning candidates", 1, 10)
        time.sleep(600)
        return 0
    if mode == "crash":
        progress(run, "tuning", "tuning candidates", 1, 10)
        os.kill(os.getpid(), signal.SIGKILL)
    if behavior.get("gate"):
        progress(run, "tuning", "tuning candidates", 3, 10)
        deadline = time.time() + 120
        while not (dataset / "release").exists() and time.time() < deadline:
            time.sleep(0.02)
    if mode == "noisy":
        print("a library warning on stdout")
        print("x" * 200000, file=sys.stderr)
    progress(run, "refitting", "refitting the production model", 9, 10)
    (run / "model.json").write_text('{"learner": "standin"}')
    (run / "features.json").write_text('{"version": "standin"}')
    (run / "dataset").mkdir()
    (run / "dataset" / "rows.json").write_text('{"rows": []}')
    files = {
        str(p.relative_to(run)): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
        }
        for p in sorted(run.rglob("*"))
        if p.is_file() and p.name != "progress.json"
    }
    record = {
        "schema": "qsar-run-v1",
        "status": "completed",
        "model_family": "xgboost",
        "created_utc": "2026-11-27T00:00:00Z",
        "seed": seed,
        "profile": profile,
        "dataset": {"id": dataset.name, "content_hash": "0" * 64, "n_rows": 10, "n_eligible": 10},
        "production": {"strategy": strategy, "n_refit_rows": 10},
        "strategies": {},
        "files": files,
    }
    if mode == "wrong_status":
        record["status"] = "failed"
    if mode == "bad_run_json":
        (run / "run.json").write_text('{"schema": "qsar-run-v1", "stat')
    elif mode != "no_run_json":
        write_json(run / "run.json", record)
    progress(run, "done", "done", 10, 10)
    print(json.dumps({"status": "completed", "run": "run.json"}))
    return 0


sys.exit(main(sys.argv[1:]))
