<template>
  <div
    v-if="visible"
    class="dock card"
    :class="{ compact: !active, dragging: dragActive }"
    :style="dockStyle"
    :title="pos ? '拖动可移动；双击回到右下角' : '拖动可移动'"
    @pointerdown="startDrag"
    @dblclick="resetPos"
  >
    <template v-if="importing">
      <div class="dock-head">
        <span class="badge warn"><span class="bullet" />导入中</span>
        <span class="spacer" />
        <button class="btn ghost icon sm" title="收起" @click="dismissed = true">
          <Icon name="x" :size="14" />
        </button>
      </div>
      <div class="dock-name truncate" :title="imp.dir">{{ imp.current || imp.dir }}</div>
      <div class="row small muted" style="justify-content: space-between">
        <span>{{ imp.done || 0 }} / {{ imp.total || 0 }} 个文件</span>
        <span class="mono">{{ importPct }}%</span>
      </div>
      <div class="bar" style="margin: 6px 0 8px">
        <span :style="{ width: `${importPct}%` }" />
      </div>
      <div class="row small muted">
        <span>新增 {{ imp.added || 0 }} · 跳过 {{ imp.skipped || 0 }}</span>
        <span class="spacer" />
        <button class="btn sm danger" @click="cancelImport">停止</button>
      </div>
    </template>

    <template v-else-if="active">
      <div class="dock-head">
        <span class="badge accent"><span class="bullet" />转写中</span>
        <span class="spacer" />
        <button class="btn ghost icon sm" title="收起" @click="dismissed = true">
          <Icon name="x" :size="14" />
        </button>
      </div>
      <RouterLink class="dock-name truncate" :to="`/files/${active.file_id}`" :title="active.name">
        {{ active.name }}
      </RouterLink>
      <div class="row small muted" style="justify-content: space-between">
        <span>{{ stageLabel }}</span>
        <span class="mono">{{ Math.round((active.progress || 0) * 100) }}%</span>
      </div>
      <div class="bar" style="margin: 6px 0 8px">
        <span :style="{ width: `${Math.min(100, (active.progress || 0) * 100)}%` }" />
      </div>
      <div class="row small muted">
        <span v-if="queue.queued">还有 {{ queue.queued }} 个排队</span>
        <span v-else>最后一个</span>
        <span class="spacer" />
        <button class="btn sm danger" @click="cancelAll">停止</button>
      </div>
    </template>

    <template v-else>
      <div class="row small">
        <Icon name="queue" :size="15" />
        <span>队列中还有 <b>{{ queue.queued }}</b> 个任务</span>
        <span class="spacer" />
        <button class="btn ghost icon sm" title="收起" @click="dismissed = true">
          <Icon name="x" :size="14" />
        </button>
      </div>
      <div v-if="etaText" class="small muted" style="margin-top: 2px">预计 {{ etaText }} 跑完</div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { STAGE_META, fmtDuration, store, toast } from '../store'

const dismissed = ref(false)
const pos = ref(loadPos()) // {left, top}，null 表示用默认的右下角
const dragActive = ref(false)
let drag = null

const dockStyle = computed(() =>
  pos.value ? { left: `${pos.value.left}px`, top: `${pos.value.top}px`, right: 'auto', bottom: 'auto' } : null,
)

function loadPos() {
  try {
    const raw = localStorage.getItem('flow.dockPos')
    if (!raw) return null
    const p = JSON.parse(raw)
    if (typeof p?.left === 'number' && typeof p?.top === 'number') return clamp(p)
  } catch {
    /* 忽略坏数据 */
  }
  return null
}

function clamp(p) {
  const w = 300
  const h = 150
  return {
    left: Math.max(0, Math.min(p.left, window.innerWidth - w)),
    top: Math.max(0, Math.min(p.top, window.innerHeight - h)),
  }
}

function startDrag(e) {
  // 按钮、链接不参与拖动，避免点不动
  if (e.target.closest('button, a, input, select')) return
  const el = e.currentTarget
  const r = el.getBoundingClientRect()
  drag = { dx: e.clientX - r.left, dy: e.clientY - r.top }
  dragActive.value = true
  pos.value = { left: r.left, top: r.top }
  try {
    el.setPointerCapture(e.pointerId)
  } catch {
    /* 某些浏览器不支持，忽略 */
  }
  window.addEventListener('pointermove', onDrag)
  window.addEventListener('pointerup', endDrag)
}

function onDrag(e) {
  if (!drag) return
  pos.value = clamp({ left: e.clientX - drag.dx, top: e.clientY - drag.dy })
}

function endDrag() {
  drag = null
  dragActive.value = false
  window.removeEventListener('pointermove', onDrag)
  window.removeEventListener('pointerup', endDrag)
  if (pos.value) {
    try {
      localStorage.setItem('flow.dockPos', JSON.stringify(pos.value))
    } catch {
      /* 隐私模式等场景写不了，忽略 */
    }
  }
}

function resetPos() {
  pos.value = null
  try {
    localStorage.removeItem('flow.dockPos')
  } catch {
    /* 忽略 */
  }
}

window.addEventListener('resize', () => {
  if (pos.value) pos.value = clamp(pos.value)
})
const active = computed(() => store.live?.active || null)
const imp = computed(() => store.live?.import || {})
const importing = computed(() => Boolean(imp.value?.running))
const importPct = computed(() => {
  const t = imp.value?.total || 0
  return t ? Math.min(100, Math.round(((imp.value?.done || 0) / t) * 100)) : 0
})
const queue = computed(() => store.live?.queue || {})

// 新任务开始后重新显示
watch(
  () => active.value?.id,
  (id) => {
    if (id) dismissed.value = false
  },
)

const visible = computed(() => {
  if (dismissed.value) return false
  return Boolean(active.value) || Boolean(importing.value) || (queue.value.queued || 0) > 0
})

const stageLabel = computed(() => STAGE_META[active.value?.stage] || '处理中')

const etaText = computed(() => {
  const rtf = queue.value.rtf
  const secs = queue.value.queued_seconds
  if (!rtf || !secs) return ''
  return fmtDuration(secs * rtf)
})

async function cancelImport() {
  try {
    await api.cancelImport()
    toast('已请求取消导入', 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function cancelAll() {
  try {
    const res = await api.cancelQueue('all')
    toast(`已请求停止：中断 ${res.interrupted} 个，取消排队 ${res.canceled} 个`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}
</script>

<style scoped>
.dock {
  position: fixed;
  bottom: 18px;
  right: 20px;
  width: 300px;
  cursor: grab;
  touch-action: none; /* 让 pointermove 拖动在触屏上也生效 */
  padding: 11px 13px;
  z-index: 45;
  animation: slidein 0.16s ease;
}
.dock-head {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}
.dock-name {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 6px;
}
.dock.compact {
  padding: 9px 12px;
}
.dock.dragging {
  cursor: grabbing;
  user-select: none;
}
@media (max-width: 760px) {
  .dock {
    right: 10px;
    width: calc(100% - 20px);
  }
}
</style>
