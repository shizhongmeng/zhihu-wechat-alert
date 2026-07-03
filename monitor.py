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


def load_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


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


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def log(message):
    text = str(message)
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace"))


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
        items.append(
            {
                "id": guid,
                "title": title,
                "link": link,
                "published": published,
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
        items.append(
            {
                "id": guid,
                "title": title or "知乎新动态",
                "link": link,
                "published": published,
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


def build_feed_url(config):
    token = normalize_zhihu_token(config["zhihu_user_token"])

    route = config.get("route", "activities").strip("/")
    if route not in {"activities", "answers", "posts", "pins"}:
        raise ValueError("route must be one of: activities, answers, posts, pins")
    if route == "pins":
        return f"https://www.zhihu.com/api/v4/members/{urllib.parse.quote(token)}/pins?limit=10&offset=0"

    base = config.get("rsshub_base", "https://rsshub.app").rstrip("/")
    return f"{base}/zhihu/people/{route}/{urllib.parse.quote(token)}"


def fetch_items(config):
    route = config.get("route", "activities").strip("/")
    url = build_feed_url(config)

    if route == "pins":
        token = normalize_zhihu_token(config["zhihu_user_token"])
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


def push(config, item):
    title_prefix = config.get("title_prefix", "知乎动态")
    title = f"{title_prefix}: {item['title']}"
    link = item.get("link") or ""
    published = item.get("published") or ""
    content = (
        f"<p><b>{html.escape(item['title'])}</b></p>"
        f"<p>{html.escape(published)}</p>"
        f"<p>{html.escape(item.get('summary', ''))}</p>"
        f"<p><a href=\"{html.escape(link)}\">打开知乎</a></p>"
    )

    provider = config.get("provider", "pushplus").lower()
    if provider == "pushplus":
        return push_plus(config, title, content)
    if provider == "serverchan":
        return push_serverchan(config, title, content)
    if provider == "wxpusher":
        return push_wxpusher(config, title, content)
    raise ValueError("provider must be one of: pushplus, serverchan, wxpusher")


def run(config_path):
    config = load_json(config_path, {})
    config = apply_env_overrides(config)

    state_path = Path(config.get("state_file", DEFAULT_STATE))
    if not state_path.is_absolute():
        state_path = SCRIPT_DIR / state_path

    feed_url, items = fetch_items(config)
    if not items:
        log(f"No items found in feed: {feed_url}")
        return 0

    state = load_json(state_path, {"seen_ids": [], "initialized": False})
    seen_ids = set(state.get("seen_ids", []))
    max_seen = int(config.get("max_seen", 200))

    if not state.get("initialized") and not config.get("send_latest_on_first_run", False):
        state["seen_ids"] = [item["id"] for item in items[:max_seen]]
        state["initialized"] = True
        state["feed_url"] = feed_url
        state["last_checked_at"] = int(time.time())
        save_json(state_path, state)
        log(f"Initialized {len(state['seen_ids'])} existing item(s); no push sent.")
        log(f"Feed: {feed_url}")
        return 0

    new_items = [item for item in items if item["id"] not in seen_ids]
    if not new_items:
        state["last_checked_at"] = int(time.time())
        state["feed_url"] = feed_url
        save_json(state_path, state)
        log("No new items.")
        return 0

    for item in reversed(new_items):
        log(f"Pushing: {item['title']}")
        log(push(config, item))

    updated_ids = [item["id"] for item in new_items] + state.get("seen_ids", [])
    state["seen_ids"] = updated_ids[:max_seen]
    state["initialized"] = True
    state["feed_url"] = feed_url
    state["last_checked_at"] = int(time.time())
    save_json(state_path, state)
    log(f"Pushed {len(new_items)} new item(s).")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Push one Zhihu user's RSSHub updates to WeChat.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.json")
    args = parser.parse_args()

    try:
        return run(Path(args.config).resolve())
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
