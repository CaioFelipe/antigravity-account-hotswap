import time
from typing import Dict, Any, Optional
from .wincred import read_credential, write_credential
from .vault import AccountsVault

TARGET_CREDENTIAL = "gemini:antigravity"

class AccountSwitcher:
    def __init__(self, vault: Optional[AccountsVault] = None):
        self.vault = vault or AccountsVault()

    def get_status(self) -> Dict[str, Any]:
        """Retorna o estado geral atual do sistema de Hot-Swap."""
        self.vault.refresh_cooldowns()
        accounts = self.vault.list_accounts()
        active = self.vault.get_active_account()
        current_win = read_credential(TARGET_CREDENTIAL)

        # Checar se a credencial no Windows bate com a conta ativa no cofre
        wincred_synced = False
        if current_win and active:
            wincred_synced = (current_win.get("blob") == active.get("blob"))

        return {
            "active_account": active,
            "wincred_synced": wincred_synced,
            "total_accounts": len(accounts),
            "accounts": accounts,
            "now": time.time()
        }

    def switch_to_account(self, account_id: str) -> Dict[str, Any]:
        """Aplica a credencial da conta especificada diretamente no Windows Credential Manager."""
        account = self.vault.get_account(account_id)
        if not account:
            return {"success": False, "message": f"Conta '{account_id}' não encontrada."}

        blob_str = account.get("blob", "")
        username = account.get("username", "antigravity")

        success = write_credential(
            target=TARGET_CREDENTIAL,
            username=username,
            blob_str=blob_str
        )

        if success:
            self.vault.set_active_account(account_id)
            self.vault.log_history(
                event_type="HOTSWAP_MANUAL",
                message=f"Hot-Swap realizado para '{account.get('name')}' ({account.get('email')})",
                details={"account_id": account_id, "email": account.get("email")}
            )
            return {
                "success": True,
                "message": f"Hot-Swap concluído com sucesso para a conta '{account.get('name')}'!",
                "account": account
            }
        else:
            return {
                "success": False,
                "message": "Falha ao gravar credencial no Windows Credential Manager."
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
