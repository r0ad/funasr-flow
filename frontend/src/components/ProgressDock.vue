<template>
  <div v-if="visible" class="dock card" :class="{ compact: !active }">
    <template v-if="active">
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
const active = computed(() => store.live?.active || null)
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
  return Boolean(active.value) || (queue.value.queued || 0) > 0
})

const stageLabel = computed(() => STAGE_META[active.value?.stage] || '处理中')

const etaText = computed(() => {
  const rtf = queue.value.rtf
  const secs = queue.value.queued_seconds
  if (!rtf || !secs) return ''
  return fmtDuration(secs * rtf)
})

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
@media (max-width: 760px) {
  .dock {
    right: 10px;
    width: calc(100% - 20px);
  }
}
</style>
