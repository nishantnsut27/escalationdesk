const dashboardModule = () => import('./dashboard')
const workspaceModule = () => import('./workspace')
const connectionsModule = () => import('./connections')
const importsModule = () => import('./imports')
const scenariosModule = () => import('./scenarios')
const logsModule = () => import('./logs')
const incidentsModule = () => import('./incidents')

export const routeModules = {
  dashboard: dashboardModule,
  errors: dashboardModule,
  collections: workspaceModule,
  investigations: workspaceModule,
  connections: connectionsModule,
  import: importsModule,
  demo: scenariosModule,
  logs: logsModule,
  incidents: incidentsModule,
} satisfies Record<string, () => Promise<unknown>>

const requested = new Set<string>()

export function preloadRoute(page: string) {
  const loader = routeModules[page as keyof typeof routeModules]
  if (!loader || requested.has(page)) return Promise.resolve()
  requested.add(page)
  return loader().then(() => undefined).catch(error => { requested.delete(page); throw error })
}

export function preloadSessionRoutes() {
  return Promise.all([preloadRoute('dashboard'), preloadRoute('collections')]).then(() => undefined)
}
