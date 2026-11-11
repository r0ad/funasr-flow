import { reactive } from 'vue'
import { api, openEvents } from './api'

/** 全局轻量状态：不引入 Pinia，够用且零依赖。 */
export const store = reactive({
  connection: 'connecting', // connecting | online | offline
  live: null, // /api/state 快照（队列、活动任务）
  health: null,
  settings: null,
  stats: null,
  speakers: [],
  toasts: [],
  reloadTick: 0, // 任何页面想让列表重拉时 +1
})

let es = null
let retryTimer = null

export function startLive() {
  if (es) return
  es = openEvents(
    (data) => {
      store.connection = 'online'
      store.live = data
    },
    () => {
      store.connection = 'offline'
      // EventSource 会自己重连；这里只在彻底断掉时兜底重建
      if (retryTimer) return
      retryTimer = window.setTimeout(() => {
        retryTimer = null
        try {
          es?.close()
        } catch {
          /* noop */
        }
        es = null
        startLive()
      }, 5000)
    },
  )
}

export function stopLive() {
  try {
    es?.close()
  } catch {
    /* noop */
  }
  es = null
}

let toastSeq = 0
export function toast(message, type = 'info', ttl = 3400) {
  const id = ++toastSeq
  store.toasts.push({ id, message, type })
  window.setTimeout(() => {
    const i = store.toasts.findIndex((t) => t.id === id)
    if (i >= 0) store.toasts.splice(i, 1)
  }, ttl)
}

export async function refreshStats() {
  try {
    store.stats = await api.stats()
  } catch {
    /* 统计失败不打扰用户 */
  }
}

export async function refreshSpeakers() {
  try {
    const { items } = await api.listSpeakers()
    store.speakers = items
    return items
  } catch {
    return []
  }
}

export async function refreshHealth() {
  try {
    store.health = await api.health()
    return store.health
  } catch {
    return null
  }
}

// ---------------------------------------------------------------- 格式化
export function fmtDuration(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '—'
  const s = Math.round(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
  return `${m}:${String(r).padStart(2, '0')}`
}

/** 毫秒 → mm:ss.s（列表与时间轴用） */
export function fmtClock(ms) {
  const total = Math.max(0, ms) / 1000
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${s.toFixed(1).padStart(4, '0')}`
}

export function fmtBytes(n) {
  if (!n) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export const STATUS_META = {
  idle: { label: '待处理', tone: '' },
  queued: { label: '排队中', tone: 'info' },
  running: { label: '转写中', tone: 'accent' },
  done: { label: '已完成', tone: 'ok' },
  failed: { label: '失败', tone: 'err' },
  canceled: { label: '已取消', tone: 'warn' },
  missing: { label: '文件缺失', tone: 'err' },
}

export const STAGE_META = {
  start: '准备中',
  convert: '转码',
  asr: '识别',
  diarize: '说话人聚类',
  save: '写入结果',
  done: '完成',
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}
