/* eslint-disable react/prop-types */
import { useCallback, useEffect, useMemo, useState } from 'react'
import demoData from './data/demo_data.json'
import {
  generateAudio,
  generateCharacters,
  generateFinalVideo,
  generateKeyframe,
  generateVideo,
  getCase,
  getTask,
  hasSubscriptionCode,
  listCases,
  regenerateShots,
  rewriteScript,
  startPlanning,
  taskToDemoData,
  updateShot,
  verifySubscriptionCode,
} from './api'

const COLORS = {
  accent: '#f59e0b',
  accentLight: '#fbbf24',
  green: '#22c55e',
  red: '#ef4444',
  text: '#ede8e0',
  secondary: '#a89b8c',
  muted: '#5c5048',
}

const EMPTY_DATA = {
  ...demoData,
  task: { ...demoData.task, id: '', title: '', status: 'pending', progress: 0, created_at: '', finished_at: '' },
  story: { raw_synopsis: '', synopsis: '', characters: [] },
  storyboard: [{ sub_script: 'Sub-Script 1', sub_script_plot: '', scenes: [] }],
  logs: [],
  cost: { ...demoData.cost, input_tokens: 0, output_tokens: 0, total_tokens: 0, estimated_cost_usd: 0 },
  final_video: '',
}

const FINISHED_FILMS = [
  { title: '森林图书馆的借阅者', video: '/videos/10015e62-b575-4dec-ae27-fcceffb70349_final.mp4', meta: '22 镜头 · 720p · 已配音' },
  { title: '提灯人', video: '/videos/f6731663-21ed-45e8-9c30-7e743ad2fc7b_tidengman_final.mp4', meta: '8 镜头 · 720p · 已配音' },
  { title: '天台上的信号', video: '/videos/155d84f0-d595-4e3a-a48e-7cebaa50e579_final.mp4', meta: '16 镜头 · 720p' },
  { title: '修伞匠', video: '/videos/718d2afa-2016-4f71-bd94-37ed494634d0_final.mp4', meta: '28 镜头 · 720p' },
  { title: '松鼠奇奇', video: '/videos/bf7d7db7-b545-4df9-889f-91a38afd6a20_squirrel_final_subtitled.mp4', meta: '6 镜头 · 720p · 字幕版' },
]

const splitCharacters = (value) => value.split(/[,，、\n]/).map((item) => item.trim()).filter(Boolean)

function allScenes(data) {
  return data.storyboard.flatMap((item) => item.scenes || [])
}

function allShots(data) {
  return allScenes(data).flatMap((scene) => (scene.shots || []).map((shot) => ({ ...shot, sceneTitle: scene.title })))
}

function statusLabel(shot) {
  if (shot.videoStatus === 'done') return { text: '视频已完成', color: COLORS.green }
  if (shot.videoStatus && shot.videoStatus !== 'pending') return { text: '视频生成中', color: COLORS.accent }
  if (shot.kfStatus === 'done') return { text: '关键帧完成', color: COLORS.green }
  if (shot.kfStatus && shot.kfStatus !== 'pending') return { text: '关键帧生成中', color: COLORS.accent }
  return { text: '未开始', color: '#8e8177' }
}

function MobileHeader({ onGallery }) {
  return (
    <header className="mobile-header">
      <div className="flex items-center gap-2 px-4 py-3">
        <div className="text-lg font-bold flex-1">🎬 MovieAgent Demo</div>
        <button className="mobile-icon-button" onClick={onGallery} aria-label="成片展示" title="成片展示">🎞</button>
      </div>
    </header>
  )
}

function CaseBar({ activeCase, subscribed, onOpenCases, onOpenSubscription, onNew }) {
  return (
    <div className="case-bar">
      <button className="case-button" onClick={onOpenCases}>📂 案例展示</button>
      <div className="case-copy"><b>{activeCase ? `《${activeCase.title}》` : '完整生成案例'}</b><span>{activeCase ? '只读浏览 · 无需订阅码' : '查看分镜、关键帧与成片'}</span></div>
      {activeCase && <button className="case-icon-button" onClick={onNew} aria-label="新建项目" title="新建项目">＋</button>}
      <button className={`case-icon-button ${subscribed ? 'unlocked' : ''}`} onClick={onOpenSubscription} aria-label="输入订阅码" title={subscribed ? '完整功能已解锁' : '输入订阅码'}>{subscribed ? '✓' : '🔑'}</button>
    </div>
  )
}

function WorkflowTabs({ section, onChange }) {
  const tabs = [
    { key: 'input', icon: '📋', label: '项目输入' },
    { key: 'storyboard', icon: '▦', label: '分镜' },
    { key: 'editor', icon: '✎', label: 'Shot 编辑' },
  ]
  return (
    <div className="workflow-tabs" aria-label="工作流视图">
      {tabs.map((tab) => (
        <button key={tab.key} className={`workflow-tab ${section === tab.key ? 'active' : ''}`} onClick={() => onChange(tab.key)}>
          <span className="mr-1">{tab.icon}</span>{tab.label}
        </button>
      ))}
    </div>
  )
}

function InputScreen({ state, actions }) {
  const canEdit = state.phase === 'empty' || state.phase === 'rewritten'
  const hasCharacters = state.data.story.characters.length > 0
  return (
    <main className="mobile-content space-y-3">
      <section className="surface p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="m-0 text-base font-bold">项目输入</h2>
          {state.taskId && <span className="text-xs font-mono" style={{ color: COLORS.muted }}>{state.taskId.slice(0, 8)}</span>}
        </div>
        <label className="block text-xs" style={{ color: COLORS.secondary }}>
          原始输入 / Raw Input
          <textarea className="field mt-1" rows={7} value={state.scriptInput} disabled={!canEdit} onChange={(event) => actions.setScriptInput(event.target.value)} placeholder="在这里输入你的故事..." />
        </label>
        <button className="primary-button w-full" disabled={!state.scriptInput.trim() || state.action === 'rewrite'} onClick={actions.rewrite}>
          {state.action === 'rewrite' ? '正在改写…' : '✨ 改写剧本'}
        </button>
        <label className="block text-xs" style={{ color: COLORS.secondary }}>
          角色（用逗号分隔）
          <input className="field mt-1" value={state.charactersInput} disabled={!canEdit} onChange={(event) => actions.setCharactersInput(event.target.value)} placeholder="例如：小满，引路鹿，山谷老人" />
        </label>
      </section>

      <section className="surface p-4">
        <div className="text-xs mb-2" style={{ color: COLORS.secondary }}>改写后剧本 / Rewritten Synopsis</div>
        <div className="field min-h-24" style={{ color: state.rewritten ? '#d7cec5' : COLORS.muted }}>
          {state.rewritten || (state.action === 'rewrite' ? '后端正在改写剧本…' : '改写结果将在这里显示')}
        </div>
      </section>

      <section className="surface p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold">角色与参考图</span>
          {state.taskId && hasCharacters && <button className="secondary-button" disabled={Boolean(state.action)} onClick={actions.generateCharacters}>生成定妆照</button>}
        </div>
        {hasCharacters ? (
          <div className="flex gap-3 overflow-x-auto pb-1">
            {state.data.story.characters.map((character) => (
              <div key={character.name} className="flex-shrink-0 text-center">
                <div className="w-16 h-16 overflow-hidden rounded-md flex items-center justify-center font-bold" style={{ border: `1px solid ${character.color}88`, color: character.color, background: `${character.color}16` }}>
                  {character.image ? <img src={character.image} alt={character.name} className="w-full h-full object-cover" /> : character.name.slice(0, 1)}
                </div>
                <div className="text-xs mt-1" style={{ color: COLORS.secondary }}>{character.name}</div>
              </div>
            ))}
          </div>
        ) : <div className="text-sm" style={{ color: COLORS.muted }}>开始规划后显示识别到的角色与定妆图</div>}
      </section>

      <button className="primary-button w-full" disabled={!state.rewritten.trim() || splitCharacters(state.charactersInput).length === 0 || Boolean(state.action)} onClick={actions.startPlanning}>
        🎬 开始规划
      </button>
    </main>
  )
}

function PlaceholderCard({ index }) {
  const hints = ['规划后显示镜头的构图与动作描述', '每个场景会拆分为可独立生成的镜头', '关键帧生成后可继续生成对应视频']
  return (
    <div className="shot-card opacity-70">
      <div className="flex items-center justify-between mb-3"><b>Scene {index + 1} · Shot —</b><span className="shot-status" style={{ color: COLORS.muted }}>未开始</span></div>
      <div className="grid grid-cols-[112px_minmax(0,1fr)] gap-3">
        <div className="shot-thumb flex items-center justify-center text-xs" style={{ color: COLORS.muted }}>16:9<br />关键帧</div>
        <div className="text-sm leading-6" style={{ color: COLORS.secondary }}>{hints[index]}</div>
      </div>
      <div className="shot-card-actions">
        <button type="button" className="shot-card-action" disabled>✎ 编辑</button>
        <button type="button" className="shot-card-action generate" disabled>✦ 生成</button>
      </div>
    </div>
  )
}

function StoryboardScreen({ data, selectedShotId, action, actionShotId, canGenerate, onSelect, onEdit, onGenerate }) {
  const shots = allShots(data)
  return (
    <main className="mobile-content">
      <div className="flex items-center gap-2 py-2 mb-2">
        <h2 className="m-0 text-base font-bold flex-1">分镜列表（{shots.length || '—'}）</h2>
        <button className="mobile-icon-button" aria-label="筛选" title="筛选">▽</button>
        <button className="mobile-icon-button" aria-label="排序" title="排序">⇅</button>
      </div>
      <div className="space-y-3">
        {shots.length === 0 ? [0, 1, 2].map((index) => <PlaceholderCard key={index} index={index} />) : shots.map((shot) => {
          const status = statusLabel(shot)
          const active = shot.id === selectedShotId
          return (
            <article key={shot.id} className={`shot-card ${active ? 'active' : ''}`} onClick={() => onSelect(shot.id)}>
              <div className="flex items-center gap-2 mb-3">
                <h3 className="m-0 text-sm font-bold flex-1">{shot.scene} · {shot.shot}</h3>
                <span className="shot-status" style={{ color: status.color }}>{status.text}</span>
              </div>
              <div className="grid grid-cols-[42%_minmax(0,1fr)] gap-3">
                <div className="shot-thumb">
                  {shot.keyframe ? <img src={shot.keyframe} alt={`${shot.scene} ${shot.shot}`} /> : <div className="h-full flex items-center justify-center text-xs text-center" style={{ color: COLORS.muted }}>关键帧<br />16:9</div>}
                </div>
                <div className="min-w-0">
                  <p className="m-0 text-sm leading-6 line-clamp-3" style={{ color: COLORS.secondary }}>{shot.plot || '等待镜头描述'}</p>
                  <div className="flex flex-wrap gap-1 mt-2">{(shot.characters || []).slice(0, 3).map((character) => <span className="chip" key={character}>{character}</span>)}</div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 mt-3 pt-3 text-xs" style={{ borderTop: '1px solid #2d2018', color: COLORS.secondary }}>
                <span>▣ {shot.cameraMovement?.split(/[（(]/)[0] || '静态'}</span>
                <span>▦ {shot.shotType?.split(/[（(]/)[0] || '景别'}</span>
                <span className="text-right">◷ 5s</span>
              </div>
              <div className="shot-card-actions" onClick={(event) => event.stopPropagation()}>
                <button type="button" className="shot-card-action" onClick={() => onEdit(shot.id)} aria-label={`编辑 ${shot.scene} ${shot.shot}`}>✎ 编辑</button>
                <button type="button" className="shot-card-action generate" disabled={!canGenerate || Boolean(action)} onClick={() => onGenerate(shot.id)} aria-label={`生成 ${shot.scene} ${shot.shot}`}>
                  {action && actionShotId === shot.id ? '生成中…' : '✦ 生成'}
                </button>
              </div>
            </article>
          )
        })}
      </div>
    </main>
  )
}

function ShotEditorScreen({ shot, taskId, action, onSave, onGenerate, onRegenerate }) {
  const [draft, setDraft] = useState({ plot: '', dialogue: '', shotType: '', cameraMovement: '' })
  const [preview, setPreview] = useState('keyframe')
  useEffect(() => {
    setDraft({ plot: shot?.plot || '', dialogue: shot?.dialogue || '', shotType: shot?.shotType || '', cameraMovement: shot?.cameraMovement || '' })
  }, [shot?.id, shot?.plot, shot?.dialogue, shot?.shotType, shot?.cameraMovement])

  if (!shot) return <main className="mobile-content"><div className="surface p-8 text-center" style={{ color: COLORS.muted }}>请先在分镜列表中选择一个镜头</div></main>
  return (
    <main className="mobile-content space-y-3">
      <div className="flex items-center justify-between py-2"><h2 className="m-0 text-base font-bold">Shot 编辑</h2><span className="text-xs" style={{ color: COLORS.accentLight }}>{shot.scene} · {shot.shot}</span></div>
      <section className="surface p-4 space-y-3">
        <label className="block text-xs" style={{ color: COLORS.secondary }}>画面描述<textarea className="field mt-1" rows={6} maxLength={500} value={draft.plot} onChange={(event) => setDraft((value) => ({ ...value, plot: event.target.value }))} /></label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block text-xs" style={{ color: COLORS.secondary }}>镜头类型<input className="field mt-1" value={draft.shotType} onChange={(event) => setDraft((value) => ({ ...value, shotType: event.target.value }))} /></label>
          <label className="block text-xs" style={{ color: COLORS.secondary }}>运镜<input className="field mt-1" value={draft.cameraMovement} onChange={(event) => setDraft((value) => ({ ...value, cameraMovement: event.target.value }))} /></label>
        </div>
        <label className="block text-xs" style={{ color: COLORS.secondary }}>旁白 / Dialogue<textarea className="field mt-1" rows={3} value={draft.dialogue} onChange={(event) => setDraft((value) => ({ ...value, dialogue: event.target.value }))} /></label>
        <div className="flex flex-wrap gap-1">{(shot.characters || []).map((character) => <span className="chip" key={character}>{character}</span>)}</div>
      </section>

      <section className="surface p-3">
        <div className="grid grid-cols-3 gap-1 mb-3">
          {[['keyframe', '关键帧'], ['video', '视频'], ['audio', '音频']].map(([key, label]) => <button key={key} className={preview === key ? 'primary-button' : 'secondary-button'} onClick={() => setPreview(key)}>{label}</button>)}
        </div>
        {preview === 'keyframe' && <div className="shot-thumb">{shot.keyframe ? <img src={shot.keyframe} alt="关键帧" /> : <div className="h-full flex items-center justify-center" style={{ color: COLORS.muted }}>尚未生成关键帧</div>}</div>}
        {preview === 'video' && (shot.video ? <video src={shot.video} controls className="w-full rounded-md bg-black" /> : <div className="shot-thumb flex items-center justify-center" style={{ color: COLORS.muted }}>尚未生成视频</div>)}
        {preview === 'audio' && <div className="py-8 text-center" style={{ color: COLORS.muted }}>音频生成状态将在这里显示</div>}
      </section>

      <div className="grid grid-cols-2 gap-2">
        <button className="primary-button" disabled={!taskId || Boolean(action)} onClick={() => onSave(shot, draft)}>保存 Shot</button>
        <button className="secondary-button" disabled={!taskId || Boolean(action)} onClick={() => onRegenerate(shot)}>重新规划</button>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {[['keyframe', '关键帧'], ['video', '视频'], ['audio', '音频']].map(([type, label]) => <button key={type} className="secondary-button !px-1 text-xs" disabled={!taskId || Boolean(action)} onClick={() => onGenerate(type, shot)}>{action === type ? '生成中…' : `生成${label}`}</button>)}
      </div>
    </main>
  )
}

function TaskScreen({ data, phase, progress, taskId, action, activeCase, onFinal, onPlay }) {
  const cost = data.cost || {}
  const displayTaskId = activeCase?.task_id || taskId
  return (
    <main className="mobile-content space-y-3">
      <div className="py-2"><h2 className="m-0 text-base font-bold">任务中心</h2></div>
      <section className="surface p-4 space-y-3">
        <div className="flex justify-between"><span style={{ color: COLORS.secondary }}>状态</span><b style={{ color: phase === 'done' ? COLORS.green : COLORS.accentLight }}>{displayTaskId ? (activeCase ? '案例已完成' : phase === 'done' ? '已完成' : '进行中') : '尚未开始'}</b></div>
        <div className="flex justify-between text-sm"><span style={{ color: COLORS.secondary }}>任务进度</span><b style={{ color: COLORS.accentLight }}>{progress}%</b></div>
        <div className="h-2 rounded-sm overflow-hidden" style={{ background: '#2d2018' }}><div className="h-full" style={{ width: `${progress}%`, background: phase === 'done' ? COLORS.green : COLORS.accent, transition: 'width .3s' }} /></div>
        {displayTaskId && <div className="text-xs font-mono break-all" style={{ color: COLORS.muted }}>{activeCase ? '案例任务' : 'Task ID'}: {displayTaskId}</div>}
      </section>
      <section className="surface p-4">
        <h3 className="m-0 mb-3 text-sm">执行日志</h3>
        <div className="space-y-2 max-h-64 overflow-y-auto">{data.logs.length ? data.logs.map((log, index) => <div key={index} className="text-xs leading-5" style={{ color: COLORS.secondary }}><span style={{ color: COLORS.muted }}>{log.time}</span> <b style={{ color: COLORS.accentLight }}>[{log.agent}]</b> {log.msg}</div>) : <div className="text-sm" style={{ color: COLORS.muted }}>任务开始后显示规划与生成进度</div>}</div>
      </section>
      <section className="surface p-4 space-y-3">
        <h3 className="m-0 text-sm">成本与成片</h3>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">{[['输入', cost.input_tokens || 0], ['输出', cost.output_tokens || 0], ['总计', cost.total_tokens || 0]].map(([label, value]) => <div key={label} className="p-2 rounded-md" style={{ border: '1px solid #2d2018' }}><b>{Number(value).toLocaleString()}</b><div style={{ color: COLORS.muted }}>{label} Tokens</div></div>)}</div>
        <button className="primary-button w-full" disabled={data.final_video ? false : !taskId || phase !== 'done' || Boolean(action)} onClick={data.final_video ? onPlay : onFinal}>{data.final_video ? '▶ 播放成片' : action === 'final' ? '正在合成…' : '🎬 合成已有镜头'}</button>
      </section>
    </main>
  )
}

function ProgressDock({ progress, active }) {
  if (!active) return null
  return <div className="progress-dock"><div className="flex justify-between text-xs mb-1"><span style={{ color: COLORS.secondary }}>任务进行中</span><b style={{ color: COLORS.accentLight }}>{progress}%</b></div><div className="h-1 rounded-sm overflow-hidden" style={{ background: '#39291f' }}><div className="h-full" style={{ width: `${progress}%`, background: COLORS.accent }} /></div></div>
}

function BottomNav({ section, onSection, onGenerate, onFilm }) {
  const items = [
    { key: 'input', icon: '▤', label: '输入', action: () => onSection('input') },
    { key: 'storyboard', icon: '▦', label: '分镜', action: () => onSection('storyboard') },
    { key: 'generate', icon: '✦', label: '生成', action: onGenerate },
    { key: 'task', icon: '▣', label: '任务', action: () => onSection('task') },
    { key: 'film', icon: '▥', label: '成片', action: onFilm },
  ]
  return <nav className="bottom-nav">{items.map((item) => item.key === 'generate' ? <button key={item.key} className="nav-button" onClick={item.action}><span className="generate-button flex items-center justify-center">{item.icon}</span><span style={{ color: COLORS.accentLight }}>{item.label}</span></button> : <button key={item.key} className={`nav-button ${section === item.key ? 'active' : ''}`} onClick={item.action}><span className="nav-icon">{item.icon}</span><span>{item.label}</span></button>)}</nav>
}

function GenerateSheet({ open, onClose, shot, taskId, phase, action, onGenerate, onPlan, onFinal }) {
  if (!open) return null
  return <><button className="sheet-backdrop border-0" aria-label="关闭生成菜单" onClick={onClose} /><section className="action-sheet"><div className="flex items-center justify-between mb-3"><div><b>生成操作</b>{shot && <div className="text-xs mt-1" style={{ color: COLORS.muted }}>{shot.scene} · {shot.shot}</div>}</div><button className="mobile-icon-button" onClick={onClose}>✕</button></div>{!taskId ? <button className="primary-button w-full" onClick={() => { onClose(); onPlan() }}>🎬 开始规划</button> : <div className="sheet-grid">{[['keyframe', '▧ 生成关键帧'], ['video', '▶ 生成视频'], ['audio', '♫ 生成音频']].map(([type, label]) => <button className="secondary-button" key={type} disabled={!shot || Boolean(action)} onClick={() => { onClose(); onGenerate(type, shot) }}>{label}</button>)}<button className="primary-button" disabled={phase !== 'done' || Boolean(action)} onClick={() => { onClose(); onFinal() }}>🎞 合成成片</button></div>}</section></>
}

function Gallery({ open, onClose }) {
  const [selected, setSelected] = useState(null)
  if (!open) return null
  return <div className="gallery-modal"><div className="sticky top-0 z-10 flex items-center px-4 py-3" style={{ background: '#17120e', borderBottom: '1px solid #2d2018' }}><div className="flex-1"><b>{selected ? `《${selected.title}》` : '成片展示'}</b><div className="text-xs mt-1" style={{ color: COLORS.muted }}>{selected ? selected.meta : `已生成作品 · ${FINISHED_FILMS.length} 部`}</div></div>{selected && <button className="mobile-icon-button mr-2" onClick={() => setSelected(null)} aria-label="返回列表">‹</button>}<button className="mobile-icon-button" onClick={onClose} aria-label="关闭">✕</button></div><div className="p-4">{selected ? <video key={selected.video} controls autoPlay src={selected.video} className="w-full bg-black rounded-md" /> : <div className="gallery-list">{FINISHED_FILMS.map((film) => <button key={film.title} className="shot-card" onClick={() => setSelected(film)}><b>《{film.title}》</b><div className="text-xs mt-1" style={{ color: COLORS.muted }}>{film.meta}</div></button>)}</div>}</div></div>
}

function CasePicker({ open, cases, loading, onClose, onSelect }) {
  if (!open) return null
  return <div className="gallery-modal"><div className="sticky top-0 z-10 flex items-center px-4 py-3" style={{ background: '#17120e', borderBottom: '1px solid #2d2018' }}><div className="flex-1"><b>案例展示</b><div className="text-xs mt-1" style={{ color: COLORS.muted }}>浏览完整生成过程，无需订阅码</div></div><button className="mobile-icon-button" onClick={onClose} aria-label="关闭">✕</button></div><div className="p-4 gallery-list">{cases.map((item) => <button key={item.id} className="shot-card case-card" onClick={() => onSelect(item.id)}><div className="shot-thumb">{item.preview && <img src={item.preview} alt="" />}</div><div><b>《{item.title}》</b><div className="text-xs mt-2" style={{ color: COLORS.muted }}>{item.meta}</div><div className="text-xs mt-2" style={{ color: COLORS.accentLight }}>查看生成过程 →</div></div></button>)}{!cases.length && <div className="py-12 text-center" style={{ color: COLORS.muted }}>{loading ? '正在加载案例…' : '暂无可展示案例'}</div>}</div></div>
}

function SubscriptionDialog({ open, onClose, onVerified }) {
  const [code, setCode] = useState('')
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  if (!open) return null
  const submit = async () => {
    if (!code.trim()) return
    setSubmitting(true); setMessage('')
    try { await verifySubscriptionCode(code.trim()); onVerified(); onClose() }
    catch (requestError) { setMessage(requestError.message) }
    finally { setSubmitting(false) }
  }
  return <><button className="sheet-backdrop border-0" aria-label="关闭订阅码输入" onClick={onClose} /><section className="action-sheet"><div className="flex items-start"><div className="flex-1"><b>请输入订阅码</b><div className="text-xs mt-2 leading-5" style={{ color: COLORS.muted }}>输入项目所有者提供的订阅码后，可使用改写、规划和全部生成能力。若暂无订阅码，可点击左上角“案例展示”，预览基础功能。</div></div><button className="mobile-icon-button" onClick={onClose}>✕</button></div><input autoFocus className="field mt-4" value={code} onChange={(event) => setCode(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && submit()} placeholder="请输入订阅码" />{message && <div className="text-xs mt-3" style={{ color: '#fca5a5' }}>{message}</div>}<button className="primary-button w-full mt-3" disabled={!code.trim() || submitting} onClick={submit}>{submitting ? '正在验证…' : '验证并解锁'}</button></section></>
}

function FinalVideo({ src, open, onClose }) {
  if (!open || !src) return null
  return <div className="gallery-modal"><div className="flex items-center px-4 py-3" style={{ background: '#17120e', borderBottom: '1px solid #2d2018' }}><b className="flex-1">项目成片</b><button className="mobile-icon-button" onClick={onClose}>✕</button></div><div className="p-4"><video src={src} controls autoPlay className="w-full bg-black rounded-md" /></div></div>
}

export default function App() {
  const [data, setData] = useState(EMPTY_DATA)
  const [section, setSection] = useState('storyboard')
  const [selectedShotId, setSelectedShotId] = useState('')
  const [scriptInput, setScriptInput] = useState('')
  const [charactersInput, setCharactersInput] = useState('')
  const [rewritten, setRewritten] = useState('')
  const [taskId, setTaskId] = useState('')
  const [phase, setPhase] = useState('empty')
  const [action, setAction] = useState('')
  const [actionShotId, setActionShotId] = useState('')
  const [error, setError] = useState('')
  const [generateOpen, setGenerateOpen] = useState(false)
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [casePickerOpen, setCasePickerOpen] = useState(false)
  const [subscriptionOpen, setSubscriptionOpen] = useState(false)
  const [subscribed, setSubscribed] = useState(() => hasSubscriptionCode())
  const [publicCases, setPublicCases] = useState([])
  const [casesLoading, setCasesLoading] = useState(true)
  const [activeCase, setActiveCase] = useState(null)
  const [videoOpen, setVideoOpen] = useState(false)
  const [pollKey, setPollKey] = useState(0)

  const shots = useMemo(() => allShots(data), [data])
  const selectedShot = shots.find((shot) => shot.id === selectedShotId)
  const progress = activeCase ? 100 : taskId ? Number(data.task.progress || 0) : phase === 'rewritten' ? 20 : 0

  const applyTask = useCallback((id, task, script, characters) => {
    const next = taskToDemoData(id, task, script, characters)
    const nextShots = allShots(next)
    setData(next)
    setPhase(task.status === 'done' ? 'done' : nextShots.length ? 'scenes' : 'planning')
    if (!selectedShotId && nextShots[0]) setSelectedShotId(nextShots[0].id)
    if (task.status === 'error') setError(task.logs?.at(-1) || '后端任务执行失败')
    return next
  }, [selectedShotId])

  useEffect(() => {
    let cancelled = false
    listCases()
      .then((result) => { if (!cancelled) setPublicCases(result.cases || []) })
      .catch((requestError) => { if (!cancelled) setError(`案例列表加载失败：${requestError.message}`) })
      .finally(() => { if (!cancelled) setCasesLoading(false) })
    const requireSubscription = () => { setSubscribed(false); setSubscriptionOpen(true) }
    window.addEventListener('movieagent:subscription-required', requireSubscription)
    return () => { cancelled = true; window.removeEventListener('movieagent:subscription-required', requireSubscription) }
  }, [])

  useEffect(() => {
    if (!taskId) return undefined
    let cancelled = false
    let timer
    const refresh = async () => {
      try {
        const task = await getTask(taskId)
        if (cancelled) return
        applyTask(taskId, task, rewritten || scriptInput, splitCharacters(charactersInput))
        if (task.status !== 'done' && task.status !== 'error') timer = window.setTimeout(refresh, 1800)
      } catch (requestError) {
        if (!cancelled) setError(`读取任务状态失败：${requestError.message}`)
      }
    }
    refresh()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [taskId, pollKey, applyTask, rewritten, scriptInput, charactersInput])

  const rewrite = async () => {
    if (!scriptInput.trim()) return
    if (!hasSubscriptionCode()) {
      setSubscriptionOpen(true)
      return
    }
    setAction('rewrite'); setError('')
    try { const result = await rewriteScript(scriptInput.trim()); setRewritten(result.rewritten || ''); setPhase('rewritten') }
    catch (requestError) { setError(`剧本改写失败：${requestError.message}`) }
    finally { setAction('') }
  }

  const startLive = async () => {
    const characters = splitCharacters(charactersInput)
    if (!rewritten.trim() || !characters.length) { setSection('input'); setError('请先完成剧本改写并填写角色'); return }
    setActiveCase(null)
    setAction('planning'); setError(''); setPhase('planning'); setSection('task')
    try {
      const result = await startPlanning(rewritten.trim(), characters, scriptInput.trim())
      setTaskId(result.task_id); window.localStorage.setItem('movieagent-task-id', result.task_id)
    } catch (requestError) { setPhase('rewritten'); setSection('input'); setError(`无法连接后端：${requestError.message}`) }
    finally { setAction('') }
  }

  const refreshTask = async () => {
    const task = await getTask(taskId)
    applyTask(taskId, task, rewritten, splitCharacters(charactersInput))
    return task
  }

  const generateArtifact = async (type, shot) => {
    if (!taskId || !shot) return
    const calls = { keyframe: generateKeyframe, video: generateVideo, audio: generateAudio }
    setAction(type); setActionShotId(shot.id); setError('')
    try { await calls[type](taskId, shot); await refreshTask() }
    catch (requestError) { setError(`${type === 'keyframe' ? '关键帧' : type === 'video' ? '视频' : '音频'}生成失败：${requestError.message}`) }
    finally { setAction(''); setActionShotId('') }
  }

  const saveShot = async (shot, draft) => {
    setAction('save'); setError('')
    try { await updateShot(taskId, shot, draft); await refreshTask() }
    catch (requestError) { setError(`保存 Shot 失败：${requestError.message}`) }
    finally { setAction('') }
  }

  const regenerate = async (shot) => {
    setAction('regenerate'); setError('')
    try { await regenerateShots(taskId, { subScriptName: shot.subScriptName, scene: shot.scene }); setPollKey((value) => value + 1) }
    catch (requestError) { setError(`重新规划失败：${requestError.message}`) }
    finally { setAction('') }
  }

  const generateCharacterRefs = async () => {
    setAction('characters'); setError('')
    try { await generateCharacters(taskId, splitCharacters(charactersInput), rewritten); await refreshTask() }
    catch (requestError) { setError(`人物定妆图生成失败：${requestError.message}`) }
    finally { setAction('') }
  }

  const generateFinal = async () => {
    setAction('final'); setError('')
    try { const result = await generateFinalVideo(taskId); setData((value) => ({ ...value, final_video: result.final_video_url })); setVideoOpen(true) }
    catch (requestError) { setError(`成片合成失败：${requestError.message}`) }
    finally { setAction('') }
  }

  const loadPublicCase = async (caseId) => {
    setCasesLoading(true); setError('')
    try {
      const result = await getCase(caseId)
      const next = taskToDemoData(result.case.task_id, result.task, result.script, result.characters)
      next.task.title = result.case.title
      const nextShots = allShots(next)
      setData(next)
      setScriptInput(result.task.raw_script || result.script)
      setRewritten(result.script)
      setCharactersInput(result.characters.join('，'))
      setTaskId('')
      setPhase('done')
      setActiveCase(result.case)
      setSelectedShotId(nextShots[0]?.id || '')
      setSection('storyboard')
      setCasePickerOpen(false)
    } catch (requestError) { setError(`案例加载失败：${requestError.message}`) }
    finally { setCasesLoading(false) }
  }

  const resetWorkspace = () => {
    setData(EMPTY_DATA); setScriptInput(''); setRewritten(''); setCharactersInput(''); setTaskId(''); setPhase('empty'); setActionShotId(''); setError(''); setActiveCase(null); setSelectedShotId(''); setSection('input')
  }

  const openShot = (id) => { setSelectedShotId(id); setSection('editor') }
  const openShotGenerate = (id) => { setSelectedShotId(id); setGenerateOpen(true) }
  const state = { data, phase, action, taskId, scriptInput, charactersInput, rewritten }
  const actions = { setScriptInput, setCharactersInput, rewrite, startPlanning: startLive, generateCharacters: generateCharacterRefs }

  return (
    <div className="mobile-app">
      <MobileHeader onGallery={() => setGalleryOpen(true)} />
      <CaseBar activeCase={activeCase} subscribed={subscribed} onOpenCases={() => setCasePickerOpen(true)} onOpenSubscription={() => setSubscriptionOpen(true)} onNew={resetWorkspace} />
      {error && <div className="mx-4 mt-3 p-3 text-sm rounded-md" style={{ color: '#fecaca', background: '#7f1d1d44', border: '1px solid #ef444466' }}>{error}<button className="float-right bg-transparent border-0" style={{ color: '#fecaca' }} onClick={() => setError('')}>✕</button></div>}
      <WorkflowTabs section={section} onChange={setSection} />
      {section === 'input' && <InputScreen state={state} actions={actions} />}
      {section === 'storyboard' && <StoryboardScreen data={data} selectedShotId={selectedShotId} action={action} actionShotId={actionShotId} canGenerate={Boolean(taskId)} onSelect={setSelectedShotId} onEdit={openShot} onGenerate={openShotGenerate} />}
      {section === 'editor' && <ShotEditorScreen shot={selectedShot} taskId={taskId} action={action} onSave={saveShot} onGenerate={generateArtifact} onRegenerate={regenerate} />}
      {section === 'task' && <TaskScreen data={data} phase={phase} progress={progress} taskId={taskId} action={action} activeCase={activeCase} onFinal={generateFinal} onPlay={() => setVideoOpen(true)} />}
      <ProgressDock progress={progress} active={Boolean(taskId) && phase !== 'done'} />
      <BottomNav section={section} onSection={setSection} onGenerate={() => setGenerateOpen(true)} onFilm={() => data.final_video ? setVideoOpen(true) : setGalleryOpen(true)} />
      <GenerateSheet open={generateOpen} onClose={() => setGenerateOpen(false)} shot={selectedShot} taskId={taskId} phase={phase} action={action} onGenerate={generateArtifact} onPlan={startLive} onFinal={generateFinal} />
      <Gallery open={galleryOpen} onClose={() => setGalleryOpen(false)} />
      <FinalVideo src={data.final_video} open={videoOpen} onClose={() => setVideoOpen(false)} />
      <CasePicker open={casePickerOpen} cases={publicCases} loading={casesLoading} onClose={() => setCasePickerOpen(false)} onSelect={loadPublicCase} />
      <SubscriptionDialog open={subscriptionOpen} onClose={() => setSubscriptionOpen(false)} onVerified={() => setSubscribed(true)} />
    </div>
  )
}
