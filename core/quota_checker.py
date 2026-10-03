import os
import re
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import psutil

PROXY_ACCOUNTS_FILE = Path(os.path.expanduser("~/.config/antigravity-proxy/accounts.json"))

_cached_ls_info: Optional[Dict[str, Any]] = None
_last_ls_check: float = 0.0

def invalidate_ls_cache():
    """Limpa o cache de informações do Language Server."""
    global _cached_ls_info, _last_ls_check
    _cached_ls_info = None
    _last_ls_check = 0.0

def _get_active_ls_info(force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """
    Localiza dinamicamente o language_server.exe em execução,
    extrai o PID, a porta HTTP e o csrf_token com cache de 3 segundos.
    """
    global _cached_ls_info, _last_ls_check
    now = time.time()
    if not force_refresh and _cached_ls_info is not None and (now - _last_ls_check) < 3.0:
        return _cached_ls_info

    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            name = proc.info.get('name') or ''
            if 'language_server' in name.lower():
                pid = proc.info.get('pid')
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                m_csrf = re.search(r'--csrf_token\s+([a-f0-9\-]+)', cmdline)
                csrf = m_csrf.group(1) if m_csrf else ''
                
                try:
                    conns = proc.net_connections(kind='tcp')
                    ports = [c.laddr.port for c in conns if c.status == 'LISTEN']
                except Exception:
                    ports = []
                
                for port in sorted(ports, reverse=True):
                    url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetUserStatus"
                    req = urllib.request.Request(
                        url,
                        data=b"{}",
                        headers={
                            "Content-Type": "application/json",
                            "x-codeium-csrf-token": csrf
                        }
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=1.5) as resp:
                            data = json.loads(resp.read().decode())
                            us = data.get("userStatus", {})
                            _cached_ls_info = {
                                "pid": pid,
                                "email": us.get("email", "").lower(),
                                "name": us.get("name", ""),
                                "port": port,
                                "csrf": csrf,
                                "raw_status": us
                            }
                            _last_ls_check = now
                            return _cached_ls_info
                    except Exception:
                        continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _last_ls_check = now
    return None

def _parse_iso_to_seconds_left(iso_str: str) -> int:
    """Calcula quantos segundos faltam a partir de um timestamp ISO (ex: '2026-10-02T20:54:39Z')"""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = (dt - now).total_seconds()
        return max(0, int(diff))
    except Exception:
        return 0

def _format_time_left(seconds: int) -> str:
    if seconds <= 0:
        return "Pronto"
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}h {rem_min:02d}m"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h"

def get_account_live_quota(account: Dict[str, Any], is_active_in_ide: bool = False, ls_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Retorna o status em tempo real de uma conta Google:
    - Se a conta for a ativa no Antigravity, lê diretamente as cotas da language_server API.
    - Se a conta estiver inativa, verifica telemetria local e cooldowns registrados.
    """
    now = time.time()
    email = account.get("email", "").lower()

    # 1. Se o email da conta coincidir com a conta logada no language_server do Antigravity
    ls_email = (ls_cache.get("email") or "").lower() if ls_cache else ""
    if ls_email and ls_email == email:
        raw_us = ls_cache.get("raw_status", {})
        configs = raw_us.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
        
        # Filtra os modelos principais
        model_quotas = []
        min_fraction = 1.0
        max_reset_seconds = 0
        has_pro = False

        for m in configs:
            label = m.get("label", "")
            q = m.get("quotaInfo")
            if not q:
                continue
            
            rem_fraction = float(q.get("remainingFraction", 1.0))
            reset_time_iso = q.get("resetTime", "")
            sec_left = _parse_iso_to_seconds_left(reset_time_iso)

            # Dá destaque a Gemini 3.1 Pro e Claude Sonnet
            if "Gemini 3.1 Pro" in label or "Claude Sonnet" in label or "Gemini 3.8 Flash" in label:
                model_quotas.append({
                    "model": label,
                    "remaining_percent": int(rem_fraction * 100),
                    "used_percent": int((1.0 - rem_fraction) * 100),
                    "reset_in": _format_time_left(sec_left),
                    "reset_iso": reset_time_iso,
                    "seconds_left": sec_left
                })

            if "Gemini 3.1 Pro" in label:
                has_pro = True
                min_fraction = min(min_fraction, rem_fraction)
                max_reset_seconds = max(max_reset_seconds, sec_left)
            elif not has_pro:
                min_fraction = min(min_fraction, rem_fraction)
                max_reset_seconds = max(max_reset_seconds, sec_left)

        rem_pct = int(min_fraction * 100)
        used_pct = 100 - rem_pct

        is_5h_blocked = (rem_pct == 0 and max_reset_seconds > 0 and max_reset_seconds <= 18000)
        is_weekly_blocked = (rem_pct == 0 and max_reset_seconds > 18000)

        # Monta resposta 5h
        if max_reset_seconds > 0 and used_pct > 0:
            badge_5h = f"{rem_pct}% disponível • Reseta em {_format_time_left(max_reset_seconds)}"
        else:
            badge_5h = "100% Livre"

        quota_5h = {
            "available": not is_5h_blocked,
            "percent_remaining": rem_pct,
            "percent_used": used_pct,
            "badge": badge_5h,
            "seconds_remaining": max_reset_seconds,
            "time_remaining_str": _format_time_left(max_reset_seconds) if max_reset_seconds > 0 else ""
        }

        # Monta resposta semanal
        quota_weekly = {
            "available": not is_weekly_blocked,
            "percent_remaining": 0 if is_weekly_blocked else 100,
            "badge": f"Esgotada • Reseta em {_format_time_left(max_reset_seconds)}" if is_weekly_blocked else "100% Disponível",
            "seconds_remaining": max_reset_seconds if is_weekly_blocked else 0
        }

        return {
            "is_live_from_ide": True,
            "quota_5h": quota_5h,
            "quota_weekly": quota_weekly,
            "models": model_quotas,
            "is_healthy": (not is_5h_blocked and not is_weekly_blocked)
        }

    # 2. Se for conta inativa, checa arquivos de rate limits do proxy e vault
    cooldown_until = account.get("cooldown_until")
    cooldown_seconds = max(0, int(cooldown_until - now)) if cooldown_until and cooldown_until > now else 0
    status = account.get("status", "READY")

    # Verifica proxy accounts.json
    proxy_limited = False
    proxy_reset_s = 0
    if PROXY_ACCOUNTS_FILE.exists():
        try:
            with open(PROXY_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            for pacc in pdata.get("accounts", []):
                if pacc.get("email", "").lower() == email:
                    rls = pacc.get("modelRateLimits", {})
                    for mname, minfo in rls.items():
                        if minfo.get("isRateLimited", False):
                            rt_ms = minfo.get("resetTime", 0)
                            if rt_ms and (rt_ms / 1000.0) > now:
                                proxy_limited = True
                                proxy_reset_s = max(proxy_reset_s, int((rt_ms / 1000.0) - now))
        except Exception:
            pass

    # Combina cooldown do vault com proxy
    effective_seconds = max(cooldown_seconds, proxy_reset_s)
    is_5h = (effective_seconds > 0 and (status == "COOLDOWN_5H" or (effective_seconds <= 18000 and proxy_limited)))
    is_weekly = (effective_seconds > 0 and (status == "COOLDOWN_WEEKLY" or (effective_seconds > 18000 and proxy_limited)))

    if is_5h:
        quota_5h = {
            "available": False,
            "percent_remaining": 0,
            "percent_used": 100,
            "badge": f"Pausa temporária ({_format_time_left(effective_seconds)})",
            "seconds_remaining": effective_seconds,
            "time_remaining_str": _format_time_left(effective_seconds)
        }
    else:
        quota_5h = {
            "available": True,
            "percent_remaining": 100,
            "percent_used": 0,
            "badge": "100% Livre",
            "seconds_remaining": 0,
            "time_remaining_str": ""
        }

    if is_weekly:
        quota_weekly = {
            "available": False,
            "percent_remaining": 0,
            "badge": f"Esgotada • Reseta em {_format_time_left(effective_seconds)}",
            "seconds_remaining": effective_seconds
        }
    else:
        quota_weekly = {
            "available": True,
            "percent_remaining": 100,
            "badge": "100% Disponível",
            "seconds_remaining": 0
        }

    return {
        "is_live_from_ide": False,
        "quota_5h": quota_5h,
        "quota_weekly": quota_weekly,
        "models": [],
        "is_healthy": (not is_5h and not is_weekly)
    }

def get_all_accounts_quota_map(accounts: List[Dict[str, Any]], active_account_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Retorna o mapa de cotas calculado para todas as contas da lista,
    consultando o language_server apenas uma vez para otimização.
    """
    ls_cache = _get_active_ls_info()
    result = {}
    for acc in accounts:
        is_active = (acc.get("id") == active_account_id)
        result[acc.get("id")] = get_account_live_quota(acc, is_active_in_ide=is_active, ls_cache=ls_cache)
    return result
