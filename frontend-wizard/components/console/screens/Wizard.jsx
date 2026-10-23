"use client";

import { WizardShell } from "@/components/console/wizard/WizardShell";

export function WizardScreen({ project, navigate }) {
  return <WizardShell project={project} navigate={navigate} />;
}
