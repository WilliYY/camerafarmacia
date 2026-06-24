# 🎥 Controle e Gravação de Câmeras - Farmácia (v4.5 NVR Unificado)

Este projeto é uma solução de NVR (Network Video Recorder) de baixíssimo consumo de hardware, projetada para capturar, gravar, monitorar e gerenciar múltiplas câmeras inteligentes (compatíveis com o ecossistema Tuya/Positivo) em um único arquivo unificado, com portabilidade total, prevenção de duplicidade na rede, visualização embutida no painel e auto-atualização direta via GitHub.

---

## 🛠️ Como Funciona o Sistema

O sistema é 100% integrado em um único arquivo gerenciador (`gerenciador.pyw`) e uma ponte RTSP:

1. **Ponte RTSP (`go2rtc.exe`)**:
   Estabelece conexões seguras e autenticadas com os servidores Tuya Cloud e expõe as transmissões de vídeo das câmeras na rede local (nos formatos RTSP na porta `8554` e API Web na porta `1984`).
   
2. **NVR Unificado integrado no Gerenciador (`gerenciador.pyw`)**:
   - **0% Transcoding (Cópia Direta)**: O gerenciador se conecta à API HTTP do `go2rtc` (`/api/stream.mp4?src=NOME`) e baixa diretamente o fluxo binário H.264/H.265 do vídeo, salvando-o no disco. O uso de CPU é de **0%**, e os arquivos finais ocupam o mínimo de espaço na qualidade original.
   - **Modo Headless/Silencioso**: Suporta a inicialização em segundo plano via linha de comando (`gerenciador.pyw --silent`) usada pelo script de inicialização do Windows (`.vbs`), rodando silenciosamente sem abrir janelas.
   - **Organização por Pasta Diária**: Os vídeos são salvos dentro de subpastas nomeadas com a data atual (formato `YYYY-MM-DD`) dentro da pasta da câmera no Drive ou no PC Local (ex: `CAMERA 1 FARMACIA\2026-06-24\camera_2026-06-24_11-00_ate_11-30.mp4`), melhorando o controle visual e limpeza do Drive.
   - **Contingência de Sincronização**: Se a pasta do Google Drive (`G:\Meu Drive\CAMERAS`) estiver offline, sem espaço ou sem permissão de escrita, o NVR desvia a gravação automaticamente para a pasta local `backup_gravacoes/{nome_camera}/{data_dia}/`.
   - **Sincronizador Automático Inteligente**: Uma thread dedicada monitora o Google Drive e, assim que a conexão é restabelecida, realiza o upload em segundo plano dos vídeos da pasta `backup_gravacoes/` para suas respectivas pastas de dia no Google Drive, deletando a cópia local para não encher o HD.
   - **Detecção de Conflitos na Rede**: Envia batimentos cardíacos (heartbeat) a cada 30 segundos para o Google Drive em formato JSON (`.active_recorder_{stream}.json`). Se outra máquina tentar gravar a mesma câmera no mesmo diretório, o conflito é detectado e o segundo gravador cessa a gravação imediatamente para evitar corrupção e duplicidade.
   - **Monitor de Visualizadores**: Consulta a API do `go2rtc` e exibe em tempo real o IP e o navegador das pessoas que estão assistindo à live das câmeras no painel Web.

3. **Visualização ao Vivo Embutida (Pillow Preview)**:
   - O painel exibe streams ao vivo das câmeras de forma direta no Tkinter usando widgets colapsáveis (`LiveCameraWidget`).
   - Ele solicita imagens estáticas à API do go2rtc (`/api/frame.jpeg?src={camera}`) a cada ~140ms (7 FPS) em uma thread secundária por câmera para exibir o vídeo sem travar a interface e sem sobrecarregar a CPU.
   - Quando colapsadas, as streams são encerradas para poupar CPU, rede e memória RAM.
   - Possui botão **Tela Cheia** que abre uma janela maximizada exibindo a stream com redimensionamento automático de proporção de aspecto (aspect ratio) a 10 FPS.

4. **Escaneamento Periódico de Corrompidos (a cada 3 horas)**:
   - Varre a pasta local e de nuvem de forma recursiva (incluindo subpastas de data) procurando arquivos de vídeo que estejam corrompidos ou com 0 bytes.
   - Os arquivos corrompidos são excluídos permanentemente e um aviso de log é exibido no painel de eventos.
   - O escaneamento é acionado automaticamente a cada 3 horas em segundo plano (sem exibir caixas de mensagem popups para não incomodar o usuário) e também pode ser executado manualmente a qualquer momento pelo botão na GUI.

5. **Interface 100% Interativa e Responsiva**:
   - Cada botão possui um sistema de feedback visual (`flash_button` e estados de transição).
   - Ao clicar em botões demorados (como *Gerar Diagnóstico* ou *Escanear Corrompidos*), o botão é desabilitado e exibe uma animação/texto de carregamento (`⏳ Gerando...`, `⏳ Escaneando...`).
   - Ao salvar ou executar ações com sucesso, o botão pisca temporariamente em verde com um checkmark (`✔️ Salvo!`, `✔️ Pasta Aberta!`), fornecendo confirmação visual em tempo real.

6. **Atualização Automática Inteligente**:
   - Compara a versão do GitHub contra a versão local e realiza o download apenas se a versão na nuvem for estritamente mais nova (prevenindo downgrades).

---

## 📂 Estrutura de Arquivos do Projeto

```
📁 camera farmacia/
├── 📁 go2rtc/
│   ├── 📄 go2rtc.exe                   # Ponte RTSP nativa go2rtc compilada para Windows
│   ├── 📄 go2rtc.yaml                  # Configurações de credenciais e IDs das câmeras
│   └── 📄 go2rtc_start.log             # Logs da ponte go2rtc
├── 📁 backup_gravacoes/                # Diretório automático de contingência (organizado por CAMERA/DATA/)
├── 📁 gravando_temp/                   # Pasta de vídeos temporários ativos durante gravação
├── 📄 gerenciador.pyw                  # Interface Gráfica de Controle, NVR & Visualizador Embutido (Pythonw)
├── 📄 visualizador.html                # Painel de Visualização Web responsivo profissional
├── 📄 Liberar Rede Local (Executar como Admin).bat   # Script para abrir as portas de rede no Firewall
├── 📄 README.md                        # Documentação Geral do Sistema (Este Arquivo)
└── 📄 .gitignore                       # Configurações de arquivos ignorados pelo Git
```

---

## 💻 Como Configurar e Rodar em Outro Computador

Como o projeto possui caminhos 100% dinâmicos e relativos, a portabilidade é imediata:

### Passo 1: Instalar o Python e Pillow
Baixe e instale o Python (recomendado 3.10 ou superior) no site oficial.
> ⚠️ **IMPORTANTE**: Durante a instalação, marque a caixa **"Add Python to PATH"**.
Instale a dependência de imagens executando no prompt:
```bash
pip install Pillow
```

### Passo 2: Clonar ou Copiar o Repositório
Copie a pasta inteira do projeto para qualquer diretório do novo computador.

### Passo 3: Configurar as Câmeras em `go2rtc.yaml`
Abra o arquivo `go2rtc/go2rtc.yaml` e configure as credenciais das suas câmeras Tuya/Positivo. O painel gráfico detectará os novos nomes e quantidades automaticamente.

### Passo 4: Executar e Ativar Inicialização Automática
1. Dê um duplo clique no arquivo `gerenciador.pyw` para abrir a interface gráfica.
2. O gerenciador irá buscar por atualizações no GitHub e se atualizar de forma inteligente.
3. Use o painel para monitorar as câmeras e clique em **"⚙️ Habilitar Inicialização Automática com o Windows"** para iniciar de forma oculta a cada boot.

---

## 🤖 Guia de Orientação para Assistentes de IA (Claude, Antigravity, Cursor, etc.)

Se você é uma Inteligência Artificial atuando neste repositório para manutenção, melhoria ou suporte, leia as regras de negócio abaixo para evitar regressões de código:

### ⚙️ Diretrizes Arquiteturais Críticas

1. **Gravação Direta Sem Decodificação (0% CPU)**:
   - A gravação do vídeo das câmeras deve usar estritamente `urllib.request` para baixar o stream MP4/H.264 bruto do `go2rtc`.
   - **NUNCA** decodifique os frames gravados no disco usando OpenCV, FFmpeg ou similares, pois isso exige poder de processamento desnecessário, quebrando o princípio de baixíssimo consumo de hardware do NVR.
   
2. **Visualização Embutida no Tkinter (Pillow Preview)**:
   - A visualização ao vivo no Tkinter é feita via `LiveCameraWidget` que lê `/api/frame.jpeg?src={camera}` de forma cíclica e renderiza em um `Label` através do Pillow (`Image` e `ImageTk`).
   - A requisição de imagem deve rodar em uma thread separada para não congelar a GUI do Tkinter.
   - O loop de frame-fetching **DEVE** parar imediatamente se a câmera for recolhida (`collapse()`) ou se a janela for fechada (`graceful_shutdown()`) para liberar as sockets e evitar vazamento de memória e processamento fantasma.
   
3. **Caminhos de Gravação Diários**:
   - As gravações devem ser salvas em subpastas de data (`YYYY-MM-DD`) dentro do diretório de destino.
   - Use o helper `extrair_data_do_arquivo(filename)` para extrair a data a partir dos arquivos salvos e direcioná-los corretamente nas rotinas de sincronização do `background_sync_loop`.
   
4. **Resolução de Loopback**:
   - Todas as requisições para a API do `go2rtc` devem usar o IP literal `127.0.0.1` em vez do host string `localhost` para evitar falhas silenciosas de resolução IPv6 do Windows.
   
5. **Comunicação Interativa**:
   - Sempre forneça feedback visual imediato para ações da GUI. Use a função `flash_button` para piscar botões de sucesso ou altere o estado para `disabled` com texto descritivo durante processamentos longos.