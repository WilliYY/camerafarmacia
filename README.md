# 🎥 NVR Inteligente Câmeras Farmácia — Versão 4.9

Este projeto é uma solução completa de NVR (Network Video Recorder) local e híbrida de baixíssimo consumo de hardware. Ele foi projetado para capturar, gravar, monitorar e gerenciar câmeras inteligentes compatíveis com o ecossistema Tuya, Smart Life e Positivo, com foco em segurança, portabilidade e tolerância a falhas.

---

## 🛠️ Stack Tecnológica & Dependências

- **Linguagem principal:** Python 3.x, executado via `pythonw.exe` para modo silencioso.
- **Interface gráfica:** Tkinter, com GUI customizada em tema escuro premium e responsivo.
- **Processamento de imagem:** Pillow (PIL) para renderização frame a frame.
- **Ponte RTSP:** go2rtc, serviço interno que gerencia a conexão P2P com a nuvem Tuya.
- **Processamento de mídia:** FFmpeg para validação, remuxing bruto e transcodificação sob demanda quando necessário.
- **APIs de integração:** Ctypes do Windows para controle de suspensão de energia e leitura de bateria.
- **TTS (Text-To-Speech):** PowerShell + `SAPI.SpVoice` para feedbacks de voz nativos.

---

## ⚙️ Principais Funcionalidades

### 1. Gravação com Baixo Consumo (0% CPU de Transcodificação)

O gravador consome o fluxo de vídeo direto da ponte go2rtc em formato bruto, usando cópia binária direta para arquivos `.mp4`. Não há re-encode local durante a gravação, mantendo o uso de CPU praticamente em zero.

As gravações são organizadas automaticamente em pastas diárias (`AAAA-MM-DD`) dentro do destino final de cada câmera.

### 2. Sincronização e Contingência Offline Inteligente

- **Destino primário:** Google Drive espelhado em `G:\Meu Drive\CAMERAS`.
- **Backup local automático:** se o Drive estiver offline, sem espaço ou inacessível, as gravações são desviadas para `backup_gravacoes`.
- **Sincronizador em background:** uma thread monitora a disponibilidade do Drive. Quando a conexão volta, os arquivos locais são enviados para o destino correto e removidos do HD local para poupar espaço.

### 3. Visualização ao Vivo de Baixíssima Latência (MJPEG Stream)

O painel principal exibe vídeo ao vivo por MJPEG stream persistente com Keep-Alive. O app decodifica frames buscando os marcadores JPEG de início e fim no buffer de bytes, reduzindo a latência para cerca de 50 ms em condições normais de rede.

O grid é responsivo e preserva a proporção original de 16:9:

- Com duas câmeras abertas, o layout reduz os players para caberem lado a lado.
- Com uma câmera aberta, o player expande para o maior tamanho útil disponível.
- O enquadramento é preservado, sem distorção ou corte.

### 4. Gestão de Energia e Quedas de Eletricidade

- **Prevenção de suspensão:** usa `SetThreadExecutionState` da Windows API para manter sistema e monitor ativos. O comportamento padrão do Windows é restaurado ao fechar o aplicativo.
- **Monitor de queda de energia:** usa `GetSystemPowerStatus` para detectar operação em bateria ou nobreak.
- **Desligamento seguro:** se a energia cair e a bateria chegar ao limite crítico (`<= 20%`), o sistema:
  1. Salva e encerra os gravadores de forma limpa para evitar corrupção.
  2. Emite alerta de voz nativo.
  3. Desliga o computador com segurança via comando do Windows.

### 5. Prevenção de Duplicidade de Rede & Limpeza

- **Heartbeat JSON:** a cada 30 segundos, o gravador envia batimentos cardíacos para a pasta de destino. Se outro PC tentar gravar a mesma câmera no mesmo diretório, o conflito é detectado e o segundo processo encerra a gravação automaticamente.
- **Auto-escaneamento:** a cada 3 horas, o sistema varre os diretórios e remove vídeos corrompidos ou zerados.
- **Auto-diagnóstico:** a cada 6 horas, gera um relatório de integridade em `diagnostico.txt`.
- **Logs coloridos:** o painel mostra mensagens por tipo: informação, sucesso, aviso e erro, com limpeza automática limitada a 200 linhas.
- **Feedback de voz:** avisos como "Gravando" e "Gravação parada" são disparados em segundo plano.

### 6. Atualização Automática Inteligente

O gerenciador compara a versão local com a versão publicada no GitHub e só atualiza quando a versão remota é estritamente mais nova, evitando downgrade acidental.

---

## 📂 Estrutura do Projeto

```text
camera farmacia/
├── go2rtc/
│   ├── go2rtc.exe
│   ├── go2rtc.yaml
│   ├── ffmpeg.exe
│   └── go2rtc_start.log
├── backup_gravacoes/
├── gravando_temp/
├── logs/
├── gerenciador.pyw
├── visualizador.html
├── Liberar Rede Local (Executar como Admin).bat
├── README.md
└── .gitignore
```

---

## 💻 Configuração e Execução

### 1. Instalar Python e Pillow

Instale o Python 3.10 ou superior e marque a opção **Add Python to PATH** durante a instalação.

Depois instale a dependência de imagem:

```bash
pip install Pillow
```

### 2. Configurar o go2rtc

Edite `go2rtc/go2rtc.yaml` com as credenciais e os identificadores das câmeras Tuya, Smart Life ou Positivo.

O gerenciador detecta os streams configurados e monta a interface automaticamente.

### 3. Configurar o destino das gravações

Por padrão, o destino é:

```text
G:\Meu Drive\CAMERAS
```

Esse caminho também pode ser ajustado pela interface gráfica ou pelo arquivo local `config.json`.

### 4. Executar o painel

Abra:

```text
gerenciador.pyw
```

Para rodar silenciosamente, use:

```bash
pythonw.exe gerenciador.pyw --silent
```

---

## 🤖 Diretrizes para Manutenção por IA ou Desenvolvedores

1. **Preserve a gravação direta.** A gravação deve consumir `/api/stream.mp4?src=NOME` via `urllib.request`, sem OpenCV, decode local ou re-encode contínuo.
2. **Use `127.0.0.1` para a API local.** Evite `localhost` para não depender de resolução IPv6 do Windows.
3. **Mantenha o MJPEG em thread separada.** A GUI Tkinter não deve bloquear enquanto lê frames.
4. **Feche streams recolhidos.** Ao recolher uma câmera ou fechar a janela, encerre conexões HTTP e loops de leitura.
5. **Não quebre as pastas diárias.** Gravações e sincronização devem respeitar subpastas `AAAA-MM-DD`.
6. **Proteja contra duplicidade.** O heartbeat JSON é parte crítica da segurança de gravação em rede.
7. **Evite dependências pesadas.** O projeto foi desenhado para rodar com baixo consumo em máquinas simples.
