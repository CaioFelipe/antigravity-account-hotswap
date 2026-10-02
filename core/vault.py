import os
import json
import time
import base64
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from .wincred import read_credential

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VAULT_FILE = DATA_DIR / "accounts_vault.json"

WEEKDAY_NAMES = [
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo"
]

def calculate_next_weekly_reset(weekday: int = 0, hour_str: str = "00:00") -> float:
    """
    Calcula o timestamp Unix da próxima ocorrência do dia da semana e horário.
    weekday: 0=Segunda, 1=Terça, ..., 6=Domingo.
    hour_str: formato 'HH:MM'.
    """
    now = datetime.datetime.now()
    try:
        parts = hour_str.split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        h, m = 0, 0

    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    days_ahead = weekday - now.weekday()

    if days_ahead < 0 or (days_ahead == 0 and now >= target):
        days_ahead += 7

    target += datetime.timedelta(days=days_ahead)
    return target.timestamp()


def parse_custom_until(until_input: Any) -> Optional[float]:
    """Interpreta diversos formatos de data/hora para timestamp Unix."""
    if until_input is None:
        return None
    if isinstance(until_input, (int, float)):
        return float(until_input)
    if isinstance(until_input, str):
        until_input = until_input.strip()
        try:
            return float(until_input)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%H:%M"):
            try:
                dt = datetime.datetime.strptime(until_input, fmt)
                if fmt == "%H:%M":
                    now = datetime.datetime.now()
                    dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
                    if dt <= now:
                        dt += datetime.timedelta(days=1)
                return dt.timestamp()
            except ValueError:
                pass
    return None


def _extract_email_from_blob(blob_str: str) -> Optional[str]:
    """Tenta extrair o e-mail a partir do payload de autenticação OAuth ou JWT."""
    try:
        data = json.loads(blob_str)
        token_info = data.get("token", {})

        id_token = token_info.get("id_token")
        if id_token and "." in id_token:
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1]
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                decoded = base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8")
                jwt_data = json.loads(decoded)
                if "email" in jwt_data:
                    return jwt_data["email"]

        if "email" in data:
            return data["email"]
        if "user" in data and isinstance(data["user"], dict) and "email" in data["user"]:
            return data["user"]["email"]
    except Exception:
        pass
    return None


class AccountsVault:
    def __init__(self, vault_path: Path = VAULT_FILE):
        self.vault_path = Path(vault_path)
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_vault()

    def _ensure_vault(self):
        if not self.vault_path.exists():
            initial_data = {
                "active_account_id": None,
                "accounts": [],
                "history": []
            }
            self._save(initial_data)
        else:
            self.refresh_cooldowns()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.vault_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"active_account_id": None, "accounts": [], "history": []}

    def _save(self, data: Dict[str, Any]):
        with open(self.vault_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _auto_discover_existing_accounts(self):
        """Descobre e importa automaticamente contas já presentes no projeto e no sistema."""
        project_root = self.vault_path.parent.parent.parent
        credentials_dir = project_root / ".agents" / "credentials"
        proxy_accounts_path = Path(os.path.expanduser("~/.config/antigravity-proxy/accounts.json"))

        proxy_emails = []
        if proxy_accounts_path.exists():
            try:
                with open(proxy_accounts_path, "r", encoding="utf-8") as pf:
                    pdata = json.load(pf)
                    proxy_emails = [a.get("email") for a in pdata.get("accounts", []) if a.get("email")]
            except Exception:
                pass

        discovered = []

        c1 = credentials_dir / "conta1.json"
        if c1.exists():
            try:
                with open(c1, "r", encoding="utf-8") as f:
                    d1 = json.load(f)
                    email = _extract_email_from_blob(d1.get("blob", "")) or (proxy_emails[0] if len(proxy_emails) > 0 else "conta1@google.com")
                    discovered.append({
                        "name": "Conta 1 (Principal)",
                        "email": email,
                        "username": d1.get("username", "antigravity"),
                        "blob": d1.get("blob", "")
                    })
            except Exception:
                pass

        c2 = credentials_dir / "conta2.json"
        if c2.exists():
            try:
                with open(c2, "r", encoding="utf-8") as f:
                    d2 = json.load(f)
                    email = _extract_email_from_blob(d2.get("blob", "")) or (proxy_emails[1] if len(proxy_emails) > 1 else "conta2@google.com")
                    discovered.append({
                        "name": "Conta 2 (Secundária)",
                        "email": email,
                        "username": d2.get("username", "antigravity"),
                        "blob": d2.get("blob", "")
                    })
            except Exception:
                pass

        if not discovered:
            current_win = read_credential("gemini:antigravity")
            if current_win:
                email = _extract_email_from_blob(current_win.get("blob", "")) or (proxy_emails[0] if proxy_emails else "conta_ativa@google.com")
                discovered.append({
                    "name": "Conta Atual Antigravity",
                    "email": email,
                    "username": current_win.get("username", "antigravity"),
                    "blob": current_win.get("blob", "")
                })

        for i, item in enumerate(discovered):
            self.add_or_update_account(
                name=item["name"],
                email=item["email"],
                blob=item["blob"],
                username=item.get("username", "antigravity"),
                account_id=f"acc_{i+1}",
                set_active=(i == 0)
            )

    def refresh_cooldowns(self) -> Dict[str, Any]:
        """Verifica se os prazos de cooldown de 5h ou semanal expiraram e reativa as contas."""
        data = self._load()
        now = time.time()
        changed = False

        for acc in data.get("accounts", []):
            cooldown_until = acc.get("cooldown_until")
            if cooldown_until and now >= cooldown_until:
                acc["status"] = "READY"
                acc["cooldown_until"] = None
                acc["cooldown_reason"] = None
                changed = True

        if changed:
            self._save(data)
        return data

    def list_accounts(self) -> List[Dict[str, Any]]:
        data = self.refresh_cooldowns()
        return data.get("accounts", [])

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        data = self.refresh_cooldowns()
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                return acc
        return None

    def get_active_account(self) -> Optional[Dict[str, Any]]:
        data = self.refresh_cooldowns()
        active_id = data.get("active_account_id")
        if not active_id:
            return None
        return self.get_account(active_id)

    def add_or_update_account(
        self,
        name: str,
        email: str,
        blob: str,
        username: str = "antigravity",
        account_id: Optional[str] = None,
        set_active: bool = False,
        weekly_reset_day: int = 0,
        weekly_reset_time: str = "00:00"
    ) -> Dict[str, Any]:
        data = self._load()
        now = time.time()

        if not account_id:
            account_id = f"acc_{int(now)}_{len(data.get('accounts', [])) + 1}"

        account = None
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id or acc.get("email").lower() == email.lower():
                account = acc
                break

        if account:
            account["name"] = name or account.get("name")
            account["email"] = email or account.get("email")
            account["blob"] = blob or account.get("blob")
            account["username"] = username or account.get("username")
            account["weekly_reset_day"] = weekly_reset_day if weekly_reset_day is not None else account.get("weekly_reset_day", 0)
            account["weekly_reset_time"] = weekly_reset_time or account.get("weekly_reset_time", "00:00")
            account["updated_at"] = now
        else:
            account = {
                "id": account_id,
                "name": name or f"Conta {len(data.get('accounts', [])) + 1}",
                "email": email or "sem_email@google.com",
                "username": username or "antigravity",
                "blob": blob,
                "status": "READY",
                "cooldown_until": None,
                "cooldown_reason": None,
                "weekly_reset_day": weekly_reset_day,       # 0 = Segunda-feira
                "weekly_reset_time": weekly_reset_time,     # "00:00"
                "created_at": now,
                "updated_at": now,
                "last_used": None,
                "switch_count": 0
            }
            data.setdefault("accounts", []).append(account)

        if set_active or data.get("active_account_id") is None:
            data["active_account_id"] = account["id"]
            account["status"] = "ACTIVE"

        self._save(data)
        return account

    def update_account_schedule(self, account_id: str, weekly_reset_day: int, weekly_reset_time: str) -> Optional[Dict[str, Any]]:
        """Atualiza a programação de renovação semanal da conta."""
        data = self._load()
        target = None
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                target = acc
                acc["weekly_reset_day"] = int(weekly_reset_day)
                acc["weekly_reset_time"] = str(weekly_reset_time)
                acc["updated_at"] = time.time()
                break

        if target:
            self._save(data)
            return target
        return None

    def remove_account(self, account_id: str) -> bool:
        data = self._load()
        accounts = [a for a in data.get("accounts", []) if a.get("id") != account_id]
        if len(accounts) == len(data.get("accounts", [])):
            return False

        data["accounts"] = accounts
        if data.get("active_account_id") == account_id:
            data["active_account_id"] = accounts[0]["id"] if accounts else None
            if accounts:
                accounts[0]["status"] = "ACTIVE"

        self._save(data)
        return True

    def set_active_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        target = None
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                target = acc
                acc["status"] = "ACTIVE"
                acc["last_used"] = time.time()
                acc["switch_count"] = acc.get("switch_count", 0) + 1
            elif acc.get("status") == "ACTIVE":
                acc["status"] = "READY"

        if target:
            data["active_account_id"] = account_id
            self._save(data)
            return target
        return None

    def set_account_cooldown(
        self,
        account_id: str,
        quota_type: str = "5h",
        custom_until: Optional[Any] = None,
        custom_seconds: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Define status de cota esgotada de forma precisa:
        - custom_until: Timestamp exato ou data/hora string fornecida pelo usuário ou resposta da API.
        - custom_seconds: Duração manual em segundos (ex: minutos ou horas que faltam).
        - quota_type == 'weekly': Calcula exatamente até o próximo dia/hora de reset semanal da conta!
        - quota_type == '5h': Padrão de 5 horas a partir de agora.
        """
        data = self._load()
        now = time.time()

        target = None
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                target = acc
                break

        if not target:
            return None

        # 1. Se foi fornecido um timestamp ou datetime específico
        parsed_until = parse_custom_until(custom_until)
        if parsed_until and parsed_until > now:
            cooldown_until = parsed_until
            reason = f"Cota esgotada (Reset manual programado)"
        # 2. Se foi fornecida duração customizada em segundos
        elif custom_seconds and custom_seconds > 0:
            cooldown_until = now + custom_seconds
            reason = f"Cota esgotada (Pausa de {custom_seconds // 60} minutos)"
        # 3. Se for cota semanal: calcular até o próximo dia e hora configurado para esta conta!
        elif quota_type == "weekly":
            weekday = target.get("weekly_reset_day", 0)
            hour_str = target.get("weekly_reset_time", "00:00")
            cooldown_until = calculate_next_weekly_reset(weekday, hour_str)
            dia_nome = WEEKDAY_NAMES[weekday] if 0 <= weekday < 7 else "Ciclo Semanal"
            reason = f"Cota Semanal esgotada (Renova {dia_nome} às {hour_str})"
        # 4. Padrão 5 horas
        else:
            cooldown_until = now + 5 * 3600
            reason = "Cota esgotada (Janela de 5 Horas)"

        target["status"] = "COOLDOWN_WEEKLY" if quota_type == "weekly" else "COOLDOWN_5H"
        target["cooldown_until"] = cooldown_until
        target["cooldown_reason"] = reason

        self._save(data)
        return target

    def adjust_account_cooldown(self, account_id: str, new_until: Any, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Permite que o usuário edite diretamente a data/hora em que a cota volta."""
        data = self._load()
        now = time.time()

        parsed_until = parse_custom_until(new_until)
        if not parsed_until or parsed_until <= now:
            return None

        target = None
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                target = acc
                target["cooldown_until"] = parsed_until
                if reason:
                    target["cooldown_reason"] = reason
                if target.get("status") not in ["COOLDOWN_5H", "COOLDOWN_WEEKLY"]:
                    target["status"] = "COOLDOWN_5H"
                break

        if target:
            self._save(data)
            return target
        return None

    def clear_account_cooldown(self, account_id: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        target = None
        for acc in data.get("accounts", []):
            if acc.get("id") == account_id:
                target = acc
                target["status"] = "READY"
                target["cooldown_until"] = None
                target["cooldown_reason"] = None
                break

        if target:
            self._save(data)
            return target
        return None

    def get_next_available_account(self, exclude_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Busca a próxima conta saudável (fora de cooldown), ordenando pelas menos recentemente usadas."""
        data = self.refresh_cooldowns()
        candidates = []
        for acc in data.get("accounts", []):
            if exclude_id and acc.get("id") == exclude_id:
                continue
            if acc.get("status") in ["READY", "ACTIVE"] and not acc.get("cooldown_until"):
                candidates.append(acc)

        if not candidates:
            return None

        candidates.sort(key=lambda a: a.get("last_used") or 0)
        return candidates[0]

    def log_history(self, event_type: str, message: str, details: Optional[Dict[str, Any]] = None):
        data = self._load()
        entry = {
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "type": event_type,
            "message": message,
            "details": details or {}
        }
        history = data.setdefault("history", [])
        history.insert(0, entry)
        if len(history) > 100:
            history.pop()
        self._save(data)
