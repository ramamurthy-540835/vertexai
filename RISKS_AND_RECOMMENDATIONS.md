# Architectural Gaps, Risks, and Recommendations

During our deep-dive codebase review of the Lead-to-POS Matching system, several architectural discrepancies, configuration risks, security concerns, and scalability limits were identified. This document catalogues these findings and recommends pragmatic remediation paths.

---

## 1. Primary Discrepancies and Gap Analysis

### A. The "Silent Bypass" of Vector Fuzzy Matching
*   **Gap:** The codebase contains a highly sophisticated semantic similarity module (`fuzzy_matching_sql.py`) that uses GenAI embeddings and PostgreSQL `pgvector` HNSW indexes to find matches when direct text matches fail. However, **this module is completely bypassed in production.**
*   **Detail:** 
    1.  The orchestrating Cloud Workflow (`lead_match_workflow.yaml`) triggers the `lead-match-job` sequential steps using `run.py`. It calls `primary_matching`, which triggers deterministic exact/score-based field matching in `lead_matching.py`.
    2.  The workflow then immediately invokes the update/sync branches (`update_database` and `update_service_now`). **It never executes a fuzzy matching stage.**
    3.  A `fuzzy_matching` step is defined in the Kubeflow pipeline (`pipeline_definition.py`), but the entire Kubeflow orchestrator block in `pipeline_definition.py` is commented out.
*   **Impact:** The system currently relies *only* on exact field alignments, meaning the massive business value of vector embedding-based fuzzy mapping remains unused.
*   **Recommendation:** Update the orchestrator (`lead_match_workflow.yaml`) and `run.py` to support and chain the `fuzzy_matching` stage immediately downstream of `primary_matching`, passing the exact classified CSV as input and feeding the fuzzy-overridden CSV as output to the sync steps.

### B. Conflicting Implementations of Primary Matching
*   **Gap:** The repository has two distinct and diverged python modules performing primary matching:
    1.  `lead_match_codebase/src/costco/leadmgmt/components/primary_matching.py`
    2.  `lead_match_codebase/src/costco/leadmgmt/components/lead_matching.py`
*   **Detail:** 
    *   `primary_matching.py` is an exact field scorer that performs simple vectorized field merges.
    *   `lead_matching.py` is a much more robust chronological processor that executes family-based scoring, handles OMS-specific attributes, implements batching, and manages **Closed-Existing** lead classification.
*   **Impact:** Maintaining two scripts with identical entrypoint functions (`primary_classification`) is highly error-prone and leads to developer confusion during upgrades.
*   **Recommendation:** Cleanly deprecate and delete `primary_matching.py`. Explicitly document that `lead_matching.py` is the official matching logic of the production cluster.

---

## 2. Security and Hardcoded Vulnerabilities

### A. Hardcoded ServiceNow Credentials
*   **Gap:** In `lead_match_codebase/main.py`, under the `snow_validation` block (lines 35-49), ServiceNow access credentials and API URLs are completely hardcoded:
    ```python
    url = "https://costcobizsvctest.service-now.com/api/sn_retail/lead_pos_data/getLead"
    username = 'lead.api.access'
    password = 'Costco@web123'
    ```
*   **Impact:** Storing plaintext usernames and API passwords in repositories exposes the environment to credential leaks.
*   **Recommendation:** Move these credentials into Secret Manager (already provisioned by Terraform as `service_now_client_id` and `service_now_client_secret`). Load them dynamically using `access_secret` inside the validation blocks.

---

## 3. Scalability and Performance Risks

### A. Parallel Job Launching on Workflow Executions
*   **Risk:** In `pos_dataflow_workflow.yaml`, the `launch_all_jobs` block fires parallel launch requests for every file in the manifest using:
    ```yaml
    concurrency_limit: '${chunk_size}'
    ```
*   **Detail:** If a manifest lists hundreds of raw POS files, the workflow will trigger multiple parallel Dataflow Flex Template container builds and job start requests.
*   **Impact:** This can quickly hit Google Cloud project quota limits for concurrent Flex Template launches, causing subsequent template launches to fail.
*   **Recommendation:** Limit the concurrency of parallel template starts by capping `chunk_size` (e.g., to 5 or 10 parallel jobs max) or update the Dataflow architecture to process folders dynamically instead of starting a separate Dataflow job for each file.

### B. Memory Safety in `lead_matching.py`
*   **Risk:** Despite implementing warehouse-based batching to split POS data into chunks, loading all active leads into memory (`leads = preprocess_leads(file_leads)`) remains a bottleneck as the historical lead database grows over time.
*   **Impact:** If the active lead database reaches millions of records, the pandas dataframe allocations on Cloud Run may exceed memory thresholds, leading to Out-Of-Memory (OOM) job crashes.
*   **Recommendation:** Transition the initial preprocess steps from Pandas dataframes to batch SQL queries directly in PostgreSQL, extracting only those leads that share warehouses present in the current POS manifest.

---

## 4. Operational Gaps

### A. Lack of Dead-Letter Queue Handling
*   **Gap:** While a Pub/Sub dead-letter topic is declared in the Terraform configuration (`gcs_pubsub_trigger`), there are no automated alerting monitors or retry subscriber queues bound to process failed messages in the Dead-Letter Queue (DLQ).
*   **Impact:** If a POS manifest fails parsing repeatedly, it will be quietly discarded into the DLQ, and operations teams will not have visibility unless they manually query the logs.
*   **Recommendation:** Provision a Cloud Monitoring alert specifically bound to DLQ publish metrics to alert on ingestion failures.
