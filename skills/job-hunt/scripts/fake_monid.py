#!/usr/bin/env python3
"""Stand-in for the monid CLI in tests: replays recorded run records from fixtures/.

`monid run -p P -e /a/b ...` prints fixtures/P_a_b.json, or a 404 record when there is none.
FAKE_MONID_LOG, if set, gets one line per call so tests can assert what ran.
"""

import json
import os
import sys
from pathlib import Path

FIXTURES = Path(os.environ.get("FAKE_MONID_FIXTURES", Path(__file__).parent / "fixtures"))


def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "balance":
        print(json.dumps({"balance": {"value": 12.34, "currency": "USD"}}))
        return 0
    if len(sys.argv) > 1 and sys.argv[1] in ("whoami", "keys"):
        return 0
    provider, endpoint = arg("-p"), arg("-e")
    if os.environ.get("FAKE_MONID_LOG"):
        try:
            with open(os.environ["FAKE_MONID_LOG"], "a") as f:
                f.write(json.dumps({"provider": provider, "endpoint": endpoint, "body": arg("-i"), "query": arg("--query")}) + "\n")
        except OSError:
            pass
    path = FIXTURES / f"{provider}_{endpoint.strip('/').replace('/', '_')}.json"
    if path.exists():
        print("Update available: 0.1.7 → 0.1.8.")  # the real CLI sometimes prints a notice before the JSON
        print(path.read_text())
    else:
        print(json.dumps({"runId": "FAKE", "provider": provider, "endpoint": endpoint, "status": "COMPLETED",
                          "providerResponse": {"httpStatus": 404, "error": "no fixture"}, "cost": {"value": 0}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
