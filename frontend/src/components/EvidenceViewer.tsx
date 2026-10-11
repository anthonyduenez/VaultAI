import { ChevronLeft, ChevronRight, FileText, Image, ShieldAlert, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { pageImageUrl } from "../api/client";
import type { Box, Evidence, LineFlag, PageInfo, PageLine } from "../api/types";
import { TEXT_METHOD } from "../lib/display";
import { formatPercent } from "../lib/format";
import { bboxToStyle } from "../lib/geometry";

/** Something on the right-hand pane whose source location can be shown on the page. */
export interface EvidenceTarget {
  id: string;
  label: string;
  evidence: Evidence;
}

interface Located {
  pageNo: number;
  bbox: Box | null;
  lineId: string | null;
}

export function locateEvidence(pages: readonly PageInfo[], evidence: Evidence): Located | null {
  const findLine = (lines: readonly PageLine[]) =>
    evidence.line_id === null ? undefined : lines.find((line) => line.id === evidence.line_id);
  let page = evidence.page === null ? undefined : pages.find((p) => p.page_no === evidence.page);
  let line = page ? findLine(page.lines) : undefined;
  if (!line && evidence.line_id !== null) {
    for (const candidate of pages) {
      line = findLine(candidate.lines);
      if (line) {
        page ??= candidate;
        break;
      }
    }
  }
  if (!page) return null;
  return { pageNo: page.page_no, bbox: evidence.bbox ?? line?.bbox ?? null, lineId: line?.id ?? evidence.line_id };
}

const FLAG_LABEL: Record<LineFlag, string> = {
  hidden: "Hidden text",
  injection: "Instruction-like text",
};

interface FlaggedLine {
  pageNo: number;
  line: PageLine;
  flag: LineFlag;
  bbox: Box | null;
}

function flaggedLines(page: PageInfo): FlaggedLine[] {
  return page.lines.flatMap((line) =>
    line.flag === null ? [] : [{ pageNo: page.page_no, line, flag: line.flag, bbox: line.bbox }],
  );
}

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2] as const;

interface EvidenceViewerProps {
  docId: string;
  filename: string;
  pages: readonly PageInfo[];
  target: EvidenceTarget | null;
  onSelect: (target: EvidenceTarget) => void;
}

type ViewMode = "image" | "text";

export function EvidenceViewer({ docId, filename, pages, target, onSelect }: EvidenceViewerProps) {
  const [pageNo, setPageNo] = useState<number | null>(pages[0]?.page_no ?? null);
  const [zoomIndex, setZoomIndex] = useState(2);
  const [preferText, setPreferText] = useState(false);
  const [brokenImages, setBrokenImages] = useState<ReadonlySet<number>>(new Set());
  const highlightRef = useRef<HTMLElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);

  const setHighlight = (el: HTMLElement | null) => {
    highlightRef.current = el;
  };

  const located = useMemo(() => (target ? locateEvidence(pages, target.evidence) : null), [pages, target]);

  useEffect(() => {
    if (located) setPageNo(located.pageNo);
  }, [located]);

  useEffect(() => {
    if (pageNo === null || !pages.some((p) => p.page_no === pageNo)) setPageNo(pages[0]?.page_no ?? null);
  }, [pages, pageNo]);

  // Scrolls the stage only; scrollIntoView would also scroll the window and hide the record pane.
  const revealHighlight = () => {
    const stage = stageRef.current;
    const el = highlightRef.current;
    if (!stage || !el) return;
    const s = stage.getBoundingClientRect();
    const e = el.getBoundingClientRect();
    stage.scrollTo({
      top: stage.scrollTop + (e.top - s.top) - (s.height - e.height) / 2,
      left: stage.scrollLeft + (e.left - s.left) - (s.width - e.width) / 2,
      behavior: "smooth",
    });
  };

  useEffect(revealHighlight, [located, pageNo, preferText]);

  const quarantined = useMemo(() => pages.flatMap((page) => flaggedLines(page)), [pages]);

  const pageIndex = pages.findIndex((p) => p.page_no === pageNo);
  const page = pages[pageIndex];
  if (!page) {
    return (
      <section className="viewer" aria-label="Original document">
        <div className="viewer-empty">
          <FileText size={28} strokeWidth={1.5} aria-hidden="true" />
          <p>No pages yet. Page previews appear once the document has been read.</p>
        </div>
      </section>
    );
  }

  const canShowImage = page.has_image && !brokenImages.has(page.page_no);
  const mode: ViewMode = canShowImage && !preferText ? "image" : "text";
  const hit = located && located.pageNo === page.page_no ? located : null;
  const zoom = ZOOMS[zoomIndex] ?? 1;
  const go = (delta: number) => {
    const next = pages[pageIndex + delta];
    if (next) setPageNo(next.page_no);
  };

  return (
    <section className="viewer" aria-label="Original document">
      <div className="viewer-toolbar">
        <div className="row gap-1">
          <button type="button" className="btn btn-sm btn-icon-only" onClick={() => go(-1)} disabled={pageIndex <= 0} aria-label="Previous page">
            <ChevronLeft size={16} aria-hidden="true" />
          </button>
          <span className="viewer-page-label num" aria-live="polite">
            Page {page.page_no} of {pages.length}
          </span>
          <button type="button" className="btn btn-sm btn-icon-only" onClick={() => go(1)} disabled={pageIndex >= pages.length - 1} aria-label="Next page">
            <ChevronRight size={16} aria-hidden="true" />
          </button>
        </div>
        <div className="segmented" role="group" aria-label="Preview mode">
          <button type="button" aria-pressed={mode === "image"} disabled={!canShowImage} onClick={() => setPreferText(false)}>
            <Image size={14} aria-hidden="true" /> Page
          </button>
          <button type="button" aria-pressed={mode === "text"} onClick={() => setPreferText(true)}>
            <FileText size={14} aria-hidden="true" /> Text
          </button>
        </div>
        <div className="row gap-1">
          <button type="button" className="btn btn-sm btn-icon-only" onClick={() => setZoomIndex((z) => Math.max(0, z - 1))} disabled={mode !== "image" || zoomIndex === 0} aria-label="Zoom out">
            <ZoomOut size={16} aria-hidden="true" />
          </button>
          <button type="button" className="btn btn-sm viewer-zoom num" onClick={() => setZoomIndex(2)} disabled={mode !== "image"} title="Fit to width">
            {Math.round(zoom * 100)}%
          </button>
          <button type="button" className="btn btn-sm btn-icon-only" onClick={() => setZoomIndex((z) => Math.min(ZOOMS.length - 1, z + 1))} disabled={mode !== "image" || zoomIndex === ZOOMS.length - 1} aria-label="Zoom in">
            <ZoomIn size={16} aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="viewer-stage" ref={stageRef}>
        {mode === "image" ? (
          <div className="page-canvas" style={{ width: `${zoom * 100}%` }}>
            <img
              src={pageImageUrl(docId, page.page_no)}
              alt={`Page ${page.page_no} of ${filename}`}
              draggable={false}
              onLoad={revealHighlight}
              onError={() => setBrokenImages((prev) => new Set(prev).add(page.page_no))}
            />
            {flaggedLines(page).map(({ line, flag, bbox }) =>
              bbox ? <div key={line.id} className="bbox bbox-quarantine" style={bboxToStyle(bbox)} title={`Quarantined: ${FLAG_LABEL[flag]}`} /> : null,
            )}
            {hit?.bbox && <div ref={setHighlight} className="bbox bbox-hit" style={bboxToStyle(hit.bbox)} aria-hidden="true" />}
          </div>
        ) : (
          <ol className="text-page" aria-label={`Text of page ${page.page_no}`}>
            {page.lines.length === 0 && <li className="muted">No text was read from this page.</li>}
            {page.lines.map((line) => {
              const isHit = hit !== null && hit.lineId === line.id;
              return (
                <li key={line.id} ref={isHit ? setHighlight : undefined} className="text-line" data-hit={isHit || undefined} data-flag={line.flag ?? undefined}>
                  {line.flag && <span className="text-line-flag">{FLAG_LABEL[line.flag]} · excluded from AI</span>}
                  {line.text}
                </li>
              );
            })}
          </ol>
        )}
      </div>

      <div className="viewer-caption">
        <span className="muted">
          {TEXT_METHOD[page.text_method]}
          {page.ocr_confidence !== null && ` · OCR ${formatPercent(page.ocr_confidence)}`}
        </span>
        {target && (
          <span className="viewer-evidence">
            <strong>{target.label}</strong>
            {located === null ? (
              <span className="muted"> · no location recorded on the page</span>
            ) : (
              target.evidence.text && <q>{target.evidence.text}</q>
            )}
          </span>
        )}
      </div>

      {quarantined.length > 0 && (
        <div className="quarantine" role="note">
          <p className="quarantine-title">
            <ShieldAlert size={16} aria-hidden="true" /> Quarantined text · untrusted, excluded from AI
          </p>
          <p className="quarantine-body">
            These lines look like hidden text or instructions aimed at an AI. VaultAI shows them here as plain text and never
            passes them to the model.
          </p>
          <ul>
            {quarantined.map(({ pageNo: p, line, flag }) => (
              <li key={line.id}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onSelect({ id: `line:${line.id}`, label: FLAG_LABEL[flag], evidence: { page: p, bbox: line.bbox, line_id: line.id, text: line.text } })}
                >
                  p.{p} · {FLAG_LABEL[flag]}
                </button>
                <span className="quarantine-text">{line.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
