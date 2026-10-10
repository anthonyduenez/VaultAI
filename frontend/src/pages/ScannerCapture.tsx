import { FileStack, Info, ScanLine, X } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import {
  assembleScan,
  cancelScanSession,
  createScanSession,
  deleteScanPage,
  errorMessage,
  listScannerDevices,
  retakePage,
  scanPage,
} from "../api/client";
import { useResource } from "../api/hooks";
import { DOC_TYPE_LABELS, DocTypeValues, type CaptureSession, type DocType, type ScanRequest } from "../api/types";
import { type EditablePage, PageEditor, type PageSize } from "../components/PageEditor";
import { EmptyState, Loaded } from "../components/States";
import { useToast } from "../components/Toast";
import { optionalText, plural } from "../lib/format";

const DPI_OPTIONS = [150, 200, 300, 600] as const;

function reconcile(prev: readonly EditablePage[], session: CaptureSession, retakenId: number | null): EditablePage[] {
  const live = new Set(session.pages.map((p) => p.id));
  const known = new Set(prev.map((p) => p.id));
  const kept = prev
    .filter((p) => live.has(p.id))
    .map((p) => (p.id === retakenId ? { ...p, version: p.version + 1 } : p));
  const added = [...session.pages]
    .filter((p) => !known.has(p.id))
    .sort((a, b) => a.idx - b.idx)
    .map((p): EditablePage => ({ id: p.id, rotation: 0, crop: null, version: 0 }));
  return [...kept, ...added];
}

export function ScannerCapture({ onImported }: { onImported: () => void }) {
  const devices = useResource("scanner-devices", listScannerDevices);
  return (
    <Loaded resource={devices} loadingLabel="Looking for scanners…">
      {(list) =>
        list.length === 0 ? (
          <EmptyState icon={ScanLine} title="No scanner found">
            Connect a SANE-compatible scanner, or set <code>VAULTAI_SCANNER_BACKEND=mock</code> to try the flow.
            <FallbackNote />
          </EmptyState>
        ) : (
          <ScannerSession devices={list} onImported={onImported} />
        )
      }
    </Loaded>
  );
}

function FallbackNote() {
  return (
    <p className="hint">
      <Info size={14} aria-hidden="true" /> Prefer your OS scanner utility? Scan to PDF with it, then drop the PDF on the{" "}
      <strong>Files &amp; folders</strong> tab. It goes through the same checks.
    </p>
  );
}

interface ScannerSessionProps {
  devices: Awaited<ReturnType<typeof listScannerDevices>>;
  onImported: () => void;
}

function ScannerSession({ devices, onImported }: ScannerSessionProps) {
  const [deviceId, setDeviceId] = useState(devices[0]?.id ?? "");
  const [settings, setSettings] = useState<Required<ScanRequest>>({ duplex: false, dpi: 300, color: "color" });
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [pages, setPages] = useState<EditablePage[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [category, setCategory] = useState<DocType | "">("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const ids = useId();

  const device = devices.find((d) => d.id === deviceId);
  const sizes = useMemo(
    () => new Map<number, PageSize>(session?.pages.map((p) => [p.id, { width: p.width, height: p.height }]) ?? []),
    [session],
  );

  useEffect(() => {
    if (device && !device.duplex && settings.duplex) setSettings((s) => ({ ...s, duplex: false }));
  }, [device, settings.duplex]);

  const apply = (next: CaptureSession, select: "newest" | "keep", retakenId: number | null = null) => {
    setSession(next);
    setPages((prev) => reconcile(prev, next, retakenId));
    const live = next.pages.map((p) => p.id);
    const newest = [...next.pages].sort((a, b) => b.idx - a.idx)[0]?.id ?? null;
    setSelectedId((current) => (select === "newest" ? newest : current !== null && live.includes(current) ? current : (live[0] ?? null)));
  };

  const guarded = async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(errorMessage(err, "The scanner action failed."));
    } finally {
      setBusy(null);
    }
  };

  const scan = () =>
    guarded("scan", async () => {
      let active = session;
      if (!active) {
        active = await createScanSession({ device_id: deviceId });
        setSession(active);
      }
      apply(await scanPage(active.id, settings), "newest");
    });

  const reset = () => {
    setSession(null);
    setPages([]);
    setSelectedId(null);
    setName("");
    setCategory("");
  };

  const assemble = () =>
    guarded("assemble", async () => {
      if (!session) return;
      const item = await assembleScan(session.id, {
        title: optionalText(name),
        doc_type_hint: category === "" ? null : category,
        pages: pages.map(({ id, rotation, crop }) => ({ id, rotation, crop })),
      });
      toast.push("accent", `Combined ${plural(pages.length, "page")} into ${item.filename} and added it to the intake queue.`);
      reset();
      onImported();
    });

  const cancel = () =>
    guarded("cancel", async () => {
      if (session) await cancelScanSession(session.id);
      reset();
    });

  return (
    <div className="scanner">
      <div className="scanner-settings">
        <div className="field">
          <label htmlFor={`${ids}-device`}>Scanner</label>
          <select id={`${ids}-device`} className="input" value={deviceId} disabled={session !== null} onChange={(e) => setDeviceId(e.target.value)}>
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} {d.backend === "mock" ? "(simulated)" : ""}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${ids}-dpi`}>Resolution</label>
          <select id={`${ids}-dpi`} className="input" value={settings.dpi} onChange={(e) => setSettings((s) => ({ ...s, dpi: Number(e.target.value) }))}>
            {DPI_OPTIONS.map((dpi) => (
              <option key={dpi} value={dpi}>
                {dpi} DPI
              </option>
            ))}
          </select>
        </div>
        <fieldset className="field">
          <legend>Color</legend>
          <div className="segmented" role="group">
            {(["color", "gray"] as const).map((mode) => (
              <button key={mode} type="button" aria-pressed={settings.color === mode} onClick={() => setSettings((s) => ({ ...s, color: mode }))}>
                {mode === "color" ? "Color" : "Grayscale"}
              </button>
            ))}
          </div>
        </fieldset>
        <label className="checkbox" title={device?.duplex ? undefined : "This scanner does not scan both sides"}>
          <input type="checkbox" checked={settings.duplex} disabled={!device?.duplex} onChange={(e) => setSettings((s) => ({ ...s, duplex: e.target.checked }))} />
          Duplex (both sides)
        </label>
        <button type="button" className="btn btn-primary" disabled={busy !== null || deviceId === ""} onClick={() => void scan()}>
          <ScanLine size={15} aria-hidden="true" /> {busy === "scan" ? "Scanning…" : pages.length === 0 ? "Scan page" : "Scan next page"}
        </button>
      </div>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {session && pages.length > 0 ? (
        <>
          <PageEditor
            sessionId={session.id}
            pages={pages}
            sizes={sizes}
            selectedId={selectedId}
            busy={busy !== null}
            onSelect={setSelectedId}
            onChange={setPages}
            onRetake={(id) => void guarded("retake", async () => apply(await retakePage(session.id, id, settings), "keep", id))}
            onDelete={(id) => void guarded("delete", async () => apply(await deleteScanPage(session.id, id), "keep"))}
          />
          <div className="assemble-bar">
            <div className="field">
              <label htmlFor={`${ids}-name`}>Document name</label>
              <input id={`${ids}-name`} className="input" type="text" value={name} placeholder="e.g. Signed lease amendment" maxLength={200} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor={`${ids}-category`}>Category (optional)</label>
              <select id={`${ids}-category`} className="input" value={category} onChange={(e) => setCategory(DocTypeValues.find((t) => t === e.target.value) ?? "")}>
                <option value="">Detect automatically</option>
                {DocTypeValues.map((t) => (
                  <option key={t} value={t}>
                    {DOC_TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
            </div>
            <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={() => void assemble()}>
              <FileStack size={15} aria-hidden="true" />
              {busy === "assemble" ? "Combining…" : `Combine ${plural(pages.length, "page")} into PDF & import`}
            </button>
            <button type="button" className="btn btn-ghost" disabled={busy !== null} onClick={() => void cancel()}>
              <X size={15} aria-hidden="true" /> Discard scan
            </button>
          </div>
        </>
      ) : (
        <EmptyState icon={ScanLine} title="No pages scanned yet">
          Place the first page on the scanner glass or in the feeder, then choose <strong>Scan page</strong>. You can rotate,
          crop, reorder, and retake pages before combining them.
        </EmptyState>
      )}
      <FallbackNote />
    </div>
  );
}
