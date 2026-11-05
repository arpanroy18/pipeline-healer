# 🐕 Pipeline Healer: AI-Powered Autonomous CI/CD Watchdog

An autonomous CI/CD self-healing watchdog for GitHub Actions. When a workflow fails on `main`, the watchdog re-runs the job to rule out flakiness. If the failure persists, it fetches the error logs, calls **DeepSeek AI** to diagnose and repair the code, verifies the fix by running tests locally in the runner, and automatically opens a Pull Request into `main`.

---

## 🏗️ How It Works (The 4-Step Loop)

```
[ Step 1: Failure 1 on main ]
   └── Test Pipeline fails on `main` (Attempt 1).
   └── Watchdog detects Attempt 1.
   └── Action: Reruns the job once to check for flaky issues (no branch or AI called yet).

[ Step 2: Failure 2 on main ]
   └── The rerun finishes and fails again on `main` (Attempt 2).
   └── Watchdog detects: "This is a real code failure, not a flaky glitch."
   └── Action: Initiates autonomous AI repair in the Watchdog runner.

[ Step 3: DeepSeek AI Diagnosis & Local Verification ]
   └── Auto-Healer creates the branch: `fix/<job-name>-<datetime_in_est>`
   └── Fetches terminal error logs from the failed job via GitHub API.
   └── Reads repository files to build context for DeepSeek AI (`deepseek-chat`).
   └── DeepSeek diagnoses the root cause and generates the exact code patch.
   └── Auto-Healer applies the patch and runs validation tests locally in the runner.

[ Step 4: Branch Push & Automated Pull Request ]
   └── If local tests pass:
         ├── Commits and pushes the fix to `fix/<job-name>-<datetime_in_est>`.
         └── Automatically opens a Pull Request into `main`!
   └── If local tests fail:
         └── Aborts to prevent pushing broken code.
```

---

## 🚀 Setting Up on a New Repository (Complete Step-by-Step Guide)

Follow these steps to set up Pipeline Healer on any new GitHub repository:

### Step 1: Copy the Watchdog Files into the New Repo

Copy these files into the new repository:
```text
your-new-repo/
├── .github/
│   └── workflows/
│       └── watchdog.yml          <-- The Auto-Healer workflow
└── scripts/
    ├── __init__.py               <-- Package marker
    ├── ai_healer.py              <-- DeepSeek AI engine & patcher
    └── watchdog_handler.py       <-- Orchestrator (rerun, branch, PR)
```

In `.github/workflows/watchdog.yml`, ensure the workflow name matches the workflow you want to watch:
```yaml
on:
  workflow_run:
    workflows: ["Your CI Workflow Name"]   # <-- Replace with your target workflow's 'name'
    types:
      - completed
```

---

### Step 2: Generate a GitHub Fine-Grained Token

1. Go to: **[https://github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta)**
2. Click **Generate new token**.
3. **Token name**: `pipeline-healer-token` (or your choice).
4. **Repository access**: Select **"Only select repositories"** -> choose your new repository.
5. Under **Repository permissions**, configure these 3 permissions:

| Permission | Access Level | Why It's Needed |
| :--- | :--- | :--- |
| **Actions** | **Read and write** | To check workflow run status and trigger reruns |
| **Contents** | **Read and write** | To create repair branches and push code fixes |
| **Pull requests** | **Read and write** | To automatically open the Pull Request into `main` |
| **Metadata** | **Read-only** | *(Automatically checked by GitHub)* |

6. Click **Generate token** and copy the `github_pat_...` string.

---

### Step 3: Configure Repository Settings & Secrets on GitHub

Go to your new repository on GitHub:

#### 1. Enable Workflow Write Permissions:
1. Navigate to: **Settings → Actions → General**
2. Scroll to **Workflow permissions**:
   - Select **"Read and write permissions"**
   - Check the box: **"Allow GitHub Actions to create and approve pull requests"**
3. Click **Save**.

#### 2. Add Secrets:
1. Navigate to: **Settings → Secrets and variables → Actions**
2. Click **New repository secret** and add:
   - **`WATCHDOG_TOKEN`**: Paste your `github_pat_...` token from Step 2.
   - **`DEEPSEEK_API_KEY`**: Paste your DeepSeek API key (`sk-...`).
   - *(Optional)* **`OPENAI_API_KEY`**: If using OpenAI instead of DeepSeek.

---

### Step 4: Customize the Local Test Command (Optional)

In [`scripts/watchdog_handler.py`](scripts/watchdog_handler.py), inside `run_validation_tests()`, you can customize the command used to verify the AI's fix before pushing:

```python
def run_validation_tests() -> Tuple[bool, str]:
    # Default runs Python unittest:
    res = subprocess.run([sys.executable, "-m", "unittest", "discover", "tests"], capture_output=True, text=True)
    
    # Or customize for pytest, npm test, etc.:
    # res = subprocess.run(["pytest"], capture_output=True, text=True)
    # res = subprocess.run(["npm", "test"], capture_output=True, text=True)
```

---

## 🏷️ Branch & PR Naming Conventions

- **Repair Branch Format**:
  ```text
  fix/<failed-job-name>-<YYYY-MM-DD_HH-MM-SS_est>
  ```
  *Example*: `fix/run-unit-tests-quality-checks-2026-11-04_20-44-22_est`
- **Timezone**: Evaluated in `America/New_York` (EST/EDT).
- **PR Title**: Set to the exact name of the job that failed (e.g., `Run Unit Tests & Quality Checks`).
- **Commit Message**: Header is the job name, body contains the DeepSeek diagnosis and fix explanation.

---

## 🛠️ Switching AI Providers (DeepSeek vs. OpenAI)

DeepSeek (`deepseek-chat`) is configured as the active primary AI in [`scripts/ai_healer.py`](scripts/ai_healer.py).

To switch to **OpenAI (`gpt-4o-mini` / `gpt-4o`)**:
1. Add `OPENAI_API_KEY` to your repository secrets.
2. In [`scripts/ai_healer.py`](scripts/ai_healer.py), uncomment the OpenAI block in `diagnose_and_fix()`:
   ```python
   # openai_key = os.environ.get("OPENAI_API_KEY")
   # if openai_key:
   #     response_text = call_openai(openai_key, prompt)
   ```

---

## 🧪 Testing the Watchdog

1. Push code with an intentional bug to `main` (or trigger your CI workflow).
2. **Attempt 1**: The CI pipeline fails. Watchdog triggers and calls `rerun`.
3. **Attempt 2**: The rerun fails. Watchdog calls DeepSeek, fixes the code, runs validation tests, pushes the branch, and **opens a verified Pull Request**!
