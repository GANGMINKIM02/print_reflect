/**
 * PDF 추출 페이지 (워크플로 5단계) — Figma 추출 80% ERAI UI.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { TranslationSegment } from "../api/client";
import { downloadMergedPdf, downloadPdf, fetchExportDocx, fetchExportPdf } from "../api/client";
import { DocxPreviewPanel } from "../components/DocxPreviewPanel";
import { WorkflowLayout } from "../components/ui/WorkflowLayout";
import { getCachedUpload } from "../utils/docCache";
import { enrichSegmentsForExport } from "../utils/exportImages";
import { loadDocumentWithRecovery } from "../utils/documentLoader";
import {
  getWorkflowSnapshot,
  resolveSummary,
  resolveTranslationSegments,
  saveWorkflowSnapshot,
} from "../utils/workflowCache";

export function ExportPage() {
  const { id } = useParams<{ id: string }>();
  const [filename, setFilename] = useState("");
  const [summary, setSummary] = useState("");
  const [segments, setSegments] = useState<TranslationSegment[]>([]);
  const [previewBlob, setPreviewBlob] = useState<Blob | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewReady, setPreviewReady] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportingMerged, setExportingMerged] = useState(false);
  const [error, setError] = useState("");

  const actionButtonClass =
    "inline-flex h-14 min-w-0 basis-0 flex-1 items-center justify-center whitespace-nowrap rounded-lg border border-white bg-primary-60 px-3 text-base font-semibold text-white shadow-sm transition-colors hover:bg-primary-90 disabled:cursor-not-allowed disabled:opacity-50";

  const load = useCallback(async () => {
    if (!id) return;
    setError("");
    try {
      const doc = await loadDocumentWithRecovery(id);
      setFilename(doc.filename);
      setSummary(resolveSummary(id, doc.summary));
      const segs = resolveTranslationSegments(id, doc.translation_segments);
      setSegments(segs);
      if (segs.length) {
        saveWorkflowSnapshot(id, { translation_segments: segs, filename: doc.filename });
      }
    } catch (err) {
      const workflow = getWorkflowSnapshot(id);
      const cached = getCachedUpload(id);
      const segs = resolveTranslationSegments(id, workflow?.translation_segments ?? []);
      if (segs.length) {
        setFilename(workflow?.filename ?? cached?.filename ?? "");
        setSummary(resolveSummary(id, workflow?.summary));
        setSegments(segs);
        return;
      }
      setError(err instanceof Error ? err.message : "문서를 불러오지 못했습니다");
    }
  }, [id]);

  useEffect(() => {
    load().catch(console.error);
  }, [load]);

  const buildExportPayload = useCallback(async () => {
    const cached = id ? getCachedUpload(id) : null;
    const mergedSegments = id ? resolveTranslationSegments(id, segments) : segments;
    const exportSegments = await enrichSegmentsForExport(mergedSegments);
    return {
      segments: exportSegments,
      translation_text: exportSegments.map((s) => s.easy_text).filter(Boolean).join("\n\n"),
      summary,
      filename,
      doc_type: cached?.doc_type,
      full_text: cached?.full_text,
      pages: cached?.pages,
    };
  }, [id, segments, summary, filename]);

  useEffect(() => {
    if (!id || segments.length === 0) {
      setPreviewBlob(null);
      setPreviewReady(false);
      return;
    }

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewReady(false);
    setError("");

    (async () => {
      const payload = await buildExportPayload();
      const blob = await fetchExportDocx(id, payload);
      if (!cancelled) {
        setPreviewBlob(blob);
      }

      // 추출 페이지 진입 시점에 easyread.pdf를 사용자 저장소에 갱신 저장한다.
      void fetchExportPdf(id, payload).catch((err) => {
        console.warn("easyread pdf cache save skipped", err);
      });
    })()
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "미리보기 생성 실패");
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id, segments, buildExportPayload]);

  async function handleExportPdf() {
    if (!id || segments.length === 0 || !previewReady) return;
    setExporting(true);
    setError("");
    try {
      const payload = await buildExportPayload();
      try {
        await downloadPdf(id, payload);
        return;
      } catch {
        // Vercel 등 Word/LibreOffice 없는 서버 — 브라우저 Print to PDF
      }
      window.print();
    } catch (err) {
      setError(err instanceof Error ? err.message : "PDF 추출 실패");
    } finally {
      setExporting(false);
    }
  }

  async function handleExportMerged() {
    if (!id || segments.length === 0 || !previewReady) return;
    setExportingMerged(true);
    setError("");
    try {
      const payload = await buildExportPayload();
      await downloadMergedPdf(id, payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "이지리드 합본 추출 실패");
    } finally {
      setExportingMerged(false);
    }
  }

  return (
    <WorkflowLayout
      step="export"
      docId={id}
      headerVariant="compact"
      projectTitle={
        <>
          ER<span className="text-primary-60">AI</span>
        </>
      }
      filename={filename || "파일명"}
      error={error || undefined}
    >
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden px-5 pt-4 pb-5">
        <div className="flex-1 flex flex-col items-center gap-4 min-h-0 overflow-hidden w-full max-w-[916px] mx-auto">
          <div className="w-full flex-1 min-h-0 border border-coolgray-30 overflow-hidden rounded-sm shadow-inner bg-[#e8e8e8]">
            {previewLoading ? (
              <div className="h-full flex items-center justify-center text-coolgray-60 text-base">
                미리보기 생성 중...
              </div>
            ) : previewBlob ? (
              <DocxPreviewPanel
                blob={previewBlob}
                onReady={() => setPreviewReady(true)}
                onError={(message) => setError(message)}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-coolgray-60 text-base px-6 text-center">
                {segments.length === 0
                  ? "번역 내용이 없어 미리보기를 생성할 수 없습니다."
                  : "미리보기를 불러올 수 없습니다."}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-center gap-4 shrink-0 w-full max-w-[720px]">
            <button
              type="button"
              onClick={handleExportPdf}
              disabled={
                exporting || exportingMerged || previewLoading || !previewReady || segments.length === 0
              }
              className={actionButtonClass}
            >
              {exporting ? "추출 중..." : "이지리드 추출하기"}
            </button>
            <button
              type="button"
              onClick={handleExportMerged}
              disabled={
                exporting || exportingMerged || previewLoading || !previewReady || segments.length === 0
              }
              className={actionButtonClass}
            >
              {exportingMerged ? "추출 중..." : "이지리드 합본 추출하기"}
            </button>
            <Link
              to="/"
              className={`${actionButtonClass} text-center`}
            >
              업로드 화면으로 돌아가기
            </Link>
          </div>
        </div>
      </div>
    </WorkflowLayout>
  );
}
