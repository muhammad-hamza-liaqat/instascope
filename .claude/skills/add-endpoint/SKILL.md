---
name: add-endpoint
description: Add or change an InstaScope API endpoint following the project's layering (schema, service, router, test, docs). Use for any new route, query parameter or response field.
---

# Add an endpoint

1. **Schema**: add or extend response/request models in `app/schemas/` (pydantic v2; use
   `computed_field` for derived values; keep `raw` fields `exclude=True`).
2. **Persistence** (if needed): queries go in `app/db/repository.py` only. New columns go in
   `app/db/models.py`; there is no Alembic yet, so note the schema change in `CLAUDE.md`.
3. **Service**: business logic in `app/services/*_service.py`. Normalize handles with
   `normalize_username`. Raise `ScraperError` subclasses; put partial-data issues in `warnings`.
4. **Route**: thin handler in `app/api/v1/<area>.py` using `Annotated` params and the
   `ProfileSvc`/`JobSvc` dependencies. Register new routers in `app/api/v1/router.py` (API-key
   auth is applied there). Give each route a `summary=`.
5. **Test**: add to `tests/test_api.py` with `respx` mocks (`mock_profile`, `mock_clips` in
   `conftest.py`). Cover the success path and at least one error path.
6. Run `make check`.
7. **Docs**: README endpoint table, `CLAUDE.md` architecture table, then run the `sync-docs` checklist.
