import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type Language = "en" | "zh-CN";

const LANGUAGE_STORAGE_KEY = "aieng-ui.language";
const SUPPORTED_LANGUAGES: Language[] = ["en", "zh-CN"];

const zhToEn: Record<string, string> = {
  "已完成": "Completed",
  "等待审批": "Awaiting approval",
  "执行失败": "Failed",
  "已拒绝": "Rejected",
  "已取消": "Cancelled",
  "上传 STEP": "Upload STEP",
  "把用户选择的 STEP 文件放入项目": "Put the selected STEP file into the project",
  "导入 aieng": "Import aieng",
  "生成 .aieng 包并自动补全 topology、AAG、feature 和摘要": "Generate the .aieng package and enrich topology, AAG, features, and summary",
  "生成预览": "Generate preview",
  "调用 FreeCADCmd 预览链并优先产出 GLB": "Run the FreeCADCmd preview pipeline and prefer GLB output",
  "刷新语义信息": "Refresh semantics",
  "同步 manifest、topology、validation 和摘要": "Sync manifest, topology, validation, and summary",
  "总结当前模型的语义状态和主要风险": "Summarize the current model semantics and main risks",
  "检查当前包是否已经具备执行 patch 的前提": "Check whether the current package is ready for a patch",
  "给出减重但不破坏受保护区域的安全步骤": "Give safe weight-reduction steps without changing protected regions",
  "智能编排": "Intelligent orchestration",
  "对话、计划与审批": "Chat, planning, and approval",
  "项目数据": "Project data",
  "导入与语义摘要": "Import and semantic summary",
  "能力中心": "Capability center",
  "工具、工作流与评测": "Tools, workflows, and benchmarks",
  "CAE 证据": "CAE evidence",
  "仿真设置与结果": "Simulation setup and results",
  "模型 Provider 规划，本地 runtime 执行。": "Model provider planning with local runtime execution.",
  "无 API key 的本地规则编排。": "Local rule orchestration without an API key.",
  "桥接 guardrail、patch 解析和 preflight。": "Bridges guardrails, patch parsing, and preflight.",
  "通过 FreeCADCmd 做几何检查和受控 CAD 动作。": "Runs geometry checks and controlled CAD actions through FreeCADCmd.",
  "正在读取 CAD 运行时配置": "Reading CAD runtime configuration",
  "运行时检测未通过": "Runtime check did not pass",
  "已执行编排请求。": "Orchestration request executed.",
  "已生成编排计划。": "Orchestration plan generated.",
  "几何检查失败": "Geometry inspection failed",
  "几何检查完成": "Geometry inspection complete",
  "外形尺寸": "dimensions",
  "体积": "volume",
  "个实体": " solids",
  "个面": " faces",
  "变更文件": "Changed files",
  "等待生成预览资产": "Waiting for preview asset",
  "预览资产缺少可用的几何边界，无法定位相机": "The preview asset has no usable geometry bounds, so the camera cannot be positioned",
  "FRD数据存在，但几何坐标可能不一致": "FRD data exists, but geometry coordinates may not match",
  "FRD真实数据": "real FRD data",
  "合成预览，不可用于工程判断": "synthetic preview, not for engineering decisions",
  "真实预览资产已加载": "Real preview asset loaded",
  "警告：FRD 坐标与几何不匹配": "Warning: FRD coordinates do not match the geometry",
  "正在加载": "Loading",
  "预览资产": "preview asset",
  "GLB 预览资产加载失败": "Failed to load GLB preview asset",
  "STL 预览资产加载失败": "Failed to load STL preview asset",
  "预览资产格式无法识别": "Preview asset format is not recognized",
  "预览加载失败": "Preview failed to load",
  "正在加载真实模型": "Loading real model",
  "等待预览资产": "Waiting for preview asset",
  "配置可用 ✓": "Configuration is usable ✓",
  "配置不可用": "Configuration is not usable",
  "连接已验证 ✓": "Connection verified ✓",
  "连接验证失败": "Connection verification failed",
  "Agent plan、workflow 和 benchmark 共用这份模型配置。": "Agent plans, workflows, and benchmarks share this model configuration.",
  "已配置": "Configured",
  "待配置": "Needs configuration",
  "恢复 LLM 默认": "Restore LLM defaults",
  "检测中...": "Checking...",
  "测试配置": "Test configuration",
  "验证中...": "Verifying...",
  "验证连接": "Verify connection",
  "环境设置": "Environment settings",
  "全局设置": "Global settings",
  "全局": "Global",
  "管理工作台偏好和界面显示。环境配置仍保留在专用抽屉中。": "Manage workbench preferences and interface display. Environment configuration stays in its dedicated drawer.",
  "偏好设置": "Preferences",
  "语言": "Language",
  "选择工作台界面的显示语言。": "Choose the display language for the workbench interface.",
  "当前语言": "Current language",
  "打开全局设置": "Open global settings",
  "界面设置": "Interface settings",
  "集中管理 LLM Provider 和 CAD Runtime。主工作区只显示当前状态与常用操作。": "Manage the LLM provider and CAD runtime in one place. The main workspace only shows current status and common actions.",
  "关闭": "Close",
  "STEP 导入、预览、语义刷新和 FreeCAD MCP 能力使用这组配置。": "STEP import, preview, semantic refresh, and FreeCAD MCP capabilities use this configuration.",
  "FreeCAD 安装目录": "FreeCAD installation directory",
  "aieng-freecad-mcp 仓库目录": "aieng-freecad-mcp repository directory",
  "aieng 仓库目录": "aieng repository directory",
  "测试 CAD 配置": "Test CAD configuration",
  "保存 CAD 配置": "Save CAD configuration",
  "恢复 CAD 默认": "Restore CAD defaults",
  "当前 Provider": "Current provider",
  "运行时状态": "Runtime status",
  "已就绪": "Ready",
  "拓扑后端": "Topology backend",
  "已找到": "Found",
  "未找到": "Not found",
  "检测问题": "Detected issues",
  "Bridge 探测": "Bridge probe",
  "探测失败": "probe failed",
  "探测": "probe",
  "STEP 工作台项目": "STEP workbench project",
  "检查当前项目状态，生成一份可审阅的工程执行计划。": "Check the current project status and generate a reviewable engineering execution plan.",
  "已恢复默认值": "Defaults restored",
  "表单已回填默认 CAD 配置，保存后才会生效。": "The form has been filled with default CAD settings. Save to apply them.",
  "CAD 配置已保存": "CAD configuration saved",
  "CAD 配置已测试": "CAD configuration tested",
  "CAD 配置操作失败": "CAD configuration action failed",
  "操作失败": "Operation failed",
  "请先选择 STEP 文件": "Select a STEP file first",
  "工作台入口需要一个 .step 或 .stp 文件。": "The workbench entry needs a .step or .stp file.",
  "正在上传": "Uploading",
  "已上传": "uploaded",
  "正在导入并补全 .aieng 语义包": "Importing and enriching the .aieng semantic package",
  "STEP 已导入并补全 topology、AAG、feature 和摘要": "STEP imported and topology, AAG, features, and summary enriched",
  "正在生成 Web 预览资产": "Generating web preview asset",
  "预览资产已生成": "Preview asset generated",
  "正在刷新校验和语义信息": "Refreshing validation and semantic information",
  "工作台语义信息已刷新": "Workbench semantics refreshed",
  "STEP 已接入工作台": "STEP connected to the workbench",
  "已完成上传、导入 aieng、生成预览，并刷新语义信息。": "Upload, aieng import, preview generation, and semantic refresh are complete.",
  "Manifest / 校验": "Manifest / validation",
  "通过": "Passed",
  "失败": "Failed",
  "待刷新": "Pending refresh",
  "Agent 工作台已刷新": "Agent workbench refreshed",
  "能力注册表、工作流和 benchmark 场景已重新读取。": "Capability registry, workflows, and benchmark scenarios have been reloaded.",
  "需要审批": "Approval required",
  "能力预览完成": "Capability preview complete",
  "工作流": "Workflow",
  "LLM 测试通过": "LLM test passed",
  "LLM 测试失败": "LLM test failed",
  "Benchmark dry-run 完成": "Benchmark dry-run complete",
  "Benchmark 运行完成": "Benchmark run complete",
  "Benchmark 运行失败": "Benchmark run failed",
  "请输入请求": "Enter a request",
  "本地运行时需要一条自然语言指令。": "The local runtime needs a natural-language instruction.",
  "本地运行时": "Local runtime",
  "CAE 摘要已刷新": "CAE summary refreshed",
  "已重新生成 CAE 结果摘要、证据索引和 Markdown 文件。": "CAE result summary, evidence index, and Markdown files have been regenerated.",
  "CAE 摘要刷新失败": "Failed to refresh CAE summary",
  "运行时返回非成功状态。": "The runtime returned a non-success status.",
  "请输入指标文件路径": "Enter a metrics file path",
  "需要提供外部 JSON/CSV 指标文件的绝对路径。": "Provide the absolute path to an external JSON/CSV metrics file.",
  "计算指标生成失败": "Failed to generate computed metrics",
  "计算指标已导入并刷新摘要": "Computed metrics imported and summary refreshed",
  "已生成计算指标并重新生成 CAE 结果摘要。": "Computed metrics have been generated and the CAE result summary regenerated.",
  "导入计算指标失败": "Failed to import computed metrics",
  "请输入 FRD 文件路径": "Enter an FRD file path",
  "需要提供 CalculiX .frd 结果文件的绝对路径。": "Provide the absolute path to a CalculiX .frd result file.",
  "FRD 提取失败": "FRD extraction failed",
  "FRD 结果已提取并刷新摘要": "FRD results extracted and summary refreshed",
  "已从 .frd 文件提取最大位移和最大 von Mises 应力，并重新生成 CAE 结果摘要。": "Maximum displacement and maximum von Mises stress were extracted from the .frd file, and the CAE result summary was regenerated.",
  "运行时审批": "Runtime approval",
  "已批准并执行": "Approved and executed",
  "运行时审批 — 已拒绝": "Runtime approval — rejected",
  "已拒绝，待执行工具未运行。": "Rejected. The pending tool was not run.",
  "请输入 Agent 目标": "Enter an Agent goal",
  "Agent 需要一条建模、检查或分析目标。": "Agent needs a modeling, inspection, or analysis goal.",
  "可以先生成计划，也可以直接运行 Agent。": "You can generate a plan first or run Agent directly.",
  "Agent 计划已生成": "Agent plan generated",
  "包含审批闸门": "includes approval gates",
  "无需审批": "no approval required",
  "Agent 计划失败": "Agent planning failed",
  "Agent 运行失败": "Agent run failed",
  "请选择项目": "Select a project",
  "需要当前项目上下文。": "needs the current project context.",
  "请输入编排请求": "Enter an orchestration request",
  "聊天窗需要一条自然语言指令才能生成计划或执行。": "The chat window needs a natural-language instruction to plan or run.",
  "已执行安全步骤": "Safe steps executed",
  "已生成计划": "Plan generated",
  "聊天窗已执行当前请求允许的后端步骤。": "The chat window executed the backend steps allowed by the current request.",
  "聊天窗已生成一组可审阅的受保护步骤。": "The chat window generated a reviewable set of protected steps.",
  "围绕 STEP 导入、模型预览、语义核对和后续编排组织单页工作区，环境配置收拢到页内设置抽屉。": "A single-page workspace for STEP import, model preview, semantic checks, and follow-on orchestration, with environment configuration kept in an in-page settings drawer.",
  "运行时已就绪": "runtime ready",
  "CAD 运行时需配置": "CAD runtime needs configuration",
  "未选择项目": "No project selected",
  "当前 STEP": "Current STEP",
  "未选择文件": "No file selected",
  "模型 ID": "Model ID",
  "校验状态": "Validation status",
  "模型预览": "Model preview",
  "当前预览": "Current preview",
  "导入后将在这里显示模型预览": "The model preview will appear here after import",
  "场可视化": "field visualization",
  "预览可用": "Preview available",
  "等待生成": "Waiting",
  "特征数": "Features",
  "拓扑实体": "Topology entities",
  "资源成员": "Package members",
  "最近更新": "Last updated",
  "环境": "Environment",
  "导入模型": "Import model",
  "从这里进入工作台主流程：选 STEP、导入、生成预览并刷新语义结果。": "Start the main workbench flow here: choose STEP, import, generate preview, and refresh semantic results.",
  "新项目名称（可选）": "New project name (optional)",
  "项目已创建": "Project created",
  "已创建项目": "Created project",
  "新建项目": "New project",
  "示例已载入": "Sample loaded",
  "已把 SFA-5.41 示例接入工作台。": "The SFA-5.41 sample has been connected to the workbench.",
  "载入示例": "Load sample",
  "选择 STEP 文件": "Choose STEP file",
  "文件已就绪，可直接导入当前工作台。": "File is ready and can be imported into the current workbench.",
  "支持 .step / .stp，若当前未选项目，会自动创建项目后继续。": "Supports .step / .stp. If no project is selected, one will be created automatically.",
  "上传并导入到工作台": "Upload and import to workbench",
  "工作台已刷新": "Workbench refreshed",
  "已刷新当前项目的预览和语义状态。": "Current project preview and semantic status refreshed.",
  "刷新工作台": "Refresh workbench",
  "待执行": "Pending",
  "进行中": "In progress",
  "当前项目": "Current project",
  "聚焦当前选中的项目与最近项目，方便在工作流之间快速切换。": "Focus on the selected project and recent projects for quick workflow switching.",
  "错误": "Error",
  "无": "None",
  "高级操作": "Advanced actions",
  "在主流程之外，按需手动重跑导入、预览和校验能力。": "Manually rerun import, preview, and validation outside the main flow when needed.",
  "重新导入成功": "Re-import succeeded",
  "已重新生成当前项目的 .aieng 包并补全语义资源。": "Regenerated the current project's .aieng package and enriched semantic resources.",
  "重新导入 aieng": "Re-import aieng",
  "预览已更新": "Preview updated",
  "已重跑 STEP 预览链并刷新模型资产。": "Reran the STEP preview pipeline and refreshed model assets.",
  "重新生成预览": "Regenerate preview",
  "校验已完成": "Validation complete",
  "已执行后端校验并刷新语义信息。": "Backend validation ran and semantic information was refreshed.",
  "校验语义信息": "Validate semantics",
  "摘要已刷新": "Summary refreshed",
  "已刷新当前项目的 manifest、topology 和 validation。": "Refreshed the current project's manifest, topology, and validation.",
  "刷新项目摘要": "Refresh project summary",
  "语义摘要": "Semantic summary",
  "默认先看关键语义结论，再按需展开原始结构与集成信息。": "Review key semantic findings first, then expand raw structures and integration data as needed.",
  "拓扑数": "Topology count",
  "AI 摘要": "AI summary",
  "导入并富化后，这里会展示面向人的简要语义说明。": "After import and enrichment, a human-readable semantic summary appears here.",
  "语义摘要已降级": "Semantic summary degraded",
  "查看": "View",
  "查看集成与预览元数据": "View integration and preview metadata",
  "统一查看 runtime、MCP、.aieng 包工具和 benchmark 能力，先看副作用，再决定是否进入流程。": "Inspect runtime, MCP, .aieng package tools, and benchmark capabilities in one place. Review side effects before entering a flow.",
  "刷新能力": "Refresh capabilities",
  "搜索 tool / source / purpose": "Search tool / source / purpose",
  "查看 capability preview": "View capability preview",
  "暂无能力": "No capabilities",
  "后端未返回 capability registry。请检查 aieng 和 freecad-mcp 路径配置。": "The backend did not return a capability registry. Check the aieng and freecad-mcp path settings.",
  "把一组工具、LLM、benchmark、审批和 artifact 步骤作为可审计 workflow 运行。": "Run tool, LLM, benchmark, approval, and artifact steps as an auditable workflow.",
  "运行选中工作流": "Run selected workflow",
  "暂无 workflow": "No workflows",
  "后端未返回工作流定义。": "The backend did not return workflow definitions.",
  "复用环境设置中的同一份 Provider 配置，支持 dry-run 估算和真实 LLM A/B 运行。": "Reuse the same provider configuration from environment settings for dry-run estimates and real LLM A/B runs.",
  "Benchmark 会直接复用当前 LLM Provider 配置。": "Benchmark will reuse the current LLM provider configuration.",
  "当前 Provider 配置不完整，benchmark 可能无法走真实 LLM 路径。": "The current provider configuration is incomplete, so benchmark may not use the real LLM path.",
  "Dry-run / 成本估算": "Dry-run / cost estimate",
  "真实运行 benchmark": "Run real benchmark",
  "查看 benchmark run payload": "View benchmark run payload",
  "把 .aieng 资源按可用、缺失和证据链状态压缩成一个扫描视图。": "Condense .aieng resources by availability, missing items, and evidence-chain status.",
  "刷新 CAE 摘要": "Refresh CAE summary",
  "重新生成 .aieng CAE 摘要/证据文件（不执行求解器）": "Regenerate .aieng CAE summary/evidence files without running the solver",
  "导入外部计算指标": "Import external computed metrics",
  "从已有的 JSON/CSV 文件导入指标，再刷新 CAE 摘要。不执行求解器。": "Import metrics from an existing JSON/CSV file, then refresh the CAE summary. Does not run the solver.",
  "正在导入并刷新…": "Importing and refreshing...",
  "导入计算指标并刷新摘要": "Import metrics and refresh summary",
  "从 FRD 文件提取求解器结果": "Extract solver results from an FRD file",
  "解析 CalculiX .frd 文件，提取节点位移和应力场极值（最大位移、最大 von Mises 应力），写入 .aieng 包并刷新结果摘要。不执行求解器。": "Parse a CalculiX .frd file, extract nodal displacement and stress-field extrema (maximum displacement and maximum von Mises stress), write them into the .aieng package, and refresh the result summary. Does not run the solver.",
  "正在提取并刷新…": "Extracting and refreshing...",
  "提取 FRD 结果并刷新摘要": "Extract FRD results and refresh summary",
  "约束": "Constraints",
  "载荷": "Loads",
  "边界条件": "Boundary conditions",
  "结果证据": "Result evidence",
  "已检测到 CAE 结果证据": "CAE result evidence detected",
  "仅检测到 CAE 上下文": "Only CAE context detected",
  "当前项目包含可用于 CAE 可视层的结果或场数据。选择标量场后，3D 预览会叠加对应的结果颜色。": "The current project contains result or field data for the CAE visualization layer. Select a scalar field to overlay result colors on the 3D preview.",
  "当前项目包含分析目标、约束或外部 CAE 交接信息，但还没有可渲染的求解结果。UI 会优雅降级，不阻断现有 CAD 预览。": "The current project contains analysis targets, constraints, or external CAE handoff information, but no renderable solver results yet. The UI degrades gracefully and keeps the existing CAD preview available.",
  "使用 FRD 节点场数据映射到当前几何": "Using FRD nodal field data mapped to the current geometry",
  "使用结果契约提供的标量场描述渲染": "Rendering from the scalar-field descriptor provided by the result contract",
  "未检测到可渲染的 CAE 结果场，3D 视图保持 CAD-only 预览。": "No renderable CAE result field detected. The 3D view remains a CAD-only preview.",
  "选择或创建项目后，这里会显示 CAE 证据、约束、载荷和结果摘要。": "After selecting or creating a project, CAE evidence, constraints, loads, and result summaries appear here.",
  "暂无项目上下文": "No project context",
  "当前没有选中项目，CAE 面板暂时没有可审计资源。": "No project is currently selected, so the CAE panel has no auditable resources yet.",
  "加载中…": "Loading...",
  "工程智能体": "Engineering Agent",
  "对话、计划、审批与审计记录集中在同一工作区。": "Chat, planning, approval, and audit records share one workspace.",
  "模型设置": "Model settings",
  "当前计划": "Current plan",
  "步": "steps",
  "包含人工审批节点": "Includes human approval nodes",
  "当前计划无需人工审批": "Current plan does not need human approval",
  "当前连接需要先选择项目。": "This connection needs a project selected first.",
  "模型 Provider 未完成配置，当前会回落到规则规划。": "Model provider is not fully configured, so this will fall back to rule planning.",
  "连接": "Connection",
  "状态": "Status",
  "工具数": "Tools",
  "审批": "Approval",
  "启用": "Enabled",
  "不需要": "Not needed",
  "规划模式": "Planning mode",
  "模型优先": "Model first",
  "本地规则": "Local rules",
  "项目": "Project",
  "发送": "Send",
  "生成计划": "Generate plan",
  "执行安全步骤": "Execute safe steps",
  "运行": "Run",
  "查看工具": "View tools",
  "查看计划 payload": "View plan payload",
  "执行": "Execute",
  "计划": "Plan",
  "运行时": "Runtime",
  "变更差异": "Change diffs",
  "变更证据": "Change evidence",
  "查看审计日志": "View audit log",
  "等待工程请求": "Waiting for an engineering request",
  "计划、执行状态和审计入口会在这里持续保留。": "Plans, execution status, and audit links will persist here.",
  "例如：总结当前模型并给出下一步可执行的安全操作。": "Example: summarize the current model and propose the next safe executable action.",
  "规则编排与运行时调试": "Rule orchestration and runtime debugging",
  "规则计划": "Rule plan",
  "规则执行": "Rule execute",
  "Runtime 执行": "Runtime execute",
  "批准执行": "Approve execution",
  "拒绝": "Reject",
  "计划步骤": "Plan steps",
  "审计 ID": "Audit ID",
  "仅计划": "Plan only",
  "查看原始计划与执行输出": "View raw plan and execution output",
};

const enToZh = Object.entries(zhToEn).reduce<Record<string, string>>((acc, [zh, en]) => {
  acc[en] = zh;
  return acc;
}, {});

let activeLanguage: Language = "en";

function isSupportedLanguage(value: string | null): value is Language {
  return SUPPORTED_LANGUAGES.includes(value as Language);
}

function getInitialLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (isSupportedLanguage(stored)) return stored;
  } catch {
    // Ignore unavailable storage and keep the product default.
  }
  return "en";
}

function applyDictionary(text: string, dictionary: Record<string, string>) {
  if (!text.trim()) return text;
  const exact = dictionary[text];
  if (exact) return exact;

  let translated = text;
  for (const [source, target] of Object.entries(dictionary).sort((a, b) => b[0].length - a[0].length)) {
    if (translated.includes(source)) {
      translated = translated.split(source).join(target);
    }
  }
  return translated;
}

function toGbkMojibake(text: string) {
  try {
    return new TextDecoder("gb18030").decode(new TextEncoder().encode(text));
  } catch {
    return text;
  }
}

export function translateText(text: string, language = activeLanguage): string {
  if (language !== "en") return applyDictionary(text, enToZh);

  const translated = applyDictionary(text, zhToEn);
  if (translated !== text) return translated;

  const mojibakeText = toGbkMojibake(text);
  if (mojibakeText === text) return translated;
  const translatedMojibake = applyDictionary(mojibakeText, zhToEn);
  return translatedMojibake === mojibakeText ? translated : translatedMojibake;
}

type TextRecord = {
  source: string;
  translated: string;
};

type AttrRecord = Record<string, TextRecord>;

const textRecords = new WeakMap<Text, TextRecord>();
const attrRecords = new WeakMap<Element, AttrRecord>();
const TRANSLATABLE_ATTRIBUTES = ["aria-label", "placeholder", "title"];

function shouldSkipNode(node: Node) {
  const parent = node.parentElement;
  return Boolean(parent?.closest("[data-i18n-skip], script, style, code, pre"));
}

function translateTextNode(node: Text, language: Language) {
  if (shouldSkipNode(node)) return;
  const current = node.nodeValue ?? "";
  if (!current.trim()) return;

  const previous = textRecords.get(node);
  const source = previous && current === previous.translated ? previous.source : current;
  const translated = translateText(source, language);
  textRecords.set(node, { source, translated });
  if (current !== translated) {
    node.nodeValue = translated;
  }
}

function translateElementAttributes(element: Element, language: Language) {
  if (element.closest("[data-i18n-skip]")) return;
  const records = attrRecords.get(element) ?? {};

  for (const attr of TRANSLATABLE_ATTRIBUTES) {
    const current = element.getAttribute(attr);
    if (!current?.trim()) continue;
    const previous = records[attr];
    const source = previous && current === previous.translated ? previous.source : current;
    const translated = translateText(source, language);
    records[attr] = { source, translated };
    if (current !== translated) {
      element.setAttribute(attr, translated);
    }
  }

  attrRecords.set(element, records);
}

function translateTree(root: ParentNode, language: Language) {
  const walker = document.createTreeWalker(root, 4);
  let node = walker.nextNode();
  while (node) {
    translateTextNode(node as Text, language);
    node = walker.nextNode();
  }

  const elementRoot = root instanceof Element ? root : null;
  if (elementRoot) translateElementAttributes(elementRoot, language);
  root.querySelectorAll?.("*").forEach((element) => translateElementAttributes(element, language));
}

type I18nContextValue = {
  language: Language;
  setLanguage(language: Language): void;
  t(text: string): string;
};

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(getInitialLanguage);

  const setLanguage = useCallback((nextLanguage: Language) => {
    activeLanguage = nextLanguage;
    setLanguageState(nextLanguage);
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
    } catch {
      // Non-persistent language changes are still fine.
    }
  }, []);

  useEffect(() => {
    activeLanguage = language;
    document.documentElement.lang = language;

    let frame = window.requestAnimationFrame(() => translateTree(document.body, language));
    const observer = new MutationObserver((mutations) => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        for (const mutation of mutations) {
          if (mutation.type === "characterData") {
            translateTextNode(mutation.target as Text, language);
          } else if (mutation.type === "attributes" && mutation.target instanceof Element) {
            translateElementAttributes(mutation.target, language);
          } else {
            mutation.addedNodes.forEach((node) => {
              if (node instanceof Text) translateTextNode(node, language);
              if (node instanceof Element) translateTree(node, language);
            });
          }
        }
      });
    });

    observer.observe(document.body, {
      attributes: true,
      attributeFilter: TRANSLATABLE_ATTRIBUTES,
      characterData: true,
      childList: true,
      subtree: true,
    });

    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [language]);

  const value = useMemo<I18nContextValue>(
    () => ({
      language,
      setLanguage,
      t: (text) => translateText(text, language),
    }),
    [language, setLanguage],
  );

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return context;
}
