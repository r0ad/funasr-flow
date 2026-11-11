<template>
  <Modal :open="open" title="选择录音目录" wide @close="emit('close')">
    <div class="picker">
      <!-- 容器提示：日常不需要挂载，音频拖进来就行 -->
      <details v-if="data.container" class="docker-tip" :open="!data.mounts.length">
        <summary class="docker-tip-head">
          <Icon name="box" :size="15" />
          <b>正在 Docker 里运行</b>
          <span class="small">可见：{{ mountNames || '仅资料库（/data）' }}</span>
        </summary>
        <div class="docker-tip-body">
          <div class="small">
            <b>日常不用挂载宿主机目录</b>：把录音文件直接拖进页面，或用「上传录音」，走 HTTP 复制进资料库。
            这里的目录列表只包含已经挂进来的路径（资料库在 <code>/data/library</code>）。
          </div>
          <div v-if="hostRoots.length" class="docker-line">
            <span class="small">已挂载的盘</span>
            <button
              v-for="r in hostRoots"
              :key="r.path"
              class="chip"
              :class="{ on: same(r.path, data.path) }"
              @click="go(r.path)"
            >
              {{ r.name }}
            </button>
          </div>
          <div v-else class="small">
            想临时翻一下宿主机的盘，可以整盘只读挂进来（可选）：
            <code>-v "D:/:/host/d:ro"</code>，详见 README 的 Docker 段。
          </div>
        </div>
      </details>

      <!-- 盘符 / 起点 -->
      <div class="picker-roots">
        <button
          v-for="r in data.roots"
          :key="r.path"
          class="chip"
          :class="{ on: same(r.path, data.path) }"
          :title="r.path"
          @click="go(r.path)"
        >
          {{ r.name }}
        </button>
      </div>

      <!-- 当前路径 + 上一级 -->
      <div class="picker-bar">
        <button class="btn sm" :disabled="!data.parent" title="上一级" @click="go(data.parent)">
          <Icon name="back" :size="14" /> 上一级
        </button>
        <input
          v-model="manual"
          class="input mono picker-path"
          placeholder="直接粘贴路径，回车跳转"
          @keyup.enter="go(manual)"
        />
        <button class="btn sm" @click="go(manual)">转到</button>
      </div>

      <div v-if="error" class="picker-error">{{ error }}</div>
      <div v-else-if="loading" class="picker-empty">读取中…</div>
      <template v-else>
        <div class="picker-crumbs">
          <template v-for="(c, i) in crumbs" :key="c.path">
            <span v-if="i" class="crumb-sep">/</span>
            <button class="crumb" :class="{ on: i === crumbs.length - 1 }" @click="go(c.path)">
              {{ c.label }}
            </button>
          </template>
        </div>

        <div class="picker-list">
          <div v-if="!data.entries.length" class="picker-empty">
            这个目录下没有子文件夹。如果录音就放在当前目录，直接点右下角「导入这个目录」。
          </div>
          <button
            v-for="e in data.entries"
            :key="e.path"
            class="picker-item"
            :title="e.path"
            @click="go(e.path)"
          >
            <Icon name="folder" :size="16" />
            <span class="name">{{ e.name }}</span>
            <span v-if="e.audio" class="badge ok">{{ e.audio }} 个录音</span>
            <Icon name="chevron" :size="14" class="chev" />
          </button>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="picker-foot">
        <span class="small">
          <b>{{ data.audio || 0 }}</b> 个录音会复制进资料库 ·
          {{ data.path || '—' }}
        </span>
        <span class="spacer" />
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="busy" @click="pick">
          <Icon name="download" :size="15" />
          {{ busy ? '导入中…' : '导入这个目录' }}
        </button>
      </div>
    </template>
  </Modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import Modal from './Modal.vue'
import Icon from './Icon.vue'
import { api } from '../api.js'
import { toast, store } from '../store.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  start: { type: String, default: '' },
})
const emit = defineEmits(['close', 'picked'])

const data = reactive({
  path: '',
  parent: null,
  roots: [],
  entries: [],
  audio: 0,
  container: false,
  mounts: [],
  image: '',
  hint: '',
})
const manual = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const mountNames = computed(() => (data.mounts || []).map((m) => m.path).join('、'))
// 容器里额外挂进来的整盘（/host/<盘符>），用于快速跳转
const hostRoots = computed(() =>
  (data.roots || []).filter((r) => /^\/host\/[a-z]$/i.test(r.path || '')),
)

// 把绝对路径拆成可点的面包屑（Windows 盘符也当一段）
const crumbs = computed(() => {
  const raw = data.path || ''
  if (!raw) return []
  const sep = raw.includes('\\') ? '\\' : '/'
  const parts = raw.split(sep).filter(Boolean)
  const out = []
  let acc = ''
  for (const p of parts) {
    acc = acc ? `${acc}${sep}${p}` : `${p}${sep}`
    out.push({ label: p, path: acc })
  }
  return out
})

function same(a, b) {
  if (!a || !b) return false
  return a.replace(/[\\/]+$/, '').toLowerCase() === b.replace(/[\\/]+$/, '').toLowerCase()
}

async function go(path) {
  // 空字符串是有意义的：后端会回退到「当前录音目录」或第一个盘符
  if (path === null) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.browse(path)
    Object.assign(data, res)
    manual.value = res.path
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function pick() {
  if (!data.path) return
  busy.value = true
  try {
    const res = await api.importDir(data.path)
    if (res.importing) {
      toast(`开始导入 ${data.path}，进度见右下角`, 'ok')
    } else {
      const n = res.import?.added ?? 0
      toast(`已导入 ${n} 个录音（已在库里的会跳过）`, 'ok')
    }
    store.reloadTick++
    emit('picked', data.path)
    emit('close')
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    busy.value = false
  }
}

watch(
  () => props.open,
  (v) => {
    if (v) go(props.start || '')
  },
  { immediate: true },
)
</script>

<style scoped>
.picker {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 320px;
}
.picker-roots {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.picker-bar {
  display: flex;
  gap: 8px;
  align-items: center;
}
.picker-path {
  flex: 1;
  font-size: 13px;
}
.picker-crumbs {
  font-size: 12px;
  color: var(--text-dim);
  word-break: break-all;
}
.crumb {
  background: none;
  border: 0;
  color: inherit;
  font: inherit;
  cursor: default;
  padding: 0;
}
.picker-list {
  border: 1px solid var(--border);
  border-radius: 10px;
  max-height: 320px;
  overflow: auto;
  background: var(--bg);
}
.picker-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  background: none;
  border: 0;
  border-bottom: 1px solid var(--border-soft, var(--border));
  color: var(--text);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.picker-item:last-child {
  border-bottom: 0;
}
.picker-item:hover {
  background: var(--bg-elev);
}
.picker-item .name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picker-item .chev {
  opacity: 0.35;
}
.picker-empty {
  padding: 22px 12px;
  text-align: center;
  color: var(--text-dim);
  font-size: 13px;
}
.picker-error {
  padding: 12px;
  border-radius: 8px;
  background: var(--danger-soft, #fee);
  color: var(--danger, #b00);
  font-size: 13px;
}
.picker-foot {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.docker-tip {
  border: 1px solid var(--accent-weak, var(--border));
  background: var(--accent-weak, rgba(99, 102, 241, 0.06));
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.docker-tip-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--accent, #4f46e5);
  font-size: 13px;
}
.docker-tip-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.docker-line {
  display: flex;
  gap: 8px;
  align-items: center;
}
.docker-tip summary {
  cursor: pointer;
  list-style: none;
}
.docker-tip summary::-webkit-details-marker {
  display: none;
}
.docker-tip-more {
  margin-left: auto;
  color: var(--accent, #4f46e5);
}
.docker-tip[open] .docker-tip-more {
  visibility: hidden;
}
</style>
