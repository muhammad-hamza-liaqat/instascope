# Running

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

[← Installation](03-installation.md) · [← Back to README](../README.md) · [Commands →](05-commands.md)
