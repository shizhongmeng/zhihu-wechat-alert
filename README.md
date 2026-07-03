# Zhihu WeChat Alert

Monitor one Zhihu user's updates and push new items to WeChat through PushPlus, ServerChan, or WxPusher.

## 1. Create config

Copy `config.example.json` to `config.json`, then edit:

```powershell
Copy-Item .\config.example.json .\config.json
notepad .\config.json
```

Set:

- `zhihu_user_token`: the last part of the Zhihu profile URL. `https://www.zhihu.com/people/abc-123` -> `abc-123`
- `route`: `pins`, `activities`, `answers`, or `posts`
- `provider`: `wxpusher`, `pushplus`, or `serverchan`
- the token/sendkey fields for your provider

For `pins`, the script uses Zhihu's public pins endpoint directly. This is the current recommended mode for `xiao-peng-61-47`, because public RSSHub instances may block script access.

For `activities`, `answers`, or `posts`, the script uses RSSHub:

```text
https://rsshub.app/zhihu/people/<route>/<zhihu_user_token>
```

Recommended default:

```json
{
  "zhihu_user_token": "abc-123",
  "route": "pins",
  "provider": "wxpusher",
  "wxpusher_app_token": "your-app-token",
  "wxpusher_uids": ["your-uid"]
}
```

The first run initializes existing items as already seen and sends no historical pushes.

## 2. Test once

```powershell
python .\monitor.py
```

Run it again after the person has posted a new item. It will push only new items.

## 3. Run every 10 minutes on Windows

Create a scheduled task from PowerShell:

```powershell
$dir = Resolve-Path .
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$dir\monitor.py`"" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName "ZhihuWeChatAlert" -Action $action -Trigger $trigger -Description "Push one Zhihu user's updates to WeChat"
```

To remove it:

```powershell
Unregister-ScheduledTask -TaskName "ZhihuWeChatAlert" -Confirm:$false
```
