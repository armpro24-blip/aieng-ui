import type { AgentPlan, AgentRunResponse, ArtifactDiffResponse, ArtifactResponse, BenchmarkRun, BenchmarkScenario, CaeArtifactDetection, CaePreprocessingSummary, CaeSimulationRunSummary, CapabilityDescriptor, CapabilityPreview, ChatResponse, LLMConfig, ProjectRecord, ProjectSummary, RuntimeConfig, RuntimeConfigSnapshot, RuntimeEvent, RuntimeRun, RuntimeRunSummary, RuntimeToolInfo, SolverFieldDescriptor, WorkflowDefinition, WorkflowStep } from "./types";

const API = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: {
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = {
  base: API,
  runtime: () => request<RuntimeConfigSnapshot>("/api/runtime"),
  getRuntimeConfig: () => request<RuntimeConfigSnapshot>("/api/runtime-config"),
  updateRuntimeConfig: (payload: RuntimeConfig) =>
    request<RuntimeConfigSnapshot>("/api/runtime-config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  testRuntimeConfig: (payload: RuntimeConfig) =>
    request<RuntimeConfigSnapshot>("/api/runtime-config/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  testLlmProvider: (config: LLMConfig, verifyConnection: boolean) =>
    request<{
      config_ready: boolean;
      connection_verified: boolean;
      provider: string;
      model: string;
      base_url?: string | null;
      api_key_present: boolean;
      error_message: string | null;
    }>("/api/llm/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_config: config, verify_connection: verifyConnection }),
    }),
  listCapabilities: () => request<CapabilityDescriptor[]>("/api/capabilities"),
  previewCapability: (operationName: string, inputs: Record<string, unknown> = {}, approved = false) =>
    request<CapabilityPreview>("/api/capabilities/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation_name: operationName, inputs, approved }),
    }),
  listWorkflows: () => request<WorkflowDefinition[]>("/api/runtime/workflows"),
  planAgent: (payload: {
    message: string;
    project_id?: string | null;
    llm_config?: LLMConfig;
    patch_json?: Record<string, unknown> | null;
    dry_run?: boolean;
  }) =>
    request<AgentPlan>("/api/agent/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  runAgent: (payload: {
    message?: string;
    project_id?: string | null;
    llm_config?: LLMConfig;
    patch_json?: Record<string, unknown> | null;
    dry_run?: boolean;
    plan?: AgentPlan;
  }) =>
    request<AgentRunResponse>("/api/agent/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  listBenchmarkScenarios: () => request<BenchmarkScenario[]>("/api/benchmarks/scenarios"),
  startBenchmarkRun: (payload: {
    scenario_id: string;
    condition?: string;
    dry_run?: boolean;
    llm_config: LLMConfig;
  }) =>
    request<BenchmarkRun>("/api/benchmarks/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  getBenchmarkRun: (runId: string) => request<BenchmarkRun>(`/api/benchmarks/runs/${runId}`),
  listProjects: () => request<ProjectRecord[]>("/api/projects"),
  createProject: (name: string) =>
    request<ProjectRecord>("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  createSampleProject: () => request<ProjectRecord>("/api/projects/sample", { method: "POST" }),
  getProject: (projectId: string) => request<ProjectSummary>(`/api/projects/${projectId}`),
  uploadFile: async (projectId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<ProjectRecord>(`/api/projects/${projectId}/upload`, {
      method: "POST",
      body: form,
    });
  },
  importAieng: (projectId: string) => request(`/api/projects/${projectId}/import-aieng`, { method: "POST" }),
  validate: (projectId: string) => request(`/api/projects/${projectId}/validate`, { method: "POST" }),
  convert: (projectId: string) => request(`/api/projects/${projectId}/convert`, { method: "POST" }),
  chat: (projectId: string, message: string, execute: boolean) =>
    request<ChatResponse>(`/api/projects/${projectId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, execute }),
    }),
  getFieldDescriptor: (projectId: string, fieldName: string) =>
    request<SolverFieldDescriptor>(`/api/projects/${projectId}/fields/${fieldName}`),
  listRuns: () => request<RuntimeRunSummary[]>("/api/runtime/runs"),
  startRun: (message: string, projectId?: string | null, toolInput?: Record<string, unknown> | null, extras?: { workflow_id?: string; steps?: WorkflowStep[]; llm_config?: LLMConfig }) =>
    request<RuntimeRun>("/api/runtime/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        project_id: projectId ?? null,
        ...(toolInput ? { tool_input: toolInput } : {}),
        ...(extras?.workflow_id ? { workflow_id: extras.workflow_id } : {}),
        ...(extras?.steps ? { steps: extras.steps } : {}),
        ...(extras?.llm_config ? { llm_config: extras.llm_config } : {}),
      }),
    }),
  getRun: (runId: string) => request<RuntimeRun>(`/api/runtime/runs/${runId}`),
  getRunEvents: (runId: string) => request<RuntimeEvent[]>(`/api/runtime/runs/${runId}/events`),
  approveRun: (runId: string) =>
    request<RuntimeRun>(`/api/runtime/runs/${runId}/approve`, { method: "POST" }),
  rejectRun: (runId: string) =>
    request<RuntimeRun>(`/api/runtime/runs/${runId}/reject`, { method: "POST" }),
  listTools: () => request<RuntimeToolInfo[]>("/api/runtime/tools"),
  getCaeArtifacts: (projectId: string) =>
    request<CaeArtifactDetection>(`/api/projects/${projectId}/cae-artifacts`),
  getCaePreprocessingSummary: (projectId: string) =>
    request<CaePreprocessingSummary>(`/api/projects/${projectId}/cae-preprocessing-summary`),
  getCaeSimulationRunSummary: (projectId: string) =>
    request<CaeSimulationRunSummary>(`/api/projects/${projectId}/cae-simulation-run-summary`),
  getProjectArtifact: (projectId: string, path: string) =>
    request<ArtifactResponse>(`/api/projects/${projectId}/artifact?path=${encodeURIComponent(path)}`),
  diffArtifactJson: (projectId: string, before: unknown, after: unknown) =>
    request<ArtifactDiffResponse>(`/api/projects/${projectId}/artifact/diff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ before, after }),
    }),
};
