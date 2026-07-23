/**
 * 이지리드 2단 레이아웃 — 양식: <소제목> + 항목마다 왼쪽 그림 / 오른쪽 본문.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import type { ImageCatalogItem, ImagePlacement } from "../api/client";
import { StyledLine } from "./BoldText";
import { DEFAULT_FONT_SIZE, clampFontSize, htmlToMarkers, markersToHtml } from "../utils/richText";
import {
  alignPlacementsToItems,
  alignPlacementsOnePerSection,
  sectionsToTranslationText,
  parseSectionItems,
  parseTranslationSections,
  mergeWithStandardClosing,
  splitStandardClosing,
  findSectionForLineIndex,
  type TranslationItem,
  type TranslationSection,
} from "../utils/translationSections";

export const IMAGE_DRAG_MIME = "application/x-easyread-image";

function AutoSizeTextarea({
  value,
  onChange,
  disabled,
  autoFocus,
  onBlur,
  textareaRef,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
  onBlur?: (e: React.FocusEvent<HTMLTextAreaElement>) => void;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  className: string;
}) {
  const innerRef = useRef<HTMLTextAreaElement>(null);

  const setRef = (el: HTMLTextAreaElement | null) => {
    innerRef.current = el;
    if (textareaRef) {
      textareaRef.current = el;
    }
  };

  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <textarea
      ref={setRef}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      autoFocus={autoFocus}
      onBlur={onBlur}
      rows={1}
      className={`${className} overflow-hidden`}
    />
  );
}

function InlineStyledEditor({
  value,
  onChange,
  disabled,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  className: string;
}) {
  const [editing, setEditing] = useState(false);
  const [fontSize, setFontSize] = useState(DEFAULT_FONT_SIZE);
  const editorRef = useRef<HTMLDivElement>(null);
  const toEditorHtml = (raw: string) => markersToHtml(raw).replace(/\*\*/g, "");
  const [editorHtml, setEditorHtml] = useState(() => toEditorHtml(value));
  const historyRef = useRef<string[]>([]);
  const historyIndexRef = useRef(-1);
  const skipHistoryRef = useRef(false);

  useEffect(() => {
    if (editing) return;
    setEditorHtml(toEditorHtml(value));
  }, [value, editing]);

  function syncMarkersFromEditor() {
    const el = editorRef.current;
    if (!el) return;
    const nextHtml = el.innerHTML;
    setEditorHtml(nextHtml);
    if (!skipHistoryRef.current) {
      const stack = historyRef.current;
      const idx = historyIndexRef.current;
      const current = idx >= 0 ? stack[idx] : null;
      if (current !== nextHtml) {
        const nextStack = idx >= 0 ? stack.slice(0, idx + 1) : [];
        nextStack.push(nextHtml);
        historyRef.current = nextStack;
        historyIndexRef.current = nextStack.length - 1;
      }
    }
    onChange(htmlToMarkers(nextHtml));
  }

  function restoreFromHistory(nextIndex: number) {
    const stack = historyRef.current;
    if (nextIndex < 0 || nextIndex >= stack.length) return;
    const el = editorRef.current;
    if (!el) return;
    historyIndexRef.current = nextIndex;
    const nextHtml = stack[nextIndex];
    skipHistoryRef.current = true;
    el.innerHTML = nextHtml;
    setEditorHtml(nextHtml);
    onChange(htmlToMarkers(nextHtml));
    requestAnimationFrame(() => {
      skipHistoryRef.current = false;
      focusEditor();
    });
  }

  function focusEditor() {
    const el = editorRef.current;
    if (!el) return;
    el.focus();
  }

  function toggleBold() {
    const el = editorRef.current;
    if (!el || disabled) return;
    focusEditor();
    document.execCommand("bold");
    syncMarkersFromEditor();
  }

  function applyUndo() {
    if (disabled) return;
    restoreFromHistory(historyIndexRef.current - 1);
  }

  function applyRedo() {
    if (disabled) return;
    restoreFromHistory(historyIndexRef.current + 1);
  }

  function applyFontSize(size: number) {
    const el = editorRef.current;
    if (!el || disabled) return;
    const clamped = clampFontSize(size);
    setFontSize(clamped);
    focusEditor();

    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    if (!el.contains(range.commonAncestorContainer)) return;

    const extracted = range.extractContents();
    const span = document.createElement("span");
    span.dataset.fontPt = String(clamped);
    span.style.fontSize = `${clamped}px`;
    span.appendChild(extracted);
    range.insertNode(span);

    selection.removeAllRanges();
    const nextRange = document.createRange();
    nextRange.selectNodeContents(span);
    selection.addRange(nextRange);

    syncMarkersFromEditor();
  }

  useEffect(() => {
    if (!editing || disabled) return;

    const handleBold = () => {
      toggleBold();
    };

    const handleFontSize = (event: Event) => {
      const detail = (event as CustomEvent<{ size?: number }>).detail;
      applyFontSize(Number(detail?.size ?? fontSize));
    };

    const handleUndo = () => {
      applyUndo();
    };

    const handleRedo = () => {
      applyRedo();
    };

    window.addEventListener("easyread:toolbar:bold", handleBold);
    window.addEventListener("easyread:toolbar:font-size", handleFontSize);
    window.addEventListener("easyread:toolbar:undo", handleUndo);
    window.addEventListener("easyread:toolbar:redo", handleRedo);

    return () => {
      window.removeEventListener("easyread:toolbar:bold", handleBold);
      window.removeEventListener("easyread:toolbar:font-size", handleFontSize);
      window.removeEventListener("easyread:toolbar:undo", handleUndo);
      window.removeEventListener("easyread:toolbar:redo", handleRedo);
    };
  }, [editing, disabled, fontSize, value]);

  if (editing && !disabled) {
    return (
      <div
        ref={editorRef}
        contentEditable
        suppressContentEditableWarning
        onKeyDown={(e) => {
          const isMod = e.ctrlKey || e.metaKey;
          if (!isMod) return;

          const key = e.key.toLowerCase();
          const isUndo = key === "z" && !e.shiftKey;
          const isRedo = key === "y" || (key === "z" && e.shiftKey);

          if (isUndo) {
            e.preventDefault();
            applyUndo();
            return;
          }

          if (isRedo) {
            e.preventDefault();
            applyRedo();
          }
        }}
        onInput={syncMarkersFromEditor}
        onBlur={(e) => {
          const next = e.relatedTarget as HTMLElement | null;
          if (next?.closest('[data-easyread-global-toolbar="true"]')) {
            requestAnimationFrame(() => focusEditor());
            return;
          }
          syncMarkersFromEditor();
          setEditing(false);
        }}
        className={`${className} overflow-hidden whitespace-pre-wrap`}
        dangerouslySetInnerHTML={{ __html: editorHtml }}
      />
    );
  }

  return (
    <button
      type="button"
      onClick={() => {
        if (!disabled) {
          const initialHtml = toEditorHtml(value);
          setEditorHtml(initialHtml);
          historyRef.current = [initialHtml];
          historyIndexRef.current = 0;
          setEditing(true);
        }
      }}
      className={`${className} text-left`}
    >
      {value.split("\n").map((line, i) => (
        <p key={i}>
          <StyledLine text={line} />
        </p>
      ))}
    </button>
  );
}

export function parseDraggedImageItem(dataTransfer: DataTransfer): ImageCatalogItem | null {
  const raw = dataTransfer.getData(IMAGE_DRAG_MIME);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ImageCatalogItem;
  } catch {
    return null;
  }
}

interface EasyReadDocumentViewProps {
  text: string;
  placements?: ImagePlacement[];
  mode: "translate" | "images";
  showImageSlots?: boolean;
  hidePlacedImages?: boolean;
  onTextChange?: (text: string) => void;
  onPlacementsChange?: (placements: ImagePlacement[]) => void;
  fill?: boolean;
  placeholder?: string;
  disabled?: boolean;
}

export function EasyReadDocumentView({
  text,
  placements = [],
  mode,
  showImageSlots = true,
  hidePlacedImages = false,
  onTextChange,
  onPlacementsChange,
  fill = false,
  placeholder = "번역 결과",
  disabled = false,
}: EasyReadDocumentViewProps) {
  const { body: documentBody, closing } = useMemo(() => splitStandardClosing(text), [text]);
  const sections = useMemo(() => parseTranslationSections(documentBody), [documentBody]);
  const alignedPlacements = useMemo(
    () =>
      mode === "images"
        ? alignPlacementsToItems(documentBody, placements)
        : alignPlacementsOnePerSection(documentBody, placements),
    [mode, documentBody, placements],
  );
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
  const docLike = mode === "translate";
  const editable = mode === "translate" && typeof onTextChange === "function";
  const placementsInteractive = mode === "images" && typeof onPlacementsChange === "function";

  function updateSections(
    updater: (current: TranslationSection[]) => TranslationSection[],
  ) {
    if (!onTextChange) return;
    const nextSections = updater(
      sections.map((section) => ({
        ...section,
        bodyLines: [...section.bodyLines],
      })),
    );
    onTextChange(mergeWithStandardClosing(sectionsToTranslationText(nextSections), closing));
  }

  function updateSectionHeading(startLineIndex: number, nextHeading: string) {
    updateSections((current) =>
      current.map((section) =>
        section.startLineIndex === startLineIndex
          ? { ...section, heading: nextHeading }
          : section,
      ),
    );
  }

  function updateItemText(sectionStartLineIndex: number, itemStartLineIndex: number, nextText: string) {
    updateSections((current) =>
      current.map((section) => {
        if (section.startLineIndex !== sectionStartLineIndex) return section;
        const items = parseSectionItems(section);
        const nextItems = items.map((item) =>
          item.startLineIndex === itemStartLineIndex
            ? { ...item, lines: nextText.split("\n") }
            : item,
        );
        return {
          ...section,
          bodyLines: nextItems.flatMap((item) => item.lines),
        };
      }),
    );
  }

  function updateClosing(nextClosing: string) {
    if (!onTextChange) return;
    onTextChange(mergeWithStandardClosing(sectionsToTranslationText(sections), nextClosing));
  }

  function setItemPlacement(
    startLineIndex: number,
    item: ImageCatalogItem,
    sectionHeading: string | null,
  ) {
    if (!onPlacementsChange) return;
    const section = findSectionForLineIndex(documentBody, startLineIndex);
    const without = placements.filter((p) => p.line_index !== startLineIndex);
    onPlacementsChange([
      ...without,
      {
        id: crypto.randomUUID(),
        image_file: item.image_file,
        line_index: startLineIndex,
        title: item.title,
        section_heading: sectionHeading ?? section?.heading ?? null,
        image_url:
          item.source_url || (item.url.startsWith("http") ? item.url : null) || null,
        auto_filled: false,
      },
    ]);
  }

  function removeItemPlacement(startLineIndex: number) {
    if (!onPlacementsChange) return;
    const target = alignedPlacements.get(startLineIndex);
    if (!target) return;
    onPlacementsChange(placements.filter((p) => p.id !== target.id));
  }

  if (!text.trim()) {
    return (
      <div
        className={`flex min-h-0 flex-1 items-center justify-center text-base text-coolgray-60 ${
          fill ? "" : "min-h-[200px]"
        }`}
      >
        {placeholder}
      </div>
    );
  }

  return (
    <div className={`${docLike ? "space-y-5" : "space-y-8"} ${fill ? "min-h-0 flex-1" : ""}`}>
      {sections.map((section, sectionIndex) => (
        <SectionBlock
          key={`${section.startLineIndex}-${sectionIndex}`}
          section={section}
          alignedPlacements={alignedPlacements}
          docLike={docLike}
          showImageSlots={showImageSlots}
          hidePlacedImages={hidePlacedImages}
          editable={editable}
          disabled={disabled}
          placementsInteractive={placementsInteractive}
          dragOverKey={dragOverKey}
          onDragOverKey={setDragOverKey}
          onHeadingChange={updateSectionHeading}
          onItemTextChange={updateItemText}
          onDropItem={(lineIndex, item) => {
            setDragOverKey(null);
            setItemPlacement(lineIndex, item, section.heading);
          }}
          onRemoveItem={removeItemPlacement}
        />
      ))}
      {closing && (
        editable ? (
          <InlineStyledEditor
            value={closing}
            onChange={updateClosing}
            disabled={disabled}
            className="min-h-[68px] w-full resize-none rounded-lg border border-coolgray-30 bg-white px-3 py-3 text-[12px] leading-[2] text-coolgray-90 outline-none focus:border-primary-60 disabled:bg-coolgray-10"
          />
        ) : (
          <p className="text-[12px] leading-[2] text-coolgray-90 pt-2">
            <StyledLine text={closing} />
          </p>
        )
      )}
    </div>
  );
}

function SectionBlock({
  section,
  alignedPlacements,
  docLike,
  showImageSlots,
  hidePlacedImages,
  editable,
  disabled,
  placementsInteractive,
  dragOverKey,
  onDragOverKey,
  onHeadingChange,
  onItemTextChange,
  onDropItem,
  onRemoveItem,
}: {
  section: TranslationSection;
  alignedPlacements: Map<number, ImagePlacement>;
  docLike: boolean;
  showImageSlots: boolean;
  hidePlacedImages: boolean;
  editable: boolean;
  disabled: boolean;
  placementsInteractive: boolean;
  dragOverKey: string | null;
  onDragOverKey: (key: string | null) => void;
  onHeadingChange: (startLineIndex: number, nextHeading: string) => void;
  onItemTextChange: (sectionStartLineIndex: number, itemStartLineIndex: number, nextText: string) => void;
  onDropItem: (lineIndex: number, item: ImageCatalogItem) => void;
  onRemoveItem: (lineIndex: number) => void;
}) {
  const items = useMemo(() => parseSectionItems(section), [section]);

  return (
    <article className={docLike ? "space-y-3" : "space-y-4"}>
      {section.heading && (
        editable ? (
          <AutoSizeTextarea
            value={section.heading}
            onChange={(next) => onHeadingChange(section.startLineIndex, next)}
            disabled={disabled}
            className={`w-full resize-none text-[20px] font-bold leading-snug text-coolgray-90 outline-none disabled:bg-transparent ${
              docLike
                ? "min-h-[38px] rounded-none border-0 bg-transparent px-0 py-0"
                : "min-h-[56px] rounded-lg border border-coolgray-30 bg-white px-3 py-3 focus:border-primary-60 disabled:bg-coolgray-10"
            }`}
          />
        ) : (
          <h3 className="leading-snug">
            <StyledLine text={section.heading} heading />
          </h3>
        )
      )}

      <div className="space-y-5">
        {items.map((item) => (
          <ItemRow
            key={item.startLineIndex}
            item={item}
            placement={alignedPlacements.get(item.startLineIndex)}
            sectionStartLineIndex={section.startLineIndex}
            docLike={docLike}
            showImageSlots={showImageSlots}
            hidePlacedImages={hidePlacedImages}
            editable={editable}
            disabled={disabled}
            placementsInteractive={placementsInteractive}
            dragOver={dragOverKey === String(item.startLineIndex)}
            onDragEnter={() => onDragOverKey(String(item.startLineIndex))}
            onDragLeave={() => onDragOverKey(null)}
            onTextChange={onItemTextChange}
            onDrop={(catalogItem) => onDropItem(item.startLineIndex, catalogItem)}
            onRemove={() => onRemoveItem(item.startLineIndex)}
          />
        ))}
      </div>
    </article>
  );
}

function ItemRow({
  item,
  placement,
  sectionStartLineIndex,
  docLike,
  showImageSlots,
  hidePlacedImages,
  editable,
  disabled,
  placementsInteractive,
  dragOver,
  onDragEnter,
  onDragLeave,
  onTextChange,
  onDrop,
  onRemove,
}: {
  item: TranslationItem;
  placement?: ImagePlacement;
  sectionStartLineIndex: number;
  docLike: boolean;
  showImageSlots: boolean;
  hidePlacedImages: boolean;
  editable: boolean;
  disabled: boolean;
  placementsInteractive: boolean;
  dragOver: boolean;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onTextChange: (sectionStartLineIndex: number, itemStartLineIndex: number, nextText: string) => void;
  onDrop: (item: ImageCatalogItem) => void;
  onRemove: () => void;
}) {
  const textContent = editable ? (
    <InlineStyledEditor
      value={item.lines.join("\n")}
      onChange={(next) => onTextChange(sectionStartLineIndex, item.startLineIndex, next)}
      disabled={disabled}
      className={`w-full resize-none text-[12px] leading-[2] text-coolgray-90 outline-none ${
        docLike
          ? "min-h-[90px] rounded-none border-0 bg-transparent px-0 py-0 disabled:bg-transparent"
          : "min-h-[120px] rounded-lg border border-coolgray-30 bg-white px-3 py-3 focus:border-primary-60 disabled:bg-coolgray-10"
      }`}
    />
  ) : (
    <div className="min-w-0 text-[12px] leading-[2] text-coolgray-90 space-y-1">
      {item.lines.map((line, i) => (
        <p key={i}>
          <StyledLine text={line} />
        </p>
      ))}
    </div>
  );

  const shouldRenderImageSlot = showImageSlots;

  if (!shouldRenderImageSlot) {
    return <div>{textContent}</div>;
  }

  return (
    <div className="grid grid-cols-[minmax(120px,32%)_1fr] gap-4 items-start">
      <ImageSlot
        placement={placement}
        docLike={docLike}
        hidePlacedImages={hidePlacedImages}
        interactive={placementsInteractive}
        dragOver={dragOver}
        onDragEnter={onDragEnter}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onRemove={onRemove}
      />
      {textContent}
    </div>
  );
}

function ImageSlot({
  placement,
  docLike,
  hidePlacedImages,
  interactive,
  dragOver,
  onDragEnter,
  onDragLeave,
  onDrop,
  onRemove,
}: {
  placement?: ImagePlacement;
  docLike: boolean;
  hidePlacedImages: boolean;
  interactive: boolean;
  dragOver: boolean;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDrop: (item: ImageCatalogItem) => void;
  onRemove: () => void;
}) {
  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    onDragEnter();
  }

  function handleDrop(e: DragEvent) {
    e.preventDefault();
    onDragLeave();
    const item = parseDraggedImageItem(e.dataTransfer);
    if (item) onDrop(item);
  }

  const url = placement
    ? placement.image_url?.startsWith("http")
      ? placement.image_url
      : `/images/${placement.image_file}`
    : null;

  const previewSlotPlaceholder = !interactive && hidePlacedImages;

  return (
    <div
      onDragOver={interactive ? handleDragOver : undefined}
      onDragLeave={interactive ? onDragLeave : undefined}
      onDrop={interactive ? handleDrop : undefined}
      className={`relative rounded-lg border min-h-[120px] flex items-center justify-center p-2 ${
        docLike && !interactive
          ? placement
            ? "border-transparent bg-transparent"
            : "border-transparent bg-transparent text-coolgray-50"
          :
        placement
          ? "border-coolgray-30 bg-[#f5f0e8]"
          : interactive && dragOver
            ? "border-primary-60 border-dashed bg-primary-60/5 text-primary-60"
            : "border-dashed border-coolgray-40 bg-[#f5f0e8] text-coolgray-60"
      } ${interactive && dragOver && placement ? "ring-2 ring-primary-60 ring-offset-1" : ""}`}
    >
      {previewSlotPlaceholder ? (
        <div className="h-32 w-full rounded border border-dashed border-coolgray-40 bg-[#f5f0e8] flex items-center justify-center text-xs text-coolgray-60">
          그림 영역
        </div>
      ) : placement && url ? (
        <>
          <img
            src={url}
            alt={placement.title || "시각자료"}
            className="max-h-32 w-full object-contain pointer-events-none"
          />
          {interactive && (
            <button
              type="button"
              onClick={onRemove}
              className="absolute top-1 right-1 size-6 rounded-full bg-white/90 border border-coolgray-30 text-coolgray-60 hover:text-alert text-sm leading-none"
              aria-label="그림 제거"
            >
              ×
            </button>
          )}
        </>
      ) : (
        <span className="text-sm text-center px-2 pointer-events-none">
          {interactive ? (
            <>
              그림 DB에서
              <br />
              드래그하여 배치
            </>
          ) : (
            <>
              그림이 들어갈
              <br />
              자리입니다
            </>
          )}
        </span>
      )}
    </div>
  );
}

/** 그림 DB 카드 — 드래그 소스 */
export function DraggableCatalogItem({
  item,
  children,
}: {
  item: ImageCatalogItem;
  children: ReactNode;
}) {
  return (
    <li
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(IMAGE_DRAG_MIME, JSON.stringify(item));
        e.dataTransfer.effectAllowed = "copy";
      }}
      className="rounded border border-coolgray-20 p-2 bg-white cursor-grab active:cursor-grabbing hover:border-primary-60 transition-colors"
    >
      {children}
    </li>
  );
}
