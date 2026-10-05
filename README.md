# pulpie-mcp

![pulpie-mcp](pulpie.png)

An MCP server that gives AI agents their own tools for turning web pages into clean Markdown. It runs the [Pulpie](https://github.com/feyninc/pulpie) content extraction model locally, so tables, code blocks, links, and images come through intact instead of getting summarized away.

The agent can read a page inline, save it as a `.md` file, crawl a whole docs section into a folder, and check what it already saved before fetching again.

## Why I made it

I already had a little local UI for Pulpie that I used to pull docs and articles into Markdown, and it works really well. But I was still the one doing the pulling. Most agent fetch tools run pages through a small model that summarizes or trims them, which is not what you want when the agent needs the actual API reference.

So this gives the agent the same thing I was using. It can grab full documentation whenever it needs it and save it somewhere useful, and I don't have to go do it by hand.

## What it does

| Tool | What it's for |
|---|---|
| `fetch_markdown` | Read a page right now. The Markdown comes back inline, cut off at `max_chars` (40,000 by default). |
| `save_markdown` | Save one page as a `.md` file. Returns the path and a heading outline instead of the whole page, so it doesn't eat the agent's context. |
| `crawl_docs` | Save a documentation section as a folder of `.md` files plus an `index.md`. Up to 500 pages per crawl, 50 by default. |
| `list_library` | List saved docs with their source URL and fetch time, optionally filtered by a search term. |

A few details worth knowing:

- **Where files go is up to the agent.** For project work it passes a folder inside the project, like `/path/to/project/DOCS/reference/uv`. For general research it leaves `directory` empty and the file goes into the global library at `~/.pulpie/library/<site>/`.
- **Every saved file has frontmatter** with `title`, `source`, and `fetched`, so you (and the agent) can always tell where a file came from and how old it is.
- **Crawling checks the sitemap first.** It looks at `robots.txt`, then `<docs path>/sitemap.xml`, then `/sitemap.xml`. If none of those exist, it follows links from the pages instead. It only crawls pages on the same host and under the starting path.
- **Markdown and plain-text URLs pass straight through.** If a URL already serves `text/markdown` or `text/plain` (like `llms.txt`), you get the file as is.

## How it works

There are two pieces:

1. **The MCP server** is what your agent launches. It's small and doesn't load the model.
2. **The backend** is a local HTTP server that holds the Pulpie model in memory and does the actual fetching and extraction.

You never have to start the backend yourself. The first time a tool needs it, the MCP server starts it in the background and waits for the model to load. Every agent session shares that one backend, so you only ever have one copy of the model on your GPU. After 30 minutes with no requests it shuts itself down and frees the memory, and the next request starts it again.

The model is `feyninc/pulpie-orange-small`, a 210M parameter encoder. It uses around 420 MB of VRAM. On my RTX 4090 laptop, a cold start (including loading an already downloaded model) took about 10 seconds. After that, a single docs page saved in under a second, and an 8-page crawl of the uv docs took about 3 seconds. Most of that is network time.

## Requirements

- Python 3.10 or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- An NVIDIA GPU is nice but not required. Pulpie uses CUDA if it can, then Apple MPS, then falls back to CPU.

Heads up: this pulls in PyTorch, so the install is a few GB.

I've only tested this on Linux with an NVIDIA GPU so far. It should work on macOS, and Windows is untested.

## Installation

Install it as a tool so the `pulpie-mcp` command is on your PATH:

```bash
uv tool install git+https://github.com/pinkpixel-dev/pulpie-mcp
```

Or from a local clone:

```bash
git clone https://github.com/pinkpixel-dev/pulpie-mcp
cd pulpie-mcp
uv tool install .
```

The model downloads from Hugging Face the first time the backend starts.

## Adding it to your agent

### Claude Code

```bash
claude mcp add --scope user pulpie -- pulpie-mcp
```

### Codex

In `~/.codex/config.toml`:

```toml
[mcp_servers.pulpie]
command = "pulpie-mcp"
```

### Other MCP clients

Most clients take a JSON config like this:

```json
{
  "mcpServers": {
    "pulpie": {
      "command": "pulpie-mcp"
    }
  }
}
```

If you add environment variables (see below), put them in the client's `env` block for this server.

## Using it

You don't need special prompts. Just ask for what you want:

- "Pull the uv docs into this project's `DOCS/reference` folder."
- "Read the asyncio queue docs and tell me how `join()` works."
- "Do we already have the FastAPI docs saved somewhere?"

The server tells the agent to save project docs inside the project and general research in the global library, so it usually picks the right spot on its own.

## Configuration

Everything is optional.

| Variable | Default | What it does |
|---|---|---|
| `PULPIE_URL` | `http://127.0.0.1:8787` | Where the backend runs. Only local URLs get auto-started. |
| `PULPIE_HOME` | `~/.pulpie` | Holds the global library and `backend.log`. |
| `PULPIE_LIBRARY` | `$PULPIE_HOME/library` | Where saved docs go when no directory is given. |
| `PULPIE_ALLOW_PRIVATE` | off | Set to `1` to allow `localhost` and LAN addresses. |
| `PULPIE_IDLE_TIMEOUT` | `1800` | Seconds without requests before the backend exits. `0` keeps it running. |
| `PULPIE_START_TIMEOUT` | `600` | How long to wait for the backend to start. The first run includes the model download. |

### Private addresses

By default the server won't fetch `localhost`, `127.0.0.1`, LAN addresses like `192.168.x.x`, or cloud metadata addresses. Agents follow links they find on the open web, so I'd rather block those than have a random page send the agent poking around your network. This is checked on every redirect too.

If you want to pull docs from a local dev server, set `PULPIE_ALLOW_PRIVATE=1`.

## Commands

```bash
pulpie-mcp            # run the MCP server over stdio (your agent does this)
pulpie-mcp serve      # run the backend in the foreground, handy for debugging
pulpie-mcp status     # show whether the backend is running, and on which device
pulpie-mcp stop       # stop the backend
```

If something goes wrong with the backend, check `~/.pulpie/backend.log`.

## Limitations

- **Pages that render entirely in JavaScript** come back mostly empty, because the backend fetches the HTML the server sends and doesn't run a browser. Most docs sites (Docusaurus, MkDocs, VitePress, Sphinx, Next.js) render on the server, so this hasn't been an issue for docs so far.
- **Code blocks are indented, not fenced.** Pulpie converts HTML to Markdown with `html2text`, which writes code as 4-space indented blocks without a language tag. It's valid Markdown, just not as pretty.
- **No PDFs.** Non-HTML pages other than Markdown and plain text are rejected.
- **Saved pages are untrusted web content.** Anything an agent reads from them is data, not instructions.

## Model license

> [!IMPORTANT]
> This project's code is Apache 2.0, but the Pulpie model it downloads, [`feyninc/pulpie-orange-small`](https://huggingface.co/feyninc/pulpie-orange-small), is licensed **CC BY-NC 4.0**. That means non-commercial use only. The `pulpie` Python library itself is Apache 2.0. If you want to use this in a commercial product, check the model license with Feyn first.

pulpie-mcp doesn't bundle or redistribute the model weights. They're downloaded from Hugging Face on your machine.

## Development

```bash
uv sync
uv run pytest
```

The integration test runs the real backend and model against a live page, so it's skipped unless you opt in:

```bash
PULPIE_INTEGRATION=1 uv run pytest tests/test_integration.py
```

## License

Apache 2.0. See [LICENSE](LICENSE).

Pulpie is made by [Feyn](https://github.com/feyninc/pulpie).

Made with 💖 by [Pink Pixel](https://pinkpixel.dev)
