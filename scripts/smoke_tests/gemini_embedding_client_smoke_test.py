#!/usr/bin/env python3
"""Smoke test for Vertex AI Gemini embeddings."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import google.auth
from google.auth import impersonated_credentials
from google import genai

try:
    from lead_match_runtime.business_rules import load_business_rules, get_embedding_model, get_vertex_location
    _RULES = load_business_rules()
    _DEFAULT_MODEL = get_embedding_model(_RULES)
    _DEFAULT_LOCATION = get_vertex_location(_RULES)
except Exception:
    _DEFAULT_MODEL = "gemini-embedding-001"
    _DEFAULT_LOCATION = "us-central1"


def get_credentials():
    target_service_account = os.environ.get("TARGET_SERVICE_ACCOUNT")
    if not target_service_account:
        return None

    source_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    return impersonated_credentials.Credentials(
        source_credentials=source_credentials,
        target_principal=target_service_account,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Gemini embedding client smoke test.")
    parser.add_argument("--project-id", required=True, help="Google Cloud project ID")
    parser.add_argument("--location", default=_DEFAULT_LOCATION, help="Vertex AI location")
    parser.add_argument("--model", default=_DEFAULT_MODEL, help="Embedding model name")
    parser.add_argument(
        "--output",
        default="gemini_embedding_client_smoke_test_results.json",
        help="Output JSON file",
    )
    args = parser.parse_args()

    result = {
        "timestamp": datetime.now().isoformat(),
        "project_id": args.project_id,
        "location": args.location,
        "model": args.model,
        "status": "failed",
    }

    try:
        client = genai.Client(
            vertexai=True,
            project=args.project_id,
            location=args.location,
            credentials=get_credentials(),
        )
        response = client.models.embed_content(
            model=args.model,
            contents="Lead-to-POS validation smoke test",
        )
        embeddings = getattr(response, "embeddings", None) or []
        first_embedding = embeddings[0] if embeddings else None
        values = getattr(first_embedding, "values", None) if first_embedding else None

        if not values:
            raise RuntimeError("Embedding response did not include vector values")

        result.update(
            {
                "status": "passed",
                "embedding_count": len(embeddings),
                "embedding_dimension": len(values),
            }
        )
        print(
            f"[OK] Gemini embedding smoke test passed "
            f"({len(embeddings)} embedding, {len(values)} dimensions)."
        )
        return_code = 0
    except Exception as exc:
        result["error"] = str(exc)
        print(f"[ERROR] Gemini embedding smoke test failed: {exc}", file=sys.stderr)
        return_code = 1
    finally:
        output_path = Path(args.output)
        with output_path.open("w") as f:
            json.dump(result, f, indent=2)
        print(f"[INFO] Results written to {output_path.absolute()}")

    return return_code


if __name__ == "__main__":
    sys.exit(main())
