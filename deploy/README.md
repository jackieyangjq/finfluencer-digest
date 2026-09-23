# Deploying with GitHub Actions

The digest can run every morning on GitHub's free scheduler, with nothing left running on your own machine. The setup is a **private config repository**: a private repo of your own holds your settings, your holdings and the digest archive, and each run installs a pinned release of this package from GitHub.

## What goes in the private repo

| Path | Needed | What it is |
|---|---|---|
| `config.yaml` | yes | Channels, X accounts, models and other settings. Start from [`config.example.yaml`](../config.example.yaml). |
| `.github/workflows/daily-digest.yml` | yes | A copy of [`daily-digest.yml`](daily-digest.yml) in this folder. |
| `holdings_manual.yaml`, `portfolio/*.json` | no | Your holdings, only if you turn on the portfolio section (see below). |
| `digests/`, `state/`, `data/` | created by runs | Digest archive, videos and posts already processed, and each blogger's daily view on each stock. Committed back after each successful run. |

Keys and passwords never go in the repo. Keep `.env` on your own machine (list it in `.gitignore`) and store the values as GitHub Actions secrets.

## Setup

1. Create a private repository and add `config.yaml` and the workflow file.
2. Add the secrets under Settings → Secrets and variables → Actions. `GEMINI_API_KEY`, `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` are required; the rest are optional. [`.env.example`](../.env.example) says what each one is for and where to get it. With the GitHub CLI you can upload a filled-in `.env` in one go; delete the lines you left empty first:

   ```bash
   gh secret set -f .env -R <owner>/<config-repo>
   ```

3. Start a first run from the Actions tab (Daily digest → Run workflow) or with `gh workflow run daily-digest.yml -R <owner>/<config-repo>`. Tick "Only list new videos and posts" for a check that uses no Gemini quota and sends no email.

To try the same config on your own machine first, run these in the folder that holds `config.yaml`:

```bash
pip install "finfluencer-digest[portfolio] @ git+https://github.com/jackieyangjq/finfluencer-digest@v1.0.0"
finfluencer-digest --check                # Gemini key and models, Gmail login, channels, X accounts
finfluencer-digest --limit 1 --dry-run    # one video; the digest is saved to digests/ and not emailed
```

The command reads keys from environment variables, or from a `.env` next to `config.yaml`.

## When it runs

GitHub's free scheduler can delay or drop scheduled runs, so the workflow wakes up every 30 minutes from 07:10 to 10:40 UTC, and a small gate job (the `finfluencer-digest gate` command) decides whether to go ahead. The digest runs in the first check after 08:00 UK time for which today's digest has not been sent yet, usually 08:10 UK time in summer and in winter. If GitHub drops that check, the next one catches up. You get at most one email a day. Manual runs skip the gate.

The 08:00 cut-off and the digest date follow UK time (Europe/London), which the package fixes for now. Editing the `cron` line moves the checks, not the cut-off.

If a run fails, GitHub notifies whoever added the workflow (by email, with default settings); the run log is on the Actions page.

## Portfolio section (optional)

Set `portfolio.enabled: true` in `config.yaml` to add a holdings section at the top of each digest: position overview, rule-based risk flags, market indices, and one card per holding with technicals, analyst targets, news and the bloggers' views. The workflow already installs the `[portfolio]` extra. Holdings files sit next to `config.yaml`:

- `holdings_manual.yaml` (the name is set by `portfolio.manual_file`), kept up to date by hand:

  ```yaml
  broker: My broker          # label shown in the digest
  updated: 2026-09-01        # when you last edited this file
  positions:
    - {symbol: NVDA.US, name: NVIDIA, quantity: 10, cost_price: 100.0, currency: USD}
  cash: {USD: 1000}
  ```

- `portfolio/*.json`: one snapshot per broker account, with the same position fields plus `broker`, `as_of`, `currency` and `total_cash`. A script that reads your broker's API can write these.

Symbols use Longbridge format (`NVDA.US`, `700.HK`). Only public data (tickers, prices, news) is sent to Gemini, never your quantities, costs or amounts. Live quotes and some of the news come from Longbridge and Finnhub when their secrets are set; otherwise the section falls back to free sources.

## Costs and limits

- Gemini's free tier covers roughly 8 hours of YouTube video a day. Reading YouTube links directly is still a preview feature, and Google may change its rules or pricing.
- In a private repo, every run counts against your account's free monthly Actions minutes: each gate check is billed as at least one minute, and a digest run with about a dozen videos takes around 15 minutes.
- The X accounts are read through the free FxTwitter API, which is unofficial and may stop working; failures are listed at the end of the digest.
