const cred = { credentials: 'same-origin' }

async function jget(url) {
  const r = await fetch(url, cred)
  if (!r.ok) throw new Error(`GET ${url} -> ${r.status}`)
  return await r.json()
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...cred
  })
  if (!r.ok) {
    const { detail } = await r.json().catch(() => ({}))
    throw new Error(detail || `POST ${url} -> ${r.status}`)
  }
  return r
}

async function jpost(url, body) {
  const r = await post(url, body)
  return await r.json()
}

async function postBinary(url, body) {
  const r = await post(url, body)
  const arrayBuffer = await r.arrayBuffer()
  const ct = r.headers.get('content-type') || 'application/octet-stream'
  return { arrayBuffer, contentType: ct }
}

async function jput(url, body) {
  const r = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
    ...cred
  })
  if (!r.ok) throw new Error(`PUT ${url} -> ${r.status}`)
  return await r.json()
}

async function jdel(url) {
  const r = await fetch(url, { method: 'DELETE', ...cred })
  if (!r.ok) throw new Error(`DELETE ${url} -> ${r.status}`)
  return true
}

const esc = encodeURIComponent

export const api = {
  catalog: () => jget('/api/catalog'),
  aliases: {
    add: (name, voice) => jput(`/api/catalog/voice_alias/${esc(name)}`, { voice }),
    del: (name) => jdel(`/api/catalog/voice_alias/${esc(name)}`)
  },
  profiles: {
    add: (body) => jput(`/api/catalog/profile/${esc(body.name)}`, body),
    del: (name) => jdel(`/api/catalog/profile/${esc(name)}`)
  },
  sfxAliases: {
    add: (name, target_id) => jput(`/api/catalog/sfx_alias/${esc(name)}`, { target_id }),
    del: (name) => jdel(`/api/catalog/sfx_alias/${esc(name)}`)
  },
  mod: {
    state: () => jget('/api/mod'),
    setMode: (mode) => jpost('/api/mod', { mode }),
    setCensoring: (censoring) => jpost('/api/mod', { censoring }),
    add: (term) => jpost('/api/mod', { add: [term] }),
    remove: (term) => jpost('/api/mod', { remove: [term] }),
    reload: () => jpost('/api/mod', { reload: true })
  },
  reload: () => jpost('/api/catalog/reload', {}),
  tts: (body) => postBinary('/api/tts', body)
}
