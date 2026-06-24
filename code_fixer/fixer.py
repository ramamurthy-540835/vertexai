import os
import sys
import argparse
import re
from pathlib import Path
from openai import OpenAI

# Terminal coloring
_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
_REASONING_COLOR = "\033[90m" if _USE_COLOR else ""
_GREEN_COLOR = "\033[32m" if _USE_COLOR else ""
_CYAN_COLOR = "\033[36m" if _USE_COLOR else ""
_YELLOW_COLOR = "\033[33m" if _USE_COLOR else ""
_RESET_COLOR = "\033[0m" if _USE_COLOR else ""

# Map of report files to original source files (as fallback if regex parse fails)
DEFAULT_SOURCE_MAPPING = {
    "review_business_rules_py.md": "lead_match_runtime/business_rules.py",
    "review_job_runner_py.md": "lead_match_runtime/job_runner.py",
    "review_report_py.md": "lead_match_runtime/report.py",
    "review_fuzzy_matching_sql_py.md": "lead_match_codebase/src/costco/leadmgmt/components/fuzzy_matching_sql.py",
    "review_vector_db_loading_leads_py.md": "lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_leads.py",
    "review_vector_db_loading_pos_py.md": "lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_pos.py",
    "review_lead_match_workflow_yaml.md": "deploy/lead_match_workflow.yaml",
    "review_lead_match_semantic_workflow_yml.md": ".github/workflows/lead_match_semantic_workflow.yml"
}

def load_env_local():
    """Loads environment variables from .env.local file natively to preserve portability."""
    script_dir = Path(__file__).parent
    env_path = script_dir / ".env.local"
    if not env_path.exists():
        # Fallback to current working directory or workspace root
        env_path = Path(".env.local")
        if not env_path.exists():
            return

    try:
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                # Strip spaces and optional wrapping quotes
                k = key.strip()
                v = val.strip().strip("'\"")
                os.environ.setdefault(k, v)
    except Exception as e:
        print(f"{_YELLOW_COLOR}Warning: Failed to load .env.local: {e}{_RESET_COLOR}")

# Run environment setup at import/startup time
load_env_local()

def parse_arguments():
    parser = argparse.ArgumentParser(
        description="GLM-5.1 Code Fixer: Reads reviews, designs precise code changes, and saves them as Markdown prompts."
    )
    parser.add_argument(
        "reports",
        nargs="*",
        help="Specific markdown report file(s) in code_reviewer/reports/ to design fixes for."
    )
    parser.add_argument(
        "--reports-dir",
        default="code_reviewer/reports",
        help="Directory where code_reviewer reports are located."
    )
    parser.add_argument(
        "--out-dir",
        default="prompts",
        help="Directory to save the markdown fix prompts (default: 'prompts')."
    )
    return parser.parse_args()

def init_openai_client():
    # Retrieve configuration with fallback defaults
    api_key = os.environ.get("GLM_API_KEY", "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")
    base_url = os.environ.get("GLM_API_BASE_URL", "https://integrate.api.nvidia.com/v1")
    
    return OpenAI(
        base_url=base_url,
        api_key=api_key
    )

def extract_source_file_from_report(report_path: Path, report_content: str) -> Path | None:
    # 1. Try mapping by filename
    report_name = report_path.name
    if report_name in DEFAULT_SOURCE_MAPPING:
        source_path = Path(DEFAULT_SOURCE_MAPPING[report_name])
        if source_path.exists():
            return source_path

    # 2. Try regex to find "Analyzing Codebase Logic: <file_path>" or "<file_path>:"
    match = re.search(r"Analyzing Codebase Logic:\s*([^\s\n]+)", report_content)
    if match:
        source_path = Path(match.group(1).strip())
        if source_path.exists():
            return source_path

    # 3. Try to locate the original file by matching name stems
    m_stem = report_path.stem.replace("review_", "")
    # Check if we can find a python or yaml file in the directory
    for root, _, files in os.walk("."):
        # Skip standard dirs
        if any(skip in root for skip in [".git", ".venv", "node_modules", ".next"]):
            continue
        for f in files:
            f_path = Path(root) / f
            sanitized_f = f.replace(".", "_")
            if sanitized_f == m_stem or f == m_stem:
                return f_path

    return None

def design_fix_specification(client, report_path: Path, source_path: Path, out_dir: Path):
    print(f"\n{_CYAN_COLOR}======================================================================{_RESET_COLOR}")
    print(f"{_CYAN_COLOR}Designing Fix Specification: {source_path}{_RESET_COLOR}")
    print(f"{_CYAN_COLOR}Based on Report: {report_path.name}{_RESET_COLOR}")
    print(f"{_CYAN_COLOR}======================================================================{_RESET_COLOR}\n")

    try:
        report_text = report_path.read_text(errors="ignore")
        source_code = source_path.read_text(errors="ignore")
    except Exception as e:
        print(f"{_YELLOW_COLOR}Failed to read input files: {e}{_RESET_COLOR}")
        return

    # Prompt design: Ask GLM-5.1 to design a precise, promptable fix spec
    system_prompt = (
        "You are an expert principal software engineer specializing in automated codebase "
        "modifications. Your goal is to design extremely precise, surgical, and prompt-friendly "
        "fix specifications that show exactly what needs to be changed in a source file based on a code review report."
    )

    user_prompt = f"""You are designing a **Fix Specification and prompt** for the file `{source_path}`.
Here is the Code Review Report detailing potential bugs, logical issues, and recommendations:
--------------------------------------------------------------------------------
{report_text}
--------------------------------------------------------------------------------

Here is the original source code of `{source_path}`:
--------------------------------------------------------------------------------
```python
{source_code}
```
--------------------------------------------------------------------------------

Please generate a structured Markdown file containing:
1. **Target File**: `{source_path}`
2. **Issue Summary**: A bulleted list of the exact bugs/gaps we are fixing from the report.
3. **Surgical Diffs (Search & Replace Blocks)**:
   For EACH issue, provide a search-and-replace block of the format:
   ```python
   <<<<<<< SEARCH
   [Exact original code snippet from the file]
   =======
   [Improved, production-grade replacement code]
   >>>>>>> REPLACE
   ```
   *Make sure the SEARCH block contains enough context to be completely unique.*
4. **Final Prompt to Apply**: A self-contained, highly optimized prompt that we can paste directly into an AI assistant or Gemini CLI to automatically apply these changes to the codebase.

Design your fixes with extreme technical safety (e.g. close connections on exceptions, proper typing, proper thread safety, retry capabilities). Output the specification now.
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    model_name = os.environ.get("GLM_MODEL_NAME", "z-ai/glm-5.1")
    temp = float(os.environ.get("GLM_TEMPERATURE", "0.1"))
    top_p = float(os.environ.get("GLM_TOP_P", "0.95"))
    max_tokens = int(os.environ.get("GLM_MAX_TOKENS", "16384"))

    print(f"{_GREEN_COLOR}Contacting model {model_name}... Streaming Fix Specification in real-time:{_RESET_COLOR}\n")

    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temp,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True
        )
    except Exception as e:
        print(f"{_YELLOW_COLOR}API call failed: {e}{_RESET_COLOR}")
        return

    fix_output = []
    in_reasoning = False

    for chunk in completion:
        if not getattr(chunk, "choices", None) or len(chunk.choices) == 0:
            continue
        delta = chunk.choices[0].delta
        
        # Check for reasoning/thought content
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            if not in_reasoning:
                sys.stdout.write(_REASONING_COLOR)
                in_reasoning = True
            sys.stdout.write(reasoning)
            sys.stdout.flush()
            continue

        # Check for standard response content
        content_chunk = getattr(delta, "content", None)
        if content_chunk:
            if in_reasoning:
                sys.stdout.write(_RESET_COLOR)
                in_reasoning = False
            sys.stdout.write(content_chunk)
            sys.stdout.flush()
            fix_output.append(content_chunk)

    if in_reasoning:
        sys.stdout.write(_RESET_COLOR)
        print()

    full_fix_text = "".join(fix_output)

    # Save prompt file
    out_dir.mkdir(parents=True, exist_ok=True)
    sanitized_name = f"fix_spec_{source_path.name.replace('.', '_')}.md"
    prompt_file = out_dir / sanitized_name

    try:
        prompt_file.write_text(full_fix_text, encoding="utf-8")
        print(f"\n\n{_GREEN_COLOR}✓ Fix Specification saved successfully to: {prompt_file}{_RESET_COLOR}")
    except Exception as e:
        print(f"\n\n{_YELLOW_COLOR}Warning: Failed to write prompt file: {e}{_RESET_COLOR}")

def main():
    args = parse_arguments()
    client = init_openai_client()

    # Locate report files
    reports_dir = Path(args.reports_dir)
    report_paths = []
    if args.reports:
        for r in args.reports:
            p = Path(r)
            if p.exists():
                report_paths.append(p)
            elif (reports_dir / r).exists():
                report_paths.append(reports_dir / r)
            else:
                print(f"{_YELLOW_COLOR}Report file not found: {r}{_RESET_COLOR}")
    else:
        # Scan reports directory for all .md files
        if reports_dir.exists():
            report_paths = list(reports_dir.glob("*.md"))
        else:
            print(f"{_YELLOW_COLOR}Reports directory {reports_dir} does not exist. Run the reviewer first!{_RESET_COLOR}")
            sys.exit(1)

    if not report_paths:
        print(f"{_YELLOW_COLOR}No report files found to design fixes for.{_RESET_COLOR}")
        sys.exit(1)

    # Resolve output directory
    script_dir = Path(__file__).parent
    out_dir = script_dir / args.out_dir

    for report_path in report_paths:
        try:
            content = report_path.read_text(errors="ignore")
        except Exception as e:
            print(f"{_YELLOW_COLOR}Failed to read report {report_path}: {e}{_RESET_COLOR}")
            continue

        source_path = extract_source_file_from_report(report_path, content)
        if not source_path:
            print(f"{_YELLOW_COLOR}Could not map report {report_path.name} to any local source file. Skipping.{_RESET_COLOR}")
            continue

        design_fix_specification(client, report_path, source_path, out_dir)

if __name__ == "__main__":
    main()
