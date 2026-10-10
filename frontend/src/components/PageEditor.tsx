import { ArrowLeft, ArrowRight, Camera, RotateCcw, RotateCw, Trash2 } from "lucide-react";
import { scanPageImageUrl } from "../api/client";
import type { Box, PageEdit } from "../api/types";
import { bboxToStyle, cropToInsets, type Insets, insetsToCrop } from "../lib/geometry";

export type Rotation = NonNullable<PageEdit["rotation"]>;

export interface EditablePage {
  id: number;
  rotation: Rotation;
  /** Normalized crop in the rotated (displayed) frame, or null for the full page. */
  crop: Box | null;
  /** Bumped on retake so the browser fetches the new image. */
  version: number;
}

export interface PageSize {
  width: number;
  height: number;
}

const NEXT_ROTATION: Record<Rotation, { cw: Rotation; ccw: Rotation }> = {
  0: { cw: 90, ccw: 270 },
  90: { cw: 180, ccw: 0 },
  180: { cw: 270, ccw: 90 },
  270: { cw: 0, ccw: 180 },
};

interface RotatedPageProps {
  src: string;
  size: PageSize;
  rotation: Rotation;
  crop: Box | null;
  alt: string;
}

/** Shows the page as it will be assembled: rotated, with the area outside the crop shaded. */
function RotatedPage({ src, size, rotation, crop, alt }: RotatedPageProps) {
  const sideways = rotation === 90 || rotation === 270;
  const aspect = sideways ? `${size.height} / ${size.width}` : `${size.width} / ${size.height}`;
  // A sideways image must be as wide as the frame is tall once it is turned.
  const width = sideways ? `${(size.width / size.height) * 100}%` : "100%";
  return (
    <div className="rotated" style={{ aspectRatio: aspect }}>
      <img src={src} alt={alt} draggable={false} style={{ width, transform: `translate(-50%, -50%) rotate(${rotation}deg)` }} />
      {crop && <div className="crop-window" style={bboxToStyle(crop)} aria-hidden="true" />}
    </div>
  );
}

const SIDES = ["left", "top", "right", "bottom"] as const satisfies readonly (keyof Insets)[];
const SIDE_LABEL: Record<keyof Insets, string> = { left: "Left", top: "Top", right: "Right", bottom: "Bottom" };

interface PageEditorProps {
  sessionId: string;
  pages: readonly EditablePage[];
  sizes: ReadonlyMap<number, PageSize>;
  selectedId: number | null;
  busy: boolean;
  onSelect: (id: number) => void;
  onChange: (pages: EditablePage[]) => void;
  onRetake: (id: number) => void;
  onDelete: (id: number) => void;
}

const FALLBACK_SIZE: PageSize = { width: 8.5, height: 11 };

export function PageEditor({ sessionId, pages, sizes, selectedId, busy, onSelect, onChange, onRetake, onDelete }: PageEditorProps) {
  const index = pages.findIndex((p) => p.id === selectedId);
  const page = pages[index];
  const src = (p: EditablePage) => `${scanPageImageUrl(sessionId, p.id)}?v=${p.version}`;

  const update = (patch: Partial<Omit<EditablePage, "id">>) => {
    if (!page) return;
    onChange(pages.map((p) => (p.id === page.id ? { ...p, ...patch } : p)));
  };

  const move = (delta: -1 | 1) => {
    const target = index + delta;
    if (!page || target < 0 || target >= pages.length) return;
    const next = [...pages];
    next.splice(index, 1);
    next.splice(target, 0, page);
    onChange(next);
  };

  const insets = cropToInsets(page?.crop ?? null);
  const setInset = (side: keyof Insets, pct: number) => update({ crop: insetsToCrop({ ...insets, [side]: pct / 100 }) });

  return (
    <div className="page-editor">
      <ol className="thumb-strip" aria-label="Scanned pages in assembly order">
        {pages.map((p, i) => (
          <li key={p.id}>
            <button type="button" className="thumb" aria-pressed={p.id === selectedId} aria-label={`Page ${i + 1}`} onClick={() => onSelect(p.id)}>
              <RotatedPage src={src(p)} size={sizes.get(p.id) ?? FALLBACK_SIZE} rotation={p.rotation} crop={p.crop} alt="" />
              <span className="thumb-label num">{i + 1}</span>
            </button>
          </li>
        ))}
      </ol>

      {page && (
        <div className="page-editor-main">
          <div className="page-editor-preview">
            <RotatedPage src={src(page)} size={sizes.get(page.id) ?? FALLBACK_SIZE} rotation={page.rotation} crop={page.crop} alt={`Scanned page ${index + 1}`} />
          </div>
          <div className="page-editor-controls">
            <p className="label-strong">Page {index + 1} of {pages.length}</p>
            <div className="row wrap gap-1">
              <button type="button" className="btn btn-sm" onClick={() => update({ rotation: NEXT_ROTATION[page.rotation].ccw })}>
                <RotateCcw size={14} aria-hidden="true" /> Rotate left
              </button>
              <button type="button" className="btn btn-sm" onClick={() => update({ rotation: NEXT_ROTATION[page.rotation].cw })}>
                <RotateCw size={14} aria-hidden="true" /> Rotate right
              </button>
            </div>
            <div className="row wrap gap-1">
              <button type="button" className="btn btn-sm" disabled={index === 0} onClick={() => move(-1)}>
                <ArrowLeft size={14} aria-hidden="true" /> Move left
              </button>
              <button type="button" className="btn btn-sm" disabled={index === pages.length - 1} onClick={() => move(1)}>
                Move right <ArrowRight size={14} aria-hidden="true" />
              </button>
            </div>
            <fieldset className="crop-controls">
              <legend>Crop edges</legend>
              {SIDES.map((side) => {
                const pct = Math.round(insets[side] * 100);
                return (
                  <label key={side} className="slider">
                    <span>{SIDE_LABEL[side]}</span>
                    <input type="range" min={0} max={45} step={1} value={pct} onChange={(e) => setInset(side, Number(e.target.value))} />
                    <span className="num">{pct}%</span>
                  </label>
                );
              })}
              <button type="button" className="btn btn-sm btn-ghost" disabled={page.crop === null} onClick={() => update({ crop: null })}>
                Reset crop
              </button>
            </fieldset>
            <div className="row wrap gap-1">
              <button type="button" className="btn btn-sm" disabled={busy} onClick={() => onRetake(page.id)}>
                <Camera size={14} aria-hidden="true" /> Retake
              </button>
              <button type="button" className="btn btn-sm btn-ghost btn-danger-text" disabled={busy} onClick={() => onDelete(page.id)}>
                <Trash2 size={14} aria-hidden="true" /> Delete page
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
