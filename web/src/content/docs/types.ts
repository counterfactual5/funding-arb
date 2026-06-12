/** Structured in-app documentation types. */

export type DocBlock =
  | { type: 'p'; text: string }
  | { type: 'formula'; lines: string[] }
  | { type: 'ul'; items: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] }
  | { type: 'callout'; variant: 'info' | 'warn'; text: string }

export type DocSection = {
  id: string
  title: string
  blocks: DocBlock[]
}

export type DocArticleDef = {
  /** URL slug, e.g. cross-interval */
  slug: string
  /** i18n key for article title */
  titleKey: string
  /** i18n key for one-line description in sidebar */
  descKey: string
  /** optional i18n key for header tag */
  tagKey?: string
  tagType?: 'success' | 'info' | 'warning' | 'default'
  sectionsByLocale: Record<string, DocSection[]>
}
