"""
============================================================
   GHOST SYNC v2.0 — Session Downloader
   Downloads .session files from GitHub repo
   Optional module — bot works without this
============================================================
"""
import os, sys, time, threading, base64, json
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

# ══════════════════════ CONFIG ══════════════════════
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO_SESSIONS = os.getenv("GITHUB_REPO_SESSIONS",
                                 "jedop62502-hue/Sessions")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")

SYNC_INTERVAL = int(os.getenv("GITHUB_SYNC_INTERVAL", "1800"))  # 30 min

# ══════════════════════ STATE ══════════════════════
_STATS = {
    "downloads": 0,
    "session_downloads": 0,
    "errors": 0,
    "last_sync": None,
    "last_error": None,
}
_LOCK = threading.Lock()
_THREAD_STARTED = False


def _log(msg, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    ic = {"info": "🔍", "ok": "✅", "fail": "❌", "warn": "⚠️", "sync": "🔄"}
    print(f"[{ts}] {ic.get(level,'•')} [SYNC] {msg}", flush=True)


# ══════════════════════ PUBLIC HELPERS ══════════════════════
def is_enabled():
    if requests is None:
        return False
    if not GITHUB_TOKEN or not GITHUB_REPO_SESSIONS:
        return False
    if "/" not in GITHUB_REPO_SESSIONS:
        return False
    return True


def get_stats():
    with _LOCK:
        return dict(_STATS)


def _record_error(msg):
    with _LOCK:
        _STATS["errors"] += 1
        _STATS["last_error"] = msg[:200]


def mark_dirty():
    """Not needed for this bot — compatibility"""
    pass


# ══════════════════════ API HELPERS ══════════════════════
def _headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "GhostSync/2.0",
    }


def _list_url():
    return f"https://api.github.com/repos/{GITHUB_REPO_SESSIONS}/contents/"


def _file_url(fname):
    return (f"https://api.github.com/repos/{GITHUB_REPO_SESSIONS}/contents/"
            f"{fname}")


# ══════════════════════ DOWNLOAD SESSIONS ══════════════════════
def download_sessions():
    """
    Download all .session files from GitHub repo.
    Files stored DIRECTLY in repo (no JSON wrapper).
    Returns (ok, msg)
    """
    if not is_enabled():
        return False, "Sync disabled"

    try:
        # List files
        r = requests.get(
            _list_url(),
            headers=_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=30,
        )
        if r.status_code == 404:
            _log("Sessions repo not found", "warn")
            return False, "Repo not found"
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"

        data = r.json()
        if not isinstance(data, list):
            return False, "Invalid response"

        # Filter .session files
        session_files = [
            item for item in data
            if item.get("type") == "file"
            and item.get("name", "").endswith(".session")
        ]

        if not session_files:
            _log("No .session files found on GitHub", "info")
            return False, "No .session files"

        _log(f"Found {len(session_files)} .session files", "sync")

        # Ensure local dir
        if not os.path.isdir(SESSIONS_DIR):
            os.makedirs(SESSIONS_DIR, exist_ok=True)

        downloaded = 0
        skipped = 0
        failed = 0

        for i, item in enumerate(session_files, 1):
            fname = item.get("name", "")
            if not fname:
                continue

            local_path = os.path.join(SESSIONS_DIR, fname)

            # Skip if already exists
            if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                skipped += 1
                continue

            try:
                # Get file content
                file_url = item.get("url") or _file_url(fname)
                fr = requests.get(
                    file_url,
                    headers=_headers(),
                    params={"ref": GITHUB_BRANCH},
                    timeout=30,
                )
                if fr.status_code != 200:
                    failed += 1
                    continue

                fd = fr.json()
                content_b64 = fd.get("content", "")
                if not content_b64:
                    failed += 1
                    continue

                clean = content_b64.replace("\n", "").replace("\r", "")
                content = base64.b64decode(clean)

                with open(local_path, "wb") as f:
                    f.write(content)
                downloaded += 1

                if i % 5 == 0:
                    _log(f"Downloaded {i}/{len(session_files)}", "sync")
            except Exception as e:
                failed += 1
                _log(f"  {fname}: fail — {str(e)[:60]}", "warn")
                continue

        with _LOCK:
            _STATS["session_downloads"] += downloaded
            _STATS["last_sync"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S")

        _log(f"Sessions: ✅{downloaded} ⏭️{skipped} ❌{failed}", "ok")
        return True, f"Downloaded {downloaded} (skipped {skipped})"
    except Exception as e:
        _record_error(f"sessions download: {str(e)[:100]}")
        _log(f"Sessions download error: {str(e)[:100]}", "fail")
        return False, str(e)[:150]


# ══════════════════════ BACKGROUND SYNC LOOP ══════════════════════
def _sync_loop():
    _log(f"Background sync thread started (every {SYNC_INTERVAL}s)", "sync")
    while True:
        try:
            time.sleep(SYNC_INTERVAL)
            download_sessions()
        except Exception as e:
            _record_error(str(e)[:100])
            time.sleep(300)


def start_sync_thread():
    """Start background sync thread (downloads sessions periodically)"""
    global _THREAD_STARTED
    if _THREAD_STARTED:
        return
    if not is_enabled():
        _log("Disabled (no token/repo)", "warn")
        return
    try:
        # Initial download
        download_sessions()
        # Start thread
        t = threading.Thread(target=_sync_loop, daemon=True,
                             name="gh_sync")
        t.start()
        _THREAD_STARTED = True
        _log(f"Auto-sync active (every {SYNC_INTERVAL}s)", "ok")
    except Exception as e:
        _log(f"Failed to start: {e}", "fail")


# ══════════════════════ SELF TEST ══════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  GHOST SYNC — SELF TEST")
    print("=" * 60)
    print(f"  Token:     {'✅ SET' if GITHUB_TOKEN else '❌ MISSING'}")
    print(f"  Repo:      {GITHUB_REPO_SESSIONS or '—'}")
    print(f"  Branch:    {GITHUB_BRANCH}")
    print(f"  Sessions:  {SESSIONS_DIR}")
    print(f"  Enabled:   {'✅' if is_enabled() else '❌'}")
    print("=" * 60)
    if not is_enabled():
        print("\n⚠️  Set env vars first:")
        print("   export GITHUB_TOKEN='ghp_xxxxx'")
        print("   export GITHUB_REPO_SESSIONS='user/repo-sessions'")
        sys.exit(1)

    print("\n⬇️  Testing sessions download...")
    ok, msg = download_sessions()
    print(f"   {'✅' if ok else '❌'} {msg}")

    print("\n📊 Stats:")
    for k, v in get_stats().items():
        print(f"   {k}: {v}")
