# Avaliação conversacional — Fase 3

Ambiente:
- Linux, Docker Compose, CPU (12 threads);
- Ollama com `gemma4:e2b` (thinking desligado) e `nomic-embed-text`;
- Mem0 2.2.1 sobre Chroma 1.5.9, SQLite 3.46;
- CoinPaprika real, plano gratuito.

Datas: 2026-10-28 e 2026-10-29 (UTC).

Três níveis de evidência, sempre identificados:
- **[offline]** testes automatizados com LLM roteirizado, CoinPaprika simulada e memória em processo (`tests/unit/test_conversation_*.py`);
- **[integração]** teste automatizado com Ollama, Mem0/Chroma e CoinPaprika reais (`tests/integration/test_phase3_live.py`);
- **[API real]** execução manual pela API em Docker, **com reinício real do container** (`docker compose restart app`).

## 1. Resultado por cenário

| Cenário | Comportamento esperado | Offline | Integração | API real |
|---|---|---|---|---|
| **A** continuação | Mantém BTC/ETH, acrescenta 30 dias, sem pedir os ativos de novo | ✅ | ✅ | ✅ |
| **B** nova dimensão (DeFi) | Reconhece a continuação, mantém ativos e período e explica que não há fonte DeFi | ✅ | ✅ | — (coberto na integração) |
| **C** resultado anterior | Recupera a análise, compara com dados atuais e rotula o histórico; pergunta se for ambíguo | ✅ (inclusive ambiguidade) | ✅ (após reinício) | ✅ |
| **D** correção | Substitui o período, mantém o resto, sem período contraditório | ✅ (7→30) | ✅ (30→7) | ✅ (30→7) |
| **E** restauração | Retoma a sessão após reinício e resolve a continuação com o contexto persistido | ✅ (nova instância da app) | ✅ (novo serviço sobre os mesmos arquivos) | ✅ (`docker compose restart app`) |
| **F** isolamento | Cada sessão mantém só o próprio contexto; sessão nova não herda nada | ✅ | ✅ | ✅ |
| **G** conversa longa | Resume as mensagens antigas, preserva os detalhes, recupera via Chroma, respeita o orçamento | ✅ | ✅ (resumos gerados e indexados) | ⚠️ não disparou (ver 3.4) |
| **H** resultado antigo × mercado atual | Identifica o resultado histórico, não o trata como atual, busca dados novos | ✅ (relógio simulado: 5 min × 48 h) | — | ✅ (rótulo "HISTÓRICO") |

## 2. Execuções reais

### 2.1 Pela API, com reinício do container (2026-10-29)

Sessão 1 (`09be1457…`), antes do reinício:

| Turno | Mensagem | Interpretação | Status | Tempo | Resposta (resumo) |
|---|---|---|---|---|---|
| A1 | "Compare BTC e ETH no mercado atual" | `asset_comparison`; ativos da mensagem | complete | 41,9 s | 24h: BTC +0,51% > ETH +0,30%; market cap BTC > ETH (CoinPaprika, 00:26 UTC) |
| A2 | "Considere os últimos 30 dias" | `follow_up` → `asset_comparison`; **ativos do contexto ativo**, período da mensagem | partial | 43,9 s | "Não há dados de 30 dias nesta fonte (plano gratuito…)"; mostra 24h e **7d reais** (BTC -3,69% > ETH -4,89%) |
| D | "Eu quis dizer 7 dias, não 30" | correção → 7 dias, ativos mantidos | complete | 57,5 s | Variação 7d BTC -3,69% > ETH -4,89% |
| Preferência | "Prefiro sempre análises com foco em risco" | declaração de preferência | complete | 8,4 s | "Anotado…" (sem nova análise; extração no Mem0 em segundo plano) |

Sessão 2 (`20c1ddca…`): "Analise SOL" → `market_analysis`, complete, 177 s (ver 3.3).

**`docker compose restart app`** → container saudável de novo às 00:35:23 UTC.

Depois do reinício:

| Turno | Mensagem | Resultado |
|---|---|---|
| E | `GET /conversations/{id}` | HTTP 200; 8 mensagens em ordem `[1..8]`; contexto ativo restaurado `{assets: [BTC, ETH], period_days: 7}` |
| E | "E nas últimas 24 horas?" | `follow_up` → comparação de BTC e ETH (ativos do contexto ativo), complete, 68,8 s |
| C/H | "Agora compare os dois com o resultado anterior" | `previous_result_reference` → análise `245607fff147`; "dados de 00:36 UTC… HISTÓRICO"; preço atual × anterior por ativo; 7,4 s |
| F | Sessão 2: "E nos últimos 7 dias?" | Só SOL (contexto da sessão 2): "Variação em 7 dias (CoinPaprika): SOL -4,47%" |
| — | `GET /conversations/000…0` | HTTP 404 |

### 2.2 Teste de integração (`test_phase3_live.py`, 2026-10-29): **1 passed em 537 s**

- A1 → A2 (30 dias indisponível, 24h e 7d reais) → D (correção 30→7) → B (DeFi `unavailable`, "Contexto mantido…: BTC, ETH, 7 dias");
- preferência → **reinício simulado**: novo `ConversationService` sobre o mesmo SQLite e o mesmo Chroma;
- C após o reinício: comparação com a análise `98fdad34e4b5` ("dados de 00:21 UTC, há 6 min"), diferenças de -0,09% e -0,05%;
- memórias da sessão: 3 episódios de análise, resumos e 1 memória semântica;
- busca "foco em risco" → **"O usuário prefere que as análises de criptoativos sejam sempre focadas em risco."** (score 0,617), extraída pelo Mem0 sem valores de mercado;
- nenhuma memória da sessão "SOL" apareceu na sessão BTC/ETH.

### 2.3 Testes automatizados

| Suíte | Resultado |
|---|---|
| Offline completa (Fases 1–3) | **260 passed** (199 anteriores + 61 da Fase 3) |
| Integração Fase 3 (Ollama + Mem0 + CoinPaprika) | 1 passed (537 s) |
| Integração completa (Fases 1–3) | **12 passed** (702 s) |

Verificação de depreciação: um teste importa todos os módulos da Fase 3 num interpretador limpo com `LangChainDeprecationWarning` tratado como erro, e passa.

## 3. Problemas encontrados e corrigidos

| # | Sintoma | Onde apareceu | Correção |
|---|---|---|---|
| 3.1 | "Prefiro sempre análises com foco em risco" **disparou de novo a análise DeFi** (tratada como continuação) | Integração real | Declarações de preferência (sem pedido de análise) são só confirmadas e enviadas à extração semântica; o pedido em andamento não muda |
| 3.2 | Um resumo indexado por versão (v1, v2, v3...) **acumulava** na memória longa | Integração real | `replace_episode`: um único resumo por sessão no Chroma (o histórico de versões fica no SQLite) |
| 3.3 | "Compare com o resultado anterior" com duas análises diferentes na sessão escolhia uma pelo contexto ativo | Teste offline | O contexto ativo só desambigua com referência explícita ("os dois", "them"); sem ela, pede esclarecimento |
| 3.4 | `percent_change_30d = 0` da CoinPaprika chegava ao modelo como dado real | Investigação antes da implementação | 0 exato em 30d vira "indisponível" no cliente; o período de 30 dias recebe a limitação explícita |

## 4. Limitações observadas (não corrigidas)

1. **Disputa pelo Ollama:** a extração semântica em segundo plano (Mem0, 68–89 s no spike) usa o mesmo Ollama em CPU. Uma requisição que chega durante a extração espera: "Analise SOL" levou 177 s contra cerca de 40 s normalmente.
2. **"Resultado anterior" pega a análise mais recente** com os mesmos ativos. Logo após uma nova análise, isso dá diferença de 0,00% (API real, 2.1). É correto ao pé da letra, mas pouco útil; não há heurística para "o resultado de antes desta conversa".
3. **Resumo depende da configuração:** com a janela padrão (10) e o gatilho (4), a conversa real de 12 mensagens ainda não tinha resumo. O cenário G foi validado com janela 4 e gatilho 2 (offline e integração).
4. **Histórico de preços:** 30 dias ou mais é sempre `partial`, com a limitação; o plano gratuito não tem séries.
5. **Memória semântica em inglês:** o Mem0 às vezes grava o fato em inglês ("The user corrected…"), independentemente do prompt.
6. **Recuperação seletiva:** com `MEMORY_MIN_SCORE=0.3`, consultas genéricas ("E nas últimas 24 horas?") não recuperam memórias. Análises já presentes na janela são deduplicadas de propósito.
7. **Sem autenticação:** o `session_id` não é credencial.
8. **Tempo de resposta em CPU:** de 7 s (comparação com resultado anterior, sem LLM de análise) a cerca de 70 s por turno.

## 5. Regressão das Fases 1 e 2

Suíte de integração completa executada em 2026-10-29, depois de toda a Fase 3: **12 passed em 702 s**. Detalhe: 5 testes da Fase 1 (chain estruturada com o modelo real), 6 da Fase 2 (CoinPaprika real, agente ReAct, roteamento paralelo, embeddings reais) e 1 da Fase 3. Nenhuma regressão.
