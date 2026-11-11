<template>
  <Modal :open="open" title="选择录音目录" wide @close="emit('close')">
    <div class="picker">
      <!-- 容器提示：只能看到挂载进来的目录 -->
      <div v-if="data.container" class="docker-tip">
        <div class="docker-tip-head">
          <Icon name="box" :size="15" />
          <b>正在 Docker 里运行</b>
          <span class="small">只能浏览挂载进容器的目录：{{ mountNames || '（无）' }}</span>
        </div>
        <div class="docker-tip-body">
          <div class="small">要把宿主机上别的目录挂进来，先停掉容器再用 -v 重启：</div>
          <div class="docker-line">
            <input
              v-model="hostPath"
              class="input mono docker-input"
              placeholder="宿主机目录，如 D:\我的通话录音"
            />
            <select v-model="target" class="input docker-sel">
              <option v-for="p in mountChoices" :key="p" :value="p">{{ p }}</option>
            </select>
            <button class="btn sm" @click="copyCmd">复制命令</button>
          </div>
          <pre class="docker-cmd">{{ cmd }}</pre>
        </div>
      </div>

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
            这个目录下没有子文件夹。如果录音就放在当前目录，直接点右下角「用这个目录」。
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
          本目录有 <b>{{ data.audio || 0 }}</b> 个录音 ·
          {{ data.path || '—' }}
        </span>
        <span class="spacer" />
        <button class="btn" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="busy" @click="pick">
          {{ busy ? '扫描中…' : '用这个目录' }}
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
const hostPath = ref('')
const target = ref('/media')

// 容器内可选的挂载点：已存在的 + 常用的备用名
const mountChoices = computed(() => {
  const have = (data.mounts || []).map((m) => m.path)
  const extra = ['/media', '/media2', '/mnt', '/recordings'].filter((p) => !have.includes(p))
  return [...have, ...extra]
})
const mountNames = computed(() => (data.mounts || []).map((m) => m.path).join('、'))

// 生成一条可直接粘贴的 docker run（数据/缓存用命名卷，录音目录只读挂载）
const cmd = computed(() => {
  const host = hostPath.value.trim() || 'D:\\我的通话录音'
  return [
    'docker rm -f funasr-flow',
    `docker run -d --name funasr-flow -p 8000:8000 \\`,
    `  -v funasr-data:/data -v funasr-cache:/cache \\`,
    `  -v "${host}:${target.value}:ro" \\`,
    `  ${data.image || 'sunzroad/funasr-flow:latest'}`,
  ].join('\n')
})

async function copyCmd() {
  try {
    await navigator.clipboard.writeText(cmd.value)
    toast('命令已复制，粘贴到终端执行后刷新页面', 'ok')
  } catch {
    toast('复制失败，请手动选中命令复制', 'err')
  }
}

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
    const res = await api.scan(data.path)
    toast(`已切换到 ${data.path}：新增 ${res.added} 个，共 ${res.total} 个录音`, 'ok')
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
</style>
