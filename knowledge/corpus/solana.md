# Solana

Solana (SOL) é uma blockchain programável de camada 1 lançada em 2020, projetada para alta vazão e baixa latência numa única camada, sem depender de rollups. SOL é a moeda nativa: paga taxas de transação e é usada no staking dos validadores.

## Consenso: Proof of History e Tower BFT

Solana usa Proof of Stake: validadores fazem stake de SOL (próprio ou delegado) e votam nos blocos. Dois componentes se destacam:

- **Proof of History (PoH):** um relógio criptográfico — uma sequência de hashes SHA-256 em que cada saída é entrada da próxima — que registra a ordem e a passagem do tempo entre eventos antes do consenso. PoH não é o mecanismo de consenso em si; ele ordena as transações e reduz a comunicação necessária entre validadores.
- **Tower BFT:** variante de tolerância a falhas bizantinas (PBFT) que usa o relógio do PoH; cada voto de um validador o compromete com um ramo da cadeia por um tempo que cresce exponencialmente com votos seguidos.

O tempo é dividido em **slots** de cerca de 400 ms. Um **líder** rotativo, escolhido antecipadamente na agenda de líderes proporcional ao stake, produz os blocos de cada slot e envia as transações diretamente aos próximos líderes (sem mempool global).

## Contratos inteligentes (programas)

Em Solana, contratos inteligentes são chamados de **programas**. São escritos principalmente em Rust (o framework Anchor é comum) e compilados para SBF (Solana Bytecode Format, derivado do eBPF), executados no runtime da Solana e não na EVM.

- **Modelo de contas:** o código do programa fica separado do estado. Programas são sem estado (stateless); os dados ficam em contas separadas que o programa possui e lê ou escreve. Na EVM, ao contrário, código e estado ficam no mesmo contrato.
- **Execução paralela (Sealevel):** cada transação declara antecipadamente quais contas vai ler e escrever. Transações que não escrevem nas mesmas contas são executadas em paralelo em vários núcleos; a EVM do Ethereum executa as transações de um bloco em sequência.
- **Programas nativos e SPL:** tokens são criados pelo programa padrão SPL Token (equivalente funcional aos padrões ERC-20/ERC-721 do Ethereum), sem precisar implantar um contrato novo por token.
- **Compute units:** o custo de execução é medido em compute units, com limite por transação, em vez de gas.

## Taxas

Cada transação paga uma taxa base fixa por assinatura e, opcionalmente, uma **taxa de prioridade** (priority fee) por compute unit para ser incluída antes em momentos de congestionamento. Parte da taxa base é queimada. As taxas são pagas em SOL e costumam ser frações de centavo, mas o valor atual exige dados de mercado.

## Finalidade

Solana expõe três níveis de confirmação (commitment) de uma transação:

- **processed:** incluída em um bloco pelo líder, ainda sem votos; pode ser descartada.
- **confirmed:** o bloco recebeu votos de uma supermaioria (2/3 do stake); acontece em menos de um segundo em condições normais e reversão é muito improvável (finalidade otimista).
- **finalized:** o bloco tem 31 ou mais blocos confirmados acima dele (máximo lockout do Tower BFT), cerca de 12 a 13 segundos; é considerado irreversível.

A atualização de consenso **Alpenglow**, aprovada pela comunidade de validadores em 2025, substitui Tower BFT e PoH no consenso por novos protocolos (Votor e Rotor) com meta de finalidade em torno de 100 a 150 ms; o cronograma de ativação deve ser conferido em fonte atual.

## Trade-offs

- Alta vazão e taxas baixas na camada 1, com experiência de uso parecida com a de um sistema centralizado rápido.
- Requisitos de hardware dos validadores elevados (CPU, memória e banda), o que encarece rodar um nó e é um argumento de quem aponta menor descentralização.
- Histórico de interrupções da rede em 2021 e 2022, quando a produção de blocos parou e os validadores precisaram reiniciar a rede de forma coordenada; a estabilidade melhorou depois, e um segundo cliente de validador independente (Firedancer) reduz o risco de falha por bug de um único cliente.
