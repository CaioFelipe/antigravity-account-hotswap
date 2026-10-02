import os
import re
import json
import time
import datetime
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from .vault import AccountsVault, calculate_next_weekly_reset
from .switcher import AccountSwitcher

PROXY_ACCOUNTS_FILE = Path(os.path.expanduser("~/.config/antigravity-proxy/accounts.json"))

def parse_error_output(text: str) -> Optional[Dict[str, Any]]:
    """
    Analisa saídas de erro do Google Gemini, Antigravity ou agy CLI
    e extrai automaticamente a data, hora ou tempo restante para o reset da cota.
    """
    if not text:
        return None

    now = time.time()
    lower = text.lower()

    # 1. Padrão Google RPC RetryInfo: "retryDelay": "14400s"
    m_retry = re.search(r'["\']?retryDelay["\']?\s*:\s*["\']?(\d+)s?["\']?', text, re.IGNORECASE)
    if m_retry:
        seconds = int(m_retry.group(1))
        return {
            "type": "5h" if seconds <= 18000 else "weekly",
            "cooldown_until": now + seconds,
            "seconds_remaining": seconds,
            "detected_pattern": f"Google RPC retryDelay de {seconds}s"
        }

    # 2. Padrão ISO / UTC timestamp no texto: "resets at 2026-10-03T14:30:00Z"
    m_iso = re.search(r'(?:resets?|retry)\s+(?:at|on|after)\s+([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)', text, re.IGNORECASE)
    if m_iso:
        iso_str = m_iso.group(1)
        try:
            # Normalizar Z para +00:00
            clean_iso = iso_str.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_iso)
            ts = dt.timestamp()
            if ts > now:
                return {
                    "type": "weekly" if (ts - now) > 24 * 3600 else "5h",
                    "cooldown_until": ts,
                    "seconds_remaining": int(ts - now),
                    "detected_pattern": f"Timestamp ISO ({iso_str})"
                }
        except Exception:
            pass

    # 3. Padrão "resets at HH:MM" (apenas horário de hoje ou amanhã)
    m_time = re.search(r'(?:resets?|retry)\s+(?:at|by)\s+([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)', text, re.IGNORECASE)
    if m_time:
        time_str = m_time.group(1)
        parts = time_str.split(":")
        h, m = int(parts[0]), int(parts[1])
        now_dt = datetime.datetime.now()
        target = now_dt.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now_dt:
            target += datetime.timedelta(days=1)
        ts = target.timestamp()
        return {
            "type": "5h",
            "cooldown_until": ts,
            "seconds_remaining": int(ts - now),
            "detected_pattern": f"Horário de reset às {time_str}"
        }

    # 4. Padrões relativos: "try again in X hours Y minutes" ou "retry in Xh Ym"
    m_rel_hm = re.search(r'(?:try again|retry)\s+in\s+(\d+)\s*(?:h|hours?)\s*(?:(\d+)\s*(?:m|minutes?|mins?))?', text, re.IGNORECASE)
    if m_rel_hm:
        h = int(m_rel_hm.group(1))
        m = int(m_rel_hm.group(2)) if m_rel_hm.group(2) else 0
        tot_sec = (h * 3600) + (m * 60)
        return {
            "type": "weekly" if tot_sec > 24 * 3600 else "5h",
            "cooldown_until": now + tot_sec,
            "seconds_remaining": tot_sec,
            "detected_pattern": f"Tempo relativo de {h}h {m}m"
        }

    # 5. Padrão minutos apenas: "try again in 45 minutes" ou "retry in 30 mins"
    m_rel_min = re.search(r'(?:try again|retry)\s+in\s+(\d+)\s*(?:m|minutes?|mins?)', text, re.IGNORECASE)
    if m_rel_min:
        m = int(m_rel_min.group(1))
        tot_sec = m * 60
        return {
            "type": "5h",
            "cooldown_until": now + tot_sec,
            "seconds_remaining": tot_sec,
            "detected_pattern": f"Tempo relativo de {m} minutos"
        }

    # 6. Padrão dia da semana: "weekly limit reached. resets on Monday"
    weekdays_map = {
        "monday": 0, "segunda": 0,
        "tuesday": 1, "terça": 1, "terca": 1,
        "wednesday": 2, "quarta": 2,
        "thursday": 3, "quinta": 3,
        "friday": 4, "sexta": 4,
        "saturday": 5, "sábado": 5, "sabado": 5,
        "sunday": 6, "domingo": 6
    }
    for day_str, day_num in weekdays_map.items():
        if f"resets on {day_str}" in lower or f"renova na {day_str}" in lower or f"renova no {day_str}" in lower:
            target_ts = calculate_next_weekly_reset(day_num, "00:00")
            return {
                "type": "weekly",
                "cooldown_until": target_ts,
                "seconds_remaining": int(target_ts - now),
                "detected_pattern": f"Renovação semanal em {day_str.capitalize()}"
            }

    # 7. Detecção genérica de tipo de cota
    if "weekly" in lower or "semanal" in lower:
        return {
            "type": "weekly",
            "cooldown_until": None, # usará o ciclo configurado da conta
            "detected_pattern": "Limite Semanal genérico identificado"
        }

    if any(k in lower for k in ["resource_exhausted", "quota exceeded", "rate limit", "429 too many"]):
        return {
            "type": "5h",
            "cooldown_until": now + 5 * 3600,
            "seconds_remaining": 18000,
            "detected_pattern": "Erro 429/RESOURCE_EXHAUSTED genérico (janela de 5h)"
        }

    return None


class AutoQuotaDetector:
    """
    Monitor autônomo que checa automaticamente arquivos de estado e telemetria
    do Antigravity para sincronizar cotas sem intervenção humana.
    """
    def __init__(self, vault: Optional[AccountsVault] = None, switcher: Optional[AccountSwitcher] = None):
        self.vault = vault or AccountsVault()
        self.switcher = switcher or AccountSwitcher(self.vault)
        self._running = False
        self._thread = None

    def scan_proxy_rate_limits(self) -> List[Dict[str, Any]]:
        """
        Lê o arquivo accounts.json do proxy de IA e atualiza automaticamente
        quaisquer contas que estejam limitadas pelo Google com seus timestamps exatos.
        """
        if not PROXY_ACCOUNTS_FILE.exists():
            return []

        try:
            with open(PROXY_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []

        events = []
        now = time.time()
        vault_accounts = self.vault.list_accounts()
        active_account = self.vault.get_active_account()

        for acc_proxy in data.get("accounts", []):
            email = acc_proxy.get("email")
            if not email:
                continue

            rate_limits = acc_proxy.get("modelRateLimits", {})
            for model_name, limit_info in rate_limits.items():
                is_limited = limit_info.get("isRateLimited", False)
                reset_time_ms = limit_info.get("resetTime")

                if is_limited and reset_time_ms:
                    reset_time_s = reset_time_ms / 1000.0
                    if reset_time_s > now:
                        # Localizar a conta correspondente no nosso cofre
                        matched = None
                        for v_acc in vault_accounts:
                            if v_acc.get("email", "").lower() == email.lower():
                                matched = v_acc
                                break

                        if matched:
                            # Se a conta ainda não estava em cooldown com esse timestamp
                            current_until = matched.get("cooldown_until") or 0
                            if abs(current_until - reset_time_s) > 10:
                                diff_s = int(reset_time_s - now)
                                min_left = max(1, diff_s // 60)
                                reason = f"Auto-detectado: Limite atingido no modelo {model_name} (restam ~{min_left} min)"

                                # Atualiza status no cofre
                                self.vault.set_account_cooldown(
                                    matched["id"],
                                    quota_type="5h",
                                    custom_until=reset_time_s
                                )

                                event_info = {
                                    "account_id": matched["id"],
                                    "email": email,
                                    "model": model_name,
                                    "reset_time": reset_time_s,
                                    "minutes_remaining": min_left
                                }
                                events.append(event_info)

                                # Se esta for a conta atualmente ativa no Windows, faz o Hot-Swap automático!
                                if active_account and active_account.get("id") == matched["id"]:
                                    swap_res = self.switcher.report_quota_and_swap(
                                        quota_type="5h",
                                        account_id=matched["id"],
                                        custom_until=reset_time_s
                                    )
                                    self.vault.log_history(
                                        "AUTO_HOTSWAP",
                                        f"Auto-Detector identificou esgotamento em '{matched.get('name')}'. Hot-Swap realizado automaticamente!",
                                        details={"new_active": swap_res.get("new_account", {}).get("name")}
                                    )
        return events

    def start_background_scanner(self, interval: int = 5):
        """Inicia uma thread em segundo plano para varredura contínua de limites."""
        if self._running:
            return

        self._running = True
        def _loop():
            while self._running:
                try:
                    self.scan_proxy_rate_limits()
                    self.vault.refresh_cooldowns()
                except Exception:
                    pass
                time.sleep(interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop_background_scanner(self):
        self._running = False
