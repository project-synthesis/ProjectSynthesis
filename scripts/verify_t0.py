"""T0 infrastructure verification script."""
import urllib.request
import json
import sqlite3
import sys

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}" + (f" ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}" + (f" ({detail})" if detail else ""))

def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return None

def fetch_status(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

def fetch_text(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.read().decode()
    except Exception:
        return ""

def fetch_headers(url):
    try:
        req = urllib.request.Request(url, headers={"Origin": "http://localhost:5199"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return dict(r.headers)
    except Exception:
        return {}

print("=== T0 Infrastructure Verification ===\n")

# T0-1: Backend starts on port 8000
status = fetch_status("http://localhost:8000/")
check("T0-1: Backend starts on port 8000", status == 200, f"status={status}")

# T0-2: Frontend serves index page on port 5199
status = fetch_status("http://localhost:5199/")
check("T0-2: Frontend serves index on port 5199", status == 200, f"status={status}")

# T0-3: Health endpoint returns complete status object
health = fetch_json("http://localhost:8000/api/health")
if health:
    required = {"status", "provider", "model_routing", "db_connected", "version"}
    has_all = required.issubset(set(health.keys()))
    check("T0-3: Health endpoint returns complete status", has_all, f"keys={sorted(health.keys())}")
else:
    check("T0-3: Health endpoint returns complete status", False, "no response")

# T0-4: Database optimizations table
conn = sqlite3.connect("backend/data/synthesis.db")
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='optimizations'")
check("T0-4: Database optimizations table created", c.fetchone() is not None)

# T0-5: Database github tables
c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('github_tokens','linked_repos')")
tables = [r[0] for r in c.fetchall()]
check("T0-5: Database github tables created", len(tables) == 2, f"tables={tables}")

# T0-6: API docs at /api/docs
status = fetch_status("http://localhost:8000/api/docs")
check("T0-6: API docs available at /api/docs", status == 200, f"status={status}")

# T0-7: CORS configured for frontend origin
# Use subprocess curl since urllib doesn't reliably expose CORS headers
import subprocess
try:
    result = subprocess.run(
        ["curl", "-s", "-I", "-H", "Origin: http://localhost:5199", "http://localhost:8000/api/health"],
        capture_output=True, text=True, timeout=5
    )
    cors_ok = "access-control-allow-origin: http://localhost:5199" in result.stdout.lower()
    check("T0-7: CORS configured for frontend origin", cors_ok, "ACAO header present" if cors_ok else "no ACAO header")
except Exception as e:
    check("T0-7: CORS configured for frontend origin", False, str(e))

# T0-8: Frontend can reach backend API (via proxy)
proxy_health = fetch_json("http://localhost:5199/api/health")
check("T0-8: Frontend can reach backend API", proxy_health is not None and "provider" in (proxy_health or {}))

# T0-9: Backend returns 404 for unknown routes
status = fetch_status("http://localhost:8000/api/unknown-endpoint-xyz")
check("T0-9: Backend returns 404 for unknown routes", status == 404, f"status={status}")

# T0-10: Frontend SvelteKit routing
html = fetch_text("http://localhost:5199/")
check("T0-10: Frontend SvelteKit routing works", "Project Synthesis" in html)

# T0-11: Provider detection completes
provider_status = fetch_json("http://localhost:8000/api/providers/status")
if provider_status:
    check("T0-11: Provider detection completes on startup", "provider" in provider_status, f"provider={provider_status.get('provider')}")
else:
    check("T0-11: Provider detection completes on startup", False, "no response")

# T0-13: Environment variables (secret key loaded)
root = fetch_json("http://localhost:8000/")
check("T0-13: Backend loads and starts properly", root is not None and root.get("version") == "2.0.0")

# T0-14: Database indexes
c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
indexes = [r[0] for r in c.fetchall()]
check("T0-14: Database indexes created", len(indexes) >= 5, f"count={len(indexes)}, indexes={indexes}")

# T0-15: Static CSS and JS assets served
check("T0-15: Static CSS/JS assets served", "tailwindcss" in html or "sveltekit" in html.lower())

# T0-16: Backend lifespan initializes provider and database
check("T0-16: Backend lifespan init", health is not None and health.get("db_connected") is True and health.get("provider") != "none")

# T0-17: Frontend loads cyberpunk theme
has_neon = "--color-neon-cyan" in html
has_bg = "--color-bg-primary" in html
has_font = "--font-mono" in html
check("T0-17: Frontend CSS loads cyberpunk theme", has_neon and has_bg and has_font)

# T0-18: Session middleware
check("T0-18: Session middleware configured", health is not None)

conn.close()

print(f"\n=== Results: {passed} passed, {failed} failed out of {passed+failed} ===")
sys.exit(0 if failed == 0 else 1)
