import { useState, useRef, useEffect, useCallback, Fragment } from 'react'
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
  regenerateScenes,
  regenerateShots,
  rewriteScript,
  startPlanning,
  taskToDemoData,
  updateScene,
  updateShot,
  verifySubscriptionCode,
} from './api'

const EMPTY_DATA = {
  ...demoData,
  task: { ...demoData.task, id: '', title: '', status: 'pending', progress: 0, created_at: '', finished_at: '' },
  story: { raw_synopsis: '', synopsis: '', characters: [] },
  storyboard: [{ sub_script: 'Sub-Script 1', sub_script_plot: '', scenes: [] }],
  logs: [],
  cost: { ...demoData.cost, input_tokens: 0, output_tokens: 0, total_tokens: 0, estimated_cost_usd: 0 },
  final_video: '',
  film_duration: '',
  showcase_video: '',
}

let currentData = EMPTY_DATA

// ─── ANIMATION PHASES ─────────────────────────────────────────────────────────
// empty → typing_raw → rewriting → typing_synopsis → director → scenes → done

const COLORS = {
  pageBg: '#120e0a',
  cardBg: '#1a1410',
  cardBorder: '#2d2018',
  accentPurple: '#f59e0b',
  accentLight: '#fbbf24',
  statusGreen: '#22c55e',
  textPrimary: '#ede8e0',
  textSecondary: '#a89b8c',
  textMuted: '#5c5048',
}

const publicTrace = (text) => text.replace(
  '（完整版将展示模型真实输出的 Chain-of-Thought）',
  '后端仅返回结构化结果，不展示模型内部推理。',
)

const FINISHED_FILMS = [
  {
    title: '森林图书馆的借阅者',
    video: '/videos/10015e62-b575-4dec-ae27-fcceffb70349_final.mp4',
    meta: '22 镜头 · 720p · 已配音',
  },
  {
    title: '提灯人',
    video: '/videos/f6731663-21ed-45e8-9c30-7e743ad2fc7b_tidengman_final.mp4',
    meta: '8 镜头 · 720p · 已配音',
  },
  {
    title: '天台上的信号',
    video: '/videos/155d84f0-d595-4e3a-a48e-7cebaa50e579_final.mp4',
    meta: '16 镜头 · 720p',
  },
  {
    title: '修伞匠',
    video: '/videos/718d2afa-2016-4f71-bd94-37ed494634d0_final.mp4',
    meta: '28 镜头 · 720p',
  },
  {
    title: '松鼠奇奇',
    video: '/videos/bf7d7db7-b545-4df9-889f-91a38afd6a20_squirrel_final_subtitled.mp4',
    meta: '6 镜头 · 720p · 字幕版',
  },
]

// Real agent system prompts from agents/shot.py and agents/scene.py
const SHOT_PROMPT = `You are a professional movie director. Your task is to transform the provided scene details into a well-structured shot list that effectively captures the emotions, plot, and visual storytelling. Follow the structured reasoning process below before generating the final output.

-------------------------------
Step 1: Internal Chain-of-Thought
-------------------------------
[INTERNAL INSTRUCTIONS:
Before generating the final output, perform structured reasoning to ensure logical and high-quality shot composition. Follow these steps:

1. **Break Down Scene into Key Shots**
   - Identify the essential moments in the scene that require distinct shots.
   - Ensure that each shot serves a clear narrative or emotional purpose.
   - Determine logical transitions between shots to maintain visual continuity.

2. **Define Shot Composition and Framing**
   - Select the appropriate shot type (e.g., close-up for emotion, wide shot for setting).
   - Ensure framing adheres to cinematic principles (e.g., rule of thirds, leading lines).
   - Identify the key objects and characters that must be visible in the frame.

3. **Determine Character Positioning & Bounding Boxes**
   - Place characters using normalized bounding boxes, ensuring proper distribution in the frame.
   - Ensure that bounding boxes do not exceed an interpolation of 0.5.
   - Make the bounding boxes as large as possible to focus on key characters.
   - Bounding boxes must not intersect or overlap.

4. **Enhance Emotional Impact**
   - Identify the dominant emotion for each shot (e.g., fear, sadness, triumph).
   - Adjust lighting, depth of field, and contrast to reinforce the emotional tone.

5. **Refine Camera Techniques and Movements**
   - Specify camera movements (e.g., static shot for tension, dolly-in for intimacy).
   - Adjust angles dynamically to maintain narrative engagement.

6. **Ensure Dialogue Accuracy**
   - Extract relevant dialogue for each shot, ensuring proper pacing.
   - All dialogue in \`Dialogue\` must be written in Simplified Chinese.]

-------------------------------
Step 2: Final Output
-------------------------------
Output your final result in JSON format with keys: "Internal Chain-of-Thought" and "Shot".`

const DIRECTOR_PROMPT = `You are a movie screenwriter. Your overall task is to transform a given script synopsis into a detailed sub-script, dividing it step by step.

-------------------------------
Step 1: Internal Chain-of-Thought
-------------------------------
1. Identify Core Narrative Structure — acts, plot beats, turning points, scene transitions.
2. Extract Key Character Information — major/supporting characters, relationships.
3. Define Temporal Segmentation — explicit/implicit timeline cues.
4. Validate Sub-Script Breakdown — each sub-script ≥50 words, total ≤20 sub-scripts.
5. Justify the Division — reasoning behind each segmentation boundary.

-------------------------------
Step 2: Final Output
-------------------------------
Output in JSON format:
{
  "Relationships": { "Character1 - Character2": "relationship" },
  "Internal Chain-of-Thought": { "Core Narrative Structure": "...", ... },
  "Sub-Script": {
    "Sub-Script 1": { "Plot": "...", "Involving Characters": [...], "Timeline": "...", "Reason for Division": "..." }
  }
}`

const SCRIPT_REWRITER_PROMPT = `你是一位专业的动画剧本改编师，擅长把零散、扁平的故事素材重组为适合分镜规划的叙事文本。

你的任务：将用户提供的原始故事输入，改写为一段叙事结构丰富的连续段落，使其更适合后续的导演分镜规划。

改写时需在以下三个维度上增强：
1. 叙事弧度：把并列的、孤立的动作描述，重组为有因果关系和情绪起伏的连贯叙事。
2. 角色塑造：为出场角色补充简洁的性格或身份标签，使不同角色具有辨识度。
3. 情绪与场景：适度补充环境描写和情绪渲染，增强画面感和感官细节。

约束：不得添加原文中不存在的核心情节、人物或结局。只输出改写后的段落正文，不要输出前言或解释。`

const SCENE_PROMPT = `You are a movie director and script planner. Your overall task is to transform a given movie script synopsis into well-defined key scenes, ensuring a structured and cinematic breakdown.

-------------------------------
Step 1: Internal Chain-of-Thought
-------------------------------
[INTERNAL INSTRUCTIONS:
1. **Analyze the Narrative Structure** — Identify core acts, turning points, scene boundaries.
2. **Extract Key Scene Elements** — List characters, roles, events, conflicts, emotional beats.
3. **Define Scene Boundaries** — Natural breaks (location shifts, time jumps, emotional climaxes).
4. **Enhance Cinematic Elements** — Scene Description, Emotional Tone, Visual Style, Key Props, Music & Sound Effects, Cinematography Notes.]

-------------------------------
Step 2: Final Output
-------------------------------
Output a structured scene breakdown in JSON format. Each scene must include:
Involving Characters, Plot, Scene Description, Emotional Tone, Visual Style, Key Props, Music and Sound Effects, Cinematography Notes.`

// ─── helpers ──────────────────────────────────────────────────────────────────
const StatusBadge = ({ status }) => {
  const done = status === 'done'
  return (
    <span
      style={{
        background: done ? 'rgba(34,197,94,0.15)' : 'rgba(148,163,184,0.12)',
        color: done ? COLORS.statusGreen : COLORS.textSecondary,
        border: `1px solid ${done ? 'rgba(34,197,94,0.3)' : COLORS.cardBorder}`,
      }}
      className="text-xs px-2 py-0.5 rounded-full font-medium"
    >
      {done ? '已完成' : status}
    </span>
  )
}

const CharacterPill = ({ name, color }) => (
  <span
    style={{
      background: `${color}22`,
      color: color,
      border: `1px solid ${color}55`,
    }}
    className="text-xs px-2 py-0.5 rounded-full font-medium whitespace-nowrap"
  >
    {name}
  </span>
)

// ─── skeleton helpers (框架常驻 · 数据流入) ─────────────────────────────────────
// A shimmering placeholder bar shown in the shell while data has not arrived yet.
const SkeletonBar = ({ w = '100%', h = 10, radius = 4, style = {} }) => (
  <div
    className="mag-skeleton"
    style={{ width: w, height: h, borderRadius: radius, ...style }}
  />
)

// One placeholder shot row filling every storyboard column with skeleton bars.
const SkeletonShotRow = () => (
  <tr style={{ background: COLORS.cardBg, borderBottom: `1px solid ${COLORS.cardBorder}` }}>
    <td className="px-2 py-2"><SkeletonBar w="22px" /></td>
    <td className="px-2 py-2"><SkeletonBar w="30px" /></td>
    <td className="px-2 py-2"><SkeletonBar w="64px" h={40} radius={4} /></td>
    <td className="px-2 py-2" style={{ maxWidth: '180px' }}>
      <SkeletonBar /><div style={{ height: '4px' }} /><SkeletonBar w="72%" />
    </td>
    <td className="px-2 py-2"><SkeletonBar w="42px" h={16} radius={999} /></td>
    <td className="px-2 py-2"><SkeletonBar w="60px" /></td>
    <td className="px-2 py-2"><SkeletonBar w="60px" /></td>
    <td className="px-2 py-2"><SkeletonBar w="84px" /></td>
    <td className="px-2 py-2"><SkeletonBar w="44px" h={16} radius={999} /></td>
    <td className="px-2 py-2"><SkeletonBar w="44px" h={16} radius={999} /></td>
    <td className="px-2 py-2"><SkeletonBar w="48px" /></td>
  </tr>
)

const EMPTY_STORYBOARD_HINTS = [
  '规划后显示镜头的画面构图与动作描述',
  '每个场景会拆分为可独立生成的镜头',
  '关键帧生成后，可继续生成对应视频',
  '选择镜头后，可在右侧查看和修改详情',
]

const EmptyStoryboardRow = ({ index }) => (
  <tr style={{ background: index % 2 === 0 ? COLORS.cardBg : COLORS.pageBg, borderBottom: `1px solid ${COLORS.cardBorder}`, height: '68px' }}>
    <td className="px-2 py-2 whitespace-nowrap" style={{ color: COLORS.textMuted }}>Scene {index + 1}</td>
    <td className="px-2 py-2 whitespace-nowrap" style={{ color: COLORS.textMuted }}>—</td>
    <td className="px-2 py-2">
      <div className="flex items-center justify-center text-center rounded text-xs" style={{ width: '64px', height: '40px', color: COLORS.textMuted, background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}` }}>
        关键帧<br />预览
      </div>
    </td>
    <td className="px-2 py-2" style={{ color: COLORS.textMuted, minWidth: '190px', maxWidth: '240px' }}>{EMPTY_STORYBOARD_HINTS[index]}</td>
    <td className="px-2 py-2 whitespace-nowrap" style={{ color: COLORS.textMuted }}>出镜角色</td>
    <td className="px-2 py-2 whitespace-nowrap" style={{ color: COLORS.textMuted }}>景别</td>
    <td className="px-2 py-2" style={{ color: COLORS.textMuted, minWidth: '90px' }}>运镜方式</td>
    <td className="px-2 py-2" style={{ color: COLORS.textMuted, minWidth: '110px' }}>台词或旁白</td>
    <td className="px-2 py-2 whitespace-nowrap" style={{ color: COLORS.textMuted }}>未生成</td>
    <td className="px-2 py-2 whitespace-nowrap" style={{ color: COLORS.textMuted }}>未生成</td>
    <td className="px-2 py-2" style={{ color: COLORS.textMuted, minWidth: '88px' }}>编辑 / 生成</td>
  </tr>
)

// Map the current animation phase to an overall progress percentage (0-100).
function phaseProgress(animPhase, visibleSceneCount, sceneTotal) {
  switch (animPhase) {
    case 'empty': return 0
    case 'typing_raw': return 12
    case 'rewriting': return 28
    case 'typing_synopsis': return 45
    case 'director': return 60
    case 'scenes':
      return Math.min(95, 60 + Math.round((visibleSceneCount / Math.max(sceneTotal, 1)) * 35))
    case 'done': return 100
    default: return 0
  }
}

// ─── TOP BAR ─────────────────────────────────────────────────────────────────
function TopBar({ animPhase, onReset, onOpenGallery, taskId }) {
  const isDone = animPhase === 'done'
  const isAnimating = animPhase !== 'empty' && animPhase !== 'done'

  return (
    <div
      className="flex items-center px-4 gap-4 flex-shrink-0"
      style={{
        height: '48px',
        background: COLORS.cardBg,
        borderBottom: `1px solid ${COLORS.cardBorder}`,
      }}
    >
      <span className="font-bold text-base" style={{ color: COLORS.textPrimary }}>
        🎬 MovieAgent Demo
      </span>
      {isDone && (
        <>
          <span style={{ color: COLORS.textMuted }} className="text-xs">
            TaskID:&nbsp;
            <span style={{ color: COLORS.textSecondary }} className="font-mono">
              {taskId ? taskId.slice(0, 8) : currentData.task.id.slice(0, 8)}
            </span>
          </span>
          <span className="flex items-center gap-1 text-xs" style={{ color: COLORS.statusGreen }}>
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: COLORS.statusGreen }} />
            已完成 100%
          </span>
        </>
      )}
      <div className="flex-1" />
      <button
        onClick={onOpenGallery}
        className="text-xs px-3 py-1 rounded font-medium"
        style={{
          border: `1px solid ${COLORS.accentPurple}`,
          color: COLORS.accentLight,
          background: `${COLORS.accentPurple}18`,
          cursor: 'pointer',
        }}
      >
        🎞 成片展示
      </button>
      {isDone && (
        <button
          onClick={onReset}
          className="text-xs px-3 py-1 rounded"
          style={{
            border: `1px solid ${COLORS.cardBorder}`,
            color: COLORS.textSecondary,
            background: 'transparent',
            cursor: 'pointer',
          }}
        >
          ↺ 重置
        </button>
      )}
      {isAnimating && (
        <span className="text-xs px-3 py-1 rounded" style={{ color: COLORS.textMuted, border: `1px solid ${COLORS.cardBorder}` }}>
          ⏳ 加载中...
        </span>
      )}
    </div>
  )
}

function CaseBar({ activeCase, subscribed, onOpenCases, onOpenSubscription }) {
  return (
    <div className="flex items-center gap-3 px-4 flex-shrink-0" style={{ minHeight: '46px', background: COLORS.pageBg, borderBottom: `1px solid ${COLORS.cardBorder}` }}>
      <button onClick={onOpenCases} className="text-xs px-3 py-2 rounded font-semibold" style={{ border: `1px solid ${COLORS.accentPurple}`, color: COLORS.accentLight, background: `${COLORS.accentPurple}18` }}>
        📂 案例展示
      </button>
      <span className="text-xs truncate" style={{ color: activeCase ? COLORS.textSecondary : COLORS.textMuted }}>
        {activeCase ? `正在浏览《${activeCase.title}》 · 只读案例，无需订阅码` : '选择完整案例，可查看剧本、分镜、关键帧、日志与成片'}
      </span>
      <div className="flex-1" />
      <button onClick={onOpenSubscription} className="text-xs px-3 py-2 rounded whitespace-nowrap" style={{ border: `1px solid ${subscribed ? COLORS.statusGreen : COLORS.cardBorder}`, color: subscribed ? COLORS.statusGreen : COLORS.textSecondary, background: 'transparent' }}>
        {subscribed ? '✓ 完整功能已解锁' : '🔑 输入订阅码'}
      </button>
    </div>
  )
}

function CasePickerModal({ open, cases, loading, onClose, onSelect }) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-5" style={{ background: 'rgba(0,0,0,.78)' }}>
      <div className="w-full overflow-hidden rounded" style={{ maxWidth: '760px', maxHeight: '82vh', background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
        <div className="flex items-center px-5 py-4" style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}>
          <div className="flex-1"><div className="font-semibold">案例展示</div><div className="text-xs mt-1" style={{ color: COLORS.textMuted }}>案例为只读展示，浏览全过程无需订阅码</div></div>
          <button onClick={onClose} className="text-lg px-2 bg-transparent border-0" style={{ color: COLORS.textSecondary }} aria-label="关闭案例展示">×</button>
        </div>
        <div className="grid grid-cols-2 gap-3 p-5 overflow-y-auto" style={{ maxHeight: '68vh' }}>
          {cases.map((item) => (
            <button key={item.id} onClick={() => onSelect(item.id)} className="flex gap-3 text-left p-3 rounded" style={{ color: COLORS.textPrimary, background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}` }}>
              <div className="flex-shrink-0 overflow-hidden rounded" style={{ width: '116px', aspectRatio: '16 / 9', background: '#090705' }}>
                {item.preview && <img src={item.preview} alt="" className="w-full h-full object-cover" />}
              </div>
              <div className="min-w-0"><div className="font-semibold text-sm">《{item.title}》</div><div className="text-xs mt-2" style={{ color: COLORS.textMuted }}>{item.meta}</div><div className="text-xs mt-2" style={{ color: COLORS.accentLight }}>查看生成过程 →</div></div>
            </button>
          ))}
          {!cases.length && <div className="col-span-2 py-10 text-center text-sm" style={{ color: COLORS.textMuted }}>{loading ? '正在加载案例…' : '暂无可展示案例'}</div>}
        </div>
      </div>
    </div>
  )
}

function SubscriptionModal({ open, onClose, onVerified }) {
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
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-5" style={{ background: 'rgba(0,0,0,.78)' }}>
      <div className="w-full p-5 rounded" style={{ maxWidth: '420px', background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
        <div className="flex items-start"><div className="flex-1"><div className="font-semibold">请输入订阅码</div><div className="text-xs mt-2 leading-5" style={{ color: COLORS.textMuted }}>输入由项目所有者提供的订阅码后，可使用改写、规划和全部生成能力。</div></div><button onClick={onClose} className="text-lg bg-transparent border-0" style={{ color: COLORS.textSecondary }} aria-label="关闭">×</button></div>
        <label className="block text-xs mt-5" style={{ color: COLORS.textSecondary }}>订阅码<input autoFocus value={code} onChange={(event) => setCode(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && submit()} className="w-full mt-2 rounded p-3" style={{ background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textPrimary, outline: 'none' }} placeholder="请输入订阅码" /></label>
        {message && <div className="text-xs mt-3" style={{ color: '#fca5a5' }}>{message}</div>}
        <button onClick={submit} disabled={!code.trim() || submitting} className="w-full mt-4 rounded py-3 font-semibold" style={{ border: 0, color: '#1a1410', background: COLORS.accentPurple, opacity: !code.trim() || submitting ? .5 : 1 }}>{submitting ? '正在验证…' : '验证并解锁'}</button>
      </div>
    </div>
  )
}

// ─── LEFT PANEL ──────────────────────────────────────────────────────────────
function LeftPanel({ animPhase, rawTyped, synopsisTyped, scriptInput, charactersInput, onScriptChange, onCharactersChange, onRewrite, onStartPlanning, onGenerateCharacters, taskId, actionStatus, error }) {
  const { characters } = currentData.story

  const hasSynopsis = !['empty', 'typing_raw', 'rewriting'].includes(animPhase)
  const hasChars = ['director', 'scenes', 'done'].includes(animPhase)

  const initials = (name) => name.slice(0, 1)

  return (
    <div
      className="flex flex-col gap-3 p-3 overflow-y-auto flex-shrink-0"
      style={{
        width: '288px',
        background: COLORS.cardBg,
        borderRight: `1px solid ${COLORS.cardBorder}`,
      }}
    >
      {/* Title */}
      <div className="flex items-center gap-2">
        <span>📋</span>
        <span className="font-semibold text-sm" style={{ color: COLORS.textPrimary }}>
          项目输入
        </span>
      </div>

      {/* Script Rewriter */}
      <ScriptRewriterPanel
        rawSynopsis={animPhase === 'empty' ? scriptInput : rawTyped}
        rewrittenSynopsis={synopsisTyped}
        animPhase={animPhase}
        onRawChange={onScriptChange}
        onRewrite={onRewrite}
        editable={['empty', 'rewritten'].includes(animPhase)}
      />

      <div>
        <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
          角色（用逗号分隔）
        </div>
        <input
          value={charactersInput}
          onChange={(event) => onCharactersChange(event.target.value)}
          disabled={!['empty', 'rewritten'].includes(animPhase)}
          className="w-full text-xs rounded p-2"
          placeholder="例如：小满，引路鹿，山谷老人"
          style={{ background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textSecondary, outline: 'none' }}
        />
      </div>

      {/* Rewritten Synopsis — container always present, fills in when改写完成 */}
      <div>
        <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
          改写后剧本 / Rewritten Synopsis
        </div>
        <div
          className="text-xs leading-relaxed overflow-y-auto rounded p-2"
          style={{
            color: COLORS.textSecondary,
            minHeight: '68px',
            maxHeight: '160px',
            background: COLORS.pageBg,
            border: `1px solid ${COLORS.cardBorder}`,
          }}
        >
          {hasSynopsis ? (
            <>
              {synopsisTyped}
              {animPhase === 'typing_synopsis' && (
                <span
                  style={{
                    display: 'inline-block',
                    width: '2px',
                    height: '12px',
                    background: COLORS.accentPurple,
                    marginLeft: '1px',
                    verticalAlign: 'middle',
                    animation: 'blink 0.7s step-end infinite',
                  }}
                />
              )}
            </>
          ) : animPhase === 'rewriting' ? (
            <div className="flex flex-col gap-1.5 pt-0.5">
              <SkeletonBar /><SkeletonBar w="94%" /><SkeletonBar w="82%" />
            </div>
          ) : (
            <div className="h-full flex items-center justify-center text-center px-2" style={{ color: COLORS.textMuted }}>
              改写结果将在这里显示
            </div>
          )}
        </div>
      </div>

      {/* Characters — container always present */}
      <div>
        <div className="text-xs font-medium mb-2" style={{ color: COLORS.textMuted }}>
          角色（Characters）
        </div>
        <div className="flex flex-wrap gap-1.5">
          {hasChars
            ? characters.map((c) => (
                <CharacterPill key={c.name} name={c.name} color={c.color} />
              ))
            : <span className="text-xs" style={{ color: COLORS.textMuted }}>开始规划后，将在这里显示识别到的角色</span>}
        </div>
      </div>

      {/* Character reference images — container always present */}
      <div>
        <div className="text-xs font-medium mb-2" style={{ color: COLORS.textMuted }}>
          角色参考图
        </div>
        <div className="flex gap-2">
          {hasChars
            ? characters.map((c) => (
                <div key={c.name} className="flex flex-col items-center gap-1">
                  <div
                    className="w-14 h-14 rounded-lg flex items-center justify-center text-lg font-bold"
                    style={{
                      background: `${c.color}22`,
                      border: `1px solid ${c.color}55`,
                      color: c.color,
                      backgroundImage: c.image ? `url(${c.image})` : 'none',
                      backgroundSize: 'cover',
                      backgroundPosition: 'center',
                    }}
                  >
                    {!c.image && initials(c.name)}
                  </div>
                  <span className="text-xs" style={{ color: COLORS.textMuted }}>
                    {c.name}
                  </span>
                </div>
              ))
            : <span className="text-xs" style={{ color: COLORS.textMuted }}>角色定妆图尚未生成</span>}
        </div>
      </div>

      {taskId && hasChars && (
        <button
          onClick={onGenerateCharacters}
          disabled={Boolean(actionStatus)}
          className="w-full py-1.5 rounded text-xs font-medium"
          style={{ border: `1px solid ${COLORS.accentPurple}55`, color: COLORS.accentLight, background: 'transparent' }}
        >
          {actionStatus === 'characters' ? '正在生成定妆图…' : '生成人物定妆图'}
        </button>
      )}

      <div className="flex-1" />

      {/* CTA button */}
      <button
        onClick={onStartPlanning}
        disabled={animPhase !== 'rewritten' || !synopsisTyped.trim() || !charactersInput.trim()}
        className="w-full py-2 rounded text-sm font-medium"
        style={{
          background: COLORS.accentPurple,
          color: '#1a1410',
          boxShadow: '0 2px 12px rgba(245,158,11,0.25)',
          cursor: animPhase === 'rewritten' && synopsisTyped.trim() && charactersInput.trim() ? 'pointer' : 'not-allowed',
          opacity: animPhase === 'rewritten' && synopsisTyped.trim() && charactersInput.trim() ? 1 : 0.55,
        }}
      >
        {['empty', 'rewritten'].includes(animPhase) ? '🎬 开始规划' : animPhase === 'done' ? '规划已完成' : '⏳ 后端处理中'}
      </button>
      {error && <div className="text-xs rounded p-2" style={{ color: '#fca5a5', background: '#7f1d1d22', border: '1px solid #ef444455' }}>{error}</div>}
    </div>
  )
}

// ─── SCRIPT REWRITER PANEL ───────────────────────────────────────────────────
function ScriptRewriterPanel({ rawSynopsis, rewrittenSynopsis, animPhase, onRawChange, onRewrite, editable = false }) {
  const [open, setOpen] = useState(false)
  const [section, setSection] = useState('output')
  const textareaRef = useRef(null)
  const isRewriting = animPhase === 'rewriting'
  const isEmpty = animPhase === 'empty'
  const canRewrite = editable && !isRewriting && Boolean(rawSynopsis.trim())

  // Auto-scroll textarea as text types in
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.scrollTop = textareaRef.current.scrollHeight
    }
  }, [rawSynopsis])

  const thinkText = `[Script Rewriter Planning]\n\n→ 分析原始输入的叙事结构平铺问题\n→ 识别核心情节点与角色关系\n→ 规划叙事弧度：铺垫 → 冲突 → 高潮 → 悬念收尾\n→ 为角色补充性格标签与身份辨识度\n→ 增强环境描写与情绪渲染\n→ 检验：未添加原文不存在的情节或角色\n\n后端仅返回结构化结果，不展示模型内部推理。`

  return (
    <div className="flex flex-col gap-1">
      {/* Header with toggle */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium" style={{ color: COLORS.textMuted }}>原始输入 / Raw Input</span>
        <button
          onClick={() => setOpen(!open)}
          className="text-xs px-2 py-0.5 rounded font-mono"
          style={{
            background: open ? `${COLORS.accentPurple}22` : 'transparent',
            color: open ? COLORS.accentLight : COLORS.textMuted,
            border: `1px solid ${open ? `${COLORS.accentPurple}55` : COLORS.cardBorder}`,
          }}
        >
          Script Rewriter {open ? '▼' : '▶'}
        </button>
      </div>

      {/* Raw input textarea */}
      <div style={{ position: 'relative' }}>
        <textarea
          ref={textareaRef}
          value={rawSynopsis}
          readOnly={!editable}
          onChange={(event) => onRawChange?.(event.target.value)}
          rows={4}
          placeholder={isEmpty ? '在这里输入你的故事...' : ''}
          className="w-full text-xs rounded p-2"
          style={{
            background: COLORS.pageBg,
            border: `1px solid ${COLORS.cardBorder}`,
            color: COLORS.textSecondary,
            resize: 'vertical',
            outline: 'none',
          }}
        />
        {/* Typing cursor */}
        {(animPhase === 'typing_raw') && (
          <span style={{
            position: 'absolute',
            bottom: '8px',
            right: '8px',
            width: '2px',
            height: '13px',
            background: COLORS.accentPurple,
            display: 'inline-block',
            animation: 'blink 0.7s step-end infinite',
          }} />
        )}
      </div>

      {/* Rewrite button — shows "改写中..." during rewriting phase */}
      <button
        onClick={onRewrite}
        disabled={!canRewrite}
        className="w-full text-xs py-1.5 rounded font-medium"
        style={{
          background: isRewriting ? `${COLORS.accentPurple}55` : COLORS.accentPurple,
          color: '#1a1410',
          border: 'none',
          boxShadow: isRewriting ? 'none' : '0 2px 10px rgba(245,158,11,0.25)',
          cursor: canRewrite ? 'pointer' : 'not-allowed',
          opacity: canRewrite ? 1 : 0.55,
          transition: 'all 0.3s ease',
        }}
      >
        {isRewriting ? '⏳ 改写中...' : '✨ 改写剧本'}
      </button>

      {/* Rewriter Trace (expandable) */}
      {open && (
        <div className="rounded" style={{ border: `1px solid ${COLORS.accentPurple}44`, background: COLORS.pageBg }}>
          <div className="flex items-center gap-2 px-3 py-2" style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}>
            <span className="text-xs font-semibold font-mono" style={{ color: COLORS.accentLight }}>Script Rewriter Trace</span>
            <div className="flex gap-1">
              {[{ k: 'output', l: 'Output' }, { k: 'prompt', l: 'Prompt' }, { k: 'thinking', l: 'Thinking' }].map(({ k, l }) => (
                <button key={k} onClick={() => setSection(k)} className="text-xs px-2 py-0.5 rounded"
                  style={{
                    background: section === k ? COLORS.accentPurple : 'transparent',
                    color: section === k ? '#1a1410' : COLORS.textSecondary,
                    border: `1px solid ${section === k ? COLORS.accentPurple : COLORS.cardBorder}`,
                  }}>
                  {l}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <button onClick={onRewrite} disabled={!canRewrite} className="text-xs px-2 py-0.5 rounded"
              style={{ background: `${COLORS.accentPurple}22`, color: COLORS.accentLight, border: `1px solid ${COLORS.accentPurple}44` }}>
              ↺ 重新改写
            </button>
          </div>
          <div className="p-3">
            {section === 'prompt' && (
              <textarea defaultValue={`[System Prompt — Script Rewriter]\n\n${SCRIPT_REWRITER_PROMPT}\n\n---\n[User Query]\n\n现在，请改写以下输入，只输出改写后的段落正文：\n\n【原始输入】\n${rawSynopsis}\n\n【改写输出】`}
                rows={8} className="w-full text-xs font-mono rounded p-2"
                style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, resize: 'vertical', outline: 'none', boxSizing: 'border-box', width: '100%' }} />
            )}
            {section === 'thinking' && (
              <pre className="text-xs font-mono rounded p-2 m-0"
                style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, whiteSpace: 'pre-wrap' }}>
                {publicTrace(thinkText)}
              </pre>
            )}
            {section === 'output' && (
              <div className="text-xs rounded p-2 leading-relaxed"
                style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textSecondary }}>
                {rewrittenSynopsis}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── DIRECTOR TRACE PANEL ─────────────────────────────────────────────────────
function DirectorTracePanel({ subScript, onClose, onRegenerate, disabled }) {
  const [section, setSection] = useState('output')

  const thinkText = `[Internal Chain-of-Thought — Director Agent]\n\nCore Narrative Structure:\n→ 识别故事主线弧度：铺垫（点灯日常）→ 转折（引路鹿降临）→ 冲突（泉水干涸）→ 顿悟（光在提灯人手中）\n→ 主要情绪节拍：日常→危机→探索→绝望→顿悟→光明\n\nKey Character Information:\n→ 小满（提灯女孩）—— 主角，行动者\n→ 引路鹿 —— 催化剂角色\n→ 山谷老人 —— 智慧传递者\n\nTemporal Segmentation:\n→ 黄昏：日常点灯场景（铺垫）\n→ 夜间：引路鹿闯入，老人揭示传说\n→ 夜间至黎明：上山寻泉，历经黑暗\n→ 山顶：顿悟时刻，分光救鹿，山谷重亮\n\nSub-Script Division Justification:\n→ 故事总体简短，叙事连贯，适合作为单一 Sub-Script 完整处理\n→ 保留所有角色关系与情节完整性\n\n（完整版将展示模型真实输出的 Chain-of-Thought）`

  return (
    <tr>
      <td colSpan={11} style={{ padding: 0, background: '#0c0a07', borderBottom: `2px solid ${COLORS.accentPurple}33` }}>
        <div style={{ padding: '10px 12px 14px', borderLeft: `3px solid ${COLORS.accentPurple}88` }}>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold font-mono" style={{ color: COLORS.accentPurple }}>Director Agent Trace</span>
            <span className="text-xs font-mono px-1.5 py-0.5 rounded"
              style={{ background: `${COLORS.accentPurple}22`, color: COLORS.accentPurple, border: `1px solid ${COLORS.accentPurple}44` }}>
              {subScript.sub_script}
            </span>
            <div className="flex gap-1">
              {[{ k: 'output', l: 'Output' }, { k: 'prompt', l: 'Prompt' }, { k: 'thinking', l: 'Thinking' }].map(({ k, l }) => (
                <button key={k} onClick={() => setSection(k)} className="text-xs px-2 py-0.5 rounded"
                  style={{
                    background: section === k ? COLORS.accentPurple : 'transparent',
                    color: section === k ? '#1a1410' : COLORS.textSecondary,
                    border: `1px solid ${section === k ? COLORS.accentPurple : COLORS.cardBorder}`,
                  }}>
                  {l}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <button onClick={onRegenerate} disabled={disabled} className="text-xs px-2 py-1 rounded"
              style={{ background: `${COLORS.accentPurple}22`, color: COLORS.accentPurple, border: `1px solid ${COLORS.accentPurple}44` }}>
              ↺ 重新规划
            </button>
            <button onClick={onClose} className="text-xs px-2 py-1 rounded"
              style={{ color: COLORS.textMuted, border: `1px solid ${COLORS.cardBorder}`, background: 'transparent' }}>
              收起 ▲
            </button>
          </div>

          {section === 'prompt' && (
            <textarea defaultValue={`[System Prompt — Director Agent]\n\n${DIRECTOR_PROMPT}\n\n---\n[User Query]\n\nScript Synopsis: "${subScript.sub_script_plot}"\nCharacters: [小满, 引路鹿, 山谷老人]`}
              rows={8} className="w-full text-xs font-mono rounded p-2"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, resize: 'vertical', outline: 'none', boxSizing: 'border-box', width: '100%' }} />
          )}
          {section === 'thinking' && (
            <pre className="text-xs font-mono rounded p-2 m-0"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, whiteSpace: 'pre-wrap' }}>
              {publicTrace(thinkText)}
            </pre>
          )}
          {section === 'output' && (
            <div className="rounded p-3" style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
              <div className="font-semibold text-xs mb-2" style={{ color: COLORS.accentPurple }}>
                Director Agent Output — {subScript.sub_script}
              </div>
              <div className="text-xs leading-relaxed mb-2" style={{ color: COLORS.textSecondary }}>
                {subScript.sub_script_plot}
              </div>
              <div className="flex gap-4 text-xs mt-2" style={{ color: COLORS.textMuted }}>
                <span>场景数：{subScript.scenes?.length ?? 4}</span>
                <span>·</span>
                <span>时间线：黄昏 → 深夜 → 山顶黎明</span>
                <span>·</span>
                <span>分割理由：故事完整弧度，单 Sub-Script 处理</span>
              </div>
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}

// ─── SCENE TRACE PANEL ───────────────────────────────────────────────────────
function SceneTracePanel({ scene, onClose, onRegenerate, disabled }) {
  const [section, setSection] = useState('output')

  const thinkText = `[Internal Chain-of-Thought — Scene Agent — ${scene.scene}]\n\nAnalyze Narrative Structure:\n→ 识别本场景在整体叙事弧中的位置与功能\n→ 确认情感基调转折点与关键戏剧事件\n\nExtract Key Scene Elements:\n→ 场景角色：${scene.shots.flatMap(s => s.characters).filter((v, i, a) => a.indexOf(v) === i).join('、')}\n→ 核心戏剧冲突与情感节拍定位\n\nDefine Scene Boundaries:\n→ 与上一场景的过渡方式（地点/时间/情绪切换）\n→ 本场景自然结束点的判断依据\n\nShot Planning Result:\n→ 规划分镜数量：${scene.shots.length} Shots\n→ 景别分布、角色出场顺序已确认\n\n（完整版将展示模型真实输出的 Chain-of-Thought）`

  const queryText = `[User Query — Scene Agent]\n\nScript Synopsis (Sub-Script 1 excerpt):\n"${scene.shots[0]?.plot?.slice(0, 120) ?? ''}..."\n\nCharacter Relationships: { provided by Director Agent }\n\nTask: Break this scene into a structured shot list with cinematic details.`

  return (
    <tr>
      <td colSpan={11} style={{ padding: 0, background: '#0e0b08', borderBottom: `2px solid #fbbf2433` }}>
        <div style={{ padding: '10px 12px 14px', borderLeft: `3px solid #fbbf24` }}>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold font-mono" style={{ color: '#fbbf24' }}>Scene Agent Trace</span>
            <span className="text-xs font-mono px-1.5 py-0.5 rounded"
              style={{ background: '#fbbf2422', color: '#fbbf24', border: '1px solid #fbbf2444' }}>
              {scene.scene} · {scene.shots.length} Shots planned
            </span>
            <div className="flex gap-1">
              {[{ k: 'output', l: 'Output' }, { k: 'prompt', l: 'Prompt' }, { k: 'thinking', l: 'Thinking' }].map(({ k, l }) => (
                <button key={k} onClick={() => setSection(k)} className="text-xs px-2 py-0.5 rounded"
                  style={{
                    background: section === k ? '#fbbf24' : 'transparent',
                    color: section === k ? '#1a1410' : COLORS.textSecondary,
                    border: `1px solid ${section === k ? '#fbbf24' : COLORS.cardBorder}`,
                  }}>
                  {l}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <button onClick={() => onRegenerate(scene)} disabled={disabled} className="text-xs px-2 py-1 rounded"
              style={{ background: '#fbbf2422', color: '#fbbf24', border: '1px solid #fbbf2444' }}>
              ↺ 重新规划
            </button>
            <button onClick={onClose} className="text-xs px-2 py-1 rounded"
              style={{ color: COLORS.textMuted, border: `1px solid ${COLORS.cardBorder}`, background: 'transparent' }}>
              收起 ▲
            </button>
          </div>

          {section === 'prompt' && (
            <textarea defaultValue={`[System Prompt — Scene Agent]\n\n${SCENE_PROMPT}\n\n---\n${queryText}`}
              rows={8} className="w-full text-xs font-mono rounded p-2"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, resize: 'vertical', outline: 'none', boxSizing: 'border-box', width: '100%' }} />
          )}
          {section === 'thinking' && (
            <pre className="text-xs font-mono rounded p-2 m-0"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, whiteSpace: 'pre-wrap' }}>
              {publicTrace(thinkText)}
            </pre>
          )}
          {section === 'output' && (
            <div className="text-xs rounded p-3"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
              <div className="font-semibold mb-2" style={{ color: '#fbbf24' }}>
                Scene Agent Output — {scene.shots.length} Shots planned for {scene.scene}
              </div>
              <div className="flex flex-col gap-2">
                {scene.shots.map((shot, i) => (
                  <div key={shot.id} className="flex items-start gap-2 rounded p-2"
                    style={{ background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}` }}>
                    <span className="font-mono flex-shrink-0" style={{ color: '#fbbf24' }}>Shot {i + 1}</span>
                    <div>
                      <div style={{ color: COLORS.textSecondary }}>{shot.plot.slice(0, 100)}...</div>
                      <div className="mt-1 flex gap-2" style={{ color: COLORS.textMuted }}>
                        <span>{shot.shotType.split('（')[0]}</span>
                        <span>·</span>
                        <span>{shot.cameraMovement.split('（')[0]}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}

// ─── SHOT AGENT TRACE PANEL (Scene-level) ────────────────────────────────────
function ShotAgentTracePanel({ scene, onClose, charMap, onRegenerate, onSaveShot, disabled }) {
  const [section, setSection] = useState('output')
  const [values, setValues] = useState(() => {
    const m = {}
    scene.shots.forEach(s => {
      m[s.id] = { plot: s.plot, dialogue: s.dialogue, shotType: s.shotType, cameraMovement: s.cameraMovement }
    })
    return m
  })
  const [dirty, setDirty] = useState({})

  const updateField = (shotId, key, val) => {
    setValues(p => ({ ...p, [shotId]: { ...p[shotId], [key]: val } }))
    setDirty(p => ({ ...p, [`${shotId}-${key}`]: true }))
  }

  const involvedChars = [...new Set(scene.shots.flatMap(s => s.characters))].join(', ')
  const promptText = `[System Prompt — Shot Agent]\n\n${SHOT_PROMPT}\n\n---\n[User Query — ${scene.scene}]\n\nGiven the following Scene Details:\n- Scene: ${scene.scene} · ${scene.title}\n- Involving Characters: ${involvedChars}\n- Scene Plot: "${scene.shots[0]?.plot?.slice(0, 120)}..."\n\nTask: Generate a complete shot list for this entire scene (${scene.shots.length} shots expected).`
  const thinkText = `[Internal Chain-of-Thought — Shot Agent — ${scene.scene}]\n\nBreak Down Scene into Key Shots:\n→ 分析本场景整体叙事节奏，识别 ${scene.shots.length} 个关键叙事节点\n→ 确认场景角色：${involvedChars}\n\nShot Planning:\n${scene.shots.map((s, i) => `→ Shot ${i + 1}：${s.shotType.split('（')[0]} · ${s.cameraMovement.split('（')[0]} · 聚焦情绪节拍`).join('\n')}\n\nComposition & Bounding Boxes:\n→ 为每个镜头中的角色规划归一化边界框\n→ 确保边界框不超过 0.5 插值，不交叉重叠\n→ 应用三分法与引导线原则保持视觉连贯\n\n（完整版将展示模型真实输出的 Chain-of-Thought）`

  return (
    <tr>
      <td colSpan={11} style={{ padding: 0, background: COLORS.pageBg, borderBottom: `2px solid ${COLORS.accentPurple}33` }}>
        <div style={{ padding: '10px 12px 14px', borderLeft: `3px solid ${COLORS.accentPurple}` }}>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-semibold font-mono" style={{ color: COLORS.accentLight }}>Shot Agent Trace</span>
            <span className="text-xs font-mono px-1.5 py-0.5 rounded"
              style={{ background: `${COLORS.accentPurple}22`, color: COLORS.accentLight, border: `1px solid ${COLORS.accentPurple}44` }}>
              {scene.scene} · {scene.shots.length} Shots
            </span>
            <div className="flex gap-1">
              {[{ k: 'output', l: 'Output' }, { k: 'prompt', l: 'Prompt' }, { k: 'thinking', l: 'Thinking' }].map(({ k, l }) => (
                <button key={k} onClick={() => setSection(k)} className="text-xs px-2 py-0.5 rounded"
                  style={{
                    background: section === k ? COLORS.accentPurple : 'transparent',
                    color: section === k ? '#1a1410' : COLORS.textSecondary,
                    border: `1px solid ${section === k ? COLORS.accentPurple : COLORS.cardBorder}`,
                  }}>
                  {l}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <button onClick={() => onRegenerate(scene)} disabled={disabled} className="text-xs px-2 py-1 rounded"
              style={{ background: `${COLORS.accentPurple}22`, color: COLORS.accentLight, border: `1px solid ${COLORS.accentPurple}44` }}>
              ↺ 重新规划
            </button>
            <button onClick={onClose} className="text-xs px-2 py-1 rounded"
              style={{ color: COLORS.textMuted, border: `1px solid ${COLORS.cardBorder}`, background: 'transparent' }}>
              收起 ▲
            </button>
          </div>

          {section === 'prompt' && (
            <textarea defaultValue={promptText} rows={8} className="w-full text-xs font-mono rounded p-2"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, resize: 'vertical', outline: 'none', boxSizing: 'border-box', width: '100%' }} />
          )}
          {section === 'thinking' && (
            <pre className="text-xs font-mono rounded p-2 m-0"
              style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted, whiteSpace: 'pre-wrap' }}>
              {publicTrace(thinkText)}
            </pre>
          )}
          {section === 'output' && (
            <div className="flex flex-col gap-3">
              <div className="text-xs font-semibold mb-1" style={{ color: COLORS.accentLight }}>
                Shot Agent Output — {scene.shots.length} Shots planned for {scene.scene}
              </div>
              <div className="flex justify-end">
                <button
                  onClick={async () => {
                    for (const shot of scene.shots) {
                      if (Object.keys(dirty).some((key) => key.startsWith(`${shot.id}-`))) {
                        await onSaveShot(shot, values[shot.id])
                      }
                    }
                    setDirty({})
                  }}
                  disabled={disabled || Object.keys(dirty).length === 0}
                  className="text-xs px-3 py-1 rounded"
                  style={{ background: COLORS.accentPurple, color: '#1a1410', opacity: disabled || Object.keys(dirty).length === 0 ? 0.5 : 1 }}
                >
                  保存全部修改
                </button>
              </div>
              {scene.shots.map((shot) => (
                <div key={shot.id} className="rounded p-3" style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-mono font-semibold" style={{ color: COLORS.accentPurple }}>{shot.number}</span>
                    <div className="flex flex-wrap gap-1">
                      {shot.characters.map(ch => (
                        <CharacterPill key={ch} name={ch} color={charMap[ch] || COLORS.textMuted} />
                      ))}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { key: 'plot', label: '画面描述 / Plot' },
                      { key: 'dialogue', label: '旁白 / Dialogue' },
                      { key: 'shotType', label: 'Shot Type' },
                      { key: 'cameraMovement', label: 'Camera Movement' },
                    ].map(({ key, label }) => {
                      const isDirty = dirty[`${shot.id}-${key}`]
                      return (
                        <div key={key}>
                          <div className="flex items-center gap-1 text-xs mb-1" style={{ color: COLORS.textMuted }}>
                            {label}
                            {isDirty && <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: COLORS.accentPurple }} title="已修改" />}
                          </div>
                          <textarea value={values[shot.id][key]} onChange={(e) => updateField(shot.id, key, e.target.value)} rows={2}
                            className="w-full text-xs rounded p-2"
                            style={{
                              background: COLORS.pageBg,
                              border: `1px solid ${isDirty ? COLORS.accentPurple + 'aa' : COLORS.cardBorder}`,
                              color: COLORS.textSecondary,
                              resize: 'vertical',
                              outline: 'none',
                              transition: 'border-color 0.15s',
                            }} />
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}

function SceneEditorView({ scenes, onSave, disabled }) {
  const [values, setValues] = useState({})
  const sceneSignature = scenes.map((scene) => `${scene.id}:${scene.plot}:${scene.description}:${scene.tone}`).join('|')

  useEffect(() => {
    setValues(Object.fromEntries(scenes.map((scene) => [scene.id, {
      plot: scene.plot || '',
      description: scene.description || scene.title || '',
      tone: scene.tone || '',
    }])))
  }, [sceneSignature])

  return (
    <div className="p-4 grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
      {scenes.map((scene) => {
        const value = values[scene.id] || { plot: '', description: '', tone: '' }
        return (
          <div key={scene.id} className="rounded p-3" style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
            <div className="text-xs font-semibold mb-2" style={{ color: COLORS.accentLight }}>{scene.scene}</div>
            {[['plot', '剧情 / Plot'], ['description', '场景描述'], ['tone', '情绪基调']].map(([key, label]) => (
              <label key={key} className="block text-xs mb-2" style={{ color: COLORS.textMuted }}>
                {label}
                <textarea
                  value={value[key]}
                  onChange={(event) => setValues((current) => ({ ...current, [scene.id]: { ...value, [key]: event.target.value } }))}
                  rows={key === 'tone' ? 2 : 3}
                  className="w-full rounded p-2 mt-1"
                  style={{ background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textSecondary, resize: 'vertical', outline: 'none' }}
                />
              </label>
            ))}
            <button onClick={() => onSave(scene, value)} disabled={disabled} className="w-full rounded py-1.5 text-xs font-medium" style={{ background: COLORS.accentPurple, color: '#1a1410', opacity: disabled ? 0.5 : 1 }}>
              保存 Scene
            </button>
          </div>
        )
      })}
    </div>
  )
}

function PipelineView({ scenes, onSelectShot }) {
  return (
    <div className="p-4 flex flex-col gap-3" style={{ minWidth: '720px' }}>
      {scenes.map((scene) => (
        <div key={scene.id} className="flex items-stretch gap-3">
          <div className="rounded p-3 flex-shrink-0" style={{ width: '180px', background: COLORS.cardBg, border: `1px solid ${COLORS.cardBorder}` }}>
            <div className="text-xs font-semibold" style={{ color: COLORS.accentLight }}>{scene.scene}</div>
            <div className="text-xs mt-1" style={{ color: COLORS.textSecondary }}>{scene.title}</div>
          </div>
          <div className="flex gap-2 overflow-x-auto flex-1">
            {scene.shots.map((shot) => (
              <button
                key={shot.id}
                onClick={() => onSelectShot(shot.id)}
                className="text-left rounded p-2 flex-shrink-0"
                style={{ width: '150px', background: COLORS.cardBg, border: `1px solid ${shot.kfStatus === 'done' ? COLORS.statusGreen + '66' : COLORS.cardBorder}` }}
              >
                <div className="text-xs font-mono" style={{ color: COLORS.accentLight }}>{shot.number}</div>
                <div className="text-xs mt-1" style={{ color: COLORS.textSecondary, height: '34px', overflow: 'hidden' }}>{shot.plot}</div>
                <div className="flex justify-between mt-2 text-xs" style={{ color: COLORS.textMuted }}>
                  <span>KF: {shot.kfStatus}</span><span>视频: {shot.videoStatus}</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── STORYBOARD ──────────────────────────────────────────────────────────────
function Storyboard({ selectedShotId, onSelectShot, animPhase, visibleSceneCount, taskId, actionStatus, onRestartPlanning, onRegenerateScene, onRegenerateShots, onSaveShot, onSaveScene, onGenerateArtifact }) {
  const allScenes = currentData.storyboard.flatMap((item) => item.scenes || [])
  const scenes = ['scenes', 'done'].includes(animPhase)
    ? allScenes.slice(0, visibleSceneCount)
    : []
  const [expanded, setExpanded] = useState(() => {
    const m = {}
    allScenes.forEach((s) => (m[s.id] = true))
    return m
  })
  const [activeTab, setActiveTab] = useState('shot')
  const [viewMode, setViewMode] = useState('table')
  const [expandedSceneTraces, setExpandedSceneTraces] = useState({})
  const [expandedShotAgentTraces, setExpandedShotAgentTraces] = useState({})
  const [directorTraceOpen, setDirectorTraceOpen] = useState(false)

  useEffect(() => {
    setExpanded((previous) => {
      const next = { ...previous }
      allScenes.forEach((scene) => { next[scene.id] = next[scene.id] ?? true })
      return next
    })
  }, [allScenes.length])

  const toggleScene = (id) => setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  const toggleSceneTrace = (id) => setExpandedSceneTraces((prev) => ({ ...prev, [id]: !prev[id] }))
  const toggleShotAgentTrace = (id) => setExpandedShotAgentTraces((prev) => ({ ...prev, [id]: !prev[id] }))

  const charMap = {}
  currentData.story.characters.forEach((c) => (charMap[c.name] = c.color))

  // Framework is ALWAYS on screen; only the data inside it streams in.
  const hasScenes = scenes.length > 0
  const isPlanning = ['director', 'scenes'].includes(animPhase) && !hasScenes
  // Status line shown in the Sub-Script header row while scenes have not arrived.
  const subScriptStatus = {
    empty: '等待剧本改写与规划',
    typing_raw: '等待剧本输入…',
    rewriting: '✨ Script Rewriter 改写剧本中…',
    rewritten: '剧本已改写，等待开始规划',
    typing_synopsis: '✨ 改写完成，准备规划…',
    director: '🎬 Director Agent 正在分析剧本、规划场景…',
  }[animPhase]

  return (
    <div
      className="flex flex-col flex-1 overflow-hidden"
      style={{ background: COLORS.pageBg }}
    >
      {/* Sub-header */}
      <div
        className="flex items-center justify-between px-4 py-2 flex-shrink-0"
        style={{
          background: COLORS.cardBg,
          borderBottom: `1px solid ${COLORS.cardBorder}`,
        }}
      >
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold" style={{ color: COLORS.textPrimary }}>
            Storyboard
          </span>
          <span className="text-xs" style={{ color: COLORS.textMuted }}>
            Sub-script → Scene → Shot
          </span>
          {/* Legend */}
          <div className="flex items-center gap-2 ml-2">
            {[
              { color: COLORS.statusGreen, label: '已完成' },
              { color: '#3b82f6', label: '进行中' },
              { color: COLORS.textMuted, label: '未开始' },
              { color: '#ef4444', label: '失败' },
            ].map((l) => (
              <span key={l.label} className="flex items-center gap-1 text-xs" style={{ color: COLORS.textMuted }}>
                <span className="w-2 h-2 rounded-full inline-block" style={{ background: l.color }} />
                {l.label}
              </span>
            ))}
          </div>
        </div>
        {/* Right controls: view mode + edit tabs */}
        <div className="flex items-center gap-2">
          {/* View mode */}
          <div className="flex gap-1">
            {[{ key: 'table', label: 'Table' }, { key: 'pipeline', label: 'Pipeline' }].map((t) => (
              <button key={t.key}
                onClick={() => setViewMode(t.key)}
                className="text-xs px-2 py-1 rounded"
                style={{
                  background: viewMode === t.key ? `${COLORS.accentPurple}33` : 'transparent',
                  color: viewMode === t.key ? COLORS.accentLight : COLORS.textMuted,
                  border: `1px solid ${viewMode === t.key ? `${COLORS.accentPurple}55` : COLORS.cardBorder}`,
                }}>
                {t.label}
              </button>
            ))}
          </div>
          <div style={{ width: '1px', height: '16px', background: COLORS.cardBorder }} />
          {/* Edit tabs */}
          <div className="flex gap-1">
            {[{ key: 'shot', label: 'Shot 编辑' }, { key: 'scene', label: 'Scene 编辑' }].map((t) => (
              <button key={t.key}
                onClick={() => setActiveTab(t.key)}
                className="text-xs px-3 py-1 rounded"
                style={{
                  background: activeTab === t.key ? COLORS.accentPurple : 'transparent',
                  color: activeTab === t.key ? '#fff' : COLORS.textSecondary,
                  border: `1px solid ${activeTab === t.key ? COLORS.accentPurple : COLORS.cardBorder}`,
                }}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {activeTab === 'scene' ? (
          <SceneEditorView scenes={scenes} onSave={onSaveScene} disabled={!taskId || Boolean(actionStatus)} />
        ) : viewMode === 'pipeline' ? (
          <PipelineView scenes={scenes} onSelectShot={onSelectShot} />
        ) : (
        <table className="w-full text-xs border-collapse" style={{ minWidth: '900px' }}>
          <thead>
            <tr style={{ background: COLORS.pageBg, borderBottom: `1px solid ${COLORS.cardBorder}` }}>
              {[
                'Scene', 'Shot', '关键帧', '画面描述', '角色',
                '镜头类型', '运镜', '旁白', 'KF状态', '视频状态', '操作',
              ].map((h) => (
                <th
                  key={h}
                  className="px-2 py-2 text-left font-medium whitespace-nowrap"
                  style={{ color: COLORS.textMuted }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {/* Sub-script header row */}
            <tr style={{ background: '#16100c', borderBottom: `1px solid ${COLORS.cardBorder}` }}>
              <td colSpan={11} className="px-3 py-1.5">
                <span className="flex items-center gap-2">
                  <span className="font-semibold text-xs" style={{ color: hasScenes ? COLORS.accentLight : COLORS.textMuted }}>
                    Sub-Script 1 &nbsp;·&nbsp; {hasScenes ? `${allScenes.length} Scenes` : '— Scenes'}
                  </span>
                  {hasScenes ? (
                    <button
                      onClick={() => setDirectorTraceOpen(!directorTraceOpen)}
                      className="text-xs px-2 py-0.5 rounded font-mono"
                      style={{
                        background: directorTraceOpen ? `${COLORS.accentPurple}22` : 'transparent',
                        color: directorTraceOpen ? COLORS.accentPurple : COLORS.textMuted,
                        border: `1px solid ${directorTraceOpen ? `${COLORS.accentPurple}55` : COLORS.cardBorder}`,
                      }}
                    >
                      Director Agent {directorTraceOpen ? '▼' : '▶'}
                    </button>
                  ) : (
                    <span
                      className="text-xs flex items-center gap-1.5"
                      style={{ color: isPlanning ? COLORS.accentLight : COLORS.textMuted }}
                    >
                      {isPlanning && (
                        <span className="flex gap-0.5">
                          {[0, 1, 2].map((i) => (
                            <span key={i} style={{
                              display: 'inline-block', width: '4px', height: '4px', borderRadius: '50%',
                              background: COLORS.accentPurple,
                              animation: `dotPulse 1.2s ease-in-out ${i * 0.2}s infinite`,
                            }} />
                          ))}
                        </span>
                      )}
                      {subScriptStatus}
                    </span>
                  )}
                </span>
              </td>
            </tr>
            {directorTraceOpen && hasScenes && (
              <DirectorTracePanel
                subScript={currentData.storyboard[0] || { sub_script: 'Sub-Script', sub_script_plot: '' }}
                onClose={() => setDirectorTraceOpen(false)}
                onRegenerate={onRestartPlanning}
                disabled={!taskId || Boolean(actionStatus)}
              />
            )}

            {/* Skeleton rows are reserved for active planning. */}
            {!hasScenes && isPlanning && [0, 1, 2, 3].map((i) => <SkeletonShotRow key={`sk-${i}`} />)}
            {!hasScenes && !isPlanning && EMPTY_STORYBOARD_HINTS.map((_, index) => <EmptyStoryboardRow key={`empty-${index}`} index={index} />)}

            {scenes.map((scene, sceneIdx) => (
              <Fragment key={`scene-frag-${scene.id}`}>
                {/* Scene collapsible header */}
                <tr
                  key={`scene-${scene.id}`}
                  style={{
                    background: '#100d09',
                    borderBottom: `1px solid ${COLORS.cardBorder}`,
                    cursor: 'pointer',
                    animation: animPhase === 'scenes' ? `fadeSlideIn 0.35s ease forwards` : 'none',
                  }}
                  onClick={() => toggleScene(scene.id)}
                >
                  <td colSpan={11} className="px-3 py-1.5">
                    <span className="flex items-center gap-2">
                      <span style={{ color: COLORS.accentLight }} className="font-semibold">
                        {expanded[scene.id] ? '▼' : '▶'}
                      </span>
                      <span style={{ color: COLORS.accentLight }} className="font-semibold">
                        {scene.scene.replace('Scene ', '')}.{scene.title}
                      </span>
                      <span style={{ color: COLORS.textMuted }}>({scene.shots.length} Shots)</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleSceneTrace(scene.id) }}
                        title="展开 Scene Agent Trace"
                        className="text-xs px-2 py-0.5 rounded font-mono"
                        style={{
                          marginLeft: '8px',
                          background: expandedSceneTraces[scene.id] ? '#fbbf2422' : 'transparent',
                          color: expandedSceneTraces[scene.id] ? '#fbbf24' : COLORS.textMuted,
                          border: `1px solid ${expandedSceneTraces[scene.id] ? '#fbbf2455' : COLORS.cardBorder}`,
                        }}
                      >
                        Scene Agent {expandedSceneTraces[scene.id] ? '▼' : '▶'}
                      </button>
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleShotAgentTrace(scene.id) }}
                        title="展开 Shot Agent Trace"
                        className="text-xs px-2 py-0.5 rounded font-mono"
                        style={{
                          background: expandedShotAgentTraces[scene.id] ? `${COLORS.accentPurple}22` : 'transparent',
                          color: expandedShotAgentTraces[scene.id] ? COLORS.accentLight : COLORS.textMuted,
                          border: `1px solid ${expandedShotAgentTraces[scene.id] ? `${COLORS.accentPurple}55` : COLORS.cardBorder}`,
                        }}
                      >
                        Shot Agent {expandedShotAgentTraces[scene.id] ? '▼' : '▶'}
                      </button>
                    </span>
                  </td>
                </tr>
                {expandedSceneTraces[scene.id] && (
                  <SceneTracePanel
                    key={`scene-trace-${scene.id}`}
                    scene={scene}
                    onClose={() => toggleSceneTrace(scene.id)}
                    onRegenerate={onRegenerateScene}
                    disabled={!taskId || Boolean(actionStatus)}
                  />
                )}
                {expandedShotAgentTraces[scene.id] && (
                  <ShotAgentTracePanel
                    key={`shot-agent-trace-${scene.id}`}
                    scene={scene}
                    onClose={() => toggleShotAgentTrace(scene.id)}
                    charMap={charMap}
                    onRegenerate={onRegenerateShots}
                    onSaveShot={onSaveShot}
                    disabled={!taskId || Boolean(actionStatus)}
                  />
                )}

                {/* Shot rows */}
                {expanded[scene.id] &&
                  scene.shots.map((shot) => {
                    const isSelected = selectedShotId === shot.id
                    return (
                      <tr
                        key={shot.id}
                        onClick={() => onSelectShot(shot.id)}
                        className="shot-row"
                        style={{
                          background: isSelected ? 'rgba(245,158,11,0.10)' : COLORS.cardBg,
                          borderBottom: `1px solid ${COLORS.cardBorder}`,
                          cursor: 'pointer',
                          outline: isSelected ? `1px solid ${COLORS.accentPurple}44` : 'none',
                        }}
                      >
                        {/* Scene */}
                        <td className="px-2 py-2" style={{ color: COLORS.textMuted, whiteSpace: 'nowrap' }}>
                          {shot.scene}
                        </td>

                        {/* Shot number */}
                        <td className="px-2 py-2 font-mono" style={{ color: COLORS.textSecondary, whiteSpace: 'nowrap' }}>
                          {shot.number}
                        </td>

                        {/* Keyframe thumbnail */}
                        <td className="px-2 py-2">
                          <img
                            src={shot.keyframe}
                            alt={shot.number}
                            style={{ width: '64px', height: '40px', objectFit: 'cover', borderRadius: '4px', border: `1px solid ${COLORS.cardBorder}` }}
                          />
                        </td>

                        {/* Plot description */}
                        <td className="px-2 py-2" style={{ maxWidth: '180px' }}>
                          <div
                            style={{
                              color: COLORS.textSecondary,
                              overflow: 'hidden',
                              display: '-webkit-box',
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: 'vertical',
                            }}
                          >
                            {shot.plot}
                          </div>
                        </td>

                        {/* Characters */}
                        <td className="px-2 py-2">
                          <div className="flex flex-wrap gap-1">
                            {shot.characters.map((ch) => (
                              <CharacterPill key={ch} name={ch} color={charMap[ch] || COLORS.textMuted} />
                            ))}
                          </div>
                        </td>

                        {/* Shot type */}
                        <td className="px-2 py-2" style={{ maxWidth: '100px' }}>
                          <div
                            style={{
                              color: COLORS.textSecondary,
                              overflow: 'hidden',
                              whiteSpace: 'nowrap',
                              textOverflow: 'ellipsis',
                              maxWidth: '100px',
                            }}
                            title={shot.shotType}
                          >
                            {shot.shotType.split('（')[0]}
                          </div>
                        </td>

                        {/* Camera movement */}
                        <td className="px-2 py-2" style={{ maxWidth: '100px' }}>
                          <div
                            style={{
                              color: COLORS.textSecondary,
                              overflow: 'hidden',
                              whiteSpace: 'nowrap',
                              textOverflow: 'ellipsis',
                              maxWidth: '100px',
                            }}
                            title={shot.cameraMovement}
                          >
                            {shot.cameraMovement.split('（')[0]}
                          </div>
                        </td>

                        {/* Dialogue */}
                        <td className="px-2 py-2" style={{ maxWidth: '120px' }}>
                          <div
                            style={{
                              color: COLORS.textMuted,
                              overflow: 'hidden',
                              display: '-webkit-box',
                              WebkitLineClamp: 2,
                              WebkitBoxOrient: 'vertical',
                            }}
                          >
                            {shot.dialogue}
                          </div>
                        </td>

                        {/* KF status */}
                        <td className="px-2 py-2">
                          <StatusBadge status={shot.kfStatus} />
                        </td>

                        {/* Video status */}
                        <td className="px-2 py-2">
                          <StatusBadge status={shot.videoStatus} />
                        </td>

                        {/* Actions */}
                        <td className="px-2 py-2">
                          <div className="flex items-center gap-1">
                            <button
                              onClick={(e) => { e.stopPropagation(); onSelectShot(shot.id) }}
                              className="text-sm hover:opacity-70 transition-opacity"
                              title="编辑 Shot"
                            >✏️</button>
                            <button
                              onClick={async (e) => {
                                e.stopPropagation()
                                await navigator.clipboard.writeText(JSON.stringify(shot, null, 2))
                              }}
                              className="text-sm hover:opacity-70 transition-opacity"
                              title="复制 Shot 数据"
                            >📋</button>
                            <button
                              onClick={(e) => { e.stopPropagation(); onGenerateArtifact('audio', shot) }}
                              disabled={!taskId || Boolean(actionStatus)}
                              className="text-sm hover:opacity-70 transition-opacity"
                              title="生成音频"
                            >🎵</button>
                          </div>
                        </td>
                      </tr>
                    )
                  })
                }
              </Fragment>
            ))}
          </tbody>
        </table>
        )}
      </div>

    </div>
  )
}

// ─── RIGHT PANEL (Shot Detail) ────────────────────────────────────────────────
function RightPanel({ shotId, taskId, onGenerateArtifact, onSaveShot, onRegenerateShots, actionStatus }) {
  const [previewTab, setPreviewTab] = useState('kf')
  const [draft, setDraft] = useState({ plot: '', dialogue: '', shotType: '', cameraMovement: '' })

  // Flatten all shots
  const allShots = currentData.storyboard.flatMap((item) => item.scenes || []).flatMap((s) => s.shots)
  const shot = allShots.find((s) => s.id === shotId)
  const hasShot = !!shot

  useEffect(() => {
    setDraft({
      plot: shot?.plot || '',
      dialogue: shot?.dialogue || '',
      shotType: shot?.shotType || '',
      cameraMovement: shot?.cameraMovement || '',
    })
  }, [shotId, shot?.plot, shot?.dialogue, shot?.shotType, shot?.cameraMovement])

  const charMap = {}
  currentData.story.characters.forEach((c) => (charMap[c.name] = c.color))

  const fieldStyle = {
    background: COLORS.pageBg,
    border: `1px solid ${COLORS.cardBorder}`,
    color: COLORS.textSecondary,
  }

  return (
    <div
      className="flex flex-col overflow-y-auto flex-shrink-0"
      style={{
        width: '320px',
        background: COLORS.cardBg,
        borderLeft: `1px solid ${COLORS.cardBorder}`,
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 flex-shrink-0"
        style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}
      >
        <span className="text-sm font-semibold" style={{ color: COLORS.textPrimary }}>
          Shot 编辑
        </span>
        <span className="text-xs" style={{ color: hasShot ? COLORS.accentLight : COLORS.textMuted }}>
          当前选中 Shot: {hasShot ? shot.number : '—'}
        </span>
      </div>

      <div className="flex flex-col gap-3 p-3">
        {!hasShot && (
          <div className="text-xs leading-relaxed rounded p-2" style={{ color: COLORS.textMuted, background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}` }}>
            选择一个镜头后，可查看和编辑画面、角色、景别与运镜。
          </div>
        )}
        {/* Plot field */}
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
            画面 / Plot Description
          </div>
          {hasShot ? <textarea
            value={draft.plot}
            onChange={(event) => setDraft((value) => ({ ...value, plot: event.target.value }))}
            maxLength={500}
            rows={5}
            className="w-full rounded p-2 text-xs leading-relaxed"
            style={{ ...fieldStyle, minHeight: '80px', resize: 'vertical', outline: 'none' }}
          /> : <div className="rounded p-2 text-xs leading-relaxed flex items-center" style={{ ...fieldStyle, minHeight: '80px', color: COLORS.textMuted }}>等待选择镜头</div>}
          <div className="text-right mt-0.5 text-xs" style={{ color: COLORS.textMuted }}>
            {hasShot ? draft.plot.length : 0} / 500
          </div>
        </div>

        {/* Characters */}
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
            Involving Characters
          </div>
          <div className="flex flex-wrap gap-1">
            {hasShot
              ? shot.characters.map((ch) => (
                  <CharacterPill key={ch} name={ch} color={charMap[ch] || COLORS.textMuted} />
                ))
              : <span className="text-xs" style={{ color: COLORS.textMuted }}>等待选择镜头</span>}
          </div>
        </div>

        {/* Shot type */}
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
            Shot Type
          </div>
          {hasShot ? <input value={draft.shotType} onChange={(event) => setDraft((value) => ({ ...value, shotType: event.target.value }))} className="w-full rounded px-2 py-1.5 text-xs" style={{ ...fieldStyle, outline: 'none' }} /> : <div className="rounded px-2 py-1.5 text-xs" style={{ ...fieldStyle, color: COLORS.textMuted }}>等待选择镜头</div>}
        </div>

        {/* Camera movement */}
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
            Camera Movement
          </div>
          {hasShot ? <input value={draft.cameraMovement} onChange={(event) => setDraft((value) => ({ ...value, cameraMovement: event.target.value }))} className="w-full rounded px-2 py-1.5 text-xs" style={{ ...fieldStyle, outline: 'none' }} /> : <div className="rounded px-2 py-1.5 text-xs" style={{ ...fieldStyle, color: COLORS.textMuted }}>等待选择镜头</div>}
        </div>

        {/* Dialogue */}
        <div>
          <div className="text-xs font-medium mb-1" style={{ color: COLORS.textMuted }}>
            旁白 / Dialogue
          </div>
          {hasShot ? <textarea value={draft.dialogue} onChange={(event) => setDraft((value) => ({ ...value, dialogue: event.target.value }))} rows={3} className="w-full rounded px-2 py-1.5 text-xs" style={{ ...fieldStyle, resize: 'vertical', outline: 'none' }} /> : <div className="rounded px-2 py-1.5 text-xs" style={{ ...fieldStyle, color: COLORS.textMuted }}>等待选择镜头</div>}
        </div>

        {/* Preview tabs */}
        <div>
          <div className="flex gap-1 mb-2">
            {[
              { key: 'kf', label: '关键帧' },
              { key: 'video', label: '视频' },
              { key: 'audio', label: '音频' },
            ].map((t) => (
              <button
                key={t.key}
                onClick={() => setPreviewTab(t.key)}
                className="text-xs px-2 py-1 rounded"
                style={{
                  background: previewTab === t.key ? COLORS.accentPurple : 'transparent',
                  color: previewTab === t.key ? '#fff' : COLORS.textSecondary,
                  border: `1px solid ${previewTab === t.key ? COLORS.accentPurple : COLORS.cardBorder}`,
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {previewTab === 'kf' && (
            hasShot && shot.keyframe ? (
              <img
                src={shot.keyframe}
                alt="keyframe"
                className="w-full rounded"
                style={{ height: '180px', objectFit: 'cover', border: `1px solid ${COLORS.cardBorder}` }}
              />
            ) : (
              <div className="w-full rounded flex items-center justify-center text-xs text-center px-4" style={{ height: '180px', background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted }}>
                {hasShot ? '该镜头尚未生成关键帧' : '选择镜头后在这里查看关键帧'}
              </div>
            )
          )}
          {previewTab === 'video' && (
            hasShot && shot.video ? (
              <video
                src={shot.video}
                controls
                className="w-full rounded"
                style={{ height: '180px', background: '#000', border: `1px solid ${COLORS.cardBorder}` }}
              />
            ) : (
              <div className="w-full rounded flex items-center justify-center text-xs text-center px-4" style={{ height: '180px', background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textMuted }}>
                {hasShot ? '该镜头尚未生成视频' : '选择镜头后在这里查看视频'}
              </div>
            )
          )}
          {previewTab === 'audio' && (
            <div
              className="w-full rounded flex items-center justify-center text-xs"
              style={{
                height: '60px',
                background: COLORS.pageBg,
                border: `1px solid ${COLORS.cardBorder}`,
                color: COLORS.textMuted,
              }}
            >
              {hasShot ? '音频生成后将在这里显示状态' : '选择镜头后在这里查看音频状态'}
            </div>
          )}
        </div>

        {/* Action buttons row 1 */}
        <div className="flex gap-2">
          <button
            onClick={() => onSaveShot(shot, draft)}
            disabled={!taskId || !hasShot || Boolean(actionStatus)}
            className="flex-1 text-xs py-1.5 rounded"
            style={{
              background: COLORS.accentPurple,
              color: '#1a1410',
              border: 'none',
              boxShadow: '0 2px 10px rgba(245,158,11,0.25)',
            }}
          >
            保存 Shot
          </button>
          <button
            onClick={() => onRegenerateShots({ subScriptName: shot.subScriptName, scene: shot.scene })}
            disabled={!taskId || !hasShot || Boolean(actionStatus)}
            className="flex-1 text-xs py-1.5 rounded"
            style={{
              background: 'transparent',
              color: COLORS.textSecondary,
              border: `1px solid ${COLORS.cardBorder}`,
            }}
          >
            重新规划
          </button>
        </div>

        {/* Action buttons row 2 */}
        <div className="flex gap-2">
          {[
            { label: '生成关键帧', type: 'keyframe' },
            { label: '生成视频', type: 'video' },
            { label: '生成音频', type: 'audio' },
          ].map(({ label, type }) => (
            <button
              key={label}
              onClick={() => onGenerateArtifact(type, shot)}
              disabled={!taskId || !hasShot || Boolean(actionStatus)}
              className="flex-1 text-xs py-1.5 rounded"
              style={{
                background: 'transparent',
                color: !taskId || !hasShot ? COLORS.textMuted : COLORS.accentLight,
                border: `1px solid ${COLORS.accentPurple}55`,
                cursor: !taskId || !hasShot || actionStatus ? 'not-allowed' : 'pointer',
              }}
            >
              {actionStatus === type ? '生成中…' : label}
            </button>
          ))}
        </div>
        {actionStatus && <div className="text-xs" style={{ color: COLORS.textMuted }}>生成请求正在后端执行，请保持页面开启。</div>}
      </div>
    </div>
  )
}

// ─── BOTTOM: LOG CARD ─────────────────────────────────────────────────────────
function LogCard({ animPhase }) {
  const logRef = useRef(null)
  const logs = currentData.logs
  const [visibleCount, setVisibleCount] = useState(0)

  // Stream log lines in one by one once planning starts; show all when done.
  useEffect(() => {
    if (animPhase === 'empty' || animPhase === 'typing_raw') {
      setVisibleCount(0)
      return
    }
    if (animPhase === 'done') {
      setVisibleCount(logs.length)
      return
    }
    // Reveal progressively during rewriting → scenes.
    const timer = setInterval(() => {
      setVisibleCount((c) => {
        if (c >= logs.length - 1) { clearInterval(timer); return c }
        return c + 1
      })
    }, 280)
    return () => clearInterval(timer)
  }, [animPhase, logs.length])

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [visibleCount])

  const agentColor = (agent) => {
    if (agent === 'Director Agent') return COLORS.accentLight
    if (agent === 'Scene Agent') return '#3b82f6'
    if (agent === 'Shot Agent') return COLORS.statusGreen
    return COLORS.textSecondary
  }

  const shown = logs.slice(0, visibleCount)

  return (
    <div
      className="flex flex-col rounded-lg overflow-hidden"
      style={{
        background: COLORS.cardBg,
        border: `1px solid ${COLORS.cardBorder}`,
        minWidth: 0,
        flex: '2 1 0',
      }}
    >
      <div
        className="px-3 py-2 text-xs font-semibold flex-shrink-0 flex items-center gap-2"
        style={{
          color: COLORS.textPrimary,
          borderBottom: `1px solid ${COLORS.cardBorder}`,
        }}
      >
        执行日志
        {animPhase !== 'empty' && animPhase !== 'done' && (
          <span className="flex gap-0.5">
            {[0, 1, 2].map((i) => (
              <span key={i} style={{
                display: 'inline-block', width: '4px', height: '4px', borderRadius: '50%',
                background: COLORS.accentPurple,
                animation: `dotPulse 1.2s ease-in-out ${i * 0.2}s infinite`,
              }} />
            ))}
          </span>
        )}
      </div>
      <div
        ref={logRef}
        className="flex-1 overflow-y-auto p-2 space-y-1"
        style={{ maxHeight: '140px' }}
      >
        {shown.length === 0 ? (
          <div className="text-xs h-full flex items-center justify-center" style={{ color: COLORS.textMuted }}>
            任务开始后，规划与生成进度将在这里更新
          </div>
        ) : (
          shown.map((log, i) => (
            <div key={i} className="flex items-start gap-2 text-xs" style={{ animation: 'fadeSlideIn 0.3s ease' }}>
              <span className="font-mono flex-shrink-0" style={{ color: COLORS.textMuted }}>
                {log.time}
              </span>
              <span className="flex-shrink-0 font-medium" style={{ color: agentColor(log.agent) }}>
                [{log.agent}]
              </span>
              <span style={{ color: COLORS.textSecondary }}>{log.msg}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ─── BOTTOM: TASK INFO CARD ───────────────────────────────────────────────────
function TaskInfoCard({ animPhase, progress }) {
  const { task } = currentData
  const isDone = animPhase === 'done'
  const isEmpty = !['director', 'scenes', 'done'].includes(animPhase)
  const statusText = isDone ? 'done' : isEmpty ? '尚未创建任务' : '进行中'
  return (
    <div
      className="flex flex-col rounded-lg overflow-hidden"
      style={{
        background: COLORS.cardBg,
        border: `1px solid ${COLORS.cardBorder}`,
        flex: '1 1 0',
        minWidth: 0,
      }}
    >
      <div
        className="px-3 py-2 text-xs font-semibold flex-shrink-0"
        style={{
          color: COLORS.textPrimary,
          borderBottom: `1px solid ${COLORS.cardBorder}`,
        }}
      >
        任务信息
      </div>
      <div className="p-3 flex flex-col gap-2 text-xs">
        <div className="flex items-center justify-between">
          <span style={{ color: COLORS.textMuted }}>状态</span>
          <StatusBadge status={statusText} />
        </div>
        {/* Progress bar */}
        <div>
          <div className="flex justify-between mb-1">
            <span style={{ color: COLORS.textMuted }}>进度</span>
            <span style={{ color: isDone ? COLORS.statusGreen : COLORS.accentLight }}>{progress}%</span>
          </div>
          <div className="rounded-full overflow-hidden" style={{ height: '6px', background: COLORS.cardBorder }}>
            <div
              className="h-full rounded-full"
              style={{
                width: `${progress}%`,
                background: isDone ? COLORS.statusGreen : COLORS.accentPurple,
                transition: 'width 0.4s ease',
              }}
            />
          </div>
        </div>
        <div className="flex justify-between">
          <span style={{ color: COLORS.textMuted }}>开始时间</span>
          <span className="font-mono" style={{ color: COLORS.textSecondary }}>
            {isEmpty ? '—' : task.created_at}
          </span>
        </div>
        <div className="flex justify-between">
          <span style={{ color: COLORS.textMuted }}>更新时间</span>
          <span className="font-mono" style={{ color: COLORS.textSecondary }}>
            {isDone ? task.finished_at : '—'}
          </span>
        </div>
        <div className="flex justify-between">
          <span style={{ color: COLORS.textMuted }}>Mode</span>
          <span style={{ color: COLORS.textSecondary }}>
            {task.mode} / {task.resolution} / {task.shot_duration}
          </span>
        </div>
      </div>
    </div>
  )
}

// ─── BOTTOM: COST CARD ───────────────────────────────────────────────────────
function CostCard({ progress = 100, animPhase }) {
  const { cost } = currentData
  const hasTask = ['director', 'scenes', 'done'].includes(animPhase)
  const frac = Math.max(0, Math.min(1, progress / 100))
  const scale = (n) => Math.round(n * frac)
  return (
    <div
      className="flex flex-col rounded-lg overflow-hidden"
      style={{
        background: COLORS.cardBg,
        border: `1px solid ${COLORS.cardBorder}`,
        flex: '1 1 0',
        minWidth: 0,
      }}
    >
      <div
        className="px-3 py-2 text-xs font-semibold flex-shrink-0"
        style={{
          color: COLORS.textPrimary,
          borderBottom: `1px solid ${COLORS.cardBorder}`,
        }}
      >
        成本信息
      </div>
      <div className="p-3 flex flex-col gap-2 text-xs">
        {!hasTask && <div style={{ color: COLORS.textMuted }}>模型调用后显示 Token 用量和预估费用</div>}
        {/* Token metrics */}
        <div className="grid grid-cols-3 gap-1">
          {[
            { label: '输入 Tokens', value: scale(cost.input_tokens).toLocaleString() },
            { label: '输出 Tokens', value: scale(cost.output_tokens).toLocaleString() },
            { label: '总计', value: scale(cost.total_tokens).toLocaleString() },
          ].map((m) => (
            <div
              key={m.label}
              className="rounded p-2 text-center"
              style={{ background: COLORS.pageBg, border: `1px solid ${COLORS.cardBorder}` }}
            >
              <div style={{ color: COLORS.textSecondary }} className="font-semibold">
                {m.value}
              </div>
              <div style={{ color: COLORS.textMuted }} className="mt-0.5">
                {m.label}
              </div>
            </div>
          ))}
        </div>
        {/* Cost */}
        <div
          className="flex items-center justify-between rounded px-2 py-1.5"
          style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)' }}
        >
          <span style={{ color: COLORS.textMuted }}>💰 预估费用 (USD)</span>
          <span className="font-bold" style={{ color: '#fbbf24' }}>
            ${(cost.estimated_cost_usd * frac).toFixed(4)}
          </span>
        </div>
        {/* Model */}
        <div className="flex justify-between">
          <span style={{ color: COLORS.textMuted }}>模型</span>
          <span className="font-mono" translate="no" style={{ color: COLORS.accentLight }}>
            {cost.model}
          </span>
        </div>
      </div>
    </div>
  )
}

// ─── BOTTOM: FINAL FILM CARD ──────────────────────────────────────────────────
function FinalFilmCard({ onOpenModal, onGenerateFinal, animPhase, taskId, actionStatus }) {
  const isDone = animPhase === 'done'
  const hasFilm = Boolean(currentData.final_video)
  return (
    <div
      className="flex flex-col rounded-lg overflow-hidden"
      style={{
        background: COLORS.cardBg,
        border: `1px solid ${COLORS.cardBorder}`,
        flex: '1 1 0',
        minWidth: 0,
      }}
    >
      <div
        className="px-3 py-2 text-xs font-semibold flex-shrink-0"
        style={{
          color: COLORS.textPrimary,
          borderBottom: `1px solid ${COLORS.cardBorder}`,
        }}
      >
        成片生成
      </div>
      <div className="p-3 flex flex-col gap-2 text-xs flex-1">
        <div style={{ color: isDone ? COLORS.textSecondary : COLORS.textMuted }}>
          {hasFilm ? '成片已生成，可以直接播放' : isDone ? '规划已完成，下一步可生成关键帧与视频' : '成片将在所有镜头生成后合成…'}
        </div>
        <button
          onClick={hasFilm ? onOpenModal : taskId && isDone ? onGenerateFinal : undefined}
          disabled={!hasFilm && (!taskId || !isDone || Boolean(actionStatus))}
          className="w-full py-2 rounded text-sm font-semibold"
          style={{
            background: hasFilm || (taskId && isDone) ? COLORS.accentPurple : COLORS.cardBorder,
            color: hasFilm || (taskId && isDone) ? '#1a1410' : COLORS.textMuted,
            boxShadow: hasFilm || (taskId && isDone) ? '0 2px 14px rgba(245,158,11,0.35)' : 'none',
            cursor: hasFilm || (taskId && isDone) ? 'pointer' : 'not-allowed',
          }}
        >
          {hasFilm ? '▶ 播放成片' : actionStatus === 'final' ? '正在合成…' : taskId && isDone ? '🎬 合成已有镜头' : '⏳ 等待规划'}
        </button>
        <div style={{ color: COLORS.textMuted }} className="flex items-center gap-1">
          <span>⏱</span>
          <span>{hasFilm ? `${currentData.film_duration || '已完成'} | 720p` : '— 秒 | — 镜头'}</span>
        </div>
        {hasFilm && (
          <button
            onClick={onOpenModal}
            className="text-xs"
            style={{ color: COLORS.accentLight, background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', padding: 0 }}
          >
            查看详情 ›
          </button>
        )}
      </div>
    </div>
  )
}

// ─── FINISHED FILM GALLERY ───────────────────────────────────────────────────
function FilmGalleryModal({ open, onClose }) {
  const [selectedIndex, setSelectedIndex] = useState(0)
  const selectedFilm = FINISHED_FILMS[selectedIndex]

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.86)' }}
      onClick={onClose}
    >
      <div
        className="relative rounded-xl overflow-hidden"
        style={{
          background: COLORS.cardBg,
          border: `1px solid ${COLORS.cardBorder}`,
          width: 'min(1040px, 92vw)',
          maxHeight: '88vh',
          display: 'grid',
          gridTemplateColumns: '260px minmax(0, 1fr)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex flex-col"
          style={{
            borderRight: `1px solid ${COLORS.cardBorder}`,
            minHeight: 0,
          }}
        >
          <div
            className="px-4 py-3"
            style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}
          >
            <div className="text-sm font-semibold" style={{ color: COLORS.textPrimary }}>
              成片展示
            </div>
            <div className="text-xs mt-1" style={{ color: COLORS.textMuted }}>
              已生成作品 · {FINISHED_FILMS.length} 部
            </div>
          </div>
          <div className="p-2 overflow-y-auto">
            {FINISHED_FILMS.map((film, index) => {
              const active = index === selectedIndex
              return (
                <button
                  key={film.title}
                  onClick={() => setSelectedIndex(index)}
                  className="w-full text-left rounded-lg px-3 py-2 mb-2"
                  style={{
                    background: active ? `${COLORS.accentPurple}22` : 'transparent',
                    border: `1px solid ${active ? `${COLORS.accentPurple}88` : COLORS.cardBorder}`,
                    color: active ? COLORS.accentLight : COLORS.textSecondary,
                    cursor: 'pointer',
                  }}
                >
                  <div className="text-sm font-semibold">《{film.title}》</div>
                  <div className="text-xs mt-1" style={{ color: COLORS.textMuted }}>
                    {film.meta}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        <div className="flex flex-col min-w-0">
          <div
            className="flex items-center justify-between px-4 py-3"
            style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}
          >
            <div>
              <div className="font-semibold text-sm" style={{ color: COLORS.textPrimary }}>
                《{selectedFilm.title}》
              </div>
              <div className="text-xs mt-1" style={{ color: COLORS.textMuted }}>
                {selectedFilm.meta}
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-xl leading-none"
              style={{ color: COLORS.textSecondary, background: 'none', border: 'none', cursor: 'pointer' }}
            >
              ✕
            </button>
          </div>
          <div className="p-4 overflow-y-auto">
            <video
              key={selectedFilm.video}
              controls
              autoPlay
              src={selectedFilm.video}
              className="w-full rounded-lg"
              style={{
                background: '#000',
                border: `1px solid ${COLORS.cardBorder}`,
                maxHeight: '68vh',
              }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── VIDEO MODAL ──────────────────────────────────────────────────────────────
function VideoModal({ open, onClose }) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.85)' }}
      onClick={onClose}
    >
      <div
        className="relative rounded-xl overflow-hidden"
        style={{
          background: COLORS.cardBg,
          border: `1px solid ${COLORS.cardBorder}`,
          maxWidth: '800px',
          width: '90vw',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between px-4 py-3"
          style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}
        >
          <span className="font-semibold text-sm" style={{ color: COLORS.textPrimary }}>
            {currentData.task.title || 'MovieAgent'} 成片
          </span>
          <button
            onClick={onClose}
            className="text-xl leading-none"
            style={{ color: COLORS.textSecondary, background: 'none', border: 'none', cursor: 'pointer' }}
          >
            ✕
          </button>
        </div>
        <video
          controls
          autoPlay
          src={currentData.final_video}
          className="w-full"
          style={{ background: '#000', display: 'block', maxHeight: '70vh' }}
        />
      </div>
    </div>
  )
}

// ─── TECH SHOWCASE ────────────────────────────────────────────────────────────
function TechShowcase() {
  return (
    <div
      className="flex-shrink-0"
      style={{
        borderTop: `2px solid ${COLORS.cardBorder}`,
        background: COLORS.cardBg,
      }}
    >
      {/* Divider label */}
      <div
        className="flex items-center gap-3 px-6 py-3"
        style={{ borderBottom: `1px solid ${COLORS.cardBorder}` }}
      >
        <div className="flex-1 h-px" style={{ background: COLORS.cardBorder }} />
        <span className="text-xs font-medium px-3" style={{ color: COLORS.textMuted }}>
          技术效果样片 / Technical Showcase
        </span>
        <div className="flex-1 h-px" style={{ background: COLORS.cardBorder }} />
      </div>

      <div className="flex flex-col md:flex-row items-start gap-8 px-6 py-5">
        {/* Left: disclaimer + full script */}
        <div className="flex-1 min-w-0">
          <p className="text-xs mb-4 leading-relaxed"
            style={{ color: COLORS.textMuted, borderLeft: `2px solid ${COLORS.cardBorder}`, paddingLeft: '10px' }}>
            此样片使用高叙事密度的已有 IP 剧本作为技术测试素材，用于展示系统的画面生成能力；产品正式内容面向原创故事创作。
          </p>

          <div className="text-xs mb-2 font-semibold tracking-wider uppercase"
            style={{ color: COLORS.textMuted }}>原始剧本 / Original Script</div>

          <div className="rounded-lg p-4 text-sm leading-relaxed"
            style={{ background: 'rgba(255,255,255,0.03)', border: `1px solid ${COLORS.cardBorder}`, color: COLORS.textSecondary, maxHeight: '320px', overflowY: 'auto' }}>
            <p className="mb-3">
              In a world where ancient seals hold back primordial darkness, five companions are called to prevent catastrophe. <strong style={{ color: COLORS.textPrimary }}>Lyra</strong> — a young scholar whose compass points toward magic — leads the group. <strong style={{ color: COLORS.textPrimary }}>Caden</strong>, her steadfast ally, watches the horizon for danger. <strong style={{ color: COLORS.textPrimary }}>Seraphine</strong>, gifted with arcane perception, reads the cracks in the world's foundations. <strong style={{ color: COLORS.textPrimary }}>Finn</strong>, blade always at the ready, guards the flanks. And <strong style={{ color: COLORS.textPrimary }}>Elder Moros</strong> — who has guarded the seal for decades — knows what failure truly means.
            </p>
            <p className="mb-3">
              The ancient seal had begun to crack. Deep fissures spread through the stone that had held the darkness at bay for generations. Elder Moros, who had devoted his life to the seal's preservation, faced the moment he had always dreaded. He called the five companions together: <em>"A primordial darkness stirs beneath the earth. The seal is failing. We must restore it — or everything ends."</em>
            </p>
            <p className="mb-3">
              Fear crossed Lyra's face when she heard the words — and then something harder, something resolute, took its place. Five companions stood small against the fractured sky. Their quest had begun.
            </p>
            <p className="mb-3">
              They traveled into the <strong style={{ color: COLORS.textPrimary }}>Whispering Highlands</strong> — ancient, untamed terrain holding the ruins of those who had bargained with gods and lost. Elder Moros led them deeper into the forest, where even the trees seemed to remember old wars. Lyra's compass glowed brighter with each step, pointing toward the heart of the ruins. <em>"Trust is earned,"</em> Caden said quietly. Finn didn't answer, but he kept walking. Moros watched each companion in turn, measuring what they carried — and what they hid.
            </p>
            <p className="mb-3">
              Seraphine's eyes snapped toward the ruins first. Something vast and ancient stirred there — she felt it in her blood, in the static charge that raised the hair on her arms. They pressed on through the mist, five shapes moving through a silence older than kingdoms.
            </p>
            <p>
              Then the ruin collapsed around Caden and Finn. Stone fell. A single passage remained open — too narrow for two. The trap had triggered. The moment of choice had arrived: who steps through, and who stays behind?
            </p>
          </div>

          {/* Shot count */}
          <div className="mt-3 flex gap-4 text-xs" style={{ color: COLORS.textMuted }}>
            <span>📽 16 镜头</span>
            <span>⏱ 80s</span>
            <span>🌐 英文旁白</span>
            <span>🎬 Seedance 1.0 Pro</span>
          </div>
        </div>

        {/* Right: video */}
        <div className="flex-shrink-0" style={{ width: '480px' }}>
          <video
            controls
            src={currentData.showcase_video}
            className="rounded-lg w-full"
            style={{
              background: '#000',
              border: `1px solid ${COLORS.cardBorder}`,
            }}
          />
        </div>
      </div>
    </div>
  )
}

// ─── APP ROOT ─────────────────────────────────────────────────────────────────
export default function App() {
  const [selectedShotId, setSelectedShotId] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [casePickerOpen, setCasePickerOpen] = useState(false)
  const [subscriptionOpen, setSubscriptionOpen] = useState(false)
  const [subscribed, setSubscribed] = useState(() => hasSubscriptionCode())
  const [publicCases, setPublicCases] = useState([])
  const [casesLoading, setCasesLoading] = useState(true)
  const [activeCase, setActiveCase] = useState(null)
  const [scriptInput, setScriptInput] = useState('')
  const [charactersInput, setCharactersInput] = useState('')
  const [resumeTaskId, setResumeTaskId] = useState(() => window.localStorage.getItem('movieagent-task-id') || '')
  const [taskId, setTaskId] = useState('')
  const [error, setError] = useState('')
  const [actionStatus, setActionStatus] = useState('')
  const [pollVersion, setPollVersion] = useState(0)
  const [, setDataVersion] = useState(0)
  const requestRef = useRef({ script: '', rawScript: '', characters: [] })

  // Animation state
  const [animPhase, setAnimPhase] = useState('empty')
  const [rawTyped, setRawTyped] = useState('')
  const [synopsisTyped, setSynopsisTyped] = useState('')
  const [visibleSceneCount, setVisibleSceneCount] = useState(0)
  const timersRef = useRef([])

  const clearAllTimers = () => {
    timersRef.current.forEach((t) => clearInterval(t))
    timersRef.current = []
  }

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

  const startDemo = useCallback(() => {
    clearAllTimers()
    currentData = demoData
    setDataVersion((value) => value + 1)
    setTaskId('')
    setError('')
    setAnimPhase('typing_raw')
    setRawTyped('')
    setSynopsisTyped('')
    setVisibleSceneCount(0)
    setSelectedShotId('')

    const RAW = demoData.story.raw_synopsis
    const SYN = demoData.story.synopsis
    const SCENE_COUNT = demoData.storyboard[0].scenes.length

    let i = 0
    const rawTimer = setInterval(() => {
      i++
      setRawTyped(RAW.slice(0, i))
      if (i >= RAW.length) {
        clearInterval(rawTimer)
        // Pause → rewriting
        const t1 = setTimeout(() => {
          setAnimPhase('rewriting')
          // Rewriting delay → start typing synopsis
          const t2 = setTimeout(() => {
            setAnimPhase('typing_synopsis')
            let j = 0
            const synTimer = setInterval(() => {
              j++
              setSynopsisTyped(SYN.slice(0, j))
              if (j >= SYN.length) {
                clearInterval(synTimer)
                // Done typing → director phase
                const t3 = setTimeout(() => {
                  setAnimPhase('director')
                  // Director thinking → reveal scenes
                  const t4 = setTimeout(() => {
                    setAnimPhase('scenes')
                    let s = 0
                    const sceneTimer = setInterval(() => {
                      s++
                      setVisibleSceneCount(s)
                      if (s >= SCENE_COUNT) {
                        clearInterval(sceneTimer)
                        const t5 = setTimeout(() => {
                          setAnimPhase('done')
                          setSelectedShotId(demoData.storyboard[0]?.scenes[0]?.shots[0]?.id || '')
                        }, 400)
                        timersRef.current.push(t5)
                      }
                    }, 380)
                    timersRef.current.push(sceneTimer)
                  }, 1300)
                  timersRef.current.push(t4)
                }, 600)
                timersRef.current.push(t3)
              }
            }, 10)
            timersRef.current.push(synTimer)
          }, 1900)
          timersRef.current.push(t2)
        }, 700)
        timersRef.current.push(t1)
      }
    }, 14)
    timersRef.current.push(rawTimer)
  }, [])

  const rewriteCurrentScript = async () => {
    const rawScript = scriptInput.trim()
    if (!rawScript) return
    if (!hasSubscriptionCode()) {
      setSubscriptionOpen(true)
      return
    }

    setError('')
    setRawTyped(rawScript)
    setSynopsisTyped('')
    setAnimPhase('rewriting')
    try {
      const result = await rewriteScript(rawScript)
      setSynopsisTyped(result.rewritten || '')
      setAnimPhase('rewritten')
    } catch (requestError) {
      setAnimPhase('empty')
      setError(`剧本改写失败：${requestError.message}`)
    }
  }

  const handleScriptChange = (value) => {
    setScriptInput(value)
    if (animPhase === 'rewritten') {
      setSynopsisTyped('')
      setAnimPhase('empty')
    }
  }

  const startLiveTask = async (options = null) => {
    const supplied = options && options.script ? options : null
    const script = (supplied?.script || synopsisTyped).trim()
    const rawScript = (supplied?.rawScript || scriptInput || script).trim()
    const characters = supplied?.characters || charactersInput.split(/[，,、\n]/).map((name) => name.trim()).filter(Boolean)
    if (!script) return
    if (!characters.length) {
      setError('请至少填写一个角色名称。')
      return
    }

    setError('')
    setActiveCase(null)
    setScriptInput(rawScript)
    setCharactersInput(characters.join('，'))
    setSelectedShotId('')
    setRawTyped(rawScript)
    setSynopsisTyped(script)
    setVisibleSceneCount(0)
    setAnimPhase('director')
    requestRef.current = { script, rawScript, characters }
    currentData = taskToDemoData('', {
      status: 'pending', progress: 0, logs: [], cost: {}, characters,
    }, script, characters)
    setDataVersion((value) => value + 1)

    try {
      const result = await startPlanning(script, characters, rawScript)
      setTaskId(result.task_id)
      setResumeTaskId(result.task_id)
      window.localStorage.setItem('movieagent-task-id', result.task_id)
    } catch (requestError) {
      setAnimPhase('rewritten')
      setError(`无法连接后端：${requestError.message}`)
    }
  }

  useEffect(() => {
    if (!taskId) return undefined
    let cancelled = false

    const refresh = async () => {
      try {
        const task = await getTask(taskId)
        if (cancelled) return
        currentData = taskToDemoData(
          taskId,
          task,
          requestRef.current.script,
          requestRef.current.characters,
        )
        const scenes = currentData.storyboard.flatMap((item) => item.scenes || [])
        setDataVersion((value) => value + 1)
        setVisibleSceneCount(scenes.length)
        setAnimPhase(task.status === 'done' ? 'done' : scenes.length ? 'scenes' : 'director')
        if (task.status === 'done') {
          const firstShot = scenes.flatMap((scene) => scene.shots || [])[0]
          setSelectedShotId(firstShot?.id || '')
          return
        }
        if (task.status === 'error') {
          setError(task.logs?.at(-1) || '后端任务执行失败。')
          setAnimPhase('empty')
          return
        }
        window.setTimeout(refresh, 1500)
      } catch (requestError) {
        if (!cancelled) {
          setError(`读取任务状态失败：${requestError.message}`)
          setAnimPhase('empty')
        }
      }
    }

    refresh()
    return () => { cancelled = true }
  }, [taskId, pollVersion])

  const refreshCurrentTask = async () => {
    const task = await getTask(taskId)
    currentData = taskToDemoData(
      taskId,
      task,
      requestRef.current.script,
      requestRef.current.characters,
    )
    setDataVersion((value) => value + 1)
    return task
  }

  const resumeExistingTask = async () => {
    const id = resumeTaskId.trim()
    if (!id) return
    setActionStatus('resume')
    setError('')
    try {
      const task = await getTask(id)
      const subScripts = task.sub_scripts?.['Sub-Script'] || {}
      const script = task.script_synopsis || Object.values(subScripts).map((item) => item?.Plot || '').filter(Boolean).join('\n\n')
      const rawScript = task.raw_script || script
      const characters = task.characters?.length
        ? task.characters
        : [...new Set(Object.values(subScripts).flatMap((item) => item?.['Involving Characters'] || []))]
      requestRef.current = { script, rawScript, characters }
      currentData = taskToDemoData(id, task, script, characters)
      const scenes = currentData.storyboard.flatMap((item) => item.scenes || [])
      setScriptInput(rawScript)
      setCharactersInput(characters.join('，'))
      setRawTyped(rawScript)
      setSynopsisTyped(script)
      setVisibleSceneCount(scenes.length)
      setTaskId(id)
      setAnimPhase(task.status === 'done' ? 'done' : scenes.length ? 'scenes' : 'director')
      setSelectedShotId(scenes.flatMap((scene) => scene.shots || [])[0]?.id || '')
      setDataVersion((value) => value + 1)
      window.localStorage.setItem('movieagent-task-id', id)
    } catch (requestError) {
      setError(`加载任务失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const loadPublicCase = async (caseId) => {
    setCasesLoading(true)
    setError('')
    try {
      const result = await getCase(caseId)
      const { task, script, characters } = result
      clearAllTimers()
      currentData = taskToDemoData(result.case.task_id, task, script, characters)
      currentData.task.title = result.case.title
      requestRef.current = { script, rawScript: task.raw_script || script, characters }
      const scenes = currentData.storyboard.flatMap((item) => item.scenes || [])
      const firstShot = scenes.flatMap((scene) => scene.shots || [])[0]
      setScriptInput(task.raw_script || script)
      setCharactersInput(characters.join('，'))
      setRawTyped(task.raw_script || script)
      setSynopsisTyped(script)
      setVisibleSceneCount(scenes.length)
      setSelectedShotId(firstShot?.id || '')
      setTaskId('')
      setActiveCase(result.case)
      setAnimPhase('done')
      setDataVersion((value) => value + 1)
      setCasePickerOpen(false)
    } catch (requestError) {
      setError(`案例加载失败：${requestError.message}`)
    } finally {
      setCasesLoading(false)
    }
  }

  const generateShotArtifact = async (type, shot) => {
    if (!taskId || !shot) return
    const actions = { keyframe: generateKeyframe, video: generateVideo, audio: generateAudio }
    setActionStatus(type)
    setError('')
    try {
      await actions[type](taskId, shot)
      await refreshCurrentTask()
    } catch (requestError) {
      setError(`${type === 'keyframe' ? '关键帧' : type === 'video' ? '视频' : '音频'}生成失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const saveShotChanges = async (shot, values) => {
    if (!taskId || !shot) return
    setActionStatus('save')
    setError('')
    try {
      await updateShot(taskId, shot, values)
      await refreshCurrentTask()
    } catch (requestError) {
      setError(`保存 Shot 失败：${requestError.message}`)
      throw requestError
    } finally {
      setActionStatus('')
    }
  }

  const saveSceneChanges = async (scene, values) => {
    if (!taskId || !scene) return
    setActionStatus('save-scene')
    setError('')
    try {
      await updateScene(taskId, scene, values)
      await refreshCurrentTask()
    } catch (requestError) {
      setError(`保存 Scene 失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const generateCharacterReferences = async () => {
    if (!taskId) return
    setActionStatus('characters')
    setError('')
    try {
      const result = await generateCharacters(taskId, requestRef.current.characters, requestRef.current.script)
      await refreshCurrentTask()
      const failures = Object.entries(result.errors || {})
      if (failures.length) {
        throw new Error(failures.map(([name, message]) => `${name}: ${message}`).join('；'))
      }
    } catch (requestError) {
      setError(`人物定妆图生成失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const regenerateScenePlan = async (scene) => {
    if (!taskId || !scene) return
    setActionStatus('scene')
    setError('')
    try {
      await regenerateScenes(taskId, scene.subScriptName)
      setAnimPhase('scenes')
      setPollVersion((value) => value + 1)
    } catch (requestError) {
      setError(`场景重新规划失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const regenerateShotPlan = async (scene) => {
    if (!taskId || !scene) return
    setActionStatus('shot')
    setError('')
    try {
      await regenerateShots(taskId, scene)
      setAnimPhase('scenes')
      setPollVersion((value) => value + 1)
    } catch (requestError) {
      setError(`镜头重新规划失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const restartPlanning = () => {
    const script = requestRef.current.script || currentData.story.raw_synopsis || scriptInput
    const rawScript = requestRef.current.rawScript || scriptInput || script
    const characters = requestRef.current.characters.length
      ? requestRef.current.characters
      : currentData.story.characters.map((character) => character.name)
    return startLiveTask({ script, rawScript, characters })
  }

  const buildFinalFilm = async () => {
    if (!taskId) return
    setActionStatus('final')
    setError('')
    try {
      const result = await generateFinalVideo(taskId)
      currentData = { ...currentData, final_video: result.final_video_url }
      setDataVersion((value) => value + 1)
    } catch (requestError) {
      setError(`成片合成失败：${requestError.message}`)
    } finally {
      setActionStatus('')
    }
  }

  const resetDemo = () => {
    clearAllTimers()
    currentData = EMPTY_DATA
    setDataVersion((value) => value + 1)
    setTaskId('')
    setError('')
    setActionStatus('')
    setAnimPhase('empty')
    setRawTyped('')
    setSynopsisTyped('')
    setVisibleSceneCount(0)
    setSelectedShotId('')
    setActiveCase(null)
  }

  const sceneTotal = currentData.storyboard.flatMap((item) => item.scenes || []).length
  const progress = taskId ? currentData.task.progress : phaseProgress(animPhase, visibleSceneCount, sceneTotal)

  return (
    <div
      style={{
        background: COLORS.pageBg,
        color: COLORS.textPrimary,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        minHeight: '100vh',
        overflowX: 'hidden',
      }}
    >
      {/* CSS animations */}
      <style>{`
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes dotPulse { 0%,80%,100%{transform:scale(0.6);opacity:0.4} 40%{transform:scale(1);opacity:1} }
        @keyframes fadeSlideIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
        @keyframes magShimmer { 0%{background-position:100% 0} 100%{background-position:-100% 0} }
        .mag-skeleton {
          background: linear-gradient(90deg, ${COLORS.cardBorder}55 25%, ${COLORS.textMuted}44 37%, ${COLORS.cardBorder}55 63%);
          background-size: 200% 100%;
          animation: magShimmer 1.4s ease-in-out infinite;
        }
      `}</style>

      {/* Fixed-height viewport section */}
      <div className="flex flex-col" style={{ height: '100vh', overflow: 'hidden' }}>
        {/* Top bar */}
        <TopBar
          animPhase={animPhase}
          onReset={resetDemo}
          onOpenGallery={() => setGalleryOpen(true)}
          taskId={taskId}
        />

        <CaseBar
          activeCase={activeCase}
          subscribed={subscribed}
          onOpenCases={() => setCasePickerOpen(true)}
          onOpenSubscription={() => setSubscriptionOpen(true)}
        />

        {/* Main 3-column area — the framework is always on screen */}
        <div className="flex flex-1 overflow-hidden">
          <LeftPanel
            animPhase={animPhase}
            rawTyped={rawTyped}
            synopsisTyped={synopsisTyped}
            scriptInput={scriptInput}
            charactersInput={charactersInput}
            onScriptChange={handleScriptChange}
            onCharactersChange={setCharactersInput}
            onRewrite={rewriteCurrentScript}
            onStartPlanning={startLiveTask}
            onGenerateCharacters={generateCharacterReferences}
            taskId={taskId}
            actionStatus={actionStatus}
            error={error}
          />

          {/* Center storyboard */}
          <Storyboard
            selectedShotId={selectedShotId}
            onSelectShot={setSelectedShotId}
            animPhase={animPhase}
            visibleSceneCount={visibleSceneCount}
            taskId={taskId}
            actionStatus={actionStatus}
            onRestartPlanning={restartPlanning}
            onRegenerateScene={regenerateScenePlan}
            onRegenerateShots={regenerateShotPlan}
            onSaveShot={saveShotChanges}
            onSaveScene={saveSceneChanges}
            onGenerateArtifact={generateShotArtifact}
          />

          {/* Right detail panel — always present, fields fill in when a shot exists */}
          <RightPanel
            shotId={selectedShotId}
            taskId={taskId}
            onGenerateArtifact={generateShotArtifact}
            onSaveShot={saveShotChanges}
            onRegenerateShots={regenerateShotPlan}
            actionStatus={actionStatus}
          />
        </div>

        {/* Bottom row — always present, data streams in */}
        <div
          className="flex gap-3 p-3 flex-shrink-0"
          style={{
            background: COLORS.pageBg,
            borderTop: `1px solid ${COLORS.cardBorder}`,
          }}
        >
          <LogCard animPhase={animPhase} />
          <TaskInfoCard animPhase={animPhase} progress={progress} />
          <CostCard progress={progress} animPhase={animPhase} />
          <FinalFilmCard
            onOpenModal={() => setModalOpen(true)}
            onGenerateFinal={buildFinalFilm}
            animPhase={animPhase}
            taskId={taskId}
            actionStatus={actionStatus}
          />
        </div>
      </div>

      {/* Tech showcase */}
      {animPhase === 'done' && !taskId && !activeCase && <TechShowcase />}

      {/* Modal */}
      <VideoModal open={modalOpen} onClose={() => setModalOpen(false)} />
      <FilmGalleryModal open={galleryOpen} onClose={() => setGalleryOpen(false)} />
      <CasePickerModal open={casePickerOpen} cases={publicCases} loading={casesLoading} onClose={() => setCasePickerOpen(false)} onSelect={loadPublicCase} />
      <SubscriptionModal open={subscriptionOpen} onClose={() => setSubscriptionOpen(false)} onVerified={() => setSubscribed(true)} />
    </div>
  )
}
