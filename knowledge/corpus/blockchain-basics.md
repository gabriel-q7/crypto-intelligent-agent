# Fundamentos de blockchain

Uma blockchain é um registro distribuído em que transações são agrupadas em blocos encadeados por hashes criptográficos. Cada bloco referencia o hash do bloco anterior, o que torna alterações em blocos antigos detectáveis: mudar um bloco exigiria refazer todos os blocos seguintes.

## Consenso

O mecanismo de consenso define quem adiciona o próximo bloco e como a rede concorda sobre o estado do registro.

- **Proof of Work (PoW):** mineradores competem resolvendo um problema computacional; quem resolve primeiro propõe o bloco e recebe a recompensa. A segurança vem do custo de energia e hardware. Usado pelo Bitcoin.
- **Proof of Stake (PoS):** validadores bloqueiam (fazem stake de) moedas como garantia e são escolhidos para propor e atestar blocos. Comportamento malicioso pode ser punido com perda de parte do stake (slashing). Usado pelo Ethereum desde 2022.

## Nós e descentralização

Nós completos armazenam e validam a blockchain de forma independente. Quanto mais nós e participantes independentes, mais difícil censurar transações ou alterar regras unilateralmente.

## Finalidade

Finalidade é a garantia de que uma transação não será revertida. Em PoW ela é probabilística (aumenta com o número de confirmações); em vários protocolos PoS há finalidade econômica após checkpoints.
