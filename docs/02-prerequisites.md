# Prerequisites

You need:

| What | Why | How to get it |
|---|---|---|
| **Mac or Linux** (Windows with WSL) | to run it | — |
| **uv** | installs Python and all libraries | `brew install uv` |
| **make** | short commands like `make dev` | already on Mac (or `xcode-select --install`) |
| **git** | to download the code | already on Mac |

Optional, but needed for real Instagram data (Instagram blocks a single home IP):

| What | Why |
|---|---|
| **A spare Instagram account** | an extra account just for this tool, not your personal one, with 2FA off |
| **A proxy** (residential, rotating) | makes requests come from many different IPs, e.g. Webshare, IPRoyal, Decodo |
| **Docker** (optional) | only if you want to run it in Docker |

You don't need to install Python yourself; uv does it.

---

[← Purpose](01-purpose.md) · [← Back to README](../README.md) · [Installation →](03-installation.md)
