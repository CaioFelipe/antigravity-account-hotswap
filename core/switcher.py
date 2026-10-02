import time
import json
import base64
import datetime
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional

from .wincred import read_credential, write_credential
from .vault import AccountsVault
from .quota_checker import get_all_accounts_quota_map
from .ide_reloader import apply_hotswap_reload

TARGET_CREDENTIAL = "gemini:antigravity"

from .oauth_config import get_google_oauth_client

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

def refresh_google_oauth_tokens(refresh_token: str) -> Optional[Dict[str, Any]]:
    """
    Renova os tokens OAuth da Google usando o refresh_token.
    Retorna dicionário com access_token, id_token, expires_in, token_type.
    """
    if not refresh_token:
        return None
    try:
        client_id, client_secret = get_google_oauth_client()
        if not client_id or not client_secret:
            return None
        params = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }).encode("utf-8")
        req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=params,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

class AccountSwitcher:
    def __init__(self, vault: Optional[AccountsVault] = None):
        self.vault = vault or AccountsVault()

    def get_status(self) -> Dict[str, Any]:
        """Retorna o estado geral atual do sistema de Hot-Swap com cotas em tempo real."""
        self.vault.refresh_cooldowns()
        accounts = self.vault.list_accounts()
        active = self.vault.get_active_account()
        current_win = read_credential(TARGET_CREDENTIAL)

        # Checar se a credencial no Windows bate com a conta ativa no cofre
        wincred_synced = False
        if current_win and active:
            from .vault import _extract_email_from_blob
            extracted_email = _extract_email_from_blob(current_win.get("blob", ""))
            wincred_synced = (bool(extracted_email) and extracted_email.lower() == active.get("email", "").lower())

        # Calcula cotas em tempo real para todas as contas
        active_id = active.get("id") if active else None
        quotas_map = get_all_accounts_quota_map(accounts, active_id)

        return {
            "active_account": active,
            "wincred_synced": wincred_synced,
            "total_accounts": len(accounts),
            "accounts": accounts,
            "quotas": quotas_map,
            "now": time.time()
        }

    def switch_to_account(self, account_id: str, auto_reload: bool = True) -> Dict[str, Any]:
        """
        Aplica a credencial da conta especificada no Windows Credential Manager:
        1. Renova o access_token e id_token junto à Google para garantir autenticação ativa e válida.
        2. Monta o payload JSON completo exigido pelo Antigravity (com id_token e token expirável).
        3. Grava no cofre do Windows (gemini:antigravity).
        4. Opcionalmente reinicia o Language Server em background para o Antigravity assumir a nova conta na hora.
        """
        account = self.vault.get_account(account_id)
        if not account:
            return {"success": False, "message": f"Conta '{account_id}' não encontrada."}

        blob_str = account.get("blob", "")
        username = account.get("username", "antigravity")

        # 1. Extrai refresh_token da conta
        refresh_token = None
        try:
            blob_dict = json.loads(blob_str)
            tok_obj = blob_dict.get("token", {})
            refresh_token = tok_obj.get("refresh_token") or blob_dict.get("refresh_token")
        except Exception:
            blob_dict = {}

        # 2. Renova tokens junto ao Google para obter id_token e access_token frescos
        final_blob_str = blob_str
        if refresh_token:
            tokens = refresh_google_oauth_tokens(refresh_token)
            if tokens and "access_token" in tokens:
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                expires_in = tokens.get("expires_in", 3600)
                expiry_dt = now_dt + datetime.timedelta(seconds=expires_in)
                expiry_str = expiry_dt.astimezone().isoformat()

                # id_token é vital para o Antigravity reconhecer a identidade do usuário
                id_token = tokens.get("id_token") or blob_dict.get("id_token", "")

                new_blob_dict = {
                    "token": {
                        "access_token": tokens.get("access_token"),
                        "token_type": tokens.get("token_type", "Bearer"),
                        "refresh_token": tokens.get("refresh_token", refresh_token),
                        "expiry": expiry_str
                    },
                    "auth_method": "consumer",
                    "id_token": id_token
                }
                final_blob_str = json.dumps(new_blob_dict)

                # Mantém o vault atualizado com o token recente
                account["blob"] = final_blob_str
                self.vault.add_or_update_account(
                    name=account.get("name"),
                    email=account.get("email"),
                    blob=final_blob_str,
                    username=username,
                    account_id=account_id
                )

        # 3. Grava no cofre do Windows
        success = write_credential(
            target=TARGET_CREDENTIAL,
            username=username,
            blob_str=final_blob_str
        )

        if not success:
            return {
                "success": False,
                "message": "Falha ao gravar credencial no Windows Credential Manager."
            }

        # 4. Registra no histórico do vault
        self.vault.set_active_account(account_id)
        self.vault.log_history(
            event_type="HOTSWAP_MANUAL",
            message=f"Hot-Swap realizado para '{account.get('name')}' ({account.get('email')})",
            details={"account_id": account_id, "email": account.get("email")}
        )

        # 5. Aplica reload no Antigravity automaticamente se solicitado
        reload_msg = ""
        if auto_reload:
            try:
                reload_res = apply_hotswap_reload()
                if reload_res.get("success"):
                    reload_msg = " " + reload_res.get("message", "")
            except Exception:
                pass

        return {
            "success": True,
            "message": f"Conta '{account.get('name')}' ativada com sucesso!{reload_msg}",
            "account": account
        }

    def report_quota_and_swap(
        self,
        quota_type: str = "5h",
        account_id: Optional[str] = None,
        custom_until: Optional[Any] = None,
        custom_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Marca a conta como esgotada (5 horas ou semanal) e faz a troca automática
        para a próxima conta saudável disponível com cálculo inteligente de reset.
        """
        if not account_id:
            active = self.vault.get_active_account()
            if not active:
                return {"success": False, "message": "Nenhuma conta ativa definida no sistema."}
            account_id = active.get("id")

        exhausted_account = self.vault.set_account_cooldown(
            account_id,
            quota_type=quota_type,
            custom_until=custom_until,
            custom_seconds=custom_seconds
        )
        if not exhausted_account:
            return {"success": False, "message": f"Conta '{account_id}' não encontrada."}

        reason_str = exhausted_account.get("cooldown_reason", quota_type)
        self.vault.log_history(
            event_type="QUOTA_EXHAUSTED",
            message=f"Cota esgotada ({reason_str}) para '{exhausted_account.get('name')}'. Buscando próxima conta...",
            details={"account_id": account_id, "quota_type": quota_type, "cooldown_until": exhausted_account.get("cooldown_until")}
        )

        # Procura a próxima conta saudável
        next_account = self.vault.get_next_available_account(exclude_id=account_id)
        if not next_account:
            self.vault.log_history(
                event_type="ALL_EXHAUSTED",
                message="ATENÇÃO: Todas as contas cadastradas estão em cooldown!",
                details={}
            )
            return {
                "success": False,
                "all_exhausted": True,
                "message": f"A conta '{exhausted_account.get('name')}' foi marcada como em pausa ({reason_str}), mas não há outras contas livres no momento."
            }

        # Realiza o Hot-Swap para a próxima conta
        swap_result = self.switch_to_account(next_account["id"])
        if swap_result.get("success"):
            self.vault.log_history(
                event_type="HOTSWAP_AUTO",
                message=f"Hot-Swap Automático ativado: '{exhausted_account.get('name')}' -> '{next_account.get('name')}'",
                details={
                    "from_account": exhausted_account.get("email"),
                    "to_account": next_account.get("email")
                }
            )
            return {
                "success": True,
                "message": f"Hot-Swap automático executado! Mudou de '{exhausted_account.get('name')}' para '{next_account.get('name')}'.",
                "old_account": exhausted_account,
                "new_account": next_account
            }
        else:
            return {
                "success": False,
                "message": f"Falha ao trocar para a próxima conta: {swap_result.get('message')}"
            }

    def capture_active_wincred(self, name: str = "", email: str = "") -> Dict[str, Any]:
        """Captura a credencial que está atualmente ativa no Windows e a adiciona ao cofre."""
        current_cred = read_credential(TARGET_CREDENTIAL)
        if not current_cred:
            return {
                "success": False,
                "message": "Nenhuma credencial ativa encontrada em 'gemini:antigravity' no Windows."
            }

        acc = self.vault.add_or_update_account(
            name=name or "Conta Antigravity Capturada",
            email=email or "ativa@google.com",
            blob=current_cred.get("blob", ""),
            username=current_cred.get("username", "antigravity"),
            set_active=True
        )

        self.vault.log_history(
            event_type="ACCOUNT_CAPTURED",
            message=f"Conta '{acc.get('name')}' ({acc.get('email')}) capturada do cofre do Windows.",
            details={"account_id": acc.get("id")}
        )

        return {
            "success": True,
            "message": f"Conta '{acc.get('name')}' capturada e registrada com sucesso!",
            "account": acc
        }
