export type DesignTargetComparisonStatus = "pass" | "fail" | "unknown" | "not_evaluated";

export type DesignTargetComparisonItem = {
  target_id: string;
  target_type?: string;
  expected?: unknown;
  actual?: unknown;
  comparator?: string;
  status: DesignTargetComparisonStatus;
  evidence_refs?: string[];
  source_artifacts?: string[];
  notes?: string;
};

export type DesignTargetComparisons = {
  present?: boolean;
  target_set_id?: string;
  evaluated_at?: string;
  summary?: {
    total?: number;
    pass?: number;
    fail?: number;
    unknown?: number;
    not_evaluated?: number;
  };
  items?: DesignTargetComparisonItem[];
};

export type CaeMode = "cad_only" | "cae_setup" | "cae_result" | "cae_validation";

export type CaePreprocessingSummary = {
  schema_version: string;
  summary_type: string;
  status: {
    has_cae_setup: boolean;
    has_materials: boolean;
    has_loads: boolean;
    has_boundary_conditions: boolean;
    has_constraints: boolean;
    has_mesh: boolean;
    has_load_cases: boolean;
    has_solver_settings: boolean;
    has_cae_mapping: boolean;
    ready_for_solver: boolean;
    missing_items: string[];
    warnings: string[];
  };
  llm_summary: {
    one_line: string;
    key_findings: string[];
    risks: string[];
    recommended_next_actions: string[];
    limitations: string[];
  };
};

export type CaeSimulationRunSummary = {
  schema_version: string;
  summary_type: string;
  status: {
    has_simulation_runs: boolean;
    run_count: number;
    latest_run_id: string | null;
    has_completed_run: boolean;
    has_converged_run: boolean;
    has_failed_run: boolean;
    warnings: string[];
  };
  runs: Array<{
    run_id: string;
    solver: string;
    software: string;
    analysis_type: string;
    state: string;
    solved: boolean | null;
    converged: boolean | null;
    warnings: string[];
    errors: string[];
    log_file: string | null;
  }>;
  llm_summary: {
    one_line: string;
    key_findings: string[];
    risks: string[];
    recommended_next_actions: string[];
    limitations: string[];
  };
};

export type CaeArtifactDetection = {
  mode: CaeMode;
  artifacts: Record<string, boolean>;
  has_cae_setup: boolean;
  has_mesh: boolean;
  has_solver_settings: boolean;
  has_results: boolean;
  has_fields: boolean;
  has_validation: boolean;
  detected_count: number;
  total_count: number;
};

export type ProjectRecord = {
  id: string;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  source_step?: string | null;
  aieng_file?: string | null;
  web_asset?: string | null;
  web_asset_format?: string | null;
  last_error?: string | null;
};

export type RuntimeConfig = {
  provider: string;
  aieng_root: string;
  freecad_mcp_root: string;
  freecad_home: string;
  topology_backend: "auto" | "mock" | "occ" | string;
};

export type RuntimeProbe = {
  provider: string;
  topology_backend_requested: string;
  topology_backend_resolved: string;
  aieng_root: string;
  aieng_src_exists: boolean;
  freecad_mcp_root: string;
  freecad_mcp_src_exists: boolean;
  freecad_home: string;
  freecad_cmd: string;
  freecad_python: string;
  freecad_cmd_exists: boolean;
  freecad_python_exists: boolean;
  ready: boolean;
  issues: string[];
  bridge?: Record<string, unknown>;
  bridge_error?: string;
  whitelisted_tools?: string[];
};

export type RuntimeConfigSnapshot = {
  config: RuntimeConfig;
  defaults: RuntimeConfig;
  probe: RuntimeProbe;
  config_path: string;
  persisted_exists: boolean;
};

export type ProjectSummary = {
  project: ProjectRecord;
  files?: Record<string, unknown>;
  members: string[];
  manifest?: Record<string, unknown> | null;
  feature_graph?: Record<string, unknown> | null;
  topology?: Record<string, unknown> | null;
  constraints?: Record<string, unknown> | null;
  validation?: Record<string, unknown> | null;
  viewer?: Record<string, unknown> | null;
  viewer_url?: string | null;
  ai_summary?: string | null;
  derived?: Record<string, unknown>;
  summary_error?: string | null;
  summary_mode?: string | null;
  cae?: {
    present: boolean;
    constraints_count: number;
    constraint_types: Record<string, number>;
    materials_count: number;
    boundary_conditions_count: number;
    loads_count: number;
    evidence_count: number;
    result_evidence_count: number;
    results_available: boolean;
    available_fields: string[];
    simulation_targets: Array<Record<string, unknown>>;
    protected_regions: Array<Record<string, unknown>>;
    materials: Array<Record<string, unknown>>;
    boundary_conditions: Array<Record<string, unknown>>;
    loads: Array<Record<string, unknown>>;
    evidence: Array<Record<string, unknown>>;
    mapping?: Record<string, unknown> | null;
    solver_status?: Record<string, unknown>;
    solver_fields?: Array<{
      field_name: string;
      descriptor_url: string;
      min_value: number;
      max_value: number;
      unit?: string | null;
      format: string;
      available: boolean;
    }> | null;
    artifact_detection?: CaeArtifactDetection | null;
    preprocessing_summary?: {
      schema_version: string;
      summary_type: string;
      status: {
        has_cae_setup: boolean;
        has_materials: boolean;
        has_loads: boolean;
        has_boundary_conditions: boolean;
        has_constraints: boolean;
        has_mesh: boolean;
        has_load_cases: boolean;
        has_solver_settings: boolean;
        has_cae_mapping: boolean;
        ready_for_solver: boolean;
        missing_items: string[];
        warnings: string[];
      };
      llm_summary: {
        one_line: string;
        key_findings: string[];
        risks: string[];
        recommended_next_actions: string[];
        limitations: string[];
      };
    } | null;
    simulation_run_summary?: {
      schema_version: string;
      summary_type: string;
      status: {
        has_simulation_runs: boolean;
        run_count: number;
        latest_run_id: string | null;
        has_completed_run: boolean;
        has_converged_run: boolean;
        has_failed_run: boolean;
        warnings: string[];
      };
      runs: Array<{
        run_id: string;
        solver: string;
        software: string;
        analysis_type: string;
        state: string;
        solved: boolean | null;
        converged: boolean | null;
        warnings: string[];
        errors: string[];
        log_file: string | null;
      }>;
      llm_summary: {
        one_line: string;
        key_findings: string[];
        risks: string[];
        recommended_next_actions: string[];
        limitations: string[];
      };
    } | null;
    result_summary?: {
      schema_version: string;
      summary_type: string;
      source: {
        package_path: string;
        solver: string;
        software: string | null;
        source_files: string[];
      };
      status: {
        mode: CaeMode;
        has_cae_setup: boolean;
        has_mesh: boolean;
        has_results: boolean;
        has_fields: boolean;
        has_validation: boolean;
        warnings: string[];
      };
      artifacts: {
        mesh_files: string[];
        field_files: string[];
        result_summary_files: string[];
        evidence_files: string[];
        validation_files: string[];
        setup_files: string[];
      };
      solver_settings: {
        solver_type?: string | null;
        analysis_type?: string | null;
        parameters?: Record<string, unknown>;
      } | null;
      load_cases: Array<{
        id: string;
        name: string;
        type: string;
        magnitude?: number | null;
        unit?: string | null;
        description?: string | null;
        source_file: string;
      }>;
      field_metadata: {
        fields: Array<Record<string, unknown>>;
        format?: string | null;
        count: number;
      } | null;
      computed_values: {
        extrema_computed: boolean;
        source?: string | null;
        computed_by?: string | null;
        max_displacement: {
          value: number;
          unit: string | null;
          field?: string | null;
          location?: Record<string, unknown> | null;
        } | null;
        max_von_mises_stress: {
          value: number;
          unit: string | null;
          field?: string | null;
          location?: Record<string, unknown> | null;
        } | null;
        minimum_safety_factor: {
          value: number;
          unit: string | null;
          basis?: string | null;
          location?: Record<string, unknown> | null;
        } | null;
        by_load_case?: Array<{
          id: string;
          metrics: Record<string, unknown>;
        }> | null;
      };
      design_target_comparisons?: DesignTargetComparisons | null;
      llm_summary: {
        one_line: string;
        key_findings: string[];
        risks: string[];
        recommended_next_actions: string[];
        limitations: string[];
      };
    } | null;
  } | null;
  integration?: RuntimeConfigSnapshot | Record<string, unknown>;
};

export type SolverFieldDescriptor = {
  field_name: string;
  project_id: string;
  format: "vertex_synthetic" | "vertex_json" | string;
  basis?: string | null;
  min_value: number;
  max_value: number;
  unit?: string | null;
  colormap?: string | null;
  source?: string | null;
  values?: number[] | null;
  node_coords?: [number, number, number][] | null;
  warnings?: string[] | null;
  bbox_status?: "aligned" | "suspicious" | null;
};

export type RuntimeEventType =
  | "run_started"
  | "plan_created"
  | "tool_started"
  | "tool_succeeded"
  | "tool_failed"
  | "approval_required"
  | "approval_granted"
  | "approval_rejected"
  | "run_completed"
  | "run_failed"
  | "run_rejected"
  | "run_cancelled";

export type RuntimeEvent = {
  id: string;
  run_id: string;
  type: RuntimeEventType;
  timestamp: string;
  payload?: unknown;
};

export type RuntimeToolCall = {
  id: string;
  name: string;
  input: unknown;
  requires_approval: boolean;
};

export type RuntimeToolError = {
  code: string;
  message: string;
  tool_name?: string | null;
  details?: Record<string, unknown> | null;
};

export type RuntimeToolResult = {
  id: string;
  status: "success" | "error" | "needs_approval" | "rejected";
  output?: unknown;
  error?: string | null;
  artifacts?: unknown[];
};

export type RuntimeRun = {
  run_id: string;
  message: string;
  created_at: string;
  status: "pending" | "running" | "completed" | "failed" | "awaiting_approval" | "rejected" | "cancelled";
  plan: Array<{ name: string; description: string; input: Record<string, unknown> }>;
  events: RuntimeEvent[];
  tool_calls: RuntimeToolCall[];
  tool_results: RuntimeToolResult[];
  tool_errors: RuntimeToolError[];
  errors: string[];
  project_id?: string | null;
  package_path?: string | null;
  summary: string;
  pending_step_index?: number | null;
};

export type RuntimeRunSummary = {
  run_id: string;
  created_at: string;
  status: RuntimeRun["status"];
  message: string;
  project_id?: string | null;
  event_count: number;
  last_event_type?: string | null;
  error_summary?: string | null;
};

export type RuntimeToolInfo = {
  name: string;
  requires_approval: boolean;
  description: string;
};

export type CapabilityDescriptor = {
  name: string;
  source: string;
  category: string;
  purpose: string;
  required_inputs: string[];
  optional_inputs: string[];
  mutates_cad: boolean;
  mutates_package: boolean;
  may_update_claim_map: boolean;
  runtime_requirements: string[];
  dry_run_support: string;
  side_effects: string[];
  claim_policy: Record<string, unknown>;
  available: boolean;
  unavailable_reason?: string | null;
};

export type CapabilityPreview = {
  status: string;
  operation_name: string;
  capability?: CapabilityDescriptor;
  approval_required?: boolean;
  blocked?: boolean;
  preview?: {
    operation_name: string;
    would_write_artifacts: string[];
    would_update_evidence: boolean;
    would_update_traces: boolean;
    would_touch_claims: boolean;
    guard_checks_required: string[];
    unavailable_runtime_blocks: string[];
    expected_duration_estimate: string;
    warnings: string[];
  } | null;
  errors?: string[];
};

export type WorkflowStep = {
  id: string;
  kind: "tool" | "mcp_tool" | "llm" | "approval" | "benchmark" | "artifact" | string;
  tool_name?: string;
  description?: string;
  input?: Record<string, unknown>;
  status: string;
  preview?: Record<string, unknown> | null;
  approval_required?: boolean;
  artifacts?: unknown[];
  errors?: string[];
};

export type WorkflowDefinition = {
  id: string;
  title: string;
  description: string;
  required_context: string[];
  steps: WorkflowStep[];
};

export type LLMConfig = {
  provider: string;
  model: string;
  base_url?: string | null;
  api_key_env?: string | null;
  temperature: number;
  top_p: number;
  max_output_tokens: number;
  input_price_per_million_tokens?: number | null;
  output_price_per_million_tokens?: number | null;
};

export type BenchmarkScenario = {
  id: string;
  name: string;
  path: string;
  question_file: string;
  condition_a_path: string;
  condition_b_index: string;
  condition_b_source: string;
  has_condition_b_package: boolean;
  has_condition_b_contents: boolean;
  rubric_file: string;
  schema_file: string;
};

export type BenchmarkRun = {
  run_id: string;
  status: string;
  scenario_id: string;
  dry_run: boolean;
  created_at: string;
  result: Record<string, unknown>;
  result_path?: string | null;
  events: Array<{ id: string; type: string; timestamp: string; payload?: unknown }>;
  warnings: string[];
  errors?: string[];
};

export type AgentPlan = {
  reply: string;
  mode: "llm" | "heuristic" | string;
  message: string;
  project_id?: string | null;
  steps: WorkflowStep[];
  requires_approval: boolean;
  preview: {
    step_count: number;
    tools: string[];
    would_execute: string[];
    approval_gated: string[];
    side_effects: string[];
    warnings: string[];
  };
  warnings: string[];
  errors: string[];
  llm_raw?: string | null;
  llm_config?: Record<string, unknown>;
};

export type AgentRunResponse = {
  agent: AgentPlan;
  run: RuntimeRun;
};

export type ChatConnection = {
  id: "llm-api" | "local-runtime" | "mcp-bridge" | "freecad-desktop" | string;
  label: string;
  transport: string;
  status: "ready" | "configurable" | "degraded" | "blocked" | string;
  detail: string;
  requires_project: boolean;
  supports_llm: boolean;
  supports_execution: boolean;
  approval_gated: boolean;
  tool_count: number;
  registry_count?: number;
};

export type ChatStep = {
  tool: string;
  description: string;
  status: string;
  inputs?: Record<string, unknown>;
  output?: Record<string, unknown> | null;
};

export type ArtifactResponse = {
  path: string;
  exists: boolean;
  media_type: string;
  size_bytes?: number | null;
  parsed_json?: unknown | null;
  text?: string | null;
  warnings: string[];
};

export type ArtifactDiffResponse = {
  changed_paths: string[];
  added_paths: string[];
  removed_paths: string[];
};

export type ArtifactDiff = {
  path: string;
  operation: string;
  json_pointer: string;
  before: unknown;
  after: unknown;
  changed_paths: string[];
  added_paths: string[];
  removed_paths: string[];
};

export type ChatResponse = {
  reply: string;
  plan: ChatStep[];
  executed: boolean;
  audit_id: string;
  audit_log_url?: string | null;
  errors?: string[];
  intent?: Record<string, unknown>;
  patch_json?: Record<string, unknown> | null;
};
