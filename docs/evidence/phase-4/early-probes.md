# Sondagens iniciais de recuperação (transcritas do console)

Saídas de scripts de sondagem executados em 2026-11-05 (UTC), antes da política final, com a coleção `crypto_knowledge` real e o `gemma4:e2b`. Essas execuções não foram salvas em arquivo. Os valores abaixo foram **transcritos da saída do console** e servem de registro das políticas descartadas (`docs/phase-4-evidence.md`, seção 2.6).

## A. Distâncias da busca original (sem reescrita), top-3

| Pergunta | Trechos (distância de cosseno) |
|---|---|
| Explique como funciona proof of stake | ethereum-staking.md#8 0,301; blockchain-basics.md#3 0,317; ethereum-staking.md#7 0,380 |
| Como funciona a queima de moedas? | market-metrics.md#9 0,437; stablecoins.md#16 0,444; stablecoins.md#17 0,448 |
| Por que a recompensa dos mineradores cai pela metade? | blockchain-basics.md#3 0,345; bitcoin.md#0 0,351; bitcoin.md#1 0,353 |
| O que acontece se eu perder minha seed phrase? | blockchain-basics.md#3 0,362; blockchain-basics.md#2 0,367; risks.md#12 0,369 |
| O que é dinheiro travado em contratos de um protocolo? | defi-metrics.md#5 0,309; risks.md#14 0,326; defi-metrics.md#6 0,340 |
| Como ganhar rendimento deixando ETH parado como garantia? | ethereum-staking.md#8 0,250; ethereum-staking.md#7 0,267; blockchain-basics.md#3 0,287 |
| Qual a diferença entre USDT e DAI? | defi-metrics.md#5 0,296; market-metrics.md#9 0,321; stablecoins.md#16 0,334 |
| O que significa slippage numa troca? | bitcoin.md#0 0,383; blockchain-basics.md#4 0,397; risks.md#12 0,405 |
| Por que moedas pareadas ao dólar podem perder a paridade? | stablecoins.md#17 0,281; risks.md#13 0,324; risks.md#14 0,366 |
| Quem valida os blocos depois que o Ethereum parou de minerar? | blockchain-basics.md#2 0,197; blockchain-basics.md#3 0,224; ethereum-staking.md#7 0,228 |
| Quanto tempo leva para sair um bloco novo de bitcoin? | bitcoin.md#0 0,225; blockchain-basics.md#2 0,231; market-metrics.md#11 0,267 |
| O que é impermanent loss? | blockchain-basics.md#4 0,387; risks.md#14 0,404; stablecoins.md#17 0,411 |

## B. Política 1 (fica a busca com o melhor trecho), prompt com exemplo "halving" (contaminado)

| Pergunta | Consulta reescrita | Top-3 reescrito |
|---|---|---|
| O que é impermanent loss? | impermanent loss perda impermanente provedores liquidez yield farming risco provedor liquidez | stablecoins.md#16 0,388; bitcoin.md#1 0,403; stablecoins.md#17 0,415 |
| O que acontece se eu perder minha seed phrase? | perda seed phrase recuperação carteira cripto perda de chave privada acesso ativos blockchain | blockchain-basics.md#2 0,239; blockchain-basics.md#4 0,269; risks.md#12 0,269 |
| Por que a recompensa dos mineradores cai pela metade? | halving Bitcoin redução da recompensa de bloco pela metade emissão de novas moedas | **bitcoin.md#1 0,139**; bitcoin.md#0 0,184; blockchain-basics.md#2 0,214 |
| Como funciona a queima de moedas? | mecanismo queima de criptoativos destruição de tokens blockchain | **blockchain-basics.md#2 0,183**; bitcoin.md#0 0,227; blockchain-basics.md#3 0,232 |

Leitura:
- **halving:** a melhora (0,139) estava inflada pelo exemplo do prompt, que era o próprio caso;
- **queima:** distância de 0,183 num trecho irrelevante, ou seja, evidência fabricada pela palavra genérica "blockchain";
- **seed phrase:** distâncias menores, mas sem o trecho certo (`risks.md#13`).

## C. Política 2 (piso pela distância à pergunta original ≤ 0,42), prompt com exemplo "rug pull"

| Pergunta | Consulta reescrita | Top-3 reescrito (dist. consulta / dist. pergunta) | Resultado |
|---|---|---|---|
| Por que a recompensa dos mineradores cai pela metade? | halving recompensa mineradores Bitcoin Bitcoin halving mecanismo de recompensa Bitcoin | bitcoin.md#1 0,204 / 0,353; bitcoin.md#0 0,300 / 0,352; stablecoins.md#17 0,366 / 0,393 | Reescrita usada; bitcoin.md#1 em 1º |
| O que é impermanent loss? | impermanent loss perda impermanente provedor liquidez yield farming variação valor ativos pool de liquidez | defi-metrics.md#5 0,346 / 0,425; **defi-metrics.md#6 0,352 / 0,455**; ethereum-staking.md#8 0,358 / 0,440 | Todos acima de 0,42 da pergunta: reescrita descartada e ficaram os trechos errados da original (0,387…) |
| O que acontece se eu perder minha seed phrase? | perda seed phrase recuperação carteira criptoativos recuperação de chaves privadas | risks.md#12 0,262 / 0,369; stablecoins.md#16 0,313 / 0,377; blockchain-basics.md#4 0,327 / 0,395 | Reescrita usada, sem o trecho certo |
| Como funciona a queima de moedas? | mecanismo queima de moedas criptoativos destruição de supply tokenomics | defi-metrics.md#6 0,243 / 0,487; blockchain-basics.md#2 0,264 / 0,499; bitcoin.md#0 0,275 / 0,483 | `no_relevant_documents` (correto) |

Leitura: o piso pela pergunta original barrou a evidência fabricada (queima), mas rejeitou o trecho certo de "impermanent loss" (`defi-metrics.md#6`), que só a reescrita tinha trazido. Com isso, a política foi substituída pela palavra âncora (arquivos `retrieval-policy-v3-*.jsonl` e `retrieval-diagnosis.jsonl`).
