"""Keep the single approved video request from being submitted twice."""

import json
import os
from pathlib import Path
import subprocess
import sys

WORKFLOW = "sho-source-memo.yml"
SEND_STEP = "Send the one Gemini request"
UPLOAD_STEP = "Publish the source memo"
ARTIFACT = "sho-source-memo-01"
PREFLIGHT_STEPS = ("Check the Gemini key is configured", "Load the latest successful public catalog")


def rows(endpoint, field):
    response = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        check=True,
        capture_output=True,
        text=True,
    )
    return [row for page in json.loads(response.stdout) for row in page[field]]


def previous_memo(repo, number, reviewed_retries=()):
    base = f"repos/{repo}/actions"
    runs = rows(f"{base}/workflows/{WORKFLOW}/runs?per_page=100", "workflow_runs")
    prior = [run for run in runs if run["run_number"] < number]
    if {run["run_number"] for run in prior} != set(range(1, number)):
        raise ValueError("Earlier run history is incomplete. Review saved project records before sending.")
    for run in sorted(prior, key=lambda row: row["run_number"]):
        run_id = run["id"]
        if run["status"] != "completed":
            raise ValueError(f"Run {run_id} has not finished. Check its status first.")
        jobs = rows(f"{base}/runs/{run_id}/jobs?filter=all&per_page=100", "jobs")
        if not jobs:
            raise ValueError(f"No job history for run {run_id}. Check the prior attempt first.")
        for job in jobs:
            steps = {step["name"]: step["conclusion"] for step in job["steps"]}
            if steps.get(UPLOAD_STEP) == "success":
                artifacts = rows(f"{base}/runs/{run_id}/artifacts?per_page=100", "artifacts")
                available = any(item["name"] == ARTIFACT and not item["expired"] for item in artifacts)
                if not available:
                    raise ValueError(f"Run {run_id} published the memo; its artifact is unavailable. Consult the saved copy.")
                print(f"Source memo already published by run {run_id}.")
                return run_id
            if steps.get(SEND_STEP) == "skipped":
                continue
            # Each manual approval names the exact failed run and job reviewed by the operator.
            approval = f"retry-reviewed-503 {run_id}/{job['id']}"
            if (approval in reviewed_retries and steps.get(SEND_STEP) == "failure"
                    and steps.get(UPLOAD_STEP) == "skipped"):
                continue
            # GitHub may omit later steps when a preflight check fails.
            if SEND_STEP not in steps and any(steps.get(name) == "failure" for name in PREFLIGHT_STEPS):
                continue
            raise ValueError(f"Prior sending status needs review for run {run_id}. No further video request sent.")
    return None


def main():
    if os.environ["GITHUB_RUN_ATTEMPT"] != "1":
        sys.exit("Re-runs never send this pilot. For a preflight failure, start a fresh request after fixing it.")
    try:
        request_file = Path(__file__).resolve().parents[1] / "requests/sho-source-memo-01.txt"
        approvals = set(request_file.read_text(encoding="utf-8").splitlines())
        run_id = previous_memo(os.environ["GITHUB_REPOSITORY"], int(os.environ["GITHUB_RUN_NUMBER"]), approvals)
    except ValueError as error:
        sys.exit(str(error))
    except (KeyError, subprocess.CalledProcessError):
        sys.exit("GitHub run history could not be verified. No video request sent.")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("send=" + ("false" if run_id else "true") + "\n")


if __name__ == "__main__":
    main()
