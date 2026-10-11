import { CircleCheck, Smartphone, Wifi, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiFailure, cancelPhoneSession, errorMessage, getPhoneSession, startPhonePairing } from "../api/client";
import { dataOf, useNow, useResource } from "../api/hooks";
import type { CaptureSession, PhonePairing } from "../api/types";
import { useSystemStatus } from "../components/Layout";
import { useToast } from "../components/Toast";

type PhoneState =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "disabled" }
  | { kind: "active"; pairing: PhonePairing }
  | { kind: "completed"; session: CaptureSession }
  | { kind: "ended"; reason: "expired" | "cancelled" }
  | { kind: "error"; message: string };

const POLL_MS = 2000;

function countdown(expiresAt: string | null, now: Date): string | null {
  if (!expiresAt) return null;
  const seconds = Math.max(0, Math.round((new Date(expiresAt).getTime() - now.getTime()) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function PhoneCapture({ onImported }: { onImported: () => void }) {
  const status = useSystemStatus();
  const [state, setState] = useState<PhoneState>({ kind: "idle" });
  const toast = useToast();

  const onEnded = (session: CaptureSession) => {
    if (session.status === "completed") {
      setState({ kind: "completed", session });
      toast.push("accent", "Pages received from your phone and imported.");
      onImported();
    } else if (session.status === "expired" || session.status === "cancelled") {
      setState({ kind: "ended", reason: session.status });
    }
  };

  const start = async () => {
    setState({ kind: "starting" });
    try {
      setState({ kind: "active", pairing: await startPhonePairing() });
    } catch (err) {
      if (err instanceof ApiFailure && err.code === "phone_disabled") setState({ kind: "disabled" });
      else setState({ kind: "error", message: errorMessage(err, "Could not start pairing.") });
    }
  };

  const cancel = async (id: string) => {
    setState({ kind: "ended", reason: "cancelled" });
    try {
      await cancelPhoneSession(id);
    } catch {
    }
  };

  if (state.kind === "disabled" || (status !== null && !status.phone_enabled && state.kind === "idle")) {
    return <PhoneDisabled />;
  }

  switch (state.kind) {
    case "active":
      return <ActivePairing pairing={state.pairing} onCancel={() => void cancel(state.pairing.session.id)} onEnded={onEnded} />;
    case "completed":
      return (
        <div className="phone-done" role="status">
          <CircleCheck size={28} aria-hidden="true" />
          <p className="empty-title">Received — document imported</p>
          <div className="row gap-1 center">
            {state.session.document_id && (
              <Link className="btn" to={`/documents/${state.session.document_id}`}>
                Open document
              </Link>
            )}
            <button type="button" className="btn btn-primary" onClick={() => void start()}>
              Pair again
            </button>
          </div>
        </div>
      );
    case "idle":
    case "starting":
    case "ended":
    case "error":
      return (
        <div className="phone-start">
          <Smartphone size={28} strokeWidth={1.5} aria-hidden="true" />
          <p>
            Photograph paperwork with your phone. The phone uploads straight to this computer over your local network; nothing
            passes through a cloud service.
          </p>
          {state.kind === "ended" && <p className="muted">{state.reason === "expired" ? "The pairing expired." : "Pairing cancelled."}</p>}
          {state.kind === "error" && (
            <p className="error-text" role="alert">
              {state.message}
            </p>
          )}
          <button type="button" className="btn btn-primary" disabled={state.kind === "starting"} onClick={() => void start()}>
            <Smartphone size={15} aria-hidden="true" /> {state.kind === "starting" ? "Starting…" : "Start pairing"}
          </button>
        </div>
      );
    default: {
      const exhaustive: never = state;
      return exhaustive;
    }
  }
}

interface ActivePairingProps {
  pairing: PhonePairing;
  onCancel: () => void;
  onEnded: (session: CaptureSession) => void;
}

function ActivePairing({ pairing, onCancel, onEnded }: ActivePairingProps) {
  const now = useNow(1000);
  const { id } = pairing.session;
  const { state } = useResource(`phone-session:${id}`, (signal) => getPhoneSession(id, signal), {
    poll: (session) => (session === null || session.status === "active" ? POLL_MS : null),
  });
  const session = dataOf(state);
  useEffect(() => {
    if (session !== null && session.status !== "active") onEnded(session);
  }, [session, onEnded]);
  const remaining = countdown(pairing.session.expires_at, now);
  return (
    <div className="phone-pairing">
      <img className="qr" src={`data:image/svg+xml;utf8,${encodeURIComponent(pairing.qr_svg)}`} alt="QR code that opens the capture page on your phone" width={220} height={220} />
      <div className="stack">
        <p className="label-strong">Scan with your phone camera</p>
        <p>
          Or open <code className="selectable">{pairing.url}</code>
        </p>
        <div>
          <p className="muted small">Confirm this code matches the one on your phone</p>
          <p className="pair-code num" aria-label={`Pairing code ${pairing.code.split("").join(" ")}`}>
            {pairing.code}
          </p>
        </div>
        <p className="muted" aria-live="off">
          {remaining ? `Expires in ${remaining}` : "Single-use link"} · waiting for pages…
        </p>
        <p className="hint">
          <Wifi size={14} aria-hidden="true" /> Phone and computer must be on the same network. The link works once.
        </p>
        <div>
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            <X size={15} aria-hidden="true" /> Cancel pairing
          </button>
        </div>
      </div>
    </div>
  );
}

function PhoneDisabled() {
  return (
    <div className="phone-start">
      <Smartphone size={28} strokeWidth={1.5} aria-hidden="true" />
      <p className="empty-title">Phone capture is turned off</p>
      <p>
        To enable it, set <code>VAULTAI_PHONE_ENABLED=true</code> in <code>.env</code> and restart VaultAI.
      </p>
      <p className="muted">
        This opens a second listener on your local network (port <code>VAULTAI_PHONE_PORT</code>, default 8766) that only
        accepts private and link-local addresses. Each pairing is a single-use link with a short expiry. Uploads go directly to
        this computer.
      </p>
    </div>
  );
}
