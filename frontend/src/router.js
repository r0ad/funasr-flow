import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'files', component: () => import('./views/FilesView.vue'), meta: { title: '录音库' } },
  {
    path: '/files/:id',
    name: 'detail',
    component: () => import('./views/DetailView.vue'),
    meta: { title: '转写详情' },
  },
  { path: '/search', name: 'search', component: () => import('./views/SearchView.vue'), meta: { title: '全文搜索' } },
  {
    path: '/speakers',
    name: 'speakers',
    component: () => import('./views/SpeakersView.vue'),
    meta: { title: '声纹库' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./views/SettingsView.vue'),
    meta: { title: '设置' },
  },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = to.meta?.title ? `${to.meta.title} · FunASR Flow` : 'FunASR Flow'
})
