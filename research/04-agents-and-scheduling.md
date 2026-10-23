# 04 — Agents, scheduling, headless auth, state

Researched 2026-10-21. Most vendor pages carry no date; "undated" means the page showed none, so the content is as fetched today. **[U]** marks a claim I could not confirm from a primary source.

## 1. Skill format and installation

The [agentskills.io spec](https://agentskills.io/specification) allows six frontmatter fields: `name` (≤64 chars, lowercase/digits/hyphens, must match the folder name), `description` (≤1024), `license`, `compatibility` (≤500), `metadata` (string→string map), and `allowed-tools` (experimental). Optional folders are `scripts/`, `references/`, and `assets/`. Keep SKILL.md under 500 lines.

**Design rule:** use only those six fields. Claude.ai uploads reject any other key with a hard error, and Claude Code's `!`cmd`` injection does not work outside Claude Code ([Claude Code skills](https://code.claude.com/docs/en/skills), undated).

| Platform | Where skills load from | One-line install |
|---|---|---|
| Claude Code | `~/.claude/skills/<name>/`, `.claude/skills/` in the repo, plugins | `cp -r skills/job-hunt ~/.claude/skills/` |
| Claude.ai / Desktop / Cowork | Account skills (Customize › Skills). Synced to `~/.claude/skills/synced/` for cloud tasks | Zip the folder, then Customize › Skills › + › Upload ([help](https://support.claude.com/en/articles/12512180-using-skills-in-claude)). Needs "Code execution" turned on. Works on all plans |
| Codex CLI/app, ChatGPT desktop | `~/.agents/skills`, `.agents/skills` up to the repo root, `/etc/codex/skills`. **Not** `~/.codex/skills` | `cp -r skills/job-hunt ~/.agents/skills/`, or `$skill-installer` with a GitHub URL ([docs](https://learn.chatgpt.com/docs/build-skills), undated) |
| ChatGPT web | Skills only come in through plugins. I found no standalone upload flow | Package as a plugin **[U]** |
| Cursor | `.cursor/skills`, `.agents/skills`, `~/.cursor/skills`. Also reads `.claude/skills` and `~/.claude/skills` | Copy the folder. Cloud agents only get `~/.cursor/skills`, and only with "Sync Skills for Cloud Agents" on ([docs](https://cursor.com/docs/context/skills)) |
| Gemini CLI | `~/.gemini/skills`, `~/.agents/skills`, `.gemini/skills`, `.agents/skills` | `gemini skills install <git-url> --path skills/job-hunt --consent` ([docs](https://geminicli.com/docs/cli/skills/), updated 2026-04-30). Free-tier users were moved to Antigravity CLI on 2026-06-18 |
| OpenClaw | `<workspace>/skills`, `.agents/skills`, `~/.agents/skills`, `~/.openclaw/skills` | `openclaw skills install git:<you>/job-hunt@main`. Declare `metadata.openclaw.requires.env: [MONID_API_KEY]` ([docs](https://docs.openclaw.ai/tools/skills)) |
| Hermes Agent | `~/.hermes/skills/` | Copy the folder ([cron docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)) |

`~/.agents/skills/` is read by Codex, Cursor, Gemini, and OpenClaw, which makes it the closest thing to a universal user path. Claude Code needs `~/.claude/skills/`, and a symlink between the two works.

**Claude.ai code execution:** Free, Pro, and Max have network "enabled… to install packages from approved sources": npm, PyPI, GitHub, and similar ([help](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude), 2026-08-06). Team and Enterprise owners can add custom domains or allow all domains. I found no documented way for Pro or Max users to allow `api.monid.ai` **[U]**. In practice, the CLI or script cannot reach Monid from Claude.ai chat, so chat users need the Monid MCP connector.

## 2. Scheduling

| Option | Runs where | Files persist between runs | Network | Secrets | Notify |
|---|---|---|---|---|---|
| Claude Code `/loop`, cron tools ([docs](https://code.claude.com/docs/en/scheduled-tasks)) | Open session on your machine. Expires after 7 days | Yes (local) | Yes | Shell env | Push via Remote Control |
| Claude Desktop local scheduled task ([docs](https://code.claude.com/docs/en/desktop-scheduled-tasks)) | Your machine. App open and awake; one catch-up run after sleep | Yes (local folder) | Yes | Shell env plus the local `monid` key store | OS notification |
| Claude cloud scheduled tasks, formerly routines ([docs](https://code.claude.com/docs/en/routines)). Pro and up, research preview | Anthropic cloud, at most hourly | **No.** Fresh clone each run; Claude pushes to `claude/*` branches unless the prompt says otherwise | Default "Trusted" allowlist. `api.monid.ai` must be added (Custom) or covered by a network secret. Connectors bypass the allowlist | Env vars (readable by anyone using the environment) or network secrets injected by a proxy ([docs](https://code.claude.com/docs/en/cloud-environments)) | Connectors (Slack, Gmail). A per-task push setting exists per the changelog **[U]** |
| Claude.ai / Cowork scheduled tasks ([help](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork)) | Remote. Paid plans | Only "files saved to your Claude account" **[U]** | Code egress limited as above. Connectors work | None for code | Phone push on cloud Cowork (search summary) **[U]** |
| GitHub Actions + `anthropics/claude-code-action@v1` ([docs](https://code.claude.com/docs/en/github-actions)) | GitHub runner, `schedule:` cron | Commit back to the repo | Full | `secrets.*` mapped to `env:` | Open an issue (GitHub emails you) |
| cron/launchd + `claude -p` | Your machine | Yes | Yes | Shell env | Your own |
| ChatGPT / Codex scheduled tasks ([docs](https://learn.chatgpt.com/docs/automations)) | Desktop app (computer on) for local projects. Web runs remotely | Desktop: yes. Web: "don't keep a local folder… between runs" | Desktop follows the sandbox setting; workspace-write **blocks network**, so the task needs full access | Codex `config.toml`, shell env | Push, email, or SMS in Settings › Notifications ([help](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt)) |
| Cursor Automations ([docs](https://cursor.com/docs/cloud-agent/automations)). Teams and Enterprise | Cursor cloud VM. Cron supported; a repo is optional | `MEMORIES.md` persists per automation. The filesystem does not | Per the environment's egress rules ([docs](https://cursor.com/docs/cloud-agent/security-network)) | Runtime Secrets (env vars, redacted from transcripts) | Slack |
| OpenClaw `openclaw automations create` / Hermes `hermes cron create --skill job-hunt --deliver telegram` | Your gateway host | Yes (local) | Yes | `skills.entries.<name>.apiKey` / `~/.hermes/.env` | Chat channels (Telegram etc.) |

**ChatGPT and MCP:** Web scheduled tasks "can use… connected tools, skills, and plugins." Adding a custom remote MCP server (Plugins › + › Add custom MCP server) is documented for Business, Enterprise, and Edu with developer mode on. Whether Plus or Pro can do it is **[U]**: the help article returned 403. Monid's OAuth metadata advertises `offline_access`, which ChatGPT needs for refresh tokens. That means an OAuth connector should keep working unattended.

## 3. Headless auth (checked on this machine)

- Installed CLI is 0.1.7; npm latest is 0.1.8, matching the `version: 0.1.8` in monid-SKILL.md. 0.1.7 lacks `feedback`, `dispute`, and `generate_session_id`. Pin `@monid-ai/cli@latest` in setup.
- **No API-key env var.** I grepped both the 0.1.7 and 0.1.8 bundles. The only env vars they read are `MONID_API_BASE_URL`, `MONID_SETUP_CLIENT`, `MONID_SETUP_EMAIL`, `XDG_CONFIG_HOME`, `NO_COLOR`/`TERM`, and debug vars. Keys are stored in `${XDG_CONFIG_HOME:-~/.config}/monid/credentials.yaml` (mode 600), with the active label kept in `config.yaml`. Every command fails with "No active API key" if neither file has one.
- **Pattern for unattended runs:** the platform injects `MONID_API_KEY`. The script then runs `monid keys list -j` and, if no key is active, `monid keys add -k "$MONID_API_KEY" -l env` (the first key auto-activates). Caveat: the key shows up in argv, so it is visible to `ps` for a moment. On an ephemeral runner, point `XDG_CONFIG_HOME` at a temp dir.
- **Fallback that skips the CLI:** the REST API is `https://api.monid.ai` with `Authorization: Bearer <key>`. The CLI calls `/v1/discover`, `/v1/inspect`, `/v1/run`, `/v1/wallet/balance`, and `/v1/auth/whoami`. stdlib `urllib` can call these directly, which also removes the npm dependency.
- **The MCP endpoint accepts the API key as a Bearer token.** I tested this; it is not in Monid's docs. Without auth, `initialize` returned 401 with a `WWW-Authenticate` header pointing to Clerk OAuth. With `Authorization: Bearer <monid key>` it returned 200, and `tools/list` returned 13 tools (`monid_discover`, `monid_inspect`, `monid_run`, `monid_get_run`, `monid_balance`, …). Headless MCP therefore works where a client accepts static headers:
  - Codex: `bearer_token_env_var = "MONID_API_KEY"` ([docs](https://learn.chatgpt.com/docs/extend/mcp))
  - Claude Code: `.mcp.json` headers, i.e. `"Authorization": "Bearer ${MONID_API_KEY}"`
  - Claude cloud tasks: a network secret on `mcp.monid.ai` / `api.monid.ai`. Whether the proxy overrides a placeholder header the CLI sends is **[U]**.
- In claude.ai and ChatGPT, OAuth is completed once in the UI. The token is stored server-side, so scheduled runs do not need a person present.

## 4. State when the filesystem resets

| Store | Pros | Cons |
|---|---|---|
| **Git repo** (`state/jobs.jsonl`) | Native to Claude cloud tasks and GitHub Actions. Diffable history | A run has to commit and push. Cloud tasks push to `claude/*` unless the prompt names a branch |
| **Monid sfs** | Free: `/put`, `/cat`, `/ls` are $0/call, 1 GB per workspace, files never auto-deleted (`/rm` frees quota), path ≤1024 chars, file ≤1 GiB. Uses the same key as everything else | Bytes move by `curl` to a signed URL on `sfs.monid.ai`, so it needs a shell and an allowlist entry for that host. Not usable from chat-only MCP **[U]** |
| Google Sheet / Notion | Reachable through first-party connectors in claude.ai and ChatGPT. Easy for the user to read | Needs connector setup. Weak for dedupe at scale |
| Cursor `MEMORIES.md` / Codex in-chat tasks | Zero setup | Platform-locked and not structured |

Recommendation: keep state behind a small adapter in the script, with `local` (default), `git` (commit if inside a repo), and `sfs` (when there's no persistent disk but there is a shell) backends. Chat-only users get a Sheet or Notion through connectors.

## 5. Recommendation

| Platform | On-demand | Daily schedule | State | Notify |
|---|---|---|---|---|
| Claude Code (local) | ✅ CLI | ✅ Desktop task or cron + `claude -p` (machine on) | local | OS / Remote Control push |
| Claude cloud scheduled task | ✅ | ✅ machine off | git or sfs | Slack/Gmail connector; push [U] |
| GitHub Actions (any user) | `workflow_dispatch` | ✅ | git | issue → email |
| Codex / ChatGPT desktop | ✅ | ✅ (computer on, full-access sandbox) | local | push/email |
| Claude.ai / ChatGPT chat | ✅ via Monid MCP | ✅ (connector-only) | Sheet/Notion | push [U] / push+email |
| Cursor | ✅ | ✅ Teams+ only | git/sfs, MEMORIES | Slack |
| Gemini CLI, OpenClaw, Hermes | ✅ | cron + headless / built-in cron | local | Telegram etc. (OpenClaw/Hermes) |

### Claude Code (on demand plus daily, machine on)
1. `npm i -g @monid-ai/cli@latest && monid keys add -k <your key> -l main`
2. `git clone <you>/job-hunt && cp -r job-hunt/skills/job-hunt ~/.claude/skills/`
3. Ask "run job-hunt" in Claude Code.
4. Daily: in Claude Desktop, go to Code › Scheduled › New task. Turn on "Only on this computer", set the folder to your tracker directory and the schedule to Daily 9:07, with the prompt "Run /job-hunt daily and summarize new matches." Click **Run now** once and "always allow" each prompt.

### Daily with the laptop closed (GitHub Actions)
1. Create a private repo with `.claude/skills/job-hunt/` and `state/`.
2. Add repo secrets `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`) and `MONID_API_KEY`.
3. Add `.github/workflows/job-hunt.yml`: on `schedule: - cron: "7 13 * * *"` and `workflow_dispatch`, run `actions/checkout@v6`, then `npm i -g @monid-ai/cli`, then `anthropics/claude-code-action@v1` with `prompt: "/job-hunt daily; commit state/; open an issue listing new matches"`, `env: MONID_API_KEY: ${{ secrets.MONID_API_KEY }}` (that step env reaches Claude's Bash **[U]**), and `permissions: contents: write, issues: write, id-token: write`. Pass `claude_args: --allowedTools "Bash,Read,Write"`.
4. GitHub emails you each new issue. Public repos have their schedules disabled after 60 days without activity.

### Codex / ChatGPT desktop
1. Install the CLI and key as in step 1 above, then `cp -r skills/job-hunt ~/.agents/skills/`.
2. Ask "$job-hunt run".
3. Go to Scheduled › New task, prompt "$job-hunt daily", choose the project folder, set sandbox to full access (workspace-write blocks network), and pick a daily schedule. Turn on push or email under Settings › Notifications.

**Chat-only (claude.ai, ChatGPT):** add the connector `https://mcp.monid.ai/v1` and sign in, upload the skill zip (Claude), then ask Claude or ChatGPT to "do this every weekday at 9" and choose a Sheet or Notion page for state.
