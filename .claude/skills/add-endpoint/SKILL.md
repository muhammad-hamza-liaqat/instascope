---
name: add-endpoint
description: Add data, fields or query parameters to InstaScope's single profile endpoint, following the project's layering (schema, service, route, test, docs). Use for any new response field, new scraped data, or new request option.
---

# Extend the API (single-endpoint rule)

The user wants **one** data endpoint: `GET /api/v1/profiles/{username}` returns everything.
New data goes into `ScrapeResult` as a field; new options become query params on that route.
Add a separate route only if the user explicitly asks for one (health probes are the exception).

1. **Schema**: add the field to `ScrapeResult` or `ProfileData`/`MediaData` in
   `app/schemas/instagram.py` (pydantic v2; `computed_field` for derived values; keep `raw`
   fields `exclude=True`). Give new fields defaults so old cached rows still validate.
2. **Scraping**: new Instagram calls go in `app/scraper/endpoints.py` + `client.py`; parsing in
   `parsers.py` (use `_dig`/`_first`).
3. **Persistence** (if needed): queries only in `app/db/repository.py`; new columns in
   `app/db/models.py`. There is no Alembic yet, so note schema changes in `CLAUDE.md`.
4. **Service**: wire it into `ProfileService.get_profile` in `app/services/profile_service.py`
   for both the live and cache paths. Partial-data issues go in `warnings`, not exceptions.
5. **Route**: only add an `Annotated` query param in `app/api/v1/profiles.py` if needed.
6. **Test**: `tests/test_api.py` with `respx` mocks (`mock_profile`, `mock_clips` in
   `conftest.py`). Cover the success path and one error/fallback path. `test_single_data_endpoint`
   guards the route list; update it only if the user approved a new route.
7. `make check`, then follow the `sync-docs` checklist (README response table, CLAUDE.md).
