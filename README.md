# 🔄 Antigravity Account Hot-Swap & Quota Manager

Sistema autônomo e de alta performance para gerenciamento de múltiplas contas do Google no **Google Antigravity / agy CLI**, alternância atômica (*Hot-Swap*) no **Windows Credential Manager (WinCred)** e **proteção de cotas** com auto-detecção em tempo real de limites de 5 horas e semanais.

---

## 🔒 Segurança e Privacidade por Design

* **Zero Credenciais Incluídas:** Este repositório de código aberto **não contém nenhuma chave de API, token OAuth ou credencial pessoal**.
* Todas as contas e tokens adicionados pelo usuário ficam armazenados exclusivamente de forma local na máquina dele (ignorado pelo `.gitignore` em `data/accounts_vault.json`).
* As substituições de token acontecem de forma atômica diretamente no Cofre de Credenciais do Windows (`advapi32.dll:CredWriteW`), sem expor senhas em texto puro.

---

## ⚡ Principais Recursos

1. **Hot-Swap Atômico no Windows Credential Manager:**
   - Alterna a conta conectada no Antigravity instantaneamente (`gemini:antigravity`).
   - Não requer reiniciar o Antigravity IDE nem o terminal `agy.exe`.
2. **Auto-Detecção de Cotas da Google (Tempo Real):**
   - Extrai automaticamente o timestamp `resetTime` em milissegundos emitido pelos servidores da Google quando um limite é atingido.
   - Analisador de mensagens de erro da IDE/CLI com regex (`retryDelay`, timestamps ISO, horários relativos).
3. **Cálculo Preciso de Ciclo Semanal (Sem esperas desnecessárias):**
   - Permite programar em qual dia da semana e horário cada conta renova no Google (ex: *Toda Segunda às 00:00*).
   - Se uma cota semanal for esgotada numa sexta e renovar no sábado, a pausa será de apenas 11 horas e **não** de 7 dias.
4. **Múltiplas Formas de Uso:**
   - 🌐 **Painel Web Localhost:** Dashboard escuro com contadores regressivos dinâmicos ao vivo na porta `5055`.
   - 🖥️ **Interface Desktop Nativa (Tkinter):** Janela tradicional rápida para Windows.
   - 💻 **Linha de Comando (CLI):** Automatizável via scripts, cron jobs ou subagentes de IA.

---

## 🚀 Como Iniciar

### 1. Painel Web Localhost (Recomendado)
Execute:
```powershell
.\iniciar_painel_web.bat
```
Ou manualmente:
```powershell
python web/server.py
```
Acesse no seu navegador: **`http://localhost:5055`**

### 2. Janela Desktop Nativa (Tkinter)
Execute:
```powershell
.\iniciar_janela_tkinter.bat
```
Ou manualmente:
```powershell
python gui_tkinter.py
```

### 3. Linha de Comando (CLI)
```powershell
# Listar contas e status de cota
python cli.py list

# Fazer Hot-Swap imediato para outra conta
python cli.py switch acc_2

# Trocar automaticamente para a próxima conta saudável
python cli.py auto-swap

# Varredura automática de rate-limits da Google
python cli.py auto-detect

# Analisar mensagem de erro e aplicar cooldown correto sozinho
python cli.py parse-error "Quota exceeded. retryDelay: 1800s"

# Rodar monitor contínuo em segundo plano
python cli.py daemon
```

---

## 📁 Estrutura do Repositório

```text
antigravity-account-hotswap/
├── core/
│   ├── wincred.py          # Integração nativa com a API Win32 Credential Manager
│   ├── vault.py            # Gestão segura do cofre de contas e ciclos semanais
│   ├── switcher.py         # Motor de Hot-Swap e failover automático
│   ├── oauth_capture.py    # Assistente guiado de login OAuth no navegador
│   └── auto_detector.py    # Auto-detector de telemetria da Google e parser de erros
├── web/
│   ├── server.py           # Servidor REST HTTP (zero dependências extras, porta 5055)
│   └── index.html          # Interface moderna Dark Mode com timers ao vivo
├── data/
│   └── accounts_vault.example.json # Template de banco de contas (limpo)
├── gui_tkinter.py          # Interface gráfica nativa Windows (Tkinter)
├── cli.py                  # Ferramenta para linha de comando
├── iniciar_painel_web.bat  # Atalho de inicialização rápida Web
├── iniciar_janela_tkinter.bat # Atalho de inicialização rápida Desktop
├── .gitignore              # Proteção contra commit de credenciais
└── README.md               # Documentação do projeto
```

---

## 📄 Licença
MIT License. Livre para uso e modificação.
