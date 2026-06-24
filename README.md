# 🎥 Controle e Gravação de Câmeras - Farmácia (v4.8.4 NVR Unificado)

Este projeto é uma solução de NVR (Network Video Recorder) de baixíssimo consumo de hardware, projetada para capturar, gravar, monitorar e gerenciar múltiplas câmeras inteligentes (compatíveis com o ecossistema Tuya/Positivo) em um único arquivo unificado, com portabilidade total, prevenção de duplicidade na rede, visualização embutida de baixíssima latência e auto-atualização direta via GitHub.

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

3. **Visualização ao Vivo Nativa (MJPEG Stream)**:
   - O painel exibe transmissões ao vivo das câmeras diretamente no Tkinter usando widgets colapsáveis (`LiveCameraWidget`).
   - Usa **transmissão MJPEG contínua** (`/api/stream.mjpeg?src={camera}_mjpeg`) através de conexões HTTP persistentes (Keep-Alive). Uma thread dedicada por câmera varre o buffer de rede em tempo real buscando marcadores JPEG (`\xff\xd8` e `\xff\xd9`) para decodificar e desenhar frames instantaneamente com delay baixíssimo (~50ms-80ms).
   - Limita a exibição a **~15 FPS** no painel principal (para balancear suavidade e uso de CPU) e **~20 FPS** na janela maximizada de **Tela Cheia** (que mantém proporção ideal do monitor sem recortar o enquadramento).
   - Quando colapsados, os streams são encerrados imediatamente para poupar CPU, largura de banda e memória RAM.

4. **Escaneamento de Corrompidos e Diagnóstico Automáticos**:
   - **Escaneamento (a cada 3 horas)**: Varre a pasta local e de nuvem recursivamente procurando arquivos de vídeo que estejam corrompidos ou com 0 bytes, deletando-os automaticamente e registrando a ação no log de eventos.
   - **Diagnóstico (a cada 6 horas)**: Gera de forma automática um relatório completo de saúde do sistema (`diagnostico.txt`), checando integridade de arquivos, processos ativos, conectividade externa do Google Drive e DNS, sem interromper o usuário.

5. **Interface Premium e Layout Inteligente**:
   - **Visual Premium**: Design refinado com barras superiores em cor accent azul (`#3B82F6`) nos cards de controle, tipografia maior, bordas e divisores geométricos dinâmicos.
   - **Dimensionamento Dinâmico**: A janela é fixada em **1280x830** com a coluna de controle expandida para **430px** para evitar cortes de palavras. As câmeras calculam seu tamanho dinamicamente: se ambas estiverem abertas, ajustam-se lado a lado para **560x315** cada. Se apenas uma estiver aberta, assume largura máxima de **~800px** com proporção 16:9 travada para mostrar o sinal da câmera por inteiro e sem cortes.
   - **Logs Coloridos**: Histórico de eventos de 6 linhas com cores dinâmicas (Vermelho = Erro, Verde = Sucesso/Ativo, Azul = Info, Amarelo = Avisos), rolagem automática e auto-cleanup para no máximo 200 linhas.

6. **Atualização Automática Inteligente**:
   - Compara a versão do GitHub contra a versão local e realiza o download apenas se a versão na nuvem for estritamente mais nova (prevenindo downgrades).

---b contra a versão local e realiza o download apenas se a versão na nuvem for estritamente mais nova (prevenindo downgrades).

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
   
2. **Visualização Embutida no Tkinter (MJPEG Real-Time Stream)**:
   - A visualização ao vivo no Tkinter é feita via `LiveCameraWidget` que abre a rota `/api/stream.mjpeg?src={camera}_mjpeg` e consome a conexão contínua via socket HTTP.
   - O código lê pedaços de dados em bytes, localizando os marcadores de SOI (`\xff\xd8`) e EOI (`\xff\xd9`) para remontar as imagens JPEG e exibí-las via Pillow (`Image` e `ImageTk`).
   - A leitura e reconstrução devem rodar em threads separadas para não congelar a GUI do Tkinter.
   - O loop de streaming **DEVE** parar imediatamente (fechando a conexão HTTP e o loop) se a câmera for recolhida (`collapse()`) ou se a janela for fechada (`graceful_shutdown()`), garantindo que não haja vazamentos de socket ou uso de rede desnecessário.
   
3. **Caminhos de Gravação Diários**:
   - As gravações devem ser salvas em subpastas de data (`YYYY-MM-DD`) dentro do diretório de destino.
   - Use o helper `extrair_data_do_arquivo(filename)` para extrair a data a partir dos arquivos salvos e direcioná-los corretamente nas rotinas de sincronização do `background_sync_loop`.
   
4. **Resolução de Loopback**:
   - Todas as requisições para a API do `go2rtc` devem usar o IP literal `127.0.0.1` em vez do host string `localhost` para evitar falhas silenciosas de resolução IPv6 do Windows.
   
5. **Comunicação Interativa**:
   - Sempre forneça feedback visual imediato para ações da GUI. Use a função `flash_button` para piscar botões de sucesso ou altere o estado para `disabled` com texto descritivo durante processamentos longos.