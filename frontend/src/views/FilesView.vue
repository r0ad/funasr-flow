<template>
  <div
    class="stack"
    style="gap: 14px"
    @dragover.prevent="onDragOver"
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <!-- 状态筛选 -->
    <div class="row wrap">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="btn sm"
        :class="{ primary: tab.key === activeTab }"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
        <span v-if="tab.count !== undefined" class="muted" style="font-weight: 400">{{ tab.count }}</span>
      </button>
      <span class="spacer" />
      <div class="search-wrap">
        <Icon name="search" :size="15" />
        <input
          v-model="filters.q"
          class="input search"
          style="width: 240px"
          placeholder="按文件名筛选…"
          @input="onSearchInput"
        />
      </div>
      <button class="btn sm" :disabled="uploading" @click="pickFiles">
        <Icon name="upload" :size="15" />
        {{ uploading ? `上传中 ${uploadPct}%` : '上传录音' }}
      </button>
      <input
        ref="fileInput"
        type="file"
        accept="audio/*,.m4a,.mp3,.wav,.amr,.aac,.flac,.ogg,.opus,.wma,.mp4,.3gp,.m4b,.webm"
        multiple
        class="vh"
        @change="onPick"
      />
    </div>

    <!-- 上传队列 -->
    <div v-if="uploads.length" class="card" style="padding: 10px 14px">
      <div class="row" style="justify-content: space-between">
        <b class="small">上传队列（{{ uploads.filter((u) => u.status === 'done').length }}/{{ uploads.length }}）</b>
        <span class="row" style="gap: 6px">
          <span v-if="uploading" class="small muted">上传中…</span>
          <button class="btn ghost icon sm" title="关闭" @click="dismissUploads">
            <Icon name="x" :size="14" />
          </button>
        </span>
      </div>
      <div v-for="(u, i) in uploads" :key="i" style="margin-top: 8px">
        <div class="row" style="justify-content: space-between">
          <span class="small truncate" style="max-width: 70%">{{ u.name }}</span>
          <span class="small" :style="{ color: u.status === 'error' ? 'var(--err)' : 'var(--muted)' }">
            {{ u.status === 'done' ? '完成' : u.status === 'error' ? u.error : `${u.pct}%` }}
          </span>
        </div>
        <div class="bar" style="margin-top: 4px; height: 4px">
          <span :style="{ width: `${u.pct}%`, background: u.status === 'error' ? 'var(--err)' : undefined }" />
        </div>
      </div>
    </div>

    <!-- 拖拽提示 -->
    <div v-if="dragging" class="drop-mask">
      <div class="drop-box">
        <Icon name="upload" :size="26" />
        <b>松开即上传到录音库</b>
        <span class="small muted">支持 .m4a .mp3 .wav .amr .aac 等，可一次拖多个</span>
      </div>
    </div>

    <!-- 列表 -->
    <div class="card" style="overflow: hidden">
      <div v-if="loading && !items.length" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        <h3>这里还没有录音</h3>
        <p class="small">
          把手机里的通话录音放进 <code>CallRecord/</code>，点右上角「扫描目录」选文件夹，或直接把文件拖进这个页面。
        </p>
        <button class="btn primary" style="margin-top: 14px" @click="scanNow">扫描录音目录</button>
      </div>

      <div v-else style="overflow: auto; max-height: calc(100vh - 260px)">
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
              <th class="sortable" style="width: 90px" @click="sortBy('duration')">
                时长 <SortMark field="duration" :sort="sort" />
              </th>
              <th style="width: 92px">大小</th>
              <th style="min-width: 150px">说话人</th>
              <th class="sortable" style="width: 108px" @click="sortBy('status')">
                状态 <SortMark field="status" :sort="sort" />
              </th>
              <th class="sortable" style="width: 130px" @click="sortBy('updated')">
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
                <RouterLink :to="`/files/${f.id}`" class="truncate" style="display: block; max-width: 420px">
                  {{ f.name }}
                </RouterLink>
                <span v-if="f.error" class="small" style="color: var(--err)">{{ f.error }}</span>
              </td>
              <td class="num">{{ fmtDuration(f.duration) }}</td>
              <td class="num">{{ fmtBytes(f.size) }}</td>
              <td>
                <div v-if="f.speakers?.length" class="row wrap" style="gap: 4px">
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
                  <span v-if="f.status === 'running'" class="bullet" />
                  {{ STATUS_META[f.status]?.label || f.status }}
                </span>
                <div
                  v-if="f.status === 'running' && progressFor(f.id) !== null"
                  class="bar"
                  style="margin-top: 5px; height: 4px"
                >
                  <span :style="{ width: `${Math.round(progressFor(f.id) * 100)}%` }" />
                </div>
              </td>
              <td class="num small">{{ shortDate(f.updated_at) }}</td>
              <td class="col-actions">
                <div class="row" style="justify-content: flex-end; gap: 4px">
                  <button
                    v-if="f.status === 'done'"
                    class="btn ghost icon sm"
                    title="查看转写"
                    @click="$router.push(`/files/${f.id}`)"
                  >
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

      <div v-if="total > limit" class="pager" style="padding: 10px 14px">
        <span class="small muted">共 {{ total }} 个</span>
        <button class="btn sm" :disabled="offset === 0" @click="goPage(-1)">上一页</button>
        <span class="small mono">{{ page }} / {{ pageCount }}</span>
        <button class="btn sm" :disabled="offset + limit >= total" @click="goPage(1)">下一页</button>
      </div>
    </div>

    <!-- 批量操作条 -->
    <div v-if="selected.size" class="batchbar">
      <span>已选 <b>{{ selected.size }}</b> 个</span>
      <button class="btn sm" @click="clearSelection">取消选择</button>
      <span class="spacer" />
      <button class="btn sm" @click="enqueueSelected(false)">
        <Icon name="zap" :size="14" /> 开始转写
      </button>
      <button class="btn sm" @click="enqueueSelected(true)">
        <Icon name="refresh" :size="14" /> 重新转写
      </button>
      <div class="row" style="gap: 0">
        <button class="btn sm" @click="exportSelected('txt')">
          <Icon name="download" :size="14" /> 导出
        </button>
        <select v-model="exportFmt" class="select sm" style="height: 28px; border-left: none; border-radius: 0 7px 7px 0" @change="exportSelected(exportFmt)">
          <option value="txt">TXT</option>
          <option value="md">Markdown</option>
          <option value="srt">SRT</option>
          <option value="json">JSON</option>
        </select>
      </div>
      <button class="btn sm danger" @click="removeSelected">
        <Icon name="trash" :size="14" /> 删除
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
const fileInput = ref(null)
const dragging = ref(false)
const uploads = ref([]) // {name, pct, status: 'uploading'|'done'|'error', error}
const uploading = ref(false)
const uploadPct = computed(() => {
  const list = uploads.value.filter((u) => u.status === 'uploading')
  if (!list.length) return 100
  return Math.round(list.reduce((a, u) => a + u.pct, 0) / list.length)
})

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

// ---- 上传 ----
function pickFiles() {
  // 用隐藏 input 触发系统选择框；不用 hidden 属性，避免个别浏览器忽略 click()
  fileInput.value?.click()
}

let uploadHideTimer = null

function dismissUploads() {
  clearTimeout(uploadHideTimer)
  uploads.value = []
}

function onPick(e) {
  const files = [...(e.target.files || [])]
  e.target.value = ''
  if (files.length) uploadAll(files)
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
