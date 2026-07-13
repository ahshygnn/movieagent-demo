const CHARACTER_COLORS = ['#f59e0b', '#a78bfa', '#34d399', '#60a5fa', '#fb7185']
const SUBSCRIPTION_KEY = 'movieagent-subscription-code'

async function request(path, options = {}) {
  const { skipSubscription = false, ...fetchOptions } = options
  const subscriptionCode = window.localStorage.getItem(SUBSCRIPTION_KEY) || ''
  const response = await fetch(path, {
    ...fetchOptions,
    headers: {
      'Content-Type': 'application/json',
      ...(!skipSubscription && subscriptionCode ? { 'X-Subscription-Code': subscriptionCode } : {}),
      ...fetchOptions.headers,
    },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const subscriptionRequired = response.status === 401
      || (response.status === 503 && String(body.detail || '').includes('订阅码'))
    if (subscriptionRequired && !skipSubscription) {
      window.localStorage.removeItem(SUBSCRIPTION_KEY)
      window.dispatchEvent(new CustomEvent('movieagent:subscription-required'))
    }
    throw new Error(body.detail || body.error || `请求失败 (${response.status})`)
  }
  if (body.error) throw new Error(body.error)
  return body
}

export function listCases() {
  return request('/api/cases', { skipSubscription: true })
}

export function getCase(caseId) {
  return request(`/api/cases/${caseId}`, { skipSubscription: true })
}

export async function verifySubscriptionCode(code) {
  const result = await request('/api/subscription/verify', {
    method: 'POST',
    body: JSON.stringify({ code }),
    skipSubscription: true,
  })
  window.localStorage.setItem(SUBSCRIPTION_KEY, code)
  return result
}

export function hasSubscriptionCode() {
  return Boolean(window.localStorage.getItem(SUBSCRIPTION_KEY))
}

export function rewriteScript(rawScript) {
  return request('/api/rewrite', {
    method: 'POST',
    body: JSON.stringify({ raw_script: rawScript }),
  })
}

export function startPlanning(scriptSynopsis, characters, rawScript = '') {
  return request('/api/generate', {
    method: 'POST',
    body: JSON.stringify({
      script_synopsis: scriptSynopsis,
      raw_script: rawScript || scriptSynopsis,
      characters,
      character_refs: {},
      voice_refs: {},
    }),
  })
}

export function getTask(taskId) {
  return request(`/api/status/${taskId}`)
}

function shotPayload(taskId, shot) {
  return {
    task_id: taskId,
    sub_script_name: shot.subScriptName,
    scene_name: shot.scene,
    shot_name: shot.shot,
  }
}

export function generateKeyframe(taskId, shot) {
  return request('/api/generate/keyframe', { method: 'POST', body: JSON.stringify(shotPayload(taskId, shot)) })
}

export function generateVideo(taskId, shot) {
  return request('/api/generate/video', { method: 'POST', body: JSON.stringify(shotPayload(taskId, shot)) })
}

export function generateAudio(taskId, shot) {
  return request('/api/generate/audio', {
    method: 'POST',
    body: JSON.stringify({ ...shotPayload(taskId, shot), voice_refs: {} }),
  })
}

export function generateFinalVideo(taskId) {
  return request('/api/generate/final_video', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, sub_script_names: [] }),
  })
}

export function updateShot(taskId, shot, values) {
  const dialogue = values.dialogue?.trim() ? { 旁白: values.dialogue.trim() } : {}
  return request('/api/update/shot', {
    method: 'POST',
    body: JSON.stringify({
      ...shotPayload(taskId, shot),
      updates: {
        'Plot/Visual Description': values.plot,
        'Shot Type': values.shotType,
        'Camera Movement': values.cameraMovement,
        Subtitles: dialogue,
        Dialogue: dialogue,
      },
    }),
  })
}

export function updateScene(taskId, scene, values) {
  return request('/api/update/scene', {
    method: 'POST',
    body: JSON.stringify({
      task_id: taskId,
      sub_script_name: scene.subScriptName,
      scene_name: scene.scene,
      updates: {
        Plot: values.plot,
        'Scene Description': values.description,
        'Emotional Tone': values.tone,
      },
    }),
  })
}

export function generateCharacters(taskId, characters, scriptSynopsis) {
  return request('/api/generate/characters', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, characters, script_synopsis: scriptSynopsis }),
  })
}

export function regenerateScenes(taskId, subScriptName) {
  return request('/api/regenerate/scene', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, sub_script_name: subScriptName }),
  })
}

export function regenerateShots(taskId, scene) {
  return request('/api/regenerate/shot', {
    method: 'POST',
    body: JSON.stringify({
      task_id: taskId,
      sub_script_name: scene.subScriptName,
      scene_name: scene.scene,
    }),
  })
}

function characterNames(value) {
  if (Array.isArray(value)) return value
  if (value && typeof value === 'object') return Object.keys(value)
  return []
}

function subtitlesText(value) {
  if (!value) return ''
  if (typeof value === 'string') return value
  return Object.entries(value).map(([speaker, line]) => `${speaker}: ${line}`).join('\n')
}

export function taskToDemoData(taskId, task, scriptSynopsis, requestedCharacters) {
  const subScripts = task.sub_scripts?.['Sub-Script'] || {}
  const plannedCharacters = [...new Set(Object.values(subScripts).flatMap((item) => item?.['Involving Characters'] || []))]
  const storyboard = Object.entries(subScripts).map(([subName, subData], subIndex) => {
    const scenes = task.scenes?.[subName]?.Scene || {}
    return {
      sub_script: subName,
      sub_script_plot: subData?.Plot || '',
      scenes: Object.entries(scenes).map(([sceneName, sceneData], sceneIndex) => {
        const shots = task.shots?.[subName]?.[sceneName]?.Shot || {}
        return {
          id: `${subIndex + 1}-${sceneIndex + 1}`,
          subScriptName: subName,
          scene: sceneName,
          title: sceneData?.['Scene Description'] || sceneData?.Plot || sceneName,
          plot: sceneData?.Plot || '',
          description: sceneData?.['Scene Description'] || '',
          tone: sceneData?.['Emotional Tone'] || '',
          shots: Object.entries(shots).map(([shotName, shotData], shotIndex) => ({
            id: `${subIndex + 1}-${sceneIndex + 1}-${shotIndex + 1}`,
            number: `${subIndex + 1}.${sceneIndex + 1}.${shotIndex + 1}`,
            subScriptName: subName,
            scene: sceneName,
            shot: shotName,
            plot: shotData?.['Plot/Visual Description'] || '',
            characters: characterNames(shotData?.['Involving Characters']),
            shotType: shotData?.['Shot Type'] || '',
            cameraMovement: shotData?.['Camera Movement'] || '',
            dialogue: subtitlesText(shotData?.Subtitles),
            subtitles: subtitlesText(shotData?.Subtitles),
            keyframe: shotData?.keyframe_url || '',
            video: shotData?.video_url || '',
            kfStatus: shotData?.keyframe_status || 'pending',
            videoStatus: shotData?.video_status || 'pending',
          })),
        }
      }),
    }
  })

  const characterList = task.characters?.length ? task.characters : requestedCharacters?.length ? requestedCharacters : plannedCharacters
  const assetUrl = (value, fallbackBase) => {
    const text = String(value || '')
    if (text.startsWith('/')) return text
    return `${fallbackBase}/${text.split(/[\\/]/).pop()}`
  }
  const characters = characterList.map((name, index) => ({
    name,
    role: '',
    color: CHARACTER_COLORS[index % CHARACTER_COLORS.length],
    image: task.character_refs?.[name]
      ? assetUrl(task.character_refs[name], '/outputs/characters')
      : '',
  }))
  const totalTokens = (task.cost?.input_tokens || 0) + (task.cost?.output_tokens || 0)

  return {
    task: {
      id: taskId,
      title: scriptSynopsis.slice(0, 16) || '未命名项目',
      status: task.status || 'pending',
      progress: task.progress || 0,
      mode: 'final',
      resolution: '720p',
      shot_duration: '5s',
      created_at: '',
      finished_at: task.status === 'done' ? new Date().toLocaleString() : '',
    },
    story: {
      raw_synopsis: scriptSynopsis,
      synopsis: scriptSynopsis,
      characters,
    },
    storyboard,
    logs: (task.logs || []).map((message, index) => ({
      time: String(index + 1).padStart(2, '0'),
      agent: message.includes('Director') ? 'Director Agent' : message.includes('Scene') ? 'Scene Agent' : 'Shot Agent',
      msg: message,
    })),
    cost: {
      input_tokens: task.cost?.input_tokens || 0,
      output_tokens: task.cost?.output_tokens || 0,
      total_tokens: totalTokens,
      estimated_cost_usd: 0,
      model: 'backend configured model',
    },
    final_video: task.final_video_url || '',
    film_duration: '',
    showcase_video: '',
  }
}
