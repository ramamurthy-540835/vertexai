# Lead Match Reporting App

Separate Next.js reporting layer for lead match results already written to GCS.

It does not run embeddings, fuzzy matching, or report generation. It only reads report artifacts and exposes secure HTTPS API/UI access for ServiceNow and Costco users.

## Environment

- `REPORT_BUCKET`: GCS bucket containing reports. Default: `lead-match-ctoteam`
- `REPORT_PREFIX`: report root prefix. Default: `reports/lead_match`
- `REPORTING_API_TOKEN`: optional bearer token for app-level API access
- `GOOGLE_CLOUD_PROJECT`: project for Google auth
- `VERTEX_PROJECT_ID`: optional project for Talk with Data
- `VERTEX_LOCATION`: optional Vertex location
- `GEMINI_MODEL`: optional Gemini model name

Cloud Run should also be deployed with `--no-allow-unauthenticated` so IAM is the primary boundary. `REPORTING_API_TOKEN` is an extra guard for API callers such as ServiceNow.

## Endpoints

- `GET /api/health`
- `GET /api/results/latest?warehouse=115`
- `GET /api/results/search?warehouse=115&lead_id=...&run_id=...`
- `GET /api/results/download?run_id=jobchain-115-...&warehouse=115&type=csv`
- `GET /api/results/graph?warehouse=115&run_id=jobchain-115-...`
- `GET /api/annotations?run_id=jobchain-115-...`
- `POST /api/annotations`
- `POST /api/ask`

## UI

- `/` latest status and navigation
- `/search` filter match rows
- `/graph` Neo4j-style match exploration
- `/talk-with-data` optional Gemini summary/Q&A
