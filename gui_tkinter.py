import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from core.vault import AccountsVault, WEEKDAY_NAMES
from core.switcher import AccountSwitcher
from core.oauth_capture import enroll_new_google_account

class HotswapTkinterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Central de Hot-Swap de Contas - Antigravity")
        self.root.geometry("880x640")
        self.root.minsize(760, 520)

        self.vault = AccountsVault()
        self.switcher = AccountSwitcher(self.vault)

        self._apply_theme()
        self._build_ui()
        self.refresh_data()
        self._start_auto_refresh()

    def _apply_theme(self):
        style = ttk.Style()
        style.theme_use("clam")

        self.root.configure(bg="#0c0f17")
        style.configure(".", background="#0c0f17", foreground="#f1f5f9", font=("Segoe UI", 9))
        style.configure("TLabel", background="#0c0f17", foreground="#f1f5f9")
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground="#60a5fa")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 8), foreground="#94a3b8")
        style.configure("TButton", font=("Segoe UI", 9, "bold"), padding=6, background="#1e2638", foreground="#ffffff")
        style.map("TButton", background=[("active", "#2563eb")])

        style.configure("Treeview", background="#151926", foreground="#f1f5f9", fieldbackground="#151926", rowheight=32)
        style.configure("Treeview.Heading", background="#1e2638", foreground="#94a3b8", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#2563eb")])

    def _build_ui(self):
        # Header
        header_frame = tk.Frame(self.root, bg="#151926", padx=20, pady=14)
        header_frame.pack(fill="x", side="top")

        lbl_title = ttk.Label(header_frame, text="Antigravity Account Hot-Swap", style="Header.TLabel", background="#151926")
        lbl_title.pack(anchor="w")

        lbl_sub = ttk.Label(header_frame, text="Gerenciador de Múltiplas Contas Google & Proteção Inteligente de Cotas", style="SubHeader.TLabel", background="#151926")
        lbl_sub.pack(anchor="w")

        # Top Info Bar
        info_frame = tk.Frame(self.root, bg="#0c0f17", padx=20, pady=10)
        info_frame.pack(fill="x")

        self.lbl_active = ttk.Label(info_frame, text="Conta Ativa no Antigravity: Carregando...", font=("Segoe UI", 10, "bold"))
        self.lbl_active.pack(side="left")

        self.lbl_pool = ttk.Label(info_frame, text="Pool: 0 contas", foreground="#94a3b8")
        self.lbl_pool.pack(side="right")

        # Main Table
        table_frame = tk.Frame(self.root, bg="#0c0f17", padx=20, pady=5)
        table_frame.pack(fill="both", expand=True)

        columns = ("id", "name", "email", "status", "cooldown", "schedule")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Nome / Identificador")
        self.tree.heading("email", text="E-mail")
        self.tree.heading("status", text="Status")
        self.tree.heading("cooldown", text="Cota / Cooldown Restante")
        self.tree.heading("schedule", text="Ciclo Semanal")

        self.tree.column("id", width=55, anchor="center")
        self.tree.column("name", width=160, anchor="w")
        self.tree.column("email", width=190, anchor="w")
        self.tree.column("status", width=130, anchor="center")
        self.tree.column("cooldown", width=160, anchor="center")
        self.tree.column("schedule", width=110, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Action Buttons
        btn_frame = tk.Frame(self.root, bg="#151926", padx=20, pady=12)
        btn_frame.pack(fill="x", side="bottom")

        # Row 1 of buttons
        row1 = tk.Frame(btn_frame, bg="#151926")
        row1.pack(fill="x", pady=2)

        btn_swap = tk.Button(row1, text="🔄 Ativar Selecionada (Hot-Swap)", bg="#2563eb", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=6, command=self.action_swap_selected)
        btn_swap.pack(side="left", padx=4)

        btn_auto = tk.Button(row1, text="⚡ Auto-Swap (Próxima Saudável)", bg="#d97706", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=6, command=self.action_auto_swap)
        btn_auto.pack(side="left", padx=4)

        btn_adj = tk.Button(row1, text="✏️ Ajustar Reset", bg="#4f46e5", fg="#ffffff", font=("Segoe UI", 9, "bold"), relief="flat", padx=8, pady=6, command=self.action_adjust_reset)
        btn_adj.pack(side="left", padx=4)

        btn_sched = tk.Button(row1, text="⚙️ Ciclo Semanal", bg="#334155", fg="#cbd5e1", font=("Segoe UI", 9), relief="flat", padx=8, pady=6, command=self.action_set_schedule)
        btn_sched.pack(side="left", padx=4)

        btn_liberar = tk.Button(row1, text="✓ Liberar Cooldown", bg="#059669", fg="#ffffff", font=("Segoe UI", 9), relief="flat", padx=8, pady=6, command=self.action_clear_cooldown)
        btn_liberar.pack(side="left", padx=4)

        btn_del = tk.Button(row1, text="✕ Excluir", bg="#475569", fg="#ffffff", font=("Segoe UI", 9), relief="flat", padx=8, pady=6, command=self.action_delete)
        btn_del.pack(side="right", padx=4)

        # Row 2 of buttons (Quota triggers & Enrollment)
        row2 = tk.Frame(btn_frame, bg="#151926")
        row2.pack(fill="x", pady=6)

        btn_5h = tk.Button(row2, text="⏱️ Esgotar Cota 5h", bg="#334155", fg="#fbbf24", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=4, command=lambda: self.action_exhaust("5h"))
        btn_5h.pack(side="left", padx=4)

        btn_week = tk.Button(row2, text="📅 Esgotar Cota Semanal", bg="#334155", fg="#f87171", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=4, command=lambda: self.action_exhaust("weekly"))
        btn_week.pack(side="left", padx=4)

        btn_capture = tk.Button(row2, text="📥 Capturar Atual do Windows", bg="#334155", fg="#93c5fd", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=4, command=self.action_capture_current)
        btn_capture.pack(side="right", padx=4)

        btn_add = tk.Button(row2, text="➕ Cadastrar Nova Conta Google", bg="#3b82f6", fg="#ffffff", font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=4, command=self.action_enroll_new)
        btn_add.pack(side="right", padx=4)

    def refresh_data(self):
        status = self.switcher.get_status()
        active = status.get("active_account")
        accounts = status.get("accounts", [])
        now = status.get("now", time.time())

        if active:
            self.lbl_active.config(text=f"Conta Ativa no Antigravity: {active.get('name')} ({active.get('email')})", foreground="#60a5fa")
        else:
            self.lbl_active.config(text="Conta Ativa no Antigravity: Nenhuma configurada", foreground="#f87171")

        healthy_count = len([a for a in accounts if not a.get("cooldown_until")])
        self.lbl_pool.config(text=f"Total: {len(accounts)} | Disponíveis: {healthy_count}")

        selected_id = self._get_selected_account_id()
        self.tree.delete(*self.tree.get_children())

        for acc in accounts:
            acc_id = acc.get("id")
            is_active = (active and active.get("id") == acc_id)

            if is_active:
                status_txt = "● ATIVA"
            elif acc.get("status") == "COOLDOWN_5H":
                status_txt = "PAUSA 5H"
            elif acc.get("status") == "COOLDOWN_WEEKLY":
                status_txt = "PAUSA SEMANAL"
            else:
                status_txt = "PRONTA"

            cooldown_until = acc.get("cooldown_until")
            if cooldown_until:
                diff = max(0, int(cooldown_until - now))
                h = diff // 3600
                m = (diff % 3600) // 60
                s = diff % 60
                cooldown_txt = f"{h:02d}h {m:02d}m {s:02d}s restantes"
            else:
                cooldown_txt = "Livre"

            w_day = acc.get("weekly_reset_day", 0)
            w_time = acc.get("weekly_reset_time", "00:00")
            day_name = WEEKDAY_NAMES[w_day][:3] if 0 <= w_day < 7 else "Seg"
            cycle_txt = f"{day_name} {w_time}"

            item = self.tree.insert("", "end", iid=acc_id, values=(acc_id, acc.get("name"), acc.get("email"), status_txt, cooldown_txt, cycle_txt))
            if selected_id == acc_id:
                self.tree.selection_set(acc_id)

    def _start_auto_refresh(self):
        self.refresh_data()
        self.root.after(2000, self._start_auto_refresh)

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
            messagebox.showinfo("Sucesso", res.get("message"))
        else:
            messagebox.showerror("Erro", res.get("message"))
        self.refresh_data()

    def action_auto_swap(self):
        res = self.switcher.report_quota_and_swap(quota_type="5h")
        if res.get("success"):
            messagebox.showinfo("Hot-Swap Automático", res.get("message"))
        else:
            messagebox.showwarning("Atenção", res.get("message"))
        self.refresh_data()

    def action_exhaust(self, quota_type: str):
        acc_id = self._get_selected_account_id()
        acc = self.vault.get_account(acc_id) if acc_id else self.vault.get_active_account()

        if quota_type == "weekly":
            # Pergunta se quer ciclo semanal configurado ou personalizado
            opt = messagebox.askyesno(
                "Cota Semanal Esgotada",
                f"Deseja calcular o tempo até o próximo ciclo semanal configurado desta conta?\n\n(Sim = Calcula até a próxima renovação da conta)\n(Não = Informar data/hora específica manualmente)"
            )
            if opt:
                res = self.switcher.report_quota_and_swap(quota_type="weekly", account_id=acc_id)
            else:
                custom_str = simpledialog.askstring("Data/Hora de Retorno", "Informe quando a cota volta (Ex: 2026-10-03T14:00 ou 16:30):")
                if not custom_str:
                    return
                res = self.switcher.report_quota_and_swap(quota_type="weekly", account_id=acc_id, custom_until=custom_str)
        else:
            # 5 horas
            opt = messagebox.askyesno(
                "Cota 5 Horas Esgotada",
                "Deseja aplicar a pausa padrão de 5 horas a partir de agora?\n\n(Sim = 5 horas padrão)\n(Não = Informar minutos restantes)"
            )
            if opt:
                res = self.switcher.report_quota_and_swap(quota_type="5h", account_id=acc_id)
            else:
                minutos = simpledialog.askinteger("Minutos Restantes", "Quantos minutos faltam para a cota resetar? (Ex: 90):", minvalue=1, maxvalue=600)
                if not minutos:
                    return
                res = self.switcher.report_quota_and_swap(quota_type="5h", account_id=acc_id, custom_seconds=minutos * 60)

        if res.get("success"):
            messagebox.showinfo("Hot-Swap Realizado", res.get("message"))
        else:
            messagebox.showwarning("Atenção", res.get("message"))
        self.refresh_data()

    def action_adjust_reset(self):
        acc_id = self._get_selected_account_id()
        if not acc_id:
            messagebox.showwarning("Aviso", "Selecione uma conta na tabela para ajustar o horário de reset.")
            return
        acc = self.vault.get_account(acc_id)
        if not acc:
            return

        novo_horario = simpledialog.askstring(
            "Ajustar Horário de Retorno",
            f"Informe quando a conta '{acc.get('name')}' volta a ficar disponível:\nFormatos aceitos: AAAA-MM-DDTHH:MM ou HH:MM (ex: 15:30):"
        )
        if not novo_horario:
            return

        res = self.vault.adjust_account_cooldown(acc_id, novo_horario)
        if res:
            messagebox.showinfo("Sucesso", f"Horário de reset atualizado para: {novo_horario}")
        else:
            messagebox.showerror("Erro", "Formato de data/hora inválido ou data já ultrapassada.")
        self.refresh_data()

    def action_set_schedule(self):
        acc_id = self._get_selected_account_id()
        if not acc_id:
            messagebox.showwarning("Aviso", "Selecione uma conta para configurar o ciclo semanal.")
            return
        acc = self.vault.get_account(acc_id)
        if not acc:
            return

        dia_txt = simpledialog.askinteger(
            "Dia da Semana de Renovação",
            "Informe o dia da semana em que a cota semanal renova:\n0 = Segunda-feira\n1 = Terça-feira\n2 = Quarta-feira\n3 = Quinta-feira\n4 = Sexta-feira\n5 = Sábado\n6 = Domingo",
            minvalue=0, maxvalue=6, initialvalue=acc.get("weekly_reset_day", 0)
        )
        if dia_txt is None:
            return

        hora_txt = simpledialog.askstring("Horário de Renovação", "Informe o horário de renovação (HH:MM):", initialvalue=acc.get("weekly_reset_time", "00:00"))
        if not hora_txt:
            hora_txt = "00:00"

        self.vault.update_account_schedule(acc_id, dia_txt, hora_txt)
        messagebox.showinfo("Sucesso", f"Ciclo semanal configurado para: Toda {WEEKDAY_NAMES[dia_txt]} às {hora_txt}!")
        self.refresh_data()

    def action_clear_cooldown(self):
        acc_id = self._get_selected_account_id()
        if not acc_id:
            messagebox.showwarning("Aviso", "Selecione uma conta para liberar o cooldown.")
            return
        acc = self.vault.clear_account_cooldown(acc_id)
        if acc:
            messagebox.showinfo("Sucesso", f"Cooldown da conta '{acc.get('name')}' liberado com sucesso!")
        self.refresh_data()

    def action_capture_current(self):
        name = simpledialog.askstring("Capturar Conta", "Digite um nome para a conta atualmente logada no Antigravity:", initialvalue="Conta Antigravity Principal")
        if not name:
            return
        res = self.switcher.capture_active_wincred(name=name)
        if res.get("success"):
            messagebox.showinfo("Sucesso", res.get("message"))
        else:
            messagebox.showerror("Erro", res.get("message"))
        self.refresh_data()

    def action_enroll_new(self):
        name = simpledialog.askstring("Cadastrar Nova Conta", "Informe um nome/apelido para a nova conta Google:")
        if not name:
            return
        messagebox.showinfo("Iniciar Login", "Ao clicar em OK, o navegador abrirá para login com sua nova conta Google.\n\nEscolha 'Usar outra conta' no Google e conclua a autenticação.")

        def run_bg():
            enroll_new_google_account(account_name=name)
            self.root.after(100, self.refresh_data)

        t = threading.Thread(target=run_bg, daemon=True)
        t.start()

    def action_delete(self):
        acc_id = self._get_selected_account_id()
        if not acc_id:
            messagebox.showwarning("Aviso", "Selecione uma conta para excluir.")
            return
        if messagebox.askyesno("Confirmar Exclusão", f"Deseja realmente excluir a conta {acc_id} do cofre?"):
            self.vault.remove_account(acc_id)
            self.refresh_data()

def run():
    root = tk.Tk()
    app = HotswapTkinterApp(root)
    root.mainloop()

if __name__ == "__main__":
    run()
