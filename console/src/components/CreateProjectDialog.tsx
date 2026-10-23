import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import { ProducerApiError } from "../api/client";
import { t } from "../i18n";
import { AsyncNotice } from "./AsyncNotice";

interface CreateProjectDialogProps {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string, remoteSlug?: string) => Promise<void>;
  onFind?: (name: string) => Promise<Array<{name: string; slug: string}>>;
  initialProject?: {name: string; slug: string};
}

function hasFieldError(payload: unknown, field: string): boolean {
  if (!payload || typeof payload !== "object" || !("errors" in payload)) {
    return false;
  }
  if (!Array.isArray(payload.errors)) {
    return false;
  }
  return payload.errors.some(
    (item) =>
      item !== null &&
      typeof item === "object" &&
      "attr" in item &&
      item.attr === field,
  );
}

function creationError(error: unknown): string {
  if (
    error instanceof ProducerApiError ||
    (error && typeof error === "object")
  ) {
    const status = "status" in error ? error.status : undefined;
    if (status === 400) {
      const payload = "payload" in error ? error.payload : undefined;
      if (hasFieldError(payload, "workspace")) {
        return t(
          "Create this project in Advanced Weblate to choose a workspace.",
        );
      }
      return t("Check the project name.");
    }
    if (status === 403) {
      return t("You do not have permission to create a project.");
    }
    if (status === 401) {
      return t("Your session has expired.");
    }
  }
  return t("The project could not be created. Try again.");
}

export function CreateProjectDialog({
  open,
  onClose,
  onCreate,
  onFind,
  initialProject,
}: CreateProjectDialogProps) {
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [matches, setMatches] = useState<Array<{name: string; slug: string}> | null>(null);
  const [remoteSlug, setRemoteSlug] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setName(initialProject?.name ?? "");
      setMatches(initialProject ? [initialProject] : null);
      setRemoteSlug(initialProject?.slug ?? "");
      setError(undefined);
      inputRef.current?.focus();
    }
  }, [open, initialProject]);

  if (!open) {
    return null;
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const projectName = name.trim();
    if (!projectName || pending) {
      return;
    }
    setPending(true);
    setError(undefined);
    try {
      if (onFind && matches === null) {
        const found = await onFind(projectName);
        if (found.length) { setMatches(found); return; }
      }
      if (matches?.length && !remoteSlug) return;
      await onCreate(projectName, remoteSlug || undefined);
      onClose();
    } catch (reason) {
      setError(creationError(reason));
    } finally {
      setPending(false);
    }
  };

  const handleKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" && !pending) {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    const controls = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled])",
      ) ?? [],
    );
    const first = controls.at(0);
    const last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  return (
    <div className="dialog-backdrop">
      <div
        aria-describedby="create-project-description"
        aria-labelledby="create-project-title"
        aria-modal="true"
        className="dialog"
        onKeyDown={handleKeys}
        ref={dialogRef}
        role="dialog"
      >
        <h2 id="create-project-title">{initialProject ? t("Connect project") : t("Create project")}</h2>
        <p id="create-project-description">
          {initialProject ? t("Confirm read-only connection. Existing project data will stay unchanged.") : t("Create an empty project. Localization setup comes next.")}
        </p>
        <form onSubmit={submit}>
          <label htmlFor="project-name">{t("Project name")}</label>
          <input
            autoComplete="off"
            id="project-name"
            onChange={(event) => {setName(event.target.value); setMatches(null); setRemoteSlug("");}}
            placeholder={t("For example, Pirate Ships")}
            ref={inputRef}
            value={name}
          />
          {matches?.length ? <fieldset><legend>{t("Existing Weblate projects")}</legend>
            <p>{t("Confirm read-only connection. Existing project data will stay unchanged.")}</p>
            {matches.map((match) => <label key={match.slug}><input type="radio" name="remote-project" value={match.slug} checked={remoteSlug === match.slug} onChange={() => setRemoteSlug(match.slug)} />{match.name} — {match.slug}</label>)}
          </fieldset> : null}
          {error ? <AsyncNotice kind="error">{error}</AsyncNotice> : null}
          <div className="dialog-actions">
            <button disabled={pending} onClick={onClose} type="button">
              {t("Cancel")}
            </button>
            <button disabled={pending || !name.trim() || !!matches?.length && !remoteSlug} type="submit">
              {pending ? t("Creating project…") : matches?.length ? t("Confirm connection") : t("Create")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
