import { Link } from "react-router-dom";

import { t } from "../i18n";

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#console-content">
        {t("Skip to content")}
      </a>
      <header className="topbar">
        <Link className="brand" to="/projects">
          HCGameLoc
        </Link>
        <nav aria-label={t("Primary navigation")}>
          <Link className="topbar-link" to="/projects">
            {t("Projects")}
          </Link>
        </nav>
      </header>
      <main className="content" id="console-content">
        {children}
      </main>
    </div>
  );
}
