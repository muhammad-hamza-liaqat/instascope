"""Instagram endpoint URLs. When Instagram changes something, start here.

Unverified against live Instagram at creation time (2026-11-25) — see CLAUDE.md.
"""

BASE_URL = "https://www.instagram.com"

# Logged-out OK (usually): profile + first 12 grid posts.
WEB_PROFILE_INFO = f"{BASE_URL}/api/v1/users/web_profile_info/"

# Needs a logged-in session: full post feed pagination (v1 media objects).
USER_FEED = BASE_URL + "/api/v1/feed/user/{user_id}/"

# Reels tab (POST, form-encoded). Sometimes works logged-out; escalates to session when refused.
CLIPS_USER = f"{BASE_URL}/api/v1/clips/user/"
