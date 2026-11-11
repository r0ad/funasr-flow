<template>
  <div class="toasts">
    <TransitionGroup name="toast">
      <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.type">
        <Icon :name="t.type === 'err' ? 'alert' : t.type === 'ok' ? 'check' : 'zap'" :size="15" />
        <span>{{ t.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup>
import Icon from './Icon.vue'
import { store } from '../store'
</script>
