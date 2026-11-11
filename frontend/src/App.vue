<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">
          <Icon name="mic" :size="17" />
        </div>
        <div>
          <div class="brand-name">FunASR Flow</div>
          <div class="brand-sub">本地录音转写</div>
        </div>
      </div>

      <RouterLink v-for="item in nav" :key="item.to" :to="item.to" class="nav-item">
        <Icon :name="item.icon" :size="17" />
        <span>{{ item.label }}</span>
        <span v-if="item.badge" class="badge badge-count">{{ item.badge }}</span>
      </RouterLink>

      <div class="sidebar-foot">
        <span class="dot" :style="{ background: connColor }" />
        {{ connText }}
        <div v-if="store.stats" style="margin-top: 6px">
          已转写 {{ store.stats.done_files ?? 0 }}/{{ store.stats.files ?? 0 }} 个 ·
          {{ store.stats.segments ?? 0 }} 句
        </div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="topbar-title">
          <h1>{{ title }}</h1>
          <span class="sub">{{ subtitle }}</span>
        </div>
        <div class="spacer" />
        <button class="btn sm" :disabled="scanning" @click="pickerOpen = true">
          <Icon name="folder" :size="15" />
          {{ scanning ? '扫描中…' : '选录音目录' }}
        </button>
      </header>

      <div class="content">
        <RouterView />
      </div>
    </div>

    <ProgressDock />
    <Toasts />
    <DirPicker :open="pickerOpen" @close="pickerOpen = false" @picked="onDirPicked" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import DirPicker from './components/DirPicker.vue'
import Icon from './components/Icon.vue'
import ProgressDock from './components/ProgressDock.vue'
import Toasts from './components/Toasts.vue'
import { api } from './api'
import { fmtDuration, refreshHealth, refreshStats, store, toast } from './store'

const route = useRoute()
const scanning = ref(false)
const pickerOpen = ref(false)

const nav = computed(() => {
  const pending = store.live?.queue?.queued || 0
  return [
    { to: '/', label: '录音库', icon: 'files', badge: pending || null },
    { to: '/search', label: '全文搜索', icon: 'search' },
    { to: '/speakers', label: '声纹库', icon: 'users' },
    { to: '/settings', label: '设置', icon: 'settings' },
  ]
})

const title = computed(() => route.meta?.title || 'FunASR Flow')
const subtitle = computed(() => {
  if (route.name === 'files') return libSubtitle.value
  if (route.name === 'detail') return '点击时间码跳转播放，文字可直接编辑'
  if (route.name === 'search') return '在所有已转写的句子中查找'
  if (route.name === 'speakers') return '声纹用于跨文件自动识别同一个人'
  if (route.name === 'settings') return '模型、目录与识别参数'
  return ''
})

const libSubtitle = computed(() => {
  const st = store.stats
  if (!st) return ''
  const hours = ((st.total_seconds || 0) / 3600).toFixed(1)
  const done = st.done_files ?? 0
  return `${st.files ?? 0} 个录音 · ${hours} 小时 · 已转写 ${done} 个（${fmtDuration(st.done_seconds || 0)}）`
})

const connText = computed(() =>
  store.connection === 'online' ? '已连接' : store.connection === 'offline' ? '连接断开' : '连接中…',
)
const connColor = computed(() =>
  store.connection === 'online' ? 'var(--ok)' : store.connection === 'offline' ? 'var(--err)' : 'var(--warn)',
)

function onDirPicked() {
  refreshStats()
  store.reloadTick++
}

async function doScan() {
  scanning.value = true
  try {
    const res = await api.scan()
    const parts = [`新增 ${res.added}`, `更新 ${res.updated}`, `缺失 ${res.missing}`]
    toast(`扫描完成：${parts.join('，')}，共 ${res.total} 个文件`, 'ok')
    refreshStats()
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    scanning.value = false
    store.reloadTick++
  }
}

onMounted(() => {
  refreshHealth()
  refreshStats()
})
</script>
