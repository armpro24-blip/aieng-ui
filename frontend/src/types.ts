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

export type ProjectSummary = {
  project: ProjectRecord;
  files?: Record<string, unknown>;
  members: string[];
  manifest?: Record<string, unknown> | null;
  feature_graph?: Record<string, unknown> | null;
  topology?: Record<string, unknown> | null;
  validation?: Record<string, unknown> | null;
  viewer?: Record<string, unknown> | null;
  viewer_url?: string | null;
  ai_summary?: string | null;
  derived?: Record<string, unknown>;
  summary_error?: string | null;
  summary_mode?: string | null;
  integration?: Record<string, unknown>;
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
};
