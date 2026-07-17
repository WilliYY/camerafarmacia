# Roteiro de Seguranca e Operacao 24h

## Estado atual

- Copia e publicacao atomicas com validacao de conteudo.
- Fallback dinamico entre HD e backup local.
- Reserva local de espaco sem apagar gravacoes pendentes.
- Encerramento aguardando as threads de gravacao.
- Scanner incremental, limitado e com confirmacao dupla.
- Quarentena no mesmo disco do arquivo original.
- Testes isolados para os principais caminhos de perda de dados.
- Avaliador central de saude com relatorio JSON atomico e alertas por transicao.
- Logs locais persistentes, limitados e espelhados no HD por thread isolada.
- Autodiagnostico `--health-check` sem iniciar cameras ou go2rtc.
- Ensaio real limitado por tempo e interrupcao local com `--safe-stop`.
- Inteligencia local com causa provavel, confianca, acoes e protecao contra
  scanner automatico pesado durante risco de hardware.
- Deteccao de novo `Kernel_144` na sessao separada do historico de 24 horas.
- Tendencia de memoria limitada a duas horas e mantida apenas em RAM.

## Prioridade alta

1. **Identidade persistente do HD**
   Gravar e validar o numero de serie do volume, alem do nome `FARMACIA`. Isso
   evita aceitar outro disco com a mesma letra ou uma pasta criada por engano.

2. **Supervisor externo controlado**
   Usar uma Tarefa Agendada do Windows com reinicio em falha, diferenciando
   encerramento planejado de queda inesperada. A configuracao deve ser feita
   pelo aplicativo e testada sem criar um ciclo infinito de reinicializacao.

3. **Teste prolongado controlado**
   O ensaio real curto agora possui encerramento automatico. Ainda falta
   executar streams simulados por 24 horas e depois cameras reais em janela
   supervisionada, medindo memoria, CPU, logs, temporarios, reconexoes, espaco
   e novos eventos USB.

4. **Expandir cenarios somente com evidencia real**
   Registrar falsos positivos e situacoes nao classificadas durante o teste de
   24 horas. Adicionar regras novas apenas quando houver sinais reproduziveis,
   sem transformar inferencias em comandos destrutivos.

## Prioridade media

5. **Atualizacao assinada**
   O hash atual detecta alteracao depois do download, mas nao prova autoria.
   Adicionar manifesto assinado ou releases autenticadas antes de substituir o
   codigo em uma maquina que grava 24h.

6. **Reduzir escrita no disco do Windows**
   Tornar `gravando_temp` configuravel para um volume apropriado, mantendo
   reserva de espaco e fallback seguro. So migrar depois de medir bitrate e
   capacidade real dos discos.

7. **Separacao gradual do arquivo principal**
   Extrair primeiro funcoes puras de armazenamento e validacao, depois scanner,
   gravador e atualizador. Manter a GUI por ultimo e executar os testes apos
   cada extracao.

8. **Diagnostico de disco mais confiavel**
   Complementar o status WMI generico com dados disponiveis de temperatura,
   erros e integridade do volume. Falha ao consultar deve aparecer como
   "desconhecido", nunca como "saudavel".

## Cuidados fora do codigo

- Investigar os eventos `USBXHCI / LiveKernelEvent 144` separadamente.
- Testar outro cabo, outra porta e alimentacao adequada do dispositivo USB.
- Confirmar temperatura, energia e configuracao de suspensao do Windows.
- Manter copia de configuracao e uma forma conhecida de iniciar sem auto-update.
- Nao executar scanner completo ou manutencao pesada durante horario critico.

## Ordem recomendada

Identidade do HD, supervisor externo, teste de 24 horas e, somente com esses
dados, reducao de escrita e separacao em modulos.
