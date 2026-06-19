#!/usr/bin/env python3
"""Smoke test for Vertex AI Gemini embeddings."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from google import genai


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Gemini embedding client smoke test.")
    parser.add_argument("--project-id", required=True, help="Google Cloud project ID")
    parser.add_argument("--location", default="us-central1", help="Vertex AI location")
    parser.add_argument("--model", default="gemini-embedding-001", help="Embedding model name")
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
        client = genai.Client(vertexai=True, project=args.project_id, location=args.location)
        response = client.models.embed_content(
            model=args.model,
            contents="Warehouse 115 validation smoke test",
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
