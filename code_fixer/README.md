# GLM Code Fixer 🤖🛠️

An autonomous, production-grade automated code fixing utility utilizing **GLM-5.1 Sparse Mixture-of-Experts (MoE)** models via OpenAI-compatible endpoints (such as NVIDIA NIM or local gateways).

This module ingests Code Review Reports (markdown), automatically maps them back to their original source files, reads the original code, and calls the GLM-5.1 endpoint to output highly structured **Fix Specifications** containing detailed logical summaries, unique **SEARCH/REPLACE blocks**, and a **self-contained prompt** to automate the refactoring.

---

## 📂 Directory Structure

```directory
code_fixer/
├── .env.local              # Local credentials, API keys, and model parameter tuning
├── prompts/                # Automatically populated markdown Fix Specifications
│   ├── fix_spec_business_rules_py.md
│   └── fix_spec_job_runner_py.md
└── fixer.py                # Main CLI execution engine
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
GLM_TEMPERATURE=0.1

# Top P nucleus sampling filter
GLM_TOP_P=0.95

# Maximum context response token count (up to 16,384)
GLM_MAX_TOKENS=16384
```

---

## 🚀 Usage Guide

You can run the fixer from the workspace root or inside the folder:

```bash
# 1. Design fixes for a single report (e.g. review_business_rules_py.md)
python3 code_fixer/fixer.py review_business_rules_py.md

# 2. Design fixes for all reports located in the default code_reviewer/reports/ directory
python3 code_fixer/fixer.py

# 3. Save Fix Specifications to a custom directory instead of prompts/
python3 code_fixer/fixer.py --out-dir custom_prompts_folder
```

---

## 🌟 Portable Reuse Instructions

This directory is designed to be **100% self-contained and portable**. To use it in **any other software project**:
1. Copy the entire `code_fixer` folder into your new project's repository.
2. Ensure you have the `openai` SDK installed (`pip install openai`).
3. Update the `.env.local` with your target GLM API keys or endpoint.
4. Open the `fixer.py` file and update the `DEFAULT_SOURCE_MAPPING` dictionary with the mapping of report names to original source file names for fallback resolution.
5. Place any generated Code Review Reports under `code_reviewer/reports/` (or specify via `--reports-dir`).
6. Run `python3 code_fixer/fixer.py <report-file>` to start generating precise search-and-replace Fix Specifications instantly!
