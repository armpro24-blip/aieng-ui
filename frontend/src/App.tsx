import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

import { api } from "./api";
import type { ChatResponse, ProjectRecord, ProjectSummary } from "./types";

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

export default function App() {
  const [runtime, setRuntime] = useState<Record<string, unknown> | null>(null);
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

  function buildFallbackSummary(project: ProjectRecord): ProjectSummary {
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
      integration: runtime ?? undefined,
    };
  }

  async function refreshProjects(nextSelectedId?: string | null) {
    const list = await api.listProjects();
    setProjects(list);
    const candidate = nextSelectedId ?? selectedId ?? list[0]?.id ?? null;
    setSelectedId(candidate);
    if (candidate) {
      try {
        setSummary(await api.getProject(candidate));
      } catch {
        const project = list.find((item) => item.id === candidate) ?? null;
        setSummary(project ? buildFallbackSummary(project) : null);
      }
    } else {
      setSummary(null);
    }
  }

  useEffect(() => {
    void (async () => {
      setRuntime(await api.runtime());
      await refreshProjects();
    })();
  }, []);

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
    {
      title: "集成信息",
      body: jsonBlock({ integration: summary?.integration ?? null, members: summary?.members ?? [], viewer: (summary as any)?.viewer ?? null }),
    },
  ];

  const aiSummary = (summary as any)?.ai_summary as string | undefined;

  return (
    <div className="app-shell workbench-shell">
      <section className="viewer-pane">
        <div className="viewer-header">
          <div>
            <h1>aieng-platform Workbench</h1>
            <p>用户自由选择 STEP 文件，Web 页面作为工作台，围绕导入、预览、语义查看和后续操作组织整个流转。</p>
          </div>
          <div className="runtime-pill">
            {runtime?.freecad_cmd_exists ? "FreeCAD 预览链已就绪" : "当前处于预览降级模式"}
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
            <strong>{(summary as any)?.validation?.report_ok === true ? "通过" : (summary as any)?.validation?.report_ok === false ? "失败" : "待刷新"}</strong>
          </div>
        </div>

        <div className="viewer-stage-shell">
          <div className="viewer-stage-head">
            <div>
              <strong>模型区</strong>
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
              <h2>STEP 导入入口</h2>
              <p>明显、直接、可用的工作台入口：选文件后即可触发完整导入流。</p>
            </div>
          </div>

          <div className="inline-form">
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="新项目名称（可选）" />
            <button disabled={busy} onClick={() => void runBusyTask(async () => {
              const created = await api.createProject(projectName);
              await refreshProjects(created.id);
              setNotice({ tone: "success", title: "项目已创建", detail: `已创建项目 ${created.name}。` });
            })}>新建项目</button>
            <button disabled={busy} onClick={() => void runBusyTask(async () => {
              const sample = await api.createSampleProject();
              await refreshProjects(sample.id);
              setNotice({ tone: "success", title: "示例已载入", detail: "已把 SFA-5.41 示例接入工作台。" });
            })}>载入示例</button>
          </div>

          <label className="dropzone">
            <input className="dropzone-input" type="file" accept=".step,.stp" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
            <div className="dropzone-content">
              <strong>{selectedFile ? selectedFile.name : "选择 STEP 文件"}</strong>
              <span>{selectedFile ? "文件已就绪，可直接导入当前工作台。" : "支持 .step / .stp，若当前未选项目，会自动创建项目后继续。"}</span>
            </div>
          </label>

          <div className="action-row primary-actions">
            <button disabled={busy || !selectedFile} onClick={() => void runWorkbenchImportFlow()}>上传并导入到工作台</button>
            <button disabled={busy || !selectedId} onClick={() => selectedId && void runProjectAction("semantic", () => api.getProject(selectedId), "工作台已刷新", "已刷新当前项目的预览和语义状态。")}>刷新工作台</button>
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

          {notice && (
            <div className={`result-banner result-${notice.tone}`}>
              <strong>{notice.title}</strong>
              <span>{notice.detail}</span>
            </div>
          )}
        </section>

        <section className="card">
          <div className="section-heading">
            <div>
              <h2>项目区</h2>
              <p>围绕导入后的操作流组织当前项目与最近项目。</p>
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
              <h2>操作区</h2>
              <p>围绕导入后的真实后端能力组织手动重跑与验证。</p>
            </div>
          </div>

          <div className="action-grid">
            <button disabled={!selectedId || busy} onClick={() => selectedId && void runProjectAction("import", () => api.importAieng(selectedId), "重新导入成功", "已重新生成当前项目的 .aieng 包并补全语义资源。")}>重新导入 aieng</button>
            <button disabled={!selectedId || busy} onClick={() => selectedId && void runProjectAction("preview", () => api.convert(selectedId), "预览已更新", "已重跑 STEP 预览链并刷新模型资产。")}>重新生成预览</button>
            <button disabled={!selectedId || busy} onClick={() => selectedId && void runProjectAction("semantic", () => api.validate(selectedId), "校验已完成", "已执行后端校验并刷新语义信息。")}>校验语义信息</button>
            <button disabled={!selectedId || busy} onClick={() => selectedId && void runProjectAction("semantic", () => api.getProject(selectedId), "摘要已刷新", "已刷新当前项目的 manifest、topology 和 validation。")}>刷新项目摘要</button>
          </div>
        </section>

        <section className="card">
          <div className="section-heading">
            <div>
              <h2>聊天区 / 编排区</h2>
              <p>自然语言工作流直接放进工作台，不再只是调试 JSON 面板。</p>
            </div>
          </div>

          <textarea rows={5} value={message} onChange={(event) => setMessage(event.target.value)} />
          <div className="action-row">
            <button disabled={!selectedId || busy} onClick={() => selectedId && void runBusyTask(async () => {
              const result = await api.chat(selectedId, message, false);
              setChat(result);
              setNotice({ tone: "info", title: "已生成计划", detail: "可在下方查看 orchestrator 给出的安全步骤。" });
            })}>生成计划</button>
            <button disabled={!selectedId || busy} onClick={() => selectedId && void runBusyTask(async () => {
              const result = await api.chat(selectedId, message, true);
              setChat(result);
              await refreshProjects(selectedId);
              setNotice({ tone: "success", title: "已执行安全步骤", detail: "工作台已根据自然语言请求执行可用后端步骤。" });
            })}>执行安全步骤</button>
          </div>
          <pre className="json-block">{jsonBlock(chat)}</pre>
        </section>

        <section className="card">
          <div className="section-heading">
            <div>
              <h2>语义区</h2>
              <p>导入后的语义信息集中展示，便于继续操作和核对。</p>
            </div>
          </div>

          <div className="semantic-overview">
            <div><span>模型 ID</span><strong>{getManifestString(summary, "model_id")}</strong></div>
            <div><span>资源成员</span><strong>{summary?.members?.length ?? 0}</strong></div>
            <div><span>特征数</span><strong>{getDerivedNumber(summary, "feature_graph", "count")}</strong></div>
            <div><span>拓扑数</span><strong>{getDerivedNumber(summary, "topology", "count")}</strong></div>
          </div>

          {summary?.summary_error ? (
            <div className="summary-note">
              <strong>语义摘要已降级</strong>
              <p>{summary.summary_error}</p>
            </div>
          ) : null}

          {aiSummary ? <div className="summary-note"><strong>AI 摘要</strong><p>{aiSummary}</p></div> : null}

          {semanticSections.map((section) => (
            <div key={section.title} className="semantic-block">
              <h3>{section.title}</h3>
              <pre className="json-block">{section.body}</pre>
            </div>
          ))}
        </section>
      </aside>
    </div>
  );
}
