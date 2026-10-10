import { FolderOpen, Package, ScanLine, Smartphone } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { errorMessage, importSamples, listIntake, type PickedFile, type UploadSource, uploadFiles } from "../api/client";
import { useResource } from "../api/hooks";
import type { IntakeItem } from "../api/types";
import { DropZone } from "../components/DropZone";
import { IntakeQueue, isProcessing } from "../components/IntakeQueue";
import { PageHeader } from "../components/PageHeader";
import { Loaded } from "../components/States";
import { Tabs, type TabSpec } from "../components/Tabs";
import { useToast } from "../components/Toast";
import { chunk } from "../lib/files";
import { plural } from "../lib/format";
import { PhoneCapture } from "./PhoneCapture";
import { ScannerCapture } from "./ScannerCapture";

type ImportTab = "files" | "scanner" | "phone" | "samples";

const TABS: readonly TabSpec<ImportTab>[] = [
  { id: "files", label: <><FolderOpen size={15} aria-hidden="true" /> Files &amp; folders</> },
  { id: "scanner", label: <><ScanLine size={15} aria-hidden="true" /> Scanner</> },
  { id: "phone", label: <><Smartphone size={15} aria-hidden="true" /> Phone</> },
  { id: "samples", label: <><Package size={15} aria-hidden="true" /> Demo samples</> },
];

const isImportTab = (value: string | null): value is ImportTab => TABS.some((t) => t.id === value);

// Small batches keep each request well under the server's per-request file limit and show progress.
const UPLOAD_BATCH = 25;

function describe(items: readonly IntakeItem[]): string {
  const accepted = items.filter((i) => i.preflight === "accepted").length;
  const rejected = items.filter((i) => i.preflight === "rejected").length;
  const duplicates = items.filter((i) => i.preflight === "duplicate").length;
  const parts = [`${accepted} accepted`];
  if (rejected > 0) parts.push(`${rejected} rejected (not stored)`);
  if (duplicates > 0) parts.push(`${plural(duplicates, "duplicate")} (not stored again)`);
  return parts.join(" · ");
}

export function ImportPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const tab: ImportTab = isImportTab(tabParam) ? tabParam : "files";
  const intake = useResource("intake", listIntake, { poll: (items) => (items?.some(isProcessing) ? 1500 : null) });
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [importingSamples, setImportingSamples] = useState(false);
  const toast = useToast();

  const upload = async (files: PickedFile[], source: UploadSource) => {
    const results: IntakeItem[] = [];
    setProgress({ done: 0, total: files.length });
    try {
      for (const batch of chunk(files, UPLOAD_BATCH)) {
        results.push(...(await uploadFiles(batch, source)));
        setProgress({ done: results.length, total: files.length });
        intake.reload();
      }
      toast.push("accent", `Imported ${plural(files.length, "file")}: ${describe(results)}.`);
    } catch (err) {
      toast.push("danger", errorMessage(err, "Upload failed."));
    } finally {
      setProgress(null);
      intake.reload();
    }
  };

  const loadSamples = async () => {
    setImportingSamples(true);
    try {
      const items = await importSamples();
      toast.push("accent", `Imported ${items.length} sample files: ${describe(items)}.`);
      intake.reload();
    } catch (err) {
      toast.push("danger", errorMessage(err, "Could not import the samples."));
    } finally {
      setImportingSamples(false);
    }
  };

  return (
    <div className="page">
      <PageHeader
        title="Import & Scan"
        subtitle="Bring paperwork in from files, folders, a scanner, or a phone on your local network. Files are checked before anything is stored."
      />
      <section className="card">
        <Tabs tabs={TABS} active={tab} onChange={(id) => setParams(id === "files" ? {} : { tab: id }, { replace: true })} label="Import source" />
        <div className="tab-panel" role="tabpanel">
          {tab === "files" && (
            <>
              <DropZone onFiles={(files, source) => void upload(files, source)} disabled={progress !== null} />
              {progress && (
                <p className="upload-progress" role="status">
                  Uploading {progress.done} of {progress.total} files to this device…
                  <progress max={progress.total} value={progress.done} />
                </p>
              )}
            </>
          )}
          {tab === "scanner" && <ScannerCapture onImported={intake.reload} />}
          {tab === "phone" && <PhoneCapture onImported={intake.reload} />}
          {tab === "samples" && (
            <div className="samples">
              <p>
                Load the bundled demo folder (<code>samples/demo/</code>) to walk through intake, processing, review, and export
                without using real paperwork. The samples go through the same preflight checks as any other file.
              </p>
              <button type="button" className="btn btn-primary" disabled={importingSamples} onClick={() => void loadSamples()}>
                <Package size={15} aria-hidden="true" /> {importingSamples ? "Importing…" : "Import sample folder"}
              </button>
            </div>
          )}
        </div>
      </section>

      <Loaded resource={intake} loadingLabel="Loading intake queue…">
        {(items) => <IntakeQueue items={items} onChanged={intake.reload} />}
      </Loaded>
    </div>
  );
}
