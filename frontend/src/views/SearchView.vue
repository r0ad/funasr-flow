<template>
  <div class="stack" style="gap: 16px; max-width: 960px">
    <!-- 搜索主区 -->
    <div class="card" style="padding: 26px 26px 20px; position: relative; overflow: hidden">
      <div
        aria-hidden="true"
        style="position: absolute; inset: -40% 40% auto -10%; height: 220px; border-radius: 50%;
               background: radial-gradient(closest-side, var(--accent-weak), transparent); pointer-events: none"
      />
      <h1 style="font-size: 20px">搜一搜说过的话</h1>
      <p class="desc muted" style="font-size: 12.5px; margin-top: 4px">
        全文检索所有已转写的句子，点结果直接跳到那句话播放。
      </p>
      <div class="search-wrap" style="width: 100%; margin-top: 14px">
        <Icon name="search" :size="17" />
        <input
          ref="inputEl"
          v-model="query"
          class="input search"
          style="width: 100%; height: 46px; font-size: 15px; border-radius: 12px"
          placeholder="例如：钱、医院、寄过来、身份证…"
          @input="onInput"
          @keydown.enter="run"
        />
      </div>
      <div class="row wrap" style="margin-top: 10px; gap: 6px">
        <span class="small muted">试试</span>
        <button v-for="w in examples" :key="w" class="chip clickable" @click="useExample(w)">{{ w }}</button>
        <span class="spacer" />
        <span v-if="searched" class="small muted">找到 {{ items.length }} 句</span>
      </div>
    </div>

    <div v-if="loading" class="empty">搜索中…</div>

    <div v-else-if="searched && !items.length" class="card empty">
      <div class="art"><Icon name="search" :size="24" /></div>
      <h3>没有找到「{{ query }}」</h3>
      <p class="hint">只搜索已经转写的录音；少于 3 个字的词会退化成模糊匹配。</p>
    </div>

    <div v-else-if="groups.length" class="stack" style="gap: 12px">
      <div v-for="g in groups" :key="g.file_id" class="card flush">
        <div class="card-head">
          <Icon name="mic" :size="15" />
          <RouterLink :to="`/files/${g.file_id}`" class="truncate file-link">{{ g.name }}</RouterLink>
          <span class="badge accent">{{ g.items.length }} 处</span>
          <span class="spacer" />
          <Icon name="chevron" :size="14" class="muted" />
        </div>
        <div style="padding: 6px 8px 8px">
          <div
            v-for="(it, i) in g.items"
            :key="i"
            class="hit"
            @click="openHit(g.file_id, it.start_ms)"
          >
            <span class="mono muted small nowrap">{{ fmtClock(it.start_ms) }}</span>
            <span class="hit-text" v-html="highlight(it.text)"></span>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="card empty">
      <div class="art"><Icon name="zap" :size="24" /></div>
      <h3>还没有搜索</h3>
      <p class="hint">输入任意词开始，支持中文短语；结果按录音分组，点击跳到那句话。</p>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { escapeHtml, fmtClock, toast } from '../store'

const route = useRoute()
const router = useRouter()
const query = ref('')
const items = ref([])
const loading = ref(false)
const searched = ref(false)
const inputEl = ref(null)
const examples = ['钱', '医院', '身份证', '寄过来', '吃饭', '身体']

const groups = computed(() => {
  const map = new Map()
  for (const it of items.value) {
    if (!map.has(it.file_id)) map.set(it.file_id, { file_id: it.file_id, name: it.name, items: [] })
    map.get(it.file_id).items.push(it)
  }
  return [...map.values()]
})

function highlight(text) {
  let html = escapeHtml(text)
  const q = query.value.trim()
  if (q) {
    const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
    html = html.replace(re, (m) => `<mark class="hl">${m}</mark>`)
  }
  return html
}

let timer = null
function onInput() {
  clearTimeout(timer)
  timer = setTimeout(run, 300)
}

async function run() {
  const q = query.value.trim()
  if (!q) {
    items.value = []
    searched.value = false
    return
  }
  loading.value = true
  try {
    const res = await api.search(q)
    items.value = res.items
    searched.value = true
    localStorage.setItem('flow.lastSearch', q)
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    loading.value = false
  }
}

function useExample(w) {
  query.value = w
  run()
}

function openHit(fileId, startMs) {
  router.push({ path: `/files/${fileId}`, query: { t: startMs, q: query.value.trim() } })
}

onMounted(() => {
  const q = String(route.query.q || localStorage.getItem('flow.lastSearch') || '')
  if (q) {
    query.value = q
    run()
  }
  inputEl.value?.focus()
})
</script>

<style scoped>
.hit {
  display: flex;
  gap: 12px;
  align-items: baseline;
  padding: 9px 10px;
  border-radius: 9px;
  cursor: pointer;
  transition: background 0.14s var(--ease);
}
.hit:hover {
  background: var(--panel-2);
}
.hit-text {
  font-size: 14px;
  line-height: 1.65;
  word-break: break-word;
}
</style>
