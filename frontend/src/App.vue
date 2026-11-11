<template>
  <div class="shell">
    <aside class="sidebar">
      <RouterLink to="/" class="brand">
        <div class="brand-mark">
          <Icon name="mic" :size="17" />
        </div>
        <div>
          <div class="brand-name">FunASR Flow</div>
          <div class="brand-sub">本地通话转写</div>
        </div>
      </RouterLink>

      <template v-for="group in navGroups" :key="group.title">
        <div class="nav-group">{{ group.title }}</div>
        <RouterLink
          v-for="item in group.items"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ active: isActive(item) }"
        >
          <Icon :name="item.icon" :size="17" />
          <span>{{ item.label }}</span>
          <span v-if="item.badge" class="badge accent badge-count">{{ item.badge }}</span>
        </RouterLink>
      </template>

      <div class="sidebar-foot">
        <span>
          <span class="dot" :style="{ background: connColor }" />
          {{ connText }}
        </span>
        <span v-if="store.stats" class="truncate">
          已转写 {{ store.stats.done_files ?? 0 }}/{{ store.stats.files ?? 0 }} 个 ·
          {{ store.stats.segments ?? 0 }} 句
        </span>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="topbar-title">
          <span class="title">{{ title }}</span>
          <span class="sub truncate">{{ subtitle }}</span>
        </div>
        <div class="spacer" />
        <router-link v-if="route.name === 'detail'" to="/" class="btn sm ghost">
          <Icon name="back" :size="15" />
          录音库
        </router-link>
        <button class="btn sm" @click="pickerOpen = true">
          <Icon name="folder" :size="15" />
          导入录音
        </button>
        <router-link to="/settings" class="btn sm ghost icon" title="设置">
          <Icon name="settings" :size="16" />
        </router-link>
      </header>

      <div class="content">
        <RouterView />
      </div>
    </div>

    <ProgressDock />
    <Toasts />
    <FolderImport :open="pickerOpen" @close="pickerOpen = false" />
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import FolderImport from './components/FolderImport.vue'
import Icon from './components/Icon.vue'
import ProgressDock from './components/ProgressDock.vue'
import Toasts from './components/Toasts.vue'
import { fmtDuration, refreshHealth, refreshStats, store } from './store'

const route = useRoute()
const pickerOpen = computed({
  get: () => store.ui.importOpen,
  set: (v) => {
    store.ui.importOpen = v
  },
})

const navGroups = computed(() => {
  const pending = store.live?.queue?.queued || 0
  return [
    {
      title: '资料',
      items: [
        { to: '/', label: '录音库', icon: 'files', badge: pending || null },
        { to: '/search', label: '全文搜索', icon: 'search' },
        { to: '/speakers', label: '声纹库', icon: 'users' },
      ],
    },
    {
      title: '系统',
      items: [{ to: '/settings', label: '设置', icon: 'settings' }],
    },
  ]
})

function isActive(item) {
  if (item.to === '/') return route.name === 'files' || route.name === 'detail'
  return route.path.startsWith(item.to)
}

const title = computed(() => route.meta?.title || 'FunASR Flow')
const subtitle = computed(() => {
  if (route.name === 'files') return libSubtitle.value
  if (route.name === 'detail') return '点击时间码跳转播放，文字可直接编辑'
  if (route.name === 'search') return '在所有已转写的句子里查找'
  if (route.name === 'speakers') return '声纹用于跨录音自动认出同一个人'
  if (route.name === 'settings') return '模型、资料库与识别参数'
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

onMounted(() => {
  refreshHealth()
  refreshStats()
})
</script>
