/**
 * 백엔드 REST API 클라이언트 및 공유 타입 정의.
 *
 * 역할: 문서 업로드·요약·번역·체크리스트·Word 출력 등 API 호출을 캡슐화한다.
 * 주요 기능: fetch 래퍼, Document/TranslationSegment 타입, 파일 다운로드.
 * 연관 파일: pages/*.tsx, components/*.tsx, utils/docCache.ts
 */
import { getAuthUserId } from "../utils/auth";

const API_BASE = "/api";

export type DocType = "criminal" | "civil" | "family" | "administrative" | "unknown";

export const DOC_TYPE_LABELS: Record<Exclude<DocType, "unknown">, string> = {
  criminal: "형사",
  civil: "민사",
  family: "가사",
  administrative: "행정",
};

export const DOC_TYPE_OPTIONS: { value: Exclude<DocType, "unknown">; label: string }[] = [
  { value: "criminal", label: "형사" },
  { value: "civil", label: "민사" },
  { value: "family", label: "가사" },
  { value: "administrative", label: "행정" },
];

export interface ImagePlacement {
  id: string;
  image_file: string;
  line_index: number;
  title?: string | null;
  image_url?: string | null;
  section_heading?: string | null;
  /** PDF 추출 POST body 전용 — 서버 파일 해석 없이 이미지 삽입 */
  image_base64?: string | null;
  /** 그림 탭 자동 배치 — X 삭제 불가 */
  auto_filled?: boolean;
}

export interface ImageCatalogItem {
  image_file: string;
  title: string;
  url: string;
  source_url?: string | null;
}

export interface TranslationSegment {
  id: string;
  original: string;
  easy_text: string;
  image_file?: string | null;
  image_url?: string | null;
  title?: string | null;
  source: "db" | "solar" | "manual";
  image_placements?: ImagePlacement[];
}

export interface ChecklistItemResult {
  id: string;
  category: string;
  label: string;
  status: "pass" | "warn" | "fail" | "manual";
  detail?: string | null;
}

export interface ChecklistReport {
  overall: "pass" | "warn" | "fail";
  summary: Record<string, number>;
  items: ChecklistItemResult[];
}

export interface Document {
  id: string;
  filename: string;
  doc_type: DocType;
  stage: "uploaded" | "summarized" | "translated";
  page_count: number;
  full_text: string;
  summary?: string | null;
  translation_segments: TranslationSegment[];
  translation_text?: string | null;
  checklist?: ChecklistReport | null;
}

export interface UploadResult {
  id: string;
  filename: string;
  doc_type: DocType;
  page_count: number;
  message: string;
  pages?: string[];
  full_text?: string;
}

export interface UserProjectItem {
  doc_id: string;
  filename: string;
  created_at: string;
  updated_at: string;
  has_summary: boolean;
  has_translation: boolean;
  has_easyread_pdf: boolean;
  has_easyread: boolean;
}

export type UserProjectArtifactKind = "summary" | "translation" | "easyread";

async function parseErrorResponse(res: Response): Promise<string> {
  const text = await res.text();
  if (!text) return res.statusText;
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail != null) return JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return text;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  const authUser = getAuthUserId();
  if (authUser) {
    headers.set("X-User-Id", authUser);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(res));
  }
  if (res.headers.get("content-type")?.includes("application/json")) {
    return res.json();
  }
  return res as unknown as T;
}

export async function uploadDocument(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  return request<UploadResult>("/documents/upload", { method: "POST", body: form });
}

export async function getDocument(id: string): Promise<Document> {
  return request<Document>(`/documents/${id}`);
}

export async function getPage(id: string, pageNum: number): Promise<string> {
  const data = await request<{ page: string }>(`/documents/${id}/pages/${pageNum}`);
  return data.page;
}

export async function summarize(id: string, force = false, fallback?: {
  full_text: string;
  doc_type: DocType;
  filename: string;
  pages: string[];
}): Promise<Document> {
  const q = force ? "?force=true" : "";
  return request<Document>(`/documents/${id}/summarize${q}`, {
    method: "POST",
    headers: fallback ? { "Content-Type": "application/json" } : undefined,
    body: fallback ? JSON.stringify(fallback) : undefined,
  });
}

export interface EnsurePayload {
  full_text: string;
  doc_type: DocType;
  filename: string;
  pages: string[];
}

export async function updateSummary(
  id: string,
  summary: string,
  ensure?: EnsurePayload,
): Promise<Document> {
  return request<Document>(`/documents/${id}/summary`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ summary, ...ensure }),
  });
}

export async function updateDocType(id: string, docType: DocType): Promise<Document> {
  return request<Document>(`/documents/${id}/doc-type`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_type: docType }),
  });
}

export async function refineSummary(
  id: string,
  prompt: string,
  summary?: string,
): Promise<Document> {
  return request<Document>(`/documents/${id}/summary/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, summary }),
  });
}

export async function translate(
  id: string,
  ensure?: EnsurePayload & { summary?: string },
): Promise<Document> {
  return request<Document>(`/documents/${id}/translate`, {
    method: "POST",
    headers: ensure ? { "Content-Type": "application/json" } : undefined,
    body: ensure ? JSON.stringify(ensure) : undefined,
  });
}

export async function updateTranslation(
  id: string,
  segments: TranslationSegment[],
  ensure?: EnsurePayload & { summary?: string },
): Promise<Document> {
  return request<Document>(`/documents/${id}/translation`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments, ...ensure }),
  });
}

export async function refineTranslation(
  id: string,
  prompt: string,
  segments?: TranslationSegment[],
): Promise<Document> {
  return request<Document>(`/documents/${id}/translation/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, segments }),
  });
}

export async function runChecklist(id: string): Promise<ChecklistReport> {
  return request<ChecklistReport>(`/documents/${id}/translation/checklist`, {
    method: "POST",
  });
}

export function exportDocxUrl(id: string): string {
  return `${API_BASE}/documents/${id}/export.docx`;
}

export function exportPdfUrl(id: string, download = false): string {
  const q = download ? "?download=true" : "";
  return `${API_BASE}/documents/${id}/export.pdf${q}`;
}

export interface ExportPayload {
  segments: TranslationSegment[];
  translation_text?: string;
  summary?: string;
  filename?: string;
  doc_type?: DocType;
  full_text?: string;
  pages?: string[];
}

function buildExportBody(payload: ExportPayload) {
  return {
    ...payload,
    translation_text:
      payload.translation_text ??
      payload.segments.map((s) => s.easy_text).filter(Boolean).join("\n\n"),
  };
}

function buildExportHeaders(): HeadersInit {
  const authUser = getAuthUserId();
  if (authUser) {
    return {
      "Content-Type": "application/json",
      "X-User-Id": authUser,
    };
  }
  return { "Content-Type": "application/json" };
}

export async function fetchExportDocx(id: string, payload: ExportPayload): Promise<Blob> {
  const res = await fetch(`${API_BASE}/documents/${id}/export.docx`, {
    method: "POST",
    headers: buildExportHeaders(),
    body: JSON.stringify(buildExportBody(payload)),
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(res));
  }
  return res.blob();
}

export async function fetchExportPdf(id: string, payload: ExportPayload): Promise<Blob> {
  const res = await fetch(`${API_BASE}/documents/${id}/export.pdf`, {
    method: "POST",
    headers: buildExportHeaders(),
    body: JSON.stringify(buildExportBody(payload)),
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(res));
  }
  return res.blob();
}

export async function downloadPdf(id: string, payload: ExportPayload): Promise<void> {
  const res = await fetch(`${API_BASE}/documents/${id}/export.pdf?download=true`, {
    method: "POST",
    headers: buildExportHeaders(),
    body: JSON.stringify(buildExportBody(payload)),
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(res));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `easyread_${id.slice(0, 8)}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function downloadDocx(id: string, payload: ExportPayload): Promise<void> {
  const res = await fetch(`${API_BASE}/documents/${id}/export.docx`, {
    method: "POST",
    headers: buildExportHeaders(),
    body: JSON.stringify(buildExportBody(payload)),
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(res));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `easyread_${id.slice(0, 8)}.docx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function downloadMergedPdf(id: string, payload: ExportPayload): Promise<void> {
  const res = await fetch(`${API_BASE}/documents/${id}/export.merged.pdf`, {
    method: "POST",
    headers: buildExportHeaders(),
    body: JSON.stringify(buildExportBody(payload)),
  });
  if (!res.ok) {
    throw new Error(await parseErrorResponse(res));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `easyread_merged_${id.slice(0, 8)}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function getImageCatalog(query = ""): Promise<ImageCatalogItem[]> {
  const q = query ? `?q=${encodeURIComponent(query)}` : "";
  return request<ImageCatalogItem[]>(`/documents/catalog/images${q}`);
}

export async function listUserProjects(): Promise<UserProjectItem[]> {
  return request<UserProjectItem[]>("/documents/user-projects");
}

export async function deleteUserProject(docId: string): Promise<void> {
  await request<void>(`/documents/user-projects/${docId}`, {
    method: "DELETE",
  });
}

export async function getUserProjectArtifact(
  docId: string,
  kind: UserProjectArtifactKind,
): Promise<string> {
  const data = await request<{ content: string }>(
    `/documents/user-projects/${docId}/artifact/${kind}`,
  );
  return data.content;
}

export function getUserProjectSourceUrl(docId: string): string {
  const userId = getAuthUserId() ?? "";
  const q = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  return `${API_BASE}/documents/user-projects/${docId}/source${q}`;
}

export function getUserProjectEasyreadPdfUrl(docId: string): string {
  const userId = getAuthUserId() ?? "";
  const q = userId ? `?user_id=${encodeURIComponent(userId)}` : "";
  return `${API_BASE}/documents/user-projects/${docId}/easyread.pdf${q}`;
}

export async function searchWebImages(query: string): Promise<ImageCatalogItem[]> {
  return request<ImageCatalogItem[]>(
    `/documents/catalog/images/web?q=${encodeURIComponent(query)}`,
  );
}

export async function detectImagePlacements(
  docId: string,
  options?: Partial<EnsurePayload> & {
    summary?: string;
    existingPlacements?: ImagePlacement[];
    translationText?: string;
  },
): Promise<ImagePlacement[]> {
  const payload = {
    ...(options ?? {}),
    existing_placements: options?.existingPlacements ?? [],
    translation_text: options?.translationText,
  };
  return request<ImagePlacement[]>(`/documents/${docId}/translation/detect-placements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  reply: string;
  sources: string[];
}

export async function sendChatMessage(
  message: string,
  history: ChatMessage[] = [],
  docId?: string,
): Promise<ChatResponse> {
  const path = docId ? `/chat/documents/${docId}` : "/chat";
  return request<ChatResponse>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });
}

export async function getChatPrompt(): Promise<{ system_prompt: string }> {
  return request<{ system_prompt: string }>("/chat/prompt");
}

export async function updateChatPrompt(systemPrompt: string): Promise<{ system_prompt: string }> {
  return request<{ system_prompt: string }>("/chat/prompt", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ system_prompt: systemPrompt }),
  });
}
