import { ArrowLeft, FileX } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getDocument } from "../api/client";
import { useResource } from "../api/hooks";
import { DocumentWorkspace } from "../components/DocumentWorkspace";
import { EmptyState, ErrorState, Spinner } from "../components/States";

export function DocumentPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const doc = useResource(`document:${id}`, (signal) => getDocument(id, signal), {
    poll: (d) => (d?.status === "processing" ? 1500 : null),
  });
  const back = (
    <Link to="/documents" className="back-link">
      <ArrowLeft size={14} aria-hidden="true" /> Documents
    </Link>
  );

  const { state } = doc;
  if (state.kind === "loading") {
    return (
      <div className="page">
        <div className="loading-block">
          <Spinner label="Loading document…" />
        </div>
      </div>
    );
  }
  if (state.kind === "error" && state.error.status === 404) {
    return (
      <div className="page">
        <EmptyState icon={FileX} title="Document not found" action={back}>
          It may have been deleted. The audit log still records what happened to it.
        </EmptyState>
      </div>
    );
  }
  const detail = state.data;
  if (detail === null) {
    return (
      <div className="page">
        {state.kind === "error" && <ErrorState error={state.error} onRetry={doc.reload} />}
      </div>
    );
  }
  return (
    <div className="page page-wide">
      <DocumentWorkspace detail={detail} onDetail={doc.replace} onReload={doc.reload} onDeleted={() => navigate("/documents")} nav={back} />
    </div>
  );
}
