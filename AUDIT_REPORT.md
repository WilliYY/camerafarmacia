# Parecer de Estabilidade, Hardware e Seguranca

Data da auditoria: 2026-07-17

## Resumo executivo

O NVR concluiu ensaios reais com as duas cameras, publicou os blocos no HD e
encerrou sem deixar processos, travas ou temporarios. Durante os ensaios nao
houve novo `Kernel_144`, aumento anormal de memoria nem desconexao do volume
`FARMACIA`.

Nao e correto declarar o sistema 100% livre de falhas com ensaios curtos. O
principal risco de travamento do computador continua fora do Python: o Windows
possui cinco relatorios recentes `Kernel_144`, historicamente associados a
USBXHCI. O teste reduz a suspeita sobre o NVR, mas nao absolve cabo, porta,
controlador, energia ou driver sem um ensaio prolongado.

O fluxo de gravacao e resiliente. Nesta revisao, o go2rtc passou a publicar
somente o visualizador em uma pasta isolada, com autenticacao para clientes da
rede, rotas restritas e RTSP protegido. O firewall existente permanece aberto
na rede interna por decisao do responsavel; nao encaminhar essas portas no
roteador nem usar perfil de rede publica.

## Achados por prioridade

### Critico operacional

**HW-01 - Instabilidade USB anterior ainda nao resolvida**

Impacto: uma falha de controlador ou dispositivo USB pode congelar acesso ao
HD e ao Windows, mesmo que o NVR esteja correto. A linha de base e a linha final
mantiveram cinco relatorios; nenhum novo apareceu nos ensaios. Manter o teste de
24 horas bloqueado enquanto o total estiver aumentando.

### Critico de seguranca

**SEC-00 - Mitigado no servico; firewall interno mantido por decisao operacional**

O YAML gerado agora usa `static_dir: "../web"`, fora da pasta de configuracao,
e adiciona usuario/senha para a API e RTSP. O arquivo local deixa de ser
controlado pelo Git. Assim, `config.json` e
`go2rtc.yaml` nao devem mais estar disponiveis pela rota web; a visualizacao,
API e RTSP exigem autenticacao para clientes da LAN.

Risco residual aceito: as regras de firewall para `1984`, `8554` e `8555`
continuam como estavam, inclusive com perfis amplos, porque o usuario pediu que
nao fossem alteradas. A protecao depende de a rede interna continuar confiavel.
Nao encaminhar portas no roteador. As credenciais Tuya que ja apareceram no
historico precisam ser trocadas manualmente no provedor das cameras.

### Alto

**SEC-01 - Resolvido: dependencias executaveis com hash fixado**

O bootstrap agora aceita `go2rtc.exe` e `ffmpeg.exe` somente quando o SHA-256
confere com os binarios aprovados da versao. Download, extracao e reutilizacao
de binarios existentes passam pela mesma verificacao; um arquivo divergente nao
e executado.

**SEC-02 - Resolvido no codigo e Git; rotacao manual ainda obrigatoria**

Os fallbacks com camera foram removidos do codigo. O YAML e a configuracao local
foram removidos do controle de versao e ignorados, e a senha da interface web e
gerada localmente. Isso nao apaga segredos de commits anteriores nem troca a
senha da conta Tuya: essa rotacao precisa ser feita manualmente pelo responsavel.

**SEC-03 - Resolvido: atualizacao remota passa a falhar fechada**

O NVR nao executa mais `pip install` no inicio. Uma versao remota so e oferecida
quando `trusted_update_hashes` local contem os hashes SHA-256 do gerenciador e
do visualizador; a verificacao ocorre antes de parar gravacoes. Sem autorizacao
local, a atualizacao fica bloqueada e o sistema continua gravando.

**SEC-04 - Resolvido: volume vinculado por serial e configuracao limitada**

O primeiro volume valido tem serial armazenado em `storage_identity`; leituras,
gravacao, sincronizacao, limpeza e abertura de pasta exigem essa identidade. A
configuracao compartilhada aceita apenas streams validados, bloco e identidade,
sem poder substituir credenciais, atualizacao ou destino local arbitrariamente.

### Medio

**DATA-01 - Timestamps irregulares vindos dos streams**

Os arquivos novos foram lidos pelo FFmpeg com codigo de saida zero, mas houve
avisos de DTS nao monotonico e, no inicio de um bloco, referencias HEVC
ausentes. Arquivos antigos exibem comportamento semelhante, indicando origem ou
ponte de stream, nao regressao desta alteracao. O gravador preserva o TS bruto
em `gravar_bloco_cam` (`gerenciador.pyw:4267`); remux continuo aumentaria CPU e
nao deve ser adotado sem teste de reproducao e carga.

**DATA-02 - Resolvido: limpeza emergencial exige politica explicita**

A exclusao emergencial agora vem desativada. Em pouco espaco, o NVR alerta,
preserva o acervo e usa o fallback local quando possivel. Se for habilitada em
manutencao, ela respeita `retention_days` (90 por padrao) e nao apaga dias
recentes para atingir um limite arbitrario.

### Medio

**OPS-01 - Resolvido: encerramento por processo proprio**

O processo retornado por `Popen` e guardado pela instancia do NVR. Watchdog e
encerramento usam esse objeto e nao executam mais `taskkill /IM go2rtc.exe`,
preservando qualquer go2rtc de outro aplicativo no mesmo PC.

**OPS-02 - Monolito e erros silenciosos dificultam manutencao**

`gerenciador.pyw` concentra GUI, rede, atualizacao, armazenamento e saude em
mais de 5 mil linhas, com 179 capturas amplas de excecao. O comportamento de
gravacao e bem protegido, mas falhas inesperadas podem ficar sem evidencia.
Separar gradualmente os modulos de configuracao, go2rtc, armazenamento e saude,
substituindo `except Exception: pass` nos limites criticos por logs com codigo
de evento.

### Resolvido nesta auditoria

- Ensaio real limitado por tempo e encerramento automatico
  (`gerenciador.pyw:5745`).
- Parada segura local sem `taskkill` (`gerenciador.pyw:1484`).
- Corrida que permitia ao startup continuar depois de um pedido de desligamento.
- Detector de camera ativa sem bytes recentes, com `STREAM_NO_DATA`
  (`gerenciador.pyw:3022`).

## Evidencia dos ensaios

- Duas cameras com produtor e consumidor ativos.
- Processo Python entre aproximadamente 55 e 59 MB; go2rtc entre 32 e 34 MB.
- C permaneceu com cerca de 35,4 GB livres e D com cerca de 921 GB.
- Blocos das duas cameras publicados no HD e aceitos pelo FFmpeg.
- Zero arquivos no fallback local ao final.
- Zero processos Python/go2rtc/FFmpeg do ensaio, travas ou artefatos de copia.
- `Kernel_144`: cinco antes e cinco depois.
- Ensaio v4.12 de 120 segundos: Python em aproximadamente 60,6 MB, go2rtc em
  aproximadamente 31,9 MB e tendencia de memoria de apenas +1,9 MB na coleta.
- Blocos v4.12 publicados com 589.824 bytes e 11.796.480 bytes; ambos aceitos
  pelo FFmpeg com codigo de saida zero.
- Suite isolada v4.13: 39 testes aprovados, incluindo injeção YAML, identidade
  do volume, hashes de atualização, interrupção apenas da ponte própria,
  retenção sem exclusão emergencial implícita e parser de streams escapados.
- Serviço v4.13: `visualizador.html` respondeu `200` no loopback,
  `config.json` respondeu `404`, e visualizador/API pela interface LAN sem
  credenciais responderam `401`.
- Ensaio v4.13 de 60 segundos: um bloco novo de cada câmera foi publicado no
  HD (2.097.152 e 4.325.376 bytes), ambos aceitos pelo FFmpeg com código zero.
  Não restaram processos Python/go2rtc/FFmpeg nem artefatos temporários.

## Proximo criterio de liberacao

Depois de estabilizar USBXHCI, executar 24 horas primeiro com observacao de
memoria, CPU, bytes por camera, reconexoes, temperatura, espaco e eventos do
Windows. A liberacao para uso continuo depende dessa prova e de uma verificacao
manual de reproducao dos blocos em varios horarios.

## Auditoria da inteligencia operacional v4.12

A camada integrada nesta revisao nao e um modelo generativo. E um motor local
de correlacao sobre evidencias que o NVR ja coleta, portanto funciona offline,
nao exige chave e nao envia dados das cameras.

### Pontos cobertos

- Diferencia os `Kernel_144` anteriores de uma falha nova na sessao.
- Correlaciona falha USB nova com HD ausente antes de culpar o Python.
- Separa falha geral do go2rtc de problema em uma unica camera.
- Reconhece pressao do fallback local, degradacao SMART, energia e crescimento
  sustentado de memoria.
- Produz causa provavel, confianca, explicacao e acoes ordenadas no painel, no
  diagnostico e em `health_status.json`.
- Adia o scanner automatico quando a propria analise indica risco de hardware.
- Calcula a protecao de hardware independentemente da causa principal exibida;
  um sintoma de video nao libera manutencao pesada durante risco de disco, USB,
  energia ou memoria critica.
- Aguarda a primeira leitura confiavel do Windows antes de estabelecer a linha
  de base `Kernel_144`, evitando classificar historico como falha nova.

O motor fica em `build_operational_intelligence` (`gerenciador.pyw:541`), a
linha de base USB em `add_kernel_session_context` (`gerenciador.pyw:2751`) e o
bloqueio do scanner automatico em `trigger_periodic_scan`
(`gerenciador.pyw:5043`).

### Limites deliberados

- Nao apaga, move ou coloca videos em quarentena por decisao inteligente.
- Nao encerra gravacoes automaticamente por inferencia; apenas recomenda a
  parada segura. O desligamento por bateria critica continua sendo regra direta.
- A tendencia de memoria fica limitada a 120 amostras em RAM e reinicia junto
  com o aplicativo, evitando um novo arquivo de telemetria e escrita no HD.
- A conclusao depende dos sensores do Windows. SMART generico em `OK` nao
  substitui diagnostico do fabricante.

### Melhorias futuras necessarias

1. Validar as correlacoes em um teste supervisionado de 24 horas.
2. Medir falsos alertas de camera sem dados em quedas reais de internet.
3. Manter qualquer integracao futura com IA externa opcional, sem imagens ou
   credenciais e sem autoridade para acoes destrutivas.
