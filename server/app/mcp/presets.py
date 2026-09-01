"""Pre-configured MCP server presets, plus the placeholder substitution they need.

Presets describe *how* to reach a server, not the secrets required to do so.
Anything the reader has to supply is written as a ``${NAME}`` placeholder inside
``args``/``env``/``headers`` and declared in ``inputs``, so the UI knows which
fields to prompt for and :func:`resolve_preset` knows what to fill in. Nothing
here is a shell, so ``${NAME}`` is expanded by this module -- previously these
were shell-style ``${NAME:-}`` strings that no shell ever saw, and the literal
text ``${FIGMA_ACCESS_TOKEN:-}`` was being sent as the bearer token.
"""

from __future__ import annotations

import os
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


PRESETS: dict[str, dict[str, Any]] = {
    # -- Figma ---------------------------------------------------------------
    #
    # Figma ships its own MCP server inside the desktop app: Figma menu ->
    # Preferences -> "Enable local MCP server". It listens on 127.0.0.1:3845 and
    # speaks Streamable HTTP at /mcp -- not SSE on :3000, which is what this
    # preset used to point at, and which nothing has ever served.
    #
    # It reads whatever is selected in the running Figma app, so it needs no
    # token at all: the desktop app is already signed in.
    "figma": {
        "name": "figma",
        "description": (
            "Figma Dev Mode MCP server built into the Figma desktop app. Reads the "
            "current selection: code, screenshots, variables, Code Connect mappings. "
            "Requires Figma desktop running with Preferences > Enable local MCP server."
        ),
        "transport": "http",
        "url": "http://127.0.0.1:3845/mcp",
        "homepage": "figma.com",
        "inputs": [],
        "auto_approve": [
            "get_code",
            "get_screenshot",
            "get_metadata",
            "get_variable_defs",
            "get_code_connect_map",
        ],
    },
    # The REST-API route, for when Figma desktop is not running (headless boxes,
    # a server that is not the reader's laptop) or when the file is addressed by
    # URL rather than by selection. `--stdio` is not optional: without it the
    # package starts its own HTTP server and never speaks a word on stdin, which
    # is exactly the 15s initialize timeout this preset used to produce.
    "figma_api": {
        "name": "figma_api",
        "description": (
            "Figma via the REST API using a personal access token. Works without the "
            "desktop app; addresses files by URL or key. Needs a token with "
            "file_content:read scope from Figma > Settings > Security."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "figma-developer-mcp", "--stdio"],
        "env": {"FIGMA_API_KEY": "${FIGMA_API_KEY}"},
        "homepage": "figma.com",
        "inputs": [
            {
                "key": "FIGMA_API_KEY",
                "label": "Figma personal access token",
                "required": True,
                "secret": True,
                "help": "Figma > Settings > Security > Personal access tokens.",
            }
        ],
        "auto_approve": ["get_figma_data", "download_figma_images"],
    },
    # -- Google Workspace ----------------------------------------------------
    #
    # All three of these are browser-OAuth servers: they open a consent page on
    # first run and cache the grant under ~/.config/google-workspace-mcp. There
    # is no token to paste, which is why `inputs` is empty -- the previous
    # GOOGLE_APPLICATION_CREDENTIALS / GMAIL_TOKEN fields were prompting for
    # values the packages never read.
    # There is no separate "gmail" preset. There was, and it ran the same
    # package as this one against the same OAuth grant -- installing both
    # registered every tool twice and gave the model two names for one thing.
    "google_workspace": {
        "name": "google_workspace",
        "description": (
            "Gmail, Calendar, Drive, Docs and Sheets in one server. Opens a Google "
            "consent page in a browser the first time it runs, then caches the grant."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@presto-ai/google-workspace-mcp"],
        "env": {},
        "inputs": [],
        "auto_approve": ["list_events", "search_emails", "get_file"],
        "homepage": "workspace.google.com",
        "notes": (
            "First run needs an interactive browser. Run "
            "`npx -y @presto-ai/google-workspace-mcp` once in a terminal to complete "
            "the OAuth grant before enabling it here."
        ),
    },
    "google_calendar": {
        "name": "google_calendar",
        "description": "List, create, update and search Google Calendar events.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@iflow-mcp/mcp-google-workspace"],
        "env": {},
        "homepage": "calendar.google.com",
        "inputs": [],
        "auto_approve": ["list_events", "get_event"],
        "notes": "First run needs an interactive browser to complete the OAuth grant.",
    },
    # -- Code and docs -------------------------------------------------------
    "github": {
        "name": "github",
        "description": (
            "GitHub's own hosted server: repositories, issues, pull requests, code "
            "search and Actions. Authenticates with a personal access token."
        ),
        "transport": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
        "homepage": "github.com",
        "inputs": [
            {
                "key": "GITHUB_TOKEN",
                "label": "GitHub personal access token",
                "required": True,
                "secret": True,
                "help": "github.com > Settings > Developer settings > Personal access tokens.",
            }
        ],
        "auto_approve": ["search_repositories", "get_file_contents", "list_issues"],
    },
    "deepwiki": {
        "name": "deepwiki",
        "description": (
            "Ask questions about any public GitHub repository and get answers from "
            "generated documentation. No account and no token."
        ),
        "transport": "http",
        "url": "https://mcp.deepwiki.com/mcp",
        "homepage": "deepwiki.com",
        "inputs": [],
        "auto_approve": ["read_wiki_structure", "read_wiki_contents", "ask_question"],
    },
    "context7": {
        "name": "context7",
        "description": (
            "Up-to-date documentation and code examples for libraries and frameworks, "
            "fetched per version. Works without a key; one raises the rate limit."
        ),
        "transport": "http",
        "url": "https://mcp.context7.com/mcp",
        "headers": {"Authorization": "Bearer ${CONTEXT7_API_KEY}"},
        "homepage": "context7.com",
        "inputs": [
            {
                "key": "CONTEXT7_API_KEY",
                "label": "Context7 API key",
                "required": False,
                "secret": True,
                "help": "Optional. Leave blank to use the anonymous rate limit.",
            }
        ],
        "auto_approve": ["resolve-library-id", "get-library-docs"],
    },
    # -- Research and the web ------------------------------------------------
    "exa": {
        "name": "exa",
        "description": (
            "Neural web search built for models rather than for people, with the page "
            "contents returned alongside the results."
        ),
        "transport": "http",
        "url": "https://mcp.exa.ai/mcp",
        "homepage": "exa.ai",
        "inputs": [],
        "auto_approve": ["web_search_exa", "get_code_context_exa"],
    },
    "huggingface": {
        "name": "huggingface",
        "description": (
            "Search models, datasets, Spaces and papers on the Hugging Face Hub. "
            "Works anonymously; a token widens what is visible."
        ),
        "transport": "http",
        "url": "https://huggingface.co/mcp",
        "headers": {"Authorization": "Bearer ${HF_TOKEN}"},
        "homepage": "huggingface.co",
        "inputs": [
            {
                "key": "HF_TOKEN",
                "label": "Hugging Face access token",
                "required": False,
                "secret": True,
                "help": "Optional. Needed only for private repositories.",
            }
        ],
        "auto_approve": ["model_search", "dataset_search", "paper_search"],
    },
    "firecrawl": {
        "name": "firecrawl",
        "description": (
            "Turn any web page or whole site into clean markdown -- scraping, "
            "crawling and structured extraction."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "firecrawl-mcp"],
        "env": {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"},
        "homepage": "firecrawl.dev",
        "inputs": [
            {
                "key": "FIRECRAWL_API_KEY",
                "label": "Firecrawl API key",
                "required": True,
                "secret": True,
                "help": "firecrawl.dev > Dashboard > API Keys.",
            }
        ],
        "auto_approve": ["firecrawl_scrape", "firecrawl_search"],
    },
    "playwright": {
        "name": "playwright",
        "description": (
            "Drive a real browser: open pages, click, fill forms and read what is on "
            "screen. Works from the accessibility tree rather than screenshots."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        "homepage": "playwright.dev",
        "inputs": [],
        "auto_approve": ["browser_snapshot", "browser_navigate"],
        "notes": (
            "Runs on the server, not on your phone -- it drives a browser on the "
            "machine Courier is installed on. First run downloads a browser engine."
        ),
    },
    # -- Local to the server -------------------------------------------------
    "filesystem": {
        "name": "filesystem",
        "description": (
            "Read, write and search files in one directory on the machine running "
            "Courier. Nothing outside the directory you name is reachable."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "${ROOT_DIR}"],
        "homepage": "modelcontextprotocol.io",
        "inputs": [
            {
                "key": "ROOT_DIR",
                "label": "Directory to expose",
                "required": True,
                "secret": False,
                "help": "An absolute path on the server, e.g. /Users/you/Documents.",
            }
        ],
        "auto_approve": ["read_file", "list_directory", "search_files"],
        "notes": (
            "The directory is the whole boundary: everything under it is readable "
            "and writable by the model, and nothing above it is reachable."
        ),
    },
    "sequential_thinking": {
        "name": "sequential_thinking",
        "description": (
            "A scratchpad for working a hard problem through in steps, with the "
            "ability to revise earlier steps. Local, no network, no key."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "homepage": "modelcontextprotocol.io",
        "inputs": [],
        "auto_approve": ["sequentialthinking"],
    },
    # -- The escape hatch ----------------------------------------------------
    #
    # Courier speaks no OAuth, so the many hosted servers that require it --
    # Notion, Linear, Slack, Atlassian -- cannot be reached directly. mcp-remote
    # runs the browser half of that dance locally and presents the result as an
    # ordinary stdio server, which is the one thing that makes them reachable
    # at all from here.
    "remote_bridge": {
        "name": "remote_bridge",
        "description": (
            "Connect to a hosted MCP server that requires OAuth sign-in -- Notion, "
            "Linear, Slack, Atlassian and the like -- by running the sign-in locally."
        ),
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-remote", "${REMOTE_URL}"],
        "homepage": "modelcontextprotocol.io",
        "inputs": [
            {
                "key": "REMOTE_URL",
                "label": "Remote MCP endpoint",
                "required": True,
                "secret": False,
                "help": "e.g. https://mcp.notion.com/mcp or https://mcp.linear.app/sse",
            }
        ],
        "notes": (
            "First run opens a browser on the machine running Courier to complete "
            "the OAuth grant, then caches it under ~/.mcp-auth. Rename the server "
            "afterwards so several bridges can coexist."
        ),
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Return every preset, each tagged with the id it is instantiated by."""
    return [{"id": preset_id, **preset_data} for preset_id, preset_data in PRESETS.items()]


def get_preset(name: str) -> dict[str, Any] | None:
    """Get a preset definition by id."""
    return PRESETS.get(name.strip().lower())


def substitute(value: Any, values: dict[str, str]) -> Any:
    """Replace every ``${NAME}`` in a string, list or dict with a supplied value.

    Falls back to the process environment, then to the empty string. Walks
    containers so a placeholder is as usable in ``args`` as in ``env``.
    """
    if isinstance(value, str):
        return _PLACEHOLDER.sub(
            lambda m: values.get(m.group(1)) or os.environ.get(m.group(1), ""), value
        )
    if isinstance(value, list):
        return [substitute(v, values) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, values) for k, v in value.items()}
    return value


def missing_inputs(preset: dict[str, Any], values: dict[str, str]) -> list[str]:
    """Which required inputs the reader has not supplied and the environment lacks."""
    missing: list[str] = []
    for spec in preset.get("inputs", []) or []:
        if not isinstance(spec, dict) or not spec.get("required"):
            continue
        key = str(spec.get("key", ""))
        if key and not (values.get(key) or os.environ.get(key)):
            missing.append(key)
    return missing


def _unfilled(template: Any, values: dict[str, str]) -> bool:
    """Whether a template's only content was a placeholder nobody filled in."""
    if not isinstance(template, str):
        return False
    names = _PLACEHOLDER.findall(template)
    if not names:
        return False
    return all(not (values.get(n) or os.environ.get(n)) for n in names)


def resolve_preset(preset: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """A copy of the preset with every placeholder in args/env/headers filled in.

    An *optional* input nobody filled in takes its whole entry with it. A
    preset like context7 writes ``Authorization: Bearer ${CONTEXT7_API_KEY}``,
    and substituting an empty key would send the literal header ``Bearer ``,
    which reads to a server as a malformed credential rather than as no
    credential -- answered with a 401 that looks like a wrong key.
    """
    resolved = dict(preset)
    for field in ("args", "url", "command", "cwd"):
        if preset.get(field) is not None:
            resolved[field] = substitute(preset[field], values)

    for field in ("env", "headers"):
        template = preset.get(field)
        if not isinstance(template, dict):
            continue
        resolved[field] = {
            key: substitute(value, values)
            for key, value in template.items()
            if not _unfilled(value, values)
        }
    return resolved
