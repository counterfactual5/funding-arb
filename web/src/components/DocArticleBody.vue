<script setup lang="ts">
import { NText, NDivider } from 'naive-ui'
import type { DocSection } from '@/content/docs'

defineProps<{
  sections: DocSection[]
}>()
</script>

<template>
  <section
    v-for="sec in sections"
    :id="sec.id"
    :key="sec.id"
    class="doc-section"
  >
    <n-text class="section-title">{{ sec.title }}</n-text>
    <n-divider style="margin: 10px 0 14px" />

    <template v-for="(block, i) in sec.blocks" :key="`${sec.id}-${i}`">
      <p v-if="block.type === 'p'" class="doc-p">{{ block.text }}</p>

      <pre v-else-if="block.type === 'formula'" class="doc-formula"><code>{{ block.lines.join('\n') }}</code></pre>

      <ul v-else-if="block.type === 'ul'" class="doc-ul">
        <li v-for="(item, j) in block.items" :key="j">{{ item }}</li>
      </ul>

      <div
        v-else-if="block.type === 'callout'"
        class="doc-callout"
        :class="block.variant"
      >
        {{ block.text }}
      </div>

      <div v-else-if="block.type === 'table'" class="doc-table-wrap">
        <table class="doc-table">
          <thead>
            <tr>
              <th v-for="h in block.headers" :key="h">{{ h }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, ri) in block.rows" :key="ri">
              <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.doc-section {
  margin-bottom: 28px;
  scroll-margin-top: 16px;
}

.doc-section:last-child {
  margin-bottom: 0;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
}

.doc-p {
  margin: 0 0 12px;
  font-size: 14px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.78);
}

.doc-formula {
  margin: 0 0 14px;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.08);
  overflow-x: auto;
}

.doc-formula code {
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  color: #63e2b7;
  white-space: pre;
}

.doc-ul {
  margin: 0 0 14px;
  padding-left: 1.25rem;
  font-size: 14px;
  line-height: 1.65;
  color: rgba(255, 255, 255, 0.78);
}

.doc-callout {
  margin: 0 0 14px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.55;
  border-left: 3px solid;
}

.doc-callout.info {
  background: rgba(24, 160, 88, 0.08);
  border-color: #18a058;
  color: rgba(255, 255, 255, 0.82);
}

.doc-callout.warn {
  background: rgba(240, 160, 32, 0.08);
  border-color: #f0a020;
  color: rgba(255, 255, 255, 0.82);
}

.doc-table-wrap {
  margin: 0 0 14px;
  overflow-x: auto;
}

.doc-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.doc-table th,
.doc-table td {
  padding: 8px 10px;
  text-align: left;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.doc-table th {
  font-weight: 600;
  color: rgba(255, 255, 255, 0.55);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.doc-table td {
  color: rgba(255, 255, 255, 0.82);
}
</style>
