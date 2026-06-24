import os
import sys
import argparse
from pathlib import Path
from openai import OpenAI

# Terminal coloring
_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
_REASONING_COLOR = "\033[90m" if _USE_COLOR else ""
_GREEN_COLOR = "\033[32m" if _USE_COLOR else ""
_CYAN_COLOR = "\033[36m" if _USE_COLOR else ""
_YELLOW_COLOR = "\033[33m" if _USE_COLOR else ""
_RESET_COLOR = "\033[0m" if _USE_COLOR else ""

# Target files for quick-review across the workspace
DEFAULT_REVIEW_TARGETS = [
    "lead_match_runtime/job_runner.py",
    "lead_match_runtime/business_rules.py",
    "lead_match_runtime/report.py",
    "lead_match_codebase/src/costco/leadmgmt/components/fuzzy_matching_sql.py",
    "lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_leads.py",
    "lead_match_codebase/src/costco/leadmgmt/components/vector_db_loading_pos.py",
    "deploy/lead_match_workflow.yaml",
    ".github/workflows/lead_match_semantic_workflow.yml"
]

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
        description="GLM-5.1 Code Reviewer: Analyzes logic, potential bugs, strengths, and missing components."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Path(s) to files or folders to review. If empty, runs default workspace reviews."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Review all default pipeline files in the workspace."
    )
    parser.add_argument(
        "--out-dir",
        default="reports",
        help="Directory to save the markdown review reports (default: 'reports')."
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

def review_file(client, file_path: Path, out_dir: Path):
    if not file_path.exists():
        print(f"{_YELLOW_COLOR}Skipping {file_path}: File does not exist.{_RESET_COLOR}")
        return

    print(f"\n{_CYAN_COLOR}======================================================================{_RESET_COLOR}")
    print(f"{_CYAN_COLOR}Analyzing Codebase Logic: {file_path}{_RESET_COLOR}")
    print(f"{_CYAN_COLOR}======================================================================{_RESET_COLOR}\n")

    try:
        content = file_path.read_text(errors="ignore")
    except Exception as e:
        print(f"{_YELLOW_COLOR}Failed to read file {file_path}: {e}{_RESET_COLOR}")
        return

    # Build prompt for GLM-5.1
    system_prompt = (
        "You are an expert principal software engineer and systems architect specializing in "
        "enterprise data pipelines, high-volume vector databases, API safety, and Cloud workflows. "
        "Your task is to perform an exceptionally thorough, architectural, and logical code review."
    )

    user_prompt = f"""Please perform an in-depth code review of the following source file from our GCP Vertex AI Matching Pipeline.

Review criteria you MUST cover:
1. **Core Logic & Flow**: Detail the architectural structure, design patterns, and flow of logic. Explain exactly how the code executes its goals.
2. **Abilities & Strengths**: Identify what the code does exceptionally well, its strengths, clean abstractions, and key capabilities.
3. **Potential Bugs & Edge Cases**: Scan for structural or logical issues (e.g. leaked connections, database autocommit behavior, missing try/finally blocks, API mismatch issues, unhandled exceptions, or missing unique constraints/inserts). Be precise and point to specific lines of code.
4. **Missing Parts & Gaps**: Highlight missing features, missing validation of API response counts, missing timeout implementations, or unhandled failure states.
5. **Actionable Recommendations**: Provide clean, concrete, production-grade refactoring steps or code snippets to fix the identified gaps and elevate the code to principal engineering standards.

Source File: {file_path}
--------------------------------------------------------------------------------
Source Code:
```python
{content}
```
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]

    model_name = os.environ.get("GLM_MODEL_NAME", "z-ai/glm-5.1")
    temp = float(os.environ.get("GLM_TEMPERATURE", "0.2"))
    top_p = float(os.environ.get("GLM_TOP_P", "0.95"))
    max_tokens = int(os.environ.get("GLM_MAX_TOKENS", "16384"))

    print(f"{_GREEN_COLOR}Contacting model {model_name} at {os.environ.get('GLM_API_BASE_URL', 'NVIDIA')}... Streaming output:{_RESET_COLOR}\n")

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
        print(f"{_YELLOW_COLOR}API call failed for {file_path}: {e}{_RESET_COLOR}")
        return

    review_output = []
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
            review_output.append(content_chunk)

    if in_reasoning:
        sys.stdout.write(_RESET_COLOR)
        print()

    full_review_text = "".join(review_output)

    # Save to a markdown file
    out_dir.mkdir(parents=True, exist_ok=True)
    sanitized_name = f"review_{file_path.name.replace('.', '_')}.md"
    report_file = out_dir / sanitized_name
    
    try:
        report_file.write_text(full_review_text, encoding="utf-8")
        print(f"\n\n{_GREEN_COLOR}✓ Review saved successfully to: {report_file}{_RESET_COLOR}")
    except Exception as e:
        print(f"\n\n{_YELLOW_COLOR}Warning: Failed to write review file: {e}{_RESET_COLOR}")

def main():
    args = parse_arguments()
    client = init_openai_client()

    # Determine files to review
    target_paths = []
    if args.paths:
        for p in args.paths:
            path_obj = Path(p)
            if path_obj.is_dir():
                # Gather python and yaml files in the directory
                for ext in ["*.py", "*.yaml", "*.yml"]:
                    target_paths.extend(path_obj.glob(f"**/{ext}"))
            else:
                target_paths.append(path_obj)
    elif args.all:
        target_paths = [Path(p) for p in DEFAULT_REVIEW_TARGETS]
    else:
        # Prompt the user with options or default to interactive choices
        print(f"{_CYAN_COLOR}No files specified. Running review on default targets...{_RESET_COLOR}")
        target_paths = [Path(p) for p in DEFAULT_REVIEW_TARGETS]

    # Convert paths to resolve relative to workspace root if they exist
    resolved_paths = []
    for p in target_paths:
        if p.exists():
            resolved_paths.append(p)
        else:
            # Try to resolve relative to current file directory
            local_path = Path(__file__).parent.parent / p
            if local_path.exists():
                resolved_paths.append(local_path)
            else:
                print(f"{_YELLOW_COLOR}File not found: {p}{_RESET_COLOR}")

    if not resolved_paths:
        print(f"{_YELLOW_COLOR}No valid files to review.{_RESET_COLOR}")
        sys.exit(1)

    # Output directory resolution
    script_dir = Path(__file__).parent
    out_dir = script_dir / args.out_dir

    for path in resolved_paths:
        review_file(client, path, out_dir)

if __name__ == "__main__":
    main()
