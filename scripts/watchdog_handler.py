import os
from datetime import datetime
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

# Ensure repository root and script directory are in sys.path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

try:
    from scripts.ai_healer import apply_edits, commit_and_push_fix, diagnose_and_fix, fetch_failed_logs, get_repo_context
except ImportError:
    from ai_healer import apply_edits, commit_and_push_fix, diagnose_and_fix, fetch_failed_logs, get_repo_context


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def generate_branch_name(job_name: str) -> str:
    """Generate branch name in format: fix/<job_name>-<datetime_in_est>"""
    slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", job_name.lower()).strip("-")
    try:
        est_now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        from datetime import timezone, timedelta
        est_now = datetime.now(timezone(timedelta(hours=-5)))
    dt_str = est_now.strftime("%Y-%m-%d_%H-%M-%S_est")
    return f"fix/{slug}-{dt_str}"


def rerun_workflow(repo: str, run_id: str, headers: dict) -> bool:
    """Trigger a rerun for a failed workflow run."""
    rerun_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/rerun"
    print(f"Triggering rerun for workflow run #{run_id}...")
    try:
        res = httpx.post(rerun_url, headers=headers)
        if res.status_code in (201, 202):
            print(f"✅ Successfully triggered rerun for workflow run #{run_id}!")
            return True
        else:
            print(f"⚠️ Rerun API returned HTTP {res.status_code}: {res.text}")
            return False
    except Exception as e:
        print(f"❌ Failed to request rerun: {e}")
        return False


def create_branch_ref(repo: str, branch_name: str, sha: str, headers: dict) -> bool:
    """Create a new branch pointing to the failed commit SHA."""
    ref_url = f"https://api.github.com/repos/{repo}/git/refs"
    ref_payload = {
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    }
    print(f"Creating branch '{branch_name}' at commit {sha[:7]}...")
    try:
        res = httpx.post(ref_url, headers=headers, json=ref_payload)
        if res.status_code in (201, 422):
            return True
        print(f"⚠️ Branch creation returned HTTP {res.status_code}: {res.text}")
        return False
    except Exception as e:
        print(f"❌ Failed to create branch: {e}")
        return False


def run_validation_tests() -> Tuple[bool, str]:
    """
    Run local tests directly inside this runner to verify the AI's fix before pushing.
    """
    print("\n🧪 Running validation tests directly in this runner...")

    # 1. Check if .fail_ci trigger file still exists
    if os.path.exists(".fail_ci"):
        return False, "Validation failed: .fail_ci still exists in repository."

    # 2. Run Python unittest suite if tests directory exists
    if os.path.isdir("tests"):
        try:
            res = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "tests"],
                capture_output=True,
                text=True
            )
            print(res.stdout)
            if res.stderr:
                print(res.stderr)
            if res.returncode != 0:
                return False, f"Unit tests failed with code {res.returncode}:\n{res.stderr}\n{res.stdout}"
        except Exception as e:
            return False, f"Failed to execute test runner: {e}"

    return True, "All validation checks passed."


def wait_for_cloud_workflow(repo: str, branch_name: str, head_sha: str, headers: dict, timeout_seconds: int = 300) -> Tuple[bool, Optional[str]]:
    """
    Option B (Strict Cloud Wait):
    Polls GitHub Actions API until the cloud workflow triggered on branch_name finishes
    with status: completed, conclusion: success.
    """
    print(f"\n⏳ Option B Active: Waiting for GitHub Actions cloud run on '{branch_name}' (commit {head_sha[:7]})...")
    start_time = time.time()
    seen_run_id = None

    while time.time() - start_time < timeout_seconds:
        try:
            # Query runs for this commit SHA
            url = f"https://api.github.com/repos/{repo}/actions/runs?head_sha={head_sha}"
            res = httpx.get(url, headers=headers)
            if res.status_code == 200:
                runs = res.json().get("workflow_runs", [])
                # Look for Test Pipeline run on the repair branch
                matching_runs = [
                    r for r in runs
                    if r.get("head_branch") == branch_name and r.get("name") == "Test Pipeline"
                ]

                if matching_runs:
                    run_data = matching_runs[0]
                    seen_run_id = run_data["id"]
                    status = run_data.get("status")
                    conclusion = run_data.get("conclusion")
                    html_url = run_data.get("html_url")

                    if status == "completed":
                        if conclusion == "success":
                            print(f"✅ Cloud run #{seen_run_id} SUCCEEDED! ({html_url})")
                            return True, html_url
                        else:
                            print(f"❌ Cloud run #{seen_run_id} FAILED with conclusion: {conclusion} ({html_url})")
                            return False, html_url
                    else:
                        print(f"🔄 Cloud run #{seen_run_id} is currently '{status}'... waiting 10s")
                else:
                    print("⌛ Waiting for GitHub to register the cloud run for this push...")
        except Exception as e:
            print(f"⚠️ Error while polling cloud run: {e}")

        time.sleep(10)

    print(f"❌ Timed out after {timeout_seconds}s waiting for cloud workflow to finish.")
    return False, None


def create_pull_request(repo: str, head_branch: str, base_branch: str, title: str, body: str, headers: dict) -> Optional[str]:
    """Create a Pull Request into base_branch if one does not already exist."""
    pulls_url = f"https://api.github.com/repos/{repo}/pulls"

    # 1. Check if PR already exists
    try:
        owner = repo.split("/")[0]
        check_url = f"{pulls_url}?head={owner}:{head_branch}&state=open"
        check_res = httpx.get(check_url, headers=headers)
        if check_res.status_code == 200 and len(check_res.json()) > 0:
            existing_pr = check_res.json()[0]
            pr_url = existing_pr.get("html_url")
            print(f"ℹ️ Pull Request already exists for '{head_branch}': {pr_url}")
            return pr_url
    except Exception as e:
        print(f"⚠️ Could not check existing PRs: {e}")

    # 2. Create the Pull Request
    payload = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body,
    }
    print(f"\n📬 Opening Pull Request from '{head_branch}' into '{base_branch}'...")
    try:
        res = httpx.post(pulls_url, headers=headers, json=payload)
        if res.status_code == 201:
            pr_data = res.json()
            pr_url = pr_data.get("html_url")
            print(f"🎉 Successfully created Pull Request: {pr_url}")
            return pr_url
        else:
            print(f"⚠️ PR creation returned HTTP {res.status_code}: {res.text}")
            return None
    except Exception as e:
        print(f"❌ Error creating Pull Request: {e}")
        return None


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("❌ Error: GH_TOKEN environment variable is not set.")
        sys.exit(1)

    conclusion = os.environ.get("CONCLUSION", "failure")
    run_id = os.environ.get("FAILED_RUN_ID")
    run_attempt = int(os.environ.get("FAILED_RUN_ATTEMPT", 1))
    head_branch = os.environ.get("HEAD_BRANCH", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    repo = os.environ.get("REPO", "arpanroy18/pipeline-healer")

    print("==================================================")
    print(f"🐕 Watchdog Auto-Healer triggered for run #{run_id}")
    print(f"📦 Repository:    {repo}")
    print(f"🌿 Head branch:   {head_branch}")
    print(f"🔖 Head SHA:      {head_sha}")
    print(f"🔄 Run attempt:   {run_attempt}")
    print(f"📊 Conclusion:    {conclusion}")
    print("==================================================")

    # We only heal failures on main
    if conclusion != "failure" or head_branch != "main":
        print(f"Nothing to heal (conclusion={conclusion}, branch={head_branch}). Exiting.")
        return

    headers = get_headers(token)

    # -------------------------------------------------------------
    # STAGE 1 (Attempt 1): Retrigger workflow run once
    # -------------------------------------------------------------
    if run_attempt == 1:
        print("\n👉 Attempt 1 failed: Retriggering workflow run once to check for flaky issues...")
        print("AI diagnosis and branch will only run if the retrigger also fails.")
        rerun_workflow(repo, run_id, headers)
        return

    # -------------------------------------------------------------
    # STAGE 2 (Attempt >= 2): Retrigger failed again! AI Auto-Repair
    # -------------------------------------------------------------
    print(f"\n🚨 Attempt {run_attempt} failed: Retry did not succeed.")
    print("Initiating autonomous AI diagnosis and repair...")

    # Step 1: Fetch failure logs and determine failed job name
    failed_job_name, logs = fetch_failed_logs(repo, run_id, headers)

    # Step 2: Generate branch name: fix/<job_name>-<datetime_in_est>
    branch_name = generate_branch_name(failed_job_name)
    print(f"🌿 Repair branch: {branch_name}")

    # Step 3: Create and switch to the local repair branch in this runner
    print(f"Creating local repair branch '{branch_name}'...")
    subprocess.run(["git", "checkout", "-b", branch_name], check=True)

    # Step 4: Gather repository context
    context = get_repo_context()

    # Step 6: Diagnose with DeepSeek AI
    print("\n🧠 Analyzing failure logs and repository code with DeepSeek...")
    diagnosis = diagnose_and_fix(logs, context)
    summary = diagnosis.get("summary", "AI automated fix applied")
    edits = diagnosis.get("edits", [])
    print(f"💡 AI Diagnosis: {summary}")
    print(f"📋 Generated {len(edits)} file edit(s).")

    # Step 6: Apply edits to the workspace
    apply_edits(edits)

    # Step 7: Validate tests directly in this runner
    passed, test_output = run_validation_tests()
    if not passed:
        print(f"\n❌ Local validation failed: {test_output}")
        print("Aborting push and PR creation because tests are still failing.")
        sys.exit(1)

    print("\n✅ Local validation passed! All tests succeeded on the repaired code.")

    # Step 8: Commit and push the verified fix to the repair branch
    commit_and_push_fix(branch_name, summary, token, repo, title=failed_job_name)
    pushed_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    # Step 9: Option B (Strict Cloud Wait): Wait for GitHub Actions cloud run to finish with success!
    cloud_success, cloud_url = wait_for_cloud_workflow(repo, branch_name, pushed_sha, headers)
    if not cloud_success:
        print("\n❌ Aborting Pull Request creation: The cloud workflow run did not succeed.")
        sys.exit(1)

    # Step 10: Open the Pull Request into main ONLY after cloud run passed!
    pr_title = failed_job_name
    pr_body = (
        f"## 🐕 AI-Powered Watchdog Auto-Repair\n\n"
        f"### Summary\n"
        f"- **Failed Run ID**: `#{run_id}`\n"
        f"- **Diagnosis**: {summary}\n"
        f"- **Repair Branch**: `{branch_name}`\n"
        f"- **Cloud Run Verification**: ✅ [GitHub Actions Cloud Run Succeeded]({cloud_url})\n\n"
        f"### What Happened\n"
        f"1. A failure was detected in `Test Pipeline` on `main`.\n"
        f"2. Watchdog automatically retriggered once to verify flakiness.\n"
        f"3. When attempt 2 failed, DeepSeek AI analyzed the error logs and repository code.\n"
        f"4. The AI fix was applied, pushed to `{branch_name}`, and verified via GitHub Actions.\n"
        f"5. The cloud workflow turned GREEN before this PR was filed.\n\n"
        f"*Ready for review and merge.*"
    )
    pr_url = create_pull_request(repo, branch_name, "main", pr_title, pr_body, headers)
    if pr_url:
        print(f"\n🎉 Everything verified in the cloud! PR is ready: {pr_url}")


if __name__ == "__main__":
    main()
