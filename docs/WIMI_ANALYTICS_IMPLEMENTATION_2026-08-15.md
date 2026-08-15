# WIMI Analytics - primeira integracao funcional

Data: 15/08/2026

## Resultado

O NVR e a fundacao do WIMI Analytics agora vivem no mesmo repositorio e aparecem
como um produto unico. Internamente continuam isolados:

- `gerenciador.pyw` continua dono exclusivo de gravacao, go2rtc, energia e HD;
- `wimi_analytics.server` e um processo local separado e somente leitura;
- o botao `Painel WIMI` inicia ou reutiliza exatamente uma instancia;
- a pagina Cameras abre o visualizador go2rtc direto, sem proxy ou transcode;
- o snapshot do NVR e filtrado antes de chegar ao navegador;
- a pagina Rede diagnostica somente a configuracao deste PC;
- coletores ainda inexistentes nunca aparecem como ativos.

## Fluxo entregue

```text
gerenciador.pyw
  |-- grava e monitora as cameras como antes
  |-- publica sistema/logs/health_status.json atomicamente
  `-- inicia wimi_analytics.server em processo separado
         |-- NvrHealthBridge le e sanitiza o snapshot
         |-- WindowsNetworkDiagnostics le configuracao local com cache
         |-- API local somente leitura exige sessao do navegador
         `-- dashboard exibe Cameras e estado operacional
```

## Rotas locais

| Rota | Finalidade | Protecao |
|---|---|---|
| `/` | painel unificado | cria cookie de sessao local |
| `/healthz` | readiness do supervisor | resposta minima, sem telemetria |
| `/api/v1/overview` | estado sanitizado do produto | sessao, Host e Origin |
| `/api/v1/nvr/health` | saude sanitizada do NVR | sessao, Host e Origin |
| `/api/v1/modules` | estado declarado dos modulos | sessao, Host e Origin |
| `/api/v1/network/status` | interface, IPv4, gateway e DNS deste PC | sessao, Host e Origin |

O servico recusa metodos de escrita, nao define CORS permissivo e escuta somente
em `127.0.0.1:8765`. As portas `1984` e `29999` sao reservadas ao go2rtc e ao
controle de instancia do NVR.

## Protecoes de hardware e dados

- zero acesso ao acervo de videos;
- zero banco, cache, modelo ou fila no HD de gravacao;
- nenhum stream e retransmitido pelo Analytics;
- leitura limitada a 2 MiB e com retry curto para troca atomica no Windows;
- snapshot ausente, invalido, antigo ou com relogio divergente falha fechado;
- DTO por lista permitida remove hostname, caminhos, URL, credenciais, serial de
  disco, evidencias internas e campos desconhecidos;
- nenhum loop de reinicio, log continuo ou download automatico foi adicionado;
- o servidor HTTP local usa workers daemon para manter `/healthz` responsivo
  durante uma coleta, sem impedir o encerramento do processo;
- diagnostico de rede usa CIM e `Get-NetAdapter` somente leitura, timeout de 4 s,
  saida maxima de 64 KiB, cache valido de 5 min, falha em cache por no maximo
  30 s e no maximo 16 interfaces;
- a coleta de rede nao varre dispositivos, captura pacotes, le navegacao, grava
  cache em disco ou expoe endereco MAC;
- shutdown termina apenas o processo cujo handle pertence ao NVR.

## Estado dos modulos

| Modulo | Estado desta entrega |
|---|---|
| Cameras e gravacao | integrado por snapshot e visualizador direto |
| Fundacao Analytics | ativa |
| Visao computacional | nao configurada |
| Computadores | nao configurado |
| Rede | diagnostico local ativo; DNS/flows da loja nao configurados |
| Relatorios | aguardando fontes reais |

Essa separacao e intencional. Ativar detector, tracking, telemetria de aplicativos
ou DNS sem benchmark e governanca produziria risco de falso positivo e carga
desnecessaria no computador do NVR.

## Validacao executavel

```powershell
python -m py_compile gerenciador.pyw wimi_analytics\__init__.py wimi_analytics\__main__.py wimi_analytics\backend.py wimi_analytics\launcher.py wimi_analytics\network_diagnostics.py wimi_analytics\server.py
python -m unittest discover -s tests -v
node --check wimi_analytics\static\app.js
python gerenciador.pyw --health-check
```

O teste real de camera nao e requisito desta fatia porque `/api/stream.ts`,
go2rtc e o player existente nao foram modificados. Antes de ativar visao
computacional, seguir o protocolo de baseline e ensaio controlado do `AGENTS.md`.

## Limitacao de atualizacao

O atualizador legado baixa apenas `gerenciador.pyw` e `visualizador.html`. Esta
integracao multi-arquivo deve ser instalada pelo checkout completo e validado do
repositorio. Nao elevar a versao anunciada pelo atualizador ate existir um pacote
assinado que inclua `wimi_analytics/` de forma atomica.
