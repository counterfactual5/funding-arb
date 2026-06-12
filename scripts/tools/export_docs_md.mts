#!/usr/bin/env -S npx tsx
/**
 * Export in-app docs (web/src/content/docs/articles/*.ts) to Markdown under docs/{locale}/.
 *
 * Usage (from repo root):
 *   npx tsx scripts/tools/export_docs_md.mts
 */

import { mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { DOC_ARTICLES, getDocSections } from '../../web/src/content/docs/index.ts'
import type { DocBlock, DocSection } from '../../web/src/content/docs/types.ts'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO = join(__dirname, '../..')
const DOCS_OUT = join(REPO, 'docs')

const LOCALES = ['zh-CN', 'en', 'zh-TW'] as const
type Locale = (typeof LOCALES)[number]

type LocaleFile = {
  docs: {
    articles: Record<string, { title: string; desc?: string }>
  }
}

function loadLocale(locale: Locale): LocaleFile {
  const raw = readFileSync(join(REPO, 'web/src/locales', `${locale}.json`), 'utf8')
  return JSON.parse(raw) as LocaleFile
}

function articleTitle(articleKey: string, locale: Locale, locales: LocaleFile): string {
  const key = articleKey.replace(/^docs\.articles\./, '').replace(/\.title$/, '')
  return locales.docs.articles[key]?.title ?? key
}

function blockToMd(block: DocBlock): string {
  switch (block.type) {
    case 'p':
      return `${block.text}\n`
    case 'formula':
      return '```text\n' + block.lines.join('\n') + '\n```\n'
    case 'ul':
      return block.items.map((i) => `- ${i}`).join('\n') + '\n'
    case 'callout': {
      const label = block.variant === 'warn' ? '⚠️' : 'ℹ️'
      return `> ${label} ${block.text}\n`
    }
    case 'table': {
      const header = '| ' + block.headers.join(' | ') + ' |'
      const sep = '| ' + block.headers.map(() => '---').join(' | ') + ' |'
      const rows = block.rows.map((r) => '| ' + r.join(' | ') + ' |')
      return [header, sep, ...rows].join('\n') + '\n'
    }
    default:
      return ''
  }
}

function sectionsToMd(sections: DocSection[], title: string, desc?: string): string {
  const lines: string[] = [`# ${title}`, '']
  if (desc) {
    lines.push(desc, '')
  }
  for (const sec of sections) {
    lines.push(`## ${sec.title}`, '')
    lines.push(`<!-- id: ${sec.id} -->`, '')
    for (const block of sec.blocks) {
      lines.push(blockToMd(block))
    }
  }
  return lines.join('\n').trimEnd() + '\n'
}

function buildIndex(locale: Locale, locales: LocaleFile): string {
  const lines = [
    `# Documentation (${locale})`,
    '',
    'Algorithm and strategy reference for the Funding Arb scanner. Generated from `web/src/content/docs/`.',
    '',
    '| Article | Description |',
    '| --- | --- |',
  ]
  for (const article of DOC_ARTICLES) {
    const key = article.titleKey.replace('docs.articles.', '').replace('.title', '')
    const title = locales.docs.articles[key]?.title ?? article.slug
    const desc = locales.docs.articles[key]?.desc ?? ''
    lines.push(`| [${title}](./${article.slug}.md) | ${desc} |`)
  }
  lines.push(
    '',
    '## Repository docs',
    '',
    '| Doc | Path |',
    '| --- | --- |',
    '| Project README | [../README.md](../README.md) |',
    '| CLI playbook (SKILL) | [../SKILL.md](../SKILL.md) |',
  )
  if (locale === 'zh-CN') {
    lines.push(
      '| Cross-interval model (legacy path) | [cross-interval-funding-model.md](../cross-interval-funding-model.md) |',
    )
  }
  lines.push(
    '',
    '---',
    '',
    '_Regenerate: `npx tsx scripts/tools/export_docs_md.mts`_',
    '',
  )
  return lines.join('\n')
}

function main() {
  const localeData = Object.fromEntries(
    LOCALES.map((l) => [l, loadLocale(l)]),
  ) as Record<Locale, LocaleFile>

  for (const locale of LOCALES) {
    const outDir = join(DOCS_OUT, locale)
    mkdirSync(outDir, { recursive: true })

    writeFileSync(join(outDir, 'README.md'), buildIndex(locale, localeData[locale]), 'utf8')

    for (const article of DOC_ARTICLES) {
      const sections = getDocSections(article, locale)
      const title = articleTitle(article.titleKey, locale, localeData[locale])
      const key = article.titleKey.replace('docs.articles.', '').replace('.title', '')
      const desc = localeData[locale].docs.articles[key]?.desc
      const md = sectionsToMd(sections, title, desc)
      writeFileSync(join(outDir, `${article.slug}.md`), md, 'utf8')
      console.log(`wrote docs/${locale}/${article.slug}.md`)
    }
  }

  // Root docs index (English)
  const rootIndex = [
    '# Documentation',
    '',
    'In-app docs are mirrored here for offline / repo browsing.',
    '',
    '| Language | Index |',
    '| --- | --- |',
    '| 简体中文 | [zh-CN/README.md](./zh-CN/README.md) |',
    '| English | [en/README.md](./en/README.md) |',
    '| 繁體中文（台灣） | [zh-TW/README.md](./zh-TW/README.md) |',
    '',
    '## Legacy',
    '',
    '- [cross-interval-funding-model.md](./cross-interval-funding-model.md) — original cross-interval reference (zh-CN, kept for backward-compatible links)',
    '',
    '## Regenerate',
    '',
    '```bash',
    'npx tsx scripts/tools/export_docs_md.mts',
    '```',
    '',
    'After editing `web/src/content/docs/articles/*.ts`, run the command above and commit both TS and generated Markdown.',
    '',
  ].join('\n')
  writeFileSync(join(DOCS_OUT, 'README.md'), rootIndex, 'utf8')
  console.log('wrote docs/README.md')
}

main()
