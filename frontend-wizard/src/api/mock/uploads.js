// Mock upload/analysis engine for the localization wizard.
// Scenario is chosen by the picked file name in step 1 (see §6 fixtures).
// Draft state persists in sessionStorage so a reload returns to the current step.

const KEY = 'hcgl:drafts'
const EXT = 'hcgl:extractions'

function load(k) { try { return JSON.parse(sessionStorage.getItem(k) || '{}') } catch { return {} } }
function save(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)) } catch {} }

function scenarioFor(fileName = '') {
  const f = fileName.toLowerCase()
  if (f.includes('utf16')) return 'utf16'
  if (f.includes('workbook') || f.includes('3-sheet')) return 'multi_sheet'
  if (f.includes('positional')) return 'positional'
  if (f.includes('en-sparse')) return 'en_sparse'
  return 'happy'
}

const BASE_COLUMNS = [
  { header: 'key', role: 'key', fill: 100 },
  { header: 'Id', role: 'service', renamed_to: 'Unity legacy ID', ambiguous: ['id'], language_shaped: true, service_policy: 'keep', fill: 100 },
  { header: 'Character', role: 'character', fill: 34 },
  { header: 'Russian', role: 'language', language: 'ru', renamed_to: 'ru', fill: 100 },
  { header: 'English', role: 'language', language: 'en', renamed_to: 'en', fill: 97 },
  { header: 'de', role: 'language', language: 'de', fill: 96 },
  { header: 'Portugal', role: 'language', language: 'pt_BR', ambiguous: ['pt', 'pt_BR'], fill: 96 },
  { header: 'tr', role: 'language', language: 'tr', low_fill: true, fill: 2 },
  { header: 'ja', role: 'language', language: 'ja', fill: 95 },
  { header: 'ko', role: 'language', language: 'ko', fill: 95 },
  { header: 'Chinese', role: 'language', language: 'zh_Hans', ambiguous: ['zh_Hans', 'zh_Hant'], fill: 94 },
  { header: 'Explanation', role: 'explanation', fill: 11 },
]

function baseAnalysis(scenario, id) {
  const a = {
    id,
    scenario,
    format: { kind: 'XLSX', sheet: 'Strings', encoding: 'UTF-8', delimiter: null, sheets: null },
    columns: JSON.parse(JSON.stringify(BASE_COLUMNS)),
    header_map: [{ from: 'Russian', to: 'ru' }, { from: 'English', to: 'en' }],
    rows: { total: 3864, importable: 3830, quarantined_by_reason: { empty_all: 17, duplicate_exact: 9, duplicate_conflict: 3, empty_key: 5 }, empty_targets: 1240 },
    source_language: null,
    explanation_language: 'source',
    source_conflict: null,
    stop: null, // {kind, ...} step-1 stop states
    note: null,
    answers: {},
    gate: null,
  }

  if (scenario === 'utf16') {
    a.format = { kind: 'TSV', encoding: 'UTF-16LE', delimiter: '\t', bom: true, sheet: null, sheets: null }
    a.note = 'Расширение файла — .txt, но по содержимому это TSV в UTF-16LE с BOM. Прочитали корректно.'
  }
  if (scenario === 'multi_sheet') {
    a.format = { kind: 'XLSX', sheet: null, sheets: [{ name: 'Strings', rows: 3120 }, { name: 'Dialogues', rows: 744 }, { name: 'Meta', rows: 12 }] }
    a.stop = { kind: 'sheet_choice' }
  }
  if (scenario === 'positional') {
    a.columns = [
      { header: 'A', role: 'key', fill: 100, note: 'ключи совпадают с английским текстом' },
      { header: 'Russian', role: 'language', language: 'ru', renamed_to: 'ru', fill: 100 },
      { header: 'English', role: 'language', language: 'en', renamed_to: 'en', fill: 100 },
    ]
    a.rows = { total: 1512, importable: 1512, quarantined_by_reason: {}, empty_targets: 0 }
  }
  if (scenario === 'en_sparse') {
    a.columns = a.columns.map((c) => (c.header === 'English' ? { ...c, fill: 41 } : c))
  }
  return a
}

const PRESET = ['en', 'de', 'fr', 'es', 'pt_BR', 'tr', 'ja', 'ko', 'zh_Hans']

function resolvedLanguages(a) {
  const ans = a.answers.languages // array of {code, include, resolved_as, source}
  if (Array.isArray(ans)) {
    return ans.filter((l) => l.include !== false).map((l) => l.resolved_as || l.code).filter(Boolean)
  }
  // default from columns
  const out = []
  a.columns.filter((c) => c.role === 'language').forEach((c) => {
    if (c.low_fill) return
    let code = c.language
    if (c.ambiguous) return // needs resolution
    if (code && code !== a.source_language) out.push(code)
  })
  return out
}

function recomputeGate(a) {
  const source = a.source_language
  a.source_conflict = null

  if (a.scenario === 'en_sparse' && source === 'en') {
    a.source_conflict = { chosen: 'en', fill: 41, rows: 1584 }
    a.gate = { ready: false, imported: 0, skipped: 0, source: 'en', languages: [], explanations: 0,
      errors: [{ row: '—', reason: 'Колонка en заполнена на 41% — как оригинал не годится' }], questions: [] }
    return a
  }
  if (!source) { a.gate = null; return a }

  const langs = resolvedLanguages(a).filter((c) => c !== source)
  const dupPolicy = a.answers.duplicates_policy || 'quarantine'
  const imported = a.rows.importable
  a.gate = {
    ready: dupPolicy !== 'stop',
    imported,
    skipped: 0,
    source,
    languages: langs,
    explanations: 412,
    errors: dupPolicy === 'stop' ? [{ row: '141', reason: 'Ожидается исправленный кит от разработчиков' }] : [],
    questions: [
      { term: 'Dead Shell', kind: 'same_in_all_languages' },
      { term: '+50% HP', kind: 'same_in_all_languages' },
    ],
  }
  return a
}

// ---------- draft store ----------
export function createDraft(fileName) {
  const drafts = load(KEY)
  const id = 'up-' + Math.random().toString(36).slice(2, 8)
  const a = recomputeGate(baseAnalysis(scenarioFor(fileName), id))
  a.file_name = fileName
  drafts[id] = a
  save(KEY, drafts)
  return a
}

export function getDraft(id) {
  const drafts = load(KEY)
  return drafts[id] || null
}

export function patchDraft(id, patch) {
  const drafts = load(KEY)
  const a = drafts[id]
  if (!a) return null
  if (patch.source_language !== undefined) a.source_language = patch.source_language
  if (patch.explanation_language !== undefined) a.explanation_language = patch.explanation_language
  if (patch.sheet !== undefined) { a.format.sheet = patch.sheet; a.stop = null }
  if (patch.columns) a.columns = patch.columns
  if (patch.languages) a.answers.languages = patch.languages
  if (patch.duplicates_policy) a.answers.duplicates_policy = patch.duplicates_policy
  if (patch.split) a.answers.split = patch.split
  recomputeGate(a)
  drafts[id] = a
  save(KEY, drafts)
  return a
}

export function deleteDraft(id) {
  const drafts = load(KEY)
  delete drafts[id]
  save(KEY, drafts)
}

// ---------- extraction ----------
const STAGES = ['Читаем лок-кит', 'Ищем термины и контекст', 'Сравниваем с глоссарием', 'Готовим предложения']

export function startExtraction() {
  const id = 'ex-' + Math.random().toString(36).slice(2, 8)
  // keep only the current extraction to avoid stale progress across reviews
  save(EXT, { [id]: { id, start: Date.now(), total_rows: 3830 } })
  return { id, status: 'queued', stage: null }
}

export function getExtraction(id) {
  const ex = load(EXT)
  const e = ex[id]
  if (!e) return { id, status: 'error', error: 'not_found' }
  const elapsed = (Date.now() - e.start) / 1000
  const perStage = 1.6
  if (elapsed < 0.8) return { id, status: 'queued', stage: null }
  const idx = Math.floor((elapsed - 0.8) / perStage)
  if (idx >= STAGES.length) return { id, status: 'done', stage: 'Готовим предложения', stage_index: STAGES.length - 1, processed_rows: e.total_rows, total_rows: e.total_rows }
  const processed = Math.min(e.total_rows, Math.round(((elapsed - 0.8) / (perStage * STAGES.length)) * e.total_rows))
  return { id, status: 'running', stage: STAGES[idx], stage_index: idx, processed_rows: processed, total_rows: e.total_rows }
}

export { PRESET }
