import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx


def fetch_failed_logs(repo: str, run_id: str, headers: dict) -> str:
    """Fetch terminal error logs from the failed workflow run's jobs."""
    jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    print(f"Fetching job logs for run #{run_id}...")
    try:
        res = httpx.get(jobs_url, headers=headers)
        if res.status_code != 200:
            print(f"⚠️ Could not fetch jobs: {res.status_code} {res.text}")
            return ""

        jobs = res.json().get("jobs", [])
        combined_logs = []

        for job in jobs:
            if job.get("conclusion") == "failure":
                job_id = job["id"]
                log_url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
                log_res = httpx.get(log_url, headers=headers, follow_redirects=True)
                if log_res.status_code == 200:
                    lines = log_res.text.splitlines()
                    tail_lines = lines[-200:] if len(lines) > 200 else lines
                    combined_logs.append(f"--- FAILED JOB: {job.get('name')} ---\n" + "\n".join(tail_lines))

        return "\n\n".join(combined_logs)
    except Exception as e:
        print(f"⚠️ Error fetching logs: {e}")
        return ""


def get_repo_context() -> Dict[str, str]:
    """Read existing repository source and test files to provide context to AI."""
    context = {}
    ignored_dirs = {".git", ".venv", "__pycache__", "build", "dist", ".idea", ".vscode"}

    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for f in files:
            file_path = os.path.relpath(os.path.join(root, f), ".")
            if file_path.endswith((".py", ".yml", ".yaml", ".json", ".md", ".txt", ".fail_ci")) or f == ".fail_ci":
                try:
                    p = Path(file_path)
                    if p.is_file() and p.stat().st_size < 50000:  # < 50KB
                        context[file_path] = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    pass
    return context


def call_deepseek(api_key: str, prompt: str) -> Optional[str]:
    """Call DeepSeek API (deepseek-chat)."""
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an autonomous CI/CD watchdog repair engineer. "
                    "Analyze the failure logs and repository code, determine the fix, "
                    "and output your response as valid JSON only."
                )
            },
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    try:
        res = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        if res.status_code == 200:
            data = res.json()
            return data["choices"][0]["message"]["content"]
        print(f"⚠️ DeepSeek API returned HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"⚠️ DeepSeek API request error: {e}")
    return None


def call_openai(api_key: str, prompt: str) -> Optional[str]:
    """Call OpenAI API (gpt-4o-mini)."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are an autonomous CI repair agent. Output valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1
    }
    try:
        res = httpx.post(url, headers=headers, json=payload, timeout=60.0)
        if res.status_code == 200:
            data = res.json()
            return data["choices"][0]["message"]["content"]
        print(f"⚠️ OpenAI API returned HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"⚠️ OpenAI API request error: {e}")
    return None


def fallback_rule_based_fix(logs: str, context: Dict[str, str]) -> Dict[str, Any]:
    """Fallback when no LLM response is obtained."""
    print("ℹ️ Running diagnostic rule-based healing engine...")
    edits = []
    summary = "Autonomous diagnostic repair"

    if ".fail_ci" in context or "Found .fail_ci trigger file" in logs:
        print("💡 Detected failure cause: .fail_ci trigger file. Action: Removing .fail_ci.")
        edits.append({"path": ".fail_ci", "action": "delete"})
        summary = "Removed .fail_ci trigger file that caused CI pipeline to fail."
    elif "'should_fail' manual trigger option was set to true" in logs:
        print("💡 Detected manual should_fail trigger in workflow dispatch.")
        edits.append({
            "path": "watchdog_fix_applied.txt",
            "action": "write",
            "content": "Fix applied by Watchdog for CI verification.\n"
        })
        summary = "Applied verification fix for manual dispatch test failure."

    return {"summary": summary, "edits": edits}


def diagnose_and_fix(logs: str, repo_context: Dict[str, str]) -> Dict[str, Any]:
    """
    Diagnoses failure and produces code patches.
    Uses DeepSeek as primary AI model (with OpenAI option available).
    """
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    # openai_key = os.environ.get("OPENAI_API_KEY")

    prompt = f"""A GitHub Actions test pipeline has failed. Your job is to inspect the error logs and repository code, diagnose the root cause, and produce exact code edits to fix the failure.

FAILURE LOGS:
{logs}

REPOSITORY FILES:
{json.dumps(repo_context, indent=2)}

INSTRUCTIONS:
Return a valid JSON object with the following schema:
{{
  "summary": "<1-2 sentence explanation of the failure cause and fix>",
  "edits": [
    {{
      "path": "<relative file path>",
      "action": "write" | "delete",
      "content": "<full file content if action is write>"
    }}
  ]
}}
Do NOT wrap in markdown code blocks. Output raw JSON only.
"""

    response_text = None

    # 1. Primary AI: DeepSeek
    if deepseek_key:
        print("🤖 Invoking DeepSeek AI Healer (deepseek-chat)...")
        response_text = call_deepseek(deepseek_key, prompt)

    # 2. Option for OpenAI (uncomment when needed):
    # elif openai_key:
    #     print("🤖 Invoking OpenAI GPT AI Healer (gpt-4o-mini)...")
    #     response_text = call_openai(openai_key, prompt)

    if response_text:
        try:
            cleaned = re.sub(r"^```json\s*", "", response_text.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)
            if "edits" in data:
                return data
        except Exception as e:
            print(f"⚠️ Failed to parse AI JSON response: {e}. Raw was: {response_text[:200]}")

    return fallback_rule_based_fix(logs, repo_context)


def apply_edits(edits: List[Dict[str, Any]]) -> List[str]:
    """Apply generated file edits to the current working tree."""
    modified_paths = []
    for edit in edits:
        path_str = edit.get("path")
        action = edit.get("action", "write")
        content = edit.get("content", "")

        if not path_str:
            continue

        target_path = Path(path_str)

        if action == "delete":
            if target_path.exists():
                target_path.unlink()
                print(f"🗑️ Deleted: {path_str}")
                modified_paths.append(path_str)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            print(f"📝 Modified/Created: {path_str}")
            modified_paths.append(path_str)

    return modified_paths


def commit_and_push_fix(branch_name: str, summary: str, token: str, repo: str) -> bool:
    """Commit changes to the local branch and push to GitHub."""
    try:
        status_out = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        if not status_out.strip():
            print("ℹ️ No file changes detected to commit.")
            touch_file = Path("watchdog_verification.txt")
            touch_file.write_text("AI Watchdog verified and re-triggered.", encoding="utf-8")
            subprocess.run(["git", "add", "watchdog_verification.txt"], check=True)

        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)

        subprocess.run(["git", "add", "-A"], check=True)
        commit_msg = f"fix(watchdog): autonomous AI repair\n\n{summary}"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)

        remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
        subprocess.run(["git", "push", remote_url, f"HEAD:refs/heads/{branch_name}"], check=True)
        print(f"🚀 Successfully pushed AI repair commit to branch '{branch_name}'!")
        return True
    except Exception as e:
        print(f"❌ Failed to commit and push fix: {e}")
        return False
