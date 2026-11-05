import asyncio
import logging
import sys
from typing import Set
from app.config import settings
from app.github_auth import get_repo_installation_id
from app.github_client import GitHubClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("watchdog.poller")


async def poll_watchdog(
    owner: str = settings.TARGET_REPO_OWNER,
    repo: str = settings.TARGET_REPO_NAME,
    interval_seconds: int = 10,
):
    """
    Polls GitHub Actions runs periodically without needing webhooks or public URLs.
    Ideal for enterprise environments, firewalls, and local development.
    """
    logger.info("Initializing Watchdog Poller for %s/%s (interval: %ss)...", owner, repo, interval_seconds)

    # 1. Initialize client using either GITHUB_TOKEN or GitHub App
    if settings.GITHUB_TOKEN:
        logger.info("Using GITHUB_TOKEN authentication.")
        client = GitHubClient()
    else:
        try:
            installation_id = await get_repo_installation_id(owner, repo)
            logger.info("Found GitHub App installation ID: %s for %s/%s", installation_id, owner, repo)
            client = GitHubClient(installation_id)
        except Exception as e:
            logger.error("Failed to authenticate with GitHub App: %s", e)
            logger.error("Set GITHUB_TOKEN in .env OR set GITHUB_APP_ID & private key.", owner, repo)
            return
    handled_runs: Set[int] = set()

    # Pre-populate handled_runs with existing runs so we don't rerun old history on startup
    try:
        initial_runs = await client.list_workflow_runs(owner, repo, per_page=15)
        for r in initial_runs:
            handled_runs.add(r["id"])
        logger.info("Watching for new failures... (seeded with %s recent runs)", len(handled_runs))
    except Exception as e:
        logger.warning("Could not fetch initial runs: %s", e)

    while True:
        try:
            runs = await client.list_workflow_runs(owner, repo, per_page=10)
            for r in runs:
                run_id = r["id"]
                status_val = r.get("status")
                conclusion = r.get("conclusion")
                head_branch = r.get("head_branch", "")
                head_sha = r.get("head_sha", "")
                run_attempt = r.get("run_attempt", 1)

                # Process new completed failures
                if status_val == "completed" and conclusion == "failure":
                    # Check if already processed
                    state_key = f"{run_id}_attempt_{run_attempt}"
                    if state_key in handled_runs:
                        continue

                    # Prevent loops on branches already created by watchdog
                    if head_branch.startswith("watchdog/"):
                        logger.warning(
                            "Workflow run %s failed on watchdog branch '%s'. Skipping auto-retry to prevent loops.",
                            run_id, head_branch
                        )
                        handled_runs.add(state_key)
                        continue

                    if run_attempt == 1:
                        logger.info(
                            "🚨 Failure detected on %s (run #%s, attempt 1). Action: Rerunning and creating repair branch...",
                            head_branch, run_id
                        )
                        handled_runs.add(state_key)

                        # 1. Rerun failed workflow
                        await client.rerun_workflow_run(owner, repo, run_id)

                        # 2. Create watchdog branch
                        branch_name = f"watchdog/fix-run-{run_id}"
                        await client.create_branch(owner, repo, branch_name, head_sha)
                    else:
                        logger.info(
                            "Workflow run %s failed on attempt %s. Ready for AI diagnosis in next phase.",
                            run_id, run_attempt
                        )
                        handled_runs.add(state_key)

        except Exception as e:
            logger.error("Error during poll cycle: %s", e)

        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    owner = sys.argv[1] if len(sys.argv) > 1 else settings.TARGET_REPO_OWNER
    repo = sys.argv[2] if len(sys.argv) > 2 else settings.TARGET_REPO_NAME
    try:
        asyncio.run(poll_watchdog(owner, repo))
    except KeyboardInterrupt:
        logger.info("Poller stopped by user.")
