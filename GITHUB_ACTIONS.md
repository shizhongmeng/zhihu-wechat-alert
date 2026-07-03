# GitHub Actions Deployment

This runs the Zhihu monitor in GitHub Actions every 10 minutes and sends new pins to WeChat through WxPusher.

## 1. Create a GitHub repository

Create a private repository for this monitor only.

Recommended repository content after copying files:

```text
monitor.py
config.example.json
.github/workflows/zhihu-wechat-alert.yml
```

Do not commit `config.json` because it contains local tokens.

Use `github-workflow-root.yml` as the workflow file:

```powershell
mkdir .github\workflows
Copy-Item .\github-workflow-root.yml .\.github\workflows\zhihu-wechat-alert.yml
```

The checked-in `.github/workflows/zhihu-wechat-alert.yml` under this local folder is for the current parent workspace layout. For a standalone GitHub repository, use `github-workflow-root.yml`.

## 2. Add GitHub Secrets

In your repository:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

Add:

```text
WXPUSHER_APP_TOKEN
WXPUSHER_UIDS
```

`WXPUSHER_UIDS` can be one UID:

```text
UID_xxx
```

or multiple UIDs separated by commas.

## 3. Enable Actions

Open the repository `Actions` tab and enable workflows if GitHub asks.

Then run:

```text
Actions -> Zhihu WeChat Alert -> Run workflow
```

The first successful run initializes `state.github.json` and sends no historical pushes. New pins after that are pushed to WeChat.

## 4. Frequency

The workflow currently runs every 10 minutes:

```yaml
cron: "*/10 * * * *"
```

GitHub scheduled workflows can be delayed, especially during busy periods. This is normal.
