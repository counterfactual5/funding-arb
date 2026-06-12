# Documentation

In-app docs are mirrored here for offline / repo browsing.

| Language | Index |
| --- | --- |
| 简体中文 | [zh-CN/README.md](./zh-CN/README.md) |
| English | [en/README.md](./en/README.md) |
| 繁體中文（台灣） | [zh-TW/README.md](./zh-TW/README.md) |

## Legacy

- [cross-interval-funding-model.md](./cross-interval-funding-model.md) — original cross-interval reference (zh-CN, kept for backward-compatible links)

## Regenerate

```bash
npx tsx scripts/tools/export_docs_md.mts
```

After editing `web/src/content/docs/articles/*.ts`, run the command above and commit both TS and generated Markdown.
