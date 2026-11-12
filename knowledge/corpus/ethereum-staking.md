# Ethereum e staking

Ethereum (ETH) é uma blockchain programável lançada em 2015. Executa contratos inteligentes — programas que rodam na Ethereum Virtual Machine (EVM) — base para tokens, DeFi e NFTs. As taxas de execução são pagas em gas, denominadas em ETH.

## Contratos inteligentes

Contratos inteligentes do Ethereum são escritos principalmente em Solidity (ou Vyper), compilados para bytecode e implantados num endereço da rede. Cada contrato guarda seu próprio código e seu próprio estado (armazenamento) no mesmo lugar; depois de implantado, o código não muda, salvo padrões de proxy atualizável.

A EVM executa as transações de um bloco em sequência, uma após a outra, o que simplifica o modelo de programação mas limita a vazão da camada 1. Cada operação consome gas; o usuário paga a taxa base (queimada, EIP-1559) mais uma gorjeta de prioridade, em ETH. Tokens seguem padrões como ERC-20 (fungíveis) e ERC-721 (NFTs). Para escalar, o Ethereum aposta em rollups de camada 2 (L2), que executam transações fora da L1 e publicam provas ou dados nela.

## Finalidade

O consenso do Ethereum em PoS é o Gasper (regra de escolha de fork LMD-GHOST + Casper FFG). O tempo é dividido em slots de 12 segundos; 32 slots formam uma época (cerca de 6,4 minutos). Um bloco é considerado finalizado quando duas épocas são justificadas por votos de 2/3 do ETH em stake, o que leva cerca de 12,8 minutos. Reverter um bloco finalizado exigiria que validadores com pelo menos 1/3 do stake fossem punidos (slashing), por isso a finalidade é econômica. A finalidade em um único slot (single-slot finality) está no roteiro de pesquisa do Ethereum.

## The Merge

Em setembro de 2022, no evento chamado The Merge, o Ethereum migrou de Proof of Work para Proof of Stake. Desde então não há mineração no Ethereum: blocos são propostos e atestados por validadores.

## Staking

Staking de ETH é bloquear ETH como garantia para participar da validação da rede e receber recompensas em ETH. Um validador próprio exige 32 ETH. Alternativas são staking em pools ou serviços de staking líquido, que emitem um token representando o ETH em stake.

Riscos do staking: slashing (perda parcial do stake por comportamento malicioso ou falhas graves do validador), risco de contrato inteligente em serviços de staking líquido, risco de custódia em plataformas centralizadas e prazo de saída da fila de validadores. O rendimento do staking varia com o número de validadores e a atividade da rede; o valor atual exige dados de mercado.
