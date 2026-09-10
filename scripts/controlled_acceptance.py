"""Run host-frozen Python check recipes once, without automatic retry or redesign.

This CLI is a host administration interface, deliberately absent from MCP tools.
It executes argument vectors with shell=False. It certifies process observations
only; independent frozen test programs must assess target causes/consequences.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from shortdrama_mcp.acceptance_control import AcceptanceController, AcceptanceControlError


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _hashes(record):
    root = Path(record["workspace_root"])
    return {name: file_sha(root / name) for name in record["candidate_hashes"] if (root / name).is_file()}


def validate_recipes(manifest, root, candidate_hashes=None):
    """No -c/eval/unknown module escape; scripts and interpreter must be frozen."""
    known = {**manifest["frozen_files"], **(candidate_hashes if candidate_hashes is not None else manifest["candidate_files"])}
    executable = Path(sys.executable).resolve()
    try:
        python_relative = executable.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("runner interpreter must be inside frozen workspace") from exc
    if known.get(python_relative) != file_sha(executable):
        raise ValueError("runner interpreter is not frozen by exact hash")
    for step in manifest["steps"]:
        recipe = step["recipe"]
        if recipe["kind"] != "python" or recipe["expected_exit_code"] != 0:
            raise ValueError("CLI supports frozen successful check-program recipes only")
        argv = recipe["argv"]
        if argv[:2] == ["-m", "unittest"]:
            if not argv[2:] or any(value in {"discover", "-c", "--catch"} for value in argv[2:]):
                raise ValueError("unittest recipes must explicitly name the frozen test modules")
            for value in argv[2:]:
                if value in {"-v", "-q", "-f", "--failfast", "--buffer", "-b"}:
                    continue
                module = value.split(".")[0]
                path = "tests/" + module + ".py"
                if path not in manifest["frozen_files"]:
                    raise ValueError("unittest oracle must be a frozen file: " + path)
        else:
            entrypoint = (root / argv[0]).resolve()
            if not entrypoint.is_relative_to(root) or entrypoint.suffix != ".py":
                raise ValueError("script recipe must name a workspace Python file")
            relative = entrypoint.relative_to(root).as_posix()
            if relative not in known:
                raise ValueError("script entrypoint must be frozen or tracked candidate: " + relative)
            if known[relative] != file_sha(entrypoint):
                raise ValueError("script entrypoint hash changed")
        if step["expected"] != {"stage": "process", "outcome": "accepted", "reason_codes": [],
                                 "prohibited_states": ["semantic_pass_claim"]}:
            raise ValueError("CLI observations support the process acceptance expectation only")


def run_round(controller, contract_id, registry, output):
    record = controller.status(contract_id)
    root = Path(record["workspace_root"])
    manifest = record["manifest"]
    validate_recipes(manifest, root, record["candidate_hashes"])
    output = output.resolve()
    if not output.is_relative_to(root):
        raise ValueError("evidence output must be an excluded directory inside frozen workspace")
    # The controller's inventory independently rejects non-excluded new files.
    begun = controller.begin_round(contract_id, _hashes(record))
    round_number = begun["round_number"]
    for step in manifest["steps"]:
        current = controller.status(contract_id)
        if current["state"] in {"passed", "stopped"}:
            break
        reservation = controller.reserve_step(contract_id, round_number, step["step_id"], _hashes(current))
        folder = output / contract_id / ("round-" + str(round_number)) / step["step_id"]
        recipe = step["recipe"]
        observation = {"reservation_id": reservation["reservation_id"], "step_id": step["step_id"],
                       "round_number": round_number, "argv": [sys.executable, *recipe["argv"]],
                       "scope": "frozen_check_program_only", "semantic_accuracy_percent": None}
        started = time.monotonic()
        classification, outcome, reasons = "completed", "accepted", []
        process_started = False
        try:
            folder.mkdir(parents=True, exist_ok=False)
            prior_steps = current["rounds"][-1]["steps"]
            dependencies_ready = all(current["reservations"][prior_steps[name]]["matched"] is True
                                     for name in step["depends_on"])
            env = os.environ.copy()
            env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPATH": os.pathsep.join(str(root / part) for part in
                                                     ("server", "tests", "skills/shortdrama-storyboard/scripts")),
                        "SHORTDRAMA_ACCEPTANCE_CONTROL": "1",
                        "SHORTDRAMA_ACCEPTANCE_REGISTRY": str(registry.resolve()),
                        "SHORTDRAMA_ACCEPTANCE_LEASE": reservation["lease_id"]})
            with (folder / "stdout.txt").open("xb") as stdout, (folder / "stderr.txt").open("xb") as stderr:
                if dependencies_ready:
                    process_started = True
                    result = subprocess.run([sys.executable, *recipe["argv"]], cwd=root, env=env,
                                            stdout=stdout, stderr=stderr, shell=False,
                                            timeout=recipe["timeout_seconds"],
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    observation["exit_code"] = result.returncode
                else:
                    classification, outcome, reasons = "blocked_dependency", "not_run", ["prerequisite_not_matched"]
                    observation["exit_code"] = None
            if process_started and observation["exit_code"] != 0:
                # Failure is observed, but cause requires host evidence review;
                # a nonzero exit alone never diagnoses the model or architecture.
                classification, outcome, reasons = "completed", "error", ["check_program_failed"]
                observation["root_cause"] = "not_determined_from_exit_code"
        except (subprocess.TimeoutExpired, OSError, KeyboardInterrupt) as exc:
            classification, outcome, reasons = "interrupted", "error", ["execution_interrupted"]
            observation.update(exit_code=None, exception_type=type(exc).__name__, error=str(exc))
        observation["elapsed_milliseconds"] = int((time.monotonic() - started) * 1000)
        observed = folder / "observation.json"
        write_once(observed, observation)
        evidence = {path.relative_to(root).as_posix(): file_sha(path) for path in
                    (observed, folder / "stdout.txt", folder / "stderr.txt") if path.is_file()}
        state = controller.complete_step(reservation["reservation_id"], {
            "classification": classification, "stage": "process", "outcome": outcome,
            "reason_codes": reasons, "reached_states": ["check_program_started"] if process_started else [], "evidence": evidence,
            "summary": "Frozen check program exit recorded; no manuscript or independent semantic acceptance claimed.",
        })
        print(json.dumps({"step_id": step["step_id"], "round_number": round_number,
                          "exit_code": observation.get("exit_code"), "state": state.get("state")}, ensure_ascii=False), flush=True)
    final = controller.status(contract_id)
    print(json.dumps({"contract_id": contract_id, "state": final["state"],
                      "repair_used": final["repair_used"], "manuscript_accuracy_proven": False}, ensure_ascii=False), flush=True)
    return 0 if final["state"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--manifest", type=Path, required=True)
    freeze.add_argument("--workspace", type=Path, required=True)
    for name in ("status", "run-round", "repair", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--contract", required=True)
        if name == "run-round":
            command.add_argument("--output", type=Path, required=True)
        elif name == "repair":
            command.add_argument("--changed-path", action="append", required=True)
            command.add_argument("--rationale", required=True)
        elif name == "stop":
            command.add_argument("--reason", required=True)
    args = parser.parse_args()
    controller = AcceptanceController(args.registry)
    if args.action == "freeze":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        validate_recipes(manifest, args.workspace.resolve())
        result = controller.freeze(manifest, args.workspace)
    elif args.action == "run-round":
        return run_round(controller, args.contract, args.registry, args.output)
    elif args.action == "repair":
        record = controller.status(args.contract)
        result = controller.authorize_repair(args.contract, observed_hashes=_hashes(record),
                                             changed_paths=args.changed_path, rationale=args.rationale)
    elif args.action == "stop":
        result = controller.stop(args.contract, args.reason)
    else:
        result = controller.status(args.contract)
    # Lease tokens and content snapshots stay in the host registry, not stdout.
    print(json.dumps({key: result.get(key) for key in ("contract_id", "state", "round_number", "repair_used")
                      if key in result}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AcceptanceControlError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc), "code": getattr(exc, "code", "host_runner_error")}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
