import { FileQuestion } from "lucide-react";
import { HashRouter, Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { EmptyState } from "./components/States";
import { ToastProvider } from "./components/Toast";
import { ActionsPage } from "./pages/ActionsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DocumentPage } from "./pages/DocumentPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { ExportPage } from "./pages/ExportPage";
import { ImportPage } from "./pages/ImportPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SettingsPage } from "./pages/SettingsPage";

function NotFound() {
  return (
    <div className="page">
      <EmptyState icon={FileQuestion} title="Page not found" action={<Link to="/" className="btn btn-sm">Go to the dashboard</Link>} />
    </div>
  );
}

// Hash routing keeps deep links working whatever the backend does with unknown paths under "/".
export function App() {
  return (
    <ToastProvider>
      <HashRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="import" element={<ImportPage />} />
            <Route path="documents" element={<DocumentsPage />} />
            <Route path="documents/:id" element={<DocumentPage />} />
            <Route path="review" element={<ReviewPage />} />
            <Route path="actions" element={<ActionsPage />} />
            <Route path="export" element={<ExportPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </HashRouter>
    </ToastProvider>
  );
}
