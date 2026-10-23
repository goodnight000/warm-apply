# Scheduling

The skill works as a one-time run ("run my job hunt") or as a daily automation. Offer the automation after the first successful daily run, once the user has seen an inbox. A scheduled run is the same five-step daily run, unattended, with the inbox left for the user. It needs the skill, a Monid key, and the state folder (`JH_HOME`), which keeps the dedupe and the queue between runs.

Ask two things: what time (default 09:07 local; an odd minute avoids the top-of-hour rush) and weekdays or every day. Then pick the first row that fits:

| Where the user works | How | Runs when |
|---|---|---|
| Claude Code or Codex CLI on macOS or Linux | `JH schedule` (below) | macOS: at that time, or when the Mac next wakes if it was asleep. Linux: only if the machine is on |
| Claude Code Desktop or the Codex app, wants it managed in the app | the app's scheduled tasks (below) | app open, machine on |
| Laptop is often closed | GitHub Actions (below) | anytime |
| Claude.ai or ChatGPT, no shell | a scheduled prompt (chat-mode.md) | anytime |

## `JH schedule` (recommended)

```sh
JH schedule --at 09:07 --days weekdays            # --agent codex for Codex; --days daily for every day
```

It writes `JH_HOME/scheduled-run.sh` and installs it with launchd on macOS (`~/Library/LaunchAgents/com.warm-apply.job-hunt.plist`) or cron on Linux. The script starts your agent CLI headless in the folder holding `JH_HOME`, with the daily-run prompt, then posts a desktop notification. It pins the absolute paths of the agent, `monid` and Python, because launchd and cron start with a bare PATH that misses nvm, Homebrew and `~/.local/bin`.

- Claude Code runs with `--permission-mode dontAsk` and may use only the Read, Write, Edit, Glob, Grep and Skill tools, plus the shell for this skill's `jobhunt.py`. Postings are untrusted text read by an agent nobody is watching, so it can't run anything else.
- Codex runs `codex exec -s danger-full-access`, because its workspace-write sandbox blocks the network Monid needs.

After installing, run it once while the user watches: the `run_now` command it prints. Check `JH_HOME/schedule.log`; on macOS, the first run may ask for permission to show notifications. Run `--dry-run` to see what would be installed, rerun with a new `--at` to change the time, and `--remove` to stop.

## Claude Code Desktop

Code → Scheduled → New task, set to "Only on this computer". Pick the folder holding `job-hunt/`, a daily time and the prompt `Run the job-hunt skill's daily run (run, judge, prepare, draft, packet, digest) and summarize the inbox.` Click **Run now** once and allow each tool prompt "always", so unattended runs don't stall on a permission.

## Codex app

The skill must be in `~/.agents/skills/job-hunt` (install.sh links it). Scheduled → New task, prompt `$job-hunt daily run`, the folder holding `job-hunt/`, sandbox **full access** (workspace-write blocks the network, so Monid calls fail), daily. Turn on push or email under Settings → Notifications.

## GitHub Actions (laptop closed)

This needs a **private** repo, because the state folder holds the user's resume and answers. Put the skill in `.claude/skills/job-hunt/` and the state folder in `job-hunt/`. Add the secrets `MONID_API_KEY` and either `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) or `ANTHROPIC_API_KEY`.

```yaml
name: job-hunt
on:
  schedule: [{cron: "7 13 * * 1-5"}]   # UTC
  workflow_dispatch:
permissions: {contents: write, issues: write}
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm i -g @monid-ai/cli@latest
      - uses: anthropics/claude-code-action@v1
        env: {MONID_API_KEY: "${{ secrets.MONID_API_KEY }}", JOB_HUNT_HOME: "${{ github.workspace }}/job-hunt"}
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: "Run the job-hunt skill's daily run, commit job-hunt/, and open an issue listing today's queue."
          claude_args: --allowedTools "Bash,Read,Write,Edit"
```

When no key is active, `jobhunt.py` registers `MONID_API_KEY` with the CLI. This row hasn't been verified end to end: on the first run, check that the log shows `monid_key_source: env` from `JH check`. The inbox PDFs need Chrome; ubuntu-latest has it.
