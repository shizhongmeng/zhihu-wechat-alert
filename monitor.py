#!/usr/bin/env python3
import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.json"
DEFAULT_STATE = SCRIPT_DIR / "state.json"
VALID_ROUTES = {"activities", "answers", "posts", "pins"}


def load_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def log(message):
    text = str(message)
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace"))


def parse_targets_env(value):
    """Parse ZHIHU_TARGETS: either a JSON array, or "token:route:title,token2:route2"."""
    value = value.strip()
    if value.startswith("["):
        return json.loads(value)

    targets = []
    for entry in value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        target = {"zhihu_user_token": parts[0]}
        if len(parts) > 1 and parts[1]:
            target["route"] = parts[1]
        if len(parts) > 2 and parts[2]:
            target["title_prefix"] = parts[2]
        targets.append(target)
    return targets


def apply_env_overrides(config):
    env_map = {
        "ZHIHU_USER_TOKEN": "zhihu_user_token",
        "ZHIHU_ROUTE": "route",
        "RSSHUB_BASE": "rsshub_base",
        "PUSH_PROVIDER": "provider",
        "TITLE_PREFIX": "title_prefix",
        "PUSHPLUS_TOKEN": "pushplus_token",
        "SERVERCHAN_SENDKEY": "serverchan_sendkey",
        "WXPUSHER_APP_TOKEN": "wxpusher_app_token",
    }
    for env_name, key in env_map.items():
        value = os.environ.get(env_name)
        if value:
            config[key] = value

    targets_env = os.environ.get("ZHIHU_TARGETS")
    if targets_env and targets_env.strip():
        config["targets"] = parse_targets_env(targets_env)

    wxpusher_uids = os.environ.get("WXPUSHER_UIDS")
    if wxpusher_uids:
        config["wxpusher_uids"] = [item.strip() for item in wxpusher_uids.split(",") if item.strip()]

    send_latest = os.environ.get("SEND_LATEST_ON_FIRST_RUN")
    if send_latest:
        config["send_latest_on_first_run"] = send_latest.lower() in {"1", "true", "yes", "y"}

    max_seen = os.environ.get("MAX_SEEN")
    if max_seen:
        config["max_seen"] = int(max_seen)

    state_file = os.environ.get("STATE_FILE")
    if state_file:
        config["state_file"] = state_file

    return config


def http_request(url, method="GET", data=None, headers=None, timeout=30):
    body = None
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; zhihu-wechat-alert/1.0; +https://rsshub.app)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    if headers:
        request_headers.update(headers)

    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")

    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_json(url, headers=None, timeout=30):
    return json.loads(http_request(url, headers=headers, timeout=timeout))


def text_of(element, path):
    found = element.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def attr_or_text(element, path, attr):
    found = element.find(path)
    if found is None:
        return ""
    return (found.get(attr) or found.text or "").strip()


def parse_feed(xml_text):
    root = ET.fromstring(xml_text)
    items = []

    # RSS 2.0
    channel_items = root.findall("./channel/item")
    for item in channel_items:
        title = text_of(item, "title") or "知乎新动态"
        link = text_of(item, "link")
        guid = text_of(item, "guid") or link or title
        published = text_of(item, "pubDate")
        summary = text_of(item, "description")
        if len(summary) > 500:
            summary = summary[:500] + "..."
        items.append(
            {
                "id": guid,
                "title": title,
                "link": link,
                "published": published,
                "summary": summary,
            }
        )

    if items:
        return items

    # Atom fallback
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("./atom:entry", ns):
        title = text_of(entry, "atom:title")
        link = attr_or_text(entry, "atom:link", "href")
        guid = text_of(entry, "atom:id") or link or title
        published = text_of(entry, "atom:updated") or text_of(entry, "atom:published")
        summary = text_of(entry, "atom:summary")
        if len(summary) > 500:
            summary = summary[:500] + "..."
        items.append(
            {
                "id": guid,
                "title": title or "知乎新动态",
                "link": link,
                "published": published,
                "summary": summary,
            }
        )

    return items


def extract_pin_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("own_text") or part.get("content") or ""
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def parse_zhihu_pins(payload):
    items = []
    for pin in payload.get("data", []):
        pin_id = str(pin.get("id") or "")
        if not pin_id:
            continue

        raw_text = extract_pin_text(pin.get("content"))
        title = pin.get("excerpt_title") or raw_text[:50] or "知乎新想法"
        url = pin.get("url") or f"/pins/{pin_id}"
        if url.startswith("/"):
            url = "https://www.zhihu.com" + url

        created = pin.get("created") or pin.get("updated")
        published = ""
        if created:
            published = datetime.fromtimestamp(int(created)).strftime("%Y-%m-%d %H:%M:%S")

        summary = raw_text
        if len(summary) > 500:
            summary = summary[:500] + "..."

        items.append(
            {
                "id": pin_id,
                "title": title,
                "link": url,
                "published": published,
                "summary": summary,
            }
        )

    return items


def normalize_zhihu_token(value):
    token = value.strip().strip("/")
    if token.startswith("http://") or token.startswith("https://"):
        token = token.rstrip("/").split("/")[-1]
    return token


def target_key(target):
    """Stable state key for one monitored feed."""
    return target.get("name") or f"{target['zhihu_user_token']}:{target['route']}"


def normalize_target(entry, config, index):
    """Turn one raw target entry into a validated target dict."""
    if isinstance(entry, str):
        entry = {"zhihu_user_token": entry}
    if not isinstance(entry, dict):
        raise ValueError(f"targets[{index}] must be a string or an object")

    raw_token = entry.get("zhihu_user_token") or entry.get("token") or ""
    token = normalize_zhihu_token(str(raw_token))
    if not token:
        raise ValueError(f"targets[{index}] is missing zhihu_user_token")

    route = str(entry.get("route") or config.get("route") or "activities").strip("/")
    if route not in VALID_ROUTES:
        raise ValueError(
            f"targets[{index}] route must be one of: " + ", ".join(sorted(VALID_ROUTES))
        )

    target = {
        "zhihu_user_token": token,
        "route": route,
        "rsshub_base": entry.get("rsshub_base") or config.get("rsshub_base") or "https://rsshub.app",
    }
    if entry.get("name"):
        target["name"] = str(entry["name"])
    if entry.get("title_prefix"):
        target["title_prefix"] = str(entry["title_prefix"])
    if entry.get("enabled") is False:
        target["enabled"] = False
    return target


def normalize_targets(config):
    """Build the target list, accepting both the new `targets` list and the old single-target keys."""
    raw_targets = config.get("targets")
    if raw_targets is None and config.get("zhihu_user_token"):
        raw_targets = [{"zhihu_user_token": config["zhihu_user_token"]}]
    if not raw_targets:
        raise ValueError("no target configured: set `targets` (or `zhihu_user_token`) in config.json")
    if not isinstance(raw_targets, list):
        raise ValueError("`targets` must be a list")

    targets = []
    seen_keys = set()
    for index, entry in enumerate(raw_targets):
        target = normalize_target(entry, config, index)
        key = target_key(target)
        if key in seen_keys:
            raise ValueError(f"duplicate target: {key}")
        seen_keys.add(key)
        targets.append(target)
    return targets


def target_label(target):
    return f"{target['zhihu_user_token']}/{target['route']}"


def build_feed_url(target):
    token = urllib.parse.quote(target["zhihu_user_token"])
    if target["route"] == "pins":
        return f"https://www.zhihu.com/api/v4/members/{token}/pins?limit=10&offset=0"

    base = target["rsshub_base"].rstrip("/")
    return f"{base}/zhihu/people/{target['route']}/{token}"


def fetch_items(target):
    url = build_feed_url(target)

    if target["route"] == "pins":
        token = target["zhihu_user_token"]
        payload = http_json(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": f"https://www.zhihu.com/people/{token}",
            },
        )
        return url, parse_zhihu_pins(payload)

    xml_text = http_request(url)
    return url, parse_feed(xml_text)

def push_plus(config, title, content):
    token = config.get("pushplus_token", "")
    if not token or token.startswith("replace-with"):
        raise ValueError("pushplus_token is missing in config.json")

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
    }
    return http_request("https://www.pushplus.plus/send", method="POST", data=payload)


def push_serverchan(config, title, content):
    sendkey = config.get("serverchan_sendkey", "")
    if not sendkey or sendkey.startswith("replace-with"):
        raise ValueError("serverchan_sendkey is missing in config.json")

    url = f"https://sctapi.ftqq.com/{urllib.parse.quote(sendkey)}.send"
    body = urllib.parse.urlencode({"title": title, "desp": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; zhihu-wechat-alert/1.0; +https://rsshub.app)",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def push_wxpusher(config, title, content):
    app_token = config.get("wxpusher_app_token", "")
    uids = config.get("wxpusher_uids", [])
    if not app_token or app_token.startswith("replace-with"):
        raise ValueError("wxpusher_app_token is missing in config.json")
    if not uids or any(uid.startswith("replace-with") for uid in uids):
        raise ValueError("wxpusher_uids is missing in config.json")

    payload = {
        "appToken": app_token,
        "summary": title[:99],
        "content": content,
        "contentType": 2,
        "uids": uids,
    }
    return http_request("https://wxpusher.zjiecode.com/api/send/message", method="POST", data=payload)


def push(config, target, item):
    title_prefix = target.get("title_prefix") or config.get("title_prefix") or "知乎动态"
    title = f"{title_prefix}: {item['title']}"
    link = item.get("link") or ""
    published = item.get("published") or ""
    source = target.get("name") or target_label(target)
    profile = f"https://www.zhihu.com/people/{target['zhihu_user_token']}"
    content = (
        f"<p><b>{html.escape(item['title'])}</b></p>"
        f"<p>{html.escape(published)}</p>"
        f"<p>{html.escape(item.get('summary', ''))}</p>"
        f"<p><a href=\"{html.escape(link)}\">打开知乎</a></p>"
        f"<p>来源: <a href=\"{html.escape(profile)}\">{html.escape(source)}</a></p>"
    )

    provider = config.get("provider", "pushplus").lower()
    if provider == "pushplus":
        return push_plus(config, title, content)
    if provider == "serverchan":
        return push_serverchan(config, title, content)
    if provider == "wxpusher":
        return push_wxpusher(config, title, content)
    raise ValueError("provider must be one of: pushplus, serverchan, wxpusher")


def migrate_state(state, targets):
    """Upgrade a flat single-target state file to the per-target layout."""
    if not isinstance(state, dict):
        return {"version": 2, "targets": {}}
    if isinstance(state.get("targets"), dict):
        state.setdefault("version", 2)
        return state

    legacy_ids = state.get("seen_ids")
    migrated = {"version": 2, "targets": {}}
    if not legacy_ids and not state.get("initialized"):
        return migrated

    # Attach the old record to the target whose feed URL it came from.
    legacy_feed = state.get("feed_url") or ""
    owner = next((t for t in targets if build_feed_url(t) == legacy_feed), None) or (
        targets[0] if targets else None
    )
    if owner is not None:
        migrated["targets"][target_key(owner)] = {
            "seen_ids": list(legacy_ids or []),
            "initialized": bool(state.get("initialized")),
            "feed_url": legacy_feed or build_feed_url(owner),
            "last_checked_at": state.get("last_checked_at"),
        }
        log(f"Migrated legacy state to target: {target_label(owner)}")
    return migrated


def check_target(config, target, target_state):
    """Fetch one target and push its new items. Returns the number pushed."""
    label = target_label(target)
    max_seen = int(config.get("max_seen", 200))
    feed_url, items = fetch_items(target)
    target_state["feed_url"] = feed_url
    target_state["last_checked_at"] = int(time.time())

    if not items:
        log(f"[{label}] No items found in feed: {feed_url}")
        return 0

    if not target_state.get("initialized") and not config.get("send_latest_on_first_run", False):
        target_state["seen_ids"] = [item["id"] for item in items[:max_seen]]
        target_state["initialized"] = True
        log(f"[{label}] Initialized {len(target_state['seen_ids'])} existing item(s); no push sent.")
        log(f"[{label}] Feed: {feed_url}")
        return 0

    seen_ids = set(target_state.get("seen_ids", []))
    new_items = [item for item in items if item["id"] not in seen_ids]
    if not new_items:
        log(f"[{label}] No new items.")
        return 0

    pushed = 0
    for item in reversed(new_items):
        log(f"[{label}] Pushing: {item['title']}")
        log(push(config, target, item))
        # Record each id right after its push so a later failure cannot cause a repeat.
        target_state["seen_ids"] = [item["id"]] + target_state.get("seen_ids", [])
        target_state["seen_ids"] = target_state["seen_ids"][:max_seen]
        pushed += 1

    target_state["initialized"] = True
    log(f"[{label}] Pushed {pushed} new item(s).")
    return pushed


def run(config_path, only_target=None):
    config = load_json(config_path, {})
    config = apply_env_overrides(config)
    targets = normalize_targets(config)

    if only_target:
        wanted = only_target.strip()
        targets = [
            t for t in targets
            if wanted in {target_key(t), target_label(t), t.get("name"), t["zhihu_user_token"]}
        ]
        if not targets:
            raise ValueError(f"no target matches --target {only_target}")

    state_path = Path(config.get("state_file", DEFAULT_STATE))
    if not state_path.is_absolute():
        state_path = SCRIPT_DIR / state_path

    state = migrate_state(load_json(state_path, {}), targets)

    total_pushed = 0
    failures = []
    for target in targets:
        label = target_label(target)
        if target.get("enabled") is False:
            log(f"[{label}] Skipped (disabled).")
            continue

        key = target_key(target)
        target_state = state["targets"].setdefault(key, {"seen_ids": [], "initialized": False})
        try:
            total_pushed += check_target(config, target, target_state)
        except Exception as exc:
            # One broken feed must not stop the remaining targets.
            failures.append(label)
            log(f"[{label}] Error: {exc}")
        finally:
            # Persist after every target so a later crash cannot lose earlier progress.
            save_json(state_path, state)

    log(f"Checked {len(targets)} target(s); pushed {total_pushed} item(s).")
    if failures:
        print(f"Failed target(s): {', '.join(failures)}", file=sys.stderr)
        return 3
    return 0


def main():
    parser = argparse.ArgumentParser(description="Push Zhihu users' updates to WeChat.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    parser.add_argument(
        "--target",
        default=None,
        help="Only check one target, by name, token, or token/route",
    )
    args = parser.parse_args()

    try:
        return run(Path(args.config).resolve(), args.target)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
