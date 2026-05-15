export type CaeMode = "cad_only" | "cae_setup" | "cae_result" | "cae_validation";

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
        max_displacement: number | null;
        max_von_mises_stress: number | null;
        minimum_safety_factor: number | null;
      };
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

export type ChatStep = {
  tool: string;
  description: string;
  status: string;
  inputs?: Record<string, unknown>;
  output?: Record<string, unknown> | null;
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
