export type GcpExecution = {
  name: string;
  executionId?: string;
  state: string;
  startTime: string;
  endTime: string;
  duration: string;
  elapsedSeconds?: number | null;
  elapsedHuman?: string;
  warehouse: string;
  matchRunId: string;
  reportBucket?: string;
  reportPrefix?: string;
  reportUriPrefix?: string;
  reportUris?: {
    summary?: string;
    matches?: string;
    report?: string;
  };
  currentStep?: string;
  currentRoutine?: string;
  currentSteps: Array<{ routine?: string; step?: string }>;
  error?: unknown;
};

export type GhRun = {
  databaseId: number;
  url?: string;
  status: string;
  conclusion: string | null;
  startedAt: string;
  updatedAt?: string;
  displayTitle: string;
  warehouse?: string;
  jobStatus?: string;
  jobName?: string;
  jobConclusion?: string;
  currentStep?: string;
  currentStepNumber?: number | null;
  completedStepCount?: number;
  totalStepCount?: number;
  failedStep?: string;
  lastCompletedStep?: string;
};

export type WarehouseStats = {
  has_reports?: boolean;
  summary_missing?: boolean;
  lead_count?: number;
  lead_embedding_count?: number;
  lead_embedding_pct?: number;
  pos_count?: number;
  pos_embedding_count?: number;
  pos_embedding_pct?: number;
  match_count?: number;
  primary_transactions?: number;
  embedding_model?: string;
  generated_at?: string;
  match_types?: Record<string, number>;
  lifecycle_states?: Record<string, number>;
  latest_run_id?: string | null;
};

export type CloudRunJobExecution = {
  name: string;
  job: string;
  state: string;
  createTime?: string;
  startTime?: string;
  completionTime?: string;
  succeededCount?: number;
  failedCount?: number;
  runningCount?: number;
  taskCount?: number | string;
  parallelism?: number | string;
  conditionType?: string;
  conditionReason?: string;
  conditionMessage?: string;
};

export type Snapshot = {
  generated_at: string;
  project: string;
  region: string;
  workflow_name?: string;
  lookback_hours: number;
  warehouse_filter: string;
  status_summary?: {
    active_gcp_workflow_count?: number;
    running_cloud_run_job_count?: number;
    failed_cloud_run_job_count?: number;
    github_in_progress_count?: number;
    github_failed_recent_count?: number;
  };
  active_runs: GcpExecution[];
  workflow_executions: GcpExecution[];
  github_actions_runs: GhRun[];
  cloud_run_jobs?: Record<string, CloudRunJobExecution[]>;
  latest_cloud_run_jobs?: Record<string, CloudRunJobExecution | null>;
  warehouse_stats: {
    available: boolean;
    warehouses: Record<string, WarehouseStats>;
  };
  warehouse_run_status: Record<
    string,
    {
      latest_state: string;
      latest_start: string;
      latest_end?: string;
      latest_match_run_id?: string;
      current_step: string;
      current_routine?: string;
      elapsed_human?: string;
      report_uri_prefix?: string;
    }
  >;
};
