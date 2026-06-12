<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NText, NAnchor, NAnchorLink, NTag } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import DocArticleBody from '@/components/DocArticleBody.vue'
import {
  DOC_ARTICLES,
  DEFAULT_DOC_SLUG,
  findDocArticle,
  getDocSections,
  sectionNavItems,
} from '@/content/docs'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const slug = computed(() => (route.params.slug as string) || DEFAULT_DOC_SLUG)

const article = computed(() => findDocArticle(slug.value) ?? DOC_ARTICLES[0])

const sections = computed(() =>
  article.value ? getDocSections(article.value, locale.value) : [],
)

const navItems = computed(() =>
  article.value ? sectionNavItems(article.value, locale.value) : [],
)

function scrollToHash(hash: string) {
  const id = hash.replace(/^#/, '')
  if (!id) return
  requestAnimationFrame(() => {
    const el = document.getElementById(id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

function ensureValidRoute() {
  const param = route.params.slug as string | undefined
  if (!param) {
    router.replace({ path: `/docs/${DEFAULT_DOC_SLUG}`, hash: route.hash })
    return
  }
  if (!findDocArticle(param)) {
    router.replace({ path: `/docs/${DEFAULT_DOC_SLUG}`, hash: route.hash })
  }
}

onMounted(() => {
  ensureValidRoute()
  if (route.hash) scrollToHash(route.hash)
})

watch(() => route.params.slug, () => {
  ensureValidRoute()
})

watch(
  () => route.hash,
  (h) => {
    if (h) scrollToHash(h)
  },
)

watch(slug, () => {
  if (route.hash) scrollToHash(route.hash)
})

function selectArticle(nextSlug: string) {
  if (nextSlug === slug.value) return
  router.push({ path: `/docs/${nextSlug}` })
}

function onSectionClick(id: string) {
  router.replace({ path: route.path, hash: `#${id}` })
}
</script>

<template>
  <div class="docs-page">
    <div class="docs-header">
      <n-text class="docs-title">{{ t('docs.title') }}</n-text>
      <n-text depth="3" class="docs-subtitle">{{ t('docs.subtitle') }}</n-text>
    </div>

    <div class="docs-layout">
      <aside class="docs-sidebar">
        <n-card size="small" :title="t('docs.articleList')" class="sidebar-card">
          <button
            v-for="item in DOC_ARTICLES"
            :key="item.slug"
            type="button"
            class="article-item"
            :class="{ active: item.slug === slug }"
            @click="selectArticle(item.slug)"
          >
            <span class="article-item-title">{{ t(item.titleKey) }}</span>
            <span class="article-item-desc">{{ t(item.descKey) }}</span>
          </button>
        </n-card>

        <n-card size="small" :title="t('docs.toc')" class="sidebar-card toc-card">
          <n-anchor :show-rail="false" :bound="80">
            <n-anchor-link
              v-for="item in navItems"
              :key="item.id"
              :title="item.title"
              :href="`#${item.id}`"
              @click.prevent="onSectionClick(item.id)"
            />
          </n-anchor>
        </n-card>
      </aside>

      <main v-if="article" class="docs-main">
        <n-card size="small" class="doc-article">
          <template #header>
            <div class="article-header">
              <n-text class="article-title">{{ t(article.titleKey) }}</n-text>
              <n-tag
                v-if="article.tagKey"
                size="small"
                :type="article.tagType ?? 'default'"
                :bordered="false"
              >
                {{ t(article.tagKey) }}
              </n-tag>
            </div>
          </template>

          <doc-article-body :sections="sections" />
        </n-card>
      </main>
    </div>
  </div>
</template>

<style scoped>
.docs-page {
  max-width: 1100px;
  margin: 0 auto;
}

.docs-header {
  margin-bottom: 20px;
}

.docs-title {
  display: block;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
}

.docs-subtitle {
  display: block;
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.5;
}

.docs-layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  align-items: start;
}

.docs-sidebar {
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: sticky;
  top: 12px;
}

.sidebar-card :deep(.n-card-header) {
  padding-bottom: 8px;
}

.article-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  width: 100%;
  margin-bottom: 6px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, border-color 0.15s;
}

.article-item:last-child {
  margin-bottom: 0;
}

.article-item:hover {
  background: rgba(255, 255, 255, 0.04);
}

.article-item.active {
  background: rgba(24, 160, 88, 0.1);
  border-color: rgba(24, 160, 88, 0.35);
}

.article-item-title {
  font-size: 13px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.9);
}

.article-item-desc {
  font-size: 11px;
  line-height: 1.4;
  color: rgba(255, 255, 255, 0.45);
}

.doc-article :deep(.n-card-header) {
  padding-bottom: 8px;
}

.article-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.article-title {
  font-size: 16px;
  font-weight: 600;
}

@media (max-width: 800px) {
  .docs-layout {
    grid-template-columns: 1fr;
  }
  .docs-sidebar {
    position: static;
  }
}
</style>
