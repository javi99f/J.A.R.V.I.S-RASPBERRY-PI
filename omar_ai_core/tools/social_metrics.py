import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from omar_ai_core.settings import get_secret


BASE_URL = "https://zernio.com/api/v1"


def _base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _load_key():
    return get_secret("ZERNIO_API_KEY")


def _load_gemini_key():
    return get_secret("GEMINI_API_KEY")


def _request(path, params=None):
    key = _load_key()
    if not key:
        raise RuntimeError("Zernio API key is not configured.")

    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        BASE_URL + path + query,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _date_range(days):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(int(days or 90), 1))
    return start.isoformat(), end.isoformat()


def _normalize_platform(platform):
    value = str(platform or "instagram").strip().lower()
    aliases = {
        "ig": "instagram",
        "insta": "instagram",
        "instagram": "instagram",
        "tt": "tiktok",
        "tik tok": "tiktok",
        "tiktok": "tiktok",
        "both": "both",
        "all": "both",
    }
    return aliases.get(value, "instagram")


def _platforms(platform):
    normalized = _normalize_platform(platform)
    if normalized == "both":
        return ["instagram", "tiktok"]
    return [normalized]


def _analytics(days=90, platform="instagram"):
    from_date, to_date = _date_range(days)
    return _request(
        "/analytics",
        {
            "platform": _normalize_platform(platform),
            "fromDate": from_date,
            "toDate": to_date,
        },
    )


def _post_metric(post, name, default=0):
    analytics = post.get("analytics") or {}
    return analytics.get(name, default) or default


def _profile_visible_posts(posts, platform_name="instagram"):
    grouped = {}
    for post in posts:
        content_key = " ".join((post.get("content") or "").lower().split())
        if not content_key:
            platform = next(
                (p for p in post.get("platforms", []) if p.get("platform") == platform_name),
                {},
            )
            content_key = platform.get("platformPostUrl") or post.get("_id")

        current = grouped.get(content_key)
        if current is None or _post_metric(post, "views") > _post_metric(current, "views"):
            grouped[content_key] = post

    return list(grouped.values())


def _format_post(post, platform="instagram", include_label=False):
    analytics = post.get("analytics") or {}
    views = analytics.get("views", 0) or 0
    likes = analytics.get("likes", 0) or 0
    comments = analytics.get("comments", 0) or 0
    shares = analytics.get("shares", 0) or 0
    hype = "We are killing it, sir."
    if views < 1000 and likes < 100:
        hype = "A quiet one, sir. Even the empire has off days."
    lines = []
    if include_label:
        lines.append("Instagram" if platform == "instagram" else "TikTok")
    lines.extend([
        f"{hype}\n"
        f"Views: {views}\n"
        f"Likes: {likes}\n"
        f"Comments: {comments}\n"
        f"Shares: {shares}"
    ])
    return "\n".join(lines)


def _format_post_brief(post, platform="instagram", index=None):
    analytics = post.get("analytics") or {}
    platform_item = next(
        (p for p in post.get("platforms", []) if p.get("platform") == platform),
        {},
    )
    published = post.get("publishedAt") or post.get("scheduledFor") or "unknown date"
    content = " ".join((post.get("content") or "").split())
    if len(content) > 100:
        content = content[:97].rstrip() + "..."
    label = f"Post {index}" if index else "Post"
    url = platform_item.get("platformPostUrl")
    metrics = (
        f"views {int(analytics.get('views') or 0):,}, "
        f"likes {int(analytics.get('likes') or 0):,}, "
        f"comments {int(analytics.get('comments') or 0):,}, "
        f"shares {int(analytics.get('shares') or 0):,}"
    )
    if analytics.get("reach") is not None:
        metrics += f", reach {int(analytics.get('reach') or 0):,}"
    line = f"{label}, {published}: {metrics}"
    if content:
        line += f". {content}"
    if url:
        line += f" ({url})"
    return line


def _recent_posts(platform="instagram", username=None, days=180, count=2):
    data = _analytics(days, platform=platform)
    posts = data.get("posts") or []
    normalized_platform = _normalize_platform(platform)
    if username:
        username = str(username).strip().lstrip("@").lower()
        posts = [
            post for post in posts
            if any(
                str(item.get("accountUsername", "")).lower() == username
                for item in post.get("platforms", [])
            )
        ]
    if normalized_platform == "instagram":
        posts = _profile_visible_posts(posts, platform_name=normalized_platform)
    posts = sorted(
        posts,
        key=lambda p: p.get("publishedAt") or p.get("scheduledFor") or "",
        reverse=True,
    )[:max(1, int(count or 2))]
    label = "Instagram" if normalized_platform == "instagram" else "TikTok"
    if not posts:
        return f"Zernio did not return any {label} posts for that account and date range."
    lines = [f"{label} performance for the last {len(posts)} post(s):"]
    for idx, post in enumerate(posts, start=1):
        lines.append("- " + _format_post_brief(post, platform=normalized_platform, index=idx))
    return "\n".join(lines)


def _latest_post_for_platform(platform, username=None, days=180, include_label=False):
    data = _analytics(days, platform=platform)
    posts = data.get("posts") or []
    if username:
        username = str(username).strip().lstrip("@").lower()
        posts = [
            post for post in posts
            if any(
                str(platform.get("accountUsername", "")).lower() == username
                for platform in post.get("platforms", [])
            )
        ]
    if platform == "instagram":
        posts = _profile_visible_posts(posts, platform_name=platform)
    posts = sorted(
        posts,
        key=lambda p: p.get("publishedAt") or p.get("scheduledFor") or "",
        reverse=True,
    )
    if not posts:
        label = "Instagram" if platform == "instagram" else "TikTok"
        return f"Zernio did not return any {label} posts for that account and date range."
    return _format_post(posts[0], platform=platform, include_label=include_label)


def _latest_post(username=None, days=180, platform="instagram"):
    selected = _platforms(platform)
    return "\n\n".join(
        _latest_post_for_platform(
            item,
            username=username,
            days=days,
            include_label=len(selected) > 1,
        )
        for item in selected
    )


def _accounts(platform="both"):
    data = _request("/accounts")
    selected = set(_platforms(platform))
    accounts = [
        account for account in data.get("accounts", [])
        if account.get("platform") in selected
    ]
    if not accounts:
        return "No matching social account is connected in Zernio yet."
    lines = ["Connected social accounts in Zernio:"]
    for account in accounts:
        profile = account.get("metadata", {}).get("profileData", {})
        username = profile.get("username") or account.get("username") or "unknown"
        display = account.get("displayName") or profile.get("displayName") or username
        followers = account.get("followersCount", 0)
        synced = account.get("analyticsLastSyncedAt") or "not synced yet"
        label = str(account.get("platform") or "social").title()
        lines.append(f"- {label} @{username}: {display}, {followers} followers, analytics synced {synced}")
    return "\n".join(lines)


def _followers(platform="instagram"):
    data = _request("/accounts")
    selected = set(_platforms(platform))
    accounts = [
        account for account in data.get("accounts", [])
        if account.get("platform") in selected
    ]
    if not accounts:
        return "Instagram follower data is not available yet."

    account = accounts[0]
    username = account.get("username") or "account"
    current = int(account.get("followersCount") or 0)
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    snapshot_path = _base_dir() / "memory" / "social_followers.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshots = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        snapshots = {}

    key = f"{account.get('platform', 'instagram')}:{username}"
    account_snapshots = snapshots.setdefault(key, {})
    month_snapshot = account_snapshots.setdefault(month_key, {"start": current})
    start = int(month_snapshot.get("start") or current)
    month_snapshot["latest"] = current
    month_snapshot["updatedAt"] = datetime.now(timezone.utc).isoformat()
    snapshot_path.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")

    gained = current - start
    if gained > 0:
        trend = f"up {gained:,} this month"
    elif gained < 0:
        trend = f"down {abs(gained):,} this month"
    else:
        trend = "flat so far this month"
    label = "Instagram" if account.get("platform") == "instagram" else "TikTok"
    return f"Current {label} followers for @{username}: {current:,}, {trend}."


def _summary(days=30, platform="instagram"):
    data = _analytics(days, platform=platform)
    overview = data.get("overview") or {}
    posts = data.get("posts") or []
    top = sorted(posts, key=lambda p: _post_metric(p, "reach"), reverse=True)[:3]
    label = "Instagram" if _normalize_platform(platform) == "instagram" else "TikTok"
    lines = [
        f"{label} analytics from Zernio for the last {days} days:",
        f"Total posts: {overview.get('totalPosts', len(posts))}",
        f"Published posts: {overview.get('publishedPosts', 0)}",
        f"Last sync: {overview.get('lastSync', 'unknown')}",
    ]
    if top:
        lines.append("Top posts by reach:")
        for post in top:
            content = (post.get("content") or "").replace("\n", " ").strip()
            if len(content) > 90:
                content = content[:87] + "..."
            lines.append(
                f"- Reach {_post_metric(post, 'reach')}, views {_post_metric(post, 'views')}, "
                f"likes {_post_metric(post, 'likes')}: {content}"
            )
    return "\n".join(lines)


def _ask(question, days=90, platform="instagram", post_count=None):
    import google.generativeai as genai

    data = _analytics(days, platform=platform)
    posts = data.get("posts") or []
    normalized_platform = _normalize_platform(platform)
    if normalized_platform == "instagram":
        posts = _profile_visible_posts(posts, platform_name=normalized_platform)
    limit = max(1, int(post_count or 25))
    compact_posts = []
    for post in sorted(posts, key=lambda p: p.get("publishedAt") or p.get("scheduledFor") or "", reverse=True)[:limit]:
        platform_item = next((p for p in post.get("platforms", []) if p.get("platform") == normalized_platform), {})
        compact_posts.append({
            "publishedAt": post.get("publishedAt"),
            "scheduledFor": post.get("scheduledFor"),
            "url": platform_item.get("platformPostUrl"),
            "content": post.get("content"),
            "analytics": post.get("analytics"),
        })

    label = "Instagram" if normalized_platform == "instagram" else "TikTok"
    prompt = (
        f"Answer the user's {label} analytics question using only this Zernio data. "
        "Be concise, compare posts when useful, and mention if the data does not contain enough information. "
        "When the user asks for the last N posts, answer from the most recent N posts provided.\n\n"
        f"Question: {question}\n\n"
        f"Overview: {json.dumps(data.get('overview', {}), ensure_ascii=False)}\n"
        f"Posts: {json.dumps(compact_posts, ensure_ascii=False)[:18000]}"
    )
    key = _load_gemini_key()
    if not key:
        raise RuntimeError("Gemini API key is not configured.")
    genai.configure(api_key=key)
    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    response = model.generate_content(prompt)
    return response.text.strip()


def zernio_social(parameters=None, player=None):
    params = parameters or {}
    action = str(params.get("action") or "latest_post").strip().lower()
    username = params.get("username")
    days = int(params.get("days") or 90)
    post_count = params.get("post_count") or params.get("count") or params.get("limit")
    platform = _normalize_platform(params.get("platform") or params.get("network") or "instagram")

    try:
        if action in {"accounts", "status", "connected_accounts"}:
            result = _accounts(platform=platform)
        elif action in {"followers", "follower_status", "account_growth"}:
            result = _followers(platform=platform)
        elif action in {"latest_post", "read_latest_post", "latest"}:
            result = _latest_post(username=username, days=max(days, 180), platform=platform)
        elif action in {"recent_posts", "last_posts", "posts", "post_performance"}:
            if platform == "both":
                result = "\n\n".join(
                    _recent_posts(platform=item, username=username, days=max(days, 180), count=post_count or 2)
                    for item in _platforms(platform)
                )
            else:
                result = _recent_posts(platform=platform, username=username, days=max(days, 180), count=post_count or 2)
        elif action in {"summary", "analytics", "performance"}:
            if platform == "both":
                result = "\n\n".join(_summary(days=days, platform=item) for item in _platforms(platform))
            else:
                result = _summary(days=days, platform=platform)
        elif action in {"ask", "question"}:
            question = str(params.get("question") or "").strip()
            if not question:
                return "Please include the social analytics question for Zernio."
            if platform == "both":
                result = "\n\n".join(_ask(question, days=days, platform=item, post_count=post_count) for item in _platforms(platform))
            else:
                result = _ask(question, days=days, platform=platform, post_count=post_count)
        else:
            result = f"Unknown Zernio action: {action}"
    except Exception as e:
        result = f"Zernio failed: {e}"

    if player and hasattr(player, "write_log"):
        player.write_log(f"[Zernio] {result[:160]}")
    return result
