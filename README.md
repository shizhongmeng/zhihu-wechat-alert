# Zhihu WeChat Alert

Monitor one or more Zhihu feeds and push new items to WeChat through PushPlus, ServerChan, or WxPusher.

## 1. Create config

Copy `config.example.json` to `config.json`, then edit:

```powershell
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

Set:

- `targets`: the list of feeds to watch (see below)
- `provider`: `wxpusher`, `pushplus`, or `serverchan`
- the token/sendkey fields for your provider

Each entry in `targets` takes:

- `zhihu_user_token`: the last part of the Zhihu profile URL. `https://www.zhihu.com/people/abc-123` -> `abc-123`. A full profile URL also works.
- `route`: `pins`, `activities`, `answers`, or `posts`
- `title_prefix` (optional): the prefix on that feed's push titles, so you can tell sources apart at a glance
- `name` (optional): a label for the feed. It is also the state key, so keep it stable once set.
- `enabled` (optional): `false` to keep an entry in the file but stop checking it

```json
{
  "targets": [
    { "zhihu_user_token": "xiao-peng-61-47", "route": "pins", "title_prefix": "知乎-小鹏" },
    { "zhihu_user_token": "dashixiongmofan", "route": "pins", "title_prefix": "知乎-大师兄" }
  ],
  "provider": "wxpusher",
  "wxpusher_app_token": "your-app-token",
  "wxpusher_uids": ["your-uid"]
}
```

Those two feeds are what `config.example.json`, the GitHub Actions workflow, and `wrangler.toml` ship with.

The same person's different feeds count as different targets, so watching both their pins and their answers is two entries with the same token and different routes.

For `pins`, the script uses Zhihu's public pins endpoint directly. This is the current recommended mode for `xiao-peng-61-47`, because public RSSHub instances may block script access.

For `activities`, `answers`, or `posts`, the script uses RSSHub:

```text
https://rsshub.app/zhihu/people/<route>/<zhihu_user_token>
```

Each target keeps its own seen-item record in the state file, and the first run of a target initializes its existing items as already seen and sends no historical pushes. So adding a target later does not re-push history for the targets you already had, and does not flood you with the new target's backlog.

One failing feed does not stop the others: the script pushes what it can, reports the failed target, and exits with code 3.

The old single-target format is still accepted:

```json
{ "zhihu_user_token": "abc-123", "route": "pins" }
```

An existing `state.json` / `state.github.json` in the old flat format is upgraded automatically on the next run, and its history is kept.

## 2. Test once

```powershell
python .\monitor.py
```

Run it again after the person has posted a new item. It will push only new items.

To check a single target while leaving the others alone, pass its name, token, or `token/route`:

```powershell
python .\monitor.py --target dashixiongmofan
python .\monitor.py --target "dashixiongmofan/pins"
```

## 3. Run every 10 minutes on Windows

Create a scheduled task from PowerShell:

```powershell
$dir = Resolve-Path .
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\monitor.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "ZhihuWeChatAlert" -Action $action -Trigger $trigger -Description "Push Zhihu updates to WeChat"
```

To remove it:

```powershell
Unregister-ScheduledTask -TaskName "ZhihuWeChatAlert" -Confirm:$false
```

The scheduled task needs no change when you add or remove targets, because it just runs `monitor.py` against whatever `config.json` holds.

## 4. Add another feed later

Append an entry to `targets` in `config.json`:

```json
{ "zhihu_user_token": "ghi-789", "route": "pins", "title_prefix": "知乎-小刚" }
```

The next run initializes that feed silently and pushes only what it posts afterwards. Confirm it resolves before waiting on the schedule:

```powershell
python .\monitor.py --target ghi-789
```

## Environment overrides

Every config key can be overridden by an environment variable, which is how the GitHub Actions and Worker deployments are configured. `ZHIHU_TARGETS` sets the whole target list, either as a JSON array or in the compact `token:route:title` form, comma separated:

```text
ZHIHU_TARGETS=abc-123:pins:知乎-小明,def-456:activities:知乎-小红
```

`ZHIHU_USER_TOKEN` and `ZHIHU_ROUTE` still work for a single feed.
