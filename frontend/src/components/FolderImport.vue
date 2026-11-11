<template>
  <Modal :open="open" title="导入录音" wide @close="close">
    <div class="fi">
      <div class="fi-intro">
        上传从<b>你电脑上选</b>：浏览器直接读文件，服务器（包括 Docker）不需要挂载任何目录。
      </div>

      <div class="fi-pick">
        <button class="btn primary lg" :disabled="running" @click="pickFolder">
          <Icon name="folder" :size="16" />
          选择文件夹
        </button>
        <button class="btn lg" :disabled="running" @click="pickFiles">
          <Icon name="upload" :size="16" />
          选择音频文件
        </button>
        <input
          ref="folderInput"
          class="vh"
          type="file"
          webkitdirectory
          multiple
          @change="onFolderInput"
        />
        <input
          ref="fileInput"
          class="vh"
          type="file"
          multiple
          :accept="accept"
          @change="onFileInput"
        />
      </div>

      <p class="small muted fi-note">
        选文件夹时只导入<b>这一层</b>的音频，不会进子文件夹；音频会复制进资料库，原文件不动。
        已有同名同大小的会跳过。
      </p>

      <div v-if="entries.length" class="fi-summary">
        <span class="badge accent">{{ entries.length }} 个音频</span>
        <span class="muted small">{{ fmtBytes(totalBytes) }}</span>
        <button v-if="!running" class="btn sm ghost" @click="reset">清空重选</button>
      </div>

      <div v-if="entries.length" class="fi-list">
        <div v-for="e in preview" :key="e.key" class="fi-item">
          <Icon name="mic" :size="14" />
          <span class="truncate">{{ e.file.name }}</span>
          <span class="muted small">{{ fmtBytes(e.file.size) }}</span>
        </div>
        <div v-if="entries.length > preview.length" class="small muted">
          还有 {{ entries.length - preview.length }} 个…
        </div>
      </div>

      <div v-if="running || finished" class="fi-progress">
        <div class="bar"><span :style="{ width: pct + '%' }" /></div>
        <div class="fi-stats small">
          <span>{{ doneCount }}/{{ entries.length }}</span>
          <span class="ok-text">成功 {{ okCount }}</span>
          <span v-if="skippedCount" class="muted">跳过 {{ skippedCount }}</span>
          <span v-if="failCount" class="err-text">失败 {{ failCount }}</span>
        </div>
        <div v-if="running && currentName" class="small muted truncate">正在上传：{{ currentName }}</div>
        <div v-if="!running && failCount && firstError" class="small err-text truncate">
          {{ firstError }}
        </div>
      </div>
    </div>

    <template #footer>
      <button class="btn ghost" :disabled="running" @click="close">
        {{ finished ? '完成' : '取消' }}
      </button>
      <button
        v-if="entries.length && !finished"
        class="btn primary"
        :disabled="running"
        @click="start"
      >
        {{ running ? '上传中…' : `开始上传 ${entries.length} 个` }}
      </button>
      <button v-else-if="finished && failCount" class="btn" @click="retryFailed">
        重试失败的 {{ failCount }} 个
      </button>
    </template>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { fmtBytes, refreshStats, store, toast } from '../store'
import Icon from './Icon.vue'
import Modal from './Modal.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const AUDIO = [
  '.m4a',
  '.mp3',
  '.wav',
  '.amr',
  '.aac',
  '.opus',
  '.ogg',
  '.flac',
  '.mp4',
  '.wma',
  '.3gp',
  '.silk',
  '.m4b',
  '.webm',
]
const accept = AUDIO.join(',')

const folderInput = ref(null)
const fileInput = ref(null)

const entries = ref([]) // [{key, file}]
const failed = ref([]) // [{key, file}]
const running = ref(false)
const finished = ref(false)
const doneCount = ref(0)
const okCount = ref(0)
const skippedCount = ref(0)
const failCount = ref(0)
const currentName = ref('')
const currentPct = ref(0)
const firstError = ref('')

const preview = computed(() => entries.value.slice(0, 6))
const totalBytes = computed(() => entries.value.reduce((n, e) => n + (e.file.size || 0), 0))
const pct = computed(() => {
  if (!entries.value.length) return 0
  const base = doneCount.value / entries.value.length
  const step = 1 / entries.value.length
  return Math.min(100, Math.round((base + (step * currentPct.value) / 100) * 100))
})

function isAudio(name) {
  const i = String(name).lastIndexOf('.')
  return i >= 0 && AUDIO.includes(String(name).slice(i).toLowerCase())
}

watch(
  () => props.open,
  (v) => {
    if (!v) return
    reset()
  },
)

function reset() {
  entries.value = []
  failed.value = []
  running.value = false
  finished.value = false
  doneCount.value = 0
  okCount.value = 0
  skippedCount.value = 0
  failCount.value = 0
  currentName.value = ''
  currentPct.value = 0
  firstError.value = ''
}

function close() {
  if (running.value) {
    toast('还在上传，先点「取消上传」或等它传完', 'warn')
    return
  }
  emit('close')
}

function addPairs(pairs) {
  const next = [...entries.value]
  const seen = new Set(next.map((e) => e.file.name + e.file.size))
  for (const file of pairs) {
    if (!isAudio(file.name)) continue
    const key = file.name + file.size
    if (seen.has(key)) continue
    seen.add(key)
    next.push({ key, file })
  }
  next.sort((a, b) => a.file.name.localeCompare(b.file.name, 'zh-Hans-CN'))
  entries.value = next
  finished.value = false
  doneCount.value = 0
  okCount.value = 0
  skippedCount.value = 0
  failCount.value = 0
}

async function pickFolder() {
  // 优先用 File System Access API：点一次就选到「文件夹」，只读这一层
  if (window.showDirectoryPicker) {
    try {
      const handle = await window.showDirectoryPicker({ mode: 'read' })
      const files = []
      for await (const item of handle.values()) {
        if (item.kind === 'file' && isAudio(item.name)) files.push(await item.getFile())
      }
      if (!files.length) {
        toast('这个文件夹里没有音频文件', 'warn')
        return
      }
      addPairs(files)
      return
    } catch (e) {
      if (e?.name === 'AbortError') return
      // 其它错误（权限/不支持）退回 <input webkitdirectory>
    }
  }
  folderInput.value?.click()
}

function onFolderInput(ev) {
  const list = [...(ev.target.files || [])]
  // webkitRelativePath = "选中目录/文件"；只留这一层的文件，不进子目录
  const flat = list.filter((f) => (f.webkitRelativePath || '').split('/').length === 2)
  if (!flat.length) toast('这个文件夹里没有音频文件', 'warn')
  else addPairs(flat)
  ev.target.value = ''
}

function pickFiles() {
  fileInput.value?.click()
}

function onFileInput(ev) {
  addPairs([...(ev.target.files || [])])
  ev.target.value = ''
}

async function start() {
  if (!entries.value.length || running.value) return
  running.value = true
  finished.value = false
  failed.value = []
  doneCount.value = 0
  okCount.value = 0
  skippedCount.value = 0
  failCount.value = 0
  firstError.value = ''

  for (const e of entries.value) {
    currentName.value = e.file.name
    currentPct.value = 0
    try {
      const res = await api.uploadFile(e.file, (p) => {
        currentPct.value = p
      })
      if (res?.skipped) skippedCount.value += 1
      else okCount.value += 1
    } catch (err) {
      failCount.value += 1
      failed.value.push(e)
      if (!firstError.value) firstError.value = `${e.file.name}：${err?.message || err}`
    }
    doneCount.value += 1
    currentPct.value = 0
  }

  running.value = false
  finished.value = true
  currentName.value = ''
  const parts = [`成功 ${okCount.value}`]
  if (skippedCount.value) parts.push(`跳过 ${skippedCount.value}`)
  if (failCount.value) parts.push(`失败 ${failCount.value}`)
  toast(`导入完成：${parts.join(' · ')}`, failCount.value ? 'warn' : 'ok')
  await refreshStats()
  store.reloadTick += 1
}

async function retryFailed() {
  if (!failed.value.length) return
  entries.value = failed.value.slice()
  await start()
}
</script>

<style scoped>
.fi {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.fi-intro {
  font-size: 13px;
  color: var(--muted);
  line-height: 1.6;
}
.fi-pick {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.fi-note {
  margin: 0;
  line-height: 1.6;
}
.fi-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 4px;
}
.fi-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  background: var(--panel-2);
}
.fi-item {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.fi-progress {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fi-progress :deep(.bar) {
  height: 8px;
}
.vh {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
.fi-stats {
  display: flex;
  gap: 12px;
}
.ok-text {
  color: var(--ok);
}
.err-text {
  color: var(--err);
}
</style>
