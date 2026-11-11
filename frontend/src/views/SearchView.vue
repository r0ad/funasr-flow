<template>
  <div class="stack" style="gap: 16px; max-width: 900px">
    <div class="card card-pad">
      <div class="search-wrap" style="width: 100%">
        <Icon name="search" :size="16" />
        <input
          ref="inputEl"
          v-model="query"
          class="input search"
          style="width: 100%; height: 42px; font-size: 15px"
          placeholder="搜索说过的话，例如：钱、医院、寄过来、身份证…"
          @input="onInput"
          @keydown.enter="run"
        />
      </div>
      <div class="row wrap small muted" style="margin-top: 8px; gap: 6px">
        <span>试试：</span>
        <button v-for="w in examples" :key="w" class="chip clickable" @click="useExample(w)">{{ w }}</button>
        <span class="spacer" />
        <span v-if="searched">找到 {{ items.length }} 句</span>
      </div>
    </div>

    <div v-if="loading" class="empty">搜索中…</div>

    <div v-else-if="searched && !items.length" class="card empty">
      <h3>没有找到「{{ query }}」</h3>
      <p class="small">只搜索已转写的录音。少于 3 个字的词会退化为模糊匹配。</p>
    </div>

    <div v-else-if="groups.length" class="stack" style="gap: 12px">
      <div v-for="g in groups" :key="g.file_id" class="card">
        <div class="card-head">
          <Icon name="mic" :size="15" />
          <RouterLink :to="`/files/${g.file_id}`" class="truncate" style="font-weight: 600; font-size: 13px">
            {{ g.name }}
          </RouterLink>
          <span class="badge">{{ g.items.length }} 处</span>
          <span class="spacer" />
        </div>
        <div style="padding: 4px 10px 10px">
          <div
            v-for="(it, i) in g.items"
            :key="i"
            class="hit"
            @click="openHit(g.file_id, it.start_ms)"
          >
            <span class="mono muted small">{{ fmtClock(it.start_ms) }}</span>
            <span class="hit-text" v-html="highlight(it.text)"></span>
          </div>
        </div>
      </div>
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
  gap: 10px;
  padding: 7px 8px;
  border-radius: 7px;
  cursor: pointer;
}
.hit:hover {
  background: var(--panel-2);
}
.hit-text {
  font-size: 13.5px;
  line-height: 1.6;
}
</style>
