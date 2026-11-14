// eSmart test client.
//
//   node esmart-test.mjs register          create a fresh XMPP test account (saved to .esmart-test.json)
//   node esmart-test.mjs serve [port]      stay logged in, local web UI on http://127.0.0.1:8099 (default)
//                                          to enter the pairing code; full XMPP traffic -> logs/
//   node esmart-test.mjs listen [secs]     log in, join the home room, GET structure/config/…, print changes
//   node esmart-test.mjs unregister        delete the test account on the server
//
// SAFETY (see .claude/CLAUDE.md): sendToHome() allows GET always, and CMD/operation only for node ids
// listed in .esmart-allow.json. Scenes and tablet operations are always blocked.

import xmppClient from './xmpp-client.cjs'
import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import tls from 'node:tls'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const { client, xml } = xmppClient
const HERE = path.dirname(fileURLToPath(import.meta.url))
const CRED_FILE = path.join(HERE, '.esmart-test.json')
const ALLOW_FILE = path.join(HERE, '.esmart-allow.json')
const CAPTURE_DIR = path.join(HERE, 'captures')
const LOG_DIR = path.join(HERE, 'logs')
const HOST = 'xmpp.myesmart.net'
const DOMAIN = 'myesmart.net'
const PAIRING_ROOM = `shared@conference.${DOMAIN}`
const DEVICE_NAME = 'homebridge-esmart test'

fs.mkdirSync(LOG_DIR, { recursive: true })
const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
const EVENT_LOG = path.join(LOG_DIR, `events-${stamp}.log`)
const RAW_LOG = path.join(LOG_DIR, `xmpp-${stamp}.log`)
const recent = []

const ts = () => new Date().toISOString().slice(11, 19)
function log(...a) {
  const line = `${ts()} ${a.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))).join(' ')}`
  console.log(line)
  fs.appendFileSync(EVENT_LOG, line + '\n')
  recent.push(line)
  if (recent.length > 300) recent.shift()
}
const maskAuth = (d) => d.replace(/(<auth\b[^>]*>)[^<]*/g, '$1***').replace(/(<response\b[^>]*>)[^<]*/g, '$1***').replace(/(<password>)[^<]*/g, '$1***')
const raw = (dir, d) => fs.appendFileSync(RAW_LOG, `${new Date().toISOString()} ${dir} ${maskAuth(d)}\n`)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function loadCreds() {
  if (!fs.existsSync(CRED_FILE)) throw new Error('No test account yet. Run: node esmart-test.mjs register')
  return JSON.parse(fs.readFileSync(CRED_FILE, 'utf8'))
}
function allowedNodes() {
  try { return new Set((JSON.parse(fs.readFileSync(ALLOW_FILE, 'utf8')).nodes ?? []).map(Number)) } catch { return new Set() }
}

// ---------------------------------------------------------------- registration (XEP-0077 over raw STARTTLS)

function rawSession() {
  let sock = net.connect(5222, HOST)
  let buf = ''
  const onData = (d) => { buf += d.toString() }
  sock.on('data', onData)
  const until = (re, ms = 10000) => new Promise((resolve, reject) => {
    const t0 = Date.now()
    const tick = () => {
      const m = buf.match(re)
      if (m) { buf = buf.slice(m.index + m[0].length); return resolve(m[0]) }
      if (Date.now() - t0 > ms) return reject(new Error(`timeout waiting for ${re}; got: ${buf.slice(0, 500)}`))
      setTimeout(tick, 50)
    }
    tick()
  })
  const header = `<?xml version='1.0'?><stream:stream to='${DOMAIN}' xmlns='jabber:client' xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>`
  return {
    write: (s) => sock.write(s),
    until,
    async starttls() {
      sock.write(header)
      await until(/<\/stream:features>/)
      sock.write(`<starttls xmlns='urn:ietf:params:xml:ns:xmpp-tls'/>`)
      await until(/<proceed[^>]*\/>/)
      sock.off('data', onData)
      buf = ''
      sock = tls.connect({ socket: sock, servername: DOMAIN })
      await new Promise((r, j) => { sock.once('secureConnect', r); sock.once('error', j) })
      sock.on('data', onData)
      sock.write(header)
      return until(/<\/stream:features>/)
    },
    end: () => { try { sock.write('</stream:stream>'); sock.end() } catch {} },
  }
}

async function register() {
  if (fs.existsSync(CRED_FILE)) throw new Error(`${CRED_FILE} already exists. Unregister or delete it first.`)
  const username = `w-${crypto.randomBytes(7).toString('hex').slice(0, 13)}-${10 + crypto.randomInt(90)}`
  const password = crypto.randomBytes(16).toString('hex')
  const s = rawSession()
  try {
    await s.starttls()
    s.write(`<iq type='get' id='reg1' to='${DOMAIN}'><query xmlns='jabber:iq:register'/></iq>`)
    const form = await s.until(/<iq[^>]*id=['"]reg1['"][\s\S]*?<\/iq>/)
    if (/type=['"]error/.test(form)) throw new Error('server refused registration form')
    s.write(`<iq type='set' id='reg2' to='${DOMAIN}'><query xmlns='jabber:iq:register'><username>${username}</username><password>${password}</password></query></iq>`)
    const res = await s.until(/<iq[^>]*id=['"]reg2['"][\s\S]*?(<\/iq>|\/>)/)
    if (/type=['"]error/.test(res)) throw new Error(`registration failed: ${res}`)
    const creds = { jid: `${username}@${DOMAIN}`, password, created: new Date().toISOString() }
    fs.writeFileSync(CRED_FILE, JSON.stringify(creds, null, 2) + '\n', { mode: 0o600 })
    log(`registered ${creds.jid} (password saved to ${CRED_FILE})`)
  } finally {
    s.end()
  }
}

// ---------------------------------------------------------------- logged-in session

function session({ onReady } = {}) {
  const { jid, password } = loadCreds()
  const [username] = jid.split('@')
  const nick = username
  const xmpp = client({ service: `xmpp://${HOST}:5222`, domain: DOMAIN, username, password, resource: `spike${crypto.randomInt(1e6)}` })
  const state = { status: 'offline', room: null, nodes: new Map(), bare: jid, roster: [], paired: false, lastData: null, events: [] }

  xmpp.on('input', (d) => raw('IN ', d))
  xmpp.on('output', (d) => raw('OUT', d))
  xmpp.on('error', (e) => log('xmpp error:', e.condition || e.message))
  xmpp.on('status', (s) => { state.status = s; log('status:', s) })

  function sendToHome(method, type, body) {
    if (method === 'CMD') {
      const allow = allowedNodes()
      if (type !== 'operation' || !allow.has(Number(body?.id))) {
        throw new Error(`BLOCKED: CMD/${type} for node ${body?.id} is not in .esmart-allow.json (see .claude/CLAUDE.md)`)
      }
    } else if (method !== 'GET') {
      throw new Error(`BLOCKED: method ${method}`)
    }
    if (!state.room) throw new Error('not in home room yet')
    const payload = { headers: { version: '1.2.0', from: state.bare, to: 'master', timestamp: new Date(), method, type }, body: body ?? {} }
    payload.headers.size = JSON.stringify(payload.body).length
    log(`-> ${method}/${type} ${JSON.stringify(payload.body)}`)
    return xmpp.send(xml('message', { to: state.room, type: 'groupchat' }, xml('body', {}, JSON.stringify(payload))))
  }

  const joinRoom = (room) => xmpp.send(
    xml('presence', { to: `${room}/${nick}` },
      xml('x', { xmlns: 'http://jabber.org/protocol/muc' }, xml('history', { maxstanzas: '0', seconds: '0' }))))
  const leaveRoom = (room) => xmpp.send(xml('presence', { to: `${room}/${nick}`, type: 'unavailable' }))

  async function refreshRoster() {
    const res = await xmpp.iqCaller.request(xml('iq', { type: 'get' }, xml('query', { xmlns: 'jabber:iq:roster' })))
    state.roster = res.getChild('query').getChildren('item').map((i) => ({ jid: i.attrs.jid, subscription: i.attrs.subscription, name: i.attrs.name }))
    state.paired = state.roster.some((r) => r.subscription === 'both')
    log('roster:', state.roster.length ? state.roster : '(empty, not paired yet)')
  }

  async function sendPairCode(code) {
    const n = parseInt(String(code).trim(), 10)
    if (!Number.isFinite(n)) throw new Error('code must be numeric')
    log(`pairing: joining ${PAIRING_ROOM}, sending code ${n}`)
    await joinRoom(PAIRING_ROOM)
    await sleep(500)
    const payload = { jid: state.bare, code: n, name: DEVICE_NAME }
    await xmpp.send(xml('message', { to: PAIRING_ROOM, type: 'groupchat' }, xml('body', {}, JSON.stringify(payload))))
    await sleep(500)
    await leaveRoom(PAIRING_ROOM)
    log('pairing: code sent, waiting for subscription request + invitation from the controller')
  }

  xmpp.on('online', async (address) => {
    log(`online as ${address}`)
    await xmpp.send(xml('presence'))
    await refreshRoster()
    if (state.room) { log(`rejoining known room ${state.room}`); await joinRoom(state.room) }
  })

  xmpp.on('stanza', async (st) => {
    if (st.is('presence')) {
      const type = st.attrs.type
      const from = st.attrs.from
      if (type === 'subscribe') {
        log(`subscription request from ${from} -> accepting (pairing handshake)`)
        await xmpp.send(xml('presence', { to: from, type: 'subscribed' }))
        setTimeout(() => refreshRoster().catch((e) => log('roster error', e.message)), 1000)
      } else if (type === 'subscribed' || type === 'unsubscribe' || type === 'unsubscribed') {
        log(`presence ${type} from ${from}`)
        setTimeout(() => refreshRoster().catch((e) => log('roster error', e.message)), 1000)
      } else if (type === 'error') {
        log(`presence error from ${from}: ${st.toString().slice(0, 300)}`)
      }
      return
    }
    if (!st.is('message')) return
    const type = st.attrs.type || 'normal'
    const from = st.attrs.from
    const bodyText = st.getChildText('body')
    if (type === 'normal' && bodyText != null) {
      const room = from.split('/')[0]
      log(`invitation from ${from} (body: ${bodyText.slice(0, 100)}) -> joining home room`)
      const first = state.room !== room
      state.room = room
      const creds = loadCreds()
      if (creds.room !== room) fs.writeFileSync(CRED_FILE, JSON.stringify({ ...creds, room }, null, 2) + '\n', { mode: 0o600 })
      await joinRoom(room)
      if (first) setTimeout(() => onReady?.(state, sendToHome), 1000)
      return
    }
    if (type === 'groupchat' && bodyText) {
      const [room, resource] = from.split('/')
      if (resource === nick) return
      let msg
      try { msg = JSON.parse(bodyText) } catch { log(`non-JSON groupchat from ${from}: ${bodyText.slice(0, 200)}`); return }
      const to = msg.headers?.to
      if (to !== 'remotes' && to !== state.bare) return
      handleInfo(room, msg)
      return
    }
    if (type === 'error') log(`message error from ${from}: ${st.toString().slice(0, 300)}`)
    else if (type === 'chat' && bodyText) log(`chat from ${from}: ${bodyText.slice(0, 200)}`)
  })

  function handleInfo(room, msg) {
    const { method, type } = msg.headers
    const body = msg.body
    if (method === 'INFO' && type === 'data' && body?.nodes) {
      state.lastData = new Date()
      const changed = []
      for (const n of body.nodes) {
        const s = JSON.stringify(n.data)
        if (state.nodes.get(n.id) !== s) { changed.push(`${n.id}=${s}`); state.nodes.set(n.id, s) }
      }
      if (changed.length) log(`data: ${changed.length} node(s) changed: ${changed.join('  ')}`)
      capture('data-latest', body)
      return
    }
    log(`<- ${method}/${type}: ${JSON.stringify(body).slice(0, 300)}`)
    capture(type, msg)
  }

  return { xmpp, state, sendToHome, sendPairCode, refreshRoster }
}

function capture(name, obj) {
  fs.mkdirSync(CAPTURE_DIR, { recursive: true })
  fs.writeFileSync(path.join(CAPTURE_DIR, `${name}.json`), JSON.stringify(obj, null, 2))
}

const requestInfo = async (state, sendToHome) => {
  log('READY: requesting read-only info (GET)')
  for (const type of ['structure', 'capabilities', 'config', 'scene']) {
    await sendToHome('GET', type)
    await sleep(300)
  }
}

async function startSession() {
  const s = session({ onReady: requestInfo })
  try {
    await s.xmpp.start()
  } catch (e) {
    await s.xmpp.stop().catch(() => {})
    throw e
  }
  return s
}

async function listen(seconds) {
  const s = await startSession()
  await sleep(seconds * 1000)
  log(`done after ${seconds}s. nodes seen: ${s.state.nodes.size}. room: ${s.state.room ?? 'none'}`)
  await s.xmpp.stop()
}

// ---------------------------------------------------------------- local web UI

const PAGE = `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>eSmart test device</title>
<style>
 :root{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--card:#fff;--line:#e7e5e4;--acc:#2563eb;--ok:#16a34a;--bad:#dc2626}
 @media (prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--card:#292524;--line:#44403c;--acc:#60a5fa;--ok:#4ade80;--bad:#f87171}}
 body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px;max-width:860px;margin-inline:auto}
 .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:12px}
 h1{font-size:18px;margin:0 0 12px} dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0} dt{color:var(--mut)}
 form{display:flex;gap:8px} input{flex:1;font-size:22px;padding:8px 10px;letter-spacing:3px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
 button{font-size:16px;padding:8px 16px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer}
 pre{font:12px/1.4 ui-monospace,monospace;white-space:pre-wrap;word-break:break-all;max-height:50vh;overflow:auto;margin:0}
 .ok{color:var(--ok)} .bad{color:var(--bad)} #msg{margin-top:8px;min-height:1.4em}
</style>
<h1>eSmart test device</h1>
<div class=card><form id=f><input id=code inputmode=numeric autocomplete=off placeholder="pairing code" autofocus><button>Send code</button></form><div id=msg></div></div>
<div class=card><dl id=st></dl></div>
<div class=card><b>Allowed switches</b> <span style="color:var(--mut)">(from .esmart-allow.json)</span><div id=sw></div></div>
<div class=card><pre id=log></pre></div>
<script>
const $=id=>document.getElementById(id)
$('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/pair',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({code:$('code').value})});const j=await r.json();$('msg').textContent=j.ok?'Code sent. Watch the log for the subscription and invitation.':'Error: '+j.error;$('msg').className=j.ok?'ok':'bad';if(j.ok)$('code').value=''}
async function sw(id,onoff){const r=await fetch('/switch',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({id,onoff})});const j=await r.json();if(!j.ok)alert(j.error)}
async function poll(){try{const s=await (await fetch('/status')).json()
 const row=(k,v,c='')=>'<dt>'+k+'</dt><dd class="'+c+'" style="margin:0">'+v+'</dd>'
 $('st').innerHTML=row('Device',s.jid)+row('Connection',s.status,s.status==='online'?'ok':'bad')+row('Paired',s.paired?'yes':'no',s.paired?'ok':'bad')+row('Home room',s.room||'–')+row('Nodes seen',s.nodes)+row('Last data',s.lastData||'–')
 $('sw').innerHTML=s.allowed.length?s.allowed.map(n=>'<div style="display:flex;gap:8px;align-items:center;margin-top:8px"><span style="flex:1">Node '+n.id+': '+(n.data?n.data.onoff:'?')+'</span><button onclick="sw('+n.id+',\\'on\\')">On</button><button onclick="sw('+n.id+',\\'off\\')">Off</button></div>').join(''):'<div style="color:var(--mut);margin-top:8px">none</div>'
 const el=$('log'),stick=el.scrollTop+el.clientHeight>=el.scrollHeight-4;el.textContent=s.log.join('\\n');if(stick)el.scrollTop=el.scrollHeight}catch{}}
poll();setInterval(poll,1000)
</script>`

async function serve(port) {
  const s = await startSession()
  const server = http.createServer(async (req, res) => {
    const json = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)) }
    if (req.method === 'GET' && req.url === '/') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(PAGE) }
    if (req.method === 'GET' && req.url === '/status') {
      const st = s.state
      const allowed = [...allowedNodes()].map((id) => ({ id, data: JSON.parse(st.nodes.get(id) ?? 'null') }))
      return json(200, { jid: st.bare, status: st.status, paired: st.paired, room: st.room, nodes: st.nodes.size, lastData: st.lastData?.toISOString().slice(11, 19) ?? null, allowed, log: recent.slice(-150) })
    }
    if (req.method === 'POST' && req.url === '/switch') {
      let b = ''
      for await (const c of req) b += c
      try {
        const { id, onoff } = JSON.parse(b)
        if (onoff !== 'on' && onoff !== 'off') throw new Error('onoff must be "on" or "off"')
        await s.sendToHome('CMD', 'operation', { id: Number(id), onoff })
        return json(200, { ok: true })
      } catch (e) { log(e.message); return json(400, { ok: false, error: e.message }) }
    }
    if (req.method === 'POST' && req.url === '/pair') {
      let b = ''
      for await (const c of req) b += c
      try { await s.sendPairCode(JSON.parse(b).code); return json(200, { ok: true }) } catch (e) { return json(400, { ok: false, error: e.message }) }
    }
    res.writeHead(404); res.end()
  })
  server.listen(port, '127.0.0.1', () => log(`UI on http://127.0.0.1:${port}  events: ${EVENT_LOG}  raw xmpp: ${RAW_LOG}`))
  const shutdown = async () => { log('shutting down'); server.close(); await s.xmpp.stop().catch(() => {}); process.exit(0) }
  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)
}

async function unregister() {
  const s = session()
  s.xmpp.on('online', async () => {
    const res = await s.xmpp.iqCaller.request(xml('iq', { type: 'set' }, xml('query', { xmlns: 'jabber:iq:register' }, xml('remove'))))
    log('unregister result:', res.attrs.type)
  })
  await s.xmpp.start().catch(() => {})
  await sleep(3000)
  await s.xmpp.stop().catch(() => {})
  fs.renameSync(CRED_FILE, `${CRED_FILE}.removed-${Date.now()}`)
  log('account removed, local cred file renamed')
}

const [cmd, ...args] = process.argv.slice(2)
try {
  if (cmd === 'register') await register()
  else if (cmd === 'serve') await serve(+(args[0] ?? 8099))
  else if (cmd === 'listen') await listen(+(args[0] ?? 60))
  else if (cmd === 'unregister') await unregister()
  else console.log('usage: node esmart-test.mjs register | serve [port] | listen [secs] | unregister')
} catch (e) {
  log('ERROR:', e.message)
  process.exitCode = 1
}
