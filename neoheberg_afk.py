#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeoHeberg AFK 广告挂机脚本（单文件可分享版）- GitHub Actions 适配版
"""

import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import requests

# ════════════════════════════════════════════════════════════════════
# 配置（全部来自环境变量）
# ════════════════════════════════════════════════════════════════════
BASE = "https://dash.neoheberg.fr"
ADS_URL = f"{BASE}/shop/ads.php"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neoheberg_state.json")

NH_REMEMBER = os.environ.get("NH_REMEMBER", "")
NH_SESSION  = os.environ.get("NH_SESSION", "")
NH_COOKIE_HEADER = os.environ.get("NH_COOKIE_HEADER", "")
NH_TG_BOT_TOKEN  = os.environ.get("NH_TG_BOT_TOKEN", "")
NH_TG_CHAT_ID   = os.environ.get("NH_TG_CHAT_ID", "")
NH_WAIT       = int(os.environ.get("NH_WAIT", "25"))
NH_MAX_ROUNDS = int(os.environ.get("NH_MAX_ROUNDS", "300"))
NH_UA         = os.environ.get("NH_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SETTLE_SECONDS = 7
RETRY_COOLDOWN = 30

# ════════════════════════════════════════════════════════════════════
# 日志 & TG 通知
# ════════════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("neoheberg-afk")

def send_tg(text: str) -> None:
    if not NH_TG_BOT_TOKEN or not NH_TG_CHAT_ID:
        return
    try:
        data = json.dumps({"chat_id": NH_TG_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{NH_TG_BOT_TOKEN}/sendMessage",
                                      data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log.warning("TG 通知失败: %s", e)

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"start_balance": None, "total": 0.0, "rounds": 0, "last_report": 0}

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ════════════════════════════════════════════════════════════════════
# 核心改造：手动 Cookie 状态管理器
# ════════════════════════════════════════════════════════════════════
ACTIVE_COOKIES = {}

# 初始化读取环境变量
if NH_COOKIE_HEADER:
    for pair in NH_COOKIE_HEADER.split(";"):
        if "=" in pair:
            k, v = pair.strip().split("=", 1)
            ACTIVE_COOKIES[k] = v
else:
    if NH_REMEMBER:
        ACTIVE_COOKIES["__Host-NH-Remember"] = NH_REMEMBER
    if NH_SESSION:
        ACTIVE_COOKIES["__Host-NH"] = NH_SESSION

def _do_req(s: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """拦截请求：手动注入 Cookie 并提取服务器返回的新 Cookie"""
    headers = kwargs.pop("headers", {})
    if ACTIVE_COOKIES:
        headers["Cookie"] = "; ".join([f"{k}={v}" for k, v in ACTIVE_COOKIES.items()])
        
    r = s.request(method, url, headers=headers, **kwargs)
    
    # 动态捕获服务器下发的新凭证（自动续期）
    for c in r.cookies:
        ACTIVE_COOKIES[c.name] = c.value
        
    return r

# ════════════════════════════════════════════════════════════════════
# 业务逻辑 (已将原生的 s.get / s.post 替换为 _do_req)
# ════════════════════════════════════════════════════════════════════
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": NH_UA})
    # 移除了硬编码的 Cookie 头，交由 _do_req 动态管理
    return s

def _get_balance(s: requests.Session) -> float:
    r = _do_req(s, "GET", ADS_URL, timeout=20)
    if "/login" in r.url or "Connexion" in r.text[:600]:
        raise PermissionError("session 过期，需要重新导出 cookies")
    m = re.search(r'font-bold text-lg">([\d.]+) coins', r.text)
    if not m:
        raise RuntimeError("页面中未找到余额")
    return float(m.group(1))

def _get_csrf(s: requests.Session) -> str:
    r = _do_req(s, "GET", ADS_URL, timeout=20)
    if "/login" in r.url or "Connexion" in r.text[:600]:
        raise PermissionError("session 过期，需要重新导出 cookies")
    m = re.search(r'name="csrf_token" value="([a-f0-9]+)"', r.text)
    if not m:
        raise RuntimeError("csrf token 未找到")
    return m.group(1)

def gen_callback(s: requests.Session, csrf: str) -> str | None:
    r = _do_req(s, "POST", ADS_URL, data={"csrf_token": csrf}, allow_redirects=False, timeout=20)
    loc = r.headers.get("Location")
    if not loc:
        return None
    m = re.search(r"url=([^&]+)", loc)
    if not m:
        return None
    return urllib.parse.unquote(m.group(1))

def redeem(s: requests.Session, callback: str) -> int:
    r = _do_req(s, "GET", callback, headers={"Referer": "https://clipurl.fr/"}, timeout=20)
    return r.status_code

def report(state: dict, balance: float, force: bool = False) -> None:
    now = time.time()
    if not force and now - state.get("last_report", 0) < 3600:
        return
    state["last_report"] = now
    save_state(state)
    earned = (balance - state.get("start_balance")) if state.get("start_balance") is not None else 0.0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg = (
        f"🪄 NeoHeberg AFK 已连接\n📅 {ts}\n\n"
        f"💰 <b>余额</b>: {balance:.4f} 🪙\n"
        f"📈 <b>本会话收益</b>: +{earned:.4f} 🪙（{state['rounds']} 轮）"
    )
    send_tg(msg)
    log.info("TG 报告: 余额=%s 收益=%s", balance, earned)

def main() -> None:
    if not ACTIVE_COOKIES:
        log.error("请设置 NH_REMEMBER 环境变量")
        sys.exit(1)

    s = make_session()
    state = load_state()
    try:
        bal = _get_balance(s)
        if state.get("start_balance") is None:
            state["start_balance"] = bal
        log.info("启动，初始余额: %s", bal)
        report(state, bal, force=True)
    except PermissionError as e:
        log.error("启动失败: %s", e)
        send_tg(f"❌ NeoHeberg AFK 启动失败: {e}")
        sys.exit(1)

    consecutive_fail = 0
    while True:
        if NH_MAX_ROUNDS > 0 and state.get("rounds", 0) >= NH_MAX_ROUNDS:
            log.info("🎯 已达到设定的最大轮次 %d，脚本平滑退出", NH_MAX_ROUNDS)
            send_tg(f"✅ NeoHeberg 任务完成，已跑满 {NH_MAX_ROUNDS} 轮，平滑退出。")
            break

        try:
            csrf = _get_csrf(s)
            cb = gen_callback(s, csrf)
            if not cb:
                consecutive_fail += 1
                if consecutive_fail >= 5:
                    raise RuntimeError("连续 5 次生成回调失败")
                time.sleep(20)
                continue
            time.sleep(NH_WAIT)
            st = redeem(s, cb)
            if st != 200:
                log.warning("回调状态异常: %s", st)
            time.sleep(SETTLE_SECONDS)
            state["rounds"] += 1
            consecutive_fail = 0
            log.info("第 %d 轮完成 (回调 %s)", state["rounds"], st)
        except PermissionError as e:
            log.error("session 过期: %s", e)
            send_tg("⚠️ NeoHeberg session 过期，请重新导出 cookies 并更新环境变量后重启")
            time.sleep(3600)
        except Exception as e:
            log.exception("异常: %s", e)
            consecutive_fail += 1
            time.sleep(RETRY_COOLDOWN)

        try:
            bal = _get_balance(s)
            report(state, bal)
            save_state(state)
        except Exception:
            pass

if __name__ == "__main__":
    main()
