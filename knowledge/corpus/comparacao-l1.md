# Comparação técnica entre blockchains: Ethereum vs Solana

Comparar Ethereum e Solana vai além do preço das moedas nativas (ETH e SOL). Resumo por eixo técnico:

- **Consenso:** ambas usam Proof of Stake; Ethereum com Gasper (slots de 12 s), Solana com Tower BFT e Proof of History (slots de cerca de 400 ms).
- **Contratos inteligentes:** Ethereum executa contratos Solidity na EVM, em sequência; Solana executa programas em Rust, em paralelo (Sealevel).
- **Finalidade:** Ethereum finaliza em cerca de 12,8 minutos; Solana confirma em menos de 1 segundo e finaliza em cerca de 13 segundos.
- **Escala:** Ethereum escala por rollups de camada 2; Solana escala na própria camada 1, com hardware mais exigente.

Preço, volume e market cap refletem o mercado, não a qualidade técnica da rede.

## Ethereum vs Solana: consenso

Ambas usam Proof of Stake. Ethereum usa o Gasper (LMD-GHOST + Casper FFG), com slots de 12 segundos e épocas de 32 slots. Solana usa Tower BFT sobre o relógio criptográfico Proof of History, com slots de cerca de 400 ms e um líder rotativo por slot que produz os blocos.

## Ethereum vs Solana: contratos inteligentes e execução

No Ethereum, contratos inteligentes são escritos em Solidity ou Vyper e executados na EVM; código e estado ficam no mesmo contrato, e as transações de um bloco são executadas em sequência. Na Solana, contratos inteligentes são programas escritos em Rust e compilados para SBF; o programa não guarda estado, os dados ficam em contas separadas, e o runtime Sealevel executa em paralelo as transações que não escrevem nas mesmas contas, porque cada transação declara antes as contas que usa. A EVM é o padrão mais adotado (também usado por L2 e redes compatíveis).

## Ethereum vs Solana: finalidade

Finalidade é a garantia de que uma transação não será revertida. No Ethereum, um bloco é finalizado após duas épocas justificadas pelo Casper FFG, cerca de 12,8 minutos; a finalidade é econômica, pois revertê-la exigiria a punição (slashing) de pelo menos 1/3 do stake. Na Solana, uma transação fica confirmada quando uma supermaioria de 2/3 do stake vota no bloco, em menos de um segundo, e finalizada após cerca de 32 slots, uns 12 a 13 segundos. Ethereum tem finalidade forte porém lenta; Solana tem finalidade rápida.

## Ethereum vs Solana: escalabilidade, taxas e descentralização

Ethereum mantém a camada 1 conservadora e escala principalmente por rollups de camada 2; o custo de execução é o gas, pago em ETH. Solana busca desempenho na própria camada 1, com taxa base por assinatura mais taxa de prioridade por compute unit, paga em SOL. Ethereum tem requisitos de hardware modestos, muitos validadores e vários clientes independentes; Solana exige hardware de validador mais potente e teve interrupções da rede em 2021 e 2022.
