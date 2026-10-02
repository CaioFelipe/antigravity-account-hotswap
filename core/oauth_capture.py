import os
import sys
import time
import shutil
import subprocess
from typing import Dict, Any, Optional
from pathlib import Path

from .wincred import read_credential, write_credential, delete_credential
from .vault import AccountsVault, _extract_email_from_blob

TARGET_CREDENTIAL = "gemini:antigravity"

def find_agy_binary() -> Optional[str]:
    agy = shutil.which("agy") or shutil.which("agy.exe")
    if agy:
        return agy
    default_path = Path(os.path.expanduser(r"~\AppData\Local\agy\bin\agy.exe"))
    if default_path.exists():
        return str(default_path)
    return None

def enroll_new_google_account(account_name: str, account_email: str = "", restore_previous: bool = False) -> Dict[str, Any]:
    """
    Executa o processo de captura de uma nova conta Google:
    1. Salva a credencial atual de backup.
    2. Libera o cofre 'gemini:antigravity'.
    3. Invoca o agy.exe para abrir a tela de login OAuth do Google no navegador.
    4. Aguarda a conclusão do login e captura o novo token.
    5. Cadastra a nova conta no vault.
    6. Opcionalmente restaura a conta anterior.
    """
    agy_path = find_agy_binary()
    if not agy_path:
        return {
            "success": False,
            "message": "Executável 'agy.exe' não foi encontrado no sistema."
        }

    vault = AccountsVault()
    previous_cred = read_credential(TARGET_CREDENTIAL)

    try:
        # 1. Limpa a credencial atual para forçar login
        delete_credential(TARGET_CREDENTIAL)

        # 2. Executa agy para abrir o navegador
        # Rodamos de forma visível ou em processo desacoplado
        proc = subprocess.Popen([agy_path, "-p", "Ola, inicializacao de conta"], creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)

        # 3. Aguardar até o usuário logar no navegador (timeout de 180s)
        start_time = time.time()
        new_cred = None
        while time.time() - start_time < 180:
            time.sleep(2)
            cred = read_credential(TARGET_CREDENTIAL)
            if cred and cred.get("blob"):
                # Se não havia credencial anterior ou se o token novo é diferente
                if not previous_cred or cred.get("blob") != previous_cred.get("blob"):
                    new_cred = cred
                    break

        if not new_cred:
            return {
                "success": False,
                "message": "Tempo limite de 180 segundos esgotado ou nenhum novo login detectado."
            }

        # 4. Extrair e-mail se não fornecido
        extracted_email = _extract_email_from_blob(new_cred.get("blob", ""))
        final_email = account_email or extracted_email or "nova_conta@google.com"

        # 5. Salvar nova conta no Vault
        new_account = vault.add_or_update_account(
            name=account_name or f"Conta Google ({final_email})",
            email=final_email,
            blob=new_cred.get("blob", ""),
            username=new_cred.get("username", "antigravity"),
            set_active=not restore_previous
        )

        vault.log_history(
            event_type="ACCOUNT_ENROLLED",
            message=f"Nova conta cadastrada: '{new_account.get('name')}' ({new_account.get('email')})",
            details={"account_id": new_account.get("id")}
        )

        # 6. Se solicitado restaurar a anterior
        if restore_previous and previous_cred:
            write_credential(
                TARGET_CREDENTIAL,
                username=previous_cred.get("username", "antigravity"),
                blob_str=previous_cred.get("blob", "")
            )

        return {
            "success": True,
            "message": f"Conta '{new_account.get('name')}' cadastrada com sucesso!",
            "account": new_account
        }

    except Exception as e:
        if previous_cred:
            write_credential(
                TARGET_CREDENTIAL,
                username=previous_cred.get("username", "antigravity"),
                blob_str=previous_cred.get("blob", "")
            )
        return {
            "success": False,
            "message": f"Erro durante o fluxo de login: {str(e)}"
        }

if __name__ == "__main__":
    name = input("Nome identificador da nova conta (ex: Gmail Pessoal): ")
    email = input("E-mail aproximado (ou deixe vazio para auto-detectar): ")
    print("Iniciando fluxo de login...")
    res = enroll_new_google_account(name, email, restore_previous=False)
    print("Resultado:", res)
