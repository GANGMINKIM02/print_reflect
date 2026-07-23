/**
 * 문서 업로드 페이지 (워크플로 1단계) — Figma 업로드 80% ERAI UI.
 */
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  DOC_TYPE_LABELS,
  DOC_TYPE_OPTIONS,
  type DocType,
  type UploadResult,
  uploadDocument,
  updateDocType,
} from "../api/client";
import { ExistingProjectsTable } from "../components/ui/ExistingProjectsTable";
import { ChatbotWidget } from "../components/ui/ChatbotWidget";
import { IconUploadCloud } from "../components/ui/icons";
import { StepIndicator } from "../components/ui/StepIndicator";
import { cacheUpload, getCachedUpload, getLastDocId } from "../utils/docCache";
import { saveSourceFile } from "../utils/sourceStore";

type SelectableDocType = Exclude<DocType, "unknown">;

interface UploadedDraft {
  result: UploadResult;
  file: File;
  suggestedType: SelectableDocType | null;
  caseNumber: string | null;
}

const CASE_NUM_PATTERN = /\d{2,4}\s*[가-힣]{1,4}\s*\d+/g;
const CASE_SYMBOLS = new Set([
  "가", "가단", "가합", "가소", "나", "다", "재가단", "재가합", "재가소", "재나", "재다", "상",
  "카", "카단", "카합", "카공", "카담", "카조", "카구", "카경", "카정", "카단조", "카합조",
  "타경", "타채", "타기", "타인", "타배", "타집", "머", "자", "차", "차전", "라", "마",
  "비", "비단", "비합", "과", "과단", "과합", "동", "인", "전", "지",
  "고", "고단", "고합", "고약", "고약정", "노", "도", "오", "재고단", "재고합", "재노", "재도",
  "모", "초", "초기", "초적", "초재", "로", "감고", "치고", "전고", "보", "버", "어", "치노", "치도",
  "드", "드단", "드합", "르", "므", "느", "느단", "느합", "재드단", "재드합", "재르", "재므",
  "재느단", "재느합", "너", "스", "정", "정단", "정합",
  "구", "구단", "구합", "누", "두", "아", "아단", "아합", "재구단", "재구합", "재누", "재두",
  "재아단", "재아합",
]);
const TYPE_STYLES: Record<SelectableDocType, string> = {
  civil: "from-[#e9f2ff] via-white to-[#f6faff] text-[#114dba] ring-[#9ec0ff] border-[#b8d0ff]",
  criminal: "from-[#edf4ff] via-white to-[#f7fbff] text-[#0f62fe] ring-[#8ab4ff] border-[#b4cbff]",
  family: "from-[#fff4e8] via-white to-[#fffaf3] text-[#b66914] ring-[#f4bf7d] border-[#f6d3aa]",
  administrative: "from-[#ecfbf3] via-white to-[#f6fffa] text-[#1f7a4d] ring-[#8dd6b0] border-[#b7e7cd]",
};

function toSelectableDocType(docType: DocType): SelectableDocType | null {
  return docType === "unknown" ? null : docType;
}

function extractCaseNumber(text?: string): string | null {
  if (!text) return null;
  const candidates = text.matchAll(CASE_NUM_PATTERN);
  for (const candidate of candidates) {
    const raw = candidate[0]?.trim();
    if (!raw) continue;
    const normalized = raw.replace(/[^0-9가-힣]/g, "");
    const parts = normalized.match(/^(\d{2,4})([가-힣]{1,4})(\d+)$/);
    if (!parts) continue;
    if (CASE_SYMBOLS.has(parts[2])) {
      return raw;
    }
  }
  return null;
}

function getSuggestedType(result: UploadResult): SelectableDocType | null {
  return toSelectableDocType(result.doc_type);
}

export function UploadPage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const lastDocId = getLastDocId();
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [uploadedDraft, setUploadedDraft] = useState<UploadedDraft | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectingType, setSelectingType] = useState<SelectableDocType | null>(null);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const filenameLabel = useMemo(() => {
    if (pendingFile?.name) return pendingFile.name;
    if (lastDocId) {
      const cached = getCachedUpload(lastDocId);
      if (cached?.filename) return cached.filename;
    }
    return "파일명";
  }, [pendingFile, lastDocId]);

  async function handleUpload(file: File) {
    setLoading(true);
    setError("");
    try {
      const result = await uploadDocument(file);
      const suggestedType = getSuggestedType(result);
      const caseNumber = extractCaseNumber(result.full_text);
      if (result.pages?.length && result.full_text) {
        cacheUpload({
          ...result,
          doc_type: result.doc_type,
          pages: result.pages,
          full_text: result.full_text,
          source_blob_url: URL.createObjectURL(file),
          source_filename: file.name,
          source_mime_type: file.type || undefined,
        });
        void saveSourceFile(result.id, file);
      }
      setUploadedDraft({
        result,
        file,
        suggestedType,
        caseNumber,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "업로드 실패");
    } finally {
      setLoading(false);
    }
  }

  async function handleTypeSelect(docType: SelectableDocType) {
    if (!uploadedDraft) return;
    setSelectingType(docType);
    setError("");
    try {
      const updated = await updateDocType(uploadedDraft.result.id, docType);
      const pages = uploadedDraft.result.pages;
      const fullText = uploadedDraft.result.full_text;
      if (pages?.length && fullText) {
        cacheUpload({
          ...uploadedDraft.result,
          doc_type: updated.doc_type,
          pages,
          full_text: fullText,
          source_blob_url: URL.createObjectURL(uploadedDraft.file),
          source_filename: uploadedDraft.file.name,
          source_mime_type: uploadedDraft.file.type || undefined,
        });
      }
      setUploadedDraft(null);
      navigate(`/documents/${uploadedDraft.result.id}/summary`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "사건 유형 저장 실패");
    } finally {
      setSelectingType(null);
    }
  }

  function selectFile(file: File) {
    setError("");
    setPendingFile(file);
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) selectFile(file);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) selectFile(file);
  }

  function handleDone() {
    if (pendingFile && !loading) {
      handleUpload(pendingFile);
    }
  }

  return (
    <div className="h-screen overflow-hidden bg-coolgray-10 flex flex-col">
      <header className="px-6 pt-4 pb-0 shrink-0">
        <div className="flex items-start justify-between gap-6 mb-3">
          <h1 className="text-[32px] font-bold leading-tight text-coolgray-90 tracking-tight">
            ER<span className="text-primary-60">AI</span>
          </h1>
          <span className="text-primary-60 font-medium text-base tracking-wide shrink-0 pt-1 truncate max-w-[40vw]">
            {filenameLabel}
          </span>
        </div>
      </header>

      <div className="flex-1 flex flex-col mx-6 mb-4 min-h-0 bg-white border border-coolgray-20 overflow-hidden">
        <StepIndicator current="upload" docId={lastDocId ?? undefined} />

        <div className="flex-1 min-h-0 overflow-y-auto">
          <section className="mx-5 mt-6">
            <h2 className="text-lg font-bold text-coolgray-90 mb-3">새 프로젝트</h2>

            <div className="rounded-xl border border-[#e6e7ea] bg-coolgray-10 p-6">
              <div
                className={`mx-auto max-w-[649px] rounded-lg border border-dashed p-8 flex flex-col items-center gap-3 transition-colors ${
                  dragOver
                    ? "border-primary-60 bg-blue-50"
                    : "border-primary-60 bg-coolgray-20"
                }`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={onDrop}
              >
                <IconUploadCloud className="size-8 text-primary-60" />
                <div className="text-center">
                  <p className="text-base font-medium text-coolgray-90">
                    {pendingFile ? pendingFile.name : "Drop file or browse"}
                  </p>
                  <p className="text-sm text-coolgray-60 mt-1">
                    Format: PDF, PNG, JPG, TXT, DOC, DOCX, HWP · Max 25 MB
                  </p>
                </div>
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => inputRef.current?.click()}
                  className="px-4 py-1 text-sm font-semibold text-white bg-[#6794e5] border border-white rounded-lg shadow-sm hover:bg-primary-60 disabled:opacity-50"
                >
                  Browse Files
                </button>
                <input
                  ref={inputRef}
                  type="file"
                  className="hidden"
                  accept=".pdf,.png,.jpg,.jpeg,.txt,.doc,.docx,.hwp,.hwpx"
                  disabled={loading}
                  onChange={onInputChange}
                />
              </div>

              <div className="mx-auto max-w-[538px] mt-4">
                <button
                  type="button"
                  disabled={loading || !pendingFile}
                  onClick={handleDone}
                  className="w-full py-2.5 text-base font-semibold text-white bg-primary-60 border border-white rounded-lg shadow-sm hover:bg-primary-90 disabled:opacity-50 transition-colors"
                >
                  {loading ? "업로드 중..." : "Done"}
                </button>
              </div>

              {error && (
                <p className="mt-3 text-sm text-alert text-center">{error}</p>
              )}
            </div>
          </section>

          <ExistingProjectsTable />
        </div>
      </div>

      <ChatbotWidget />

      {uploadedDraft && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#0f172a]/38 px-4 py-6 backdrop-blur-[2px]">
          <div className="w-full max-w-[760px] overflow-hidden rounded-[28px] border border-[#d9e2f3] bg-white shadow-[0_24px_80px_rgba(15,23,42,0.18)]">
            <div className="border-b border-[#edf1f7] bg-[linear-gradient(135deg,#f8fbff_0%,#eef5ff_48%,#ffffff_100%)] px-7 py-6">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm font-semibold uppercase tracking-[0.18em] text-primary-60">Case Type</p>
                </div>
                <button
                  type="button"
                  onClick={() => setUploadedDraft(null)}
                  className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-[#d6deeb] bg-white text-coolgray-60 transition-colors hover:bg-coolgray-10 hover:text-coolgray-90"
                  aria-label="팝업 닫기"
                >
                  X
                </button>
              </div>
              <h3 className="mt-2 text-[28px] font-bold leading-tight text-coolgray-90">
                사건 유형을 선택해 주세요
              </h3>
              <p className="mt-2 text-sm leading-6 text-coolgray-60">
                업로드한 판결문을 파싱한 결과를 바탕으로 사건 유형 색상을 먼저 표시했습니다.
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
                <span className="rounded-full bg-white px-3 py-1 font-medium text-coolgray-90 ring-1 ring-[#d6deeb]">
                  파일명: {uploadedDraft.result.filename}
                </span>
                {uploadedDraft.caseNumber && (
                  <span className="rounded-full bg-[#eaf2ff] px-3 py-1 font-medium text-primary-90 ring-1 ring-[#c3d7ff]">
                    사건번호 {uploadedDraft.caseNumber}
                  </span>
                )}
                {uploadedDraft.suggestedType && (
                  <span className="rounded-full bg-[#edf4ff] px-3 py-1 font-medium text-primary-90 ring-1 ring-[#c3d7ff]">
                    {DOC_TYPE_LABELS[uploadedDraft.suggestedType]}
                  </span>
                )}
              </div>
            </div>

            <div className="px-7 py-7">
              <div className="grid gap-3 sm:grid-cols-2">
                {DOC_TYPE_OPTIONS.map(({ value, label }) => {
                  const suggested = uploadedDraft.suggestedType === value;
                  const busy = selectingType !== null;
                  const typeTone = TYPE_STYLES[value];
                  return (
                    <button
                      key={value}
                      type="button"
                      disabled={busy}
                      onClick={() => handleTypeSelect(value)}
                      className={`group relative overflow-hidden rounded-[22px] border px-5 py-5 text-left transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-60 ${
                        suggested
                          ? `bg-gradient-to-br ${typeTone} shadow-[0_14px_36px_rgba(15,98,254,0.12)] ring-2`
                          : "border-[#dde5f0] bg-white text-coolgray-90 hover:border-[#adc8ff] hover:bg-[#f8fbff]"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-[22px] font-bold tracking-tight">{label}</p>
                          <p className="mt-2 text-sm leading-6 text-coolgray-60">
                            {suggested
                              ? "사건번호와 문서 파싱 결과를 기준으로 먼저 강조된 유형입니다."
                              : "이 유형으로 요약과 이후 워크플로를 진행합니다."}
                          </p>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>

              <div className="mt-6 flex items-center justify-between gap-4 border-t border-[#edf1f7] pt-5">
                <p className="text-sm text-coolgray-60">
                  선택한 사건 유형으로 요약 페이지가 열립니다.
                </p>
                {selectingType && (
                  <span className="text-sm font-medium text-primary-60">
                    {DOC_TYPE_LABELS[selectingType]} 유형으로 이동 중...
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
