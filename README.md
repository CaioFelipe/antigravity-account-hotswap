# 🔄 Antigravity Account Hot-Swap & Quota Manager

Sistema autônomo, limpo e de alta performance para gerenciamento de múltiplas contas do Google no **Google Antigravity / agy CLI**, alternância atômica (*Hot-Swap*) no **Windows Credential Manager (WinCred)** e **monitoramento de cotas em tempo real** (janela de 5 horas e limite semanal) com atualização direta da janela da IDE.

---

## 🔒 Segurança e Privacidade por Design

* **Zero Credenciais Incluídas:** Este repositório de código aberto **não contém nenhuma chave de API, token OAuth ou credencial pessoal**.
* Todas as contas e tokens adicionados pelo usuário ficam armazenados exclusivamente de forma local na máquina dele (ignorado pelo `.gitignore` em `data/accounts_vault.json`).
* As substituições de token acontecem diretamente no Cofre de Credenciais do Windows (`advapi32.dll:CredWriteW`), sem expor senhas em texto puro.

---

## ⚡ Principais Recursos

1. **Monitoramento de Cotas em Tempo Real:**
   - Consulta diretamente a telemetria do Antigravity (`language_server.exe`) para exibir a porcentagem exata disponível e o tempo restante para o reset da janela de 5 horas e cota semanal.
   - Suporte a modelos como Gemini 3.1 Pro, Gemini 3.8 Flash e Claude Sonnet 4.6.
2. **Hot-Swap com 1 Clique:**
   - Alterne instantaneamente entre contas Google cadastradas.
   - Botão **🔄 Recarregar Janela do Antigravity**: envia um sinal direto via DevTools para atualizar o ambiente do Antigravity sem precisar reiniciar a aplicação.
3. **Cadastro Simplificado de Contas Google:**
   - Abre o navegador automaticamente na página oficial de consentimento do Google.
   - Suporte ao código de autorização Google (`4/0...`) com troca direta de credenciais e armazenamento seguro no cofre.
4. **Interface Minimalista e Direta:**
   - Sem telas complexas ou poluição visual: apenas os cards das suas contas, consumo de cota em tempo real e o botão de troca rápida.
   - Disponível em **Painel Web Localhost** (porta `5055`) e **Janela Desktop (Tkinter)**.

---

## 🚀 Como Iniciar

### 1. Painel Web Localhost (Recomendado)
Execute o arquivo:
```powershell
.\iniciar_painel_web.bat
```
Ou via terminal:
```powershell
python web/server.py
```
Acesse no seu navegador: **`http://localhost:5055`**

### 2. Janela Desktop Nativa (Tkinter)
Execute o arquivo:
```powershell
.\iniciar_janela_tkinter.bat
```
Ou via terminal:
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
```

---

## 📁 Estrutura do Repositório

```text
antigravity-account-hotswap/
├── core/
│   ├── quota_checker.py    # Leitura em tempo real de cotas e reset do Antigravity
│   ├── ide_reloader.py     # Atualização remota da janela do Antigravity via DevTools
│   ├── oauth_capture.py    # Fluxo automatizado de login Google e troca de códigos
│   ├── switcher.py         # Motor de Hot-Swap no Windows Credential Manager
│   ├── wincred.py          # Integração nativa com a API Win32 Credential Manager
│   ├── vault.py            # Gestão segura do cofre de contas local
│   └── auto_detector.py    # Auto-detector de telemetria e proxy
├── web/
│   ├── server.py           # Servidor REST HTTP (zero dependências extras, porta 5055)
│   └── index.html          # Dashboard minimalista Dark Mode com barras de cota ao vivo
├── data/
│   └── accounts_vault.example.json # Template de banco de contas (limpo)
├── gui_tkinter.py          # Interface gráfica nativa Windows (Tkinter)
├── cli.py                  # Ferramenta para linha de comando
├── iniciar_painel_web.bat  # Atalho de inicialização rápida Web
├── iniciar_janela_tkinter.bat # Atalho de inicialização rápida Desktop
├── .gitignore              # Proteção contra commit de credenciais
└── README.md               # Documentação do projeto
```
