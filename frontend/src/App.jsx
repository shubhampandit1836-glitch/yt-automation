import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  control,
  forceRun,
  getExperiments,
  getComments,
  getMemory,
  getMetrics,
  getPlaybook,
  getVideo,
  createSeries,
  getOps,
  getYoutubeAuthUrl,
  getYoutubeChannel,
  syncYoutubeChannel,
  getOverview,
  getRuns,
  getSeries,
  getVideos,
  subscribeToEvents,
} from './api'

const nav = [
  { id: 'overview', label: 'Home', icon: '◈' },
  { id: 'videos', label: 'My videos', icon: '▷' },
  { id: 'performance', label: 'Channel growth', icon: '⌁' },
  { id: 'series', label: 'Content series', icon: '◌' },
  { id: 'experiments', label: 'Learning', icon: '✦' },
  { id: 'comments', label: 'Viewer feedback', icon: '◍' },
  { id: 'memory', label: 'Channel memory', icon: '▤' },
  { id: 'ops', label: 'System health', icon: '⌘' },
]

const formatNumber = (value = 0) => new Intl.NumberFormat('en-IN', { notation: value > 9999 ? 'compact' : 'standard', maximumFractionDigits: 1 }).format(value)
const relativeTime = (value) => {
  if (!value) return '—'
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  return `${Math.round(seconds / 3600)}h ago`
}

function StatusPill({ value, tone }) {
  const normalized = String(value || 'unknown').toLowerCase().replaceAll('_', ' ')
  const className = tone || (['healthy', 'ok', 'succeeded', 'qa passed', 'full'].includes(normalized) ? 'good' : ['blocked', 'failed', 'degraded', 'paused'].includes(normalized) ? 'warn' : 'neutral')
  return <span className={`pill ${className}`}><i />{normalized}</span>
}

function MetricCard({ label, value, detail, accent = 'purple', icon }) {
  return <article className={`metric-card ${accent}`}>
    <div className="metric-top"><span>{label}</span><b>{icon}</b></div>
    <strong>{value}</strong>
    <small>{detail}</small>
  </article>
}

function SectionHeading({ eyebrow, title, action }) {
  return <div className="section-heading"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{action}</div>
}

function EmptyState({ title, detail }) {
  return <div className="empty-state"><span className="empty-orbit">✦</span><h3>{title}</h3><p>{detail}</p></div>
}

function Overview({ overview, runs, videos, events, onForceRun }) {
  const activeRuns = runs.filter((run) => ['queued', 'running'].includes(run.status))
  const providers = overview?.providers || []
  const healthyProviders = providers.filter((provider) => provider.status === 'healthy').length
  return <>
    <SectionHeading
      eyebrow="Home / your channel"
      title="Your channel is on autopilot."
      action={<button className="button primary" onClick={onForceRun}><span>＋</span> Force a brief</button>}
    />
    <div className="hero-banner">
      <div className="hero-copy"><span className="live-dot">● AUTOMATION STATUS</span><h1>Every day, your channel runs<br /><em>without you.</em></h1><p>Topics are found, videos are written, edited, checked and queued automatically. You only need to look here when something needs attention.</p></div>
      <div className="hero-orbit"><div className="orbit-ring ring-one" /><div className="orbit-ring ring-two" /><div className="orbit-core">◈</div><span className="orbit-label label-one">PLAN</span><span className="orbit-label label-two">MAKE</span><span className="orbit-label label-three">LEARN</span></div>
      <div className="hero-foot"><span>Next automatic upload</span><b>Tonight · 00:30 IST</b><span className="hero-separator" /><span>{overview?.automation?.mode === 'autopilot' ? 'Autopilot is on' : 'Preview mode'}</span><StatusPill value={overview?.automation?.mode === 'autopilot' ? 'autopilot on' : 'setup needed'} tone={overview?.automation?.mode === 'autopilot' ? 'good' : 'warn'} /></div>
    </div>
    <div className="metric-grid">
      <MetricCard label="Uploaded today" value={`${overview?.today?.published || 0} / 1`} detail="Your daily target" accent="purple" icon="↗" />
      <MetricCard label="Total views" value={formatNumber(overview?.views)} detail="From your saved channel data" accent="orange" icon="◒" />
      <MetricCard label="Services ready" value={`${healthyProviders} / ${providers.length || 0}`} detail={overview?.automation?.mode === 'autopilot' ? 'Everything needed is connected' : 'Preview mode is active'} accent="blue" icon="◫" />
      <MetricCard label="Work in progress" value={`${overview?.queue?.running || 0} running`} detail={`${overview?.queue?.queued || 0} waiting · ${overview?.queue?.failed || 0} failed`} accent="green" icon="✓" />
    </div>
    <div className="two-column">
      <section className="panel pipeline-panel"><SectionHeading eyebrow="Automatic work" title="What is happening now" action={<span className="muted-text">{activeRuns.length} active</span>} />
        {activeRuns.length === 0 ? <EmptyState title="Everything is caught up" detail="Your worker is ready for the next automatic task. You never need to keep this page open." /> : activeRuns.map((run) => <RunRow key={run.id} run={run} />)}
        <div className="panel-footer"><span className="status-line"><i className="green-dot" /> Your worker is listening</span><a href="#ops">See system health →</a></div>
      </section>
      <section className="panel slate-panel"><SectionHeading eyebrow="Your content" title="Today’s videos" action={<span className="muted-text">{videos.length} total</span>} />
        {videos.slice(0, 3).map((video) => <VideoMini key={video.id} video={video} />)}
        {videos.length === 0 && <EmptyState title="The board is clear" detail="Research and planning will populate it automatically." />}
      </section>
    </div>
    <div className="two-column lower-grid">
      <section className="panel activity-panel"><SectionHeading eyebrow="Recent activity" title="What your channel did" action={<span className="live-tag"><i /> streaming</span>} />
        <div className="event-list">{events.length ? events.slice(-6).reverse().map((event) => <div className="event-row" key={`${event.id}-${event.created_at}`}><span className="event-time">{relativeTime(event.created_at)}</span><span className="event-icon">{event.event_type?.includes('failed') ? '!' : '✦'}</span><div><b>{event.message}</b><small>{event.node || event.event_type}</small></div></div>) : <EmptyState title="Listening for signals" detail="Durable graph events will appear here in real time." />}</div>
      </section>
      <section className="panel checklist-panel"><SectionHeading eyebrow="Peace of mind" title="Automation health" />
        <CheckRow label="Content safety" detail="Every video is checked before upload" status="good" />
        <CheckRow label="Safe preview" detail="No video is uploaded yet" status="good" />
        <CheckRow label="Outside alert" detail={overview?.heartbeat?.status || 'not connected'} status="warn" />
        <CheckRow label="Owner access" detail="Only you can use controls" status="neutral" />
        <div className="notice"><span>i</span><p>{overview?.automation?.message || 'Connect the remaining services before switching from preview to real uploads.'}</p></div>
      </section>
    </div>
  </>
}

function RunRow({ run }) {
  const nodes = ['planner', 'research', 'creation', 'render', 'qa', 'publish']
  const current = nodes.indexOf(run.current_node)
  return <div className="run-row"><div className="run-meta"><span className="run-type">{run.graph}</span><StatusPill value={run.status} /><span className="muted-text">{relativeTime(run.created_at)}</span></div><div className="run-progress">{nodes.map((node, index) => <span key={node} className={index < current ? 'done' : index === current ? 'current' : ''} title={node} />)}</div><div className="run-current"><b>{run.current_node || 'queued'}</b><span>stage</span></div></div>
}

function VideoMini({ video }) {
  return <div className="video-mini"><div className={`format-art ${video.format}`}><span>{video.format === 'short' ? 'SHORT' : 'LONG'}</span><b>{video.content_type?.replace('_', ' ')}</b></div><div className="video-mini-copy"><b>{video.title || video.topic}</b><small>{video.topic}</small><StatusPill value={video.status} /></div><span className="chevron">›</span></div>
}

function CheckRow({ label, detail, status }) {
  return <div className="check-row"><span className={`check-icon ${status}`}>{status === 'good' ? '✓' : status === 'warn' ? '!' : '·'}</span><div><b>{label}</b><small>{detail}</small></div><span className="check-state">{status === 'good' ? 'ready' : status === 'warn' ? 'attention' : 'local'}</span></div>
}

function VideosPage({ videos, onOpenVideo, onExport }) {
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const visible = videos.filter((video) => {
    const matchesFilter = filter === 'all' || video.format === filter
    const haystack = `${video.title || ''} ${video.topic || ''}`.toLowerCase()
    return matchesFilter && haystack.includes(query.toLowerCase())
  })
  return <><SectionHeading eyebrow="Your videos" title="Everything your channel has made." action={<button className="button secondary" onClick={onExport}>Export report ↗</button>} /><section className="panel table-panel"><div className="table-toolbar"><div className="filter-tabs"><button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>All <b>{videos.length}</b></button><button className={filter === 'short' ? 'active' : ''} onClick={() => setFilter('short')}>Shorts</button><button className={filter === 'long' ? 'active' : ''} onClick={() => setFilter('long')}>Long-form</button></div><label className="table-search">⌕ <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search your videos" /></label></div>{visible.length ? <div className="video-table"><div className="table-head"><span>Content</span><span>Format</span><span>Stage</span><span>Created</span><span /></div>{visible.map((video) => <button className="table-row table-row-button" key={video.id} onClick={() => onOpenVideo(video.id)}><div className="title-cell"><div className={`format-art tiny ${video.format}`}><span>{video.format === 'short' ? 'S' : 'L'}</span></div><div><b>{video.title || 'Untitled brief'}</b><small>{video.topic}</small></div></div><span className="table-muted">{video.content_type?.replace('_', ' ')}</span><StatusPill value={video.status} /><span className="table-muted">{relativeTime(video.created_at)}</span><span className="row-arrow">↗</span></button>)}</div> : <EmptyState title="No videos match" detail="Try another search or choose All videos." />}</section></>
}

function VideoDetail({ videoId, onClose }) {
  const [video, setVideo] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { let active = true; getVideo(videoId).then((value) => active && setVideo(value)).catch(() => {}).finally(() => active && setLoading(false)); return () => { active = false } }, [videoId])
  return <div className="drawer-backdrop" onClick={onClose}><aside className="video-drawer" onClick={(event) => event.stopPropagation()}><div className="drawer-header"><div><span className="eyebrow">Video audit</span><h2>{loading ? 'Loading video…' : video?.title || video?.topic || 'Video details'}</h2></div><button className="icon-button" onClick={onClose}>×</button></div>{loading ? <EmptyState title="Opening the audit" detail="Loading the script, sources and safety checks." /> : video ? <div className="drawer-body"><div className="drawer-meta"><StatusPill value={video.status} /><span>{video.format} · {video.content_type}</span></div><section><span className="eyebrow">Script</span><p className="drawer-script">{video.script || 'No script saved.'}</p></section><section><span className="eyebrow">Fact sheet</span>{video.fact_sheet?.length ? video.fact_sheet.map((fact) => <div className="fact-card" key={fact.claim}><b>{fact.claim}</b><small>{fact.verified ? 'Verified' : 'Needs verification'} · {fact.sources?.length || 0} sources</small></div>) : <p className="drawer-muted">No facts recorded.</p>}</section><section><span className="eyebrow">Safety check</span><div className="qa-grid">{Object.entries(video.qa_report || {}).filter(([key]) => !['blocking_issues', 'publishable', 'mode'].includes(key)).map(([key, value]) => <div className="qa-card" key={key}><b>{key.replaceAll('_', ' ')}</b><StatusPill value={value?.passed ? 'passed' : 'review'} /></div>)}</div></section>{video.youtube_url && <a className="button primary drawer-link" href={video.youtube_url} target="_blank" rel="noreferrer">Open on YouTube ↗</a>}</div> : <EmptyState title="Video not found" detail="This record may have been removed." />}</aside></div>
}

function PerformancePage({ metrics = [] }) {
  const views = metrics.reduce((sum, item) => sum + (item.views || 0), 0)
  const likes = metrics.reduce((sum, item) => sum + (item.likes || 0), 0)
  return <><SectionHeading eyebrow="Channel growth" title="See what viewers enjoy." action={<span className="date-chip">Last 28 days · live data</span>} /><div className="metric-grid"><MetricCard label="Views recorded" value={formatNumber(views)} detail={metrics.length ? `${metrics.length} snapshots` : 'Waiting for YouTube sync'} accent="purple" icon="◒" /><MetricCard label="Likes recorded" value={formatNumber(likes)} detail="From channel sync" accent="orange" icon="♡" /><MetricCard label="Average view %" value={metrics.find((item) => item.average_view_percentage != null)?.average_view_percentage ? `${metrics.find((item) => item.average_view_percentage != null).average_view_percentage}%` : '—'} detail="Needs Analytics data" accent="blue" icon="⌁" /><MetricCard label="Experiments" value="1 active" detail="Safe exploration floor" accent="green" icon="✦" /></div><div className="performance-grid"><section className="panel chart-panel"><div className="chart-heading"><div><span className="eyebrow">Your channel</span><h3>Views over time</h3></div><div className="legend"><i /> synced data</div></div>{metrics.length ? <div className="snapshot-list">{metrics.slice(0, 12).map((item) => <div className="snapshot-row" key={item.id}><span>{relativeTime(item.captured_at)}</span><b>{formatNumber(item.views)} views</b><i style={{ width: `${Math.min(100, Math.max(3, (item.views || 0) / Math.max(1, views) * 100))}%` }} /></div>)}</div> : <EmptyState title="Waiting for your first sync" detail="Connect YouTube and the worker will collect real channel metrics automatically." />}</section><section className="panel score-panel"><span className="eyebrow">Learning system</span><h3>How the channel improves</h3><div className="score-ring"><strong>{metrics.length ? 'ON' : '—'}</strong><span>{metrics.length ? 'learning from\nreal data' : 'awaiting\nsample size'}</span></div><p>Rules are promoted only after repeated evidence. One video is never treated as proof.</p><div className="score-legend"><span><i className="purple-dot" /> Retention <b>35%</b></span><span><i className="orange-dot" /> Discovery <b>25%</b></span><span><i className="blue-dot" /> CTR <b>15%</b></span></div></section></div></>
}

function SeriesPage({ series, onCreate }) {
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState('')
  const [promise, setPromise] = useState('')
  const submit = async (event) => { event.preventDefault(); if (!name.trim() || !promise.trim()) return; await onCreate({ name, promise, format: 'short' }); setName(''); setPromise(''); setShowForm(false) }
  const states = ['pilot', 'active', 'paused', 'graduated']
  return <><SectionHeading eyebrow="Content planning" title="Keep your best ideas going." action={<button className="button secondary" onClick={() => setShowForm((value) => !value)}>＋ New series</button>} />{showForm && <form className="panel inline-form" onSubmit={submit}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Series name" /><input value={promise} onChange={(event) => setPromise(event.target.value)} placeholder="What will viewers get every episode?" /><button className="button primary">Save series</button></form>}<div className="series-board">{states.map((state) => <div className="series-column" key={state}><div className="column-heading"><span>{state}</span><b>{series.filter((item) => item.state === state).length}</b></div>{series.filter((item) => item.state === state).map((item) => <article className="series-card" key={item.id}><div className="series-card-top"><span className="series-symbol">◌</span><StatusPill value={item.state} /></div><h3>{item.name}</h3><p>{item.bible?.promise || 'Series bible is ready for its first episode.'}</p><div className="series-foot"><span>Episodes <b>{item.bible?.episode_count || 0}</b></span><span>›</span></div></article>)}</div>)}</div>{series.length === 0 && <section className="panel"><EmptyState title="No series memory" detail="Create a series or let the planner propose one." /></section>}</>
}

function ExperimentsPage({ experiments, playbook = [] }) {
  return <><SectionHeading eyebrow="Automatic learning" title="Your channel gets smarter over time." /><div className="experiment-list">{experiments.length ? experiments.map((experiment) => <section className="panel experiment-card" key={experiment.id}><div className="experiment-top"><div><span className="eyebrow">Running experiment</span><h3>{experiment.name}</h3></div><StatusPill value={experiment.status} tone="good" /></div><p>{experiment.hypothesis}</p><div className="arms">{experiment.arms?.map((arm) => <div className="arm" key={arm.name}><div><b>{arm.name}</b><span>{arm.samples || 0} samples</span></div><div className="arm-track"><i style={{ width: `${(arm.traffic_share || 0) * 100}%` }} /></div><strong>{Math.round((arm.traffic_share || 0) * 100)}%</strong></div>)}</div><div className="experiment-note">Exploration guardrail active · no promotion before repeated confirmation.</div></section>) : <section className="panel"><EmptyState title="No experiments yet" detail="The learning engine will propose one-variable tests after the first metric snapshots." /></section>}</div><section className="panel playbook-panel"><SectionHeading eyebrow="Versioned rules" title="What the channel has learned" />{playbook.length ? playbook.map((rule) => <div className="rule-row" key={rule.id}><div><b>{rule.rule_text}</b><small>{rule.component} · {rule.sample_size} samples · {Math.round(rule.confidence * 100)}% confidence</small></div><StatusPill value={rule.status} /></div>) : <EmptyState title="No rules promoted" detail="Candidates appear only after enough evidence." />}</section></>
}

function OpsPage({ ops }) {
  return <><SectionHeading eyebrow="System health" title="Everything important, in one place." action={<StatusPill value={ops?.kill_switch ? 'paused' : 'healthy'} />} /><div className="ops-grid"><section className="panel"><SectionHeading eyebrow="Provider router" title="Quota posture" />{ops?.providers?.providers?.map((provider) => <div className="provider-row" key={provider.provider}><div className="provider-logo">{provider.provider === 'youtube' ? '▶' : '✦'}</div><div><b>{provider.provider}</b><small>{provider.role}</small></div><StatusPill value={provider.status} /><div className="quota"><div><span>{provider.quota_remaining ?? '—'} <small>/ {provider.quota_limit ?? '∞'} units</small></span><b>{provider.quota_limit ? `${Math.round((provider.quota_remaining / provider.quota_limit) * 100)}%` : 'n/a'}</b></div><div className="quota-track"><i style={{ width: `${provider.quota_limit ? (provider.quota_remaining / provider.quota_limit) * 100 : 8}%` }} /></div></div></div>)}</section><section className="panel"><SectionHeading eyebrow="Job queue" title="Throughput" /><div className="queue-big"><strong>{ops?.queue?.running || 0}</strong><span>running now</span></div><div className="queue-stats"><span><b>{ops?.queue?.queued || 0}</b> queued</span><span><b>{ops?.queue?.failed || 0}</b> failed</span></div><div className="notice"><span>i</span><p>Workers claim jobs with row locking. A crashed job retries with backoff and keeps its graph checkpoint.</p></div></section></div><section className="panel audit-panel"><SectionHeading eyebrow="Audit trail" title="Control actions" />{ops?.audit?.length ? ops.audit.slice(0, 8).map((item) => <div className="audit-row" key={item.id}><span>{relativeTime(item.created_at)}</span><b>{item.action}</b><small>{item.actor} · {item.target || 'system'}</small></div>) : <EmptyState title="No control actions" detail="Emergency actions will be immutable and visible here." />}</section></>
}

function MemoryPage({ memoryQuery, setMemoryQuery, memory, searchMemory }) {
  return <><SectionHeading eyebrow="Channel memory" title="Your channel remembers what works." action={<div className="search-box"><span>⌕</span><input value={memoryQuery} onChange={(event) => setMemoryQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && searchMemory()} placeholder="Search memory" /><button onClick={searchMemory}>Search</button></div>} /><div className="memory-grid"><section className="panel memory-stats"><span className="eyebrow">Memory layers</span><div className="memory-number">3</div><p>Working state, structured facts, and semantic recall are kept separate so a bad hypothesis cannot rewrite compliance.</p><div className="memory-layer"><i className="purple-dot" /><span>Working memory</span><b>per run</b></div><div className="memory-layer"><i className="orange-dot" /><span>Structured memory</span><b>permanent</b></div><div className="memory-layer"><i className="blue-dot" /><span>Playbook</span><b>versioned</b></div></section><section className="panel memory-results"><SectionHeading eyebrow="Recall results" title={memory?.query ? `Matches for “${memory.query}”` : 'Pinned knowledge'} />{memory?.items?.length ? memory.items.map((item) => <article className="memory-item" key={`${item.kind}-${item.title}`}><span className="memory-kind">{item.kind}</span><div><h3>{item.title}</h3><p>{item.snippet || item.content}</p></div><span>↗</span></article>) : <EmptyState title="No matching memory" detail="Try a broader query." />}</section></div></>
}

function CommentsPage({ commentsData }) {
  const items = commentsData?.items || []
  return <><SectionHeading eyebrow="Viewer feedback" title="Know what people want next." /><div className="metric-grid"><MetricCard label="Video requests" value={commentsData?.insights?.requests || 0} detail="Fed back into planning" accent="purple" icon="＋" /><MetricCard label="Corrections" value={commentsData?.insights?.corrections || 0} detail="Re-checked against sources" accent="orange" icon="✓" /><MetricCard label="Spam held" value={commentsData?.insights?.spam || 0} detail="Never enters memory" accent="blue" icon="⌫" /><MetricCard label="Replies sent" value={commentsData?.insights?.replies || 0} detail="Rate limited and on persona" accent="green" icon="↗" /></div><section className="panel comments-empty">{items.length ? <div className="comment-list">{items.slice(0, 20).map((item) => <article className="comment-item" key={item.youtube_comment_id}><div><b>{item.author || 'Viewer'}</b><p>{item.text}</p></div><StatusPill value={item.label} /></article>)}</div> : <><div className="comment-orbit">◍</div><h2>Comment intelligence is ready.</h2><p>Once your channel is connected, only high-confidence genuine feedback enters memory. Spam, toxic bait, and uncertain classifications stay out of the learning loop.</p><div className="comment-rules"><span>✓ topic requests weighted by likes</span><span>✓ corrections re-verified against sources</span><span>✓ criticism is never deleted</span></div></>}</section></>
}

function ControlsPage({ overview, onControl, onForceRun, onConnect, onSync }) {
  const connected = overview?.automation?.mode === 'autopilot'
  return <><SectionHeading eyebrow="Autopilot setup" title="Connect once, then let it run." /><section className="panel youtube-connect-panel"><div className="connect-mark">▶</div><div className="connect-copy"><span className="eyebrow">YouTube channel</span><h3>{connected ? 'Your channel is connected' : 'Connect your real channel'}</h3><p>{connected ? 'The scheduler can now research, create, upload and sync channel metrics.' : 'Authorize your channel once. Orbit will never need this dashboard open to keep working.'}</p></div><StatusPill value={connected ? 'connected' : 'not connected'} tone={connected ? 'good' : 'warn'} /><div className="connect-actions"><button className="button primary" onClick={onConnect}>{connected ? 'Reconnect YouTube' : 'Connect YouTube'}</button><button className="button secondary" onClick={onSync}>Sync now</button></div></section><SectionHeading eyebrow="Emergency controls" title="You are always in control." /><div className="controls-grid"><section className="panel control-card"><span className="control-icon purple-bg">Ⅱ</span><h3>Pause automatic uploads</h3><p>Keep the channel thinking and planning, but stop new videos from being uploaded.</p><button className="button secondary" onClick={() => onControl(overview?.publishing_paused ? 'resume' : 'pause')}>{overview?.publishing_paused ? 'Resume publishing' : 'Pause publishing'}</button><small>Current: {overview?.publishing_paused ? 'paused' : 'running'}</small></section><section className="panel control-card danger-card"><span className="control-icon red-bg">■</span><h3>Stop everything</h3><p>Stop new automatic work immediately. Use this only for an emergency.</p><button className="button danger" onClick={() => onControl(overview?.kill_switch ? 'clear-kill-switch' : 'kill-switch')}>{overview?.kill_switch ? 'Clear kill switch' : 'Enable kill switch'}</button><small>Current: {overview?.kill_switch ? 'active' : 'off'}</small></section><section className="panel control-card"><span className="control-icon blue-bg">✦</span><h3>Make one video now</h3><p>Ask the channel to create one Short. It will still pass every safety check.</p><button className="button primary" onClick={onForceRun}>Create a Short</button><small>Safe default: dry-run</small></section></div><div className="control-warning"><b>Every action is recorded.</b><span>Safety rules, budgets and upload protections cannot be changed by the learning system.</span></div></>
}

function App() {
  const [page, setPage] = useState('overview')
  const [overview, setOverview] = useState(null)
  const [channel, setChannel] = useState(null)
  const [videos, setVideos] = useState([])
  const [runs, setRuns] = useState([])
  const [ops, setOps] = useState(null)
  const [series, setSeries] = useState([])
  const [experiments, setExperiments] = useState([])
  const [playbook, setPlaybook] = useState([])
  const [metrics, setMetrics] = useState([])
  const [selectedVideo, setSelectedVideo] = useState(null)
  const [commentsData, setCommentsData] = useState({ items: [], insights: {} })
  const [memory, setMemory] = useState({ items: [] })
  const [memoryQuery, setMemoryQuery] = useState('')
  const [events, setEvents] = useState([])
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const [nextOverview, nextVideos, nextRuns, nextOps, nextSeries, nextExperiments, nextPlaybook, nextMetrics, nextComments, nextMemory, nextChannel] = await Promise.all([getOverview(), getVideos(), getRuns(), getOps(), getSeries(), getExperiments(), getPlaybook(), getMetrics(), getComments(), getMemory(), getYoutubeChannel().catch(() => null)])
      setOverview(nextOverview); setChannel(nextChannel); setVideos(nextVideos); setRuns(nextRuns); setOps(nextOps); setSeries(nextSeries); setExperiments(nextExperiments); setPlaybook(nextPlaybook.rules || []); setMetrics(nextMetrics); setCommentsData(nextComments); setMemory(nextMemory); setError('')
    } catch (loadError) { setError(loadError.message) } finally { setRefreshing(false) }
  }, [])

  useEffect(() => { load(); const timer = setInterval(load, 30000); return () => clearInterval(timer) }, [load])
  useEffect(() => subscribeToEvents((event) => { setEvents((current) => [...current.slice(-29), event]); load() }, () => {}), [load])

  const handleForceRun = async () => { try { await forceRun({ format: 'short', content_type: 'facts', reason: 'dashboard queue action' }); await load(); setPage('overview') } catch (actionError) { setError(actionError.message) } }
  const handleConnect = async () => {
    try {
      const response = await getYoutubeAuthUrl()
      window.open(response.authorization_url, '_blank', 'noopener,noreferrer')
    } catch (actionError) { setError(actionError.message) }
  }
  const handleSync = async () => { try { await syncYoutubeChannel(); await load() } catch (actionError) { setError(actionError.message) } }
  const handleCreateSeries = async (payload) => { try { await createSeries(payload); await load() } catch (actionError) { setError(actionError.message) } }
  const handleExport = () => {
    const rows = [['Title', 'Topic', 'Format', 'Status', 'Created'], ...videos.map((video) => [video.title || '', video.topic || '', video.format || '', video.status || '', video.created_at || ''])]
    const csv = rows.map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); const link = document.createElement('a'); link.href = url; link.download = 'orbit-videos.csv'; link.click(); URL.revokeObjectURL(url)
  }
  const handleControl = async (name) => { if (!window.confirm(name === 'kill-switch' ? 'Stop all automatic work now?' : `${name === 'pause' ? 'Pause' : 'Resume'} automatic uploads?`)) return; try { await control(name, 'dashboard emergency control'); await load() } catch (actionError) { setError(actionError.message) } }
  const searchMemory = async () => { try { setMemory(await getMemory(memoryQuery)) } catch (actionError) { setError(actionError.message) } }

  const content = useMemo(() => {
    if (page === 'overview') return <Overview overview={overview} runs={runs} videos={videos} events={events} onForceRun={handleForceRun} />
    if (page === 'videos') return <VideosPage videos={videos} onOpenVideo={setSelectedVideo} onExport={handleExport} />
    if (page === 'performance') return <PerformancePage metrics={metrics} />
    if (page === 'series') return <SeriesPage series={series} onCreate={handleCreateSeries} />
    if (page === 'experiments') return <ExperimentsPage experiments={experiments} playbook={playbook} />
    if (page === 'comments') return <CommentsPage commentsData={commentsData} />
    if (page === 'memory') return <MemoryPage memoryQuery={memoryQuery} setMemoryQuery={setMemoryQuery} memory={memory} searchMemory={searchMemory} />
    if (page === 'ops') return <OpsPage ops={ops} />
    return <ControlsPage overview={overview} onControl={handleControl} onForceRun={handleForceRun} onConnect={handleConnect} onSync={handleSync} />
  }, [page, overview, runs, videos, events, series, experiments, playbook, metrics, commentsData, memory, memoryQuery, ops])

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-mark">◈</div><div><b>orbit</b><span>YT AUTOMATION</span></div></div><div className="workspace-switch"><span className="workspace-avatar">{(channel?.snippet?.title || 'A').slice(0, 1).toUpperCase()}</span><div><b>{channel?.snippet?.title || 'Preview workspace'}</b><small>{channel ? 'YouTube channel' : 'Connect your channel'}</small></div><span className="workspace-chevron">⌄</span></div><nav>{nav.map((item) => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => setPage(item.id)}><span className="nav-icon">{item.icon}</span>{item.label}{item.id === 'ops' && <i className="nav-alert" />}</button>)}</nav><div className="sidebar-bottom"><button className={page === 'controls' ? 'active' : ''} onClick={() => setPage('controls')}><span className="nav-icon">⚙</span>Autopilot</button><div className="connection"><i /><span><b>Backend connected</b><small>{refreshing ? 'Syncing now…' : 'Synced just now'}</small></span></div><div className="sidebar-version">ORBIT 0.1.0 <span>·</span> {overview?.automation?.mode === 'autopilot' ? 'AUTOPILOT' : 'PREVIEW'}</div></div></aside>
    <main className="main-content"><header className="topbar"><div className="mobile-brand"><span>◈</span> orbit</div><div className="breadcrumbs"><span>Workspace</span><b>›</b><strong>{nav.find((item) => item.id === page)?.label || 'Controls'}</strong></div><div className="top-actions"><span className="top-status"><i /> {overview?.automation?.mode === 'autopilot' ? 'autopilot is on' : 'preview mode'}</span><button className="icon-button" onClick={load} aria-label="Refresh">↻</button><button className="avatar-button" onClick={() => setPage('controls')}>A</button></div></header><div className="page-content">{error && <div className="error-banner"><b>Connection issue</b><span>{error}</span><button onClick={() => setError('')}>×</button></div>}{content}</div></main>{selectedVideo && <VideoDetail videoId={selectedVideo} onClose={() => setSelectedVideo(null)} />}
  </div>
}

export default App
