import os
import sys
import httpx


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

    # 2. Attempt 1 failure: Rerun and create repair branch
    if failed_run_attempt == 1:
        print("\n👉 Attempt 1 failure detected. Running Watchdog recovery actions...")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Step A: Trigger workflow rerun
        rerun_url = f"https://api.github.com/repos/{repo}/actions/runs/{failed_run_id}/rerun"
        print(f"Triggering rerun for run #{failed_run_id}...")
        try:
            res_rerun = httpx.post(rerun_url, headers=headers)
            if res_rerun.status_code in (201, 202):
                print(f"✅ Successfully triggered rerun for workflow run #{failed_run_id}!")
            else:
                print(f"⚠️ Rerun API returned HTTP {res_rerun.status_code}: {res_rerun.text}")
        except Exception as e:
            print(f"❌ Failed to request rerun: {e}")

        # Step B: Create watchdog branch
        branch_name = f"watchdog/fix-run-{failed_run_id}"
        ref_url = f"https://api.github.com/repos/{repo}/git/refs"
        ref_payload = {
            "ref": f"refs/heads/{branch_name}",
            "sha": head_sha,
        }
        print(f"Creating repair branch '{branch_name}' at {head_sha[:7]}...")
        try:
            res_branch = httpx.post(ref_url, headers=headers, json=ref_payload)
            if res_branch.status_code == 201:
                print(f"✅ Successfully created branch '{branch_name}'!")
            elif res_branch.status_code == 422:
                print(f"ℹ️ Branch '{branch_name}' already exists.")
            else:
                print(f"⚠️ Branch creation returned HTTP {res_branch.status_code}: {res_branch.text}")
        except Exception as e:
            print(f"❌ Failed to create branch: {e}")

    else:
        print(f"\n🔍 Workflow run failed on attempt {failed_run_attempt}.")
        print("Ready for AI Agent diagnosis & code repair in the next phase.")


if __name__ == "__main__":
    main()
