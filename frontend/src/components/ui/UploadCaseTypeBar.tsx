/**
 * Figma 업로드 화면 — 사건유형 선택 바.
 */
import type { DocType } from "../../api/client";

const UPLOAD_DOC_TYPES: { value: Exclude<DocType, "unknown">; label: string }[] = [
  { value: "civil", label: "민사" },
  { value: "criminal", label: "형사" },
  { value: "family", label: "가사" },
  { value: "administrative", label: "행정" },
];

interface UploadCaseTypeBarProps {
  active: DocType;
  disabled?: boolean;
  onChange: (value: Exclude<DocType, "unknown">) => void;
}

export function UploadCaseTypeBar({ active, disabled, onChange }: UploadCaseTypeBarProps) {
  const activeType = active !== "unknown" ? active : null;

  return (
    <div className="mx-5 mt-3 mb-1 flex items-center gap-3 rounded-xl border border-coolgray-20 bg-coolgray-10 h-[58px] px-4 shrink-0">
      <span className="text-lg font-bold text-coolgray-90 shrink-0">사건유형</span>
      <div className="flex flex-wrap items-center gap-2.5">
        {UPLOAD_DOC_TYPES.map(({ value, label }) => {
          const selected = activeType === value;
          return (
            <button
              key={value}
              type="button"
              disabled={disabled}
              onClick={() => onChange(value)}
              className={`h-[33px] min-w-[59px] px-4 rounded-full text-base transition-colors disabled:opacity-50 ${
                selected
                  ? "bg-primary-60 text-white border border-primary-60"
                  : "bg-white text-coolgray-90 border border-coolgray-30 hover:bg-coolgray-10"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
