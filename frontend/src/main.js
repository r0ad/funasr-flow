import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import { startLive } from './store'
import './style.css'

createApp(App).use(router).mount('#app')

// 全局 SSE：任何页面都能看到队列进度
startLive()
