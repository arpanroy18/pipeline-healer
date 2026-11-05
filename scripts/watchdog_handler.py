import os
from pathlib import Path
import subprocess
import sys
from typing import Optional, Tuple

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

    branch_name = f"watchdog/fix-run-{run_id}"

    # Step 1: Create remote branch if it doesn't exist
    create_branch_ref(repo, branch_name, head_sha, headers)

    # Step 2: Fetch and switch to the repair branch locally
    print(f"Checking out repair branch '{branch_name}'...")
    subprocess.run(["git", "fetch", "origin", f"{branch_name}:{branch_name}"], check=False)
    subprocess.run(["git", "checkout", branch_name], check=True)

    # Step 3: Fetch failure logs
    logs = fetch_failed_logs(repo, run_id, headers)

    # Step 4: Gather repository context
    context = get_repo_context()

    # Step 5: Diagnose with DeepSeek AI
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
    commit_and_push_fix(branch_name, summary, token, repo)

    # Step 9: Open the Pull Request into main right here in this job!
    pr_title = f"fix(watchdog): Automated AI repair for run #{run_id}"
    pr_body = (
        f"## 🐕 AI-Powered Watchdog Auto-Repair\n\n"
        f"### Summary\n"
        f"- **Failed Run ID**: `#{run_id}`\n"
        f"- **Diagnosis**: {summary}\n"
        f"- **Repair Branch**: `{branch_name}`\n"
        f"- **Validation**: ✅ Tests were executed and passed locally in the Watchdog runner.\n\n"
        f"### What Happened\n"
        f"1. A failure was detected in `Test Pipeline` on `main`.\n"
        f"2. Watchdog automatically retriggered once to verify flakiness.\n"
        f"3. When attempt 2 failed, DeepSeek AI analyzed the error logs and repository code.\n"
        f"4. The AI fix was applied, validated against the test suite, committed to `{branch_name}`, and filed as this PR.\n\n"
        f"*Ready for review and merge.*"
    )
    pr_url = create_pull_request(repo, branch_name, "main", pr_title, pr_body, headers)
    if pr_url:
        print(f"\n🎉 Everything completed in this Auto-Healer run! PR is ready: {pr_url}")


if __name__ == "__main__":
    main()
