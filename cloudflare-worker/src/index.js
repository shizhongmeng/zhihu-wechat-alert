const DEFAULT_MAX_SEEN = 200;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/run") {
      return runAndRespond(env);
    }
    if (url.pathname === "/test") {
      await pushWxPusher(env, {
        title: "Cloudflare test",
        published: new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }),
        summary: "Cloudflare Worker cloud push test.",
        link: `https://www.zhihu.com/people/${required(env.ZHIHU_USER_TOKEN, "ZHIHU_USER_TOKEN")}`,
      });
      return Response.json({ ok: true, message: "Test push sent." });
    }
    if (url.pathname === "/health") {
      return Response.json({ ok: true, service: "zhihu-wechat-alert" });
    }
    return new Response("ok\nGET /run to check now\nGET /test to push a test message\nGET /health for status\n");
  },

  async scheduled(event, env, ctx) {
    console.log("scheduled event", event.cron, new Date().toISOString());
    ctx.waitUntil(run(env));
  },
};

async function runAndRespond(env) {
  try {
    return Response.json(await run(env));
  } catch (error) {
    return Response.json({ ok: false, error: String(error && error.message ? error.message : error) }, { status: 500 });
  }
}

async function run(env) {
  const token = required(env.ZHIHU_USER_TOKEN, "ZHIHU_USER_TOKEN");
  const stateKey = `zhihu:${token}:pins`;
  console.log("run start", stateKey);
  const state = (await env.ZHIHU_ALERT_KV.get(stateKey, "json")) || {
    initialized: false,
    seenIds: [],
  };

  const items = await fetchPins(token);
  console.log("fetched items", items.length);
  if (!items.length) {
    await saveState(env, stateKey, state, { lastCheckedAt: Date.now() });
    return { ok: true, pushed: 0, message: "No items found." };
  }

  if (!state.initialized && env.SEND_LATEST_ON_FIRST_RUN !== "true") {
    await saveState(env, stateKey, {
      initialized: true,
      seenIds: items.slice(0, maxSeen(env)).map((item) => item.id),
      lastCheckedAt: Date.now(),
    });
    console.log("initialized state", items.length);
    return { ok: true, pushed: 0, message: `Initialized ${items.length} item(s); no push sent.` };
  }

  const seen = new Set(state.seenIds || []);
  const newItems = items.filter((item) => !seen.has(item.id));
  console.log("new items", newItems.length);
  if (!newItems.length) {
    await saveState(env, stateKey, state, { lastCheckedAt: Date.now() });
    return { ok: true, pushed: 0, message: "No new items." };
  }

  for (const item of newItems.slice().reverse()) {
    await pushWxPusher(env, item);
  }

  await saveState(env, stateKey, {
    initialized: true,
    seenIds: [...newItems.map((item) => item.id), ...(state.seenIds || [])].slice(0, maxSeen(env)),
    lastCheckedAt: Date.now(),
  });

  return { ok: true, pushed: newItems.length };
}

async function fetchPins(token) {
  const apiUrl = `https://www.zhihu.com/api/v4/members/${encodeURIComponent(token)}/pins?limit=10&offset=0`;
  const response = await fetch(apiUrl, {
    headers: {
      "Accept": "application/json, text/plain, */*",
      "Referer": `https://www.zhihu.com/people/${token}`,
      "User-Agent": "Mozilla/5.0 (compatible; zhihu-wechat-alert/1.0)",
    },
  });

  if (!response.ok) {
    throw new Error(`Zhihu API HTTP ${response.status}: ${await response.text()}`);
  }

  const payload = await response.json();
  return (payload.data || []).map(parsePin).filter(Boolean);
}

function parsePin(pin) {
  const id = String(pin.id || "");
  if (!id) return null;

  const rawText = extractPinText(pin.content);
  const title = pin.excerpt_title || rawText.slice(0, 50) || "New Zhihu pin";
  let link = pin.url || `/pins/${id}`;
  if (link.startsWith("/")) link = `https://www.zhihu.com${link}`;

  const created = pin.created || pin.updated;
  const published = created ? new Date(Number(created) * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }) : "";
  const summary = rawText.length > 500 ? `${rawText.slice(0, 500)}...` : rawText;

  return { id, title, link, published, summary };
}

function extractPinText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (!part || typeof part !== "object") return "";
      return part.own_text || part.content || "";
    })
    .filter(Boolean)
    .join("\n")
    .trim();
}

async function pushWxPusher(env, item) {
  const appToken = required(env.WXPUSHER_APP_TOKEN, "WXPUSHER_APP_TOKEN");
  const uids = parseUids(required(env.WXPUSHER_UIDS, "WXPUSHER_UIDS"));
  const titlePrefix = env.TITLE_PREFIX || "Zhihu";

  const payload = {
    appToken,
    summary: `${titlePrefix}: ${item.title}`.slice(0, 99),
    content: [
      `<p><b>${escapeHtml(item.title)}</b></p>`,
      `<p>${escapeHtml(item.published || "")}</p>`,
      `<p>${escapeHtml(item.summary || "")}</p>`,
      `<p><a href="${escapeHtml(item.link || "")}">Open Zhihu</a></p>`,
    ].join(""),
    contentType: 2,
    uids,
  };

  const response = await fetch("https://wxpusher.zjiecode.com/api/send/message", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`WxPusher HTTP ${response.status}: ${text}`);
  }

  const result = JSON.parse(text);
  if (result.code !== 1000) {
    throw new Error(`WxPusher error: ${text}`);
  }
  return result;
}

function parseUids(value) {
  const trimmed = value.trim();
  if (trimmed.startsWith("[")) return JSON.parse(trimmed);
  return trimmed.split(",").map((item) => item.trim()).filter(Boolean);
}

async function saveState(env, key, state, patch = {}) {
  await env.ZHIHU_ALERT_KV.put(key, JSON.stringify({ ...state, ...patch }));
}

function maxSeen(env) {
  const value = Number(env.MAX_SEEN || DEFAULT_MAX_SEEN);
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_MAX_SEEN;
}

function required(value, name) {
  if (!value) throw new Error(`${name} is required.`);
  return value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}
