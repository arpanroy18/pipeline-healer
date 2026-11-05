import os
import sys
import httpx


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


def create_repair_branch(repo: str, run_id: str, sha: str, headers: dict) -> str | None:
    """Create a new repair branch pointing to the failed commit SHA."""
    branch_name = f"watchdog/fix-run-{run_id}"
    ref_url = f"https://api.github.com/repos/{repo}/git/refs"
    ref_payload = {
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    }
    print(f"Creating repair branch '{branch_name}' at commit {sha[:7]}...")
    try:
        res = httpx.post(ref_url, headers=headers, json=ref_payload)
        if res.status_code == 201:
            print(f"✅ Successfully created branch '{branch_name}'!")
            return branch_name
        elif res.status_code == 422:
            print(f"ℹ️ Branch '{branch_name}' already exists.")
            return branch_name
        else:
            print(f"⚠️ Branch creation returned HTTP {res.status_code}: {res.text}")
            return None
    except Exception as e:
        print(f"❌ Failed to create branch: {e}")
        return None


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("❌ Error: GH_TOKEN environment variable is not set.")
        sys.exit(1)

    failed_run_id = os.environ.get("FAILED_RUN_ID")
    failed_run_attempt = int(os.environ.get("FAILED_RUN_ATTEMPT", 1))
    head_branch = os.environ.get("HEAD_BRANCH", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    repo = os.environ.get("REPO", "arpanroy18/pipeline-healer")

    print("==================================================")
    print(f"🐕 Watchdog Healer triggered for run #{failed_run_id}")
    print(f"📦 Repository:    {repo}")
    print(f"🌿 Head branch:   {head_branch}")
    print(f"🔖 Head SHA:      {head_sha}")
    print(f"🔄 Run attempt:   {failed_run_attempt}")
    print("==================================================")

    # 1. Loop protection: avoid retrying branches already created by watchdog
    if head_branch.startswith("watchdog/"):
        print(f"⚠️ Workflow failed on watchdog repair branch '{head_branch}'.")
        print("Skipping automatic retry to avoid infinite loops.")
        return

    headers = get_headers(token)

    # 2. Stage 1 (Attempt 1): Auto-retry once (do NOT create branch yet)
    if failed_run_attempt == 1:
        print("\n👉 Attempt 1 failed: Retriggering workflow run once to check for flaky issues...")
        print("Branch will only be created if the retrigger also fails.")
        rerun_workflow(repo, failed_run_id, headers)

    # 3. Stage 2 (Attempt >= 2): Retrigger failed again! Now create repair branch
    else:
        print(f"\n🚨 Attempt {failed_run_attempt} failed: Retry did not succeed.")
        print(f"Creating isolated repair branch for investigation...")
        branch = create_repair_branch(repo, failed_run_id, head_sha, headers)
        if branch:
            print(f"🎯 Repair branch ready: {branch}")
            print("Next step: Ready for AI Agent diagnosis & code repair.")


if __name__ == "__main__":
    main()
