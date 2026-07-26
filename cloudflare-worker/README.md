# Cloudflare Worker Deployment

This Worker checks one or more Zhihu pins feeds every 5 minutes and pushes new items to WxPusher.

## Targets

Set `ZHIHU_TARGETS` in `wrangler.toml` under `[vars]`, one entry per feed as
`token` or `token:route:title-prefix`, comma separated. The checked-in default watches
two feeds:

```toml
[vars]
ZHIHU_TARGETS = "xiao-peng-61-47:pins:Zhihu-xiaopeng,dashixiongmofan:pins:Zhihu-dashixiong"
```

A JSON array also works if you want `name` or `enabled`:

```toml
ZHIHU_TARGETS = '[{"token":"xiao-peng-61-47","route":"pins"},{"token":"dashixiongmofan","route":"pins","title_prefix":"Zhihu-dashixiong"}]'
```

If `ZHIHU_TARGETS` is empty, the Worker falls back to the single `ZHIHU_USER_TOKEN` var.

Each target keeps its own KV record under `zhihu:<token>:<route>`, so adding a target
does not disturb the ones already running, and the new target's first run only
initializes its history instead of pushing the backlog.

The Worker supports the `pins` route only, because the Workers runtime has no XML
parser for the RSSHub feeds. An `activities`, `answers`, or `posts` target is reported
as a failure for that target alone and does not affect the others. Run those routes
with `monitor.py` on GitHub Actions instead.

## Commands

```powershell
cd .\cloudflare-worker
cmd /c npx wrangler login
cmd /c npx wrangler kv namespace create ZHIHU_ALERT_KV
```

Put the returned KV `id` in `wrangler.toml`.

Set secrets:

```powershell
cmd /c npx wrangler secret put WXPUSHER_APP_TOKEN
cmd /c npx wrangler secret put WXPUSHER_UIDS
```

Deploy:

```powershell
cmd /c npx wrangler deploy
```

Manual checks:

```text
https://zhihu-wechat-alert.<your-subdomain>.workers.dev/run
https://zhihu-wechat-alert.<your-subdomain>.workers.dev/targets
https://zhihu-wechat-alert.<your-subdomain>.workers.dev/test
https://zhihu-wechat-alert.<your-subdomain>.workers.dev/health
```

`/run` returns a per-target breakdown, so you can see which feed pushed and which failed.
`/targets` lists the feeds the Worker parsed from your config without checking them.
`/test` sends one test message per target, so two configured feeds mean two messages.
