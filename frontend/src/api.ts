import type { ChatResponse, ProjectRecord, ProjectSummary } from "./types";

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
  runtime: () => request<Record<string, unknown>>("/api/runtime"),
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
};
