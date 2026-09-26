import { api } from './api.js'

const POLL_IDLE_MS = 600
const POLL_ACTIVE_MS = 400
const BLOCK_SHOWN_MAX = 50

let ALIAS_SET = new Set()
let PROFILE_SET = new Set()
let SFX_MAP = {}
let BLOCK_TERMS = []
let BLOCK_SHOWN = false
let SFX_REMOVABLE = []

function byId(id) {
  return document.getElementById(id)
}
function numOrNull(id) {
  const v = byId(id)?.value?.trim() ?? ''
  if (v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

// the select mixes both, so a profile name must not be sent as a voice id
function voiceOrProfile(v) {
  if (!v) return { voice: null }
  return PROFILE_SET.has(v.toLowerCase()) ? { profile: v } : { voice: v }
}

function payload(text, voice) {
  return {
    text,
    ...voiceOrProfile(voice),
    preset: byId('preset')?.value || null,
    length_scale: numOrNull('length_scale')
  }
}

async function getPanelStatus() {
  const me = await api.panel.status()
  const role = me.role
  const s = { admin: role === 'admin', mod: role === 'admin' || role === 'mod' }
  byId('auth_status').textContent = role ? `signed in as ${role}` : 'not logged in'
  const canTts = s.mod
  byId('submit').disabled = !canTts
  for (const id of ['catalog_admin', 'token_admin', 'oauth_admin']) {
    byId(id).hidden = !s.admin
  }
  byId('mod_panel').hidden = !s.mod

  if (s.admin) {
    loadEmbeds()
    loadMappings()
  }

  if (s.mod) {
    loadCensorMode()
    loadBlocklist()
  }
  byId('pollq').disabled = !(s.admin || s.mod)
  if (byId('pollq').disabled) byId('pollq').checked = false
  return s
}

async function loadVoices() {
  const sel = byId('voices')
  const keep = sel.value
  sel.innerHTML = ''

  let cat = {}
  try {
    cat = await api.catalog()
  } catch { }

  const voices = cat.voices || []
  const aliases = cat.voice_aliases || {}
  const profs = cat.profiles || {}

  if (Object.keys(profs).length) {
    const ogP = document.createElement('optgroup')
    ogP.label = 'profiles'
    for (const [n, p] of Object.entries(profs)) {
      const o = document.createElement('option')
      o.value = n
      o.textContent = `${n} → ${p.voice || 'default'}${p.fx ? ` (${p.fx})` : ''}`
      ogP.appendChild(o)
    }
    sel.appendChild(ogP)
  }

  if (Object.keys(aliases).length) {
    const ogA = document.createElement('optgroup')
    ogA.label = 'aliases'
    for (const [n, t] of Object.entries(aliases)) {
      const o = document.createElement('option')
      o.value = n
      o.textContent = `${n} → ${t}`
      ogA.appendChild(o)
    }
    sel.appendChild(ogA)
  }

  if (voices.length) {
    const ogV = document.createElement('optgroup')
    ogV.label = 'voices'
    for (const v of voices) {
      const o = document.createElement('option')
      o.value = v.id
      o.textContent = v.id
      ogV.appendChild(o)
    }
    sel.appendChild(ogV)
  }

  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep
  else if (sel.options.length) sel.selectedIndex = 0

  for (const id of ['alias_target', 'profile_target']) {
    const tgt = byId(id)
    if (!tgt) continue

    const keep2 = tgt.value
    tgt.innerHTML = ''
    for (const v of voices) {
      const o = document.createElement('option')
      o.value = v.id
      o.textContent = v.id
      tgt.appendChild(o)
    }
    if ([...tgt.options].some((o) => o.value === keep2)) tgt.value = keep2
  }

  const rm = cat.removable || {}

  const al = byId('aliaslist')
  if (al) {
    const items = Object.entries(aliases).map(([n, t]) => ({ term: n, label: `${n} \u2192 ${t}` }))
    renderTermList(al, items, 'alias-del', rm.voice_alias)
  }

  const psel = byId('preset')
  if (psel) {
    const keepP = psel.value
    psel.innerHTML = '<option value="">(none)</option>'
    for (const name of cat.presets || []) {
      const o = document.createElement('option')
      o.value = name
      o.textContent = name
      psel.appendChild(o)
    }
    if ([...psel.options].some((o) => o.value === keepP)) psel.value = keepP
  }

  PROFILE_SET = new Set(Object.keys(profs).map((s) => s.toLowerCase()))
  ALIAS_SET = new Set([
    ...PROFILE_SET,
    ...Object.keys(aliases).map((s) => s.toLowerCase()),
    ...voices.map((v) => v.id.toLowerCase())
  ])
}

async function loadCensorMode() {
  const sel = byId('censor_mode')
  if (!sel) return

  try {
    const { mode, modes, censoring } = await api.mod.state()
    sel.innerHTML = ''
    for (const m of modes) {
      const o = document.createElement('option')
      o.value = m
      o.textContent = m
      sel.appendChild(o)
    }
    sel.value = mode
    setCensoringUi(censoring)
  } catch { }
}

function setCensoringUi(on) {
  const box = byId('censor_on')
  if (box) box.checked = !!on

  const sel = byId('censor_mode')
  if (sel) sel.disabled = !on
}

function renderTermList(host, items, action, removable) {
  host.innerHTML = ''

  for (const it of items) {
    const { term, label } = typeof it === 'string' ? { term: it, label: it } : it
    const chip = document.createElement('span')
    chip.className = 'chip'
    chip.textContent = label

    if (action && (!removable || removable.includes(term))) {
      const x = document.createElement('button')
      x.dataset.action = action
      x.dataset.term = term
      x.textContent = '\u00d7'
      chip.appendChild(x)
    }

    host.appendChild(chip)
  }
}

function maskTerm(t) {
  if (t.length <= 2) return '*'.repeat(t.length)
  return t[0] + '*'.repeat(t.length - 2) + t.at(-1)
}

function renderBlocklist() {
  const host = byId('blocklist')
  const count = byId('block_count')
  const btn = byId('block_reveal')
  if (!host) return

  if (count) count.textContent = `${BLOCK_TERMS.length} blocked`
  if (btn) btn.textContent = BLOCK_SHOWN ? 'hide' : 'show'

  if (!BLOCK_SHOWN) {
    host.innerHTML = ''
    return
  }

  const shown = BLOCK_TERMS.slice(0, BLOCK_SHOWN_MAX)
  renderTermList(
    host,
    shown.map((t) => ({ term: t, label: maskTerm(t) })),
    'block-remove'
  )

  if (BLOCK_TERMS.length > shown.length) {
    const more = document.createElement('div')
    more.textContent = `and ${BLOCK_TERMS.length - shown.length} more, edit the file for the rest`
    host.appendChild(more)
  }
}

async function loadBlocklist() {
  if (!byId('blocklist')) return

  try {
    const { terms } = await api.mod.state()
    BLOCK_TERMS = terms
  } catch {
    BLOCK_TERMS = []
  }

  renderBlocklist()
}

async function loadSounds() {
  SFX_MAP = {}
  try {
    const cat = await api.catalog()
    SFX_REMOVABLE = (cat.removable || {}).sfx_alias || []
    const index = cat.sounds || {}
    const aliases = cat.sfx_aliases || {}
    for (const [id, v] of Object.entries(index)) SFX_MAP[id.toLowerCase()] = '/sounds/' + v.file
    for (const [name, target] of Object.entries(aliases)) {
      const tgt = index[target]
      if (tgt) SFX_MAP[name.toLowerCase()] = '/sounds/' + tgt.file
    }
    renderSfxAdmin(index, aliases)
  } catch { }
}

function renderSfxAdmin(index, aliases) {
  const tgt = byId('sfx_target')

  if (tgt) {
    const keep = tgt.value
    tgt.innerHTML = ''
    for (const id of Object.keys(index).sort()) {
      const o = document.createElement('option')
      o.value = id
      o.textContent = id
      tgt.appendChild(o)
    }
    if ([...tgt.options].some((o) => o.value === keep)) tgt.value = keep
  }

  const host = byId('sfxlist')

  if (host) {
    const items = Object.entries(aliases).map(([n, t]) => ({ term: n, label: `${n} \u2192 ${t}` }))
    renderTermList(host, items, 'sfx-del', SFX_REMOVABLE)
  }
}
function parseParts(input, fallbackVoice) {
  const reVoice = /(^|\s)([a-z0-9_]+):\s*/gi
  const reSfx = /\{([a-z0-9_-]+)\}/gi
  const reSpeed = /\[(fast|slow)\]/gi
  const parts = []
  let curVoice = fallbackVoice || null
  let i = 0,
    m,
    sfxCount = 0

  let speedMult = 1.0
  const speedMatch = reSpeed.exec(input)
  if (speedMatch) {
    speedMult = speedMatch[1].toLowerCase() === 'fast' ? 0.5 : 2.0
    input = input.replace(reSpeed, '').trim()
  }

  function pushText(chunk) {
    if (!chunk) return
    reSfx.lastIndex = 0
    let pos = 0,
      sm
    while ((sm = reSfx.exec(chunk)) !== null) {
      const t0 = chunk.slice(pos, sm.index)
      if (t0.trim()) parts.push({ type: 'tts', text: t0.trim(), voice: curVoice })
      const key = sm[1].toLowerCase()
      if (SFX_MAP[key] && sfxCount < 10) {
        parts.push({ type: 'sfx', name: key })
        sfxCount += 1
      } else parts.push({ type: 'tts', text: sm[0], voice: curVoice })
      pos = reSfx.lastIndex
    }
    const tail = chunk.slice(pos)
    if (tail.trim()) parts.push({ type: 'tts', text: tail.trim(), voice: curVoice })
  }

  while ((m = reVoice.exec(input)) !== null) {
    const tok = m[2].toLowerCase()
    if (!ALIAS_SET || !ALIAS_SET.has(tok)) continue
    const segStart = m.index + m[1].length
    if (segStart > i) pushText(input.slice(i, segStart))
    curVoice = tok
    i = reVoice.lastIndex
  }
  pushText(input.slice(i))
  const resultParts = parts.length ? parts : [{ type: 'tts', text: input, voice: fallbackVoice || null }]
  return { parts: resultParts, speedMult }
}

async function playText(fullText, fallbackVoice, statusEl) {
  const a = byId('player')
  const { parts, speedMult } = parseParts(fullText, fallbackVoice)
  const single = parts.length === 1 && parts[0].type !== 'sfx'
  statusEl.textContent = single ? 'playing...' : 'rendering...'

  let ls = numOrNull('length_scale')
  if (speedMult !== 1.0) {
    ls = (ls || 1.0) * speedMult
  }

  try {
    const body = single
      ? { ...payload(parts[0].text, parts[0].voice), length_scale: ls }
      : {
        parts: parts.map((p) => {
          if (p.type === 'sfx') return { sfx: p.name }
          return Object.assign({ text: p.text }, voiceOrProfile(p.voice))
        }),
        format: 'mp3',
        preset: byId('preset')?.value || null,
        length_scale: ls
      }
    const res = await api.tts(body)
    // res: { arrayBuffer, contentType }
    const blobObj = new Blob([res.arrayBuffer], { type: res.contentType })
    const url = URL.createObjectURL(blobObj)
    a.src = url
    await a.play()
    statusEl.textContent = 'done'
  } catch (err) {
    console.error(err)
    statusEl.textContent = 'error'
  }
}

async function addRow(text, voice, jobId, opts = {}) {
  const allowAutoplay = opts.allowAutoplay !== false
  const tbody = byId('list')
  const tr = document.createElement('tr')
  tr.dataset.jobId = jobId || ''
  tr.dataset.text = text
  tr.dataset.voice = voice || ''

  const tdTime = document.createElement('td')
  tdTime.textContent = new Date().toLocaleTimeString()
  const tdText = document.createElement('td')
  tdText.textContent = text
  const tdVP = document.createElement('td')
  tdVP.textContent = `${voice || '(auto)'} / ${byId('preset').value || '(none)'}`
  const tdStat = document.createElement('td')
  tdStat.textContent = jobId ? 'queued' : 'ready'
  const tdAct = document.createElement('td')
  tdAct.innerHTML = `
    <button data-action="play-text">play</button>
    <button data-action="remove-row">remove</button>
    ${jobId ? '<button data-action="delete-job">delete</button>' : ''}
  `
  tr.append(tdTime, tdText, tdVP, tdStat, tdAct)
  tbody.prepend(tr)
  if (allowAutoplay && byId('autoplay').checked) playText(text, voice, tdStat)
}

async function pollQueue() {
  if (!byId('pollq').checked) return setTimeout(pollQueue, POLL_IDLE_MS)
  try {
    const { items } = await api.queue.list()
    const shown = new Set([...byId('list').querySelectorAll('tr')].map((tr) => tr.dataset.jobId))
    for (const job of items) {
      if (!job.text || shown.has(job.id)) continue
      const v = job.voice || byId('voices').value || null
      await addRow(job.text, v, job.id, { allowAutoplay: false })
    }
  } catch { }
  setTimeout(pollQueue, POLL_ACTIVE_MS)
}

document.addEventListener('click', async (e) => {
  const a = e.target.dataset.action
  if (!a) return
  const row = e.target.closest('tr')

  try {
    switch (a) {
      case 'logout':
        BLOCK_SHOWN = false
        await api.panel.logout()
        await getPanelStatus()
        await Promise.all([loadVoices(), loadSounds()])
        break
      case 'alias-add': {
        const name = byId('alias_name').value.trim().toLowerCase()
        const voice = byId('alias_target').value
        if (!name || !voice) return
        await api.aliases.add(name, voice)
        byId('alias_name').value = ''
        await loadVoices()
        break
      }
      case 'alias-del':
        await api.aliases.del(e.target.dataset.term)
        await loadVoices()
        break
      case 'profile-del':
        await api.profiles.del(e.target.dataset.term)
        await loadVoices()
        break
      case 'sfx-del':
        await api.sfxAliases.del(e.target.dataset.term)
        await loadSounds()
        break
      case 'block-add': {
        const term = byId('block_term').value.trim()
        if (!term) return
        await api.mod.add(term)
        byId('block_term').value = ''
        await loadBlocklist()
        break
      }
      case 'block-remove':
        await api.mod.remove(e.target.dataset.term)
        await loadBlocklist()
        break
      case 'block-reveal':
        BLOCK_SHOWN = !BLOCK_SHOWN
        renderBlocklist()
        break
      case 'block-reload':
        await api.mod.reload()
        await loadBlocklist()
        break
      case 'profile-add': {
        const name = byId('profile_name').value.trim().toLowerCase()
        const voice = byId('profile_target').value
        const fx = byId('profile_fx').value.trim()
        if (!name || !voice) return
        await api.profiles.add({ name, voice, fx: fx || null })
        byId('profile_name').value = ''
        byId('profile_fx').value = ''
        await loadVoices()
        break
      }
      case 'sfx-add': {
        const name = byId('sfx_name').value.trim().toLowerCase()
        const target = byId('sfx_target').value
        if (!name || !target) return
        await api.sfxAliases.add(name, target)
        byId('sfx_name').value = ''
        await loadSounds()
        break
      }
      case 'refresh':
        Promise.all([loadVoices(), loadSounds()])
        break
      case 'play-text':
        await playText(row.dataset.text, row.dataset.voice, row.children[3])
        break
      case 'remove-row':
        row.remove()
        break
      case 'delete-job':
        if (row.dataset.jobId) await api.queue.del(row.dataset.jobId)
        row.remove()
        break
      case 'submit-text': {
        const t = byId('tts').value.trim()
        const v = byId('voices').value || null
        if (!t || byId('submit').disabled) return
        await addRow(t, v, null)
        byId('tts').value = ''
        byId('tts').focus()
        break
      }
      case 'mint-token':
        await handleMintToken()
        break
      case 'map-add': {
        const remote = byId('map_remote').value.trim()
        if (!remote) return
        await api.auth.mapping({
          provider: byId('map_provider').value,
          remote,
          role: byId('map_role').value
        })
        byId('map_remote').value = ''
        await loadMappings()
        break
      }
      case 'map-del':
        await api.auth.delMapping(e.target.dataset.prov, e.target.dataset.term)
        await loadMappings()
        break
      case 'login-oauth':
        window.location.href = '/api/auth/oauth/twitch'
        break
    }
  } catch (err) {
    console.error(err)
    alert(err.message)
  }
})

document.addEventListener('change', (e) => {
  const a = e.target.dataset.action
  if (!a) return
  const auto = byId('autoplay')
  const poll = byId('pollq')
  if (a === 'toggle-autoplay' && auto.checked) poll.checked = false
  if (a === 'toggle-poll' && poll.checked) auto.checked = false
  if (a === 'censor-mode') {
    api.mod.setMode(e.target.value).catch((err) => {
      console.error(err)
      alert(err.message)
    })
  }
  if (a === 'censor-toggle') {
    const on = e.target.checked
    setCensoringUi(on)
    api.mod.setCensoring(on).catch((err) => {
      console.error(err)
      alert(err.message)
      setCensoringUi(!on)
    })
  }
})

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch { }

  // the clipboard api is https only so fall back without revealing the text
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.left = '-9999px'
  document.body.appendChild(ta)
  ta.select()

  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch { }

  ta.remove()
  return ok
}

function addEmbedRow(url, expires) {
  const full = new URL(url, location.origin).href
  const eid = new URL(full).searchParams.get('embed') || ''
  const row = document.createElement('div')

  const label = document.createElement('span')
  label.textContent = location.origin + '/api/overlay?embed=' + (eid ? eid.slice(0, 6) + '***' : '***')
  label.textContent += expires ? ' exp ' + new Date(expires * 1000).toLocaleString() : ' no expiry'

  const btn = document.createElement('button')
  btn.textContent = 'copy'
  btn.style.marginLeft = '6px'
  btn.addEventListener('click', async () => {
    const ok = await copyText(full)
    btn.textContent = ok ? 'copied' : 'copy failed'
    setTimeout(() => (btn.textContent = 'copy'), 1200)
  })

  const del = document.createElement('button')
  del.textContent = 'delete'
  del.style.marginLeft = '4px'
  del.addEventListener('click', async () => {
    del.disabled = true
    try {
      await api.overlay.del(eid)
      row.remove()
    } catch (err) {
      del.disabled = false
      console.error(err)
      alert(err.message)
    }
  })

  row.append(label, btn, del)
  byId('mint_result').appendChild(row)
}

async function loadEmbeds() {
  const out = byId('mint_result')
  out.innerHTML = ''

  try {
    const j = await api.overlay.embeds()
    for (const e of j.embeds || []) {
      if (e.revoked) continue
      addEmbedRow(e.url, e.expires)
    }
  } catch (err) {
    console.error(err)
  }
}

async function handleMintToken() {
  const ttl = parseInt(byId('token_ttl').value || '3600', 10)
  const originElem = byId('token_origin')
  const originVal = originElem ? originElem.value.trim() || null : null
  try {
    const res = await api.overlay.embed({ ttl, origin: originVal })
    addEmbedRow(res.url || '/api/overlay?embed=' + res.embed_id, res.expires)
  } catch (err) {
    const errEl = document.createElement('div')
    errEl.style.color = 'red'
    errEl.textContent = 'error: ' + err.message
    byId('mint_result').appendChild(errEl)
  }
}

async function loadMappings() {
  const host = byId('maplist')
  if (!host) return

  try {
    const { mappings } = await api.auth.mappings()
    host.innerHTML = ''
    for (const [prov, entries] of Object.entries(mappings || {})) {
      for (const [remote, role] of Object.entries(entries || {})) {
        const chip = document.createElement('span')
        chip.className = 'chip'
        chip.textContent = `${prov}:${remote} \u2192 ${role}`
        const x = document.createElement('button')
        x.dataset.action = 'map-del'
        x.dataset.prov = prov
        x.dataset.term = remote
        x.textContent = '\u00d7'
        chip.appendChild(x)
        host.appendChild(chip)
      }
    }
  } catch { }
}

getPanelStatus()
  .then(() => Promise.all([loadVoices(), loadSounds()]))
  .then(pollQueue)
