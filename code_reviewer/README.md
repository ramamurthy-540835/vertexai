# GLM Code Reviewer 🤖🔍

An autonomous, production-grade codebase reviewer utilizing **GLM-5.1 Sparse Mixture-of-Experts (MoE)** models via OpenAI-compatible endpoints (such as NVIDIA NIM or local gateways).

This module recursively traverses workspace folders, reads code files, contacts the GLM-5.1 endpoint, streams deep reasoning thoughts, identifies architectural patterns, highlights potential bugs/leaks, and writes comprehensive markdown review reports.

---

## 📂 Directory Structure

```directory
code_reviewer/
├── .env.local              # Local credentials, API keys, and model parameter tuning
├── reports/                # Automatically populated markdown code review reports
│   ├── review_business_rules_py.md
│   └── review_job_runner_py.md
└── reviewer.py             # Main CLI execution engine
```

---

## 🛠️ Setup & Configuration

### 1. Requirements
* Python 3.10+
* `openai` Python SDK (installed via `pip install openai`)

### 2. Configure Environment (`.env.local`)
Create a file named `.env.local` inside this directory to populate your API endpoints and key. The script will automatically discover and parse this file on execution:

```ini
# NVIDIA OpenAI-Compatible API Endpoint URL
GLM_API_BASE_URL=https://integrate.api.nvidia.com/v1

# NVIDIA API Key (Used to authorize requests to GLM)
GLM_API_KEY=your-nvapi-key-here

# GLM Model Identifier (Using Sparse MoE z-ai/glm-5.1)
GLM_MODEL_NAME=z-ai/glm-5.1

# Temperature setting for creative analysis (lower is more analytical)
GLM_TEMPERATURE=0.2

# Top P nucleus sampling filter
GLM_TOP_P=0.95

# Maximum context response token count (up to 16,384)
GLM_MAX_TOKENS=16384
```

---

## 🚀 Usage Guide

You can run the reviewer from the workspace root or inside the folder:

```bash
# 1. Review a single file (e.g. business_rules.py)
python3 code_reviewer/reviewer.py lead_match_runtime/business_rules.py

# 2. Review all files inside a whole directory recursively
python3 code_reviewer/reviewer.py lead_match_runtime/

# 3. Review all default pipeline files in the workspace
python3 code_reviewer/reviewer.py --all

# 4. Save reviews to a custom directory instead of reports/
python3 code_reviewer/reviewer.py --all --out-dir custom_reports_folder
```

---

## 🌟 Portable Reuse Instructions

This directory is designed to be **100% self-contained and portable**. To use it in **any other software project**:
1. Copy the entire `code_reviewer` folder into your new project's repository.
2. Ensure you have the `openai` SDK installed (`pip install openai`).
3. Update the `.env.local` with your target GLM API keys or endpoint.
4. Open the `reviewer.py` file and update the `DEFAULT_REVIEW_TARGETS` list with the relative file paths of your new project's main codebase files for quick `--all` scanning.
5. Run `python3 code_reviewer/reviewer.py <your-src-files>` to start generating reports instantly!
