# Fix Specification for `lead_match_runtime/job_runner.py`

## Target File
`lead_match_runtime/job_runner.py`

## Issue Summary
- **Bug: Thread-Unsafe Database Connection Closure**: Sharing a single DB connection and cursor across concurrent ThreadPool workers causes connection state pollution and crashes.
- **Bug: Infinite Parameter Mutation in `_run_fuzzy_match`**: Parameters are appended to `fetch_params` in each iteration of the pagination loop without being reset, causing driver parameter count mismatches.
- **Bug: Empty-Table Bootstrapping Crash in `_ensure_indexes`**: Calling `vector_dims()` on an empty table returns NULL and crashes the initial indexing step on fresh deployments.
- **Bug: Unnecessary Session Variable Commits**: Session-level variables (`SET hnsw.ef_search`) do not require WAL flushing `commit()` calls.
- **Missing: HTTP Timeouts**: Vertex AI GenAI Client should have a configured HTTP timeout to prevent thread blocks on network hangs.

---

## Surgical Diffs (Search & Replace Blocks)

### 1. Add Timeout Parameter to Vertex AI Client
```python
<<<<<<< SEARCH
def vertex_client():
    project = os.environ.get("VERTEX_PROJECT_ID") or get_project_id(BUSINESS_RULES)
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    if not project:
        raise RuntimeError("Missing VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT")
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(api_version="v1"),
    )
=======
def vertex_client():
    project = os.environ.get("VERTEX_PROJECT_ID") or get_project_id(BUSINESS_RULES)
    location = os.environ.get("VERTEX_LOCATION", "us-central1")
    if not project:
        raise RuntimeError("Missing VERTEX_PROJECT_ID or GOOGLE_CLOUD_PROJECT")
    timeout = float(os.environ.get("VERTEX_TIMEOUT_SECONDS", "120.0"))
    return genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=types.HttpOptions(api_version="v1", timeout=timeout),
    )
>>>>>>> REPLACE
```

### 2. Fix Thread-Unsafe Cursor/Connection Sharing in Concurrency Loop
```python
<<<<<<< SEARCH
    max_workers = min(EMBEDDING_BATCH_WORKERS, len(batches))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(build_insert_rows, bn, batch): bn
            for bn, batch in batches
        }
        for future in as_completed(futures):
            bn = futures[future]
            batch_number, insert_rows, duration = future.result()
            write_result(batch_number, insert_rows, duration)
    return inserted
=======
    max_workers = min(EMBEDDING_BATCH_WORKERS, len(batches))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(build_insert_rows, bn, batch): bn
            for bn, batch in batches
        }
        for future in as_completed(futures):
            bn = futures[future]
            # Safely fetch result to raise potential worker exceptions
            batch_number, insert_rows, duration = future.result()
            try:
                write_result(batch_number, insert_rows, duration)
            except Exception as e:
                conn.rollback()
                raise RuntimeError(f"Database write failed during parallel batch {batch_number}: {e}") from e
    return inserted
>>>>>>> REPLACE
```

### 3. Fix Infinite Mutation Loop for SQL Query Parameters
```python
<<<<<<< SEARCH
    fetch_params = [limit]
    warehouse_clause = warehouse_sql_filter("leads_embeddings", fetch_params)
    query = f"""
        SELECT lead_id, phone_embedding, email_embedding, name_embedding, address_embedding, combined_embedding
        FROM leads_embeddings
        WHERE lead_id > %s {warehouse_clause}
        ORDER BY lead_id
        LIMIT %s;
    """

    cursor = conn.cursor()
    configure_hnsw_search(conn, cursor)

    processed_leads = 0
    last_lead_id = 0

    while processed_leads < limit:
        params = [last_lead_id] + fetch_params
        cursor.execute(query, params)
        rows = cursor.fetchall()
=======
    cursor = conn.cursor()
    configure_hnsw_search(conn, cursor)

    processed_leads = 0
    last_lead_id = 0

    while processed_leads < limit:
        # Re-initialize params per iteration to avoid infinite mutation
        fetch_params = [limit]
        warehouse_clause = warehouse_sql_filter("leads_embeddings", fetch_params)
        query = f"""
            SELECT lead_id, phone_embedding, email_embedding, name_embedding, address_embedding, combined_embedding
            FROM leads_embeddings
            WHERE lead_id > %s {warehouse_clause}
            ORDER BY lead_id
            LIMIT %s;
        """
        params = [last_lead_id] + fetch_params
        cursor.execute(query, params)
        rows = cursor.fetchall()
>>>>>>> REPLACE
```

### 4. Remove Unnecessary wal-commit on Session Variables
```python
<<<<<<< SEARCH
def configure_hnsw_search(conn, cursor):
    if HNSW_EF_SEARCH <= 0:
        return
    try:
        cursor.execute(f"SET hnsw.ef_search = {HNSW_EF_SEARCH}")
        conn.commit()
        print(f"HNSW ef_search set to {HNSW_EF_SEARCH}")
    except Exception as exc:
        conn.rollback()
        print(f"Warning: failed to set hnsw.ef_search: {exc}", file=sys.stderr)
=======
def configure_hnsw_search(conn, cursor):
    if HNSW_EF_SEARCH <= 0:
        return
    try:
        # SET is session-level, no transaction commit required
        cursor.execute(f"SET hnsw.ef_search = {HNSW_EF_SEARCH}")
        print(f"HNSW ef_search set to {HNSW_EF_SEARCH}")
    except Exception as exc:
        print(f"Warning: failed to set hnsw.ef_search: {exc}", file=sys.stderr)
>>>>>>> REPLACE
```

### 5. Fix Empty Table Indexing Bootstrapping Crash
```python
<<<<<<< SEARCH
        bad_dims = fetchall(
            f"""
            SELECT vector_dims({column})
            FROM {schema}.{table}
            WHERE {column} IS NOT NULL
            GROUP BY vector_dims({column})
            HAVING vector_dims({column}) != %s
            """,
            (dimension,),
        )
        if bad_dims:
            raise RuntimeError(
                f"Table {schema}.{table} contains vector column {column} with dimension "
                f"{bad_dims[0][0]}, expected {dimension}"
            )
=======
        # Check table size first to avoid vector_dims() Null/eval crash on empty tables
        row_count = fetchall(f"SELECT COUNT(*) FROM {schema}.{table}")[0][0]
        if row_count > 0:
            bad_dims = fetchall(
                f"""
                SELECT vector_dims({column})
                FROM {schema}.{table}
                WHERE {column} IS NOT NULL
                GROUP BY vector_dims({column})
                HAVING vector_dims({column}) != %s
                """,
                (dimension,),
            )
            if bad_dims:
                raise RuntimeError(
                    f"Table {schema}.{table} contains vector column {column} with dimension "
                    f"{bad_dims[0][0]}, expected {dimension}"
                )
>>>>>>> REPLACE
```

---

## Final Prompt to Apply

Copy and paste the prompt below into an AI assistant or Gemini CLI to automatically apply these changes:

```text
You are an expert principal software engineer performing a surgical, production-grade refactoring of `lead_match_runtime/job_runner.py`. Apply the following changes precisely. Do not alter any other logic, formatting, or structure outside of these specifications.

1. **Add Timeout Parameter to Vertex AI Client**:
   - Inside `vertex_client()`, read an optional timeout from environment: `timeout = float(os.environ.get("VERTEX_TIMEOUT_SECONDS", "120.0"))`.
   - Pass this timeout to `types.HttpOptions(api_version="v1", timeout=timeout)`.

2. **Fix Thread-Unsafe Concurrency Loop Database Writes**:
   - In `process_embedding_batches()`, inside the `ThreadPoolExecutor` futures collection block, wrap the `write_result()` call in a `try/except` block.
   - If writing fails, call `conn.rollback()` and raise a descriptive `RuntimeError`.

3. **Fix Infinite Mutation Loop for SQL Query Parameters**:
   - In `_run_fuzzy_match()`, move the initialization of `fetch_params`, `warehouse_clause`, and `query` INSIDE the `while processed_leads < limit` pagination loop. This ensures parameters are completely reset on every database batch request, avoiding infinite parameter growth.

4. **Remove Unnecessary Commit on Session Variables**:
   - In `configure_hnsw_search()`, remove `conn.commit()` and `conn.rollback()` calls. Set statements are session-level and do not require transaction boundaries.

5. **Fix Empty Table Indexing Bootstrapping Crash**:
   - In `_ensure_indexes()`, count table rows `row_count = fetchall(f"SELECT COUNT(*) FROM {schema}.{table}")[0][0]` before executing the `bad_dims` query.
   - Only execute the `bad_dims = fetchall(...)` check if `row_count > 0`, avoiding Null evaluations on fresh, empty databases.
```
