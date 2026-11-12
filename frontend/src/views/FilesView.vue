<template>
  <div
    class="stack"
    style="gap: 16px"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <!-- 概览 -->
    <div class="grid grid-4">
      <div class="stat">
        <span class="k">录音总数</span>
        <span class="v">{{ store.stats?.files ?? total }}<small>个</small></span>
        <span class="d">资料库内已入库的音频</span>
      </div>
      <div class="stat">
        <span class="k">语音时长</span>
        <span class="v">{{ hoursText }}<small>小时</small></span>
        <span class="d">全部录音累计时长</span>
      </div>
      <div class="stat">
        <span class="k">已转写</span>
        <span class="v">{{ store.stats?.done_files ?? 0 }}<small>个</small></span>
        <span class="d">{{ fmtDuration(store.stats?.done_seconds || 0) }} 已出文字</span>
      </div>
      <div class="stat">
        <span class="k">待处理</span>
        <span class="v">{{ todoCount }}<small>个</small></span>
        <span class="d">{{ pendingText }}</span>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="card flush">
      <div class="toolbar">
        <div class="seg">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            :class="{ active: tab.key === activeTab }"
            @click="switchTab(tab.key)"
          >
            {{ tab.label }}
            <span v-if="tab.count !== undefined" class="n">{{ tab.count }}</span>
          </button>
        </div>
        <span class="spacer" />
        <div class="search-wrap">
          <Icon name="search" :size="15" />
          <input
            v-model="filters.q"
            class="input search"
            style="width: 232px"
            placeholder="按文件名筛选…"
            @input="onSearchInput"
          />
        </div>
        <button
          class="btn ghost"
          :disabled="!canBulk"
          title="把所有还没转写的录音一次性排进队列"
          @click="transcribeAll"
        >
          <Icon name="zap" :size="15" />
          全部转写
        </button>
        <button class="btn primary" @click="openImport">
          <Icon name="folder" :size="15" />
          导入录音
        </button>
      </div>

      <!-- 上传队列 -->
      <div v-if="uploads.length" class="queue-card" style="border-top: 1px solid var(--border)">
        <div class="row">
          <b class="small">
            上传队列 {{ uploads.filter((u) => u.status === 'done').length }}/{{ uploads.length }}
          </b>
          <span class="spacer" />
          <span v-if="uploading" class="small muted">上传中…</span>
          <button class="btn ghost icon sm" title="关闭" @click="dismissUploads">
            <Icon name="x" :size="14" />
          </button>
        </div>
        <div v-for="(u, i) in uploads" :key="i" class="queue-item">
          <span class="nm truncate">{{ u.name }}</span>
          <div class="bar thin">
            <span
              :style="{ width: `${u.pct}%`, background: u.status === 'error' ? 'var(--err)' : undefined }"
            />
          </div>
          <span
            class="small nowrap"
            style="width: 62px; text-align: right"
            :style="{ color: u.status === 'error' ? 'var(--err)' : 'var(--muted)' }"
          >
            {{ u.status === 'done' ? '完成' : u.status === 'error' ? u.error : `${u.pct}%` }}
          </span>
        </div>
      </div>
    </div>

    <!-- 拖拽提示 -->
    <div v-if="dragging" class="drop-mask">
      <div class="box">
        <Icon name="upload" :size="28" />
        <div style="font-weight: 640; margin-top: 6px">松开即上传</div>
        <div class="small" style="color: var(--muted); margin-top: 2px">
          支持 .m4a .mp3 .wav .amr 等，可一次拖多个
        </div>
      </div>
    </div>

    <!-- 列表 -->
    <div class="card flush">
      <div v-if="loading && !items.length" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        <div class="art"><Icon name="files" :size="26" /></div>
        <h3>还没有录音</h3>
        <p class="hint">
          选一个文件夹（或几个文件）就行，浏览器直接读你电脑上的音频并上传，服务器不需要
          访问你的磁盘。导入的音频会复制进资料库，之后重启、换机器都不影响已有文字稿。
        </p>
        <div class="row" style="justify-content: center; gap: 8px; margin-top: 16px">
          <button class="btn primary" @click="openImport">
            <Icon name="folder" :size="15" /> 导入录音
          </button>
          <span class="small muted" style="align-self: center">也可以直接把音频拖进来</span>
        </div>
      </div>

      <div v-else class="table-wrap" style="max-height: calc(100vh - 330px)">
        <table class="table">
          <thead>
            <tr>
              <th class="col-check">
                <input
                  type="checkbox"
                  class="cb"
                  :checked="allOnPageSelected"
                  :indeterminate.prop="someSelected"
                  @change="toggleAllOnPage"
                />
              </th>
              <th class="sortable" @click="sortBy('name')">
                文件 <SortMark field="name" :sort="sort" />
              </th>
              <th class="sortable" style="width: 92px" @click="sortBy('duration')">
                时长 <SortMark field="duration" :sort="sort" />
              </th>
              <th style="width: 92px">大小</th>
              <th style="min-width: 160px">说话人</th>
              <th class="sortable" style="width: 112px" @click="sortBy('status')">
                状态 <SortMark field="status" :sort="sort" />
              </th>
              <th class="sortable" style="width: 124px" @click="sortBy('updated')">
                更新时间 <SortMark field="updated" :sort="sort" />
              </th>
              <th class="col-actions" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in items" :key="f.id" :class="{ selected: selected.has(f.id) }">
              <td class="col-check">
                <input type="checkbox" class="cb" :checked="selected.has(f.id)" @change="toggle(f.id)" />
              </td>
              <td>
                <RouterLink :to="`/files/${f.id}`" class="file-link truncate" style="display: block; max-width: 430px">
                  {{ f.name }}
                </RouterLink>
                <span v-if="f.error" class="small truncate" style="display: block; color: var(--err)">
                  {{ f.error }}
                </span>
              </td>
              <td class="num">{{ fmtDuration(f.duration) }}</td>
              <td class="num">{{ fmtBytes(f.size) }}</td>
              <td>
                <div v-if="f.speakers?.length" class="row wrap" style="gap: 5px">
                  <span
                    v-for="s in f.speakers.slice(0, 3)"
                    :key="s.cluster"
                    class="chip"
                    :title="`${s.name} · ${s.seconds}s`"
                  >
                    <span class="speaker-dot" :style="{ background: s.color }" />
                    {{ s.name }}
                  </span>
                  <span v-if="f.speakers.length > 3" class="small muted">+{{ f.speakers.length - 3 }}</span>
                </div>
                <span v-else class="muted small">—</span>
              </td>
              <td>
                <span class="badge" :class="STATUS_META[f.status]?.tone">
                  {{ STATUS_META[f.status]?.label || f.status }}
                </span>
                <div v-if="f.status === 'running' && progressFor(f.id) !== null" class="bar thin" style="margin-top: 6px">
                  <span :style="{ width: `${Math.round(progressFor(f.id) * 100)}%` }" />
                </div>
              </td>
              <td class="num small">{{ shortDate(f.updated_at) }}</td>
              <td class="col-actions">
                <div class="row row-actions" style="justify-content: flex-end; gap: 2px">
                  <button v-if="f.status === 'done'" class="btn ghost icon sm" title="查看转写" @click="$router.push(`/files/${f.id}`)">
                    <Icon name="play" :size="14" />
                  </button>
                  <button
                    v-else
                    class="btn ghost icon sm"
                    title="加入转写队列"
                    :disabled="f.status === 'queued' || f.status === 'running'"
                    @click="enqueueOne(f)"
                  >
                    <Icon name="zap" :size="14" />
                  </button>
                  <button class="btn ghost icon sm" title="删除记录" @click="removeOne(f)">
                    <Icon name="trash" :size="14" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="total > limit" class="pager" style="padding: 12px 16px; border-top: 1px solid var(--border)">
        <span class="small muted">共 {{ total }} 个</span>
        <span class="spacer" />
        <button class="btn sm ghost icon" :disabled="offset === 0" title="上一页" @click="goPage(-1)">
          <Icon name="back" :size="14" />
        </button>
        <span class="pages">{{ page }} / {{ pageCount }}</span>
        <button class="btn sm ghost icon" :disabled="offset + limit >= total" title="下一页" @click="goPage(1)">
          <Icon name="chevron" :size="14" />
        </button>
      </div>
    </div>

    <!-- 批量操作条 -->
    <div v-if="selected.size" class="batchbar">
      <span class="count">已选 <b>{{ selected.size }}</b> 个</span>
      <span class="divider" style="width: 1px; height: 20px; margin: 0 2px" />
      <button class="btn sm" @click="enqueueSelected(false)">
        <Icon name="zap" :size="14" /> 开始转写
      </button>
      <button class="btn sm ghost" @click="enqueueSelected(true)">
        <Icon name="refresh" :size="14" /> 重新转写
      </button>
      <div class="row" style="gap: 6px">
        <button class="btn sm ghost" @click="exportSelected('txt')">
          <Icon name="download" :size="14" /> 导出
        </button>
        <select v-model="exportFmt" class="select" style="height: 29px; font-size: 12.5px" @change="exportSelected(exportFmt)">
          <option value="txt">TXT</option>
          <option value="md">Markdown</option>
          <option value="srt">SRT</option>
          <option value="json">JSON</option>
        </select>
      </div>
      <button class="btn sm danger ghost" @click="removeSelected">
        <Icon name="trash" :size="14" /> 删除
      </button>
      <button class="btn sm ghost icon" title="取消选择" @click="clearSelection">
        <Icon name="x" :size="14" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import SortMark from '../components/SortMark.vue'
import { api } from '../api'
import { STATUS_META, fmtBytes, fmtDuration, store, toast } from '../store'

const filters = reactive({ q: '' })
const activeTab = ref('all')
const sort = reactive({ field: 'name', order: 'asc' })
const items = ref([])
const total = ref(0)
const limit = ref(50)
const offset = ref(0)
const loading = ref(false)
const selected = ref(new Set())
const exportFmt = ref('txt')
const dragging = ref(false)
const uploads = ref([]) // {name, pct, status: 'uploading'|'done'|'error', error}
const uploading = ref(false)

const TABS = [
  { key: 'all', label: '全部' },
  { key: 'todo', label: '待处理', status: 'idle,missing' },
  { key: 'active', label: '处理中', status: 'queued,running' },
  { key: 'done', label: '已完成', status: 'done' },
  { key: 'failed', label: '失败', status: 'failed' },
]

const tabs = computed(() =>
  TABS.map((t) => {
    const fileCounts = store.live?.files || {}
    let count
    if (t.status) {
      count = 0
      for (const code of t.status.split(',')) count += fileCounts[code] || 0
    } else if (store.live?.files) {
      count = Object.values(store.live.files).reduce((a, b) => a + b, 0)
    }
    return { ...t, count }
  }),
)

const hoursText = computed(() => (((store.stats?.total_seconds || 0) / 3600) || 0).toFixed(1))
const todoCount = computed(() => {
  const c = store.live?.files || {}
  return (c.idle || 0) + (c.missing || 0)
})
const pendingText = computed(() => {
  const q = store.live?.queue || {}
  if (q.running) return '正在转写 1 个'
  if (q.queued) return `${q.queued} 个排队中`
  return '队列空闲'
})
const canBulk = computed(() => {
  const c = store.live?.files || {}
  return (c.idle || 0) + (c.failed || 0) > 0
})

const page = computed(() => Math.floor(offset.value / limit.value) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
const allOnPageSelected = computed(() => items.value.length > 0 && items.value.every((f) => selected.value.has(f.id)))
const someSelected = computed(() => selected.value.size > 0 && !allOnPageSelected.value)

async function load() {
  loading.value = true
  try {
    const tab = TABS.find((t) => t.key === activeTab.value)
    const res = await api.listFiles({
      status: tab?.status,
      q: filters.q,
      sort: sort.field,
      order: sort.order,
      limit: limit.value,
      offset: offset.value,
    })
    items.value = res.items
    total.value = res.total
    // 清理已不存在的选择
    const ids = new Set(res.items.map((f) => f.id))
    for (const id of [...selected.value]) {
      if (!ids.has(id) && offset.value === 0) selected.value.delete(id)
    }
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    loading.value = false
  }
}

function switchTab(key) {
  activeTab.value = key
  offset.value = 0
  load()
}

let searchTimer = null
function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    offset.value = 0
    load()
  }, 280)
}

function sortBy(field) {
  if (sort.field === field) sort.order = sort.order === 'asc' ? 'desc' : 'asc'
  else {
    sort.field = field
    sort.order = field === 'updated' ? 'desc' : 'asc'
  }
  load()
}

function goPage(delta) {
  offset.value = Math.max(0, offset.value + delta * limit.value)
  load()
}

function toggle(id) {
  const s = new Set(selected.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selected.value = s
}

function toggleAllOnPage() {
  const s = new Set(selected.value)
  if (allOnPageSelected.value) items.value.forEach((f) => s.delete(f.id))
  else items.value.forEach((f) => s.add(f.id))
  selected.value = s
}

function openImport() {
  store.ui.importOpen = true
}

function clearSelection() {
  selected.value = new Set()
}

function progressFor(fileId) {
  const a = store.live?.active
  if (a && a.file_id === fileId) return a.progress ?? 0
  return null
}

async function enqueueOne(f) {
  try {
    await api.batch([f.id], 'enqueue')
    toast(`已加入队列：${f.name}`, 'ok')
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function transcribeAll() {
  try {
    const pre = await api.transcribeAll({ dry_run: true })
    if (!pre.candidates) return toast('没有待转写的录音了', 'ok')
    const audio = fmtDuration(pre.seconds)
    const eta = pre.eta_seconds ? `，按当前速度预计 ${fmtDuration(pre.eta_seconds)}` : ''
    const ok = confirm(
      `把 ${pre.candidates} 条录音加入队列？\n共约 ${audio} 音频${eta}。\n\n` +
        '入队后按文件从短到长串行转写，随时可以在排队卡片里停止。',
    )
    if (!ok) return
    const res = await api.transcribeAll({})
    toast(`已加入队列 ${res.queued} 条${res.skipped ? `，跳过 ${res.skipped} 条` : ''}`, 'ok')
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function enqueueSelected(reset) {
  const ids = [...selected.value]
  try {
    const res = await api.batch(ids, reset ? 'reset' : 'enqueue')
    toast(`已加入队列 ${res.queued} 个${res.skipped ? `，跳过 ${res.skipped} 个（已在队列）` : ''}`, 'ok')
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function removeOne(f) {
  if (!confirm(`只删除这条记录（不影响原始录音文件）：\n${f.name}`)) return
  try {
    await api.deleteFile(f.id)
    toast('已删除记录', 'ok')
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function removeSelected() {
  if (!confirm(`删除选中的 ${selected.value.size} 条记录？原始录音文件不受影响。`)) return
  try {
    await api.batch([...selected.value], 'delete')
    toast('已删除', 'ok')
    clearSelection()
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

function exportSelected(fmt) {
  const ids = [...selected.value]
  if (!ids.length) return
  window.location.href = api.exportUrl(ids, fmt)
}

async function scanNow() {
  try {
    const res = await api.scan()
    toast(`扫描完成：新增 ${res.added} 个，共 ${res.total} 个`, 'ok')
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

// ---- 拖拽上传 ----
let uploadHideTimer = null

function dismissUploads() {
  clearTimeout(uploadHideTimer)
  uploads.value = []
}

let dragDepth = 0
function onDragOver(e) {
  // 只有拖进来的是文件才提示
  if (e.dataTransfer?.types?.includes('Files')) {
    dragDepth += 1
    dragging.value = true
  }
}

function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) dragging.value = false
}

function onDrop(e) {
  dragDepth = 0
  dragging.value = false
  const files = [...(e.dataTransfer?.files || [])]
  if (files.length) uploadAll(files)
}

async function uploadAll(files) {
  uploading.value = true
  const items = files.map((f) => ({ name: f.name, pct: 0, status: 'uploading', error: '' }))
  uploads.value = [...items, ...uploads.value]
  let ok = 0
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]
    const row = items[i]
    try {
      await api.uploadFile(file, (pct) => {
        row.pct = pct
      })
      row.pct = 100
      row.status = 'done'
      ok += 1
    } catch (e) {
      row.status = 'error'
      row.error = e.message
    }
    uploads.value = [...uploads.value]
  }
  uploading.value = false
  uploads.value = [...uploads.value]
  if (ok) {
    toast(`已上传 ${ok} 个录音${ok < files.length ? `，${files.length - ok} 个失败` : ''}`, 'ok')
    offset.value = 0
    await load()
    store.reloadTick++
    // 全部成功后 6 秒自动收起上传队列，失败项留着让人看清原因
    if (ok === files.length) {
      clearTimeout(uploadHideTimer)
      uploadHideTimer = setTimeout(() => {
        uploads.value = []
      }, 6000)
    }
  } else {
    toast('上传失败，请检查文件格式', 'err')
  }
}

function shortDate(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ').slice(5, 16)
}

// 队列完成数变化时静默刷新（避免每帧都请求）
watch(
  () => `${store.live?.queue?.done}|${store.live?.queue?.failed}|${store.live?.queue?.running}`,
  () => load(),
)
watch(() => store.reloadTick, () => load())

onMounted(load)
</script>

<style scoped>
.vh {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.drop-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: color-mix(in srgb, var(--bg) 70%, transparent);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.drop-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 34px 46px;
  border: 2px dashed var(--accent);
  border-radius: 16px;
  background: var(--card);
  color: var(--accent);
}
</style>
