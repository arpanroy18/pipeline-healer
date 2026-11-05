# Pipeline Healer (GitHub Actions Watchdog)

An automated watchdog and self-healing system for GitHub Actions workflows.

---

## What We Have Built (Current Phase)

1. **Configurable Test Workflow** (`.github/workflows/test-pipeline.yml`):
   - Can be triggered manually via GitHub UI with a `should_fail` toggle.
   - Can be triggered via git push to `main` or `watchdog/**`.
   - Easily fails on demand by creating a `.fail_ci` file or adding `[fail]` to your commit message.

2. **Watchdog Backend Service** (`app/`):
   - Receives GitHub webhook events (`workflow_run`).
   - Automatically detects failed workflow runs.
   - On the first failure (`run_attempt == 1`):
     - Automatically calls GitHub Actions API to **rerun the workflow**.
     - Creates a dedicated branch named **`watchdog/fix-run-<run_id>`** at the failed commit SHA.
   - Safeguards against infinite loops on watchdog branches.

---

## Quickstart

### 1. Set up Environment

```bash
# Activate existing virtualenv
source .venv/bin/activate

# Install dependencies (already installed if using repo venv)
pip install -r requirements.txt
```

### 2. Configure GitHub App Credentials

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Fill in:
- `GITHUB_APP_ID`: Numeric App ID from your GitHub App.
- `GITHUB_APP_PRIVATE_KEY_PATH`: Path to your downloaded `.pem` private key file.
- `GITHUB_WEBHOOK_SECRET`: Secret token configured in your GitHub App settings.

### 3. Run the Watchdog Service

```bash
uvicorn app.main:app --reload --port 8000
```

---

## Testing the Pipeline

### Option A: Manual Trigger via GitHub UI
1. Go to your repo on GitHub: **Actions** -> **Test Pipeline**.
2. Click **Run workflow**.
3. Toggle **Force this workflow run to fail** (True/False).

### Option B: Trigger Failure via Git
```bash
# Push with [fail] in commit message:
git commit --allow-empty -m "testing failure trigger [fail]"
git push origin main
```
Or create a `.fail_ci` file:
```bash
touch .fail_ci
git add .fail_ci
git commit -m "add fail trigger"
git push origin main
```
