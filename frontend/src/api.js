const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json()
}

export const getHealth = () => request('/health')
export const getOverview = () => request('/overview')
export const getVideos = () => request('/videos?limit=50')
export const getVideo = (id) => request(`/videos/${id}`)
export const getMetrics = (videoId) => request(`/metrics${videoId ? `?video_id=${encodeURIComponent(videoId)}` : ''}`)
export const getPlaybook = () => request('/playbook')
export const createSeries = (payload) => request('/series', { method: 'POST', body: JSON.stringify(payload) })
export const getRuns = () => request('/runs?limit=30')
export const getOps = () => request('/ops')
export const getSeries = () => request('/series')
export const getExperiments = () => request('/experiments')
export const getComments = () => request('/comments?limit=100')
export const getMemory = (query = '') => request(`/memory/search?q=${encodeURIComponent(query)}`)
export const getYoutubeStatus = () => request('/auth/youtube/status')
export const getYoutubeChannel = () => request('/channel')
export const getYoutubeAuthUrl = () => request('/auth/youtube/start')
export const syncYoutubeChannel = () => request('/channel/sync', { method: 'POST', body: JSON.stringify({}) })

export const forceRun = (payload) => request('/controls/force-run', {
  method: 'POST',
  body: JSON.stringify(payload),
})

export const control = (name, reason) => request(`/controls/${name}`, {
  method: 'POST',
  body: JSON.stringify({ reason }),
})

export function subscribeToEvents(onEvent, onError) {
  const stream = new EventSource(`${API_BASE}/events/stream`)
  stream.onmessage = (message) => {
    try { onEvent(JSON.parse(message.data)) } catch { /* ignore malformed keepalive */ }
  }
  stream.onerror = onError
  return () => stream.close()
}
