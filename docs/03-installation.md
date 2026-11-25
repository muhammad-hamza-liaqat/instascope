# Installation

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

**If you have a list of proxies** (like `ip:port:user:pass`), paste them comma-separated:
```
PROXIES=1.2.3.4:8000:user:pass,5.6.7.8:8000:user:pass
```

**If you have one rotating link**, use this instead:

In `.env`, set the link your proxy company gives you:
```
PROXY_URL=http://USERNAME:PASSWORD@gate.provider.com:7000
```

`.env` and `accounts.txt` are never uploaded to GitHub, so your passwords stay private.

---

[← Prerequisites](02-prerequisites.md) · [← Back to README](../README.md) · [Running →](04-running.md)
