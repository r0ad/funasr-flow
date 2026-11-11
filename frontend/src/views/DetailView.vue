<template>
  <div class="stack" style="gap: 14px">
    <!-- 头部信息 -->
    <div class="card card-pad">
      <div class="row wrap" style="gap: 12px">
        <button class="btn ghost icon sm" title="返回列表" @click="$router.push('/')">
          <Icon name="arrowLeft" :size="16" />
        </button>
        <div style="min-width: 220px; flex: 1">
          <div style="font-weight: 650; word-break: break-all">{{ file?.name || '加载中…' }}</div>
          <div class="row wrap small muted" style="gap: 10px; margin-top: 2px">
            <span v-if="file?.duration"><Icon name="clock" :size="12" /> {{ fmtDuration(file.duration) }}</span>
            <span v-if="transcript?.model">模型 {{ transcript.model }}</span>
            <span v-if="transcript?.asr_ms">识别耗时 {{ (transcript.asr_ms / 1000).toFixed(1) }}s</span>
            <span>{{ segments.length }} 句 · {{ speakers.length }} 位说话人</span>
            <span v-if="file?.status && file.status !== 'done'" class="badge" :class="STATUS_META[file.status]?.tone">
              {{ STATUS_META[file.status]?.label }}
            </span>
          </div>
        </div>
        <div class="row" style="gap: 6px">
          <select v-model="exportFmt" class="select" style="height: 34px">
            <option value="txt">TXT</option>
            <option value="md">Markdown</option>
            <option value="srt">SRT 字幕</option>
            <option value="json">JSON</option>
          </select>
          <button class="btn" :disabled="!segments.length" @click="doExport">
            <Icon name="download" :size="15" /> 导出
          </button>
          <button class="btn" :disabled="retranscribing" @click="retranscribe">
            <Icon name="refresh" :size="15" /> {{ retranscribing ? '已排队' : '重新转写' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="!segments.length" class="card empty">
      <h3>还没有转写结果</h3>
      <p class="small">把这条录音加入队列，识别完成后自动出现在这里。</p>
      <button class="btn primary" style="margin-top: 14px" @click="enqueue">
        <Icon name="zap" :size="15" /> 开始转写
      </button>
    </div>

    <div v-else class="detail-layout">
      <!-- 左栏：播放器 + 说话人 -->
      <div class="stack">
        <div class="player">
          <audio
            ref="audioEl"
            :src="audioSrc"
            controls
            preload="metadata"
            @timeupdate="onTimeUpdate"
            @loadedmetadata="onLoaded"
          />
          <div class="times muted">
            <span class="mono">{{ fmtClock(currentMs) }}</span>
            <div class="row" style="gap: 4px">
              <button
                v-for="r in [0.75, 1, 1.25, 1.5]"
                :key="r"
                class="btn ghost sm"
                :class="{ primary: rate === r }"
                style="height: 22px; padding: 0 6px"
                @click="setRate(r)"
              >
                {{ r }}×
              </button>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <Icon name="users" :size="15" />
            <span style="font-weight: 600; font-size: 13px">说话人</span>
            <span class="spacer" />
            <span class="small muted">点击筛选</span>
          </div>
          <div class="card-pad" style="padding: 12px">
            <div v-for="s in speakers" :key="s.cluster" class="speaker-card" style="flex-direction: column; align-items: stretch; gap: 6px">
              <div class="row">
                <span class="speaker-dot" :style="{ background: s.color }" />
                <input
                  class="input"
                  style="height: 26px; font-size: 12.5px; flex: 1; min-width: 0"
                  :value="s.name"
                  :title="s.cluster"
                  @change="renameSpeaker(s, $event.target.value)"
                />
                <span class="small muted nowrap">{{ Math.round(s.seconds || 0) }}s</span>
              </div>
              <div class="row small">
                <select
                  class="select"
                  style="height: 26px; font-size: 12px; flex: 1; min-width: 0"
                  :value="s.speaker_id || ''"
                  @change="bindSpeaker(s, $event.target.value)"
                >
                  <option value="">未绑定声纹</option>
                  <option v-for="sp in store.speakers" :key="sp.id" :value="sp.id">
                    {{ sp.name }}{{ sp.has_embedding ? '' : '（无声纹）' }}
                  </option>
                </select>
                <button class="btn sm ghost" title="把这段声音登记进声纹库" @click="enroll(s)">
                  <Icon name="plus" :size="13" />
                </button>
                <button
                  class="btn sm ghost"
                  :class="{ primary: filterCluster === s.cluster }"
                  title="只看这个人说的话"
                  @click="filterCluster = filterCluster === s.cluster ? null : s.cluster"
                >
                  <Icon name="search" :size="13" />
                </button>
              </div>
            </div>
            <p v-if="!speakers.length" class="small muted">未检测到说话人</p>
          </div>
        </div>
      </div>

      <!-- 右栏：逐句稿 -->
      <div class="card" style="overflow: hidden">
        <div class="card-head">
          <span style="font-weight: 600; font-size: 13px">文字稿</span>
          <span class="small muted">单击文字可编辑，回车保存</span>
          <span class="spacer" />
          <span v-if="filterCluster" class="badge accent">
            筛选中 <button class="btn ghost sm" style="height: 18px" @click="filterCluster = null">清除</button>
          </span>
          <button class="btn sm ghost" title="复制全文" @click="copyAll">复制</button>
        </div>
        <div style="max-height: calc(100vh - 250px); overflow: auto; padding: 8px 10px">
          <div
            v-for="seg in visibleSegments"
            :key="seg.idx"
            class="transcript-line"
            :class="{ active: activeIdx === seg.idx }"
            :ref="(el) => setLineRef(seg.idx, el)"
          >
            <div class="line-meta">
              <span class="time" @click="seek(seg.start_ms)">{{ fmtClock(seg.start_ms) }}</span>
              <span class="row" style="gap: 4px">
                <span class="speaker-dot" :style="{ background: colorOf(seg.speaker) }" />
                <select
                  class="sel-inline"
                  :value="seg.speaker"
                  @change="changeSpeaker(seg, $event.target.value)"
                >
                  <option v-for="s in speakers" :key="s.cluster" :value="s.cluster">{{ s.name }}</option>
                </select>
              </span>
            </div>

            <div>
              <textarea
                v-if="editingIdx === seg.idx"
                ref="editBox"
                v-model="editText"
                class="textarea"
                style="width: 100%; font-size: 14px"
                rows="2"
                @blur="saveEdit(seg)"
                @keydown.enter.exact.prevent="saveEdit(seg)"
                @keydown.esc.prevent="cancelEdit"
              />
              <div
                v-else
                class="text"
                title="单击编辑"
                @click="startEdit(seg)"
                v-html="highlight(seg.text)"
              />
            </div>
          </div>
          <div v-if="!visibleSegments.length" class="empty small">这个人没有说话</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import {
  STATUS_META,
  escapeHtml,
  fmtClock,
  fmtDuration,
  refreshSpeakers,
  store,
  toast,
} from '../store'

const route = useRoute()
const fileId = computed(() => Number(route.params.id))

const file = ref(null)
const transcript = ref(null)
const segments = ref([])
const speakers = ref([])
const audioEl = ref(null)
const audioSrc = ref('')
const currentMs = ref(0)
const durationMs = ref(0)
const rate = ref(1)
const activeIdx = ref(-1)
const filterCluster = ref(null)
const exportFmt = ref('txt')
const retranscribing = ref(false)
const editingIdx = ref(-1)
const editText = ref('')
const lineRefs = {}

const query = computed(() => String(route.query.q || ''))
const visibleSegments = computed(() =>
  filterCluster.value ? segments.value.filter((s) => s.speaker === filterCluster.value) : segments.value,
)

function colorOf(cluster) {
  return speakers.value.find((s) => s.cluster === cluster)?.color || 'var(--muted)'
}

function highlight(text) {
  let html = escapeHtml(text)
  const q = query.value.trim()
  if (q.length >= 1) {
    const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi')
    html = html.replace(re, (m) => `<mark class="hl">${m}</mark>`)
  }
  return html
}

async function load() {
  try {
    const data = await api.transcript(fileId.value)
    file.value = data.file
    transcript.value = data.transcript
    segments.value = data.segments
    speakers.value = data.speakers
    audioSrc.value = api.audioUrl(fileId.value)
    await refreshSpeakers()
    const t = Number(route.query.t)
    if (t) seek(t)
  } catch (e) {
    toast(e.message, 'err')
  }
}

function setLineRef(idx, el) {
  if (el) lineRefs[idx] = el
}

function onLoaded() {
  durationMs.value = (audioEl.value?.duration || file.value?.duration || 0) * 1000
}

function onTimeUpdate() {
  if (!audioEl.value) return
  currentMs.value = audioEl.value.currentTime * 1000
  const idx = segments.value.findIndex((s) => currentMs.value >= s.start_ms && currentMs.value < s.end_ms)
  if (idx >= 0) {
    const seg = segments.value[idx]
    if (seg.idx !== activeIdx.value) {
      activeIdx.value = seg.idx
      // 仅当可见且不在编辑时滚动
      if (editingIdx.value < 0) {
        const el = lineRefs[seg.idx]
        el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      }
    }
  }
}

function seek(ms) {
  if (!audioEl.value) return
  audioEl.value.currentTime = Math.max(0, ms) / 1000
  audioEl.value.play().catch(() => {})
}

function setRate(r) {
  rate.value = r
  if (audioEl.value) audioEl.value.playbackRate = r
}

function startEdit(seg) {
  editingIdx.value = seg.idx
  editText.value = seg.text
  nextTick(() => {
    const el = document.querySelector('textarea.textarea')
    el?.focus()
  })
}

async function saveEdit(seg) {
  if (editingIdx.value !== seg.idx) return
  const text = editText.value.trim()
  editingIdx.value = -1
  if (!text || text === seg.text) return
  try {
    await api.saveSegment(fileId.value, seg.idx, { text })
    seg.text = text
    toast('已保存', 'ok', 1500)
  } catch (e) {
    toast(e.message, 'err')
  }
}

function cancelEdit() {
  editingIdx.value = -1
}

async function changeSpeaker(seg, cluster) {
  try {
    await api.saveSegment(fileId.value, seg.idx, { speaker: cluster })
    seg.speaker = cluster
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function renameSpeaker(s, name) {
  const value = String(name || '').trim()
  if (!value || value === s.name) return
  try {
    await api.setSpeaker(fileId.value, s.cluster, { display_name: value })
    s.name = value
    s.display_name = value
    toast('已改名', 'ok', 1500)
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function bindSpeaker(s, speakerId) {
  try {
    if (!speakerId) {
      await api.setSpeaker(fileId.value, s.cluster, { display_name: s.name })
      toast('已解绑', 'ok', 1500)
      return
    }
    await api.setSpeaker(fileId.value, s.cluster, { speaker_id: Number(speakerId) })
    await load()
    toast('已绑定声纹，之后同声音会自动识别', 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function enroll(s) {
  const name = prompt('登记到声纹库，姓名：', s.name)
  if (!name) return
  try {
    await api.enrollCluster(fileId.value, s.cluster, name.trim(), true)
    await refreshSpeakers()
    await load()
    toast(`已登记声纹：${name}`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

function copyAll() {
  const names = Object.fromEntries(speakers.value.map((s) => [s.cluster, s.name]))
  const text = segments.value
    .map((s) => `[${fmtClock(s.start_ms)}] ${names[s.speaker] || s.speaker}：${s.text}`)
    .join('\n')
  navigator.clipboard
    ?.writeText(text)
    .then(() => toast('全文已复制', 'ok', 1600))
    .catch(() => toast('复制失败', 'err'))
}

function doExport() {
  window.location.href = api.exportUrl([fileId.value], exportFmt.value)
}

async function enqueue() {
  try {
    await api.batch([fileId.value], 'enqueue')
    retranscribing.value = true
    toast('已加入队列', 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function retranscribe() {
  if (!confirm('重新转写会清空当前结果（原录音不动），继续？')) return
  try {
    await api.batch([fileId.value], 'reset')
    retranscribing.value = true
    toast('已重新排队', 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

// 转写完成后自动刷新
watch(
  () => store.live?.queue?.done,
  async () => {
    if (retranscribing.value) {
      const d = await api.transcript(fileId.value)
      if (d.transcript) {
        retranscribing.value = false
        await load()
        toast('转写完成', 'ok')
      }
    }
  },
)

onMounted(load)
</script>

<style scoped>
.sel-inline {
  border: none;
  background: transparent;
  color: var(--text-2);
  font-family: inherit;
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  padding: 0 2px;
  max-width: 88px;
}
.sel-inline:hover {
  color: var(--accent);
}
</style>
