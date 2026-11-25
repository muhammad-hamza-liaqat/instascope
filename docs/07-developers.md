# For developers

- API: one endpoint, `GET /api/v1/profiles/{username}?posts_limit=12&reels_limit=12&refresh=false`
- Every setting is explained in `.env.example`.
- Code guide and architecture: `CLAUDE.md`.
- Tests work offline (Instagram is mocked): `make check`.

> Scraping Instagram is against Meta's Terms of Use. Collect only public data, don't send too
> many requests, and follow privacy laws for the data you store.

---

[← Troubleshooting](06-troubleshooting.md) · [← Back to README](../README.md)
