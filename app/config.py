from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # GitHub Credentials
    # Option A: Personal Access Token (simplest for enterprise / single repo)
    GITHUB_TOKEN: Optional[str] = None

    # Option B: GitHub App Credentials (if using GitHub App)
    GITHUB_APP_ID: str = ""
    GITHUB_APP_PRIVATE_KEY_PATH: Optional[str] = None
    GITHUB_APP_PRIVATE_KEY: Optional[str] = None
    GITHUB_WEBHOOK_SECRET: Optional[str] = None

    # Target repository defaults (can be overridden via webhook payloads)
    TARGET_REPO_OWNER: str = "arpanroy18"
    TARGET_REPO_NAME: str = "pipeline-healer"

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def get_private_key(self) -> str:
        """Returns the private key content either from direct env var or from key file."""
        if self.GITHUB_APP_PRIVATE_KEY:
            # Handle possible escaped newlines in env var
            return self.GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
        if self.GITHUB_APP_PRIVATE_KEY_PATH:
            key_path = Path(self.GITHUB_APP_PRIVATE_KEY_PATH)
            if key_path.exists():
                return key_path.read_text(encoding="utf-8")
            raise FileNotFoundError(f"Private key file not found at: {self.GITHUB_APP_PRIVATE_KEY_PATH}")
        raise ValueError("Neither GITHUB_APP_PRIVATE_KEY nor GITHUB_APP_PRIVATE_KEY_PATH is set.")


settings = Settings()
