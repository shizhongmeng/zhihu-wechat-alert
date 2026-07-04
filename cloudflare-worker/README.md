# Cloudflare Worker Deployment

This Worker checks `xiao-peng-61-47` Zhihu pins every 5 minutes and pushes new items to WxPusher.

## Commands

```powershell
cd C:\Users\guoch\Desktop\test\zhihu-wechat-alert-cloudflare\cloudflare-worker
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
https://zhihu-wechat-alert.<your-subdomain>.workers.dev/test
```
