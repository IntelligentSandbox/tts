const cred = { credentials: 'same-origin' }

async function jget(url) {
  const r = await fetch(url, cred)
  if (!r.ok) throw new Error(`GET ${url} -> ${r.status}`)
  return await r.json()
}

async function jpost(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...cred
  })
  if (!r.ok) throw new Error(`POST ${url} -> ${r.status}`)
  return await r.json()
}

async function postBinary(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    ...cred
  })
  if (!r.ok) throw new Error(`POST ${url} -> ${r.status}`)
  const arrayBuffer = await r.arrayBuffer()
  const ct = r.headers.get('content-type') || 'application/octet-stream'
  return { arrayBuffer, contentType: ct }
}

async function jdel(url) {
  const r = await fetch(url, { method: 'DELETE', ...cred })
  if (!r.ok) throw new Error(`DELETE ${url} -> ${r.status}`)
  return true
}

export const api = {
  panel: {
    status: () => jget('/api/panel/status'),
    login: (role, key) => jpost('/api/panel/login', { role, key }),
    logout: () => jpost('/api/panel/logout', {})
  },
  voices: () => jget('/api/voices'),
  aliases: {
    list: () => jget('/api/aliases'),
    add: (name, voice) => jpost('/api/aliases', { name, voice })
  },
  sounds: () => jget('/api/sounds'),
  mod: {
    mode: () => jget('/api/mod/mode'),
    setMode: (mode) => jpost('/api/mod/mode', { mode })
  },
  tts: (body) => postBinary('/api/tts', body),
  ttsBatch: (body) => postBinary('/api/tts_batch', body),
  queue: {
    peek: () => jget('/api/peek'),
    del: (id) => jdel(`/api/queue/${encodeURIComponent(id)}`)
  },
  overlay: {
    embed: (body) => jpost('/api/overlay/embed', body),
    embeds: () => jget('/api/overlay/embeds'),
    del: (id) => jdel(`/api/overlay/embed/${encodeURIComponent(id)}`)
  },
  auth: {
    mappings: () => jget('/api/auth/mappings'),
    mapping: (body) => jpost('/api/auth/mapping', body),
    delMapping: (prov, remote) => jdel(`/api/auth/mapping/${encodeURIComponent(prov)}/${encodeURIComponent(remote)}`),
    whoami: (provider) => jget(`/api/auth/me?provider=${encodeURIComponent(provider)}`)
  }
}
