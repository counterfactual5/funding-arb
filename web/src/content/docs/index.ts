/**
 * In-app documentation registry.
 *
 * To add a new article:
 * 1. Create web/src/content/docs/articles/<name>.ts (export DocArticleDef)
 * 2. Register it in DOC_ARTICLES below
 * 3. Add docs.articles.<name>.title / .desc / .tag to locale JSON files
 */

import { readmeArticle } from './articles/readme'
import { skillCliArticle } from './articles/skillCli'
import { crossIntervalArticle } from './articles/crossInterval'
import type { DocArticleDef, DocSection } from './types'

export type { DocBlock, DocSection, DocArticleDef } from './types'

export const DOC_ARTICLES: DocArticleDef[] = [
  readmeArticle,
  skillCliArticle,
  crossIntervalArticle,
]

export const DEFAULT_DOC_SLUG = DOC_ARTICLES[0]?.slug ?? 'readme'

export function findDocArticle(slug: string): DocArticleDef | undefined {
  return DOC_ARTICLES.find((a) => a.slug === slug)
}

export function getDocSections(article: DocArticleDef, locale: string): DocSection[] {
  return article.sectionsByLocale[locale] ?? article.sectionsByLocale['zh-CN'] ?? []
}

export function sectionNavItems(article: DocArticleDef, locale: string) {
  return getDocSections(article, locale).map((s) => ({ id: s.id, title: s.title }))
}
