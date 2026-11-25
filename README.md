# InstaScope

Give it an Instagram username and it returns that profile's details.

---

## 1. Purpose

InstaScope is our own Instagram scraper, like Apify but ours: no per-run fees, and we keep
all the data.

You send a username, and it returns:

- **Profile:** name, bio, profile picture, followers, following, post count, links, verified or business
- **Posts:** caption, likes, comments, views, hashtags, date, images
- **Reels:** plays, views, likes, audio, video link
- **History:** how followers changed over time

It also:

- **Saves everything** in a database, including copies of the pictures.
- **Answers repeat requests fast** from the database (within 1 hour).
- **Runs by itself:** it logs in your spare accounts, rotates proxies, rests blocked accounts, and sends alerts.

---

## 2. Prerequisites

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

## 3. How to install

```bash
# 1. Download the code
git clone https://github.com/muhammad-hamza-liaqat/instascope.git
cd instascope

# 2. Install everything (Python + libraries)
make install

# 3. Create your settings file
cp .env.example .env
```

### Add a spare account (recommended)

1. Create a file called `accounts.txt` in the project folder.
2. Add one line per account:
   ```
   your_spare_username:your_password
   ```
3. In `.env`, set:
   ```
   IG_ACCOUNTS_FILE=accounts.txt
   ```

### Add a proxy (optional)

In `.env`, set the link your proxy company gives you:
```
PROXY_URL=http://USERNAME:PASSWORD@gate.provider.com:7000
```

`.env` and `accounts.txt` are never uploaded to GitHub, so your passwords stay private.

---

## 4. How to run

```bash
make dev
```

Then open **http://127.0.0.1:8000** in your browser. It shows the API page.

### Try it

1. Click **GET /api/v1/profiles/{username}**.
2. Click **Try it out**.
3. Type a username, for example `natgeo`.
4. Click **Execute**.

Or use the terminal:
```bash
curl http://127.0.0.1:8000/api/v1/profiles/natgeo
```

### Check that everything is OK

Open **http://127.0.0.1:8000/health/ready** to see:
- how many accounts work right now (`active`)
- whether a proxy is set
- recent errors and alerts

### Stop it

Press `Ctrl + C` in the terminal.

### Run with Docker (optional)

```bash
make docker
```

---

## 5. Useful commands

| Command | What it does |
|---|---|
| `make install` | install everything |
| `make dev` | start the server |
| `make test` | run the tests |
| `make check` | run all checks (code style, types, tests) |
| `make docker` | run in Docker |
| `make memory-sync` | save Claude's project memory into the repo |

---

## 6. Common problems

| You see | Meaning | Fix |
|---|---|---|
| `"error": "blocked"` | Instagram blocked your IP | add a spare account and/or a proxy |
| account status `checkpoint` | Instagram wants to confirm the login | open Instagram on that account and tap **"This was me"** |
| account status `bad_credentials` | wrong password | fix it in `accounts.txt` |
| warning "Only the first 12 posts…" | no logged-in account | add a spare account |
| `{"detail":"Not Found"}` | wrong address | use http://127.0.0.1:8000 |

---

## 7. For developers

- API: one endpoint, `GET /api/v1/profiles/{username}?posts_limit=12&reels_limit=12&refresh=false`
- Every setting is explained in `.env.example`.
- Code guide and architecture: `CLAUDE.md`.
- Tests work offline (Instagram is mocked): `make check`.

> Scraping Instagram is against Meta's Terms of Use. Collect only public data, don't send too
> many requests, and follow privacy laws for the data you store.
