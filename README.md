# 🎥 Controle e Gravação de Câmeras - Farmácia (v3.5 NVR Premium Dinâmico)

Este projeto é uma solução de NVR (Network Video Recorder) de baixíssimo consumo de hardware, projetada para capturar, gravar e monitorar múltiplas câmeras inteligentes (compatíveis com o ecossistema Tuya/Positivo) de forma dinâmica, portable e com tolerância a falhas.

---

## 🛠️ Como Funciona o Sistema

O sistema é dividido em três camadas integradas:

1. **Ponte RTSP (`go2rtc.exe`)**:
   Estabelece conexões seguras e autenticadas com os servidores Tuya Cloud e expõe as transmissões de vídeo das câmeras na rede local (nos formatos RTSP na porta `8554` e API Web na porta `1984`).
   
2. **Gravador Ultra-Leve (`gravador_camera.py`)**:
   - **0% Transcoding (Cópia Direta)**: O gravador se conecta à API HTTP do `go2rtc` (`/api/stream.mp4?src=NOME`) e baixa diretamente o fluxo binário H.264/H.265 do vídeo, salvando-o no disco. Isso elimina todo o processamento de decodificação e recodificação de vídeo. O uso de CPU é praticamente **0%**, e os arquivos finais são muito menores e mantêm a qualidade nativa da câmera.
   - **Independência de Bibliotecas**: Não requer `opencv-python` ou `numpy` para gravação. Funciona utilizando apenas as bibliotecas nativas do Python.
   - **Contingência de Sincronização**: Se a pasta do Google Drive (`G:\Meu Drive\CAMERAS`) estiver offline, cheia ou sem permissão de escrita ("Acesso de Leitor"), o gravador desvia o arquivo automaticamente para a pasta local `backup_gravacoes/` do PC.
   - **Detecção de Duplicidade na Rede**: Para evitar que dois computadores gravem a mesma câmera ao mesmo tempo (gerando conflitos de sincronização no Drive), cada gravador ativo envia batimentos cardíacos (heartbeat) em formato JSON a cada 30 segundos no Google Drive. Se outro gravador iniciar e detectar um batimento recente de outro host, ele cessa a gravação local imediatamente para evitar duplicidade.

3. **Gerenciador Gráfico (`gerenciador.pyw`)**:
   Interface em tema escuro que lê as configurações do arquivo `go2rtc.yaml` e cria dinamicamente os cards de controle para qualquer quantidade de câmeras. Ele também:
   - Monitora se as gravações locais estão ativas e se há alertas de gravação duplicada.
   - Exibe o número de visualizadores ao vivo conectados nas câmeras, identificando o IP e o navegador de cada um.
   - Executa uma thread silenciosa que detecta quando o Google Drive volta a ficar disponível, faz o upload dos vídeos acumulados na pasta `backup_gravacoes/` e os apaga localmente para economizar espaço em disco.
   - Permite ativar a inicialização automática do Windows com apenas um clique.

---

## 📂 Estrutura de Arquivos do Projeto

```
📁 camera farmacia/
├── 📁 go2rtc/
│   ├── 📄 go2rtc.exe                   # Ponte RTSP nativa go2rtc compilada para Windows
│   ├── 📄 go2rtc.yaml                  # Configurações das credenciais e IDs das câmeras
│   └── 📄 go2rtc_start.log             # Logs da ponte go2rtc
├── 📁 backup_gravacoes/                # Diretório automático para vídeos salvos localmente em contingência
├── 📄 gerenciador.pyw                  # Interface Gráfica de Controle (Pythonw / Tkinter)
├── 📄 gravador_camera.py                # Script Gravador de Baixo Consumo (Python CLI)
├── 📄 visualizador.html                # Painel de Visualização Web responsivo profissional
├── 📄 Liberar Rede Local (Executar como Admin).bat   # Script para abrir as portas de rede no Firewall
├── 📄 README.md                        # Documentação Geral do Sistema
└── 📄 .gitignore                       # Configurações de arquivos ignorados pelo Git
```

---

## 💻 Como Configurar e Rodar em Outro Computador

Como o projeto possui caminhos 100% dinâmicos e relativos, a portabilidade é imediata:

### Passo 1: Instalar o Python
Baixe e instale o Python (recomendado 3.10 ou superior) no site oficial.
> ⚠️ **IMPORTANTE**: Durante a instalação, marque a caixa **"Add Python to PATH"** (Adicionar Python às variáveis de ambiente).

### Passo 2: Clonar ou Copiar o Repositório
Copie a pasta inteira do projeto (ou clone o repositório git) para qualquer pasta do novo computador.
> *Nota: Não há necessidade de instalar bibliotecas pesadas como `opencv-python` ou `numpy` para o funcionamento padrão dos gravadores H.264/H.265 HTTP.*

### Passo 3: Configurar as Câmeras em `go2rtc.yaml`
Abra o arquivo `go2rtc/go2rtc.yaml` com o Bloco de Notas e configure as credenciais das suas câmeras Tuya/Positivo. O painel gráfico detectará os novos nomes e quantidades automaticamente.
```yaml
streams:
  camera1: "tuya://protect-us.ismartlife.me?device_id=ID_AQUI&email=SEU_EMAIL&password=SUA_SENHA"
  camera2: "tuya://protect-us.ismartlife.me?device_id=OUTRO_ID&email=SEU_EMAIL&password=SUA_SENHA"
```

### Passo 4: Executar e Ativar Inicialização Automática
1. Dê um duplo clique no arquivo `gerenciador.pyw` para abrir a interface gráfica.
2. Clique no botão **"⚙️ Habilitar Inicialização Automática com o Windows"**. 
3. Pronto! O sistema gerará o arquivo `.vbs` na pasta de Inicialização do Windows com os caminhos corretos deste PC.

---

## 🤖 Guia de Orientação para Assistentes de IA (Claude, Antigravity, Cursor, etc.)

Se você é uma Inteligência Artificial atuando neste repositório para manutenção, melhoria ou suporte, leia as regras de negócio abaixo para evitar regressões de código:

### ⚙️ Regras Arquiteturais
1. **Evitar Importar OpenCV/NumPy no Gravador**: O script `gravador_camera.py` deve permanecer ultra-leve, sem importar `cv2` ou `numpy`, realizando a captura do fluxo MP4 diretamente da API do `go2rtc` via socket/HTTP.
2. **Caminhos Dinâmicos**: Nunca escreva caminhos absolutos como `C:\Users\Thiesen\...` nos códigos. Sempre utilize `os.path.dirname(os.path.abspath(__file__))` para obter o caminho relativo à pasta do projeto.
3. **Mapeamento de Pastas do Drive**:
   - `farmacia` -> `CAMERA 1 FARMACIA`
   - `farmacia2` -> `CAMERA 2 FARMACIA`
   - Câmeras adicionais de index $i \ge 2$ -> `CAMERA {i+1} {NOME_STREAM}`
4. **Arquivo de Trava e Encerramento Gracioso**:
   - Os processos do gravador são controlados via arquivos `.lock` contendo o PID do processo.
   - O encerramento seguro e a liberação de buffers é feito removendo o arquivo `.lock` e deixando o loop de gravação fechar o arquivo MP4 naturalmente.
   - O painel (`gerenciador.pyw`) monitora os processos de forma nativa consultando a API do Windows via `ctypes` (`kernel32.OpenProcess` e `GetExitCodeProcess`) para economizar processamento e não travar o loop de Tkinter.

### 🛡️ Tratamento de Colisões
O sistema de batimento cardíaco é armazenado no Google Drive no arquivo `.active_recorder_{stream}.json`. Em caso de manutenção no sistema de detecção de duplicidade, certifique-se de que a verificação de batimento ocorra de forma assíncrona ou sem bloquear o início de gravações legítimas.