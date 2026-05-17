import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import { api } from "./api";
import type { AgentPlan, ArtifactDiff, ArtifactResponse, BenchmarkRun, BenchmarkScenario, CapabilityDescriptor, CapabilityPreview, ChatResponse, LLMConfig, ProjectRecord, ProjectSummary, RuntimeConfig, RuntimeConfigSnapshot, RuntimeRun, SolverFieldDescriptor, WorkflowDefinition } from "./types";

// Status labels for runtime runs
function runtimeStatusLabel(status: RuntimeRun["status"]): string {
  if (status === "completed") return "已完成";
  if (status === "awaiting_approval") return "等待审批";
  if (status === "failed") return "执行失败";
  if (status === "rejected") return "已拒绝";
  if (status === "cancelled") return "已取消";
  return status;
}

type StageState = "idle" | "active" | "done" | "error";

type StageItem = {
  key: string;
  label: string;
  detail: string;
  state: StageState;
};

type Notice = {
  tone: "success" | "error" | "info";
  title: string;
  detail: string;
};

type ChatHistoryItem = {
  id: string;
  role: "user" | "assistant";
  body: string;
  createdAt: string;
  mode?: "plan" | "execute" | "runtime";
  plan?: ChatResponse["plan"];
  errors?: string[];
  auditLogUrl?: string | null;
  artifactPaths?: string[];
  artifactDiffs?: ArtifactDiff[];
};

type ViewerLoadState = "idle" | "loading" | "ready" | "error";
type ControlPaneMode = "project" | "agent" | "cae" | "chat";

const BASE_STAGES: StageItem[] = [
  { key: "upload", label: "上传 STEP", detail: "把用户选择的 STEP 文件放入项目", state: "idle" },
  { key: "import", label: "导入 aieng", detail: "生成 .aieng 包并自动补全 topology、AAG、feature 和摘要", state: "idle" },
  { key: "preview", label: "生成预览", detail: "调用 FreeCADCmd 预览链并优先产出 GLB", state: "idle" },
  { key: "semantic", label: "刷新语义信息", detail: "同步 manifest、topology、validation 和摘要", state: "idle" },
];

const CAD_PROVIDER_OPTIONS = [{ value: "freecad", label: "FreeCAD" }] as const;
const LLM_CONFIG_STORAGE_KEY = "aieng-ui.llm-config";
const LLM_PROVIDER_SUGGESTIONS = ["openai-compatible", "anthropic", "openai", "azure-openai"] as const;
const CHAT_SUGGESTIONS = [
  "总结当前模型的语义状态和主要风险",
  "检查当前包是否已经具备执行 patch 的前提",
  "给出减重但不破坏受保护区域的安全步骤",
] as const;

const DEFAULT_LLM_CONFIG: LLMConfig = {
  provider: "openai-compatible",
  model: "configured-model",
  base_url: "",
  api_key_env: "OPENAI_API_KEY",
  temperature: 0,
  top_p: 1,
  max_output_tokens: 8192,
  input_price_per_million_tokens: null,
  output_price_per_million_tokens: null,
};

const CONTROL_PANE_MODES: Array<{ id: ControlPaneMode; label: string; detail: string }> = [
  { id: "chat", label: "LLM 主流程", detail: "Provider 配置与 Agent 编排" },
  { id: "project", label: "项目", detail: "导入与语义摘要" },
  { id: "agent", label: "Agent", detail: "能力与工作流" },
  { id: "cae", label: "CAE", detail: "证据与结果" },
];

function jsonBlock(value: unknown) {
  return JSON.stringify(value ?? null, null, 2);
}

function formatTime(value?: string | null) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function getDerivedNumber(summary: ProjectSummary | null, group: string, key: string) {
  const derived = ((summary as any)?.derived ?? {}) as Record<string, Record<string, unknown>>;
  const value = derived[group]?.[key];
  return typeof value === "number" ? value : 0;
}

function getManifestString(summary: ProjectSummary | null, key: string) {
  const manifest = (summary?.manifest ?? null) as Record<string, unknown> | null;
  const value = manifest?.[key];
  return value == null ? "-" : String(value);
}

function getProviderLabel(provider?: string | null) {
  if (provider === "freecad") return "FreeCAD";
  return provider ?? "-";
}

function getLlmProviderLabel(provider?: string | null) {
  if (provider === "openai-compatible") return "OpenAI-compatible";
  if (provider === "azure-openai") return "Azure OpenAI";
  if (provider === "openai") return "OpenAI";
  if (provider === "anthropic") return "Anthropic";
  return provider ?? "-";
}

function normalizeLlmConfig(raw: unknown): LLMConfig {
  const base = { ...DEFAULT_LLM_CONFIG };
  if (!raw || typeof raw !== "object") return base;
  const data = raw as Record<string, unknown>;
  return {
    provider: typeof data.provider === "string" && data.provider.trim() ? data.provider : base.provider,
    model: typeof data.model === "string" && data.model.trim() ? data.model : base.model,
    base_url: typeof data.base_url === "string" ? data.base_url : base.base_url,
    api_key_env: typeof data.api_key_env === "string" ? data.api_key_env : base.api_key_env,
    temperature: typeof data.temperature === "number" && Number.isFinite(data.temperature) ? data.temperature : base.temperature,
    top_p: typeof data.top_p === "number" && Number.isFinite(data.top_p) ? data.top_p : base.top_p,
    max_output_tokens:
      typeof data.max_output_tokens === "number" && Number.isFinite(data.max_output_tokens)
        ? data.max_output_tokens
        : base.max_output_tokens,
    input_price_per_million_tokens:
      typeof data.input_price_per_million_tokens === "number" && Number.isFinite(data.input_price_per_million_tokens)
        ? data.input_price_per_million_tokens
        : base.input_price_per_million_tokens,
    output_price_per_million_tokens:
      typeof data.output_price_per_million_tokens === "number" && Number.isFinite(data.output_price_per_million_tokens)
        ? data.output_price_per_million_tokens
        : base.output_price_per_million_tokens,
  };
}

function isLlmConfigReady(config: LLMConfig) {
  return Boolean(config.provider.trim() && config.model.trim() && (config.api_key_env?.trim() || config.base_url?.trim()));
}

function getRuntimeDetail(snapshot: RuntimeConfigSnapshot | null) {
  if (!snapshot) return "正在读取 CAD 运行时配置";
  if (snapshot.probe.ready) {
    return `${getProviderLabel(snapshot.config.provider)} / topology=${snapshot.probe.topology_backend_resolved}`;
  }
  return snapshot.probe.issues.join("；") || snapshot.probe.bridge_error || "运行时检测未通过";
}

function createChatId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function fieldLabel(field: string) {
  if (field === "stress") return "Von Mises Stress";
  if (field === "displacement") return "Displacement Magnitude";
  return field;
}

function caeModeLabel(mode: string) {
  if (mode === "cad_only") return "CAD-only";
  if (mode === "cae_setup") return "CAE setup";
  if (mode === "cae_result") return "CAE result (external solver-output)";
  if (mode === "cae_validation") return "CAE validation / review";
  return mode;
}

function caeModeClass(mode: string) {
  if (mode === "cad_only") return "mode-cad-only";
  if (mode === "cae_setup") return "mode-cae-setup";
  if (mode === "cae_result") return "mode-cae-result";
  if (mode === "cae_validation") return "mode-cae-validation";
  return "";
}

function mutabilityLabel(capability: CapabilityDescriptor) {
  const parts = [];
  if (capability.mutates_cad) parts.push("CAD");
  if (capability.mutates_package) parts.push(".aieng");
  if (capability.may_update_claim_map) parts.push("claim");
  return parts.length ? parts.join(" + ") : "read-only";
}

function workflowStepLabel(kind: string) {
  if (kind === "tool") return "runtime tool";
  if (kind === "mcp_tool") return "MCP tool";
  if (kind === "llm") return "LLM";
  if (kind === "approval") return "approval";
  if (kind === "benchmark") return "benchmark";
  if (kind === "artifact") return "artifact";
  return kind;
}

function formatRecordSummary(record: Record<string, unknown>) {
  return Object.entries(record)
    .filter(([, value]) => value != null && value !== "")
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" / ");
}

function summarizeAssistantReply(response: ChatResponse, mode: "plan" | "execute") {
  const prefix = mode === "execute" ? "已执行编排请求。" : "已生成编排计划。";
  return `${prefix} ${response.reply}`;
}

function runtimeRunToChatPlan(run: RuntimeRun): ChatResponse["plan"] {
  return run.plan.map((step) => {
    const tc = run.tool_calls.find((c) => c.name === step.name);
    const tr = tc ? run.tool_results.find((r) => r.id === tc.id) : undefined;
    const status =
      tr?.status === "success"
        ? "done"
        : tr?.status === "needs_approval"
          ? "needs_approval"
          : tr?.status === "error"
            ? "failed"
            : "pending";
    return {
      tool: step.name,
      description: step.description,
      status,
      inputs: typeof step.input === "object" && step.input !== null ? (step.input as Record<string, unknown>) : {},
      output: tr?.output as Record<string, unknown> | null ?? null,
    };
  });
}

function formatGeometryResult(output: Record<string, unknown>): string {
  if (!output || output.status === "error") {
    const code = output?.code ?? "error";
    const msg = output?.message ?? "Geometry inspection failed.";
    return `几何检查失败 [${code}]: ${msg}`;
  }
  const bb = output.bounding_box as Record<string, number> | undefined;
  const dims = bb
    ? `${bb.xlen?.toFixed(1)} × ${bb.ylen?.toFixed(1)} × ${bb.zlen?.toFixed(1)} mm`
    : "—";
  const vol = typeof output.total_volume_mm3 === "number"
    ? `${(output.total_volume_mm3 / 1000).toFixed(2)} cm³`
    : "—";
  const faces = output.total_face_count ?? "—";
  const solids = output.total_solid_count ?? "—";
  const ver = output.freecad_version ? ` (FreeCAD ${output.freecad_version})` : "";
  return `几何检查完成${ver} — 外形尺寸 ${dims}，体积 ${vol}，${solids} 个实体，${faces} 个面`;
}

function formatArtifactChanges(run: import("./types").RuntimeRun): string | null {
  const allArtifacts = run.tool_results.flatMap((tr) => tr.artifacts ?? []);
  if (allArtifacts.length === 0) return null;
  const paths = allArtifacts
    .filter((a): a is Record<string, unknown> => typeof a === "object" && a !== null)
    .map((a) => String(a.path ?? ""))
    .filter(Boolean);
  if (paths.length === 0) return null;
  return "变更文件:\n" + paths.map((p) => `  - ${p}`).join("\n");
}

function extractArtifactPaths(run: import("./types").RuntimeRun): string[] {
  const allArtifacts = run.tool_results.flatMap((tr) => tr.artifacts ?? []);
  return allArtifacts
    .filter((a): a is Record<string, unknown> => typeof a === "object" && a !== null)
    .map((a) => String(a.path ?? ""))
    .filter(Boolean);
}

function isLowRiskArtifactPath(path: string): boolean {
  const lower = path.toLowerCase();
  return [".json", ".txt", ".md", ".yaml", ".yml", ".inp", ".csv", ".log"].some((ext) => lower.endsWith(ext));
}

function projectViewerUrl(project: ProjectRecord | null) {
  if (!project?.id || !project?.web_asset) return null;
  return `/assets/projects/${project.id}/${project.web_asset}`;
}

function resolveAssetFormat(assetUrl?: string | null, assetFormat?: string | null) {
  if (assetFormat) return assetFormat;
  if (!assetUrl) return null;
  const normalized = assetUrl.toLowerCase();
  if (normalized.endsWith(".glb")) return "glb";
  if (normalized.endsWith(".stl")) return "stl";
  return null;
}

function withAssetVersion(assetUrl?: string | null, version?: string | null) {
  if (!assetUrl || !version) return assetUrl ?? null;
  const separator = assetUrl.includes("?") ? "&" : "?";
  return `${assetUrl}${separator}v=${encodeURIComponent(version)}`;
}

function sampleColormap(t: number, name?: string | null): THREE.Color {
  const c = Math.max(0, Math.min(1, t));
  if (name === "coolwarm") {
    // blue(0) -> white(0.5) -> red(1)
    const r = c < 0.5 ? 0.2 + c * 1.6 : 1.0;
    const g = c < 0.5 ? 0.2 + c * 1.6 : 1.0 - (c - 0.5) * 2.0;
    const b = c < 0.5 ? 1.0 : 1.0 - (c - 0.5) * 1.6;
    return new THREE.Color(r, g, b);
  }
  // thermal: blue -> cyan -> green -> yellow -> red
  const r = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * c - 3)));
  const g = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * c - 2)));
  const b = Math.max(0, Math.min(1, 1.5 - Math.abs(4 * c - 1)));
  return new THREE.Color(r, g, b);
}

function applyYNormalizedColors(object: THREE.Object3D, colormap?: string | null): boolean {
  let applied = false;
  object.traverse((node) => {
    if (!(node instanceof THREE.Mesh)) return;
    const geo = node.geometry as THREE.BufferGeometry;
    const pos = geo.attributes.position;
    if (!pos) return;
    let yMin = Infinity;
    let yMax = -Infinity;
    for (let i = 0; i < pos.count; i++) {
      const y = pos.getY(i);
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    }
    const yRange = yMax > yMin ? yMax - yMin : 1;
    const colors = new Float32Array(pos.count * 3);
    for (let i = 0; i < pos.count; i++) {
      const col = sampleColormap((pos.getY(i) - yMin) / yRange, colormap);
      colors[i * 3] = col.r;
      colors[i * 3 + 1] = col.g;
      colors[i * 3 + 2] = col.b;
    }
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    node.material = new THREE.MeshStandardMaterial({ vertexColors: true, metalness: 0.1, roughness: 0.65 });
    applied = true;
  });
  return applied;
}

function fitCameraToObject(
  camera: THREE.PerspectiveCamera,
  controls: { target: THREE.Vector3; update(): void },
  object: THREE.Object3D,
) {
  const bounds = new THREE.Box3().setFromObject(object);
  if (bounds.isEmpty()) return false;

  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const maxDimension = Math.max(size.x, size.y, size.z, 1);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const distance = (maxDimension / (2 * Math.tan(fov / 2))) * 1.8;

  camera.near = Math.max(distance / 100, 0.1);
  camera.far = Math.max(distance * 20, 1000);
  camera.position.copy(center).add(new THREE.Vector3(distance, distance * 0.7, distance));
  camera.lookAt(center);
  camera.updateProjectionMatrix();

  controls.target.copy(center);
  controls.update();
  return true;
}

function ModelViewer({
  assetUrl,
  assetFormat,
  fieldDescriptor,
}: {
  assetUrl?: string | null;
  assetFormat?: string | null;
  fieldDescriptor?: SolverFieldDescriptor | null;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [viewerState, setViewerState] = useState<{ status: ViewerLoadState; detail: string }>({
    status: "idle",
    detail: "等待生成预览资产",
  });
  const fieldDescriptorKey = fieldDescriptor
    ? [
        fieldDescriptor.project_id,
        fieldDescriptor.field_name,
        fieldDescriptor.format,
        fieldDescriptor.basis ?? "",
        fieldDescriptor.colormap ?? "",
        fieldDescriptor.min_value,
        fieldDescriptor.max_value,
        fieldDescriptor.unit ?? "",
        fieldDescriptor.source ?? "",
      ].join("|")
    : "";

  useEffect(() => {
    if (!hostRef.current) return;

    const host = hostRef.current;
    const getHostSize = () => ({
      width: Math.max(host.clientWidth, 1),
      height: Math.max(host.clientHeight, 1),
    });
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#08111f");

    const initialSize = getHostSize();
    const camera = new THREE.PerspectiveCamera(45, initialSize.width / initialSize.height, 0.1, 1000);
    camera.position.set(3, 3, 5);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setSize(initialSize.width, initialSize.height, false);
    host.innerHTML = "";
    host.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0.5, 0.5, 0.5);

    scene.add(new THREE.AmbientLight(0xffffff, 1.4));
    const dirLight = new THREE.DirectionalLight(0xffffff, 2);
    dirLight.position.set(5, 10, 7);
    scene.add(dirLight);
    const fillLight = new THREE.DirectionalLight(0x60a5fa, 0.8);
    fillLight.position.set(-6, 4, -5);
    scene.add(fillLight);
    scene.add(new THREE.GridHelper(10, 10, 0x3b82f6, 0x334155));

    let object3d: THREE.Object3D | null = null;
    let isDisposed = false;
    const setSafeViewerState = (status: ViewerLoadState, detail: string) => {
      if (!isDisposed) {
        setViewerState({ status, detail });
      }
    };

    const resolvedFormat = resolveAssetFormat(assetUrl, assetFormat);
    const attachObject = (nextObject: THREE.Object3D) => {
      if (object3d) scene.remove(object3d);
      object3d = nextObject;
      if (fieldDescriptor?.basis === "y_normalized") {
        applyYNormalizedColors(nextObject, fieldDescriptor.colormap);
      }
      scene.add(nextObject);
      if (!fitCameraToObject(camera, controls, nextObject)) {
        setSafeViewerState("error", "预览资产缺少可用的几何边界，无法定位相机");
        return;
      }
      const fieldNote = fieldDescriptor ? ` · ${fieldLabel(fieldDescriptor.field_name)} overlay` : "";
      setSafeViewerState("ready", `真实预览资产已加载${fieldNote}`);
    };

    if (assetUrl && resolvedFormat) {
      const absoluteUrl = assetUrl.startsWith("http") ? assetUrl : `${api.base}${assetUrl}`;
      setSafeViewerState("loading", `正在加载 ${resolvedFormat.toUpperCase()} 预览资产`);

      if (resolvedFormat === "glb") {
        new GLTFLoader().load(
          absoluteUrl,
          (gltf: { scene: THREE.Object3D }) => {
            attachObject(gltf.scene);
          },
          undefined,
          (error: unknown) => {
            const detail = error instanceof Error ? error.message : "GLB 预览资产加载失败";
            setSafeViewerState("error", detail);
          },
        );
      } else if (resolvedFormat === "stl") {
        new STLLoader().load(
          absoluteUrl,
          (geometry: THREE.BufferGeometry) => {
            geometry.computeVertexNormals();
            const mesh = new THREE.Mesh(
              geometry,
              new THREE.MeshStandardMaterial({ color: 0x94a3b8, metalness: 0.15, roughness: 0.6 }),
            );
            attachObject(mesh);
          },
          undefined,
          (error: unknown) => {
            const detail = error instanceof Error ? error.message : "STL 预览资产加载失败";
            setSafeViewerState("error", detail);
          },
        );
      }
    } else if (assetUrl && !resolvedFormat) {
      setSafeViewerState("error", "预览资产格式无法识别");
    } else {
      setSafeViewerState("idle", "等待生成预览资产");
    }

    const onResize = () => {
      const size = getHostSize();
      camera.aspect = size.width / size.height;
      camera.updateProjectionMatrix();
      renderer.setSize(size.width, size.height, false);
    };

    let frame = 0;
    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(animate);
    };

    const resizeObserver = new ResizeObserver(() => onResize());
    resizeObserver.observe(host);
    window.addEventListener("resize", onResize);
    animate();

    return () => {
      isDisposed = true;
      resizeObserver.disconnect();
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(frame);
      controls.dispose();
      renderer.dispose();
      host.innerHTML = "";
    };
  }, [assetFormat, assetUrl, fieldDescriptorKey]);

  return (
    <div className="viewer-canvas-shell">
      <div className="viewer-canvas" ref={hostRef} />
      {viewerState.status !== "ready" ? (
        <div className={`viewer-overlay state-${viewerState.status}`}>
          <strong>
            {viewerState.status === "error"
              ? "预览加载失败"
              : viewerState.status === "loading"
                ? "正在加载真实模型"
                : "等待预览资产"}
          </strong>
          <span>{viewerState.detail}</span>
        </div>
      ) : null}
    </div>
  );
}

function JsonDisclosure({ title, body, defaultOpen = false }: { title: string; body: string; defaultOpen?: boolean }) {
  return (
    <details className="fold-block" open={defaultOpen}>
      <summary className="fold-summary">{title}</summary>
      <pre className="json-block">{body}</pre>
    </details>
  );
}

type LlmPriorityCardProps = {
  llmConfig: LLMConfig;
  llmReady: boolean;
  selectedProjectName?: string | null;
  agentBusy: boolean;
  controlPaneMode: ControlPaneMode;
  onOpenSettings(): void;
  onFocusChat(): void;
  onPlan(): void;
  onRun(): void;
};

function LlmPriorityCard({
  llmConfig,
  llmReady,
  selectedProjectName,
  agentBusy,
  controlPaneMode,
  onOpenSettings,
  onFocusChat,
  onPlan,
  onRun,
}: LlmPriorityCardProps) {
  return (
    <section className="card llm-priority-card">
      <div className="section-heading">
        <div>
          <h2>LLM 主流程</h2>
          <p>自然语言编排优先显示，Provider 参数统一放进环境设置。项目、Agent、CAE 仍按原流程保留。</p>
        </div>
        <div className={`llm-readiness-pill ${llmReady ? "ready" : "degraded"}`}>
          {llmReady ? "LLM ready" : "需要 Provider 配置"}
        </div>
      </div>

      <div className="capability-facts llm-facts-grid">
        <div><span>当前 Provider</span><strong>{getLlmProviderLabel(llmConfig.provider)}</strong></div>
        <div><span>当前模型</span><strong>{llmConfig.model || "-"}</strong></div>
        <div><span>当前项目</span><strong>{selectedProjectName ?? "未绑定项目"}</strong></div>
        <div><span>入口状态</span><strong>{controlPaneMode === "chat" ? "LLM 主流程" : "辅助面板中"}</strong></div>
      </div>

      <div className="summary-note summary-muted llm-summary-note">
        <strong>{llmReady ? "LLM planner 会优先接管" : "当前会退化到 heuristic planner"}</strong>
        <p>
          {llmReady
            ? "Agent plan、workflow、benchmark 会复用环境设置里的 LLM Provider。"
            : "没有完整 Provider 时，后端会保留启发式 planner 兜底。"}
        </p>
      </div>

      <div className="action-row llm-primary-actions">
        <button type="button" disabled={agentBusy} onClick={onPlan}>
          生成 LLM 计划
        </button>
        <button type="button" className="ghost-button" disabled={agentBusy} onClick={onRun}>
          执行 LLM 计划
        </button>
        <button type="button" className="ghost-button" onClick={onFocusChat}>
          聚焦主流程
        </button>
        <button type="button" className="ghost-button" onClick={onOpenSettings}>
          配置 Provider
        </button>
      </div>
    </section>
  );
}

type LlmProviderSettingsProps = {
  llmConfig: LLMConfig;
  llmReady: boolean;
  onChange<K extends keyof LLMConfig>(key: K, value: LLMConfig[K]): void;
  onPreset(provider: string): void;
  onRestore(): void;
};

function LlmProviderSettings({
  llmConfig,
  llmReady,
  onChange,
  onPreset,
  onRestore,
}: LlmProviderSettingsProps) {
  return (
    <section className="drawer-section">
      <div className="drawer-section-heading">
        <div>
          <h3>LLM Provider</h3>
          <p>Agent plan、workflow 和 benchmark 共用这份模型配置。</p>
        </div>
        <div className={`llm-readiness-pill ${llmReady ? "ready" : "degraded"}`}>
          {llmReady ? "已配置" : "待配置"}
        </div>
      </div>

      <div className="llm-preset-row">
        {LLM_PROVIDER_SUGGESTIONS.map((provider) => (
          <button
            key={provider}
            type="button"
            className={llmConfig.provider === provider ? "ghost-button llm-preset active" : "ghost-button llm-preset"}
            onClick={() => onPreset(provider)}
          >
            {getLlmProviderLabel(provider)}
          </button>
        ))}
      </div>

      <div className="runtime-config-grid llm-config-grid">
        <label className="form-field">
          <span>Provider</span>
          <input
            list="llm-provider-options"
            value={llmConfig.provider}
            onChange={(event) => onChange("provider", event.target.value)}
            placeholder="openai-compatible"
          />
          <datalist id="llm-provider-options">
            {LLM_PROVIDER_SUGGESTIONS.map((provider) => (
              <option key={provider} value={provider} />
            ))}
          </datalist>
        </label>
        <label className="form-field">
          <span>Model</span>
          <input value={llmConfig.model} onChange={(event) => onChange("model", event.target.value)} placeholder="configured-model" />
        </label>
        <label className="form-field">
          <span>API key env</span>
          <input
            value={llmConfig.api_key_env ?? ""}
            onChange={(event) => onChange("api_key_env", event.target.value)}
            placeholder="OPENAI_API_KEY"
          />
        </label>
        <label className="form-field">
          <span>Base URL</span>
          <input
            value={llmConfig.base_url ?? ""}
            onChange={(event) => onChange("base_url", event.target.value)}
            placeholder="https://api.openai.com/v1"
          />
        </label>
        <label className="form-field">
          <span>Temperature</span>
          <input
            type="number"
            min="0"
            max="2"
            step="0.1"
            value={llmConfig.temperature}
            onChange={(event) => onChange("temperature", Number(event.target.value) || 0)}
          />
        </label>
        <label className="form-field">
          <span>Top P</span>
          <input
            type="number"
            min="0"
            max="1"
            step="0.05"
            value={llmConfig.top_p}
            onChange={(event) => onChange("top_p", Number(event.target.value) || 1)}
          />
        </label>
        <label className="form-field runtime-config-span">
          <span>Max output tokens</span>
          <input
            type="number"
            min="256"
            step="256"
            value={llmConfig.max_output_tokens}
            onChange={(event) => onChange("max_output_tokens", Number(event.target.value) || DEFAULT_LLM_CONFIG.max_output_tokens)}
          />
        </label>
      </div>

      <div className="action-row runtime-config-actions">
        <button type="button" className="ghost-button" onClick={onRestore}>
          恢复 LLM 默认
        </button>
      </div>
    </section>
  );
}

type RuntimeSettingsDrawerProps = {
  open: boolean;
  runtime: RuntimeConfigSnapshot | null;
  runtimeDraft: RuntimeConfig | null;
  runtimeBusy: boolean;
  runtimeNotice: Notice | null;
  runtimeProvider: string;
  runtimeReady: boolean;
  llmConfig: LLMConfig;
  llmReady: boolean;
  onClose(): void;
  onDraftChange<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]): void;
  onLlmChange<K extends keyof LLMConfig>(key: K, value: LLMConfig[K]): void;
  onLlmPreset(provider: string): void;
  onLlmRestore(): void;
  onTest(): void;
  onSave(): void;
  onRestore(): void;
};

function RuntimeSettingsDrawer({
  open,
  runtime,
  runtimeDraft,
  runtimeBusy,
  runtimeNotice,
  runtimeProvider,
  runtimeReady,
  llmConfig,
  llmReady,
  onClose,
  onDraftChange,
  onLlmChange,
  onLlmPreset,
  onLlmRestore,
  onTest,
  onSave,
  onRestore,
}: RuntimeSettingsDrawerProps) {
  if (!open) return null;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="settings-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="环境设置"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <div>
            <h2>环境设置</h2>
            <p>集中管理 LLM Provider 和 CAD Runtime。主工作区只显示当前状态与常用操作。</p>
          </div>
          <button type="button" className="ghost-button drawer-close" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="drawer-body">
          <LlmProviderSettings
            llmConfig={llmConfig}
            llmReady={llmReady}
            onChange={onLlmChange}
            onPreset={onLlmPreset}
            onRestore={onLlmRestore}
          />

          <section className="drawer-section">
            <div className="drawer-section-heading">
              <div>
                <h3>CAD Runtime</h3>
                <p>STEP 导入、预览、语义刷新和 FreeCAD MCP 能力使用这组配置。</p>
              </div>
            </div>

            <div className="runtime-config-grid">
              <label className="form-field">
                <span>CAD Provider</span>
                <select
                  value={runtimeDraft?.provider ?? "freecad"}
                  disabled={runtimeBusy}
                  onChange={(event) => onDraftChange("provider", event.target.value)}
                >
                  {CAD_PROVIDER_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="form-field">
                <span>Topology Backend</span>
                <select
                  value={runtimeDraft?.topology_backend ?? "auto"}
                  disabled={runtimeBusy}
                  onChange={(event) => onDraftChange("topology_backend", event.target.value)}
                >
                  <option value="auto">auto</option>
                  <option value="mock">mock</option>
                  <option value="occ">occ</option>
                </select>
              </label>
              <label className="form-field runtime-config-span">
                <span>FreeCAD Home</span>
                <input
                  value={runtimeDraft?.freecad_home ?? ""}
                  disabled={runtimeBusy}
                  onChange={(event) => onDraftChange("freecad_home", event.target.value)}
                  placeholder="FreeCAD 安装目录"
                />
              </label>
              <label className="form-field runtime-config-span">
                <span>FREECAD_MCP_ROOT</span>
                <input
                  value={runtimeDraft?.freecad_mcp_root ?? ""}
                  disabled={runtimeBusy}
                  onChange={(event) => onDraftChange("freecad_mcp_root", event.target.value)}
                  placeholder="aieng-freecad-mcp 仓库目录"
                />
              </label>
              <label className="form-field runtime-config-span">
                <span>AIENG_ROOT</span>
                <input
                  value={runtimeDraft?.aieng_root ?? ""}
                  disabled={runtimeBusy}
                  onChange={(event) => onDraftChange("aieng_root", event.target.value)}
                  placeholder="aieng 仓库目录"
                />
              </label>
            </div>

            <div className="action-row runtime-config-actions">
              <button disabled={!runtimeDraft || runtimeBusy} onClick={onTest}>
                测试 CAD 配置
              </button>
              <button disabled={!runtimeDraft || runtimeBusy} onClick={onSave}>
                保存 CAD 配置
              </button>
              <button disabled={!runtime?.defaults || runtimeBusy} onClick={onRestore}>
                恢复 CAD 默认
              </button>
            </div>

            <div className="runtime-probe-grid">
              <div>
                <span>当前 Provider</span>
                <strong>{runtimeProvider}</strong>
              </div>
              <div>
                <span>运行时状态</span>
                <strong>{runtimeReady ? "已就绪" : "待配置"}</strong>
              </div>
              <div>
                <span>拓扑后端</span>
                <strong>{runtime?.probe.topology_backend_resolved ?? "-"}</strong>
              </div>
              <div>
                <span>FreeCADCmd</span>
                <strong>{runtime?.probe.freecad_cmd_exists ? "已找到" : "未找到"}</strong>
              </div>
            </div>
          </section>

          {runtime?.probe.issues?.length ? (
            <div className="summary-note">
              <strong>检测问题</strong>
              <p>{runtime.probe.issues.join("；")}</p>
            </div>
          ) : null}

          {runtime?.probe.bridge_error ? (
            <div className="summary-note">
              <strong>Bridge 探测</strong>
              <p>{runtime.probe.bridge_error}</p>
            </div>
          ) : null}

          {runtimeNotice ? (
            <div className={`result-banner result-${runtimeNotice.tone}`}>
              <strong>{runtimeNotice.title}</strong>
              <span>{runtimeNotice.detail}</span>
            </div>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

export default function App() {
  const [runtime, setRuntime] = useState<RuntimeConfigSnapshot | null>(null);
  const [runtimeDraft, setRuntimeDraft] = useState<RuntimeConfig | null>(null);
  const [runtimeNotice, setRuntimeNotice] = useState<Notice | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [summary, setSummary] = useState<ProjectSummary | null>(null);
  const [projectName, setProjectName] = useState("STEP 工作台项目");
  const [message, setMessage] = useState("检查当前项目状态，并生成一份可审阅、可执行的 LLM Agent 计划。");
  const [chat, setChat] = useState<ChatResponse | null>(null);
  const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [stages, setStages] = useState<StageItem[]>(BASE_STAGES);
  const [selectedCaeField, setSelectedCaeField] = useState("stress");
  const [fieldDescriptor, setFieldDescriptor] = useState<SolverFieldDescriptor | null>(null);
  const [lastRuntimeRun, setLastRuntimeRun] = useState<RuntimeRun | null>(null);
  const [caeRefreshing, setCaeRefreshing] = useState(false);
  const [metricsInputPath, setMetricsInputPath] = useState("");
  const [metricsLoadCaseId, setMetricsLoadCaseId] = useState("load_case_001");
  const [metricsSoftware, setMetricsSoftware] = useState("");
  const [metricsImporting, setMetricsImporting] = useState(false);
  const [frdInputPath, setFrdInputPath] = useState("");
  const [frdLoadCaseId, setFrdLoadCaseId] = useState("load_case_001");
  const [frdSoftware, setFrdSoftware] = useState("CalculiX");
  const [frdExtracting, setFrdExtracting] = useState(false);
  const [capabilities, setCapabilities] = useState<CapabilityDescriptor[]>([]);
  const [capabilityCategory, setCapabilityCategory] = useState("all");
  const [capabilityQuery, setCapabilityQuery] = useState("");
  const [selectedCapabilityName, setSelectedCapabilityName] = useState<string>("");
  const [capabilityPreview, setCapabilityPreview] = useState<CapabilityPreview | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState("");
  const [benchmarkScenarios, setBenchmarkScenarios] = useState<BenchmarkScenario[]>([]);
  const [selectedScenarioId, setSelectedScenarioId] = useState("");
  const [benchmarkRun, setBenchmarkRun] = useState<BenchmarkRun | null>(null);
  const [benchmarkBusy, setBenchmarkBusy] = useState(false);
  const [llmConfig, setLlmConfig] = useState<LLMConfig>(DEFAULT_LLM_CONFIG);
  const [controlPaneMode, setControlPaneMode] = useState<ControlPaneMode>("chat");
  const [agentPlan, setAgentPlan] = useState<AgentPlan | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [artifactViewerPath, setArtifactViewerPath] = useState("");
  const [artifactViewerData, setArtifactViewerData] = useState<ArtifactResponse | null>(null);
  const [artifactViewerBusy, setArtifactViewerBusy] = useState(false);
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const sidePaneRef = useRef<HTMLElement | null>(null);

  const selectedProject = useMemo(
    () => projects.find((item) => item.id === selectedId) ?? null,
    [projects, selectedId],
  );
  const capabilityCategories = useMemo(() => {
    const values = Array.from(new Set(capabilities.map((item) => item.category))).sort();
    return ["all", ...values];
  }, [capabilities]);
  const filteredCapabilities = useMemo(() => {
    const query = capabilityQuery.trim().toLowerCase();
    return capabilities.filter((item) => {
      const categoryOk = capabilityCategory === "all" || item.category === capabilityCategory;
      const queryOk =
        !query ||
        item.name.toLowerCase().includes(query) ||
        item.purpose.toLowerCase().includes(query) ||
        item.source.toLowerCase().includes(query);
      return categoryOk && queryOk;
    });
  }, [capabilities, capabilityCategory, capabilityQuery]);
  const selectedCapability = useMemo(
    () => capabilities.find((item) => item.name === selectedCapabilityName) ?? filteredCapabilities[0] ?? null,
    [capabilities, filteredCapabilities, selectedCapabilityName],
  );
  const selectedWorkflow = useMemo(
    () => workflows.find((item) => item.id === selectedWorkflowId) ?? workflows[0] ?? null,
    [workflows, selectedWorkflowId],
  );
  const fallbackViewerUrl = useMemo(() => projectViewerUrl(selectedProject), [selectedProject]);
  const rawViewerUrl = summary?.viewer_url ?? fallbackViewerUrl;
  const viewerVersion = summary?.project?.updated_at ?? selectedProject?.updated_at ?? null;
  const effectiveViewerUrl = useMemo(() => withAssetVersion(rawViewerUrl, viewerVersion), [rawViewerUrl, viewerVersion]);
  const summaryViewerFormat = typeof summary?.viewer?.asset_format === "string" ? summary.viewer.asset_format : null;
  const effectiveViewerFormat = resolveAssetFormat(rawViewerUrl, summaryViewerFormat ?? selectedProject?.web_asset_format ?? null);
  const llmReady = useMemo(() => isLlmConfigReady(llmConfig), [llmConfig]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(LLM_CONFIG_STORAGE_KEY);
      if (!raw) return;
      setLlmConfig(normalizeLlmConfig(JSON.parse(raw)));
    } catch {
      // Ignore malformed local cache and keep defaults.
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(LLM_CONFIG_STORAGE_KEY, JSON.stringify(llmConfig));
    } catch {
      // Ignore persistence failures in private mode / restricted environments.
    }
  }, [llmConfig]);

  function buildFallbackSummary(project: ProjectRecord, runtimeSnapshot: RuntimeConfigSnapshot | null = runtime): ProjectSummary {
    return {
      project,
      files: {},
      members: [],
      manifest: null,
      feature_graph: null,
      topology: null,
      validation: null,
      viewer: {
        asset_format: project.web_asset_format ?? null,
        asset_path: project.web_asset ?? null,
        asset_exists: Boolean(project.web_asset),
      },
      viewer_url: projectViewerUrl(project),
      ai_summary: null,
      derived: {},
      summary_error: "project summary unavailable; using project metadata fallback",
      summary_mode: "project_fallback",
      integration: runtimeSnapshot ?? undefined,
    };
  }

  async function refreshProjects(nextSelectedId?: string | null, runtimeSnapshot: RuntimeConfigSnapshot | null = runtime) {
    const list = await api.listProjects();
    setProjects(list);
    const candidate = nextSelectedId ?? selectedId ?? list[0]?.id ?? null;
    setSelectedId(candidate);
    if (candidate) {
      try {
        setSummary(await api.getProject(candidate));
      } catch {
        const project = list.find((item) => item.id === candidate) ?? null;
        setSummary(project ? buildFallbackSummary(project, runtimeSnapshot) : null);
      }
    } else {
      setSummary(null);
    }
  }

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const runtimeSnapshot = await api.runtime();
      if (cancelled) return;
      setRuntime(runtimeSnapshot);
      setRuntimeDraft(runtimeSnapshot.config);
      const [nextCapabilities, nextWorkflows, nextScenarios] = await Promise.all([
        api.listCapabilities().catch(() => []),
        api.listWorkflows().catch(() => []),
        api.listBenchmarkScenarios().catch(() => []),
      ]);
      if (cancelled) return;
      setCapabilities(nextCapabilities);
      setSelectedCapabilityName(nextCapabilities[0]?.name ?? "");
      setWorkflows(nextWorkflows);
      setSelectedWorkflowId(nextWorkflows[0]?.id ?? "");
      setBenchmarkScenarios(nextScenarios);
      setSelectedScenarioId(nextScenarios[0]?.id ?? "");
      const list = await api.listProjects();
      if (cancelled) return;
      setProjects(list);
      const candidate = list[0]?.id ?? null;
      setSelectedId(candidate);
      if (candidate) {
        try {
          const nextSummary = await api.getProject(candidate);
          if (!cancelled) setSummary(nextSummary);
        } catch {
          if (cancelled) return;
          const project = list.find((item) => item.id === candidate) ?? null;
          setSummary(project ? buildFallbackSummary(project, runtimeSnapshot) : null);
        }
      } else {
        setSummary(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!settingsOpen) return;

    const previousOverflow = document.body.style.overflow;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSettingsOpen(false);
      }
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [settingsOpen]);

  function updateRuntimeDraft<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]) {
    setRuntimeDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  function syncRuntimeIntoSummary(snapshot: RuntimeConfigSnapshot) {
    setSummary((current) => (current ? { ...current, integration: snapshot } : current));
  }

  function restoreRuntimeDefaults() {
    if (!runtime?.defaults) return;
    setRuntimeDraft(runtime.defaults);
    setRuntimeNotice({ tone: "info", title: "已恢复默认值", detail: "表单已回填默认 CAD 配置，保存后才会生效。" });
  }

  async function runRuntimeTask(kind: "save" | "test", task: () => Promise<RuntimeConfigSnapshot>) {
    if (!runtimeDraft) return;
    setRuntimeBusy(true);
    setRuntimeNotice(null);
    try {
      const snapshot = await task();
      setRuntime(snapshot);
      setRuntimeDraft(snapshot.config);
      syncRuntimeIntoSummary(snapshot);
      setRuntimeNotice({
        tone: snapshot.probe.ready ? "success" : "info",
        title: kind === "save" ? "CAD 配置已保存" : "CAD 配置已测试",
        detail: getRuntimeDetail(snapshot),
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setRuntimeNotice({ tone: "error", title: "CAD 配置操作失败", detail });
    } finally {
      setRuntimeBusy(false);
    }
  }

  function resetStages() {
    setStages(BASE_STAGES.map((item) => ({ ...item, state: "idle" })));
  }

  function patchStage(key: string, state: StageState, detail?: string) {
    setStages((current) =>
      current.map((item) =>
        item.key === key ? { ...item, state, detail: detail ?? item.detail } : item,
      ),
    );
  }

  async function runBusyTask(task: () => Promise<void>) {
    setBusy(true);
    try {
      await task();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "操作失败", detail });
    } finally {
      setBusy(false);
    }
  }

  async function ensureProject() {
    if (selectedId) return selectedId;
    const baseName = selectedFile?.name.replace(/\.(step|stp)$/i, "") || projectName || "STEP 工作台项目";
    const created = await api.createProject(baseName);
    await refreshProjects(created.id);
    return created.id;
  }

  async function runWorkbenchImportFlow() {
    if (!selectedFile) {
      setNotice({ tone: "info", title: "请先选择 STEP 文件", detail: "工作台入口需要一个 .step 或 .stp 文件。" });
      return;
    }

    resetStages();
    setChat(null);
    setNotice(null);

    await runBusyTask(async () => {
      const projectId = await ensureProject();

      patchStage("upload", "active", `正在上传 ${selectedFile.name}`);
      await api.uploadFile(projectId, selectedFile);
      patchStage("upload", "done", `${selectedFile.name} 已上传`);

      patchStage("import", "active", "正在导入并补全 .aieng 语义包");
      await api.importAieng(projectId);
      patchStage("import", "done", "STEP 已导入并补全 topology、AAG、feature 和摘要");

      patchStage("preview", "active", "正在生成 Web 预览资产");
      await api.convert(projectId);
      patchStage("preview", "done", "预览资产已生成");

      patchStage("semantic", "active", "正在刷新校验和语义信息");
      await api.validate(projectId);
      await refreshProjects(projectId);
      patchStage("semantic", "done", "工作台语义信息已刷新");

      setNotice({
        tone: "success",
        title: "STEP 已接入工作台",
        detail: "已完成上传、导入 aieng、生成预览，并刷新语义信息。",
      });
    });
  }

  async function runProjectAction(
    key: string,
    action: () => Promise<unknown>,
    title: string,
    detail: string,
  ) {
    if (!selectedId) return;
    setNotice(null);
    await runBusyTask(async () => {
      patchStage(key, "active");
      await action();
      await refreshProjects(selectedId);
      patchStage(key, "done");
      setNotice({ tone: "success", title, detail });
    });
  }

  const semanticSections = [
    {
      title: "Manifest / 校验",
      body: jsonBlock({ manifest: summary?.manifest ?? null, validation: summary?.validation ?? null }),
    },
    {
      title: "Feature / Topology",
      body: jsonBlock({ feature_graph: summary?.feature_graph ?? null, topology: summary?.topology ?? null }),
    },
  ];

  const aiSummary = (summary as any)?.ai_summary as string | undefined;
  const runtimeReady = runtime?.probe.ready ?? false;
  const runtimeProvider = getProviderLabel(runtime?.config.provider);
  const runtimeDetail = getRuntimeDetail(runtime);
  const validationState =
    (summary as any)?.validation?.report_ok === true
      ? "通过"
      : (summary as any)?.validation?.report_ok === false
        ? "失败"
        : "待刷新";
  const integrationBody = jsonBlock({
    integration: summary?.integration ?? null,
    members: summary?.members ?? [],
    viewer: (summary as any)?.viewer ?? null,
  });
  const caeSummary = summary?.cae ?? null;
  const caeFields = caeSummary?.available_fields ?? [];
  const hasCaeContext = caeSummary?.present ?? false;

  useEffect(() => {
    if (!caeFields.length) return;
    if (!caeFields.includes(selectedCaeField)) {
      setSelectedCaeField(caeFields[0]);
    }
  }, [caeFields, selectedCaeField]);

  useEffect(() => {
    if (!selectedId || !hasCaeContext) {
      setFieldDescriptor(null);
      return;
    }
    let cancelled = false;
    void api.getFieldDescriptor(selectedId, selectedCaeField)
      .then((desc) => {
        if (cancelled) return;
        setFieldDescriptor((current) => {
          if (
            current &&
            current.project_id === desc.project_id &&
            current.field_name === desc.field_name &&
            current.format === desc.format &&
            current.basis === desc.basis &&
            current.colormap === desc.colormap &&
            current.min_value === desc.min_value &&
            current.max_value === desc.max_value &&
            current.unit === desc.unit &&
            current.source === desc.source
          ) {
            return current;
          }
          return desc;
        });
      })
      .catch(() => { if (!cancelled) setFieldDescriptor(null); });
    return () => { cancelled = true; };
  }, [selectedId, selectedCaeField, hasCaeContext]);

  useEffect(() => {
    setChatHistory([]);
  }, [selectedId]);

  useEffect(() => {
    if (!chatLogRef.current) return;
    chatLogRef.current.scrollTop = chatLogRef.current.scrollHeight;
  }, [chatHistory]);

  useEffect(() => {
    sidePaneRef.current?.scrollTo({ top: 0 });
  }, [controlPaneMode]);

  function appendRunToChatHistory(run: RuntimeRun) {
    const statusLabel = runtimeStatusLabel(run.status);
    // If a geometry inspection tool completed, produce a human-readable summary line
    const geoResult = run.tool_results.find(
      (tr) =>
        tr.status === "success" &&
        run.tool_calls.find((tc) => tc.id === tr.id && tc.name === "freecad.inspect_geometry")
    );
    const geoLine =
      geoResult && typeof geoResult.output === "object" && geoResult.output !== null
        ? formatGeometryResult(geoResult.output as Record<string, unknown>)
        : null;
    const artifactLine = formatArtifactChanges(run);
    const body = geoLine
      ? `[本地运行时] ${statusLabel} — ${geoLine}${artifactLine ? "\n" + artifactLine : ""}`
      : run.summary
        ? `[本地运行时] ${statusLabel} — ${run.summary}${artifactLine ? "\n" + artifactLine : ""}`
        : `[本地运行时] ${statusLabel}${artifactLine ? "\n" + artifactLine : ""}`;
    const artifactPaths = extractArtifactPaths(run);

    // Extract artifact_diffs from cae.apply_setup_patch output
    let artifactDiffs: ArtifactDiff[] | undefined;
    const patchResult = run.tool_results.find((tr) =>
      tr.status === "success" &&
      run.tool_calls.find((tc) => tc.id === tr.id && tc.name === "cae.apply_setup_patch")
    );
    if (patchResult && typeof patchResult.output === "object" && patchResult.output !== null) {
      const diffs = (patchResult.output as Record<string, unknown>).artifact_diffs;
      if (Array.isArray(diffs) && diffs.length > 0) {
        artifactDiffs = diffs as ArtifactDiff[];
      }
    }

    setChatHistory((current) => [
      ...current,
      {
        id: createChatId(),
        role: "assistant",
        body,
        createdAt: new Date().toISOString(),
        mode: "runtime",
        plan: runtimeRunToChatPlan(run),
        errors: run.errors,
        auditLogUrl: null,
        artifactPaths: artifactPaths.length ? artifactPaths : undefined,
        artifactDiffs,
      },
    ]);
  }

  async function refreshAgentWorkbench() {
    await runBusyTask(async () => {
      const [nextCapabilities, nextWorkflows, nextScenarios] = await Promise.all([
        api.listCapabilities(),
        api.listWorkflows(),
        api.listBenchmarkScenarios(),
      ]);
      setCapabilities(nextCapabilities);
      setWorkflows(nextWorkflows);
      setBenchmarkScenarios(nextScenarios);
      setSelectedCapabilityName((current) => current || nextCapabilities[0]?.name || "");
      setSelectedWorkflowId((current) => current || nextWorkflows[0]?.id || "");
      setSelectedScenarioId((current) => current || nextScenarios[0]?.id || "");
      setNotice({ tone: "success", title: "Agent 工作台已刷新", detail: "能力注册表、工作流和 benchmark 场景已重新读取。" });
    });
  }

  async function previewSelectedCapability(approved = false) {
    if (!selectedCapability) return;
    await runBusyTask(async () => {
      const preview = await api.previewCapability(
        selectedCapability.name,
        selectedId ? { project_id: selectedId } : {},
        approved,
      );
      setCapabilityPreview(preview);
      setNotice({
        tone: preview.status === "success" ? "success" : "info",
        title: preview.approval_required ? "需要审批" : "能力预览完成",
        detail: preview.preview?.warnings?.[0] || preview.errors?.[0] || `${selectedCapability.name} preview ready.`,
      });
    });
  }

  async function runSelectedWorkflow() {
    if (!selectedWorkflow) return;
    const workflowMessage = `run workflow ${selectedWorkflow.id}`;
    await runBusyTask(async () => {
      const run = await api.startRun(workflowMessage, selectedId ?? null, selectedId ? { project_id: selectedId } : null, {
        workflow_id: selectedWorkflow.id,
        steps: selectedWorkflow.steps,
        llm_config: llmConfig,
      });
      setLastRuntimeRun(run);
      appendRunToChatHistory(run);
      setNotice({
        tone: run.status === "completed" ? "success" : run.status === "awaiting_approval" ? "info" : "error",
        title: `工作流 — ${runtimeStatusLabel(run.status)}`,
        detail: run.summary || run.errors[0] || selectedWorkflow.title,
      });
    });
  }

  function updateLlmConfig<K extends keyof LLMConfig>(key: K, value: LLMConfig[K]) {
    setLlmConfig((current) => ({ ...current, [key]: value }));
  }

  function applyLlmProviderPreset(provider: string) {
    setLlmConfig((current) => {
      const next = { ...current, provider };
      if (provider === "anthropic") {
        next.api_key_env = "ANTHROPIC_API_KEY";
        next.base_url = "";
      } else if (provider === "azure-openai") {
        next.api_key_env = "AZURE_OPENAI_API_KEY";
      } else {
        next.api_key_env = "OPENAI_API_KEY";
      }
      return next;
    });
  }

  function restoreDefaultLlmConfig() {
    setLlmConfig({ ...DEFAULT_LLM_CONFIG });
  }

  async function runBenchmark(dryRun: boolean) {
    if (!selectedScenarioId || benchmarkBusy) return;
    setBenchmarkBusy(true);
    setNotice(null);
    try {
      const run = await api.startBenchmarkRun({
        scenario_id: selectedScenarioId,
        condition: "both",
        dry_run: dryRun,
        llm_config: llmConfig,
      });
      setBenchmarkRun(run);
      setNotice({
        tone: run.status === "completed" ? "success" : "error",
        title: dryRun ? "Benchmark dry-run 完成" : "Benchmark 运行完成",
        detail: run.errors?.[0] || run.warnings[0] || run.result_path || run.run_id,
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "Benchmark 运行失败", detail });
    } finally {
      setBenchmarkBusy(false);
    }
  }

  async function submitRuntime() {
    const prompt = message.trim();
    if (!prompt) {
      setNotice({ tone: "info", title: "请输入请求", detail: "本地运行时需要一条自然语言指令。" });
      return;
    }
    setChatHistory((current) => [
      ...current,
      { id: createChatId(), role: "user", body: prompt, createdAt: new Date().toISOString(), mode: "runtime" },
    ]);
    await runBusyTask(async () => {
      const run = await api.startRun(prompt, selectedId ?? null);
      setLastRuntimeRun(run);
      appendRunToChatHistory(run);
      const statusLabel = runtimeStatusLabel(run.status);
      setNotice({
        tone: run.status === "completed" ? "success" : run.status === "awaiting_approval" ? "info" : "error",
        title: `本地运行时 — ${statusLabel}`,
        detail: run.summary || run.errors[0] || "",
      });
    });
  }

  async function refreshCaeSummary() {
    if (!selectedId || caeRefreshing) return;
    setCaeRefreshing(true);
    setNotice(null);
    try {
      const run = await api.startRun("refresh cae summary", selectedId, {
        project_id: selectedId,
        overwrite: true,
      });
      setLastRuntimeRun(run);
      appendRunToChatHistory(run);
      if (run.status === "completed") {
        await refreshProjects(selectedId);
        setNotice({
          tone: "success",
          title: "CAE 摘要已刷新",
          detail: run.summary || "已重新生成 CAE 结果摘要、证据索引和 Markdown 文件。",
        });
      } else {
        setNotice({
          tone: "error",
          title: "CAE 摘要刷新失败",
          detail: run.errors[0] || run.summary || "运行时返回非成功状态。",
        });
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "CAE 摘要刷新失败", detail });
    } finally {
      setCaeRefreshing(false);
    }
  }

  async function importMetricsAndRefresh() {
    if (!selectedId || metricsImporting) return;
    const inputPath = metricsInputPath.trim();
    if (!inputPath) {
      setNotice({ tone: "info", title: "请输入指标文件路径", detail: "需要提供外部 JSON/CSV 指标文件的绝对路径。" });
      return;
    }
    setMetricsImporting(true);
    setNotice(null);
    try {
      // Step 1: generate computed_metrics.json
      const genRun = await api.startRun("generate computed metrics", selectedId, {
        inputPath,
        project_id: selectedId,
        loadCaseId: metricsLoadCaseId.trim() || "load_case_001",
        software: metricsSoftware.trim() || undefined,
      });
      setLastRuntimeRun(genRun);
      appendRunToChatHistory(genRun);
      if (genRun.status !== "completed") {
        setNotice({
          tone: "error",
          title: "计算指标生成失败",
          detail: genRun.errors[0] || genRun.summary || "运行时返回非成功状态。",
        });
        setMetricsImporting(false);
        return;
      }

      // Step 2: refresh CAE summary
      const refreshRun = await api.startRun("refresh cae summary", selectedId, {
        project_id: selectedId,
        overwrite: true,
      });
      setLastRuntimeRun(refreshRun);
      appendRunToChatHistory(refreshRun);
      if (refreshRun.status === "completed") {
        await refreshProjects(selectedId);
        setNotice({
          tone: "success",
          title: "计算指标已导入并刷新摘要",
          detail: refreshRun.summary || "已生成计算指标并重新生成 CAE 结果摘要。",
        });
      } else {
        setNotice({
          tone: "error",
          title: "CAE 摘要刷新失败",
          detail: refreshRun.errors[0] || refreshRun.summary || "运行时返回非成功状态。",
        });
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "导入计算指标失败", detail });
    } finally {
      setMetricsImporting(false);
    }
  }

  async function extractFrdAndRefresh() {
    if (!selectedId || frdExtracting) return;
    const frdPath = frdInputPath.trim();
    if (!frdPath) {
      setNotice({ tone: "info", title: "请输入 FRD 文件路径", detail: "需要提供 CalculiX .frd 结果文件的绝对路径。" });
      return;
    }
    setFrdExtracting(true);
    setNotice(null);
    try {
      const extractRun = await api.startRun("extract solver results", selectedId, {
        project_id: selectedId,
        frdPath: frdPath,
        loadCaseId: frdLoadCaseId.trim() || "load_case_001",
        software: frdSoftware.trim() || "CalculiX",
        refresh_result_summary: true,
      });
      setLastRuntimeRun(extractRun);
      appendRunToChatHistory(extractRun);
      if (extractRun.status !== "completed") {
        setNotice({
          tone: "error",
          title: "FRD 提取失败",
          detail: extractRun.errors[0] || extractRun.summary || "运行时返回非成功状态。",
        });
        setFrdExtracting(false);
        return;
      }
      await refreshProjects(selectedId);
      setNotice({
        tone: "success",
        title: "FRD 结果已提取并刷新摘要",
        detail: extractRun.summary || "已从 .frd 文件提取最大位移和最大 von Mises 应力，并重新生成 CAE 结果摘要。",
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "FRD 提取失败", detail });
    } finally {
      setFrdExtracting(false);
    }
  }

  async function viewArtifact(path: string) {
    if (!selectedId || !path.trim()) return;
    setArtifactViewerPath(path.trim());
    setArtifactViewerBusy(true);
    setArtifactViewerData(null);
    try {
      const data = await api.getProjectArtifact(selectedId, path.trim());
      setArtifactViewerData(data);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setArtifactViewerData({
        path: path.trim(),
        exists: false,
        media_type: "unknown",
        warnings: [detail],
      });
    } finally {
      setArtifactViewerBusy(false);
    }
  }

  async function approveRun() {
    if (!lastRuntimeRun || lastRuntimeRun.status !== "awaiting_approval") return;
    await runBusyTask(async () => {
      const run = await api.approveRun(lastRuntimeRun.run_id);
      setLastRuntimeRun(run);
      appendRunToChatHistory(run);
      const statusLabel = runtimeStatusLabel(run.status);
      setNotice({
        tone: run.status === "completed" ? "success" : "error",
        title: `运行时审批 — ${statusLabel}`,
        detail: run.summary || run.errors[0] || "已批准并执行",
      });
    });
  }

  async function rejectRun() {
    if (!lastRuntimeRun || lastRuntimeRun.status !== "awaiting_approval") return;
    await runBusyTask(async () => {
      const run = await api.rejectRun(lastRuntimeRun.run_id);
      setLastRuntimeRun(run);
      appendRunToChatHistory(run);
      setNotice({ tone: "info", title: "运行时审批 — 已拒绝", detail: "已拒绝，待执行工具未运行。" });
    });
  }

  async function planAgentChat() {
    const prompt = message.trim();
    if (!prompt) {
      setNotice({ tone: "info", title: "请输入 Agent 目标", detail: "Agent 需要一条建模、检查或分析目标。" });
      return;
    }
    setAgentBusy(true);
    setNotice(null);
    try {
      const plan = await api.planAgent({
        message: prompt,
        project_id: selectedId ?? null,
        llm_config: llmConfig,
        dry_run: false,
      });
      setAgentPlan(plan);
      setChatHistory((current) => [
        ...current,
        { id: createChatId(), role: "user", body: prompt, createdAt: new Date().toISOString(), mode: "plan" },
        {
          id: createChatId(),
          role: "assistant",
          body: `[Agent ${plan.mode}] ${plan.reply}`,
          createdAt: new Date().toISOString(),
          mode: "runtime",
          plan: plan.steps.map((step) => ({
            tool: step.tool_name ?? step.id,
            description: step.description || step.tool_name || step.id,
            status: step.approval_required ? "needs_approval" : "pending",
            inputs: step.input ?? {},
            output: null,
          })),
          errors: [...(plan.errors ?? []), ...(plan.warnings ?? [])],
        },
      ]);
      setNotice({
        tone: plan.errors?.length ? "info" : "success",
        title: "Agent 计划已生成",
        detail: plan.preview.warnings[0] || `${plan.steps.length} 个步骤，${plan.requires_approval ? "包含审批闸门" : "无需审批"}`,
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "Agent 计划失败", detail });
    } finally {
      setAgentBusy(false);
    }
  }

  async function runAgentChat() {
    const prompt = message.trim();
    if (!prompt && !agentPlan) {
      setNotice({ tone: "info", title: "请输入 Agent 目标", detail: "可以先生成计划，也可以直接运行 Agent。" });
      return;
    }
    setAgentBusy(true);
    setNotice(null);
    try {
      const result = await api.runAgent({
        message: prompt || agentPlan?.message,
        project_id: selectedId ?? agentPlan?.project_id ?? null,
        llm_config: llmConfig,
        plan: agentPlan ?? undefined,
      });
      setAgentPlan(result.agent);
      setLastRuntimeRun(result.run);
      appendRunToChatHistory(result.run);
      setNotice({
        tone: result.run.status === "completed" ? "success" : result.run.status === "awaiting_approval" ? "info" : "error",
        title: `Agent 运行 — ${runtimeStatusLabel(result.run.status)}`,
        detail: result.run.summary || result.run.errors[0] || result.agent.preview.warnings[0] || result.agent.reply,
      });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setNotice({ tone: "error", title: "Agent 运行失败", detail });
    } finally {
      setAgentBusy(false);
    }
  }

  async function submitChat(mode: "plan" | "execute") {
    if (!selectedId) return;
    const prompt = message.trim();
    if (!prompt) {
      setNotice({ tone: "info", title: "请输入编排请求", detail: "聊天窗需要一条自然语言指令才能生成计划或执行。" });
      return;
    }

    setChatHistory((current) => [
      ...current,
      { id: createChatId(), role: "user", body: prompt, createdAt: new Date().toISOString(), mode },
    ]);

    await runBusyTask(async () => {
      const result = await api.chat(selectedId, prompt, mode === "execute");
      setChat(result);
      if (mode === "execute") {
        await refreshProjects(selectedId);
      }
      setChatHistory((current) => [
        ...current,
        {
          id: createChatId(),
          role: "assistant",
          body: summarizeAssistantReply(result, mode),
          createdAt: new Date().toISOString(),
          mode,
          plan: result.plan,
          errors: result.errors,
          auditLogUrl: result.audit_log_url ?? null,
        },
      ]);
      setNotice({
        tone: mode === "execute" ? "success" : "info",
        title: mode === "execute" ? "已执行安全步骤" : "已生成计划",
        detail: mode === "execute" ? "聊天窗已执行当前请求允许的后端步骤。" : "聊天窗已生成一组可审阅的受保护步骤。",
      });
    });
  }

  return (
    <>
      <div className="app-shell workbench-shell">
        <section className="viewer-pane">
          <div className="viewer-header">
            <div>
              <h1>aieng-platform Workbench</h1>
              <p>围绕 STEP 导入、模型预览、语义核对和后续编排组织单页工作区，环境配置收拢到页内设置抽屉。</p>
            </div>
            <div className="runtime-cluster">
              <div className="runtime-actions">
                <div className="runtime-pill">
                  {runtimeReady ? `${runtimeProvider} 运行时已就绪` : "CAD 运行时需配置"}
                </div>
                <button type="button" className="ghost-button" onClick={() => setSettingsOpen(true)}>
                  环境设置
                </button>
              </div>
              <small className="runtime-note">{runtimeDetail}</small>
            </div>
          </div>

          <div className="viewer-toolbar">
            <div className="viewer-toolbar-block">
              <span className="viewer-toolbar-label">当前项目</span>
              <strong>{selectedProject?.name ?? "未选择项目"}</strong>
            </div>
            <div className="viewer-toolbar-block">
              <span className="viewer-toolbar-label">当前 STEP</span>
              <strong>{selectedFile?.name ?? selectedProject?.source_step ?? "未选择文件"}</strong>
            </div>
            <div className="viewer-toolbar-block">
              <span className="viewer-toolbar-label">模型 ID</span>
              <strong>{getManifestString(summary, "model_id")}</strong>
            </div>
            <div className="viewer-toolbar-block">
              <span className="viewer-toolbar-label">校验状态</span>
              <strong>{validationState}</strong>
            </div>
          </div>

          <div className="viewer-stage-shell">
            <div className="viewer-stage-head">
              <div>
                <strong>模型预览</strong>
                <span>{effectiveViewerFormat ? `当前预览：${effectiveViewerFormat.toUpperCase()}` : "导入后将在这里显示模型预览"}</span>
              </div>
              <div className="viewer-stage-badge">{effectiveViewerUrl ? "预览可用" : "等待生成"}</div>
            </div>
            <ModelViewer assetUrl={effectiveViewerUrl} assetFormat={effectiveViewerFormat} fieldDescriptor={fieldDescriptor} />
          </div>

          <div className="viewer-insights">
            <div className="insight-card"><span>特征数</span><strong>{getDerivedNumber(summary, "feature_graph", "count")}</strong></div>
            <div className="insight-card"><span>拓扑实体</span><strong>{getDerivedNumber(summary, "topology", "count")}</strong></div>
            <div className="insight-card"><span>资源成员</span><strong>{summary?.members?.length ?? 0}</strong></div>
            <div className="insight-card"><span>最近更新</span><strong>{formatTime(selectedProject?.updated_at)}</strong></div>
          </div>
        </section>

        <aside className="side-pane" ref={sidePaneRef}>
          <div className="control-pane-header">
            <div>
              <span className="control-pane-kicker">Workbench Control</span>
              <strong>{CONTROL_PANE_MODES.find((mode) => mode.id === controlPaneMode)?.detail}</strong>
            </div>
            <button type="button" className="ghost-button compact-button" onClick={() => setSettingsOpen(true)}>
              环境
            </button>
          </div>

          <div className="control-pane-tabs" role="tablist" aria-label="Workbench control sections">
            {CONTROL_PANE_MODES.map((mode) => (
              <button
                key={mode.id}
                type="button"
                role="tab"
                aria-selected={controlPaneMode === mode.id}
                className={controlPaneMode === mode.id ? "control-pane-tab active" : "control-pane-tab"}
                onClick={() => setControlPaneMode(mode.id)}
              >
                <strong>{mode.label}</strong>
                <span>{mode.detail}</span>
              </button>
            ))}
          </div>

          <LlmPriorityCard
            llmConfig={llmConfig}
            llmReady={llmReady}
            selectedProjectName={selectedProject?.name ?? null}
            agentBusy={agentBusy}
            controlPaneMode={controlPaneMode}
            onOpenSettings={() => setSettingsOpen(true)}
            onFocusChat={() => setControlPaneMode("chat")}
            onPlan={() => void planAgentChat()}
            onRun={() => void runAgentChat()}
          />

          {notice ? (
            <div className={`result-banner result-${notice.tone} control-status-banner`}>
              <strong>{notice.title}</strong>
              <span>{notice.detail}</span>
            </div>
          ) : null}

          {controlPaneMode === "project" ? (
            <>
          <section className="card workbench-entry-card">
            <div className="section-heading">
              <div>
                <h2>导入模型</h2>
                <p>从这里进入工作台主流程：选 STEP、导入、生成预览并刷新语义结果。</p>
              </div>
            </div>

            <div className="inline-form">
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="新项目名称（可选）" />
              <button
                disabled={busy}
                onClick={() =>
                  void runBusyTask(async () => {
                    const created = await api.createProject(projectName);
                    await refreshProjects(created.id);
                    setNotice({ tone: "success", title: "项目已创建", detail: `已创建项目 ${created.name}。` });
                  })
                }
              >
                新建项目
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void runBusyTask(async () => {
                    const sample = await api.createSampleProject();
                    await refreshProjects(sample.id);
                    setNotice({ tone: "success", title: "示例已载入", detail: "已把 SFA-5.41 示例接入工作台。" });
                  })
                }
              >
                载入示例
              </button>
            </div>

            <label className="dropzone">
              <input className="dropzone-input" type="file" accept=".step,.stp" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
              <div className="dropzone-content">
                <strong>{selectedFile ? selectedFile.name : "选择 STEP 文件"}</strong>
                <span>{selectedFile ? "文件已就绪，可直接导入当前工作台。" : "支持 .step / .stp，若当前未选项目，会自动创建项目后继续。"}</span>
              </div>
            </label>

            <div className="action-row primary-actions">
              <button disabled={busy || !selectedFile} onClick={() => void runWorkbenchImportFlow()}>
                上传并导入到工作台
              </button>
              <button
                disabled={busy || !selectedId}
                onClick={() =>
                  selectedId &&
                  void runProjectAction("semantic", () => api.getProject(selectedId), "工作台已刷新", "已刷新当前项目的预览和语义状态。")
                }
              >
                刷新工作台
              </button>
            </div>

            <div className="workflow-list">
              {stages.map((stage) => (
                <div key={stage.key} className={`workflow-item status-${stage.state}`}>
                  <div>
                    <strong>{stage.label}</strong>
                    <p>{stage.detail}</p>
                  </div>
                  <span>{stage.state === "idle" ? "待执行" : stage.state === "active" ? "进行中" : stage.state === "done" ? "已完成" : "失败"}</span>
                </div>
              ))}
            </div>

          </section>

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>当前项目</h2>
                <p>聚焦当前选中的项目与最近项目，方便在工作流之间快速切换。</p>
              </div>
            </div>

            <div className="project-list">
              {projects.map((project) => (
                <button key={project.id} className={project.id === selectedId ? "project-item active" : "project-item"} onClick={() => void refreshProjects(project.id)}>
                  <div className="project-item-main">
                    <strong>{project.name}</strong>
                    <small>{project.id}</small>
                  </div>
                  <span>{project.status}</span>
                </button>
              ))}
            </div>

            <div className="project-metadata">
              <div><span>STEP</span><strong>{selectedProject?.source_step ?? "-"}</strong></div>
              <div><span>.aieng</span><strong>{selectedProject?.aieng_file ?? "-"}</strong></div>
              <div><span>预览资产</span><strong>{selectedProject?.web_asset ?? "-"}</strong></div>
              <div><span>错误</span><strong>{selectedProject?.last_error ?? "无"}</strong></div>
            </div>
          </section>

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>高级操作</h2>
                <p>在主流程之外，按需手动重跑导入、预览和校验能力。</p>
              </div>
            </div>

            <div className="action-grid">
              <button
                disabled={!selectedId || busy}
                onClick={() =>
                  selectedId &&
                  void runProjectAction("import", () => api.importAieng(selectedId), "重新导入成功", "已重新生成当前项目的 .aieng 包并补全语义资源。")
                }
              >
                重新导入 aieng
              </button>
              <button
                disabled={!selectedId || busy}
                onClick={() =>
                  selectedId &&
                  void runProjectAction("preview", () => api.convert(selectedId), "预览已更新", "已重跑 STEP 预览链并刷新模型资产。")
                }
              >
                重新生成预览
              </button>
              <button
                disabled={!selectedId || busy}
                onClick={() =>
                  selectedId &&
                  void runProjectAction("semantic", () => api.validate(selectedId), "校验已完成", "已执行后端校验并刷新语义信息。")
                }
              >
                校验语义信息
              </button>
              <button
                disabled={!selectedId || busy}
                onClick={() =>
                  selectedId &&
                  void runProjectAction("semantic", () => api.getProject(selectedId), "摘要已刷新", "已刷新当前项目的 manifest、topology 和 validation。")
                }
              >
                刷新项目摘要
              </button>
            </div>
          </section>

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>语义摘要</h2>
                <p>默认先看关键语义结论，再按需展开原始结构与集成信息。</p>
              </div>
            </div>

            <div className="semantic-overview">
              <div><span>模型 ID</span><strong>{getManifestString(summary, "model_id")}</strong></div>
              <div><span>资源成员</span><strong>{summary?.members?.length ?? 0}</strong></div>
              <div><span>特征数</span><strong>{getDerivedNumber(summary, "feature_graph", "count")}</strong></div>
              <div><span>拓扑数</span><strong>{getDerivedNumber(summary, "topology", "count")}</strong></div>
            </div>

            {aiSummary ? (
              <div className="summary-note summary-primary">
                <strong>AI 摘要</strong>
                <p>{aiSummary}</p>
              </div>
            ) : (
              <div className="summary-note summary-muted">
                <strong>AI 摘要</strong>
                <p>导入并富化后，这里会展示面向人的简要语义说明。</p>
              </div>
            )}

            {summary?.summary_error ? (
              <div className="summary-note">
                <strong>语义摘要已降级</strong>
                <p>{summary.summary_error}</p>
              </div>
            ) : null}

            {semanticSections.map((section) => (
              <JsonDisclosure key={section.title} title={`查看 ${section.title}`} body={section.body} />
            ))}
            <JsonDisclosure title="查看集成与预览元数据" body={integrationBody} />
          </section>
            </>
          ) : null}

          {controlPaneMode === "agent" ? (
            <>
          <section className="card agent-workbench-card">
            <div className="section-heading">
              <div>
                <h2>Capability Browser</h2>
                <p>统一查看 runtime、MCP、.aieng 包工具和 benchmark 能力，先看副作用，再决定是否进入流程。</p>
              </div>
              <button className="ghost-button" type="button" disabled={busy} onClick={() => void refreshAgentWorkbench()}>
                刷新能力
              </button>
            </div>

            <div className="capability-toolbar">
              <select value={capabilityCategory} onChange={(event) => setCapabilityCategory(event.target.value)}>
                {capabilityCategories.map((category) => (
                  <option key={category} value={category}>
                    {category === "all" ? "all categories" : category}
                  </option>
                ))}
              </select>
              <input
                value={capabilityQuery}
                onChange={(event) => setCapabilityQuery(event.target.value)}
                placeholder="搜索 tool / source / purpose"
              />
            </div>

            <div className="capability-browser">
              <div className="capability-list">
                {filteredCapabilities.slice(0, 40).map((capability) => (
                  <button
                    type="button"
                    key={`${capability.source}-${capability.name}`}
                    className={capability.name === selectedCapability?.name ? "capability-item active" : "capability-item"}
                    onClick={() => {
                      setSelectedCapabilityName(capability.name);
                      setCapabilityPreview(null);
                    }}
                  >
                    <strong>{capability.name}</strong>
                    <span>{capability.category} / {capability.source}</span>
                  </button>
                ))}
              </div>

              <div className="capability-detail">
                {selectedCapability ? (
                  <>
                    <div className="capability-detail-head">
                      <div>
                        <strong>{selectedCapability.name}</strong>
                        <span>{selectedCapability.purpose}</span>
                      </div>
                      <small className={selectedCapability.available ? "capability-available" : "capability-missing"}>
                        {selectedCapability.available ? "available" : "unavailable"}
                      </small>
                    </div>
                    <div className="capability-facts">
                      <div><span>Mutability</span><strong>{mutabilityLabel(selectedCapability)}</strong></div>
                      <div><span>Dry-run</span><strong>{selectedCapability.dry_run_support}</strong></div>
                      <div><span>Runtime</span><strong>{selectedCapability.runtime_requirements.join(", ") || "none"}</strong></div>
                      <div><span>Inputs</span><strong>{selectedCapability.required_inputs.length} required</strong></div>
                    </div>
                    {selectedCapability.unavailable_reason ? (
                      <div className="summary-note summary-muted">
                        <strong>Capability gap</strong>
                        <p>{selectedCapability.unavailable_reason}</p>
                      </div>
                    ) : null}
                    {selectedCapability.side_effects.length ? (
                      <div className="side-effect-list">
                        {selectedCapability.side_effects.map((effect) => (
                          <span key={effect}>{effect}</span>
                        ))}
                      </div>
                    ) : null}
                    <div className="action-row">
                      <button disabled={busy} onClick={() => void previewSelectedCapability(false)}>
                        Preview
                      </button>
                      <button className="ghost-button" disabled={busy} onClick={() => void previewSelectedCapability(true)}>
                        Preview as approved
                      </button>
                    </div>
                    {capabilityPreview ? (
                      <JsonDisclosure title="查看 capability preview" body={jsonBlock(capabilityPreview)} defaultOpen />
                    ) : null}
                  </>
                ) : (
                  <div className="summary-note summary-muted">
                    <strong>暂无能力</strong>
                    <p>后端未返回 capability registry。请检查 aieng 和 freecad-mcp 路径配置。</p>
                  </div>
                )}
              </div>
            </div>
          </section>

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>Agent Flow Panel</h2>
                <p>把一组工具、LLM、benchmark、审批和 artifact 步骤作为可审计 workflow 运行。</p>
              </div>
            </div>

            <label className="form-field">
              <span>Workflow</span>
              <select value={selectedWorkflow?.id ?? ""} onChange={(event) => setSelectedWorkflowId(event.target.value)}>
                {workflows.map((workflow) => (
                  <option key={workflow.id} value={workflow.id}>
                    {workflow.title}
                  </option>
                ))}
              </select>
            </label>

            {selectedWorkflow ? (
              <>
                <div className="summary-note summary-muted">
                  <strong>{selectedWorkflow.title}</strong>
                  <p>{selectedWorkflow.description}</p>
                </div>
                <div className="workflow-step-list">
                  {selectedWorkflow.steps.map((step) => (
                    <div key={step.id} className="workflow-step-item">
                      <span>{workflowStepLabel(step.kind)}</span>
                      <strong>{step.tool_name ?? step.id}</strong>
                      {step.approval_required ? <small>approval required</small> : null}
                    </div>
                  ))}
                </div>
                <div className="action-row">
                  <button disabled={busy || !selectedWorkflow} onClick={() => void runSelectedWorkflow()}>
                    运行选中工作流
                  </button>
                </div>
              </>
            ) : (
              <div className="summary-note summary-muted">
                <strong>暂无 workflow</strong>
                <p>后端未返回工作流定义。</p>
              </div>
            )}
          </section>

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>Benchmark Panel</h2>
                <p>复用环境设置中的同一份 Provider 配置，支持 dry-run 估算和真实 LLM A/B 运行。</p>
              </div>
            </div>

            <div className="runtime-config-grid">
              <label className="form-field">
                <span>Scenario</span>
                <select value={selectedScenarioId} onChange={(event) => setSelectedScenarioId(event.target.value)}>
                  {benchmarkScenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      {scenario.name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="summary-note summary-muted llm-inline-note runtime-config-span">
                <strong>{getLlmProviderLabel(llmConfig.provider)} / {llmConfig.model}</strong>
                <p>{llmReady ? "Benchmark 会直接复用当前 LLM Provider 配置。" : "当前 Provider 配置不完整，benchmark 可能无法走真实 LLM 路径。"}</p>
              </div>
            </div>

            <div className="action-row runtime-config-actions">
              <button disabled={benchmarkBusy || !selectedScenarioId} onClick={() => void runBenchmark(true)}>
                Dry-run / 成本估算
              </button>
              <button className="ghost-button" disabled={benchmarkBusy || !selectedScenarioId} onClick={() => void runBenchmark(false)}>
                真实运行 benchmark
              </button>
            </div>

            {benchmarkRun ? (
              <div className="benchmark-result">
                <div className="capability-facts">
                  <div><span>Run</span><strong>{benchmarkRun.run_id}</strong></div>
                  <div><span>Status</span><strong>{benchmarkRun.status}</strong></div>
                  <div><span>Mode</span><strong>{benchmarkRun.dry_run ? "dry-run" : "run"}</strong></div>
                  <div><span>Result</span><strong>{benchmarkRun.result_path ?? "-"}</strong></div>
                </div>
                {benchmarkRun.warnings.length ? (
                  <div className="side-effect-list">
                    {benchmarkRun.warnings.map((warning) => <span key={warning}>{warning}</span>)}
                  </div>
                ) : null}
                <JsonDisclosure title="查看 benchmark run payload" body={jsonBlock(benchmarkRun)} />
              </div>
            ) : null}
          </section>

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>Semantic Map</h2>
                <p>把 .aieng 资源按可用、缺失和证据链状态压缩成一个扫描视图。</p>
              </div>
            </div>
            <div className="semantic-map-grid">
              {[
                ["manifest", Boolean(summary?.manifest)],
                ["feature_graph", Boolean(summary?.feature_graph)],
                ["topology", Boolean(summary?.topology)],
                ["constraints", Boolean(summary?.constraints)],
                ["validation", Boolean(summary?.validation)],
                ["ai_summary", Boolean(summary?.ai_summary)],
                ["cae_context", Boolean(summary?.cae?.present)],
                ["result_summary", Boolean(summary?.cae?.result_summary)],
              ].map(([label, present]) => (
                <div key={String(label)} className={present ? "semantic-map-item present" : "semantic-map-item missing"}>
                  <span>{String(label)}</span>
                  <strong>{present ? "present" : "missing"}</strong>
                </div>
              ))}
            </div>
          </section>
            </>
          ) : null}

          {controlPaneMode === "cae" ? (
            <>
          {summary ? (
            <section className="card">
              <div className="section-heading">
                <div>
                  <h2>CAE Artifact Status</h2>
                  <p>Honest artifact detection — no solver is executed here.</p>
                </div>
              </div>

              {caeSummary?.artifact_detection ? (
                <>
                  <div className={`cae-mode-badge ${caeModeClass(caeSummary.artifact_detection.mode)}`}>
                    {caeModeLabel(caeSummary.artifact_detection.mode)}
                  </div>
                  <div className="cae-artifact-grid">
                    {Object.entries(caeSummary.artifact_detection.artifacts).map(([path, present]) => (
                      <div key={path} className={`cae-artifact-item ${present ? "present" : "missing"}`}>
                        <span className="cae-artifact-icon">{present ? "✓" : "✗"}</span>
                        {present && isLowRiskArtifactPath(path) ? (
                          <button
                            type="button"
                            className="cae-artifact-path artifact-link"
                            onClick={() => void viewArtifact(path)}
                            title={`查看 ${path}`}
                          >
                            {path}
                          </button>
                        ) : (
                          <span className="cae-artifact-path">{path}</span>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="cae-artifact-footer">
                    Detected {caeSummary.artifact_detection.detected_count} / {caeSummary.artifact_detection.total_count} artifacts.
                    Solver execution remains in external CAD/CAE software.
                  </div>
                  <div className="action-row" style={{ marginTop: 10 }}>
                    <button
                      disabled={caeRefreshing || !selectedId}
                      onClick={() => void refreshCaeSummary()}
                    >
                      {caeRefreshing ? "正在刷新 CAE 摘要…" : "刷新 CAE 摘要"}
                    </button>
                    <span className="summary-muted" style={{ fontSize: 12 }}>
                      重新生成 .aieng CAE 摘要/证据文件（不执行求解器）
                    </span>
                  </div>
                  <div className="summary-note" style={{ marginTop: 12 }}>
                    <strong>导入外部计算指标</strong>
                    <p style={{ fontSize: 12, margin: "4px 0 8px" }}>
                      从已有的 JSON/CSV 文件导入指标，再刷新 CAE 摘要。不执行求解器。
                    </p>
                    <input
                      type="text"
                      placeholder="C:\path\to\metrics.json or metrics.csv"
                      value={metricsInputPath}
                      onChange={(e) => setMetricsInputPath(e.target.value)}
                      style={{ width: "100%", marginBottom: 6 }}
                    />
                    <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                      <input
                        type="text"
                        placeholder="Load case ID"
                        value={metricsLoadCaseId}
                        onChange={(e) => setMetricsLoadCaseId(e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <input
                        type="text"
                        placeholder="Software (e.g. FreeCAD FEM)"
                        value={metricsSoftware}
                        onChange={(e) => setMetricsSoftware(e.target.value)}
                        style={{ flex: 1 }}
                      />
                    </div>
                    <div className="action-row">
                      <button
                        disabled={metricsImporting || !selectedId}
                        onClick={() => void importMetricsAndRefresh()}
                      >
                        {metricsImporting ? "正在导入并刷新…" : "导入计算指标并刷新摘要"}
                      </button>
                    </div>
                  </div>
                  <div className="summary-note" style={{ marginTop: 12 }}>
                    <strong>从 FRD 文件提取求解器结果</strong>
                    <p style={{ fontSize: 12, margin: "4px 0 8px" }}>
                      解析 CalculiX .frd 文件，提取节点位移和应力场极值（最大位移、最大 von Mises 应力），写入 .aieng 包并刷新结果摘要。不执行求解器。
                    </p>
                    <input
                      type="text"
                      placeholder="C:\path\to\job.frd"
                      value={frdInputPath}
                      onChange={(e) => setFrdInputPath(e.target.value)}
                      style={{ width: "100%", marginBottom: 6 }}
                    />
                    <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                      <input
                        type="text"
                        placeholder="Load case ID"
                        value={frdLoadCaseId}
                        onChange={(e) => setFrdLoadCaseId(e.target.value)}
                        style={{ flex: 1 }}
                      />
                      <input
                        type="text"
                        placeholder="Software (e.g. CalculiX)"
                        value={frdSoftware}
                        onChange={(e) => setFrdSoftware(e.target.value)}
                        style={{ flex: 1 }}
                      />
                    </div>
                    <div className="action-row">
                      <button
                        disabled={frdExtracting || !selectedId}
                        onClick={() => void extractFrdAndRefresh()}
                      >
                        {frdExtracting ? "正在提取并刷新…" : "提取 FRD 结果并刷新摘要"}
                      </button>
                    </div>
                  </div>
                  {caeSummary?.preprocessing_summary ? (
                    <div className="summary-note" style={{ marginTop: 10 }}>
                      <strong>Setup / Pre-processing</strong>
                      <p>{caeSummary.preprocessing_summary.llm_summary.one_line}</p>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", marginTop: 6 }}>
                        <small>Materials: {caeSummary.preprocessing_summary.status.has_materials ? "✓" : "✗"}</small>
                        <small>Loads: {caeSummary.preprocessing_summary.status.has_loads ? "✓" : "✗"}</small>
                        <small>Boundary conditions: {caeSummary.preprocessing_summary.status.has_boundary_conditions ? "✓" : "✗"}</small>
                        <small>Mesh: {caeSummary.preprocessing_summary.status.has_mesh ? "✓" : "✗"}</small>
                        <small>Solver settings: {caeSummary.preprocessing_summary.status.has_solver_settings ? "✓" : "✗"}</small>
                        <small>Ready for solver: <strong>{caeSummary.preprocessing_summary.status.ready_for_solver ? "yes" : "no"}</strong></small>
                      </div>
                      {caeSummary.preprocessing_summary.status.missing_items.length > 0 ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Missing:</strong> {caeSummary.preprocessing_summary.status.missing_items.join(", ")}</small>
                        </div>
                      ) : null}
                      <div style={{ marginTop: 6 }}>
                        <small className="summary-muted">Setup readiness is artifact-based only. No solver execution.</small>
                      </div>
                    </div>
                  ) : null}
                  {caeSummary?.simulation_run_summary ? (
                    <div className="summary-note" style={{ marginTop: 10 }}>
                      <strong>Simulation Runs</strong>
                      <p>{caeSummary.simulation_run_summary.llm_summary.one_line}</p>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 12px", marginTop: 6 }}>
                        <small>Runs recorded: {caeSummary.simulation_run_summary.status.has_simulation_runs ? "yes" : "no"}</small>
                        <small>Run count: {caeSummary.simulation_run_summary.status.run_count}</small>
                        <small>Latest: {caeSummary.simulation_run_summary.status.latest_run_id ?? "none"}</small>
                        <small>Completed: {caeSummary.simulation_run_summary.status.has_completed_run ? "yes" : "no"}</small>
                        <small>Converged: {caeSummary.simulation_run_summary.status.has_converged_run ? "yes" : "no"}</small>
                        <small>Failed: {caeSummary.simulation_run_summary.status.has_failed_run ? "yes" : "no"}</small>
                      </div>
                      {caeSummary.simulation_run_summary.runs.length > 0 ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Latest run:</strong> {caeSummary.simulation_run_summary.runs[0].solver} / {caeSummary.simulation_run_summary.runs[0].software} — {caeSummary.simulation_run_summary.runs[0].analysis_type} — {caeSummary.simulation_run_summary.runs[0].state}</small>
                        </div>
                      ) : null}
                      {caeSummary.simulation_run_summary.status.warnings.length > 0 ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Warnings:</strong> {caeSummary.simulation_run_summary.status.warnings.length}</small>
                        </div>
                      ) : null}
                      <div style={{ marginTop: 6 }}>
                        <small className="summary-muted">Simulation run status is metadata-based only. Solver execution remains external.</small>
                      </div>
                    </div>
                  ) : null}
                  {caeSummary?.result_summary ? (
                    <div className="summary-note" style={{ marginTop: 10 }}>
                      <strong>Results / Post-processing</strong>
                      <p>{caeSummary.result_summary.llm_summary.one_line}</p>
                      {caeSummary.result_summary.source.solver !== "external_or_unknown" ? (
                        <small>Solver: {caeSummary.result_summary.source.solver}</small>
                      ) : null}
                      {caeSummary.result_summary.source.software ? (
                        <small> | Software: {caeSummary.result_summary.source.software}</small>
                      ) : null}
                      {caeSummary.result_summary.load_cases.length > 0 ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Load cases ({caeSummary.result_summary.load_cases.length}):</strong></small>
                          <ul style={{ margin: "4px 0", paddingLeft: 16 }}>
                            {caeSummary.result_summary.load_cases.map((lc) => (
                              <li key={lc.id}>
                                <small>{lc.name} ({lc.type}){lc.magnitude != null ? ` — ${lc.magnitude}${lc.unit ? ` ${lc.unit}` : ""}` : ""}</small>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                      {caeSummary.result_summary.solver_settings ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Solver settings:</strong> {caeSummary.result_summary.solver_settings.solver_type ?? "unknown"}{caeSummary.result_summary.solver_settings.analysis_type ? ` / ${caeSummary.result_summary.solver_settings.analysis_type}` : ""}</small>
                        </div>
                      ) : null}
                      {caeSummary.result_summary.field_metadata && caeSummary.result_summary.field_metadata.count > 0 ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Field metadata:</strong> {caeSummary.result_summary.field_metadata.count} field(s) registered{caeSummary.result_summary.field_metadata.format ? ` (${caeSummary.result_summary.field_metadata.format})` : ""}</small>
                        </div>
                      ) : null}
                      {caeSummary.result_summary.computed_values.extrema_computed ? (
                        <div style={{ marginTop: 6 }}>
                          <small><strong>Imported computed metrics</strong>{caeSummary.result_summary.computed_values.computed_by ? ` — ${caeSummary.result_summary.computed_values.computed_by}` : ""}</small>
                          <div style={{ marginTop: 2 }}>
                            {caeSummary.result_summary.computed_values.max_von_mises_stress ? (
                              <small>σ_max: {caeSummary.result_summary.computed_values.max_von_mises_stress.value} {caeSummary.result_summary.computed_values.max_von_mises_stress.unit || ""} | </small>
                            ) : null}
                            {caeSummary.result_summary.computed_values.max_displacement ? (
                              <small>U_max: {caeSummary.result_summary.computed_values.max_displacement.value} {caeSummary.result_summary.computed_values.max_displacement.unit || ""} | </small>
                            ) : null}
                            {caeSummary.result_summary.computed_values.minimum_safety_factor ? (
                              <small>SF_min: {caeSummary.result_summary.computed_values.minimum_safety_factor.value}</small>
                            ) : null}
                          </div>
                        </div>
                      ) : null}
                      {caeSummary.result_summary.llm_summary.limitations.length ? (
                        <div style={{ marginTop: 6 }}>
                          <small>
                            Limitations: {caeSummary.result_summary.llm_summary.limitations.join(" ")}
                          </small>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="summary-note summary-muted">
                  <strong>Artifact detector unavailable</strong>
                  <p>Install or configure aieng to enable CAE artifact scanning.</p>
                </div>
              )}

              {hasCaeContext ? (
                <>
                  <div className="cae-overview-grid" style={{ marginTop: 14 }}>
                    <div><span>约束</span><strong>{caeSummary?.constraints_count ?? 0}</strong></div>
                    <div><span>载荷</span><strong>{caeSummary?.loads_count ?? 0}</strong></div>
                    <div><span>边界条件</span><strong>{caeSummary?.boundary_conditions_count ?? 0}</strong></div>
                    <div><span>结果证据</span><strong>{caeSummary?.result_evidence_count ?? 0}</strong></div>
                  </div>

                  <div className={caeSummary?.results_available ? "summary-note summary-primary" : "summary-note summary-muted"}>
                    <strong>{caeSummary?.results_available ? "已检测到 CAE 结果证据" : "仅检测到 CAE 上下文"}</strong>
                    <p>
                      {caeSummary?.results_available
                        ? "当前项目包含可用于后续 CAE 可视层的结果证据。此版先把字段、证据和约束整理进 UI，便于继续接入真正的 field renderer。"
                        : "当前项目包含分析目标、约束或外部 CAE 交接信息，但还没有可渲染的求解结果。UI 会优雅降级，不阻断现有 CAD 预览。"}
                    </p>
                  </div>

                  {caeFields.length ? (
                    <div className="cae-field-shell">
                      <div className="cae-field-head">
                        <div>
                          <strong>Scalar Field</strong>
                          <span>{caeSummary?.results_available ? "结果层已具备接线位" : "结果层等待外部求解写回"}</span>
                        </div>
                        <select value={selectedCaeField} onChange={(event) => setSelectedCaeField(event.target.value)}>
                          {caeFields.map((field) => (
                            <option key={field} value={field}>
                              {fieldLabel(field)}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className={caeSummary?.results_available ? "cae-legend ready" : "cae-legend pending"} />
                      <div className="cae-legend-scale">
                        <span>{fieldDescriptor ? `${fieldDescriptor.min_value} ${fieldDescriptor.unit ?? ""}`.trim() : "Low"}</span>
                        <strong>{fieldLabel(selectedCaeField)}</strong>
                        <span>{fieldDescriptor ? `${fieldDescriptor.max_value} ${fieldDescriptor.unit ?? ""}`.trim() : "High"}</span>
                      </div>
                    </div>
                  ) : null}

                  {caeSummary?.simulation_targets?.length ? (
                    <div className="cae-section-block">
                      <strong>Simulation Targets</strong>
                      <div className="cae-chip-list">
                        {caeSummary.simulation_targets.map((target, index) => (
                          <div key={`${String(target.id ?? index)}`} className="cae-chip-card">
                            <span>{String(target.metric ?? target.target ?? "simulation_target")}</span>
                            <strong>
                              {String(target.operator ?? "")}
                              {target.value != null ? ` ${String(target.value)}` : ""}
                            </strong>
                            <small>{String(target.reason ?? "")}</small>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {caeSummary?.protected_regions?.length ? (
                    <div className="cae-section-block">
                      <strong>Fixtures / Protected Regions</strong>
                      <div className="cae-list">
                        {caeSummary.protected_regions.map((item, index) => (
                          <div key={`${String(item.id ?? index)}`} className="cae-list-item">
                            <span>{String(item.target ?? item.id ?? "protected_region")}</span>
                            <strong>{String(item.type ?? "constraint")}</strong>
                            <small>{String(item.reason ?? "")}</small>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {caeSummary?.loads?.length ? (
                    <div className="cae-section-block">
                      <strong>Loads</strong>
                      <div className="cae-list">
                        {caeSummary.loads.map((item, index) => (
                          <div key={`${String((item as Record<string, unknown>).id ?? index)}`} className="cae-list-item compact">
                            <span>{formatRecordSummary(item as Record<string, unknown>) || `load_${index + 1}`}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {caeSummary?.boundary_conditions?.length ? (
                    <div className="cae-section-block">
                      <strong>Boundary Conditions</strong>
                      <div className="cae-list">
                        {caeSummary.boundary_conditions.map((item, index) => (
                          <div key={`${String((item as Record<string, unknown>).id ?? index)}`} className="cae-list-item compact">
                            <span>{formatRecordSummary(item as Record<string, unknown>) || `bc_${index + 1}`}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {caeSummary?.evidence?.length ? (
                    <div className="cae-section-block">
                      <strong>Evidence Ledger</strong>
                      <div className="cae-list">
                        {caeSummary.evidence.map((item, index) => {
                          const record = item as Record<string, unknown>;
                          return (
                            <div key={`${String(record.evidence_id ?? index)}`} className="cae-list-item">
                              <span>{String(record.evidence_type ?? "evidence")}</span>
                              <strong>{String(record.verification_status ?? "unknown")}</strong>
                              <small>{String(record.artifact_path ?? record.notes ?? "")}</small>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </>
              ) : null}
            </section>
          ) : (
            <section className="card">
              <div className="section-heading">
                <div>
                  <h2>CAE Artifact Status</h2>
                  <p>选择或创建项目后，这里会显示 CAE 证据、约束、载荷和结果摘要。</p>
                </div>
              </div>
              <div className="summary-note summary-muted">
                <strong>暂无项目上下文</strong>
                <p>当前没有选中项目，CAE 面板暂时没有可审计资源。</p>
              </div>
            </section>
          )}

          {selectedId ? (
            <section className="card">
              <div className="section-heading">
                <div>
                  <h2>Artifact Inspector</h2>
                  <p>Read-only inspection of .aieng package artifacts without opening ZIP files manually.</p>
                </div>
              </div>

              <div className="action-row" style={{ gap: 8 }}>
                <input
                  type="text"
                  placeholder="e.g. results/computed_metrics.json"
                  value={artifactViewerPath}
                  onChange={(e) => setArtifactViewerPath(e.target.value)}
                  style={{ flex: 1 }}
                />
                <button
                  disabled={artifactViewerBusy || !artifactViewerPath.trim()}
                  onClick={() => void viewArtifact(artifactViewerPath)}
                >
                  {artifactViewerBusy ? "加载中…" : "查看"}
                </button>
              </div>

              {artifactViewerData ? (
                <div style={{ marginTop: 10 }}>
                  {!artifactViewerData.exists ? (
                    <div className="summary-note summary-muted">
                      <strong>Artifact not found</strong>
                      <p>Path: {artifactViewerData.path}</p>
                    </div>
                  ) : (
                    <>
                      <div className="capability-facts" style={{ marginBottom: 8 }}>
                        <div><span>Path</span><strong>{artifactViewerData.path}</strong></div>
                        <div><span>Type</span><strong>{artifactViewerData.media_type}</strong></div>
                        <div><span>Size</span><strong>{artifactViewerData.size_bytes != null ? `${artifactViewerData.size_bytes} bytes` : "-"}</strong></div>
                      </div>
                      {artifactViewerData.warnings.length > 0 ? (
                        <div className="side-effect-list" style={{ marginBottom: 8 }}>
                          {artifactViewerData.warnings.map((w) => (
                            <span key={w}>{w}</span>
                          ))}
                        </div>
                      ) : null}
                      {artifactViewerData.parsed_json != null ? (
                        <details className="fold-block" open>
                          <summary className="fold-summary">Parsed JSON</summary>
                          <pre className="json-block">{JSON.stringify(artifactViewerData.parsed_json, null, 2)}</pre>
                        </details>
                      ) : artifactViewerData.text != null ? (
                        <details className="fold-block" open>
                          <summary className="fold-summary">Text content</summary>
                          <pre className="json-block">{artifactViewerData.text}</pre>
                        </details>
                      ) : (
                        <div className="summary-note summary-muted">
                          <strong>Binary or unreadable content</strong>
                          <p>This artifact exists but is not displayable as JSON or text.</p>
                        </div>
                      )}
                    </>
                  )}
                </div>
              ) : null}
            </section>
          ) : null}
            </>
          ) : null}

          {controlPaneMode === "chat" ? (
          <section className="card">
            <div className="section-heading">
              <div>
                <h2>LLM Agent Console</h2>
                <p>先用 LLM 生成可审阅计划，再由白名单工具、MCP preflight 和审批闸门执行。这里现在是默认主入口。</p>
              </div>
            </div>

            <div className="agent-chat-strip">
              <div>
                <strong>{agentPlan ? `当前计划：${agentPlan.steps.length} steps / ${agentPlan.mode}` : "LLM 驱动的建模入口"}</strong>
                <span>
                  {agentPlan
                    ? agentPlan.preview.warnings[0] || (agentPlan.requires_approval ? "包含审批步骤" : "当前计划不需要审批")
                    : llmReady
                      ? "可以直接描述建模目标，系统会优先走 LLM planner。"
                      : "Provider 未配完整时仍可运行，但会退化到本地启发式 planner。"}
                </span>
              </div>
              <div className="capability-facts llm-console-facts">
                <div><span>Provider</span><strong>{getLlmProviderLabel(llmConfig.provider)}</strong></div>
                <div><span>Model</span><strong>{llmConfig.model}</strong></div>
                <div><span>Project</span><strong>{selectedId ?? "-"}</strong></div>
                <div><span>Route</span><strong>{llmReady ? "LLM-first" : "heuristic fallback"}</strong></div>
              </div>
              <div className="agent-chat-actions">
                <button disabled={agentBusy} onClick={() => void planAgentChat()}>
                  生成 LLM 计划
                </button>
                <button className="ghost-button" disabled={agentBusy} onClick={() => void runAgentChat()}>
                  执行 LLM 计划
                </button>
              </div>
            </div>

            {agentPlan ? (
              <div className="agent-plan-preview">
                <div className="capability-facts">
                  <div><span>Mode</span><strong>{agentPlan.mode}</strong></div>
                  <div><span>Steps</span><strong>{agentPlan.preview.step_count}</strong></div>
                  <div><span>Approval</span><strong>{agentPlan.requires_approval ? "required" : "not required"}</strong></div>
                  <div><span>Project</span><strong>{agentPlan.project_id ?? "-"}</strong></div>
                </div>
                <div className="workflow-step-list compact-list">
                  {agentPlan.steps.map((step) => (
                    <div key={step.id} className="workflow-step-item">
                      <span>{workflowStepLabel(step.kind)}</span>
                      <strong>{step.tool_name ?? step.id}</strong>
                      {step.approval_required ? <small>approval</small> : null}
                    </div>
                  ))}
                </div>
                {agentPlan.preview.warnings.length || agentPlan.errors.length ? (
                  <div className="side-effect-list">
                    {[...agentPlan.preview.warnings, ...agentPlan.errors].map((warning) => (
                      <span key={warning}>{warning}</span>
                    ))}
                  </div>
                ) : null}
                <JsonDisclosure title="查看 Agent plan payload" body={jsonBlock(agentPlan)} />
              </div>
            ) : null}

            <div className="chat-suggestion-row">
              {CHAT_SUGGESTIONS.map((suggestion) => (
                <button key={suggestion} type="button" className="ghost-button chat-suggestion" onClick={() => setMessage(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>

            <div className="chat-window" ref={chatLogRef}>
              {chatHistory.length ? (
                chatHistory.map((entry) => (
                  <article key={entry.id} className={entry.role === "assistant" ? "chat-bubble assistant" : "chat-bubble user"}>
                    <header>
                      <strong>{entry.role === "assistant" ? "Workbench" : "You"}</strong>
                      <span>{entry.mode === "execute" ? "执行" : entry.mode === "plan" ? "计划" : entry.mode === "runtime" ? "运行时" : ""}</span>
                    </header>
                    <p>{entry.body}</p>
                    {entry.plan?.length ? (
                      <div className="chat-plan-list">
                        {entry.plan.map((step, index) => (
                          <div key={`${step.tool}-${index}`} className={`chat-plan-item status-${step.status === "failed" ? "error" : step.status === "done" ? "done" : "active"}`}>
                            <strong>{step.tool}</strong>
                            <span>{step.description}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {entry.errors?.length ? (
                      <div className="chat-error-list">
                        {entry.errors.map((error, index) => (
                          <small key={`${entry.id}-error-${index}`}>{error}</small>
                        ))}
                      </div>
                    ) : null}
                    {entry.artifactDiffs?.length ? (
                      <div className="chat-artifact-diffs">
                        <small>变更差异:</small>
                        {entry.artifactDiffs.map((diff, idx) => (
                          <div key={`${diff.path}-${idx}`} className="chat-diff-item">
                            <div className="chat-diff-header">
                              {isLowRiskArtifactPath(diff.path) ? (
                                <button
                                  type="button"
                                  className="artifact-link"
                                  onClick={() => void viewArtifact(diff.path)}
                                  title={`查看 ${diff.path}`}
                                >
                                  {diff.path}
                                </button>
                              ) : (
                                <span>{diff.path}</span>
                              )}
                              <span className="chat-diff-op">{diff.operation}</span>
                              {diff.json_pointer ? <span className="chat-diff-pointer">{diff.json_pointer}</span> : null}
                            </div>
                            {diff.changed_paths.length > 0 || diff.added_paths.length > 0 || diff.removed_paths.length > 0 ? (
                              <div className="chat-diff-paths">
                                {diff.changed_paths.length > 0 ? <small>changed: {diff.changed_paths.join(", ")}</small> : null}
                                {diff.added_paths.length > 0 ? <small>added: {diff.added_paths.join(", ")}</small> : null}
                                {diff.removed_paths.length > 0 ? <small>removed: {diff.removed_paths.join(", ")}</small> : null}
                              </div>
                            ) : null}
                            {(diff.before !== null || diff.after !== null) && JSON.stringify(diff.before).length < 200 && JSON.stringify(diff.after).length < 200 ? (
                              <div className="chat-diff-values">
                                <pre className="chat-diff-before">{JSON.stringify(diff.before, null, 2)}</pre>
                                <pre className="chat-diff-after">{JSON.stringify(diff.after, null, 2)}</pre>
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {entry.artifactPaths?.length ? (
                      <div className="chat-artifact-links">
                        <small>变更证据:</small>
                        {entry.artifactPaths.filter(isLowRiskArtifactPath).map((path) => (
                          <button
                            key={path}
                            type="button"
                            className="ghost-button chat-artifact-link"
                            onClick={() => void viewArtifact(path)}
                            title={`查看 ${path}`}
                          >
                            {path}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {entry.auditLogUrl ? (
                      <a className="chat-audit-link" href={entry.auditLogUrl} target="_blank" rel="noreferrer">
                        查看审计日志
                      </a>
                    ) : null}
                  </article>
                ))
              ) : (
                <div className="summary-note summary-muted chat-empty-state">
                  <strong>聊天窗已就绪</strong>
                  <p>这里会保留你的请求、编排回复、步骤状态和审计入口，不再只显示一次性的 plan 结果。</p>
                </div>
              )}
            </div>

            <textarea rows={4} value={message} onChange={(event) => setMessage(event.target.value)} placeholder="例如：总结当前模型并给出下一步可执行的安全操作。" />
            <details className="fold-block legacy-chat-panel">
              <summary className="fold-summary">兼容 / 调试路径</summary>
              <div className="legacy-chat-actions">
                <button disabled={!selectedId || busy} className="ghost-button" onClick={() => void submitChat("plan")}>
                  旧版规则计划
                </button>
                <button disabled={!selectedId || busy} className="ghost-button" onClick={() => void submitChat("execute")}>
                  旧版安全执行
                </button>
                <button disabled={busy} className="ghost-button" onClick={() => void submitRuntime()}>
                  直接 runtime
                </button>
              </div>
            </details>

            {lastRuntimeRun?.status === "awaiting_approval" ? (
              <div className="action-row approval-action-row">
                <span className="approval-label">
                  等待审批 — {lastRuntimeRun.plan[lastRuntimeRun.pending_step_index ?? 0]?.name ?? "tool"}
                </span>
                <button disabled={busy} onClick={() => void approveRun()}>
                  批准执行
                </button>
                <button disabled={busy} className="ghost-button" onClick={() => void rejectRun()}>
                  拒绝
                </button>
              </div>
            ) : null}

            {chat ? (
              <>
                <div className="chat-meta">
                  <span>计划步骤 {chat.plan.length}</span>
                  <span>审计 ID {chat.audit_id}</span>
                  <span>{chat.executed ? "已执行" : "仅计划"}</span>
                </div>
                <JsonDisclosure title="查看原始计划与执行输出" body={jsonBlock(chat)} />
              </>
            ) : null}
          </section>
          ) : null}
        </aside>
      </div>

      <RuntimeSettingsDrawer
        open={settingsOpen}
        runtime={runtime}
        runtimeDraft={runtimeDraft}
        runtimeBusy={runtimeBusy}
        runtimeNotice={runtimeNotice}
        runtimeProvider={runtimeProvider}
        runtimeReady={runtimeReady}
        llmConfig={llmConfig}
        llmReady={llmReady}
        onClose={() => setSettingsOpen(false)}
        onDraftChange={updateRuntimeDraft}
        onLlmChange={updateLlmConfig}
        onLlmPreset={applyLlmProviderPreset}
        onLlmRestore={restoreDefaultLlmConfig}
        onTest={() => void runRuntimeTask("test", () => api.testRuntimeConfig(runtimeDraft!))}
        onSave={() => void runRuntimeTask("save", () => api.updateRuntimeConfig(runtimeDraft!))}
        onRestore={restoreRuntimeDefaults}
      />
    </>
  );
}
