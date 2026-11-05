import logging
from typing import Any, Dict, Optional
import httpx
from app.github_auth import get_installation_access_token

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubClient:
    def __init__(self, installation_id: int):
        self.installation_id = installation_id

    async def _headers(self) -> Dict[str, str]:
        token = await get_installation_access_token(self.installation_id)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def rerun_workflow_run(self, owner: str, repo: str, run_id: int) -> bool:
        """
        Rerun a failed workflow run.
        Endpoint: POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun
        """
        headers = await self._headers()
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/actions/runs/{run_id}/rerun"

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers)
            if response.status_code in (201, 202):
                logger.info("Successfully triggered rerun for workflow run %s (%s/%s)", run_id, owner, repo)
                return True
            logger.error(
                "Failed to rerun workflow run %s (%s/%s): %s %s",
                run_id, owner, repo, response.status_code, response.text
            )
            return False

    async def create_branch(self, owner: str, repo: str, branch_name: str, sha: str) -> Optional[Dict[str, Any]]:
        """
        Create a new branch in the repository pointing to a given commit SHA.
        Endpoint: POST /repos/{owner}/{repo}/git/refs
        """
        headers = await self._headers()
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs"
        ref_name = f"refs/heads/{branch_name}"
        payload = {
            "ref": ref_name,
            "sha": sha,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code == 201:
                logger.info("Created branch '%s' at SHA %s in %s/%s", branch_name, sha, owner, repo)
                return response.json()
            elif response.status_code == 422:
                # Reference already exists
                logger.warning("Branch '%s' already exists in %s/%s (HTTP 422)", branch_name, owner, repo)
                return None
            else:
                logger.error(
                    "Failed to create branch '%s' in %s/%s: %s %s",
                    branch_name, owner, repo, response.status_code, response.text
                )
                return None
