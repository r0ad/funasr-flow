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
        ② 这里从录音库挑一段只有这个人说话的音频（5 秒以上即可）。
      </p>
    </div>

    <div v-else class="grid grid-3">
      <div v-for="s in speakers" :key="s.id" class="card card-pad">
        <div class="row" style="gap: 8px">
          <span
            class="speaker-dot"
            :style="{ background: s.color || 'var(--accent)', width: '12px', height: '12px' }"
          />
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
        <div class="row wrap" style="margin-top: 10px; gap: 4px">
          <button class="btn sm ghost" @click="openEnroll(s)">
            <Icon name="upload" :size="13" /> 更新声纹
          </button>
          <button class="btn sm ghost" @click="openMerge(s)">合并到…</button>
          <span class="spacer" />
          <button class="btn sm ghost danger" :title="`删除 ${s.name}`" @click="remove(s)">
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
          <label>声纹来源</label>
          <div class="row" style="gap: 6px">
            <button
              class="btn sm"
              :class="createMode === 'library' ? 'primary' : ''"
              @click="createMode = 'library'"
            >
              从录音库选一段
            </button>
            <button
              class="btn sm"
              :class="createMode === 'upload' ? 'primary' : ''"
              @click="createMode = 'upload'"
            >
              上传本机音频
            </button>
            <button
              class="btn sm"
              :class="createMode === 'none' ? 'primary' : ''"
              @click="createMode = 'none'"
            >
              先只建名字
            </button>
          </div>
        </div>

        <template v-if="createMode === 'library'">
          <div class="field">
            <label>录音</label>
            <select v-model="createForm.fileId" class="select">
              <option value="">选择一条已转写的录音…</option>
              <option v-for="f in libraryFiles" :key="f.id" :value="f.id">
                {{ f.name }}（{{ fmtDuration(f.duration) }}）
              </option>
            </select>
            <span class="hint" v-if="!libraryFiles.length">还没有转写好的录音，先转写一条再来。</span>
          </div>
          <div class="row" style="gap: 10px">
            <div class="field" style="flex: 1">
              <label>起始（秒）</label>
              <input v-model.number="createForm.start" class="input" type="number" min="0" step="1" />
            </div>
            <div class="field" style="flex: 1">
              <label>结束（秒，0 = 到结尾）</label>
              <input v-model.number="createForm.end" class="input" type="number" min="0" step="1" />
            </div>
          </div>
          <span class="hint">挑一段（5 秒以上）只有这个人说话、没有杂音的部分，识别更准。</span>
        </template>

        <div class="field" v-else-if="createMode === 'upload'">
          <label>音频文件</label>
          <input ref="fileInput" type="file" accept="audio/*" class="input" style="padding-top: 6px" />
          <span class="hint">m4a / mp3 / wav 都行，会在本机转码后提取声纹。</span>
        </div>
        <p class="small muted" v-else style="margin: 0">
          只登记名字，稍后在转写详情页点说话人旁边的 <span class="kbd">+</span>，或回到这里点「更新声纹」补上。
        </p>
      </div>
      <template #footer>
        <button class="btn" @click="creating = false">取消</button>
        <button class="btn primary" :disabled="!form.name.trim() || saving || !canCreate" @click="create">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </template>
    </Modal>

    <!-- 更新声纹 -->
    <Modal :open="Boolean(enrolling)" title="更新声纹" @close="enrolling = null">
      <div class="stack" v-if="enrolling">
        <p class="small muted" style="margin: 0">
          给 <b>{{ enrolling.name }}</b> 重新提取声纹。建议选一段（5 秒以上）只有他自己说话、没有杂音的部分。
        </p>
        <div class="row" style="gap: 6px">
          <button
            class="btn sm"
            :class="enrollMode === 'library' ? 'primary' : ''"
            @click="enrollMode = 'library'"
          >
            从录音库选
          </button>
          <button
            class="btn sm"
            :class="enrollMode === 'upload' ? 'primary' : ''"
            @click="enrollMode = 'upload'"
          >
            上传本机音频
          </button>
        </div>

        <template v-if="enrollMode === 'library'">
          <div class="field">
            <label>录音</label>
            <select v-model="enrollForm.fileId" class="select">
              <option value="">选择一条已转写的录音…</option>
              <option v-for="f in libraryFiles" :key="f.id" :value="f.id">
                {{ f.name }}（{{ fmtDuration(f.duration) }}）
              </option>
            </select>
            <span class="hint" v-if="!libraryFiles.length">还没有转写好的录音，先转写一条再来。</span>
          </div>
          <div class="row" style="gap: 10px">
            <div class="field" style="flex: 1">
              <label>起始（秒）</label>
              <input v-model.number="enrollForm.start" class="input" type="number" min="0" step="1" />
            </div>
            <div class="field" style="flex: 1">
              <label>结束（秒，0 = 到结尾）</label>
              <input v-model.number="enrollForm.end" class="input" type="number" min="0" step="1" />
            </div>
          </div>
        </template>

        <div class="field" v-else>
          <label>音频文件</label>
          <input ref="enrollFileInput" type="file" accept="audio/*" class="input" style="padding-top: 6px" />
          <span class="hint">m4a / mp3 / wav 都行，会在本机转码后提取声纹。</span>
        </div>
      </div>
      <template #footer>
        <button class="btn" @click="enrolling = null">取消</button>
        <button class="btn primary" :disabled="enrollingBusy || !canEnroll" @click="doEnroll">
          {{ enrollingBusy ? '提取中…' : '保存声纹' }}
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
import { fmtDuration, refreshSpeakers, store, toast } from '../store'

const speakers = computed(() => store.speakers)
const creating = ref(false)
const saving = ref(false)
const form = ref({ name: '' })
const fileInput = ref(null)
const createMode = ref('library')
const createForm = ref({ fileId: '', start: 0, end: 0 })
const mergeSource = ref(null)
const mergeTarget = ref(null)

// 更新声纹
const enrolling = ref(null)
const enrollingBusy = ref(false)
const enrollMode = ref('library')
const enrollFileInput = ref(null)
const libraryFiles = ref([])
const enrollForm = ref({ fileId: '', start: 0, end: 0 })

const canEnroll = computed(() =>
  enrollMode.value === 'library' ? Boolean(enrollForm.value.fileId) : true,
)

onMounted(refreshSpeakers)

const canCreate = computed(() => {
  if (createMode.value === 'library') return Boolean(createForm.value.fileId)
  return true
})

async function openCreate() {
  form.value = { name: '' }
  createMode.value = 'library'
  createForm.value = { fileId: '', start: 0, end: 0 }
  if (fileInput.value) fileInput.value.value = ''
  creating.value = true
  await loadLibrary()
}

async function loadLibrary() {
  try {
    const res = await api.listFiles({ status: 'done', sort: 'name', limit: 200 })
    libraryFiles.value = res.items || []
  } catch {
    libraryFiles.value = []
  }
}

async function create() {
  saving.value = true
  const name = form.value.name.trim()
  try {
    if (createMode.value === 'library') {
      const { fileId, start, end } = createForm.value
      await api.createSpeakerFromFile({
        fileId,
        startMs: Math.max(0, Math.round((Number(start) || 0) * 1000)),
        endMs: Math.max(0, Math.round((Number(end) || 0) * 1000)),
        name,
      })
    } else if (createMode.value === 'upload') {
      const file = fileInput.value?.files?.[0]
      if (!file) throw new Error('先选一个音频文件')
      await api.enrollUpload(file, { name })
    } else {
      await api.createSpeaker(name)
    }
    await refreshSpeakers()
    creating.value = false
    toast(`已新增：${name}`, 'ok')
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

async function openEnroll(s) {
  enrolling.value = s
  enrollMode.value = 'library'
  enrollForm.value = { fileId: '', start: 0, end: 0 }
  if (enrollFileInput.value) enrollFileInput.value.value = ''
  await loadLibrary()
}

async function doEnroll() {
  if (!enrolling.value) return
  enrollingBusy.value = true
  try {
    if (enrollMode.value === 'library') {
      const { fileId, start, end } = enrollForm.value
      await api.enrollSpeakerFromFile(enrolling.value.id, {
        fileId,
        startMs: Math.max(0, Math.round((Number(start) || 0) * 1000)),
        endMs: Math.max(0, Math.round((Number(end) || 0) * 1000)),
      })
    } else {
      const file = enrollFileInput.value?.files?.[0]
      if (!file) throw new Error('先选一个音频文件')
      await api.enrollUpload(file, { speakerId: enrolling.value.id })
    }
    await refreshSpeakers()
    toast(`已更新 ${enrolling.value.name} 的声纹`, 'ok')
    enrolling.value = null
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    enrollingBusy.value = false
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
