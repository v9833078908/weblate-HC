// Typed API boundary (JSDoc). This is the single contract the console consumes.
// Live mode must compile against these same shapes; only src/api/client.js knows URLs.

/**
 * @typedef {Object} Me
 * @property {string} username
 * @property {string} full_name
 */

/**
 * @typedef {Object} LanguageStatus
 * @property {string} code
 * @property {string} name
 * @property {number} strings
 * @property {'ready'|'decisions'|'translating'} status
 * @property {number} decisions
 * @property {boolean} in_kit
 */

/**
 * @typedef {Object} ContentItem
 * @property {string} id
 * @property {'loc-kit'|'glossary'|'store'} type
 * @property {string} label
 * @property {string} [store]
 * @property {number} [keys]
 * @property {number} [terms]
 */

/**
 * @typedef {Object} ProjectSummary
 * @property {string} slug
 * @property {string} name
 * @property {boolean} localized
 * @property {number} ready_count
 * @property {number} language_count
 * @property {number} decisions_count
 * @property {number} blocking_count
 * @property {string} spend_month
 * @property {string} month_label
 * @property {{status:string, kind:string, when:string, id:number}} [last_run]
 */

/**
 * @typedef {Object} Project
 * @property {string} slug
 * @property {string} name
 * @property {boolean} localized
 * @property {string} source_language
 * @property {number} ready_count
 * @property {number} language_count
 * @property {number} decisions_count
 * @property {number} blocking_count
 * @property {string} spend_month
 * @property {string} month_label
 * @property {LanguageStatus[]} languages
 * @property {ContentItem[]} contents
 */

/**
 * @typedef {Object} Decision
 * @property {string} unit_id
 * @property {string} key
 * @property {string} language
 * @property {string} language_name
 * @property {string} source
 * @property {string} target
 * @property {string} [back_translation]
 * @property {string} reason
 * @property {'blocking'|'judge_critical'|'judge_note'} kind
 * @property {'critical'|'major'|'minor'} [severity]
 * @property {string} content
 * @property {{lang:string, from:number, to:number}[]} [glossary]
 * @property {{attempt:number, kind:string, note:string}[]} history
 * @property {string} advanced_url
 */

/**
 * @typedef {Object} Run
 * @property {number} id
 * @property {'translate'|'judge'} kind
 * @property {'queued'|'running'|'stalled'|'completed'|'error'} status
 * @property {string} title
 * @property {string} started
 * @property {string} [finished]
 * @property {string} elapsed
 * @property {{name:string, status:string, detail?:string, sub?:{name:string,status:string}[]}[]} stages
 * @property {{language:string, model:string, usd:string}[]} cost
 * @property {{passed:number, notes:number, decisions:number}} [outcome]
 * @property {string} [error]
 */

export {};
