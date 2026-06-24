# Lead Match Preflight

- Project: `ctoteam`
- Region: `us-central1`
- Cloud SQL instance: `lead-mgmt-db`
- Schema: `leadmgmt`
- Workflow: `lead_match_workflow`
- Cloud Run Jobs: `lead-match-lead-embeddings`, `lead-match-pos-embeddings`, `lead-match-fuzzy-match`, `lead-match-fuzzy-match`
- Warehouse: `947`
- Smoke tests: `false`
- Cloud SQL index maintenance: `false`
- READY URI: `gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/READY`
- Report URI: `gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/report.md`
- Result URI: `gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/result.json`
- Generated UTC: `2026-06-23T14:22:23Z`

## Enabled Services

Required services for this workflow:

- `aiplatform.googleapis.com`
- `artifactregistry.googleapis.com`
- `cloudbuild.googleapis.com`
- `run.googleapis.com`
- `secretmanager.googleapis.com`
- `sqladmin.googleapis.com`
- `storage.googleapis.com`
- `workflows.googleapis.com`

```text
aiplatform.googleapis.com
artifactregistry.googleapis.com
bigquerystorage.googleapis.com
cloudbuild.googleapis.com
run.googleapis.com
secretmanager.googleapis.com
sqladmin.googleapis.com
storage.googleapis.com
workflows.googleapis.com
```

## Cloud SQL
```text
NAME          DATABASE_VERSION  REGION       TIER               STATE
lead-mgmt-db  POSTGRES_15       us-central1  db-custom-4-15360  RUNNABLE
```

## Cloud Run Jobs
```text
NAME                        TYPE   STATUS
lead-match-ensure-indexes   Ready  True
lead-match-exact-match      Ready  True
lead-match-fuzzy-match      Ready  True
lead-match-lead-embeddings  Ready  True
lead-match-pos-embeddings   Ready  True
lead-match-report           Ready  True
lead-match-warehouse-smoke  Ready  True
```

## Workflow
```text
createTime: '2026-06-20T16:20:01.754377945Z'
name: projects/ctoteam/locations/us-central1/workflows/lead_match_workflow
revisionCreateTime: '2026-06-23T11:32:16.288933848Z'
revisionId: 000034-85a
serviceAccount: projects/ctoteam/serviceAccounts/1035117862188-compute@developer.gserviceaccount.com
sourceContents: |
  main:
    params: [args]
    steps:
      - init:
          assign:
            - project: ${default(map.get(args, "project"), sys.get_env("GOOGLE_CLOUD_PROJECT_ID"))}
            - region: ${default(map.get(args, "region"), "us-central1")}
            - warehouse: ${default(map.get(args, "warehouse"), "all")}
            - exactJob: ${default(map.get(args, "exactJob"), "lead-match-exact-match")}
            - leadJob: ${default(map.get(args, "leadJob"), "lead-match-lead-embeddings")}
            - posJob: ${default(map.get(args, "posJob"), "lead-match-pos-embeddings")}
            - matchJob: ${default(map.get(args, "matchJob"), "lead-match-fuzzy-match")}
            - indexJob: ${default(map.get(args, "indexJob"), "lead-match-ensure-indexes")}
            - matchRunId: ${default(map.get(args, "matchRunId"), "")}
            - dryRun: ${default(map.get(args, "dryRun"), "false")}
            - leadEmbeddingLimit: ${default(map.get(args, "leadEmbeddingLimit"), "")}
            - posEmbeddingLimit: ${default(map.get(args, "posEmbeddingLimit"), "")}
            - matchLeadLimit: ${default(map.get(args, "matchLeadLimit"), "")}
            - exactResult: null
            - leadResult: null
            - posResult: null

      - runExactMatch:
          call: runCloudRunJob
          args:
            project: ${project}
            region: ${region}
            jobName: ${exactJob}
            containerArgs:
              - exact-match
            envVars:
              - name: WAREHOUSE
                value: ${warehouse}
              - name: WAREHOUSE_SCOPE
                value: ${warehouse}
              - name: MATCH_RUN_ID
                value: ${matchRunId}
              - name: DRY_RUN
                value: ${dryRun}
              - name: MATCH_LEAD_LIMIT
                value: ${matchLeadLimit}
          result: exactResult

      - runLeadEmbeddings:
          call: runCloudRunJob
          args:
            project: ${project}
            region: ${region}
            jobName: ${leadJob}
            containerArgs: []
            envVars:
              - name: WAREHOUSE
                value: ${warehouse}
              - name: WAREHOUSE_SCOPE
                value: ${warehouse}
              - name: MATCH_RUN_ID
                value: ${matchRunId}
              - name: DRY_RUN
                value: ${dryRun}
              - name: LEAD_EMBEDDING_LIMIT
                value: ${leadEmbeddingLimit}
          result: leadResult

      - runPosEmbeddings:
          call: runCloudRunJob
          args:
            project: ${project}
            region: ${region}
            jobName: ${posJob}
            containerArgs: []
            envVars:
              - name: WAREHOUSE
                value: ${warehouse}
              - name: WAREHOUSE_SCOPE
                value: ${warehouse}
              - name: MATCH_RUN_ID
                value: ${matchRunId}
              - name: DRY_RUN
                value: ${dryRun}
              - name: POS_EMBEDDING_LIMIT
                value: ${posEmbeddingLimit}
          result: posResult

      - runEnsureIndexes:
          call: runCloudRunJob
          args:
            project: ${project}
            region: ${region}
            jobName: ${indexJob}
            containerArgs:
              - ensure-indexes
            envVars:
              - name: WAREHOUSE
                value: ${warehouse}
              - name: WAREHOUSE_SCOPE
                value: ${warehouse}
              - name: DRY_RUN
                value: ${dryRun}
          result: indexResult

      - runFuzzyMatch:
          call: runCloudRunJob
          args:
            project: ${project}
            region: ${region}
            jobName: ${matchJob}
            containerArgs: []
            envVars:
              - name: WAREHOUSE
                value: ${warehouse}
              - name: WAREHOUSE_SCOPE
                value: ${warehouse}
              - name: MATCH_RUN_ID
                value: ${matchRunId}
              - name: DRY_RUN
                value: ${dryRun}
              - name: MATCH_LEAD_LIMIT
                value: ${matchLeadLimit}
          result: matchResult

      - done:
          return:
            status: "success"
            warehouse: ${warehouse}
            matchRunId: ${matchRunId}
            dryRun: ${dryRun}
            leadEmbeddingLimit: ${leadEmbeddingLimit}
            posEmbeddingLimit: ${posEmbeddingLimit}
            matchLeadLimit: ${matchLeadLimit}
            exact: ${exactResult}
            lead: ${leadResult}
            pos: ${posResult}
            indexes: ${indexResult}
            match: ${matchResult}

  runCloudRunJob:
    params: [project, region, jobName, envVars, containerArgs]
    steps:
      - init:
          assign:
            - startedAt: ${sys.now()}
      - logStart:
          call: sys.log
          args:
            severity: INFO
            text: ${"Starting Cloud Run Job " + jobName}
      - runJob:
          try:
            call: http.post
            args:
              url: ${"https://run.googleapis.com/v2/projects/" + project + "/locations/" + region + "/jobs/" + jobName + ":run"}
              auth:
                type: OAuth2
              body:
                overrides:
                  containerOverrides:
                    - args: ${containerArgs}
                      env: ${envVars}
            result: jobOperation
          except:
            as: e
            steps:
              - failRunJobStart:
                  raise: '${"Failed to start Cloud Run Job " + jobName + ": " + json.encode_to_string(e)}'
      - extractExecutionName:
          assign:
            - jobOperationBody: ${jobOperation.body}
            - jobOperationMetadata: ${map.get(jobOperationBody, "metadata")}
            - jobExecutionName: ${map.get(jobOperationMetadata, "name")}
      - validateExecutionName:
          switch:
            - condition: ${jobExecutionName == null}
              next: failMissingExecutionName
          next: waitJob
      - failMissingExecutionName:
          raise: '${"Failed to find Cloud Run Job execution name for " + jobName + ": operation=" + json.encode_to_string(jobOperationBody)}'
      - waitJob:
          call: waitForRunJobExecution
          args:
            executionName: ${jobExecutionName}
          result: jobResult
      - finish:
          assign:
            - completedAt: ${sys.now()}
      - logComplete:
          call: sys.log
          args:
            severity: INFO
            text: ${"Completed Cloud Run Job " + jobName + " execution " + jobExecutionName}
      - returnResult:
          return:
            job: ${jobName}
            executionName: ${jobExecutionName}
            startedAt: ${startedAt}
            completedAt: ${completedAt}
            durationSeconds: ${completedAt - startedAt}
            taskCount: ${default(map.get(jobResult, "taskCount"), 1)}
            succeededCount: ${default(map.get(jobResult, "succeededCount"), 0)}
            failedCount: ${default(map.get(jobResult, "failedCount"), 0)}
            cancelledCount: ${default(map.get(jobResult, "cancelledCount"), 0)}

  waitForRunJobExecution:
    params: [executionName]
    steps:
      - initWait:
          assign:
            - pollStartedAt: ${sys.now()}
            - maxWaitSeconds: 3600
      - poll:
          call: googleapis.run.v2.projects.locations.jobs.executions.get
          args:
            name: ${executionName}
          result: execution
      - checkTimeout:
          switch:
            - condition: ${sys.now() - pollStartedAt > maxWaitSeconds}
              next: timeout
      - extractCounts:
          assign:
            - executionStatus: ${map.get(execution, "status")}
            - executionSpec: ${map.get(execution, "spec")}
            - taskCount: ${int(default(map.get(execution, "taskCount"), 1))}
            - succeededCount: ${int(default(map.get(execution, "succeededCount"), 0))}
            - failedCount: ${int(default(map.get(execution, "failedCount"), 0))}
            - cancelledCount: ${int(default(map.get(execution, "cancelledCount"), 0))}
      - extractNestedSpec:
          switch:
            - condition: ${executionSpec != null}
              steps:
                - applyNestedSpec:
                    assign:
                      - taskCount: ${int(default(map.get(executionSpec, "taskCount"), taskCount))}
      - extractNestedStatus:
          switch:
            - condition: ${executionStatus != null}
              steps:
                - applyNestedStatus:
                    assign:
                      - succeededCount: ${int(default(map.get(executionStatus, "succeededCount"), succeededCount))}
                      - failedCount: ${int(default(map.get(executionStatus, "failedCount"), failedCount))}
                      - cancelledCount: ${int(default(map.get(executionStatus, "cancelledCount"), cancelledCount))}
      - checkCompletion:
          switch:
            - condition: ${succeededCount >= taskCount}
              next: returnSuccess
            - condition: ${failedCount > 0}
              next: fail
            - condition: ${cancelledCount > 0}
              next: fail
      - sleep:
          call: sys.sleep
          args:
            seconds: 15
          next: poll
      - returnSuccess:
          return:
            executionName: ${executionName}
            execution: ${execution}
            taskCount: ${taskCount}
            succeededCount: ${succeededCount}
            failedCount: ${failedCount}
            cancelledCount: ${cancelledCount}
      - timeout:
          raise: '${"Timed out waiting for Cloud Run Job execution after " + json.encode_to_string(maxWaitSeconds) + " seconds: " + executionName + " last_execution=" + json.encode_to_string(execution)}'
      - fail:
          raise: '${"Cloud Run Job execution failed: " + executionName + " execution=" + json.encode_to_string(execution)}'
state: ACTIVE
updateTime: '2026-06-23T11:32:16.485237181Z'
```

## GCS
```text
        60  2026-06-23T14:22:27Z  gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/.keep
       419  2026-06-23T11:46:37Z  gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/report.md
       644  2026-06-23T11:46:38Z  gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/result.json
TOTAL: 3 objects, 1123 bytes (1.1 KiB)
```

## Manual Cloud SQL Readiness SQL
```sql
SELECT count(*) FROM leadmgmt.lead WHERE 1=1 AND warehouse_number IN (947);
SELECT count(*) FROM leadmgmt.transaction WHERE 1=1 AND warehouse_number IN (947);
SELECT count(*) FROM leadmgmt.leads_embeddings WHERE 1=1 AND warehouse_number IN (947);
SELECT count(*) FROM leadmgmt.pos_embeddings WHERE 1=1 AND warehouse_number IN (947);
SELECT count(*) FROM leadmgmt.match_decision_detail;
```
## Readiness Contract

- Required checks status: `PASS`
- Cloud SQL index status: `SKIPPED`
- Overall status: `PASS`
- Ready: `true`
- Result URI: `gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/result.json`
- READY URI: `gs://lead-match-ctoteam/preflight/lead_match/ctoteam/947/READY`

