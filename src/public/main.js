import { api } from "./api.js";

let ALIAS_SET = new Set();
let SFX_MAP = {};

function byId(id) {
  return document.getElementById(id);
}
function numOrNull(id) {
  const v = byId(id)?.value?.trim() ?? "";
  if (v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function payload(text, voice) {
  return {
    text,
    voice: voice || null,
    preset: byId("preset")?.value || null,
    length_scale: numOrNull("length_scale"),
    noise_w: numOrNull("noise_w"),
    sentence_silence: numOrNull("sentence_silence"),
    speaker_id: numOrNull("speaker_id"),
  };
}

async function getPanelStatus() {
  const s = await api.panel.status();
  byId("auth_status").textContent = `admin=${s.admin ? "on" : "off"} | mod=${
    s.mod ? "on" : "off"
  }`;
  const canTts = !!(s.admin || s.mod || s.tts);
  byId("submit").disabled = !canTts;
  byId("alias_admin").style.display = s.admin ? "block" : "none";
  const ta = byId("token_admin");
  if (ta) ta.style.display = s.admin ? "block" : "none";
  const oa = byId("oauth_admin");
  if (oa) oa.style.display = s.admin ? "block" : "none";
  byId("pollq").disabled = !(s.admin || s.mod);
  if (byId("pollq").disabled) byId("pollq").checked = false;
  return s;
}

async function loadVoices() {
  const sel = byId("voices");
  const keep = sel.value;
  sel.innerHTML = "";

  let voices = [];
  let aliases = {};
  try {
    voices = await api.voices();
  } catch {}
  try {
    aliases = await api.aliases.list();
  } catch {}

  if (Object.keys(aliases).length) {
    const ogA = document.createElement("optgroup");
    ogA.label = "aliases";
    for (const [n, t] of Object.entries(aliases)) {
      const o = document.createElement("option");
      o.value = n;
      o.textContent = `${n} → ${t}`;
      ogA.appendChild(o);
    }
    sel.appendChild(ogA);
  }

  if (voices.length) {
    const ogV = document.createElement("optgroup");
    ogV.label = "voices";
    for (const v of voices) {
      const o = document.createElement("option");
      o.value = v.id;
      o.textContent = v.id;
      ogV.appendChild(o);
    }
    sel.appendChild(ogV);
  }

  if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  else if (sel.options.length) sel.selectedIndex = 0;

  const tgt = byId("alias_target");
  if (tgt) {
    const keep2 = tgt.value;
    tgt.innerHTML = "";
    for (const v of voices) {
      const o = document.createElement("option");
      o.value = v.id;
      o.textContent = v.id;
      tgt.appendChild(o);
    }
    if ([...tgt.options].some((o) => o.value === keep2)) tgt.value = keep2;
  }

  ALIAS_SET = new Set([
    ...Object.keys(aliases).map((s) => s.toLowerCase()),
    ...voices.map((v) => v.id.toLowerCase()),
  ]);
}

async function loadSounds() {
  SFX_MAP = {};
  try {
    const { index, aliases } = await api.sounds();
    for (const [id, v] of Object.entries(index))
      SFX_MAP[id.toLowerCase()] = "/sounds/" + v.file;
    for (const [name, target] of Object.entries(aliases || {})) {
      const tgt = index[target];
      if (tgt) SFX_MAP[name.toLowerCase()] = "/sounds/" + tgt.file;
    }
  } catch {}
}

function parseParts(input, fallbackVoice) {
  const reVoice = /(^|\s)([a-z0-9_]+):\s*/gi;
  const reSfx = /\[sfx:([a-z0-9_]+)\]/gi;
  const parts = [];
  let curVoice = fallbackVoice || null;
  let i = 0,
    m,
    sfxCount = 0;

  function pushText(chunk) {
    if (!chunk) return;
    reSfx.lastIndex = 0;
    let pos = 0,
      sm;
    while ((sm = reSfx.exec(chunk)) !== null) {
      const t0 = chunk.slice(pos, sm.index);
      if (t0.trim())
        parts.push({ type: "tts", text: t0.trim(), voice: curVoice });
      const key = sm[1].toLowerCase();
      if (SFX_MAP[key] && sfxCount < 10) {
        parts.push({ type: "sfx", name: key });
        sfxCount += 1;
      } else parts.push({ type: "tts", text: sm[0], voice: curVoice });
      pos = reSfx.lastIndex;
    }
    const tail = chunk.slice(pos);
    if (tail.trim())
      parts.push({ type: "tts", text: tail.trim(), voice: curVoice });
  }

  while ((m = reVoice.exec(input)) !== null) {
    const segStart = m.index + m[1].length;
    if (segStart > i) pushText(input.slice(i, segStart));
    curVoice = m[2].toLowerCase();
    i = reVoice.lastIndex;
  }
  pushText(input.slice(i));
  return parts.length
    ? parts
    : [{ type: "tts", text: input, voice: fallbackVoice || null }];
}

async function playText(fullText, fallbackVoice, statusEl) {
  const a = byId("player");
  const parts = parseParts(fullText, fallbackVoice);
  const single = parts.length === 1 && parts[0].type !== "sfx";
  statusEl.textContent = single ? "playing..." : "rendering...";

  try {
    const body = single
      ? payload(parts[0].text, parts[0].voice)
      : {
          parts: parts.map((p) =>
            p.type === "sfx"
              ? { sfx: p.name }
              : { text: p.text, voice: p.voice || null }
          ),
          format: "mp3",
          preset: byId("preset")?.value || null,
          length_scale: numOrNull("length_scale"),
          noise_w: numOrNull("noise_w"),
          sentence_silence: numOrNull("sentence_silence"),
          speaker_id: numOrNull("speaker_id"),
        };
    const res = single ? await api.tts(body) : await api.ttsBatch(body);
    // res: { arrayBuffer, contentType }
    const blobObj = new Blob([res.arrayBuffer], { type: res.contentType });
    const url = URL.createObjectURL(blobObj);
    a.src = url;
    await a.play();
    statusEl.textContent = "done";
  } catch (err) {
    console.error(err);
    statusEl.textContent = "error";
  }
}

async function addRow(text, voice, jobId, opts = {}) {
  const allowAutoplay = opts.allowAutoplay !== false;
  const tbody = byId("list");
  const tr = document.createElement("tr");
  tr.dataset.jobId = jobId || "";
  tr.dataset.text = text;
  tr.dataset.voice = voice || "";

  const tdTime = document.createElement("td");
  tdTime.textContent = new Date().toLocaleTimeString();
  const tdText = document.createElement("td");
  tdText.textContent = text;
  const tdVP = document.createElement("td");
  tdVP.textContent = `${voice || "(auto)"} / ${
    byId("preset").value || "(none)"
  }`;
  const tdStat = document.createElement("td");
  tdStat.textContent = jobId ? "queued" : "ready";
  const tdAct = document.createElement("td");
  tdAct.innerHTML = `
    <button data-action="play-text">play</button>
    <button data-action="remove-row">remove</button>
    ${jobId ? '<button data-action="delete-job">delete</button>' : ""}
  `;
  tr.append(tdTime, tdText, tdVP, tdStat, tdAct);
  tbody.prepend(tr);
  if (allowAutoplay && byId("autoplay").checked) playText(text, voice, tdStat);
}

async function pollQueue() {
  if (!byId("pollq").checked) return setTimeout(pollQueue, 600);
  try {
    const job = await api.queue.peek();
    const v = job.voice || byId("voices").value || null;
    const t = job.text || "";
    const id = job.id || null;
    if (t) await addRow(t, v, id, { allowAutoplay: false });
  } catch {}
  setTimeout(pollQueue, 400);
}

document.addEventListener("click", async (e) => {
  const a = e.target.dataset.action;
  if (!a) return;
  const row = e.target.closest("tr");

  try {
    switch (a) {
      case "login-admin": {
        const key = byId("key_admin").value.trim();
        if (key) await api.panel.login("admin", key);
        await getPanelStatus();
        await Promise.all([loadVoices(), loadSounds()]);
        break;
      }
      case "logout":
        await api.panel.logout();
        await getPanelStatus();
        await Promise.all([loadVoices(), loadSounds()]);
        break;
      case "alias-add": {
        const name = byId("alias_name").value.trim().toLowerCase();
        const voice = byId("alias_target").value;
        if (!name || !voice) return;
        await api.aliases.add(name, voice);
        byId("alias_name").value = "";
        await loadVoices();
        break;
      }
      case "refresh":
        Promise.all([loadVoices(), loadSounds()]);
        break;
      case "play-text":
        await playText(row.dataset.text, row.dataset.voice, row.children[3]);
        break;
      case "remove-row":
        row.remove();
        break;
      case "delete-job":
        if (row.dataset.jobId) await api.queue.del(row.dataset.jobId);
        row.remove();
        break;
      case "submit-text": {
        const t = byId("tts").value.trim();
        const v = byId("voices").value || null;
        if (!t || byId("submit").disabled) return;
        await addRow(t, v, null);
        byId("tts").value = "";
        byId("tts").focus();
        break;
      }
      case "mint-token":
        await handleMintToken();
        break;
      case "oauth-refresh":
        await handleOAuthWhoami();
        break;
      case "login-oauth":
        window.location.href = "/api/auth/login?provider=twitch";
        break;
    }
  } catch (err) {
    console.error(err);
    alert(err.message);
  }
});

document.addEventListener("change", (e) => {
  const a = e.target.dataset.action;
  if (!a) return;
  const auto = byId("autoplay");
  const poll = byId("pollq");
  if (a === "toggle-autoplay" && auto.checked) poll.checked = false;
  if (a === "toggle-poll" && poll.checked) auto.checked = false;
});

async function handleMintToken() {
  const ttl = parseInt(byId("token_ttl").value || "3600", 10);
  const roles = JSON.parse(byId("token_roles").value);
  const originElem = byId("token_origin");
  const originVal = originElem ? originElem.value.trim() || null : null;
  const out = byId("mint_result");
  try {
    const res = await api.overlay.embed({ ttl, roles, origin: originVal });
    const embed = location.origin + res.url;
    const eid =
      res.embed_id ||
      new URL(res.url, location.origin).searchParams.get("embed");
    const masked =
      location.origin +
      "/api/overlay?embed=" +
      (eid ? eid.slice(0, 6) + "***" : "***");
    const entry = document.createElement("div");
    entry.textContent = masked;
    out.appendChild(entry);
  } catch (err) {
    const errEl = document.createElement("div");
    errEl.style.color = "red";
    errEl.textContent = "error: " + err.message;
    out.appendChild(errEl);
  }
}

async function handleOAuthWhoami() {
  const div = byId("oauth_whoami");
  div.textContent = "Loading...";
  try {
    const j = await api.auth.whoami("twitch");
    if (!j.ok) {
      div.textContent = "no oauth user in session";
      return;
    }
    div.innerHTML = `provider=${j.provider} id=${j.id} login=${j.login}`;
  } catch {
    div.textContent = "error fetching whoami";
  }
}

getPanelStatus()
  .then(() => Promise.all([loadVoices(), loadSounds()]))
  .then(pollQueue);
