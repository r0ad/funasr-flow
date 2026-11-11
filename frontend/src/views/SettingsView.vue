<template>
  <div class="stack" style="gap: 16px; max-width: 980px">
    <!-- 环境状态 -->
    <div class="card">
      <div class="card-head">
        <Icon name="db" :size="15" />
        <span style="font-weight: 600; font-size: 13px">运行环境</span>
        <span class="spacer" />
        <button class="btn sm ghost" @click="reload">
          <Icon name="refresh" :size="13" /> 刷新
        </button>
      </div>
      <div class="card-pad grid grid-3">
        <div class="stat">
          <span class="stat-label">GPU</span>
          <span class="stat-value">
            {{ health?.gpu?.available ? health.gpu.name : '未检测到（将用 CPU）' }}
          </span>
          <span v-if="health?.gpu?.vram_mb" class="small muted">{{ (health.gpu.vram_mb / 1024).toFixed(0) }} GB 显存</span>
        </div>
        <div class="stat">
          <span class="stat-label">依赖</span>
          <span class="row wrap" style="gap: 4px">
            <span class="badge" :class="health?.ffmpeg ? 'ok' : 'err'">ffmpeg</span>
            <span class="badge" :class="health?.funasr ? 'ok' : 'err'">funasr</span>
            <span class="badge" :class="health?.torch ? 'ok' : 'err'">torch</span>
          </span>
          <span class="small muted">缓存目录 {{ health?.cache_dir }}</span>
        </div>
        <div class="stat">
          <span class="stat-label">后台队列</span>
          <span class="stat-value">
            {{ health?.worker?.thread ? '运行中' : '未启动' }}
            <span v-if="health?.worker?.heartbeat_age != null" class="small muted">
              · {{ health.worker.heartbeat_age }}s 前心跳
            </span>
          </span>
          <span class="small muted">
            已处理 {{ store.stats?.segments || 0 }} 句 · {{ store.stats?.speech_hours || 0 }} 小时语音
          </span>
        </div>
      </div>
    </div>

    <!-- 设置表单 -->
    <div v-if="!settings" class="empty">加载配置…</div>
    <div v-else class="card">
      <div class="card-head">
        <Icon name="settings" :size="15" />
        <span style="font-weight: 600; font-size: 13px">识别设置</span>
        <span class="spacer" />
        <span v-if="dirtyCount" class="badge warn">{{ dirtyCount }} 项待保存</span>
      </div>

      <div class="card-pad stack" style="gap: 18px">
        <div class="field">
          <label>录音目录</label>
          <div class="row">
            <input v-model="form.input_dir" class="input mono" style="flex: 1" />
            <button class="btn" @click="browserOpen = true">
              <Icon name="folder" :size="15" /> 浏览…
            </button>
            <button class="btn primary" @click="changeDir">应用并扫描</button>
          </div>
          <span class="hint">手机通话录音所在文件夹，支持 .m4a .mp3 .wav .amr .aac 等。扫描只登记文件，不会复制或上传。</span>
        </div>

        <div class="field-row">
          <div class="field">
            <label>识别模型</label>
            <input v-model="form.asr_model" class="input" list="asr-models" />
            <datalist id="asr-models">
              <option v-for="m in health?.ml?.models?.asr || []" :key="m.id" :value="m.id">{{ m.label }}</option>
            </datalist>
            <span class="hint">默认 Paraformer 中文；方言重的录音可换四川话微调模型。改完对之后的任务生效。</span>
          </div>
          <div class="field">
            <label>计算设备</label>
            <select v-model="form.device" class="select">
              <option value="cuda:0">GPU (cuda:0)</option>
              <option value="cpu">CPU（慢，约占 1/8 速度）</option>
            </select>
            <span class="hint">GPU 推理时任务串行执行，这是正常的。</span>
          </div>
        </div>

        <div class="field">
          <label>热词（人名、地名、方言词）</label>
          <textarea v-model="form.hotwords" class="textarea" rows="2" placeholder="妈妈 爸爸 外婆 李嬢嬢" />
          <span class="hint">用空格分隔。只对 SeACo 系列模型（默认 paraformer-zh）生效，能明显减少人名误识别。</span>
        </div>

        <div class="field-row">
          <div class="field">
            <label>说话人数</label>
            <input v-model.number="form.spk_num" type="number" min="0" max="10" class="input" />
            <span class="hint">0 = 自动估计；已知是两个人时填 2 可减少误分。</span>
          </div>
          <div class="field">
            <label>声纹匹配阈值</label>
            <input v-model.number="form.min_speaker_sim" type="number" step="0.01" min="0" max="1" class="input" />
            <span class="hint">与声纹库余弦相似度超过该值才自动套用名字，默认 0.62。偏低容易认错人。</span>
          </div>
        </div>

        <div class="field-row">
          <div class="field">
            <label>批处理长度（秒）</label>
            <input v-model.number="form.batch_size_s" type="number" min="30" max="600" class="input" />
            <span class="hint">单批音频长度，越大显存占用越多。12G 显存 300 足够。</span>
          </div>
          <div class="field">
            <label>定时自动扫描（分钟）</label>
            <input v-model.number="form.auto_scan_minutes" type="number" min="0" class="input" />
            <span class="hint">0 = 关闭。开启后会周期性发现新录音，但不会自动转写。</span>
          </div>
        </div>

        <div class="row wrap" style="gap: 18px">
          <label class="check">
            <input v-model="form.auto_map_speakers" type="checkbox" />
            自动匹配声纹库
          </label>
          <label class="check">
            <input v-model="form.keep_wav" type="checkbox" />
            保留转码后的 wav
          </label>
          <span class="small muted">（默认删除中间文件，节省空间）</span>
        </div>

        <details>
          <summary class="small muted" style="cursor: pointer">高级：模型 ID 与聚类阈值</summary>
          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>VAD</label>
              <input v-model="form.vad_model" class="input mono" />
            </div>
            <div class="field">
              <label>标点</label>
              <input v-model="form.punc_model" class="input mono" />
            </div>
            <div class="field">
              <label>声纹</label>
              <input v-model="form.spk_model" class="input mono" />
            </div>
            <div class="field">
              <label>并发数</label>
              <input :value="1" class="input" disabled />
            </div>
          </div>
          <div class="field-row" style="margin-top: 10px">
            <div class="field">
              <label>合并阈值</label>
              <input v-model.number="form.merge_threshold" type="number" step="0.01" min="0.3" max="0.99" class="input" />
            </div>
            <div class="field">
              <label>碎片吸收阈值</label>
              <input v-model.number="form.absorb_threshold" type="number" step="0.05" min="0" max="0.9" class="input" />
            </div>
            <div class="field">
              <label>碎片吸收时长（秒）</label>
              <input v-model.number="form.absorb_max_seconds" type="number" step="0.5" min="0" max="60" class="input" />
            </div>
          </div>
          <p class="small muted" style="margin: 8px 0 0">
            「合并阈值」越高越不容易把两个人并成一个；只有 ≤2 句且总时长不超过「碎片吸收时长」的簇，
            才会在够像时被吸收（专治「嗯」「啊」这类噪声簇）。多出来的说话人可以在详情页「合并到…」手动收拾。
          </p>
        </details>

        <div class="row">
          <button class="btn primary" :disabled="!dirtyCount || saving" @click="save">
            {{ saving ? '保存中…' : '保存设置' }}
          </button>
          <button class="btn" :disabled="!dirtyCount" @click="reset">放弃修改</button>
          <span class="spacer" />
          <span class="small muted">设置存在 data/flow.db，重启后依然有效</span>
        </div>
      </div>
    </div>

    <!-- 队列工具 -->
    <div class="card">
      <div class="card-head">
        <Icon name="queue" :size="15" />
        <span style="font-weight: 600; font-size: 13px">队列与维护</span>
      </div>
      <div class="card-pad row wrap" style="gap: 10px">
        <button class="btn" @click="cancelQueued">取消所有排队</button>
        <button class="btn danger" @click="stopAll">停止当前任务</button>
        <button class="btn" @click="clearQueue">清理已结束记录</button>
        <span class="spacer" />
        <span class="small muted">队列状态：排队 {{ live?.queue?.queued || 0 }} · 运行 {{ live?.queue?.running || 0 }}</span>
      </div>
    </div>

    <p class="small muted">
      所有数据都在本机：录音不复制，转写结果存在 <code>data/flow.db</code>，模型缓存在 <code>.cache/</code>。
    </p>
  </div>

  <DirPicker
    :open="browserOpen"
    :start="form.input_dir"
    @close="browserOpen = false"
    @picked="onDirPicked"
  />
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import DirPicker from '../components/DirPicker.vue'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { refreshHealth, refreshStats, store, toast } from '../store'

const settings = ref(null)
const form = reactive({})
const saving = ref(false)
const health = computed(() => store.health)
const browserOpen = ref(false)
const live = computed(() => store.live)

const dirtyCount = computed(() => {
  if (!settings.value) return 0
  let n = 0
  for (const k of Object.keys(form)) {
    if (JSON.stringify(form[k]) !== JSON.stringify(settings.value[k])) n++
  }
  return n
})

function fill(data) {
  settings.value = data
  for (const k of Object.keys(data)) form[k] = data[k]
}

async function reload() {
  try {
    const data = await api.settings()
    fill(data.values)
    await refreshHealth()
    await refreshStats()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function save() {
  saving.value = true
  try {
    const values = {}
    for (const k of Object.keys(form)) {
      if (JSON.stringify(form[k]) !== JSON.stringify(settings.value[k])) values[k] = form[k]
    }
    const res = await api.saveSettings(values)
    fill(res.values)
    toast('设置已保存，对之后的任务生效', 'ok')
    refreshHealth()
  } catch (e) {
    toast(e.message, 'err')
  } finally {
    saving.value = false
  }
}

function reset() {
  for (const k of Object.keys(settings.value)) form[k] = settings.value[k]
}

function onDirPicked(path) {
  form.input_dir = path
  reload()
}

async function changeDir() {
  try {
    const res = await api.scan(form.input_dir)
    toast(`已切换并扫描：新增 ${res.added} 个，共 ${res.total} 个`, 'ok')
    await reload()
    store.reloadTick++
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function cancelQueued() {
  try {
    const res = await api.cancelQueue('queued')
    toast(`已取消 ${res.canceled} 个排队任务`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function stopAll() {
  if (!confirm('停止当前正在执行的任务？已完成的不会受影响。')) return
  try {
    const res = await api.cancelQueue('all')
    toast(`已请求停止（${res.interrupted} 个运行中）`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function clearQueue() {
  try {
    const res = await api.clearQueue()
    toast(`清理 ${res.deleted} 条任务记录`, 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

onMounted(reload)
</script>

<style scoped>
.stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.stat-label {
  font-size: 11.5px;
  color: var(--muted);
  font-weight: 600;
}
.stat-value {
  font-size: 13.5px;
  font-weight: 600;
  word-break: break-word;
}
</style>
