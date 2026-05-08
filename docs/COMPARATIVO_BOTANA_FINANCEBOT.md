# Comparativo Botana x FinanceBot alinhado ao FinanceAnaHub

Data da analise: 2026-05-08

Este documento compara as funcionalidades solicitadas do Botana com o estado atual do FinanceBot/FinanceiroApp e descreve como cada uma deve ser adaptada para funcionar dentro do FinanceAnaHub antes de qualquer alteracao no codigo.

## Objetivo

Espelhar no FinanceBot as funcionalidades existentes no Botana, respeitando a diferenca de fluxo entre os dois sistemas e o contrato de execucao do FinanceAnaHub.

Funcionalidades solicitadas:

- Recuperar e-mails
- Reprocessar e-mails, usando a logica nova do Botana
- Historico
- Conferencia
- Prazos
- Diagnosticos, usando a logica nova do Botana

Fora do escopo deste ciclo:

- Mudancas especificas de Braspress
- Regras de fatura Braspress
- Diagnosticos dedicados a Braspress
- Conferencia especial para lancamentos Braspress

## Resumo executivo

As funcionalidades existem no Botana, mas nao podem ser copiadas literalmente para o FinanceBot porque os dois projetos trabalham com fluxos diferentes.

O Botana processa e-mails enviados, usando a consulta Gmail `in:sent has:attachment filename:xml`. O FinanceBot processa e-mails recebidos em duas contas, `principal` e `nfe`, usando anexos XML recebidos em inbox.

O Botana escolhe a planilha com base no CNPJ emitente. O FinanceBot escolhe a planilha com base no CNPJ destinatario, pois o objetivo e registrar contas a pagar para MVA/EH quando elas aparecem como destino da NF-e ou CT-e.

Conclusao: as telas e capacidades podem ser espelhadas, mas a camada de Gmail, a chave de conferencia, as regras de prazos e os endpoints precisam ser adaptados ao dominio do FinanceBot e expostos de forma compativel com o FinanceAnaHub em `/financeiro/`.

## Alinhamento com FinanceAnaHub

O FinanceAnaHub funciona como gateway e orquestrador. Ele inicia cada app, mantem a lista de instancias e publica o painel por prefixo de rota.

Configuracao atual identificada no Hub para o FinanceBot:

- Instancia: `financeiro_principal`
- Tipo: `financeiro`
- Backend: `http://127.0.0.1:8765`
- Prefixo publico: `/financeiro/`
- Diretorio do app: `D:\financeiroAPP`
- Comando: `main.py --server --no-browser`

Configuracao atual identificada no Hub para o Botana:

- Instancia: `botana_principal`
- Tipo: `botana`
- Backend: `http://127.0.0.1:8865`
- Prefixo publico: `/botana/`
- Diretorio do app: `D:\Botana`
- Comando: `main.py --server --host 127.0.0.1 --port 8865`

Contrato tecnico para o FinanceBot:

- Continuar subindo pelo Hub com `main.py --server --no-browser`.
- Manter o backend local em `127.0.0.1:8765` ou garantir compatibilidade com esse endereco.
- Manter chamadas internas do painel como `/api/...`; o Hub reescreve para `/financeiro/api/...` quando acessado pelo navegador.
- Evitar URLs absolutas hardcoded como `localhost`, `127.0.0.1` ou porta fixa no JavaScript do painel.
- Nao abrir navegador quando iniciado pelo Hub.
- Acoes longas devem rodar em background com polling, porque o proxy do Hub nao deve segurar chamadas demoradas.
- Respostas de API devem ser JSON estavel para o Hub poder exibir cards, historico operacional e diagnosticos.

Endpoints que o FinanceBot deve expor para uso direto ou futuro card do Hub:

| Funcionalidade | Endpoint interno FinanceBot | Endpoint via Hub |
| --- | --- | --- |
| Estado/progresso | `GET /api/state` | `GET /financeiro/api/state` |
| Executar leitura normal | `POST /api/run-now` | `POST /financeiro/api/run-now` |
| Reprocessar e-mails | `POST /api/reprocess` | `POST /financeiro/api/reprocess` |
| Recuperar e-mails | `POST /api/recover-emails` | `POST /financeiro/api/recover-emails` |
| Historico | `GET /api/history` | `GET /financeiro/api/history` |
| Exportar historico | `GET /api/history/export` | `GET /financeiro/api/history/export` |
| Iniciar conferencia | `POST /api/conferencia-parcelas/start` | `POST /financeiro/api/conferencia-parcelas/start` |
| Consultar conferencia | `GET /api/conferencia-parcelas/job` | `GET /financeiro/api/conferencia-parcelas/job` |
| Prazos | `GET /api/prazos` | `GET /financeiro/api/prazos` |
| Busca em prazos | `GET /api/prazos/search` | `GET /financeiro/api/prazos/search` |
| Diagnosticos | `GET /api/diagnostics` | `GET /financeiro/api/diagnostics` |

Observacao: hoje o FinanceAnaHub possui fluxos especificos do Botana apontando para `/botana/api/...`, inclusive recuperacao de NFs ausentes e relatorio de NFs. Para o FinanceBot, a recomendacao e criar endpoints proprios em `/financeiro/api/...` e depois adicionar cards especificos do tipo `financeiro`, sem forcar o formato de NF de venda do Botana.

## Diferencas principais de arquitetura

| Area | Botana | FinanceBot |
| --- | --- | --- |
| Origem dos e-mails | E-mails enviados | E-mails recebidos |
| Consulta base do Gmail | `in:sent has:attachment filename:xml` | `has:attachment filename:xml in:inbox -in:sent -in:drafts` |
| Contas Gmail | Uma conta principal | Duas contas: `principal` e `nfe` |
| Labels | `XML Processado Botana` e legadas do Botana | `XML Processado` e `XML Analisado` |
| Escolha da planilha | CNPJ emitente | CNPJ destinatario |
| Documentos principais | NF-e de vendas | NF-e e CT-e |
| Historico | Linhas `HIST_JSON` em relatorios TXT | JSONL em `historico_eventos.jsonl` |
| Painel | Funcionalidades concentradas em `main.py` | Painel em `panel_web.py`, processamento em modulos separados |
| Rota no Hub | `/botana/` | `/financeiro/` |

## Funcionalidades

### 1. Recuperar e-mails

No Botana, a recuperacao existe nas rotas `/api/recover-emails` e `/api/recover-missing`. Ela permite buscar mensagens por periodo, faixa de NF ou lista manual de NFs. Depois de localizar as mensagens, o sistema rele exatamente os e-mails encontrados.

Arquivos relevantes no Botana:

- `D:\Botana\main.py`, funcoes `_recover_search_query`, `_find_missing_messages`, `_start_recover_missing_background`
- `D:\Botana\gmail_service.py`, funcoes `buscarMessagesEnviadosPagina` e `baixar_anexos_de_mensagem`

No FinanceBot, ainda nao existe uma recuperacao equivalente. Hoje a leitura normal busca mensagens novas ou sem labels e processa o lote retornado pelo Gmail.

Arquivos relevantes no FinanceBot:

- `D:\financeiroAPP\gmail_fetcher.py`
- `D:\financeiroAPP\panel_web.py`

Adaptacao necessaria:

- Criar uma recuperacao para duas contas: `principal`, `nfe` ou ambas.
- Usar consulta base de inbox, nao sent.
- Aceitar filtros por periodo, faixa de NF e lista manual.
- Para NF-e, validar o numero pelo XML sempre que possivel.
- Para CT-e, considerar o numero de CT-e encontrado no XML.
- Permitir que a recuperacao rode em background e atualize o painel com progresso.
- Expor `POST /api/recover-emails` com resposta rapida indicando `job_id` ou estado equivalente.
- Publicar progresso em `GET /api/state`, para funcionar em `/financeiro/api/state` via Hub.
- Garantir que o painel use fetch relativo (`/api/recover-emails`) e nao URL absoluta.

Observacao importante: no FinanceBot, assunto e nome do anexo podem nao ser confiaveis. A recuperacao deve preferir ler metadados dos anexos XML quando a busca por assunto nao for suficiente.

### 2. Reprocessar e-mails

No Botana, a logica nova de reprocessamento nao apenas remove labels. Ela seleciona mensagens ja marcadas com labels do Botana, ordena por data, remarca o lote e chama a leitura exatamente sobre essas mensagens. Tambem permite continuar no proximo lote mais antigo.

Arquivos relevantes no Botana:

- `D:\Botana\main.py`, funcoes `_reprocess_recent`, `_start_reprocess_background`
- `D:\Botana\gmail_service.py`, funcoes `list_botana_labels`, `listar_mensagens_com_labels_botana`, `marcar_mensagem_para_reprocessar`

No FinanceBot, o reprocessamento atual remove as labels `XML Processado` e `XML Analisado` e, opcionalmente, marca como nao lido. Depois disso, depende de uma nova leitura comum.

Arquivo relevante no FinanceBot:

- `D:\financeiroAPP\panel_web.py`, funcao `_reprocess_recent`

Adaptacao necessaria:

- Listar mensagens com labels do FinanceBot em vez de apenas remover labels por consulta.
- Ordenar as mensagens por data real.
- Selecionar um lote exato.
- Remarcar/preparar esse lote para reprocessamento.
- Executar a leitura diretamente nas mensagens selecionadas, usando `messages_override` ou mecanismo equivalente.
- Mostrar progresso no painel.
- Permitir continuar o proximo lote quando houver mensagens mais antigas.
- Ajustar para uma conta especifica ou ambas as contas.
- Manter `POST /api/reprocess` como endpoint principal e compativel com `/financeiro/api/reprocess`.
- Retornar rapidamente e acompanhar a execucao por `GET /api/state`.

Essa e uma das mudancas mais importantes, porque corrige a fragilidade atual de "remover label e esperar que a proxima busca pegue o e-mail certo".

### 3. Historico

No Botana, o historico e gravado como JSON estruturado dentro dos relatorios TXT, usando linhas `HIST_JSON`. A tela permite filtros por data, vencimento, NF, cliente, aba, empresa, CNPJ e exportacao CSV.

Arquivos relevantes no Botana:

- `D:\Botana\main.py`, funcoes `_write_history_launch_event`, `_history_from_reports`, `_history_csv_rows`

No FinanceBot, a base de historico ja e melhor: existe `historico_eventos.jsonl`, com eventos `email_processado` e `boleto_lancado`.

Arquivos relevantes no FinanceBot:

- `D:\financeiroAPP\history_store.py`
- `D:\financeiroAPP\processor.py`
- `D:\financeiroAPP\panel_web.py`

Adaptacao recomendada:

- Manter o JSONL do FinanceBot como fonte principal.
- Ampliar os filtros da tela de historico.
- Incluir exportacao CSV.
- Mostrar origem `MVA`, `EH`, conta Gmail e tipo de documento.
- Preservar eventos de NF-e e CT-e; eventos Braspress existentes ficam fora do escopo de regra especial.
- Melhorar a busca textual para encontrar fornecedor, documento, arquivo XML, vencimento, parcela e local de lancamento.
- Expor `GET /api/history` com filtros por query string.
- Expor `GET /api/history/export` para CSV, funcionando tambem em `/financeiro/api/history/export`.

Nao recomendo migrar o FinanceBot para `HIST_JSON` em TXT, porque o JSONL atual e mais apropriado para consulta e evolucao.

### 4. Conferencia

No Botana, a conferencia le diretamente as planilhas, agrupa por NF e compara a quantidade esperada de parcelas com a quantidade realmente lancada. Ela detecta:

- OK
- Faltando
- Duplicada
- A mais
- Faltando + duplicada
- NF ausente

Tambem existe logica para limpar linhas excedentes/duplicadas de forma controlada.

Arquivos relevantes no Botana:

- `D:\Botana\main.py`, funcoes `_load_audit_sheet_rows`, `_gerar_conferencia_parcelas_from_rows`, `_gerar_conferencia_parcelas`, `_start_audit_job`, `_delete_audit_rows`

No FinanceBot, nao existe uma conferencia equivalente. Hoje e possivel ver historico, mas nao existe uma rotina que leia a planilha real e diga se as parcelas esperadas batem com o que foi lancado.

Adaptacao necessaria:

- Ler todas as planilhas configuradas do FinanceBot: `EH_2025`, `EH_2026`, `MVA_2025`, `MVA_2026`.
- Ler as abas mensais.
- Normalizar datas, valores, parcelas e numeros de documento.
- Agrupar por chave segura: empresa, ano, tipo de documento e numero do documento.
- Para NF-e, usar `Qtd Parcelas` como expectativa principal.
- Para CT-e, normalmente esperar 1 parcela.
- Detectar duplicidade estrutural por numero de parcela, aceitando variacoes como `1a Parcela`, `Parcela 1`, `1/6`.
- Identificar lancamentos faltantes, duplicados e excedentes.
- Opcionalmente cruzar com historico e Gmail para explicar por que uma NF esta ausente.
- Rodar como job em background, iniciado por `POST /api/conferencia-parcelas/start`.
- Consultar resultado/progresso por `GET /api/conferencia-parcelas/job`.

Ponto de atencao: Braspress fica fora deste ciclo. Linhas historicas de Braspress podem ser ignoradas, tratadas genericamente como CT-e ou marcadas como fora de escopo, mas nao devem receber regra especial agora.

### 5. Prazos

No Botana, a aba Prazos le as planilhas e lista titulos pendentes. Ela considera status vazio ou `A Receber`, ignora `BAIXADO`, `BAIXADA`, `ESTORNADO` e `ESTORNADA`, e classifica o tipo por descricao:

- `BLT` ou `BOLETO` = boleto
- `DEP` = deposito

Arquivos relevantes no Botana:

- `D:\Botana\main.py`, funcoes `_gerar_relacao_pendencias`, `_buscar_boletos_em_aberto_por_nome`, `_sheet_watch_kind`, `_sheet_watch_is_baixado`

No FinanceBot, a descricao gravada normalmente e o fornecedor, por exemplo `NWT COMERCIAL E IMPORTADORA LTDA (Bot)`. O padrao `BLT`/`DEP` nao existe de forma consistente.

Adaptacao recomendada:

- Criar Prazos como "titulos em aberto por vencimento".
- Considerar pendente quando `Status` estiver vazio ou indicar algo equivalente a aberto.
- Ignorar linhas pagas, baixadas ou estornadas.
- Separar por origem `MVA`, `EH` ou ambas.
- Mostrar vencidos, vencendo hoje e a vencer.
- Permitir busca por fornecedor/documento.
- Expor `GET /api/prazos` para listagem filtrada.
- Expor `GET /api/prazos/search` para busca rapida usada pelo painel.
- Nao copiar inicialmente a separacao `Boleto` x `Deposito` do Botana sem uma fonte confiavel.

Decisao pendente:

- Se a separacao boleto/deposito for obrigatoria no FinanceBot, sera necessario criar uma regra nova. Possibilidades:
- Adicionar marcador na descricao no momento do lancamento.
- Inferir por conta de e-mail ou tipo de XML.
- Criar campo auxiliar/configuracao.
- Classificar tudo inicialmente como `Titulo` e evoluir depois.

### 6. Diagnosticos

No Botana, diagnostico nao e apenas erro tecnico. Ele tambem ajuda a explicar problemas de conferencia e recuperacao, por exemplo quando o assunto do e-mail indica uma NF, mas o XML/anexo indica outra.

Arquivos relevantes no Botana:

- `D:\Botana\main.py`, funcoes `_recovery_match_details`, `_audit_missing_nf_reason`, `_audit_missing_nf_candidates_for_month`

No FinanceBot, diagnostico atual guarda erros recentes de API, autenticao e execucoes manuais.

Arquivo relevante no FinanceBot:

- `D:\financeiroAPP\panel_web.py`, funcoes `_add_diagnostic` e `_friendly_error`

Adaptacao necessaria:

- Manter diagnosticos tecnicos atuais.
- Adicionar diagnosticos operacionais:
- E-mail encontrado mas XML nao corresponde ao filtro.
- XML sem duplicatas.
- XML com vencimento invalido.
- Documento sem planilha para CNPJ destino.
- CT-e sem vencimento.
- Mensagem reprocessada sem novo lancamento por duplicidade.
- NF ausente na conferencia mas localizada no Gmail.
- Erro de autenticacao ou proxy quando acessado via `/financeiro/`.
- Exibir o diagnostico no painel de forma legivel e manter detalhes tecnicos para suporte.
- Expor `GET /api/diagnostics` com JSON estavel para o painel e para o Hub.

## Proposta de implementacao

### Fase 1: Base de Gmail e acoes manuais

- Refatorar `gmail_fetcher.py` para aceitar uma lista de mensagens especificas.
- Criar funcoes de listagem de labels do FinanceBot.
- Implementar o reprocessamento novo com lote exato.
- Implementar recuperacao por periodo, faixa e lista manual.
- Atualizar painel com progresso das acoes manuais.
- Garantir endpoints `POST /api/reprocess` e `POST /api/recover-emails` compativeis com `/financeiro/api/...`.

### Fase 2: Historico

- Melhorar consulta do `history_store.py`.
- Adicionar exportacao CSV.
- Ampliar filtros da tela.
- Incluir tipo de documento, origem, conta, fornecedor, vencimento e local.
- Expor `GET /api/history` e `GET /api/history/export`.

### Fase 3: Conferencia

- Criar modulo proprio para leitura e normalizacao das planilhas.
- Implementar agrupamento por documento.
- Detectar faltantes, duplicados e excedentes.
- Adicionar tela `Conferencia`.
- Adicionar diagnosticos operacionais conectados ao Gmail.
- Rodar conferencia em job iniciado por API, com polling pelo painel.
- Nao implementar regra especial Braspress nesta fase.

### Fase 4: Prazos

- Reaproveitar a leitura normalizada das planilhas.
- Criar tela `Prazos`.
- Listar titulos abertos por vencimento.
- Adicionar filtros por origem, dias, fornecedor/documento e status.
- Expor `GET /api/prazos` e `GET /api/prazos/search`.

### Fase 5: Diagnosticos

- Unificar erros tecnicos e operacionais.
- Adicionar contexto de Gmail, XML, planilha e documento.
- Expor na aba `Diagnostico`.
- Expor `GET /api/diagnostics` para painel e Hub.

### Fase 6: Integracao com FinanceAnaHub

- Validar inicializacao por `main.py --server --no-browser`.
- Validar acesso a `/financeiro/login`, `/financeiro/` e `/financeiro/api/state`.
- Validar que todos os fetches do painel usam URLs relativas.
- Validar que acoes longas retornam rapido e seguem por polling.
- Adicionar cards ou atalhos do tipo `financeiro` no Hub somente depois dos endpoints do FinanceBot estarem estaveis.
- Manter fluxos existentes do Botana em `/botana/` sem alteracao.

## Riscos e pontos de decisao

### Classificacao de Prazos

O Botana diferencia boleto/deposito por descricao. O FinanceBot nao possui essa marcacao hoje. Copiar essa regra literalmente geraria classificacoes erradas ou vazias.

Recomendacao: iniciar com `Titulos em aberto` e deixar boleto/deposito para uma segunda etapa, depois de definir uma fonte confiavel.

### Braspress fora do ciclo

Braspress sera tratado futuramente em especifico. Neste ciclo:

- Nao alterar `braspress_utils.py`.
- Nao criar diagnosticos especificos de Braspress.
- Nao reconciliar fatura Braspress na conferencia.
- Nao usar fatura Braspress como chave de recuperacao.
- Se uma linha exigir regra Braspress, marcar como `fora_do_escopo_braspress` ou tratar genericamente como CT-e sem regra especial.

### Labels legadas

O Botana procura labels antigas/datadas e converge tudo para uma label estavel. O FinanceBot hoje usa `XML Processado` e `XML Analisado`. Antes de implementar, e preciso decidir se manteremos esses nomes ou se criaremos labels mais especificas, como:

- `XML Processado FinanceBot`
- `XML Analisado FinanceBot`
- `XML Reprocessado FinanceBot`

Minha recomendacao e manter compatibilidade com as labels atuais e, se for criar novas, fazer uma migracao gradual.

### Reprocessamento com duas contas

No FinanceBot, toda acao precisa aceitar:

- Conta Principal
- Conta NFe
- Ambas

Isso afeta recuperacao, reprocessamento, diagnostico e historico.

### Compatibilidade com proxy do Hub

O Hub publica o FinanceBot em `/financeiro/` e reescreve rotas comuns para esse prefixo. O risco principal e introduzir JavaScript ou HTML com caminho absoluto incorreto.

Regras para evitar quebra:

- Usar fetch relativo para `/api/...`.
- Nao montar URLs com `window.location.origin + ":8765"`.
- Nao depender de redirect absoluto para `/login` sem testar via `/financeiro/login`.
- Preferir jobs em background para qualquer acao que possa passar de poucos segundos.

## Arquivos analisados

Botana:

- `D:\Botana\main.py`
- `D:\Botana\gmail_service.py`
- `D:\Botana\sheets_writer.py`
- `D:\Botana\xml_parser.py`
- `D:\Botana\config.py`
- `D:\Botana\README.md`

FinanceBot:

- `D:\financeiroAPP\gmail_fetcher.py`
- `D:\financeiroAPP\processor.py`
- `D:\financeiroAPP\panel_web.py`
- `D:\financeiroAPP\history_store.py`
- `D:\financeiroAPP\audit_store.py`
- `D:\financeiroAPP\sheets_utils.py`
- `D:\financeiroAPP\settings_manager.py`
- `D:\financeiroAPP\config.py`
- `D:\financeiroAPP\braspress_utils.py` somente para delimitar o fora de escopo

FinanceAnaHub:

- `D:\FinanceAnaHub\data\instances.json`
- `D:\FinanceAnaHub\src\web\server.py`
- `D:\FinanceAnaHub\src\instances\models.py`
- `D:\FinanceAnaHub\src\storage\settings.py`

## Conclusao

As seis funcionalidades solicitadas foram identificadas no Botana.

O caminho correto nao e copiar o `main.py` do Botana para o FinanceBot. O correto e portar as ideias e adaptar as regras para:

- e-mails recebidos;
- duas contas Gmail;
- escolha de planilha por CNPJ destinatario;
- NF-e e CT-e;
- historico JSONL ja existente;
- formato real das planilhas do FinanceBot;
- execucao atras do FinanceAnaHub em `/financeiro/`.

Antes de implementar, a unica decisao funcional pendente e como a tela `Prazos` deve classificar boleto/deposito no FinanceBot. Sem uma marcacao confiavel, a primeira versao deve tratar tudo como titulo em aberto por vencimento.

Braspress permanece explicitamente fora deste ciclo e deve ser tratado em documento/implementacao separados.
