/**
 * 기존 프로젝트 대시보드 (로그인 사용자별 저장 항목).
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  deleteUserProject,
  getUserProjectArtifact,
  getUserProjectEasyreadPdfUrl,
  getUserProjectSourceUrl,
  listUserProjects,
  type UserProjectArtifactKind,
  type UserProjectItem,
} from "../../api/client";
import { StyledLine } from "../BoldText";

export function ExistingProjectsTable() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<UserProjectItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modalTitle, setModalTitle] = useState("");
  const [modalText, setModalText] = useState("");
  const [modalOpen, setModalOpen] = useState(false);
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null);
  const modalLines = modalText.split("\n");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    listUserProjects()
      .then((items) => {
        if (!cancelled) setRows(items);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "기존 프로젝트를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  async function openArtifact(docId: string, kind: UserProjectArtifactKind) {
    try {
      const content = await getUserProjectArtifact(docId, kind);
      const titleMap: Record<UserProjectArtifactKind, string> = {
        summary: "요약",
        translation: "번역",
        easyread: "이지리드",
      };
      setModalTitle(titleMap[kind]);
      setModalText(content);
      setModalOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "파일을 열지 못했습니다.");
    }
  }

  function openSource(docId: string) {
    window.open(getUserProjectSourceUrl(docId), "_blank", "noopener,noreferrer");
  }

  function openEasyreadPdf(docId: string) {
    window.open(getUserProjectEasyreadPdfUrl(docId), "_blank", "noopener,noreferrer");
  }

  function editProject(docId: string) {
    try {
      sessionStorage.setItem("easyread:last-doc-id", docId);
    } catch {
      /* ignore session storage write errors */
    }
    navigate(`/documents/${docId}/summary`);
  }

  async function deleteProject(docId: string, filename: string) {
    const ok = window.confirm(`'${filename}' 프로젝트를 삭제할까요?`);
    if (!ok) return;

    setDeletingDocId(docId);
    setError("");
    try {
      await deleteUserProject(docId);
      setRows((prev) => prev.filter((row) => row.doc_id !== docId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "프로젝트 삭제에 실패했습니다.");
    } finally {
      setDeletingDocId(null);
    }
  }

  return (
    <section className="mx-5 mb-6 mt-6">
      <h2 className="text-lg font-bold text-coolgray-90 mb-4">기존 프로젝트</h2>

      <div className="border border-coolgray-20 bg-white overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="bg-coolgray-10 border-t border-coolgray-20">
                <th className="px-3 py-4 text-left font-medium text-coolgray-90 border-t border-coolgray-20">
                  파일명
                </th>
                <th className="px-3 py-4 text-left font-medium text-coolgray-90 border-t border-coolgray-20 w-36">
                  수정하기
                </th>
                <th className="px-3 py-4 text-left font-medium text-coolgray-90 border-t border-coolgray-20 w-36">
                  요약
                </th>
                <th className="px-3 py-4 text-left font-medium text-coolgray-90 border-t border-coolgray-20 w-36">
                  번역
                </th>
                <th className="px-3 py-4 text-left font-medium text-coolgray-90 border-t border-coolgray-20 w-36">
                  이지리드
                </th>
                <th className="px-3 py-4 text-center font-medium text-coolgray-90 border-t border-coolgray-20 w-16" />
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr className="border-t border-coolgray-20">
                  <td colSpan={6} className="px-3 py-8 text-center text-coolgray-60">
                    불러오는 중...
                  </td>
                </tr>
              )}

              {!loading && rows.length === 0 && (
                <tr className="border-t border-coolgray-20">
                  <td colSpan={6} className="px-3 py-8 text-center text-coolgray-60">
                    저장된 프로젝트가 없습니다.
                  </td>
                </tr>
              )}

              {!loading &&
                rows.map((row) => (
                  <tr key={row.doc_id} className="border-t border-coolgray-20">
                    <td className="px-3 py-3">
                      <button
                        type="button"
                        onClick={() => openSource(row.doc_id)}
                        className="text-left font-medium text-primary-60 underline-offset-2 hover:underline"
                      >
                        {row.filename}
                      </button>
                    </td>
                    <td className="px-3 py-3 text-coolgray-90">
                      <button
                        type="button"
                        onClick={() => editProject(row.doc_id)}
                        className="rounded border border-primary-60 px-2 py-1 text-xs font-medium text-primary-60 hover:bg-blue-50"
                      >
                        수정하기
                      </button>
                    </td>
                    <td className="px-3 py-3 text-coolgray-90">
                      <button
                        type="button"
                        disabled={!row.has_summary}
                        onClick={() => openArtifact(row.doc_id, "summary")}
                        className="rounded border border-coolgray-30 px-2 py-1 text-xs font-medium text-coolgray-90 hover:bg-coolgray-10 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        열기
                      </button>
                    </td>
                    <td className="px-3 py-3 text-coolgray-90">
                      <button
                        type="button"
                        disabled={!row.has_translation}
                        onClick={() => openArtifact(row.doc_id, "translation")}
                        className="rounded border border-coolgray-30 px-2 py-1 text-xs font-medium text-coolgray-90 hover:bg-coolgray-10 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        열기
                      </button>
                    </td>
                    <td className="px-3 py-3 text-coolgray-90">
                      <button
                        type="button"
                        disabled={!row.has_easyread_pdf}
                        onClick={() => openEasyreadPdf(row.doc_id)}
                        className="rounded border border-coolgray-30 px-2 py-1 text-xs font-medium text-coolgray-90 hover:bg-coolgray-10 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        열기
                      </button>
                    </td>
                    <td className="px-3 py-3 text-center">
                      <button
                        type="button"
                        onClick={() => deleteProject(row.doc_id, row.filename)}
                        disabled={deletingDocId === row.doc_id}
                        className="inline-flex h-7 w-7 items-center justify-center rounded border border-[#ff8389] text-[#da1e28] hover:bg-[#fff1f1] disabled:opacity-50"
                        aria-label={`${row.filename} 삭제`}
                      >
                        X
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-alert">{error}</p>}

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4">
          <div className="w-full max-w-3xl rounded-xl bg-white shadow-xl">
            <div className="flex items-center justify-between border-b border-coolgray-20 px-4 py-3">
              <h3 className="text-base font-bold text-coolgray-90">{modalTitle}</h3>
              <button
                type="button"
                onClick={() => setModalOpen(false)}
                className="rounded border border-coolgray-30 px-2 py-1 text-xs text-coolgray-90 hover:bg-coolgray-10"
              >
                닫기
              </button>
            </div>
            <div className="max-h-[70vh] overflow-auto whitespace-pre-wrap px-4 py-4 text-sm text-coolgray-90 leading-7">
              {modalLines.map((line, idx) =>
                line.trim() ? (
                  <div key={`${idx}-${line.slice(0, 12)}`}>
                    <StyledLine text={line} />
                  </div>
                ) : (
                  <div key={`blank-${idx}`} className="h-4" aria-hidden="true" />
                ),
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
