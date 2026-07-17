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

## Achados por prioridade

### Critico operacional

**HW-01 - Instabilidade USB anterior ainda nao resolvida**

Impacto: uma falha de controlador ou dispositivo USB pode congelar acesso ao
HD e ao Windows, mesmo que o NVR esteja correto. A linha de base e a linha final
mantiveram cinco relatorios; nenhum novo apareceu nos ensaios. Manter o teste de
24 horas bloqueado enquanto o total estiver aumentando.

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

### Medio

**DATA-01 - Timestamps irregulares vindos dos streams**

Os arquivos novos foram lidos pelo FFmpeg com codigo de saida zero, mas houve
avisos de DTS nao monotonico e, no inicio de um bloco, referencias HEVC
ausentes. Arquivos antigos exibem comportamento semelhante, indicando origem ou
ponte de stream, nao regressao desta alteracao. O gravador preserva o TS bruto
em `gravar_bloco_cam` (`gerenciador.pyw:3805`); remux continuo aumentaria CPU e
nao deve ser adotado sem teste de reproducao e carga.

**DATA-02 - Limpeza emergencial e permanente**

Quando o HD fica abaixo do limite, `executar_limpeza_emergencial`
(`gerenciador.pyw:2828`) remove pastas antigas ate recuperar espaco. Isso evita
disco cheio, mas pode reduzir a retencao mais do que o esperado. Tornar a
politica explicita na configuracao e registrar previamente os dias escolhidos.

### Resolvido nesta auditoria

- Ensaio real limitado por tempo e encerramento automatico
  (`gerenciador.pyw:5274`).
- Parada segura local sem `taskkill` (`gerenciador.pyw:1232`).
- Corrida que permitia ao startup continuar depois de um pedido de desligamento.
- Detector de camera ativa sem bytes recentes, com `STREAM_NO_DATA`
  (`gerenciador.pyw:2633`).

## Evidencia dos ensaios

- Duas cameras com produtor e consumidor ativos.
- Processo Python entre aproximadamente 55 e 59 MB; go2rtc entre 32 e 34 MB.
- C permaneceu com cerca de 35,4 GB livres e D com cerca de 921 GB.
- Blocos das duas cameras publicados no HD e aceitos pelo FFmpeg.
- Zero arquivos no fallback local ao final.
- Zero processos Python/go2rtc/FFmpeg do ensaio, travas ou artefatos de copia.
- `Kernel_144`: cinco antes e cinco depois.

## Proximo criterio de liberacao

Depois de estabilizar USBXHCI, executar 24 horas primeiro com observacao de
memoria, CPU, bytes por camera, reconexoes, temperatura, espaco e eventos do
Windows. A liberacao para uso continuo depende dessa prova e de uma verificacao
manual de reproducao dos blocos em varios horarios.
