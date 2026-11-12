<template>
  <div ref="root" class="combo">
    <div class="combo-box" :class="{ on: open }">
      <input
        ref="input"
        v-model="text"
        class="combo-input"
        :placeholder="placeholder"
        spellcheck="false"
        autocomplete="off"
        @focus="open = true"
        @click="open = true"
        @input="onInput"
        @keydown="onKey"
      />
      <button
        v-if="text"
        type="button"
        class="combo-btn"
        title="清空，改用自定义 id"
        @click="clear"
      >
        <Icon name="x" :size="13" />
      </button>
      <button
        type="button"
        class="combo-btn combo-caret"
        :class="{ flip: open }"
        title="选择模型"
        @click="toggle"
      >
        <Icon name="chevron" :size="14" />
      </button>
    </div>

    <div v-if="open" class="combo-panel" role="listbox">
      <div v-if="!filtered.length" class="combo-empty">
        没有匹配的模型。可以直接输入 ModelScope / HuggingFace 上的仓库 id。
      </div>
      <button
        v-for="(m, i) in filtered"
        :key="m.id"
        type="button"
        class="combo-item"
        :class="{ picking: i === active, current: m.id === modelValue }"
        role="option"
        :aria-selected="m.id === modelValue"
        @mousemove="active = i"
        @mousedown.prevent="pick(m)"
      >
        <span class="ci-head">
          <span class="ci-title">{{ m.label || m.id }}</span>
          <span class="ci-size">{{ m.size }}</span>
          <Icon v-if="m.id === modelValue" name="check" :size="13" class="ci-check" />
        </span>
        <span class="ci-id mono">{{ m.id }}</span>
        <span v-if="m.note" class="ci-note">{{ m.note }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] },
  placeholder: { type: String, default: '输入或选择模型 id' },
})
const emit = defineEmits(['update:modelValue'])

const root = ref(null)
const input = ref(null)
const text = ref(props.modelValue || '')
const open = ref(false)
const active = ref(0)
const touched = ref(false) // 用户是否动过输入框（决定要不要按文字过滤）

watch(
  () => props.modelValue,
  (v) => {
    if (v !== text.value) text.value = v || ''
  },
)

const filtered = computed(() => {
  const q = touched.value ? text.value.trim().toLowerCase() : ''
  if (!q) return props.options
  return props.options.filter((m) =>
    [m.id, m.label, m.note, ...(m.tags || [])].join(' ').toLowerCase().includes(q),
  )
})

watch(filtered, () => {
  active.value = 0
})

function onInput() {
  touched.value = true
  open.value = true
  emit('update:modelValue', text.value.trim())
  nextTick(() => {
    const i = filtered.value.findIndex((m) => m.id === text.value.trim())
    if (i >= 0) active.value = i
  })
}

function pick(m) {
  text.value = m.id
  touched.value = false
  open.value = false
  emit('update:modelValue', m.id)
  input.value?.blur()
}

function clear() {
  text.value = ''
  touched.value = false
  open.value = true
  emit('update:modelValue', '')
  nextTick(() => input.value?.focus())
}

function toggle() {
  open.value = !open.value
  if (open.value) nextTick(() => input.value?.focus())
}

function onKey(e) {
  if (e.key === 'Escape') {
    open.value = false
    return
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    open.value = true
    const n = filtered.value.length
    if (!n) return
    active.value = (active.value + (e.key === 'ArrowDown' ? 1 : -1) + n) % n
    return
  }
  if (e.key === 'Enter') {
    const m = filtered.value[active.value]
    if (open.value && m) {
      e.preventDefault()
      pick(m)
    }
  }
}

// 点外面才关面板；面板里的选项用 mousedown.prevent，不会先触发 blur
function onDocDown(e) {
  if (!open.value) return
  if (root.value && !root.value.contains(e.target)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))
</script>

<style scoped>
.combo {
  position: relative;
}
.combo-box {
  display: flex;
  align-items: center;
  height: 36px;
  border: 1px solid var(--border-strong);
  border-radius: 9px;
  background: var(--panel);
  transition: border-color 0.14s var(--ease), box-shadow 0.14s var(--ease);
}
.combo-box:hover {
  border-color: var(--muted);
}
.combo-box.on {
  border-color: var(--accent);
  box-shadow: var(--ring);
}
.combo-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  padding: 0 6px 0 11px;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.combo-input::placeholder {
  color: var(--faint);
}
.combo-btn {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  margin-right: 4px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--faint);
  cursor: pointer;
}
.combo-btn:hover {
  background: var(--panel-3);
  color: var(--text);
}
.combo-caret :deep(svg) {
  transform: rotate(90deg);
  transition: transform 0.16s var(--ease);
}
.combo-caret.flip :deep(svg) {
  transform: rotate(-90deg);
}

.combo-panel {
  position: absolute;
  z-index: 40;
  left: 0;
  right: 0;
  top: calc(100% + 6px);
  max-height: 320px;
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: var(--shadow-lg);
  animation: rise 0.14s var(--ease);
}
.combo-item {
  display: block;
  width: 100%;
  padding: 8px 9px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.combo-item.picking {
  background: var(--accent-soft);
}
.combo-item.current .ci-title {
  color: var(--accent);
}
.ci-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.ci-title {
  font-size: 13px;
  font-weight: 600;
}
.ci-size {
  font-size: 11.5px;
  color: var(--muted);
}
.ci-check {
  margin-left: auto;
  color: var(--accent);
}
.ci-id {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  color: var(--muted);
  word-break: break-all;
}
.ci-note {
  display: block;
  margin-top: 3px;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--faint);
}
.combo-empty {
  padding: 10px 10px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
}
</style>
