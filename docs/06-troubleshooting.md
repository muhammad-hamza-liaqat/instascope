# Troubleshooting

| You see | Meaning | Fix |
|---|---|---|
| `"error": "blocked"` | Instagram refused the request | run `make diagnose`, then add a spare account and/or a proxy |
| account status `checkpoint` | Instagram wants to confirm the login | open Instagram on that account and tap **"This was me"** |
| `checkpoint` but **no notification** in Instagram | Instagram blocked a login from the proxy's country | set `ACCOUNTS_USE_PROXY=false` in `.env`, log in once in the app/browser, restart |
| account status `bad_credentials` | wrong password | fix it in `accounts.txt` |
| warning "Only the first 12 posts…" | no logged-in account | add a spare account |
| `{"detail":"Not Found"}` | wrong address | use http://127.0.0.1:8000 |

---

[← Commands](05-commands.md) · [← Back to README](../README.md) · [For developers →](07-developers.md)
