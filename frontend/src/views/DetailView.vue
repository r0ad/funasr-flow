<template>
  <div class="stack" style="gap: 16px">
    <!-- 头部 -->
    <div class="card detail-head">
      <button class="btn ghost icon" title="返回录音库" @click="$router.push('/')">
        <Icon name="arrowLeft" :size="16" />
      </button>
      <div style="min-width: 0; flex: 1">
        <div class="file-title truncate">{{ file?.name || '加载中…' }}</div>
        <div class="meta">
          <span v-if="file?.duration"><Icon name="clock" :size="12" /> {{ fmtDuration(file.duration) }}</span>
          <span v-if="transcript?.model" class="truncate" style="max-width: 320px">模型 {{ transcript.model }}</span>
          <span v-if="transcript?.asr_ms">识别 {{ (transcript.asr_ms / 1000).toFixed(1) }}s</span>
          <span>{{ segments.length }} 句</span>
          <span>{{ speakers.length }} 位说话人</span>
          <span v-if="file?.status && file.status !== 'done'" class="badge" :class="STATUS_META[file.status]?.tone">
            {{ STATUS_META[file.status]?.label }}
          </span>
        </div>
      </div>
      <div class="row" style="gap: 8px">
        <label class="spknum" title="这通电话里有几个人说话？指定后重新转写会按人数收敛">
          人数
          <select v-model.number="spkNum" class="select" style="height: 34px">
            <option :value="0">自动</option>
            <option :value="2">2 人</option>
            <option :value="3">3 人</option>
            <option :value="4">4 人</option>
          </select>
        </label>
        <button class="btn" :disabled="retranscribing" @click="retranscribe">
          <Icon name="refresh" :size="15" /> {{ retranscribing ? '已排队' : '重新转写' }}
        </button>
        <div class="row" style="gap: 0">
          <select v-model="exportFmt" class="select" style="border-radius: 9px 0 0 9px; border-right: none">
            <option value="txt">TXT</option>
            <option value="md">Markdown</option>
            <option value="srt">SRT 字幕</option>
            <option value="json">JSON</option>
          </select>
          <button class="btn" style="border-radius: 0 9px 9px 0" :disabled="!segments.length" @click="doExport">
            <Icon name="download" :size="15" /> 导出
          </button>
        </div>
      </div>
    </div>

    <!-- 空态 -->
    <div v-if="!segments.length" class="card empty">
      <div class="art">
        <Icon :name="file?.status === 'failed' ? 'alert' : 'mic'" :size="26" />
      </div>
      <template v-if="file?.status === 'failed'">
        <h3>转写失败</h3>
        <p class="hint">{{ file?.error || '这条录音没有转写成功，可以重新试一次。' }}</p>
      </template>
      <template v-else-if="file?.status === 'done'">
        <h3>没有检测到语音</h3>
        <p class="hint">已经跑过识别，但没有分出一句话（可能是静音、纯音乐，或者短到 VAD 都不分段）。</p>
      </template>
      <template v-else>
        <h3>还没有转写结果</h3>
        <p class="hint">把这条录音加入队列，识别完成后文字稿会自动出现在这里。</p>
      </template>
      <button class="btn primary" style="margin-top: 16px" @click="enqueue">
        <Icon name="zap" :size="15" /> 开始转写
      </button>
    </div>

    <div v-else class="detail-layout">
      <!-- 左栏 -->
      <div class="stack" style="gap: 14px">
        <div class="player">
          <div class="row" style="align-items: baseline">
            <span class="big-time">{{ fmtClock(currentMs) }}<small>/ {{ fmtClock(durationMs) }}</small></span>
            <span class="spacer" />
            <div class="rate">
              <button
                v-for="r in [0.75, 1, 1.25, 1.5]"
                :key="r"
                :class="{ on: rate === r }"
                @click="setRate(r)"
              >
                {{ r }}×
              </button>
            </div>
          </div>
          <audio
            ref="audioEl"
            :src="audioSrc"
            controls
            preload="metadata"
            style="width: 100%; margin-top: 10px; height: 36px"
            @timeupdate="onTimeUpdate"
            @loadedmetadata="onLoaded"
          />
        </div>

        <div class="card flush">
          <div class="card-head">
            <Icon name="users" :size="15" />
            说话人
            <span class="spacer" />
            <span v-if="filterCluster" class="badge accent">只看一人</span>
            <span v-else class="small muted">点击放大镜筛选</span>
          </div>
          <div class="card-pad" style="padding: 12px">
            <div v-for="s in speakers" :key="s.cluster" class="speaker-card">
              <span class="avatar" :style="{ background: s.color }">{{ initial(s.name) }}</span>
              <div class="who">
                <div class="row" style="gap: 6px">
                  <input
                    class="input"
                    style="height: 30px; font-size: 13px; font-weight: 560"
                    :value="s.name"
                    :title="s.cluster"
                    @change="renameSpeaker(s, $event.target.value)"
                  />
                  <span class="dur nowrap">{{ Math.round(s.seconds || 0) }}s</span>
                </div>
                <div class="row" style="gap: 4px; margin-top: 7px">
                  <select
                    class="select"
                    style="height: 28px; font-size: 12px; flex: 1; min-width: 0"
                    :value="s.speaker_id || ''"
                    @change="bindSpeaker(s, $event.target.value)"
                  >
                    <option value="">未绑定声纹</option>
                    <option v-for="sp in store.speakers" :key="sp.id" :value="sp.id">
                      {{ sp.name }}{{ sp.has_embedding ? '' : '（无声纹）' }}
                    </option>
                  </select>
                  <button class="btn ghost icon sm" title="登记声纹" @click="enroll(s)">
                    <Icon name="plus" :size="13" />
                  </button>
                  <button
                    class="btn ghost icon sm"
                    :class="{ active: filterCluster === s.cluster }"
                    title="只看这个人"
                    @click="filterCluster = filterCluster === s.cluster ? null : s.cluster"
                  >
                    <Icon name="search" :size="13" />
                  </button>
                  <button class="btn ghost icon sm" title="试听（跳到第一句）" @click="previewCluster(s)">
                    <Icon name="play" :size="13" />
                  </button>
                </div>
                <select
                  v-if="speakers.length > 1"
                  class="select"
                  style="height: 26px; font-size: 11.5px; color: var(--muted); margin-top: 7px; width: 100%"
                  :value="''"
                  title="把这个人并进另一个人（收拾过聚类）"
                  @change="mergeSpeaker(s, $event.target.value, $event)"
                >
                  <option value="">合并到…</option>
                  <option
                    v-for="o in speakers.filter((x) => x.cluster !== s.cluster)"
                    :key="o.cluster"
                    :value="o.cluster"
                  >
                    {{ o.name }}
                  </option>
                </select>
              </div>
            </div>
            <p v-if="!speakers.length" class="small muted">未检测到说话人</p>
          </div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="card flush">
        <div class="card-head">
          文字稿
          <span class="small muted">单击文字编辑，回车保存</span>
          <span class="spacer" />
          <button v-if="filterCluster" class="btn sm ghost" @click="filterCluster = null">
            <Icon name="x" :size="13" /> 清除筛选
          </button>
          <button class="btn sm ghost" title="复制全文" @click="copyAll">
            <Icon name="copy" :size="14" /> 复制
          </button>
        </div>
        <div class="transcript" style="max-height: calc(100vh - 290px); overflow: auto">
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
                <select class="sel-inline" :value="seg.speaker" @change="changeSpeaker(seg, $event.target.value)">
                  <option v-for="s in speakers" :key="s.cluster" :value="s.cluster">{{ s.name }}</option>
                </select>
              </span>
            </div>

            <div>
              <textarea
                v-if="editingIdx === seg.idx"
                ref="editBox"
                v-model="editText"
                rows="2"
                @blur="saveEdit(seg)"
                @keydown.enter.exact.prevent="saveEdit(seg)"
                @keydown.esc.prevent="cancelEdit"
              />
              <div v-else class="text" title="单击编辑" @click="startEdit(seg)" v-html="highlight(seg.text)" />
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
// 0 = 自动；指定人数会作为任务级 options 覆盖全局 spk_num
const spkNum = ref(0)
const editingIdx = ref(-1)
const editText = ref('')
const lineRefs = {}

const query = computed(() => String(route.query.q || ''))
const visibleSegments = computed(() =>
  filterCluster.value ? segments.value.filter((s) => s.speaker === filterCluster.value) : segments.value,
)

function initial(name) {
  const t = String(name || '').trim()
  if (!t) return '?'
  // 中文取第一个字，英文取首字母
  return /[a-zA-Z]/.test(t[0]) ? t[0].toUpperCase() : t[0]
}

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

async function mergeSpeaker(s, target, ev) {
  if (!target) return
  const other = speakers.value.find((x) => x.cluster === target)
  if (!confirm(`把「${s.name}」的 ${s.utt_count ?? ''} 句话合并进「${other ? other.name : target}」？`)) {
    if (ev) ev.target.value = ''
    return
  }
  try {
    const r = await api.mergeCluster(fileId.value, s.cluster, target)
    await load()
    toast(`已合并 ${r.moved ?? 0} 句`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
    if (ev) ev.target.value = ''
  }
}

function previewCluster(s) {
  const seg = segments.value.find((x) => String(x.speaker) === String(s.cluster))
  if (!seg) return
  seek(seg.start_ms)
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
  const tip = spkNum.value
    ? `按 ${spkNum.value} 人重新转写，会清空当前结果（原录音不动），继续？`
    : '重新转写会清空当前结果（原录音不动），继续？'
  if (!confirm(tip)) return
  try {
    await api.batch([fileId.value], 'reset', spkNum.value ? { spk_num: spkNum.value } : null)
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
