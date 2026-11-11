<template>
  <div class="stack" style="gap: 16px">
    <div class="card card-pad">
      <div class="row wrap" style="gap: 12px">
        <div>
          <div style="font-weight: 650">共 {{ speakers.length }} 位说话人</div>
          <p class="small muted">
            声纹用来在<b>不同录音</b>里认出同一个人。登记一次，之后自动匹配并套用名字。
          </p>
        </div>
        <span class="spacer" />
        <button class="btn primary" @click="openCreate">
          <Icon name="plus" :size="15" /> 新增声纹
        </button>
      </div>
    </div>

    <div v-if="!speakers.length" class="card empty">
      <h3>声纹库还是空的</h3>
      <p class="small">
        两种登记方式：① 在转写详情页点说话人旁边的 <span class="kbd">+</span>；
        ② 这里上传一段只有这个人说话、没有杂音的音频（5 秒以上即可）。
      </p>
    </div>

    <div v-else class="grid grid-3">
      <div v-for="s in speakers" :key="s.id" class="card card-pad" style="position: relative">
        <div class="row" style="gap: 8px">
          <span class="speaker-dot" :style="{ background: s.color || 'var(--accent)', width: '12px', height: '12px' }" />
          <input
            class="input"
            style="height: 30px; flex: 1; min-width: 0; font-weight: 600"
            :value="s.name"
            @change="rename(s, $event.target.value)"
          />
        </div>
        <div class="row wrap small muted" style="margin-top: 8px; gap: 6px">
          <span class="badge" :class="s.has_embedding ? 'ok' : 'warn'">
            {{ s.has_embedding ? '已有声纹' : '仅名字' }}
          </span>
          <span>{{ s.file_count }} 个录音</span>
        </div>
        <div class="row" style="margin-top: 10px; gap: 4px">
          <label class="btn sm ghost" style="cursor: pointer">
            <Icon name="upload" :size="13" /> 更新声纹
            <input type="file" accept="audio/*" hidden @change="uploadEmbedding(s, $event)" />
          </label>
          <button class="btn sm ghost" @click="openMerge(s)">合并到…</button>
          <span class="spacer" />
          <button class="btn sm ghost danger" @click="remove(s)">
            <Icon name="trash" :size="13" />
          </button>
        </div>
      </div>
    </div>

    <!-- 新增 -->
    <Modal :open="creating" title="新增声纹" @close="creating = false">
      <div class="stack">
        <div class="field">
          <label>姓名</label>
          <input v-model="form.name" class="input" placeholder="例如：妈妈、老爸、张哥" />
        </div>
        <div class="field">
          <label>音频（可选）</label>
          <input ref="fileInput" type="file" accept="audio/*" class="input" style="padding-top: 6px" />
          <span class="hint">选一段只有这个人说话、5 秒以上的干净音频。也可以先只建名字，之后在转写页登记。</span>
        </div>
      </div>
      <template #footer>
        <button class="btn" @click="creating = false">取消</button>
        <button class="btn primary" :disabled="!form.name.trim() || saving" @click="create">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </template>
    </Modal>

    <!-- 合并 -->
    <Modal :open="Boolean(mergeSource)" title="合并声纹" @close="mergeSource = null">
      <p class="small" v-if="mergeSource">
        把 <b>{{ mergeSource.name }}</b> 的所有录音归到另一位说话人，并删除这条声纹。
      </p>
      <div class="field" style="margin-top: 12px">
        <label>合并到</label>
        <select v-model="mergeTarget" class="select">
          <option value="">选择目标…</option>
          <option v-for="s in speakers.filter((x) => x.id !== mergeSource?.id)" :key="s.id" :value="s.id">
            {{ s.name }}
          </option>
        </select>
      </div>
      <template #footer>
        <button class="btn" @click="mergeSource = null">取消</button>
        <button class="btn primary" :disabled="!mergeTarget" @click="doMerge">合并</button>
      </template>
    </Modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { refreshSpeakers, store, toast } from '../store'

const speakers = computed(() => store.speakers)
const creating = ref(false)
const saving = ref(false)
const form = ref({ name: '' })
const fileInput = ref(null)
const mergeSource = ref(null)
const mergeTarget = ref(null)

onMounted(refreshSpeakers)

function openCreate() {
  form.value = { name: '' }
  creating.value = true
}

async function create() {
  saving.value = true
  try {
    const file = fileInput.value?.files?.[0]
    if (file) {
      await api.enrollUpload(file, { name: form.value.name.trim() })
    } else {
      await api.createSpeaker(form.value.name.trim())
    }
    await refreshSpeakers()
    creating.value = false
    toast(`已新增：${form.value.name}`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    saving.value = false
  }
}

async function rename(s, name) {
  const value = String(name || '').trim()
  if (!value || value === s.name) return
  try {
    await api.patchSpeaker(s.id, { name: value })
    await refreshSpeakers()
    toast('已改名', 'ok', 1500)
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function uploadEmbedding(s, event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    await api.enrollUpload(file, { speakerId: s.id })
    await refreshSpeakers()
    toast(`已更新 ${s.name} 的声纹`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    event.target.value = ''
  }
}

function openMerge(s) {
  mergeSource.value = s
  mergeTarget.value = null
}

async function doMerge() {
  try {
    await api.mergeSpeakers(Number(mergeTarget.value), mergeSource.value.id)
    await refreshSpeakers()
    toast('已合并', 'ok')
    mergeSource.value = null
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function remove(s) {
  if (!confirm(`删除声纹「${s.name}」？不会删除录音和转写结果。`)) return
  try {
    await api.deleteSpeaker(s.id)
    await refreshSpeakers()
    toast('已删除', 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}
</script>
