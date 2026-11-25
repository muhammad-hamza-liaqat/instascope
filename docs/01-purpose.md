# Purpose

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

[← Back to README](../README.md) · [Prerequisites →](02-prerequisites.md)
