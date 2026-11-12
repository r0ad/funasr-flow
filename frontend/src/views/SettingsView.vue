<template>
  <div class="stack" style="gap: 16px; max-width: 1020px">
    <!-- 运行环境 -->
    <div class="card">
      <div class="card-head">
        <Icon name="db" :size="15" />
        运行环境
        <span class="spacer" />
        <button class="btn sm ghost" @click="reload">
          <Icon name="refresh" :size="13" /> 刷新
        </button>
      </div>
      <div class="card-pad grid grid-3">
        <div class="stat" style="border: none; box-shadow: none; padding: 0">
          <span class="k">计算设备</span>
          <span class="v" style="font-size: 15px">
            {{ health?.gpu?.available ? health.gpu.name : 'CPU（未检测到 GPU）' }}
          </span>
          <span class="d">
            <template v-if="health?.gpu?.available">{{ (health.gpu.vram_mb / 1024).toFixed(0) }} GB 显存 · 任务串行</template>
            <template v-else>{{ health?.gpu?.hint || 'GPU 推理需要 CUDA 版 torch' }}</template>
          </span>
        </div>
        <div class="stat" style="border: none; box-shadow: none; padding: 0">
          <span class="k">依赖</span>
          <span class="row wrap" style="gap: 5px; margin: 2px 0">
            <span class="badge" :class="health?.ffmpeg ? 'ok' : 'err'">ffmpeg</span>
            <span class="badge" :class="health?.funasr ? 'ok' : 'err'">funasr</span>
            <span class="badge" :class="health?.torch ? 'ok' : 'err'">torch</span>
            <span v-if="health?.container" class="badge info">Docker</span>
          </span>
          <span class="d truncate" :title="health?.cache_dir">模型缓存 {{ health?.cache_dir }}</span>
        </div>
        <div class="stat" style="border: none; box-shadow: none; padding: 0">
          <span class="k">后台队列</span>
          <span class="v" style="font-size: 15px">
            {{ health?.worker?.thread ? '运行中' : '未启动' }}
            <small v-if="health?.worker?.heartbeat_age != null">{{ health.worker.heartbeat_age }}s 前心跳</small>
          </span>
          <span class="d">已处理 {{ store.stats?.segments || 0 }} 句 · {{ store.stats?.speech_hours || 0 }} 小时语音</span>
        </div>
      </div>
    </div>

    <!-- 资料库 -->
    <div class="card">
      <div class="card-head">
        <Icon name="box" :size="15" />
        资料库
        <span class="spacer" />
        <span class="badge plain mono">{{ lib?.dir || '…' }}</span>
      </div>
      <div class="card-pad stack" style="gap: 14px">
        <div class="row wrap" style="gap: 10px">
          <span class="small muted">
            已入库 <b>{{ lib?.files ?? 0 }}</b> 个音频 · 占用
            <b>{{ lib ? fmtBytes(lib.bytes) : '—' }}</b>
          </span>
          <span class="spacer" />
          <button class="btn primary" @click="openImport">
            <Icon name="folder" :size="15" /> 导入录音
          </button>
          <button class="btn ghost" :disabled="importing" @click="rescanLibrary">
            <Icon name="refresh" :size="14" /> 重新扫描资料库
          </button>
        </div>

        <div v-if="importing" class="card" style="padding: 12px 14px; background: var(--panel-2)">
          <div class="row small">
            <span class="badge accent"><span class="bullet" />导入中</span>
            <span class="truncate" style="max-width: 46%" :title="imp?.current">{{ imp?.current || '准备中…' }}</span>
            <span class="spacer" />
            <span class="mono muted">{{ imp?.done || 0 }}/{{ imp?.total || 0 }}</span>
            <button class="btn sm ghost danger" @click="cancelImport">取消</button>
          </div>
          <div class="bar thin" style="margin-top: 8px">
            <span :style="{ width: `${imp?.total ? Math.round(((imp.done || 0) / imp.total) * 100) : 0}%` }" />
          </div>
          <div class="small muted" style="margin-top: 6px">
            新增 {{ imp?.added || 0 }} · 跳过 {{ imp?.skipped || 0 }} · 覆盖 {{ imp?.replaced || 0 }} ·
            失败 {{ imp?.failed || 0 }}
          </div>
        </div>

        <p class="hint" style="margin: 0">
          音频由浏览器读取你电脑上的文件后上传，<b>复制</b>进资料库（<code>data/library</code>，
          Docker 里是 <code>/data</code> 卷）。数据库只记资料库里的路径，所以重启容器、换机器、
          甚至把原始文件夹删掉，都不影响已有录音和文字稿。服务器不需要访问你的磁盘。
        </p>

      </div>
    </div>

    <!-- 识别设置 -->
    <div v-if="!settings" class="empty">加载配置…</div>
    <div v-else class="card">
      <div class="card-head">
        <Icon name="settings" :size="15" />
        识别设置
        <span class="spacer" />
        <span v-if="dirtyCount" class="badge warn">{{ dirtyCount }} 项待保存</span>
      </div>

      <div class="card-pad stack" style="gap: 18px">
        <div class="form-grid">
          <div class="field">
            <label>识别模型</label>
            <ModelPicker v-model="form.asr_model" :options="asrModels" placeholder="输入或选择识别模型 id" />
            <span v-if="asrChoice" class="hint">
              <b>{{ asrChoice.label }}</b> · {{ asrChoice.size }}<br />{{ asrChoice.note }}
            </span>
            <span v-else class="hint">自定义模型 id；ModelScope / HuggingFace 上的仓库名都可直接填。</span>
          </div>
          <div class="field">
            <label>模型来源</label>
            <select v-model="form.model_hub" class="select">
              <option value="ms">ModelScope（推荐，国内快）</option>
              <option value="hf">HuggingFace</option>
              <option value="auto">自动：先 ModelScope，失败再 HuggingFace</option>
            </select>
            <span class="hint">首次使用按来源在线下载到缓存目录，之后离线可用。</span>
            <span v-if="form.model_hub === 'hf'" class="hint">
              国内连不上 huggingface.co 时，启动服务前设环境变量
              <code>HF_ENDPOINT=https://hf-mirror.com</code>。
            </span>
          </div>
        </div>

        <div class="form-grid">
          <div class="field">
            <label>计算设备</label>
            <select v-model="form.device" class="select">
              <option value="cuda:0">GPU (cuda:0)</option>
              <option value="cpu">CPU（慢，约占 1/8 速度）</option>
            </select>
            <span class="hint">GPU 推理串行执行，这是刻意的：三模型一起吃显存。</span>
          </div>
          <div class="field">
            <label>声纹模型</label>
            <select v-model="form.spk_model" class="select">
              <option v-for="m in spkModels" :key="m.id" :value="m.id">{{ m.label }}</option>
              <option
                v-if="form.spk_model && !spkModels.some((m) => m.id === form.spk_model)"
                :value="form.spk_model"
              >
                {{ form.spk_model }}（自定义）
              </option>
            </select>
            <span class="hint">说话人分离与声纹库都用它（192 维）。</span>
          </div>
        </div>

        <div class="field">
          <label>热词（人名、地名、方言词）</label>
          <textarea v-model="form.hotwords" class="textarea" rows="2" placeholder="妈妈 爸爸 外婆 李嬢嬢" />
          <span class="hint">空格分隔。只对 SeACo 系模型（默认 paraformer-zh）生效，能明显减少人名误识别。</span>
        </div>

        <div class="form-grid">
          <div class="field">
            <label>说话人数</label>
            <input v-model.number="form.spk_num" type="number" min="0" max="10" class="input" />
            <span class="hint">0 = 自动估计；已知是两个人时填 2 可减少误分。</span>
          </div>
          <div class="field">
            <label>声纹匹配阈值</label>
            <input v-model.number="form.min_speaker_sim" type="number" step="0.01" min="0" max="1" class="input" />
            <span class="hint">与声纹库余弦相似度超过该值才自动套名字，默认 0.62。</span>
          </div>
          <div class="field">
            <label>批处理长度（秒）</label>
            <input v-model.number="form.batch_size_s" type="number" min="30" max="600" class="input" />
            <span class="hint">单批音频长度，越大显存占用越多；16G 显存 300 足够。</span>
          </div>
          <div class="field">
            <label>定时导入（分钟）</label>
            <input v-model.number="form.auto_scan_minutes" type="number" min="0" class="input" />
            <span class="hint">0 = 关闭。开启后会周期性把新录音复制进资料库，但不会自动转写。</span>
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
          <div class="form-grid" style="margin-top: 12px">
            <div class="field">
              <label>VAD</label>
              <input v-model="form.vad_model" class="input mono" />
            </div>
            <div class="field">
              <label>标点</label>
              <ModelPicker v-model="form.punc_model" :options="puncModels" placeholder="标点模型 id" />
            </div>
            <div class="field">
              <label>并发数</label>
              <input :value="1" class="input" disabled />
            </div>
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
          <p class="hint" style="margin: 10px 0 0">
            「合并阈值」越高越不容易把两个人并成一个；只有 ≤2 句且总时长不超过「碎片吸收时长」的簇，
            才会在够像时被吸收（专治「嗯」「啊」这类噪声簇）。多出来的说话人可以在详情页「合并到…」手动收拾。
          </p>
        </details>

        <div class="row">
          <button class="btn primary" :disabled="!dirtyCount || saving" @click="save">
            {{ saving ? '保存中…' : '保存设置' }}
          </button>
          <button class="btn ghost" :disabled="!dirtyCount" @click="reset">放弃修改</button>
          <span class="spacer" />
          <span class="small muted">设置存在 data/flow.db，重启后依然有效</span>
        </div>
      </div>
    </div>

    <!-- 队列维护 -->
    <div class="card">
      <div class="card-head">
        <Icon name="queue" :size="15" />
        队列与维护
      </div>
      <div class="card-pad row wrap" style="gap: 10px">
        <button class="btn" @click="cancelQueued">取消所有排队</button>
        <button class="btn danger" @click="stopAll">停止当前任务</button>
        <button class="btn ghost" @click="clearQueue">清理已结束记录</button>
        <span class="spacer" />
        <span class="small muted">
          排队 {{ live?.queue?.queued || 0 }} · 运行 {{ live?.queue?.running || 0 }}
        </span>
      </div>
    </div>

    <p class="small muted">
      所有数据都在本机：音频复制进资料库，转写结果存在 <code>data/flow.db</code>，模型缓存在 <code>.cache/</code>。
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import Icon from '../components/Icon.vue'
import ModelPicker from '../components/ModelPicker.vue'
import { api } from '../api'
import { fmtBytes, refreshHealth, refreshStats, store, toast } from '../store'

const settings = ref(null)
const form = reactive({})
const saving = ref(false)
const health = computed(() => store.health)
const lib = ref(null)
const importing = computed(() => !!store.live?.import?.running)
const imp = computed(() => store.live?.import || null)
const live = computed(() => store.live)

// 后端 /api/health 里带的模型清单（config.MODEL_CHOICES），每条带 label/note/size
const asrModels = computed(() => health.value?.ml?.models?.asr || [])
const spkModels = computed(() => health.value?.ml?.models?.spk || [])
const puncModels = computed(() => health.value?.ml?.models?.punc || [])
const asrChoice = computed(() => asrModels.value.find((m) => m.id === form.asr_model) || null)

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
    lib.value = await api.library()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function rescanLibrary() {
  try {
    const res = await api.scan()
    toast(`资料库已刷新：共 ${res.total} 个文件`, 'ok')
    lib.value = await api.library()
  } catch (e) {
    toast(e.message, 'err')
  }
}

async function cancelImport() {
  try {
    await api.cancelImport()
    toast('已请求取消导入', 'ok')
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

function openImport() {
  store.ui.importOpen = true
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


