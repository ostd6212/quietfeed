#!/usr/bin/env python3
"""Live progress reporting for the currently running pipeline.

Writes data/status.json and pushes it immediately (outside of Git Actions
checkpoints elsewhere in the workflow) so the already-deployed static site
can show progress -- source fetched, vacancies found/scored -- while this
run is still in flight, well before it finishes and the site itself gets
redeployed. Locally (outside GitHub Actions) this just writes the file,
no push.
"""

import json
import os
import subprocess
from datetime import datetime, timezone

STATUS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "status.json"
)


def publish(stage: str, **fields) -> None:
    status = {
        "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)

    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    try:
        subprocess.run(["git", "config", "user.name", "job-radar-bot"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "job-radar-bot@users.noreply.github.com"],
            check=True,
        )
        subprocess.run(["git", "add", STATUS_FILE], check=True)
        committed = subprocess.run(
            ["git", "commit", "-m", f"chore: status - {stage} [skip ci]"],
            capture_output=True,
        )
        if committed.returncode == 0:
            # Push explicitly to main regardless of local HEAD state --
            # actions/checkout can leave the workspace on a detached HEAD.
            subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"    ⚠ Could not publish status: {e}")
