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
  if (!r.ok) throw new Error(`POST ${url} -> ${r.status}`)
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
  panel: {
    status: () => jget('/api/auth/me'),
    logout: () => jpost('/api/auth/logout', {})
  },
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
    state: () => jget('/api/admin/mod'),
    setMode: (mode) => jpost('/api/admin/mod', { mode }),
    setCensoring: (censoring) => jpost('/api/admin/mod', { censoring }),
    add: (term) => jpost('/api/admin/mod', { add: [term] }),
    remove: (term) => jpost('/api/admin/mod', { remove: [term] }),
    reload: () => jpost('/api/admin/mod', { reload: true })
  },
  reload: () => jpost('/api/admin/reload', {}),
  tts: (body) => postBinary('/api/tts', body),
  queue: {
    list: () => jget('/api/queue'),
    del: (id) => jdel(`/api/queue/${esc(id)}`)
  },
  overlay: {
    embed: (body) => jpost('/api/admin/embeds', body),
    embeds: () => jget('/api/admin/embeds'),
    del: (id) => jdel(`/api/admin/embeds/${esc(id)}`)
  },
  auth: {
    mappings: () => jget('/api/admin/mappings'),
    mapping: (body) => jput(`/api/admin/mappings/${esc(body.provider)}/${esc(body.remote)}`, { role: body.role }),
    delMapping: (prov, remote) => jdel(`/api/admin/mappings/${esc(prov)}/${esc(remote)}`)
  }
}
