# Corpus de conhecimento local

Documentos usados pela busca semântica (FAISS). Escopo: **conceitos estáveis** sobre criptomoedas,
blockchain e métricas DeFi. Por decisão de projeto, o corpus **não contém preços, volumes ou outros
valores de mercado**: dados atuais vêm apenas das ferramentas de mercado (CoinPaprika).

| Arquivo | Tema |
|---|---|
| blockchain-basics.md | Blockchain, blocos, consenso, PoW × PoS |
| bitcoin.md | Bitcoin, emissão, halving |
| ethereum-staking.md | Ethereum, contratos inteligentes, staking, The Merge |
| defi-metrics.md | DeFi, TVL, DEX, AMM, liquidez |
| stablecoins.md | Stablecoins e seus tipos |
| market-metrics.md | Market cap, volume, volatilidade, drawdown, dominância |
| risks.md | Riscos comuns em cripto |

Este README não é indexado. Para reindexar: `python -m scripts.ingest_knowledge`.
