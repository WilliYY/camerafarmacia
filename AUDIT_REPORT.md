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

O fluxo de gravacao e resiliente, mas a superficie de rede nao esta pronta para
ser considerada segura: a API do go2rtc, RTSP e WebRTC sao liberados para toda
a rede, e um ensaio controlado confirmou que a rota web entrega a configuracao
local. Nao deixar este PC em rede publica nem encaminhar essas portas no
roteador antes da correcao de `SEC-00`.

## Achados por prioridade

### Critico operacional

**HW-01 - Instabilidade USB anterior ainda nao resolvida**

Impacto: uma falha de controlador ou dispositivo USB pode congelar acesso ao
HD e ao Windows, mesmo que o NVR esteja correto. A linha de base e a linha final
mantiveram cinco relatorios; nenhum novo apareceu nos ensaios. Manter o teste de
24 horas bloqueado enquanto o total estiver aumentando.

### Critico de seguranca

**SEC-00 - API e configuracao das cameras expostas na rede local**

Impacto: qualquer equipamento que alcance o PC pode visualizar streams e, no
estado atual, obter a configuracao com credenciais das cameras. A configuracao
gera `api.listen: ":1984"` e `static_dir: ".."`
(`gerenciador.pyw:80-81`), enquanto o firewall libera `1984`, `8554` e `8555`
para qualquer IP e tambem para o perfil Publico (`gerenciador.pyw:5282-5285`).
No ensaio controlado de 2026-07-17, sem solicitar video, `GET /config.json`
respondeu `200` com 380 bytes de configuracao; `GET /visualizador.html` e
`GET /api/streams` tambem responderam. A regra `Camera Farmacia - API (1984)`
esta habilitada para os perfis Dominio, Particular e Publico.

Antes de qualquer acesso pela rede, limitar go2rtc a `127.0.0.1` ou colocar a
visualizacao externa atras de proxy com autenticacao, TLS e lista curta de
rotas. Remover as regras abertas ou restringi-las a IPs conhecidos e perfil
Particular. Como as credenciais ja existem no arquivo de configuracao servido,
devem ser trocadas depois da correcao.

### Alto

**SEC-01 - Dependencias executaveis sem verificacao de autoria**

O primeiro uso baixa go2rtc e FFmpeg e valida apenas a estrutura do ZIP
(`gerenciador.pyw:117-118`). HTTPS ajuda no transporte, mas nao substitui hash
fixado ou assinatura. Um download comprometido executaria codigo nesta maquina.
Adicionar manifesto de hashes por versao antes de automatizar novas instalacoes.

**SEC-02 - Credenciais de contingencia incorporadas no codigo**

O gerador de configuracao possui um fallback com dados de acesso dentro de
`atualizar_go2rtc_yaml` (`gerenciador.pyw:56`). Mesmo sendo um sistema interno,
um repositorio, backup ou log compartilhado amplia a exposicao. A correcao
completa exige remover o fallback, manter segredos apenas no arquivo local
ignorado e trocar as credenciais ja publicadas no historico.

**SEC-03 - Atualizacao e dependencia sem identidade criptografica fixada**

O atualizador baixa o Python de `main` e o troca depois de validar apenas
sintaxe, marcadores e versao (`gerenciador.pyw:5315-5430`). A instalacao inicial
tambem aceita ZIPs sem hash fixado e instala Pillow sem versao travada. O limite
de tamanho e o rollback protegem contra arquivo truncado, nao contra codigo
malicioso publicado no repositorio ou entregue por dependencia comprometida.
Usar releases assinadas ou manifesto de SHA-256 versionado e exigir revisao
manual para cada atualizacao.

**SEC-04 - Volume e configuracao compartilhada nao possuem identidade forte**

O destino e escolhido por label `FARMACIA` ou pela existencia de uma pasta e a
configuracao compartilhada e mesclada sem lista de campos permitidos
(`gerenciador.pyw:366-496`). Um disco errado, pendrive preparado ou arquivo
corrompido pode redirecionar o destino e alterar streams. Associar o volume por
serial/UUID armazenado na primeira configuracao e aceitar somente campos
esperados antes de regenerar o YAML.

### Medio

**DATA-01 - Timestamps irregulares vindos dos streams**

Os arquivos novos foram lidos pelo FFmpeg com codigo de saida zero, mas houve
avisos de DTS nao monotonico e, no inicio de um bloco, referencias HEVC
ausentes. Arquivos antigos exibem comportamento semelhante, indicando origem ou
ponte de stream, nao regressao desta alteracao. O gravador preserva o TS bruto
em `gravar_bloco_cam` (`gerenciador.pyw:4267`); remux continuo aumentaria CPU e
nao deve ser adotado sem teste de reproducao e carga.

**DATA-02 - Limpeza emergencial e permanente**

Quando o HD fica abaixo do limite, `executar_limpeza_emergencial`
(`gerenciador.pyw:3261`) remove pastas antigas ate recuperar espaco. Isso evita
disco cheio, mas pode reduzir a retencao mais do que o esperado. Tornar a
politica explicita na configuracao e registrar previamente os dias escolhidos.

### Medio

**OPS-01 - Encerramento da ponte por nome de processo**

O watchdog e a parada usam `taskkill /IM go2rtc.exe`
(`gerenciador.pyw:3444`, `gerenciador.pyw:4684`). Se outro aplicativo usar a
mesma ponte neste PC, ele tambem pode ser encerrado. Guardar o PID retornado por
`Popen`, validar seu caminho e encerrar somente aquele processo.

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
- Suite isolada: 31 testes aprovados, incluindo prioridades, falso alerta,
  primeira leitura USB inconclusiva e limite de amostras em memoria.

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
3. Adicionar identidade persistente do volume antes de automatizar recuperacao
   mais agressiva do HD.
4. Manter qualquer integracao futura com IA externa opcional, sem imagens ou
   credenciais e sem autoridade para acoes destrutivas.
