import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core.vault import AccountsVault
from core.switcher import AccountSwitcher
from core.oauth_capture import enrollment_manager
from core.ide_reloader import reload_antigravity_window

class HotswapTkinterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Antigravity Hot-Swap de Contas")
        self.root.geometry("820x540")
        self.root.minsize(720, 480)

        self.vault = AccountsVault()
        self.switcher = AccountSwitcher(self.vault)

        self._apply_theme()
        self._build_ui()
        self.refresh_data()
        self._start_auto_refresh()

    def _apply_theme(self):
        style = ttk.Style()
        style.theme_use("clam")

        self.root.configure(bg="#0a0d14")
        style.configure(".", background="#0a0d14", foreground="#f8fafc", font=("Segoe UI", 9))
        style.configure("TLabel", background="#0a0d14", foreground="#f8fafc")
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground="#60a5fa")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 8), foreground="#94a3b8")
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=6, background="#1e283d", foreground="#ffffff")
        style.map("TButton", background=[("active", "#2563eb")])

        style.configure("Treeview", background="#121722", foreground="#f8fafc", fieldbackground="#121722", rowheight=32)
        style.configure("Treeview.Heading", background="#1e283d", foreground="#94a3b8", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#2563eb")])

    def _build_ui(self):
        # Header
        header_frame = tk.Frame(self.root, bg="#121722", padx=20, pady=12)
        header_frame.pack(fill="x", side="top")

        lbl_title = ttk.Label(header_frame, text="⚡ Antigravity Hot-Swap", style="Header.TLabel", background="#121722")
        lbl_title.pack(anchor="w")

        lbl_sub = ttk.Label(header_frame, text="Gerenciador de Múltiplas Contas Google & Monitoramento de Cotas em Tempo Real", style="SubHeader.TLabel", background="#121722")
        lbl_sub.pack(anchor="w")

        # Top Info Bar
        info_frame = tk.Frame(self.root, bg="#0a0d14", padx=20, pady=10)
        info_frame.pack(fill="x")

        self.lbl_active = ttk.Label(info_frame, text="Conta Ativa no Antigravity: Carregando...", font=("Segoe UI", 10, "bold"))
        self.lbl_active.pack(side="left")

        self.lbl_pool = ttk.Label(info_frame, text="0 contas", foreground="#94a3b8")
        self.lbl_pool.pack(side="right")

        # Main Table
        table_frame = tk.Frame(self.root, bg="#0a0d14", padx=20, pady=4)
        table_frame.pack(fill="both", expand=True)

        columns = ("id", "name", "email", "status", "quota_5h", "quota_weekly")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Nome da Conta")
        self.tree.heading("email", text="E-mail")
        self.tree.heading("status", text="Status")
        self.tree.heading("quota_5h", text="Cota 5 Horas")
        self.tree.heading("quota_weekly", text="Cota Semanal")

        self.tree.column("id", width=55, anchor="center")
        self.tree.column("name", width=150, anchor="w")
        self.tree.column("email", width=180, anchor="w")
        self.tree.column("status", width=120, anchor="center")
        self.tree.column("quota_5h", width=190, anchor="center")
        self.tree.column("quota_weekly", width=140, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Action Buttons
        btn_frame = tk.Frame(self.root, bg="#121722", padx=20, pady=12)
        btn_frame.pack(fill="x", side="bottom")

        btn_swap = tk.Button(btn_frame, text="⚡ Usar Conta Selecionada (Hot-Swap)", bg="#2563eb", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=14, pady=7, command=self.action_swap_selected)
        btn_swap.pack(side="left", padx=4)

        btn_reload = tk.Button(btn_frame, text="🔄 Recarregar Janela do Antigravity", bg="#7c3aed", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=7, command=self.action_reload_ide)
        btn_reload.pack(side="left", padx=4)

        btn_add = tk.Button(btn_frame, text="➕ Nova Conta Google", bg="#059669", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=7, command=self.action_enroll_new)
        btn_add.pack(side="right", padx=4)

        btn_del = tk.Button(btn_frame, text="✕ Excluir", bg="#334155", fg="#cbd5e1", font=("Segoe UI", 9), relief="flat", padx=8, pady=7, command=self.action_delete)
        btn_del.pack(side="right", padx=4)

    def refresh_data(self):
        status = self.switcher.get_status()
        active = status.get("active_account")
        accounts = status.get("accounts", [])
        quotas = status.get("quotas", {})

        if active:
            self.lbl_active.config(text=f"Conta Ativa no Antigravity: {active.get('name')} ({active.get('email')})", foreground="#60a5fa")
        else:
            self.lbl_active.config(text="Conta Ativa no Antigravity: Nenhuma configurada", foreground="#f87171")

        self.lbl_pool.config(text=f"Total: {len(accounts)} conta(s)")

        selected_id = self._get_selected_account_id()
        self.tree.delete(*self.tree.get_children())

        for acc in accounts:
            acc_id = acc.get("id")
            is_active = (active and active.get("id") == acc_id)
            quota_info = quotas.get(acc_id, {})

            if is_active:
                status_txt = "● EM USO"
            elif acc.get("status") == "COOLDOWN_5H":
                status_txt = "PAUSA 5H"
            elif acc.get("status") == "COOLDOWN_WEEKLY":
                status_txt = "PAUSA SEMANAL"
            else:
                status_txt = "PRONTA"

            q5h = quota_info.get("quota_5h", {})
            q_week = quota_info.get("quota_weekly", {})

            badge_5h = q5h.get("badge", "100% Livre")
            badge_week = q_week.get("badge", "100% Disponível")

            item = self.tree.insert("", "end", iid=acc_id, values=(acc_id, acc.get("name"), acc.get("email"), status_txt, badge_5h, badge_week))
            if selected_id == acc_id:
                self.tree.selection_set(acc_id)

    def _start_auto_refresh(self):
        self.refresh_data()
        self.root.after(4000, self._start_auto_refresh)

    def _get_selected_account_id(self):
        selected = self.tree.selection()
        return selected[0] if selected else None

    def action_swap_selected(self):
        acc_id = self._get_selected_account_id()
        if not acc_id:
            messagebox.showwarning("Aviso", "Selecione uma conta na tabela para ativar.")
            return
        res = self.switcher.switch_to_account(acc_id)
        if res.get("success"):
            reload_res = reload_antigravity_window()
            msg = res.get("message")
            if reload_res.get("success"):
                msg += "\n\nJanela do Antigravity recarregada automaticamente com a nova conta!"
            else:
                msg += "\n\nPara atualizar o IDE, pressione F1 no Antigravity e tecle Enter em 'Reload Window'."
            messagebox.showinfo("Sucesso", msg)
            self.refresh_data()
        else:
            messagebox.showerror("Erro", res.get("message"))

    def action_reload_ide(self):
        res = reload_antigravity_window()
        if res.get("success"):
            messagebox.showinfo("Sucesso", res.get("message"))
        else:
            messagebox.showwarning("Aviso", res.get("message"))

    def action_enroll_new(self):
        name = simpledialog.askstring("Cadastrar Nova Conta", "Informe um apelido para a nova conta Google:")
        if not name:
            return

        res = enrollment_manager.start_enrollment(account_name=name)
        if not res.get("success"):
            messagebox.showerror("Erro", res.get("message"))
            return

        code = simpledialog.askstring(
            "Autorização Google",
            "O navegador foi aberto na página do Google.\nFaça login e copie o código de autorização (inicia com 4/0...).\n\nCole o código abaixo:"
        )
        if not code:
            enrollment_manager.cancel_enrollment()
            return

        submit_res = enrollment_manager.submit_code(code)
        if submit_res.get("success"):
            messagebox.showinfo("Sucesso", submit_res.get("message"))
            self.refresh_data()
        else:
            messagebox.showerror("Erro", submit_res.get("message"))

    def action_delete(self):
        acc_id = self._get_selected_account_id()
        if not acc_id:
            messagebox.showwarning("Aviso", "Selecione uma conta para excluir.")
            return
        if messagebox.askyesno("Confirmar Exclusão", f"Deseja realmente remover a conta {acc_id}?"):
            self.vault.remove_account(acc_id)
            self.refresh_data()

def run():
    root = tk.Tk()
    app = HotswapTkinterApp(root)
    root.mainloop()

if __name__ == "__main__":
    run()
