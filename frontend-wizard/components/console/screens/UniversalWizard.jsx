'use client'

import { UniversalWizard } from '@/components/console/wizard2/UniversalWizard'

export function UniversalWizardScreen({ project, navigate }) {
  return <UniversalWizard project={project} navigate={navigate} />
}
