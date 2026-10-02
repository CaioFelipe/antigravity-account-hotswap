import os
import sys
import re
import time
import shutil
import threading
import subprocess
import webbrowser
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

class OAuthEnrollmentManager:
    """
    Gerenciador singleton para cadastro interativo de novas contas Google.
    Suporta abertura automática do navegador e recepção do código retornado pelo Google.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_state()
            return cls._instance

    def _init_state(self):
        self.state = "idle"  # idle | starting | waiting_code | completed | error
        self.auth_url = ""
        self.account_name = ""
        self.error_message = ""
        self.account_data = None
        self.proc: Optional[subprocess.Popen] = None
        self.backup_cred = None
        self.worker_thread = None

    def start_enrollment(self, account_name: str = "Nova Conta Google") -> Dict[str, Any]:
        with self._lock:
            # Se já houver um processo anterior rodando, cancela
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass

            agy_path = find_agy_binary()
            if not agy_path:
                self.state = "error"
                self.error_message = "Executável 'agy.exe' não foi encontrado no sistema."
                return {"success": False, "message": self.error_message}

            # 1. Faz backup da credencial atual
            self.backup_cred = read_credential(TARGET_CREDENTIAL)
            self.account_name = account_name
            self.state = "starting"
            self.auth_url = ""
            self.error_message = ""
            self.account_data = None

            # 2. Deleta temporariamente a credencial para o agy disparar autenticação
            delete_credential(TARGET_CREDENTIAL)

            # 3. Spawna agy em subprocesso capturando stdin/stdout
            try:
                self.proc = subprocess.Popen(
                    [agy_path, "-p", "auth_probe"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
            except Exception as e:
                # Restaura credencial em caso de falha de spawn
                if self.backup_cred:
                    write_credential(TARGET_CREDENTIAL, self.backup_cred.get("username", "antigravity"), self.backup_cred.get("blob", ""))
                self.state = "error"
                self.error_message = f"Falha ao iniciar processo de autenticação: {str(e)}"
                return {"success": False, "message": self.error_message}

            # 4. Inicia thread de escuta para capturar a URL do Google
            self.worker_thread = threading.Thread(target=self._read_agy_stdout, daemon=True)
            self.worker_thread.start()

            return {
                "success": True,
                "state": "starting",
                "message": "Iniciando autenticação Google..."
            }

    def _read_agy_stdout(self):
        url_regex = re.compile(r'https://accounts\.google\.com/o/oauth2/auth[^\s"\'\<\>]+')
        browser_opened = False

        try:
            for line in iter(self.proc.stdout.readline, ''):
                if not line:
                    break

                # Procura URL de autenticação
                match = url_regex.search(line)
                if match and not browser_opened:
                    self.auth_url = match.group(0)
                    self.state = "waiting_code"
                    browser_opened = True
                    # Abre o navegador automaticamente para o usuário
                    try:
                        webbrowser.open(self.auth_url)
                    except Exception:
                        pass

            # Quando o processo encerra
            self.proc.wait()
            
            # Checa se gravou nova credencial
            new_cred = read_credential(TARGET_CREDENTIAL)
            if new_cred and new_cred.get("blob") and (not self.backup_cred or new_cred.get("blob") != self.backup_cred.get("blob")):
                self._finalize_success(new_cred)
            elif self.state != "completed":
                # Se não concluiu e o processo morreu
                if self.backup_cred:
                    write_credential(TARGET_CREDENTIAL, self.backup_cred.get("username", "antigravity"), self.backup_cred.get("blob", ""))
                if self.state != "error":
                    self.state = "error"
                    self.error_message = "O processo de login foi encerrado antes de concluir a autorização."

        except Exception as e:
            self.state = "error"
            self.error_message = f"Erro ao monitorar login: {str(e)}"
            if self.backup_cred:
                write_credential(TARGET_CREDENTIAL, self.backup_cred.get("username", "antigravity"), self.backup_cred.get("blob", ""))

    def submit_code(self, auth_code: str) -> Dict[str, Any]:
        """
        Recebe o código copiado pelo usuário (ex: 4/0...) e envia no STDIN do agy.
        """
        clean_code = auth_code.strip()
        if not clean_code:
            return {"success": False, "message": "Código de autorização não pode ser vazio."}

        if not self.proc or self.proc.poll() is not None:
            # O processo agy pode ter encerrado antes ou esperado muito tempo
            return {"success": False, "message": "A sessão de login expirou. Inicie o cadastro novamente."}

        try:
            self.proc.stdin.write(f"{clean_code}\n")
            self.proc.stdin.flush()
        except Exception as e:
            return {"success": False, "message": f"Falha ao enviar código para o processo: {str(e)}"}

        # Aguarda até 10 segundos para a finalização
        for _ in range(20):
            time.sleep(0.5)
            new_cred = read_credential(TARGET_CREDENTIAL)
            if new_cred and new_cred.get("blob") and (not self.backup_cred or new_cred.get("blob") != self.backup_cred.get("blob")):
                return self._finalize_success(new_cred)

        return {"success": False, "message": "Código recebido, mas o Google não confirmou o token a tempo. Verifique se o código está completo."}

    def _finalize_success(self, new_cred: dict) -> Dict[str, Any]:
        vault = AccountsVault()
        extracted_email = _extract_email_from_blob(new_cred.get("blob", ""))
        email = extracted_email or "nova_conta@google.com"
        name = self.account_name or f"Conta Google ({email})"

        # Salva no cofre como conta pronta
        account = vault.add_or_update_account(
            name=name,
            email=email,
            blob=new_cred.get("blob", ""),
            username=new_cred.get("username", "antigravity"),
            set_active=True
        )

        vault.log_history("ACCOUNT_ENROLLED", f"Nova conta '{name}' ({email}) cadastrada com sucesso!")

        self.state = "completed"
        self.account_data = account

        return {
            "success": True,
            "message": f"Conta '{name}' ({email}) cadastrada com sucesso!",
            "account": account
        }

    def cancel_enrollment(self) -> Dict[str, Any]:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            if self.backup_cred:
                write_credential(TARGET_CREDENTIAL, self.backup_cred.get("username", "antigravity"), self.backup_cred.get("blob", ""))
            self.state = "idle"
            self.auth_url = ""
            return {"success": True, "message": "Cadastro cancelado."}

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "auth_url": self.auth_url,
            "account_name": self.account_name,
            "error_message": self.error_message,
            "account": self.account_data
        }

# Instância global
enrollment_manager = OAuthEnrollmentManager()

def enroll_new_google_account(account_name: str, account_email: str = "", restore_previous: bool = False) -> Dict[str, Any]:
    return enrollment_manager.start_enrollment(account_name)
