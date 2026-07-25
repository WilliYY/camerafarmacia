# Auditoria Tecnica do NVR - 2026-07-24

## Atualizacao de remediacao - 2026-07-25

Os achados de software de prioridade alta e media desta auditoria foram
tratados incrementalmente nos commits:

- `f85d087`: diagnostico `Kernel_144`, reserva local dinamica, scanner
  conservador, retencao desacoplada e mapa persistente de pastas.
- `f99e6e8`: serializacao do ciclo de vida, ownership de processos, singleton,
  encerramento e copias atomicas retomaveis.
- `77241b6`: allowlist de rotas/modulos, downloads limitados, visualizador
  local com CSP e telemetria de disco corretamente identificada como basica.
- `d887f97`: modulo `mpegts` correto e prova de disponibilidade da rota
  `/api/stream.ts` antes de iniciar os gravadores.
- `951e301`: temporario no HD validado, publicacao sem copia no mesmo volume,
  recuperacao limitada de `.recording`, fallback local somente quando
  necessario e monitor de smoke test.

Validacao executada:

- `59/59` testes unitarios aprovados.
- Compilacao Python e `git diff --check` aprovados.
- Rotas reais das duas cameras entregaram MPEG-TS.
- Ensaio real controlado de 30 segundos aprovado para duas cameras.
- Pico do processo: `180,6 MB`, `43` threads e `1,4%` de CPU.
- Dois videos novos de `851.968 bytes`, ambos aceitos pelo FFmpeg.
- `0` novos `Kernel_144`, `0` artefatos e `0` processos residuais.
- HD principal com `847,05 GB` livres e status basico do Windows `OK`.

O software nao faz mais a escrita continua primeiro no SSD do Windows quando o
HD validado esta disponivel. O `C:` estava com cerca de `22 GB` livres, abaixo
da reserva dinamica de `23,74 GB`; isso continua sendo alertado, mas nao impede
a gravacao no HD principal. Nenhum arquivo foi apagado para liberar espaco.

Permanecem abertos:

- diagnostico fisico do historico USBXHCI/cabo/porta/energia;
- liberacao segura de espaco no `C:` fora das pastas de gravacao;
- teste supervisionado prolongado;
- testes reais de desconexao do HD e queda de energia;
- telemetria SMART detalhada com ferramenta do fabricante.

As regras de firewall nao foram alteradas, conforme decisao operacional do
responsavel.

## Resumo executivo original (estado em 2026-07-24)

As secoes abaixo preservam a fotografia e as evidencias da auditoria original.
Para o estado atual, prevalece a atualizacao de remediacao acima.

O projeto possui boas protecoes contra perda direta de gravacoes, mas ainda nao
deve ser classificado como comprovadamente seguro para operacao 24h nesta
maquina.

O risco imediato mais importante esta no computador: existem cinco dumps
USBXHCI distintos entre 2026-07-03 e 2026-07-18 e tres reinicializacoes sem
desligamento limpo em 2026-07-22 e 2026-07-23. O Windows nao registrou erros
criticos de `disk`, `Ntfs`, `storahci`, `stornvme`, `volmgr` ou `WHEA` nos
ultimos sete dias. Isso aponta para instabilidade real, mas nao prova que o NVR
seja a causa. A carga continua do NVR pode apenas expor uma fragilidade de USB,
driver, cabo, energia ou controlador.

Tambem foi encontrado um erro na inteligencia: ela usa a data de modificacao
das pastas do Windows Error Reporting e, por isso, contou reprocessamentos de
dumps antigos como falhas recentes. Nos sete dias analisados houve 306 eventos
WER, mas eles referenciam repetidamente apenas cinco dumps USBXHCI reais; o mais
novo foi criado em 2026-07-18 21:29.

Resultado da revisao:

- 1 risco operacional critico na maquina.
- 8 fragilidades de prioridade alta no software.
- 9 melhorias de prioridade media.
- Compilacao Python aprovada.
- 39 testes aprovados quando os temporarios sao colocados no disco `D:`.
- Nenhum teste real de camera foi iniciado, pois os eventos de hardware ainda
  nao permitem autorizar um ensaio continuo com seguranca.

## Escopo

Foram analisados:

- `gerenciador.pyw` completo: 6.247 linhas, 188 funcoes e 5 classes.
- `CameraManagerApp`: 4.414 linhas e 125 metodos.
- `sistema/visualizador.html`: 671 linhas.
- `tests/test_storage_safety.py`: 832 linhas e 39 testes.
- Inicializacao, gravacao, sincronizacao, retencao, scanner, encerramento,
  atualizacao, logs, energia, go2rtc, FFmpeg e rotas.
- Estado atual dos discos, processos, portas e eventos do Windows.

## Achados criticos

### HW-01 - Instabilidade real do Windows/USB ainda nao resolvida

- Severidade: Critica operacional.
- Local: maquina Windows; fora do repositorio.
- Evidencia:
  - Dumps USBXHCI distintos: `20260703-2208`, `20260710-1633`,
    `20260711-2106`, `20260716-1820` e `20260718-2129`.
  - `Kernel-Power 41`: `2026-07-22 18:32:04`,
    `2026-07-23 15:07:02` e `2026-07-23 18:34:15`.
  - Os dois primeiros desligamentos tinham `BugcheckCode=0` e
    `PowerButtonTimestamp=0`; o ultimo tinha `PowerButtonTimestamp` preenchido.
- Impacto: travamento completo, desligamento forcado, interrupcao de escrita e
  necessidade de recuperar temporarios. Repeticao de desligamentos bruscos pode
  corromper sistema de arquivos ou videos em andamento.
- Correlacao: o NVR faz I/O USB continuo e pode revelar o defeito, mas os dados
  nao provam que Python, go2rtc ou FFmpeg o causem.
- Acao: antes do teste de 24h, revisar cabo, porta USB, alimentacao do HD,
  driver/chipset USBXHCI e firmware. Testar o HD em outra porta/controlador.
- Mitigacao: manter scanner e manutencao pesada adiados; preservar a gravacao
  direta e os temporarios.
- Nota: o volume `D:` esta com 919,4 GB livres, 98,7% de espaco e nao esta
  marcado como sujo.

## Achados altos

### DIAG-01 - Falhas USB antigas sao apresentadas como recentes

- Severidade: Alta.
- Local: `gerenciador.pyw:3089-3125`.
- Evidencia: `scan_recent_kernel_144_reports()` usa `entry.stat().st_mtime` das
  pastas `ReportArchive` e `ReportQueue`.
- Impacto: o Windows altera essas pastas ao tentar reenviar um relatorio. O
  painel informou seis falhas nas ultimas 24h e ultimo evento em
  `2026-07-24 14:22:08`, embora o dump mais novo seja de
  `2026-07-18 21:29`.
- Correcao: identificar e deduplicar o caminho do dump USBXHCI e usar a data
  real do dump ou do evento de criacao, nao o `mtime` da fila WER.
- Mitigacao: exibir separadamente `dumps unicos`, `reprocessamentos WER` e
  `falhas novas desde a ultima execucao`.
- Falso positivo: a falha USB historica e real; somente sua contagem e
  atualidade estao erradas.

### PROC-01 - Corridas podem iniciar duas pontes e matar FFmpeg ativo

- Severidade: Alta.
- Local: `gerenciador.pyw:1793-1807`, `1952-1963`, `2849-2853`,
  `3841-3891` e `4949-4960`.
- Evidencia:
  - A limpeza de FFmpeg inicia em thread assincrona.
  - O monitor inicia imediatamente depois.
  - No modo silencioso, `run_start_sequence()` inicia em outra thread.
  - Monitor e sequencia de inicio podem chamar `iniciar_go2rtc()` sem lock.
  - A limpeza encerra todos os `ffmpeg.exe` cujo caminho combine com
    `sistema\go2rtc`.
- Impacto: duas instancias de go2rtc, porta ocupada, reconexoes, preview preto e
  interrupcao de consumidores FFmpeg validos.
- Correcao: criar um unico controlador de ciclo de vida com lock; finalizar a
  limpeza antes de iniciar o monitor; nunca permitir que visualizadores ou
  sequencias paralelas controlem o processo.
- Mitigacao: registrar PID, PID pai e transicao de estado de cada ponte.

### PROC-02 - Encerramento pode matar outro processo Python

- Severidade: Alta.
- Local: `gerenciador.pyw:3893-3917` e `5005-5076`.
- Evidencia: um PID lido de arquivo `.lock` e considerado valido quando o nome
  do executavel contem `python`; depois `os.kill(pid, 9)` e executado.
- Impacto: um lock antigo e um PID reutilizado pelo Windows podem encerrar outro
  programa Python sem relacao com o NVR.
- Correcao: validar caminho completo do script, linha de comando, instante de
  criacao e um token aleatorio gravado no lock. Preferir nao matar processos
  legados automaticamente.
- Mitigacao: preservar lock suspeito e registrar alerta para revisao manual.

### DISK-01 - Fallback local permite reduzir o disco C: a apenas 5 GB

- Severidade: Alta.
- Local: `gerenciador.pyw:897`, `1152-1167` e `4310-4345`.
- Evidencia: `LOCAL_STORAGE_RESERVE_BYTES = 5 * 1024 * 1024 * 1024`.
- Impacto: se o HD externo cair, o backup local pode consumir o disco do
  Windows ate restarem somente 5 GB. Isso pode causar lentidao extrema,
  falhas de atualizacao, falta de memoria virtual e travamento do PC.
- Estado atual: `C:` possui 29,0 GB livres, 12,2%.
- Correcao: reserva configuravel, usando o maior valor entre 20 GB e 10% do
  volume; alertas antecipados em 20% e 15%.
- Mitigacao: limitar a taxa e o volume do fallback sem apagar videos pendentes.

### API-01 - go2rtc publica mais modulos e rotas que o NVR usa

- Severidade: Alta de seguranca.
- Local: `gerenciador.pyw:164-190`.
- Evidencia: existem usuario, senha e `exec.allow_paths`, mas nao existem
  `modules` nem `api.allow_paths`.
- Impacto: um cliente autenticado recebe a superficie completa da WebUI/API do
  go2rtc, incluindo funcoes que o NVR nao necessita. Isso aumenta o impacto de
  senha vazada ou vulnerabilidade futura.
- Correcao: permitir somente os modulos e caminhos realmente usados:
  streams, RTSP, WebRTC, MJPEG, FFmpeg e as rotas de visualizacao/probe.
- Mitigacao: manter senha aleatoria, `exec.allow_paths` restrito ao FFmpeg e nao
  ampliar firewall.
- Nota: `go2rtc 1.9.14` e a versao oficial mais recente em 2026-07-24. As
  vulnerabilidades publicas antigas analisadas afetavam a versao 1.7.1.

### LOCK-01 - Heartbeat de gravacao compartilhada nao e atomico

- Severidade: Alta.
- Local: `gerenciador.pyw:4604-4657`.
- Evidencia: `.active_recorder.json` e sobrescrito diretamente com `open(...,
  "w")`; erro de leitura JSON e tratado como ausencia de conflito.
- Impacto: dois computadores podem ler arquivo parcial ou iniciar ao mesmo
  tempo, concluir que nao existe outro gravador e escrever no mesmo destino.
- Correcao: publicar heartbeat por temporario + `fsync` + `os.replace`, incluir
  token de proprietario e confirmar a posse depois da escrita.
- Mitigacao: em JSON invalido ou erro de leitura, falhar fechado e pausar a
  gravacao naquele destino.

### DATA-01 - Scanner de integridade executa exclusao por retencao

- Severidade: Alta de integridade.
- Local: `gerenciador.pyw:5176-5183`, `5381-5382` e `5406-5432`.
- Evidencia: ao terminar o scanner, o codigo sempre chama
  `rotacionar_videos_hd()`, que usa `shutil.rmtree()` nas pastas antigas.
- Impacto: o comando que aparenta somente verificar videos tambem pode apagar
  dias inteiros de gravacao. A acao e irreversivel e fica acoplada ao scanner.
- Correcao: separar scanner e retencao. Rotacao deve ter tarefa propria,
  manifesto, contagem previa e politica explicitamente aprovada.
- Mitigacao: nunca executar `rmtree` durante uma verificacao de integridade.

### LIFE-01 - Protecao de instancia unica e vulneravel a corrida

- Severidade: Alta.
- Local: `gerenciador.pyw:6170-6179`.
- Evidencia: o singleton usa TCP com `SO_REUSEADDR`; duas inicializacoes
  simultaneas podem atravessar a janela de corrida.
- Impacto: gravacao duplicada, duas pontes e concorrencia sobre logs, heartbeat
  e temporarios.
- Correcao: usar mutex nomeado do Windows ou `SO_EXCLUSIVEADDRUSE`, mantendo o
  canal local apenas para `SHOW` e `STOP_SAFE`.
- Mitigacao: confirmar um unico PID antes de iniciar go2rtc e gravadores.

## Achados medios

### MON-01 - Indicador chamado SMART nao consulta telemetria SMART real

- Severidade: Media.
- Local: `gerenciador.pyw:1915-1950`.
- Evidencia: o comando consulta `Win32_DiskDrive` e apenas o campo generico
  `Status`.
- Impacto: o painel pode mostrar `SMART OK` sem conhecer temperatura, setores
  pendentes, erros de leitura/escrita, desgaste ou horas ligadas.
- Correcao: renomear para `status basico do Windows` ou integrar leitura SMART
  real com estado `indisponivel` quando o USB nao expuser telemetria.
- Estado atual: a consulta basica diz `OK`; contadores de confiabilidade nao
  ficaram disponiveis nesta maquina.

### IO-01 - Copia atomica remove o temporario quando ocorre erro

- Severidade: Media.
- Local: `gerenciador.pyw:4347-4391`.
- Evidencia: o bloco de excecao remove `tmp_dst`.
- Impacto: a origem e preservada, mas uma queda no fim da copia obriga regravar
  tudo, aumentando I/O e eliminando evidencia util para recuperacao.
- Correcao: preservar temporario incompleto com metadados e reutiliza-lo ou
  limpa-lo somente apos decisao segura.
- Mitigacao: nunca remover a origem enquanto destino e hash nao forem
  confirmados.

### UI-01 - Visualizador possui caminho de DOM XSS

- Severidade: Media de seguranca.
- Local: `sistema/visualizador.html:474`, `540-548`, `570` e `621-633`.
- Evidencia:
  - IP vem de `localStorage` e nao possui validacao por allowlist.
  - IP e nome do stream entram em HTML montado por `innerHTML`.
  - Existem handlers inline e nao ha CSP.
- Impacto: valor malicioso em storage ou nome de stream nao confiavel pode
  injetar HTML/JavaScript no painel.
- Correcao: validar IPv4/IPv6/hostname, criar elementos com DOM APIs,
  `textContent`, `addEventListener` e URLs construidas por `URL`.
- Mitigacao: CSP por cabecalho e remocao de scripts/handlers inline.

### TEST-01 - Teste de saude depende do espaco real do volume temporario

- Severidade: Media de qualidade.
- Local: `tests/test_storage_safety.py:712-749`.
- Evidencia: no `C:` com 29 GB livres, o teste recebe `HD_SPACE_LOW` e falha
  esperando `healthy`; no `D:` os 39 testes passam.
- Impacto: a mesma revisao pode ficar verde ou vermelha conforme o computador,
  escondendo regressao real em ruido ambiental.
- Correcao: simular `shutil.disk_usage()` no teste.
- Mitigacao: executar testes de armazenamento somente em diretorios temporarios
  e com capacidades totalmente controladas.

### ARCH-01 - Arquivo monolitico e excesso de excecoes genericas

- Severidade: Media.
- Local: `gerenciador.pyw` completo.
- Evidencia: 6.247 linhas, classe principal com 4.414 linhas e 200 handlers
  `except Exception` ou `except:`. Funcoes criticas chegam a 313 linhas.
- Impacto: falhas podem ser silenciadas, estados compartilhados ficam dificeis
  de raciocinar e alteracoes pequenas atingem varias responsabilidades.
- Correcao gradual: separar armazenamento, ciclo de vida, diagnostico, rede e
  atualizacao, mantendo testes de contrato antes de cada extracao.
- Mitigacao: substituir `pass` por log limitado e codigo de erro nos caminhos
  que alteram gravacao, disco ou processos.

### INSTALL-01 - Downloads de dependencias nao possuem limite de tamanho

- Severidade: Media de disponibilidade.
- Local: `gerenciador.pyw:271-348` e `396-434`.
- Evidencia: os ZIPs sao lidos ate EOF; o fluxo silencioso usa `conn.read()` sem
  limite. O hash so e validado depois.
- Impacto: servidor comprometido, proxy defeituoso ou resposta anormal pode
  consumir memoria e espaco do disco do Windows.
- Correcao: limite de bytes, validacao de `Content-Length`, escrita em chunks e
  reserva minima antes do download.
- Controle existente: executaveis so sao aceitos se o SHA-256 corresponder ao
  valor confiavel.

### POWER-01 - Modo silencioso mantem a tela ligada

- Severidade: Media operacional.
- Local: `gerenciador.pyw:1744-1747` e `3736-3752`.
- Evidencia: `ES_DISPLAY_REQUIRED` e aplicado mesmo com a janela oculta.
- Impacto: gasto de energia e desgaste desnecessario do monitor em operacao
  24h.
- Correcao: no modo silencioso usar apenas `ES_SYSTEM_REQUIRED`.

### MAP-01 - Pastas das cameras dependem da ordem dos streams

- Severidade: Media de integridade.
- Local: `gerenciador.pyw:2036-2044`.
- Evidencia: indice 0 grava em `camera 1`, indice 1 em `camera 2`.
- Impacto: reordenar ou renomear streams pode misturar identidades e historico
  das cameras.
- Correcao: mapa persistente `stream -> pasta`, validado e imutavel por padrao.

### SCAN-01 - Scanner verifica apenas o primeiro segundo do video

- Severidade: Media.
- Local: `gerenciador.pyw:5311-5357`.
- Evidencia: FFmpeg recebe `-t 1`; qualquer texto em `stderr` tambem classifica
  a rodada como falha. Duas falhas levam a quarentena.
- Impacto: corrupcao apos o primeiro segundo nao e detectada; avisos benignos do
  FFmpeg podem gerar falso positivo.
- Correcao: amostragem no inicio, meio e fim; classificar por retorno e erros
  conhecidos, mantendo timeout como inconclusivo.

## Controles que estao corretos

- Gravacao usa `/api/stream.ts?src=NOME` sem recodificacao continua.
- Nomes e URLs de streams sao validados e serializados com JSON no YAML.
- Credencial web e aleatoria e exige senha longa.
- `exec.allow_paths` permite somente o FFmpeg configurado.
- `go2rtc.yaml`, `config.json`, pasta web gerada e segredos locais nao entram no
  Git.
- Binarios go2rtc e FFmpeg sao verificados por SHA-256.
- Atualizacao valida versao, sintaxe, marcadores e hashes aprovados.
- Copia para destino compara conteudo, evita sobrescrita conflitante e publica
  com `os.replace`.
- Gravacao preserva temporario quando a publicacao final falha.
- Fallback local nao apaga backups pendentes.
- Limpeza emergencial fica desativada por padrao e respeita retencao.
- Scanner e serial, incremental, limitado e exige duas falhas antes da
  quarentena.
- Quarentena permanece no mesmo disco da origem.
- Logs em memoria e filas possuem limites.
- Encerramento para novas gravacoes, fecha conexoes e aguarda threads antes de
  parar go2rtc.

## Rotas utilizadas pelo NVR

| Rota | Uso | Exposicao atual |
|---|---|---|
| `/api/streams` | saude e descoberta de streams | API go2rtc |
| `/api/stream.ts?src=NOME` | gravacao bruta | API go2rtc |
| `/api/stream.mjpeg?src=NOME_mjpeg` | preview Tkinter | API go2rtc |
| `/video.html?src=NOME` | visualizador web | WebUI go2rtc |
| `/visualizador.html` | painel web local | pasta estatica publicada |
| `127.0.0.1:29999` | `SHOW` e `STOP_SAFE` | somente loopback |

As portas `1984` e `8554` escutam em todas as interfaces. A autenticacao de
rede esta configurada; o acesso loopback e deliberadamente liberado para os
clientes internos do Python. A politica de firewall foi mantida fora do escopo,
conforme decisao operacional existente.

## Validacoes executadas

- `python -m py_compile gerenciador.pyw`: aprovado.
- `python -m unittest tests.test_storage_safety`: 39 testes aprovados usando
  temporarios isolados no `D:`.
- `--health-check`: executado sem iniciar cameras.
- Processos e portas: nenhuma instancia operacional do NVR estava ativa durante
  a auditoria.
- `go2rtc`: versao `1.9.14 (b5948cf)`.
- `FFmpeg`: versao `8.1.2`.
- `D:`: 919,4 GB livres, 98,7%, volume nao sujo.
- `C:`: 29,0 GB livres, 12,2%.
- Eventos de armazenamento: nenhum erro/aviso critico de disco, NTFS, SATA,
  NVMe, volume ou WHEA nos ultimos sete dias.

## O que ainda nao foi validado

- Gravacao real controlada de 180 segundos.
- Operacao continua de 24 horas.
- Desconexao e reconexao do HD durante gravacao.
- Queda real de energia/nobreak.
- Temperatura e telemetria SMART detalhada.
- Rotas do go2rtc testadas em execucao com allowlist reduzida.
- Reproducao manual de blocos gravados em horarios diferentes.

O ensaio real nao deve comecar antes de corrigir `DIAG-01`, revisar `HW-01` e
confirmar que nao surgiram novos dumps USBXHCI reais.

## Ordem recomendada

1. Corrigir a contagem USBXHCI e diferenciar dumps reais de reprocessamento WER.
2. Eliminar corridas de go2rtc/FFmpeg e fortalecer a instancia unica.
3. Aumentar a reserva do disco local.
4. Remover o encerramento por PID sem prova forte de propriedade.
5. Limitar modulos e rotas da API go2rtc.
6. Tornar heartbeat atomico e falhar fechado.
7. Separar scanner de retencao.
8. Corrigir teste dependente de espaco e executar ensaio real de 180 segundos.
9. Somente depois executar teste supervisionado de 24 horas.

## Referencias externas

- go2rtc, configuracao de seguranca e allowlists:
  https://github.com/AlexxIT/go2rtc
- go2rtc v1.9.14, versao oficial mais recente:
  https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14
- Microsoft, interpretacao do Kernel-Power Event ID 41:
  https://learn.microsoft.com/pt-br/troubleshoot/windows-client/performance/event-id-41-restart
- GitHub Security Lab, vulnerabilidades antigas do go2rtc v1.7.1:
  https://securitylab.github.com/advisories/GHSL-2023-205_GHSL-2023-207_go2rtc/
