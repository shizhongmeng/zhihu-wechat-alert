const DEFAULT_MAX_SEEN = 200;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/run") {
      return runAndRespond(env);
    }
    if (url.pathname === "/targets") {
      try {
        return Response.json({ ok: true, targets: parseTargets(env) });
      } catch (error) {
        return Response.json({ ok: false, error: errorMessage(error) }, { status: 500 });
      }
    }
    if (url.pathname === "/test") {
      try {
        const targets = parseTargets(env);
        const results = [];
        for (const target of targets) {
          const result = await pushWxPusher(env, target, {
            title: "Cloudflare test",
            published: new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }),
            summary: "Cloudflare Worker cloud push test.",
            link: profileUrl(target.token),
          });
          // Surface the per-recipient rows: an overall code 1000 can still hide a
          // dead UID, which is invisible if we only report "sent".
          results.push({ target: targetLabel(target), delivery: result && result.data });
        }
        return Response.json({
          ok: true,
          message: `Test push sent for ${targets.length} target(s).`,
          results,
        });
      } catch (error) {
        return Response.json({ ok: false, error: errorMessage(error) }, { status: 500 });
      }
    }
    if (url.pathname === "/health") {
      return Response.json({ ok: true, service: "zhihu-wechat-alert" });
    }
    return new Response(
      "ok\nGET /run to check now\nGET /targets to list monitored feeds\nGET /test to push a test message\nGET /health for status\n",
    );
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
    return Response.json({ ok: false, error: errorMessage(error) }, { status: 500 });
  }
}

function parseTargets(env) {
  const raw = (env.ZHIHU_TARGETS || "").trim();
  let entries;

  if (raw) {
    entries = raw.startsWith("[") ? JSON.parse(raw) : raw.split(",");
  } else {
    entries = [required(env.ZHIHU_USER_TOKEN, "ZHIHU_USER_TOKEN or ZHIHU_TARGETS")];
  }

  const targets = [];
  const seen = new Set();
  for (const entry of entries) {
    const target = normalizeTarget(entry, env);
    if (!target) continue;
    const key = stateKey(target);
    if (seen.has(key)) throw new Error(`Duplicate target: ${key}`);
    seen.add(key);
    targets.push(target);
  }

  if (!targets.length) throw new Error("No Zhihu target configured.");
  return targets;
}

function normalizeTarget(entry, env) {
  let token = "";
  let route = "pins";
  let name = "";
  let titlePrefix = "";

  if (typeof entry === "string") {
    const parts = entry.split(":").map((part) => part.trim());
    token = parts[0] || "";
    if (parts[1]) route = parts[1];
    if (parts[2]) titlePrefix = parts[2];
  } else if (entry && typeof entry === "object") {
    token = String(entry.zhihu_user_token || entry.token || "").trim();
    if (entry.route) route = String(entry.route).trim();
    if (entry.name) name = String(entry.name);
    if (entry.title_prefix || entry.titlePrefix) titlePrefix = String(entry.title_prefix || entry.titlePrefix);
    if (entry.enabled === false) return null;
  } else {
    throw new Error("Each ZHIHU_TARGETS entry must be a string or an object.");
  }

  token = normalizeToken(token);
  if (!token) throw new Error("A ZHIHU_TARGETS entry is missing its user token.");

  return { token, route, name, titlePrefix: titlePrefix || env.TITLE_PREFIX || "Zhihu" };
}

function normalizeToken(value) {
  let token = String(value || "").trim().replace(/\/+$/, "");
  if (token.startsWith("http://") || token.startsWith("https://")) {
    token = token.split("/").filter(Boolean).pop() || "";
  }
  return token;
}

function stateKey(target) {
  return `zhihu:${target.token}:${target.route}`;
}

function targetLabel(target) {
  return target.name || `${target.token}/${target.route}`;
}

function profileUrl(token) {
  return `https://www.zhihu.com/people/${token}`;
}

async function run(env) {
  const targets = parseTargets(env);
  console.log("run start", targets.map(targetLabel).join(", "));

  let pushed = 0;
  const results = [];
  const failures = [];

  for (const target of targets) {
    const label = targetLabel(target);
    try {
      const result = await checkTarget(env, target);
      pushed += result.pushed;
      results.push({ target: label, ...result });
    } catch (error) {
      // One failing feed must not stop the remaining targets.
      const message = errorMessage(error);
      failures.push(label);
      results.push({ target: label, pushed: 0, error: message });
      console.log("target failed", label, message);
    }
  }

  return { ok: failures.length === 0, pushed, targets: results, failed: failures };
}

async function checkTarget(env, target) {
  const key = stateKey(target);
  const label = targetLabel(target);

  // The Worker runtime has no XML parser, so RSSHub-backed routes stay on the Python runner.
  // This is checked per target so one unsupported entry cannot stop the pins targets.
  if (target.route !== "pins") {
    throw new Error(
      `Route "${target.route}" is not supported in the Worker (pins only). ` +
        "Run this target with monitor.py / GitHub Actions instead.",
    );
  }

  await sendPendingTestMessage(env, target);

  const state = (await env.ZHIHU_ALERT_KV.get(key, "json")) || { initialized: false, seenIds: [] };
  const items = await fetchPins(target.token);
  console.log("fetched items", label, items.length);

  if (!items.length) {
    await saveState(env, key, state, { lastCheckedAt: Date.now() });
    return { pushed: 0, message: "No items found." };
  }

  if (!state.initialized && env.SEND_LATEST_ON_FIRST_RUN !== "true") {
    await saveState(env, key, {
      initialized: true,
      seenIds: items.slice(0, maxSeen(env)).map((item) => item.id),
      lastCheckedAt: Date.now(),
    });
    console.log("initialized state", label, items.length);
    return { pushed: 0, message: `Initialized ${items.length} item(s); no push sent.` };
  }

  const seen = new Set(state.seenIds || []);
  const newItems = items.filter((item) => !seen.has(item.id));
  console.log("new items", label, newItems.length);
  if (!newItems.length) {
    await saveState(env, key, state, { lastCheckedAt: Date.now() });
    return { pushed: 0, message: "No new items." };
  }

  let seenIds = state.seenIds || [];
  let pushed = 0;
  for (const item of newItems.slice().reverse()) {
    await pushWxPusher(env, target, item);
    // Record each id right after its push so a later failure cannot cause a repeat.
    seenIds = [item.id, ...seenIds].slice(0, maxSeen(env));
    pushed += 1;
    await saveState(env, key, { initialized: true, seenIds, lastCheckedAt: Date.now() });
  }

  return { pushed };
}

async function sendPendingTestMessage(env, target) {
  const testKey = `test-once:${target.token}`;
  const testMessage = await env.ZHIHU_ALERT_KV.get(testKey);
  if (!testMessage) return;

  await pushWxPusher(env, target, {
    title: "Cloudflare scheduled test",
    published: new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" }),
    summary: testMessage,
    link: profileUrl(target.token),
  });
  await env.ZHIHU_ALERT_KV.delete(testKey);
  console.log("sent one-time scheduled test", targetLabel(target));
}

async function fetchPins(token) {
  const apiUrl = `https://www.zhihu.com/api/v4/members/${encodeURIComponent(token)}/pins?limit=10&offset=0`;
  const response = await fetch(apiUrl, {
    headers: {
      "Accept": "application/json, text/plain, */*",
      "Referer": profileUrl(token),
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

async function pushWxPusher(env, target, item) {
  const appToken = required(env.WXPUSHER_APP_TOKEN, "WXPUSHER_APP_TOKEN");
  const uids = parseUids(required(env.WXPUSHER_UIDS, "WXPUSHER_UIDS"));
  const titlePrefix = target.titlePrefix || env.TITLE_PREFIX || "Zhihu";

  const payload = {
    appToken,
    summary: `${titlePrefix}: ${item.title}`.slice(0, 99),
    content: [
      `<p><b>${escapeHtml(item.title)}</b></p>`,
      `<p>${escapeHtml(item.published || "")}</p>`,
      `<p>${escapeHtml(item.summary || "")}</p>`,
      `<p><a href="${escapeHtml(item.link || "")}">Open Zhihu</a></p>`,
      `<p>From: <a href="${escapeHtml(profileUrl(target.token))}">${escapeHtml(targetLabel(target))}</a></p>`,
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

  // A top-level 1000 only means WxPusher accepted the request. Each recipient row in
  // `data` carries its own code, so a message can be "sent" and still reach nobody
  // (unsubscribed UID, wrong UID). Check the rows before calling this a success.
  const rows = Array.isArray(result.data) ? result.data : [];
  const failed = rows.filter((row) => row && row.code !== undefined && row.code !== 1000);
  if (rows.length && failed.length === rows.length) {
    throw new Error(`WxPusher accepted the request but every recipient failed: ${text}`);
  }
  if (failed.length) {
    // Partial failure: do not throw, or the working recipients would get a repeat on
    // every retry. Log it so the bad UID is visible in `wrangler tail`.
    console.log("wxpusher partial failure", targetLabel(target), JSON.stringify(failed));
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

function errorMessage(error) {
  return String(error && error.message ? error.message : error);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}
