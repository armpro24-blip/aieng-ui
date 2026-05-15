import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import { api } from "./api";
import type { ChatResponse, ProjectRecord, ProjectSummary, RuntimeConfig, RuntimeConfigSnapshot } from "./types";

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

type ViewerLoadState = "idle" | "loading" | "ready" | "error";

const BASE_STAGES: StageItem[] = [
  { key: "upload", label: "上传 STEP", detail: "把用户选择的 STEP 文件放入项目", state: "idle" },
  { key: "import", label: "导入 aieng", detail: "生成 .aieng 包并自动补全 topology、AAG、feature 和摘要", state: "idle" },
  { key: "preview", label: "生成预览", detail: "调用 FreeCADCmd 预览链并优先产出 GLB", state: "idle" },
  { key: "semantic", label: "刷新语义信息", detail: "同步 manifest、topology、validation 和摘要", state: "idle" },
];

const CAD_PROVIDER_OPTIONS = [{ value: "freecad", label: "FreeCAD" }] as const;

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

function getRuntimeDetail(snapshot: RuntimeConfigSnapshot | null) {
  if (!snapshot) return "正在读取 CAD 运行时配置";
  if (snapshot.probe.ready) {
    return `${getProviderLabel(snapshot.config.provider)} / topology=${snapshot.probe.topology_backend_resolved}`;
  }
  return snapshot.probe.issues.join("；") || snapshot.probe.bridge_error || "运行时检测未通过";
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

function ModelViewer({ assetUrl, assetFormat }: { assetUrl?: string | null; assetFormat?: string | null }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [viewerState, setViewerState] = useState<{ status: ViewerLoadState; detail: string }>({
    status: "idle",
    detail: "等待生成预览资产",
  });

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
      scene.add(nextObject);
      if (!fitCameraToObject(camera, controls, nextObject)) {
        setSafeViewerState("error", "预览资产缺少可用的几何边界，无法定位相机");
        return;
      }
      setSafeViewerState("ready", "真实预览资产已加载");
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
  }, [assetFormat, assetUrl]);

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

type RuntimeSettingsDrawerProps = {
  open: boolean;
  runtime: RuntimeConfigSnapshot | null;
  runtimeDraft: RuntimeConfig | null;
  runtimeBusy: boolean;
  runtimeNotice: Notice | null;
  runtimeProvider: string;
  runtimeReady: boolean;
  onClose(): void;
  onDraftChange<K extends keyof RuntimeConfig>(key: K, value: RuntimeConfig[K]): void;
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
  onClose,
  onDraftChange,
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
        aria-label="CAD 配置"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="drawer-header">
          <div>
            <h2>CAD 配置</h2>
            <p>将环境配置收拢到二级设置里，主工作区只保留运行状态与导入主线。</p>
          </div>
          <button type="button" className="ghost-button drawer-close" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="drawer-body">
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
              测试配置
            </button>
            <button disabled={!runtimeDraft || runtimeBusy} onClick={onSave}>
              保存配置
            </button>
            <button disabled={!runtime?.defaults || runtimeBusy} onClick={onRestore}>
              恢复默认
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
  const [message, setMessage] = useState("上传当前 STEP，导入 aieng，生成预览，并刷新语义信息");
  const [chat, setChat] = useState<ChatResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [stages, setStages] = useState<StageItem[]>(BASE_STAGES);

  const selectedProject = useMemo(
    () => projects.find((item) => item.id === selectedId) ?? null,
    [projects, selectedId],
  );
  const fallbackViewerUrl = useMemo(() => projectViewerUrl(selectedProject), [selectedProject]);
  const rawViewerUrl = summary?.viewer_url ?? fallbackViewerUrl;
  const viewerVersion = summary?.project?.updated_at ?? selectedProject?.updated_at ?? null;
  const effectiveViewerUrl = useMemo(() => withAssetVersion(rawViewerUrl, viewerVersion), [rawViewerUrl, viewerVersion]);
  const summaryViewerFormat = typeof summary?.viewer?.asset_format === "string" ? summary.viewer.asset_format : null;
  const effectiveViewerFormat = resolveAssetFormat(rawViewerUrl, summaryViewerFormat ?? selectedProject?.web_asset_format ?? null);

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
    void (async () => {
      const runtimeSnapshot = await api.runtime();
      setRuntime(runtimeSnapshot);
      setRuntimeDraft(runtimeSnapshot.config);
      await refreshProjects(undefined, runtimeSnapshot);
    })();
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
            <ModelViewer assetUrl={effectiveViewerUrl} assetFormat={effectiveViewerFormat} />
          </div>

          <div className="viewer-insights">
            <div className="insight-card"><span>特征数</span><strong>{getDerivedNumber(summary, "feature_graph", "count")}</strong></div>
            <div className="insight-card"><span>拓扑实体</span><strong>{getDerivedNumber(summary, "topology", "count")}</strong></div>
            <div className="insight-card"><span>资源成员</span><strong>{summary?.members?.length ?? 0}</strong></div>
            <div className="insight-card"><span>最近更新</span><strong>{formatTime(selectedProject?.updated_at)}</strong></div>
          </div>
        </section>

        <aside className="side-pane">
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

            {notice ? (
              <div className={`result-banner result-${notice.tone}`}>
                <strong>{notice.title}</strong>
                <span>{notice.detail}</span>
              </div>
            ) : null}
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

          <section className="card">
            <div className="section-heading">
              <div>
                <h2>智能编排</h2>
                <p>通过自然语言生成安全步骤，必要时再展开查看原始执行结果。</p>
              </div>
            </div>

            <textarea rows={4} value={message} onChange={(event) => setMessage(event.target.value)} />
            <div className="action-row">
              <button
                disabled={!selectedId || busy}
                onClick={() =>
                  selectedId &&
                  void runBusyTask(async () => {
                    const result = await api.chat(selectedId, message, false);
                    setChat(result);
                    setNotice({ tone: "info", title: "已生成计划", detail: "可在下方查看 orchestrator 给出的安全步骤。" });
                  })
                }
              >
                生成计划
              </button>
              <button
                disabled={!selectedId || busy}
                onClick={() =>
                  selectedId &&
                  void runBusyTask(async () => {
                    const result = await api.chat(selectedId, message, true);
                    setChat(result);
                    await refreshProjects(selectedId);
                    setNotice({ tone: "success", title: "已执行安全步骤", detail: "工作台已根据自然语言请求执行可用后端步骤。" });
                  })
                }
              >
                执行安全步骤
              </button>
            </div>

            {chat ? (
              <>
                <div className="summary-note chat-reply">
                  <strong>{chat.executed ? "编排执行结果" : "编排计划已生成"}</strong>
                  <p>{chat.reply}</p>
                </div>
                <div className="chat-meta">
                  <span>计划步骤 {chat.plan.length}</span>
                  <span>审计 ID {chat.audit_id}</span>
                </div>
                <JsonDisclosure title="查看原始计划与执行输出" body={jsonBlock(chat)} />
              </>
            ) : (
              <div className="summary-note summary-muted">
                <strong>智能编排尚未运行</strong>
                <p>选择项目后可先生成计划，再决定是否执行安全步骤。</p>
              </div>
            )}
          </section>
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
        onClose={() => setSettingsOpen(false)}
        onDraftChange={updateRuntimeDraft}
        onTest={() => void runRuntimeTask("test", () => api.testRuntimeConfig(runtimeDraft!))}
        onSave={() => void runRuntimeTask("save", () => api.updateRuntimeConfig(runtimeDraft!))}
        onRestore={restoreRuntimeDefaults}
      />
    </>
  );
}
