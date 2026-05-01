import json
import os
import threading
import urllib.request
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify, render_template, request

from tech_check import DEFAULT_EDITORIAL, DEFAULT_MODEL, run_newsletter

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LOG_FILE = os.path.join(BASE_DIR, "tech_check.log")

DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

DEFAULT_CONFIG = {
    "model": DEFAULT_MODEL,
    "prompt": DEFAULT_EDITORIAL,
    "day_of_week": "sun",
    "hour": 21,
    "minute": 0,
    "recipient": "sujathom@gmail.com",
}

scheduler = BackgroundScheduler(timezone="America/New_York")
_run_lock = threading.Lock()


# ── Config helpers ──────────────────────────────────────────────────────────

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Scheduler ───────────────────────────────────────────────────────────────

def _scheduled_run():
    cfg = load_config()
    print(f"[{datetime.now().isoformat()}] Scheduled run starting...")
    try:
        run_newsletter(
            model=cfg.get("model"),
            editorial=cfg.get("prompt"),
            recipient=cfg.get("recipient"),
        )
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Scheduled run failed: {e}")


def apply_schedule(cfg: dict) -> None:
    day = DAY_MAP.get(cfg.get("day_of_week", "sun"), 6)
    hour = int(cfg.get("hour", 21))
    minute = int(cfg.get("minute", 0))

    if scheduler.get_job("newsletter"):
        scheduler.remove_job("newsletter")

    scheduler.add_job(
        _scheduled_run,
        CronTrigger(day_of_week=day, hour=hour, minute=minute),
        id="newsletter",
        replace_existing=True,
    )
    print(f"[{datetime.now().isoformat()}] Scheduled: day_of_week={day} {hour:02d}:{minute:02d} ET")


# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(load_config())
    cfg = {**DEFAULT_CONFIG, **request.get_json(force=True)}
    save_config(cfg)
    apply_schedule(cfg)
    return jsonify({"status": "saved"})


@app.route("/api/models")
def api_models():
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    try:
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        models = [
            {"id": m["id"], "name": m.get("name", m["id"])}
            for m in data.get("data", [])
            if m["id"].endswith(":free")
            or (
                str(m.get("pricing", {}).get("prompt", "1")) == "0"
                and str(m.get("pricing", {}).get("completion", "1")) == "0"
            )
        ]
        models.sort(key=lambda m: m["name"].lower())
        return jsonify(models)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/run", methods=["POST"])
def api_run():
    if not _run_lock.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 409

    cfg = load_config()

    def _run():
        try:
            run_newsletter(
                model=cfg.get("model"),
                editorial=cfg.get("prompt"),
                recipient=cfg.get("recipient"),
            )
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Manual run failed: {e}")
        finally:
            _run_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/logs")
def api_logs():
    if not os.path.exists(LOG_FILE):
        return jsonify({"lines": []})
    with open(LOG_FILE) as f:
        lines = f.readlines()
    return jsonify({"lines": [l.rstrip() for l in lines[-50:]]})


# ── Startup ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = load_config()
    scheduler.start()
    apply_schedule(cfg)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
