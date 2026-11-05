import time
from typing import Dict, Tuple
import httpx
import jwt
from app.config import settings

# In-memory cache for installation tokens: {installation_id: (token, expiry_timestamp)}
_token_cache: Dict[int, Tuple[str, float]] = {}


def generate_jwt() -> str:
    """Generate a GitHub App JWT valid for 9 minutes."""
    private_key = settings.get_private_key()
    now = int(time.time())
    payload = {
        "iat": now - 60,  # 60 seconds in the past for clock drift
        "exp": now + (9 * 60),  # 9 minutes in the future
        "iss": settings.GITHUB_APP_ID,
    }
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")
    return encoded_jwt


async def get_installation_access_token(installation_id: int) -> str:
    """
    Get an installation access token for a specific installation ID.
    Caches token until 60 seconds before its expiration.
    """
    now = time.time()
    if installation_id in _token_cache:
        token, expires_at = _token_cache[installation_id]
        if now < expires_at - 60:
            return token

    app_jwt = generate_jwt()
    headers = {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"

    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers)
        if response.status_code != 201:
            raise RuntimeError(
                f"Failed to get installation access token: {response.status_code} - {response.text}"
            )
        data = response.json()
        token = data["token"]
        # Default GitHub token validity is 1 hour (3600s)
        expires_at = now + 3500
        _token_cache[installation_id] = (token, expires_at)
        return token
