<template>
  <span v-if="sort.field === field" style="color: var(--accent); font-weight: 700">
    {{ sort.order === 'asc' ? '↑' : '↓' }}
  </span>
</template>

<script setup>
defineProps({
  field: { type: String, required: true },
  sort: { type: Object, required: true },
})
</script>
