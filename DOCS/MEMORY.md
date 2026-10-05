# Memory

Significant project decisions, the reasoning behind them, and the alternatives that were turned down.

Log a decision when a real fork in the road existed, the reason is not visible in the code, and reversing it later without knowing that reason would cost something. Skip routine implementation choices, feature announcements, and anything that belongs in `CHANGELOG.md` or `ROADMAP.md`.

## 2026-10-05

### Decision: Keep the model in a shared backend, separate from the stdio MCP process

What was decided: `pulpie-mcp` (stdio, `server.py`) never imports torch. It talks over HTTP to one backend (`backend/app.py`) that holds the model, and every session shares that backend.

Why: Stdio MCP servers start once per client session. Loading Pulpie in each one would mean a cold model load per session and a separate copy of the model on the GPU for every open agent.

Rejected: Loading Pulpie directly inside the stdio server. It's simpler to install, but you'd get duplicate GPU memory and slow starts. Also rejected: mounting MCP inside the private `pulpie-ui` FastAPI app, because the UI isn't being published and the MCP server has to work on its own.

### Decision: Auto-start the backend, and let the port act as the lock

What was decided: `launcher.ensure_backend()` spawns `pulpie-mcp serve` detached when `/health` doesn't answer on a local URL. `bind_socket()` binds and listens before the model loads, so racing copies fail with `EADDRINUSE` in about a second and exit. There's no lock file.

Why: The user wanted zero manual setup. Binding first makes the race cheap and works across platforms without a lock-file dependency.

Rejected: A `fcntl` lock file, which is Unix-only, or the `filelock` package, which adds a dependency for something the port already guarantees. Also rejected: a user-managed backend with systemd docs, since the user chose auto-start.

### Decision: Idle shutdown after 30 minutes, counted from model ready

What was decided: The backend exits after `PULPIE_IDLE_TIMEOUT` (1800 by default) seconds without `/extract` requests. The idle clock starts only once `ModelHolder.wait_loaded()` returns, and `/health` polls don't count as activity.

Why: It frees about 420 MB of VRAM when nobody is using it, and auto-start brings it back on demand. Counting from process start shut the backend down during a slow model load in testing.

Rejected: Keeping the backend running forever, which holds GPU memory all day.

### Decision: Block private and local addresses by default

What was decided: `netguard.make_client()` checks every request hop, including redirects, and rejects non-global IPs unless `allow_private` is set. The MCP client sends `allow_private` from `PULPIE_ALLOW_PRIVATE`.

Why: The project is published, and agents fetch URLs they found on the open web. Blocking LAN, loopback, and metadata addresses is the safer default for other people. The env var covers local dev servers.

Rejected: Allowing everything by default. It's convenient for the author, but a bad default for a published tool.

### Decision: Saving location is a tool parameter the agent fills in

What was decided: `save_markdown` and `crawl_docs` take an optional absolute `directory`. Empty means the global library (`$PULPIE_HOME/library/<host>`). The server instructions tell the agent to use a project folder for project work and the library for general research.

Why: The agent knows whether the task belongs to a project. The user preferred this over a fixed setting.

Rejected: A global-only library, which keeps project reference docs out of the repo, and a per-project-only folder, which has no home for general research.
