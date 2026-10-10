export type Fact = {
  id: string;
  version: number;
  claim: string;
  origin: "source" | "company" | "web" | "history" | "user" | "draft";
  verification: "verified" | "unverified" | "conflicted" | "pending";
  marked: boolean;
  excluded: boolean;
  required_for_article: boolean;
  mark_reasons: string[];
  evidence?: Array<{
    evidence_id: string;
    source_kind: string;
    title: string;
    url: string;
    quote: string;
    authority: number;
    document_id?: string;
    document_version?: string;
    metadata?: Record<string, unknown>;
  }>;
};

export type ReviewIssue = { issue_id?: string; fact_id?: string; section_id?: string; paragraph_id?: string;
  rule_code?: string; claim?: string; expected_fact?: string; reason?: string; message?: string; evidence?: Fact["evidence"] };
export type FactCheck = { check_id?: string; check_type: "baseline" | "draft_claim";
  fact_id?: string; fact_version?: number; section_id?: string; paragraph_id: string;
  decision: "accept" | "refuse" | "missing" | "pending"; claim: string;
  expected_fact?: string; reason: string };

export type OutlineSection = {
  section_id: string;
  position: number;
  title: string;
  purpose: string;
  main_points: string[];
  fact_ids: string[];
  paragraphs: Array<{ paragraph_id: string; order: number; purpose: string; main_points: string[]; fact_ids: string[]; required_fact_ids: string[] }>;
};

export type ParagraphDraft = { section_id: string; paragraph_id: string; section_order: number; paragraph_order: number; fact_ids: string[]; content: string };
export type ParagraphProvenance = {
  paragraph_id: string;
  section_id: string;
  draft_version: number;
  step: "generator" | "revise";
  previous_draft_version?: number;
  issue_ids: string[];
  issues: ReviewIssue[];
  fact_refs: Array<{ fact_id: string; version: number; claim: string; decision_id?: string; evidence: NonNullable<Fact["evidence"]> }>;
};

export type Workspace = {
  id: string;
  stage: string;
  article_id?: string;
  source_text?: string;
  fact_version: number;
  outline_version: number;
  facts: Fact[];
  outline: OutlineSection[];
  messages: Array<{ id: string; role: string; content: string; created_at: string }>;
  plan_dirty: boolean;
  current_draft_version: number;
  draft?: { version: number; content: string; paragraphs: ParagraphDraft[]; created_at: string };
  paragraph_provenance: ParagraphProvenance[];
  waiting_job_id?: string;
  pause_request?: { kind: string; fact_version?: number; outline_version?: number; thesis?: string; key_points?: string[]; facts?: Array<Record<string, any>>; conflicts?: Array<Record<string, any>>; issues?: Array<Record<string, any>>; outline?: OutlineSection[] } | null;
  system_decisions?: Array<{id: string; note: string; fact_id: string; fact_version: number; conflict: ReviewIssue}>;
  user_decisions?: Array<{id: string; fact_id: string; fact_version: number; issue_id?: string; action: string; note: string; decided_by: string; decided_at: string}>;
  review?: { draft_version: number; status: string; issues: ReviewIssue[]; fact_checks: FactCheck[];
    stop_reason: string; failure_number: number };
  events: Array<{
    sequence: number;
    event_type: string;
    payload: { error?: string; warnings?: string[]; stage?: string; code?: string; message?: string };
    created_at: string;
  }>;
};

const tokenKey = "forge.token";
export const session = {
  get token() { return localStorage.getItem(tokenKey) ?? ""; },
  set token(value: string) { localStorage.setItem(tokenKey, value); },
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail ?? "请求失败");
  }
  return response.json() as Promise<T>;
}

export const api = {
  async login(email: string, password: string) {
    const result = await request<{ access_token: string }>("/v1/auth/token", {
      method: "POST", body: JSON.stringify({ email, password }),
    });
    session.token = result.access_token;
  },
  async register(email: string, password: string) {
    const result = await request<{ access_token: string }>("/v1/auth/register", {
      method: "POST", body: JSON.stringify({ email, password }),
    });
    session.token = result.access_token;
  },
  createConversation: () => request<{ conversation_id: string }>("/v1/conversations", { method: "POST" }),
  workspace: (id: string) => request<Workspace>(`/v1/conversations/${id}`),
  message: (id: string, body: { content: string; source_text?: string }) =>
    request<{ job_id: string }>(`/v1/conversations/${id}/messages`, { method: "POST", body: JSON.stringify(body) }),
  fact: (id: string, body: object) =>
    request<{ fact_version: number }>(`/v1/conversations/${id}/facts`, { method: "PATCH", body: JSON.stringify(body) }),
  revise: (id: string) => request<{ job_id: string }>(`/v1/conversations/${id}/revise`, { method: "POST" }),
  decision: (id: string, body: { job_id: string; fact_version: number; decision: Record<string, any> }) =>
    request<{ job_id: string }>(`/v1/conversations/${id}/decisions`, { method: "POST", body: JSON.stringify(body) }),
  finalize: (id: string) => request<{ job_id: string }>(`/v1/conversations/${id}/finalize`, { method: "POST" }),
};
