# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Professional Web Dashboard & Real-Time EXP Tracker
Embedded Async Web Server (aiohttp)
"""

import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from aiohttp import web

# Global bot state shared between Main.py and Web Dashboard
class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}
        # Tracks deleted UIDs so re-registration and log spam are blocked
        self.deleted_uids: set = set()
        # Maps game account_id -> worker key for reliable task cancellation
        self.game_id_to_worker_key: Dict[str, str] = {}

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": uid
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int, likes: int = 0, worker_key: Optional[str] = None):
        uid_str = str(uid)
        # Block re-registration for deleted accounts
        if uid_str in self.deleted_uids:
            return
        if worker_key:
            self.game_id_to_worker_key[uid_str] = worker_key
        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": level or 1,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "status": "ONLINE",
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            if level:
                acc["level"] = level
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            acc["status"] = "ONLINE"
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.deleted_uids:
            return
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            acc["current_exp"] = current_exp
            if level is not None and level > 0:
                acc["level"] = level
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            if diff > 0:
                self.log(f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! Total Gained: +{acc['gained_exp']}", "success", uid_str)
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.deleted_uids:
            return
        if uid_str in self.accounts:
            self.accounts[uid_str]["status"] = status
            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = str(uid)
        if uid_str in self.deleted_uids:
            return
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(f"Account {self.accounts[uid_str]['nickname']} finished Match #{self.accounts[uid_str]['matches_played']}", "info", uid_str)

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()


# ==================== HTTP HANDLERS ====================

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

async def handle_index(request: web.Request) -> web.Response:
    if os.path.exists(TEMPLATE_PATH):
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "<h1>templates/index.html not found!</h1>"
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_get_stats(request: web.Request) -> web.Response:
    accounts_data = list(bot_state.accounts.values())
    accounts_data.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    return web.json_response({
        "total_accounts": len(bot_state.accounts),
        "total_matches": bot_state.total_matches,
        "total_gained_exp": bot_state.total_gained_exp,
        "accounts": accounts_data,
        "logs": bot_state.logs[-60:],
        "uptime": int(time.time() - bot_state.start_time)
    })


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        accounts_file = "accounts.json"
        existing = []
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        if "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return web.json_response({"status": "error", "error": "UID and Password are required"})
            # BUG FIX: Deduplicate in file before appending
            existing = [acc for acc in existing if str(acc.get("uid")) != uid]
            existing.append({"uid": uid, "password": pwd})
        elif "token" in data:
            token = str(data["token"]).strip()
            if not token:
                return web.json_response({"status": "error", "error": "Token is required"})
            # BUG FIX: Deduplicate in file before appending
            existing = [acc for acc in existing if acc.get("token") != token]
            existing.append({"token": token})
        else:
            return web.json_response({"status": "error", "error": "Invalid payload"})

        with open(accounts_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)

        # Determine if this account already has a running worker (re-add = restart)
        _wkey = str(data.get("uid", "")).strip() if data.get("uid") else f"tok_{str(data.get('token',''))[:20]}"
        is_restart = _wkey in bot_state.account_workers
        label = data.get("uid") or "Token"
        if is_restart:
            bot_state.log(f"Account {label} restarted (old worker replaced).", "warning")
        else:
            bot_state.log(f"New account added: {label}", "success")

        # Trigger dynamic worker launch (on_account_added_handler handles old-task cancellation)
        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))

        return web.json_response({"status": "ok", "restarted": is_restart})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()

        # Mark as deleted FIRST — blocks all re-registration & state mutations immediately
        bot_state.deleted_uids.add(uid)

        # Capture credentials and worker key BEFORE removing them
        creds = bot_state.account_credentials.get(uid, {})
        auth_uid   = str(creds.get("auth_uid",   "")).strip()
        auth_token = str(creds.get("auth_token", "")).strip()
        mapped_worker_key = bot_state.game_id_to_worker_key.get(uid)  # get BEFORE pop

        # Remove from accounts.json — handles BOTH guest (uid field) and token (token field) entries
        accounts_file = "accounts.json"
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)

                def _should_keep(acc):
                    if str(acc.get("uid", "")) == uid:
                        return False
                    if auth_uid and str(acc.get("uid", "")) == auth_uid:
                        return False
                    if auth_token and acc.get("token", "") == auth_token:
                        return False
                    return True

                existing = [acc for acc in existing if _should_keep(acc)]
                with open(accounts_file, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2)
            except Exception:
                pass

        # Remove from live accounts dict
        bot_state.accounts.pop(uid, None)

        # Clean ALL credential entries for this account (stored under multiple keys)
        bot_state.account_credentials.pop(uid, None)
        if auth_uid:
            bot_state.account_credentials.pop(auth_uid, None)
        if auth_token:
            bot_state.account_credentials.pop(f"tok_{auth_token[:20]}", None)

        # Clean mapping
        bot_state.game_id_to_worker_key.pop(uid, None)

        # Cancel worker — try every possible key this account could be stored under
        _tok_key = f"tok_{auth_token[:20]}" if auth_token else None
        _candidate_keys = [k for k in [mapped_worker_key, uid, auth_uid, _tok_key] if k]
        _seen = set()
        for key in _candidate_keys:
            if key in _seen:
                continue
            _seen.add(key)
            if key in bot_state.account_workers:
                try:
                    bot_state.account_workers[key].cancel()
                except Exception:
                    pass
                del bot_state.account_workers[key]

        bot_state.recalc_totals()
        bot_state.log(f"Account {uid} deleted and stopped.", "warning", uid)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def handle_export_account(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        uid = str(data.get("uid")).strip()
        
        # 1. Fetch live profile stats
        acc_stats = bot_state.accounts.get(uid)
        
        # 2. Try to fetch credentials using the game UID (for token-based) or auth_uid (for guest)
        creds = bot_state.account_credentials.get(uid)
        if not creds:
            # If not found directly by game UID, search all credentials to find the matching account_id
            for c_uid, c_data in bot_state.account_credentials.items():
                if str(c_data.get("account_id")) == uid:
                    creds = c_data
                    break
        
        # Sanitize credentials to avoid "bytes is not JSON serializable" (e.g. login_payload_data)
        safe_creds = {}
        if creds:
            safe_creds = {
                "auth_type": creds.get("auth_type"),
                "auth_uid": creds.get("auth_uid"),
                "auth_password": creds.get("auth_password"),
                "auth_token": creds.get("auth_token")
            }
                    
        return web.json_response({
            "status": "ok",
            "account": acc_stats,
            "credentials": safe_creds
        })
    except Exception as e:
        return web.json_response({"status": "error", "error": str(e)})


async def start_web_dashboard(host: str = "0.0.0.0", port: int = 5000):
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/refresh", handle_refresh_account)
    app.router.add_post("/api/account/export", handle_export_account)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"\033[92m[+] Web Dashboard running on http://localhost:{port}\033[0m")
