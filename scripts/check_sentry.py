"""Send a bounded set of real HTTP requests to the dedicated Sentry test app."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
import argparse
from datetime import datetime, timezone

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', action='store_true', help='Send one error issue, plus two healthy records, to the dedicated test project.')
    parser.add_argument('--run-id', help='Reuse a pilot fingerprint to verify a genuine reopening.')
    args = parser.parse_args()
    run_id = args.run_id or uuid.uuid4().hex
    if not run_id.isascii() or not run_id.isalnum() or not 1 <= len(run_id) <= 64:
        raise ValueError('Use a 1-64 character ASCII alphanumeric run ID.')
    artifact_id = run_id + ('-pilot-' + uuid.uuid4().hex[:8] if args.pilot else '')
    directory = ROOT / ".cache" / "sentry-verification" / artifact_id
    directory.mkdir(parents=True)
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    started = datetime.now(timezone.utc).isoformat()
    with (directory / "test-app.log").open("w", encoding="utf-8") as log:
        command = [sys.executable, "-m", "examples.sentry_test_app", "--port", str(port), "--run-id", run_id] + (['--new-issue'] if args.pilot else [])
        process = subprocess.Popen(command,
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20) as client:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Test app failed to start. Inspect its local log.")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError("Test app readiness timed out.")
                responses = []
                values = ["10", "invalid-record", "20"] if args.pilot else ["10", "invalid-record", "20", "another-invalid-record", "30", "invalid-value"]
                for value in values:
                    response = client.post("/process", json={"value": value})
                    expected = 200 if value.isdigit() else 422
                    if response.status_code != expected:
                        raise RuntimeError(f"Unexpected HTTP status {response.status_code}.")
                    responses.append({"http_status": response.status_code, **response.json()})
                flushed = client.post("/flush")
                flushed.raise_for_status()
            result = {"run_id": run_id, "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
                      "project": "escalation-test", "environment": "verification", "responses": responses,
                      "expected_errors": 1 if args.pilot else 3, "expected_logs": 6 if args.pilot else 12,
                      "pilot": args.pilot,
                      "transport": flushed.json(),
                      "delivery": "SDK flushed; confirm arrival independently in Sentry"}
            (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result, indent=2))
            if result["transport"].get("dropped"):
                raise RuntimeError("Sentry transport dropped telemetry: " + ", ".join(result["transport"]["dropped"]))
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
