// Notification gateway for the GTA 6 PC release checker (Cloudflare Worker).
//
// Why it exists: registering a device means starting the "push.yml" workflow on GitHub,
// and GitHub only allows that with a key. A key inside a public web page is not secret,
// so the page talks to THIS worker instead. The worker keeps the key, and it can only do
// three things: subscribe, unsubscribe, test. It can never start check.yml (that stays
// behind your password in the developer options).
//
// Settings (Worker > Settings > Variables and secrets):
//   GH_TOKEN        (Secret)   GitHub fine-grained token, ONLY this repo, permission "Actions: Read and write"
//   REPO            (Text)     yourname/your-repo
//   ALLOWED_ORIGIN  (Text)     https://yourname.github.io      (no trailing slash, no path)
//   REF             (Text, optional)  branch name, default "main"
// Optional: bind a KV namespace named RATE to turn on per-visitor limits (recommended).

const PUSH_HOSTS = [
  /^fcm\.googleapis\.com$/,                 // Chrome, Edge, Android, Samsung
  /(^|\.)push\.services\.mozilla\.com$/,    // Firefox
  /(^|\.)push\.apple\.com$/,                // Safari / iPhone
  /(^|\.)notify\.windows\.com$/,            // Windows / Edge
];

// per visitor, per hour
const LIMITS = { subscribe: 12, unsubscribe: 12, test: 3 };

function b64uLen(s) {
  try {
    const p = String(s).replace(/-/g, "+").replace(/_/g, "/");
    return atob(p + "=".repeat((4 - (p.length % 4)) % 4)).length;
  } catch { return -1; }
}

function cleanSubscription(sub) {
  if (!sub || typeof sub !== "object") return null;
  let url;
  try { url = new URL(sub.endpoint); } catch { return null; }
  if (url.protocol !== "https:" || !PUSH_HOSTS.some((re) => re.test(url.hostname))) return null;
  if (String(sub.endpoint).length > 1000) return null;
  const k = sub.keys || {};
  if (b64uLen(k.p256dh) !== 65 || b64uLen(k.auth) !== 16) return null;
  return { endpoint: sub.endpoint, keys: { p256dh: k.p256dh, auth: k.auth } };
}

function cleanEndpoint(ep) {
  if (typeof ep !== "string" || ep.length > 1000) return null;
  try {
    const u = new URL(ep);
    if (u.protocol !== "https:" || !PUSH_HOSTS.some((re) => re.test(u.hostname))) return null;
  } catch { return null; }
  return ep;
}

function cleanPrefs(p) {
  p = p && typeof p === "object" ? p : {};
  return { official: p.official !== false, released: p.released !== false, rumor: p.rumor === true };
}

// total across ALL visitors, per hour. Stops spam that rotates IP addresses.
// Raise these numbers if real users ever start hitting "slow down".
const GLOBAL_LIMITS = { subscribe: 60, unsubscribe: 60, test: 30 };

async function bump(env, key, limit) {
  const n = parseInt((await env.RATE.get(key)) || "0", 10);
  if (n >= limit) return true;                       // over the limit: no write at all
  await env.RATE.put(key, String(n + 1), { expirationTtl: 7200 });
  return false;
}

async function overLimit(env, request, action) {
  if (!env.RATE) return false;                       // no KV bound: no limit
  try {
    const hour = Math.floor(Date.now() / 3600000);
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";
    if (await bump(env, `rl:${action}:${ip}:${hour}`, LIMITS[action])) return true;
    if (await bump(env, `rl:all:${action}:${hour}`, GLOBAL_LIMITS[action])) return true;
    return false;
  } catch {
    return true;                                     // KV broken or out of quota: refuse (protects GitHub)
  }
}

async function dispatchPush(env, inputs) {
  const r = await fetch(`https://api.github.com/repos/${env.REPO}/actions/workflows/push.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "gta6-push-gateway",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.REF || "main", inputs }),
  });
  return r.status === 204;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const cors = {
      "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      Vary: "Origin",
    };
    const reply = (status, text) => new Response(text || "", { status, headers: cors });

    if (request.method === "OPTIONS") return reply(204);
    if (request.method !== "POST") return reply(405, "POST only");
    // Stops other websites from using this worker from a visitor's browser.
    // (Scripts can fake this header: the per-visitor limits above are what hold those back.)
    if (!env.ALLOWED_ORIGIN || origin !== env.ALLOWED_ORIGIN) return reply(403, "not allowed");
    if (!env.GH_TOKEN || !env.REPO) return reply(500, "worker is not configured");

    const action = new URL(request.url).pathname.replace(/^\/+|\/+$/g, "");
    if (!LIMITS[action]) return reply(404, "unknown action");

    const raw = await request.text();
    if (raw.length > 6000) return reply(413, "too big");
    let body;
    try { body = JSON.parse(raw); } catch { return reply(400, "bad json"); }

    if (await overLimit(env, request, action)) return reply(429, "slow down");

    let inputs;
    if (action === "subscribe") {
      const sub = cleanSubscription(body.subscription);
      if (!sub) return reply(400, "bad subscription");
      inputs = {
        action: "subscribe",
        subscription: JSON.stringify(sub),
        prefs: JSON.stringify(cleanPrefs(body.prefs)),
        label: String(body.label || "").slice(0, 60),
      };
    } else {
      const endpoint = cleanEndpoint(body.endpoint);
      if (!endpoint) return reply(400, "bad endpoint");
      inputs = action === "test"
        ? { action: "test", endpoint, delay: "15" }
        : { action: "unsubscribe", endpoint };
    }

    const ok = await dispatchPush(env, inputs);
    return ok ? reply(202, "started") : reply(502, "GitHub refused");
  },
};
