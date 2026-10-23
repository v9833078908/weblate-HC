import type {
  ProducerUploadAnalysis,
  ProducerUploadAnswers,
} from "../../api/client";

export type GateMessage = ProducerUploadAnalysis["errors"][number];
export type UploadAnalysisBody = ProducerUploadAnalysis["analysis"];
export type AnalyzedSheet = NonNullable<
  UploadAnalysisBody["loc_kit"]
>["files"][number]["sheets"][number];

export function analysisBody(draft: ProducerUploadAnalysis): UploadAnalysisBody {
  return draft.analysis;
}

export function gateMessages(value: unknown): GateMessage[] {
  return Array.isArray(value) ? (value as GateMessage[]) : [];
}

export function completeAnswers(
  draft: ProducerUploadAnalysis,
): ProducerUploadAnswers {
  const stored = (draft.answers ?? {}) as Partial<ProducerUploadAnswers>;
  return {
    selected_files: stored.selected_files ?? [],
    selected_sheets: stored.selected_sheets ?? [],
    source_language: stored.source_language ?? null,
    included_target_languages: stored.included_target_languages ?? [],
    column_roles: stored.column_roles ?? [],
    duplicate_policy: stored.duplicate_policy ?? "separate-components",
    profile_answers: stored.profile_answers ?? {},
  };
}

export function selectedKey(file: string, sheet: string): string {
  return `${file}\u0000${sheet}`;
}
