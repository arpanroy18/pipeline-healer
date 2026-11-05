import hashlib
import hmac
import logging
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from app.config import settings
from app.github_client import GitHubClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("watchdog")

app = FastAPI(title="GitHub Actions Watchdog")


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected_sig = signature_header[7:]
    mac = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), expected_sig)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "github-actions-watchdog"}


@app.post("/webhook")
async def handle_webhook(
    request: Request,
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    body = await request.body()

    # Verify signature if secret configured
    if settings.GITHUB_WEBHOOK_SECRET:
        if not verify_signature(settings.GITHUB_WEBHOOK_SECRET, body, x_hub_signature_256):
            logger.warning("Invalid webhook signature received.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature"
            )

    payload: Dict[str, Any] = await request.json()

    # Ping event from GitHub when webhook is first set up
    if x_github_event == "ping":
        logger.info("Received ping event from GitHub. Webhook is successfully connected!")
        return {"msg": "pong"}

    # Handle workflow_run events
    if x_github_event == "workflow_run":
        action = payload.get("action")
        workflow_run = payload.get("workflow_run", {})
        conclusion = workflow_run.get("conclusion")
        status_val = workflow_run.get("status")
        run_id = workflow_run.get("id")
        run_attempt = workflow_run.get("run_attempt", 1)
        head_branch = workflow_run.get("head_branch", "")
        head_sha = workflow_run.get("head_sha", "")
        repo_data = payload.get("repository", {})
        repo_name = repo_data.get("name")
        owner = repo_data.get("owner", {}).get("login")
        installation = payload.get("installation", {})
        installation_id = installation.get("id")

        logger.info(
            "Workflow event: run_id=%s, action=%s, status=%s, conclusion=%s, branch=%s, attempt=%s",
            run_id, action, status_val, conclusion, head_branch, run_attempt
        )

        # Only process completed failed runs
        if action == "completed" and conclusion == "failure":
            if not installation_id:
                logger.error("No installation ID found in webhook payload. Is the GitHub App installed?")
                return {"status": "ignored", "reason": "no_installation_id"}

            client = GitHubClient(installation_id)

            # Avoid recursive loops on branches already created by watchdog
            if head_branch.startswith("watchdog/"):
                logger.warning(
                    "Workflow run %s failed on watchdog branch '%s'. Skipping auto-retry to prevent loops.",
                    run_id, head_branch
                )
                return {
                    "status": "watchdog_branch_failed",
                    "run_id": run_id,
                    "branch": head_branch,
                }

            # If attempt 1: Trigger rerun and create a watchdog branch
            if run_attempt == 1:
                logger.info(
                    "First failure detected for run %s on branch '%s'. Triggering rerun and creating watchdog branch...",
                    run_id, head_branch
                )

                # 1. Rerun the failed workflow
                rerun_ok = await client.rerun_workflow_run(owner, repo_name, run_id)

                # 2. Create branch called watchdog/<name>
                branch_name = f"watchdog/fix-run-{run_id}"
                branch_res = await client.create_branch(owner, repo_name, branch_name, head_sha)

                return {
                    "status": "handled_attempt_1",
                    "run_id": run_id,
                    "rerun_triggered": rerun_ok,
                    "branch_created": branch_name if branch_res else "already_exists_or_failed",
                }
            else:
                logger.info(
                    "Workflow run %s failed on attempt %s. Ready for AI diagnostic/healing in upcoming phase.",
                    run_id, run_attempt
                )
                return {
                    "status": "ready_for_ai_diagnosis",
                    "run_id": run_id,
                    "attempt": run_attempt,
                }

    return {"status": "ignored", "event": x_github_event}
