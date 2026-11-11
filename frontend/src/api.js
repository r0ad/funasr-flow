// 所有后端交互的唯一入口。换存储、加鉴权都只需改这里。
const BASE = import.meta.env.VITE_API_BASE || ''

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path, { method = 'GET', body, headers, raw = false } = {}) {
  const opts = { method, headers: { ...(headers || {}) } }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(BASE + path, opts)
  } catch (e) {
    throw new ApiError('无法连接后端服务，请确认 `funasr-flow serve` 正在运行', 0)
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data)
    } catch {
      /* 保留 statusText */
    }
    throw new ApiError(detail || `请求失败 (${res.status})`, res.status)
  }
  if (raw) return res
  if (res.status === 204) return null
  return res.json()
}

function qs(params) {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '') continue
    p.append(k, String(v))
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const api = {
  // ---- 文件
  listFiles: (params) => request(`/api/files${qs(params)}`),
  getFile: (id) => request(`/api/files/${id}`),
  scan: (dir) => request('/api/files/scan', { method: 'POST', body: dir ? { dir } : {} }),
  batch: (ids, action, options) =>
    request('/api/files/batch', { method: 'POST', body: { ids, action, options } }),
  deleteFile: (id) => request(`/api/files/${id}`, { method: 'DELETE' }),
  audioUrl: (id) => `${BASE}/api/files/${id}/audio`,

  // ---- 转写结果
  transcript: (id) => request(`/api/transcripts/${id}`),
  saveSegment: (fileId, idx, patch) =>
    request(`/api/transcripts/${fileId}/segments/${idx}`, {
      method: 'PATCH',
      body: typeof patch === 'string' ? { text: patch } : patch,
    }),
  setSpeaker: (fileId, cluster, patch) =>
    request(`/api/transcripts/${fileId}/speakers/${encodeURIComponent(cluster)}`, {
      method: 'PATCH',
      body: patch,
    }),
  enrollCluster: (fileId, cluster, name, overwrite = false) =>
    request(`/api/transcripts/${fileId}/speakers/${encodeURIComponent(cluster)}/enroll`, {
      method: 'POST',
      body: { name, overwrite },
    }),
  bulkAssign: (fileId, cluster, speakerId) =>
    request('/api/transcripts/bulk-assign', {
      method: 'POST',
      body: { file_id: fileId, cluster, speaker_id: speakerId },
    }),
  mergeCluster: (fileId, cluster, target) =>
    request(`/api/transcripts/${fileId}/speakers/${encodeURIComponent(cluster)}/merge`, {
      method: 'POST',
      body: { target },
    }),

  // ---- 搜索 / 导出
  search: (q, limit = 60) => request(`/api/search${qs({ q, limit })}`),
  exportUrl: (ids, format) => `${BASE}/api/export${qs({ ids: ids.join(','), format })}`,

  // ---- 声纹库
  listSpeakers: () => request('/api/speakers'),
  createSpeaker: (name, notes) => request('/api/speakers', { method: 'POST', body: { name, notes } }),
  patchSpeaker: (id, patch) => request(`/api/speakers/${id}`, { method: 'PATCH', body: patch }),
  deleteSpeaker: (id) => request(`/api/speakers/${id}`, { method: 'DELETE' }),
  mergeSpeakers: (targetId, sourceId) =>
    request(`/api/speakers/${targetId}/merge`, { method: 'POST', body: { source_id: sourceId } }),
  speakerFiles: (id) => request(`/api/speakers/${id}/files`),
  /** 用录音库里某条录音的一段音频新建声纹 */
  createSpeakerFromFile: ({ fileId, startMs = 0, endMs = 0, name, overwrite = false }) =>
    request(
      `/api/speakers/from-file${qs({
        file_id: fileId,
        start_ms: startMs,
        end_ms: endMs,
        name,
        overwrite,
      })}`,
      { method: 'POST' },
    ),
  /** 用录音库里某条录音的一段音频，覆盖已有说话人的声纹 */
  enrollSpeakerFromFile: (speakerId, { fileId, startMs = 0, endMs = 0 }) =>
    request(
      `/api/speakers/${speakerId}/enroll-from-file${qs({
        file_id: fileId,
        start_ms: startMs,
        end_ms: endMs,
      })}`,
      { method: 'POST' },
    ),
  /** 上传一段干净人声登记声纹（raw body，绕开 multipart 依赖） */
  enrollUpload: async (file, { name, speakerId } = {}) => {
    const path = speakerId
      ? `/api/speakers/${speakerId}/enroll`
      : `/api/speakers/enroll${qs({ name })}`
    const res = await fetch(BASE + path, { method: 'POST', body: file })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new ApiError(data.detail || '登记失败', res.status)
    return data
  },

  // ---- 系统
  health: () => request('/api/health'),
  stats: () => request('/api/stats'),
  state: () => request('/api/state'),
  settings: () => request('/api/settings'),
  saveSettings: (values) => request('/api/settings', { method: 'PATCH', body: { values } }),
  cancelQueue: (mode = 'queued') => request(`/api/queue/cancel${qs({ mode })}`, { method: 'POST' }),
  clearQueue: () => request('/api/queue/clear', { method: 'POST' }),
}

/** 构造 SSE 连接（状态变化推送） */
export function openEvents(onMessage, onError) {
  const es = new EventSource(`${BASE}/api/events`)
  es.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch {
      /* 忽略 parse 失败 */
    }
  }
  if (onError) es.onerror = onError
  return es
}
