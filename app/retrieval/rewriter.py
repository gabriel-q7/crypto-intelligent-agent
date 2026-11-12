"""Query Rewriting: reescreve a pergunta do usuário no vocabulário dos documentos indexados.

Única técnica de transformação de consulta da Fase 4 (sem Multi-Query nem HyDE). O LLM
(Ollama configurado, modo JSON) identifica o conceito e a intenção e devolve UMA consulta
reescrita com termos técnicos e sinônimos, sem mudar o assunto nem acrescentar suposições.
"""

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.chains.market_chains import ask_json

SYSTEM_PROMPT = """Você reescreve perguntas sobre criptoativos para busca semântica numa base de documentos técnicos \
em português (blockchain, consenso, Bitcoin, Ethereum, Solana, contratos inteligentes, finalidade, \
staking, DeFi, stablecoins, métricas de mercado, riscos).

Tarefa:
1. Identifique o conceito principal e a intenção da pergunta.
2. Escreva UMA consulta de busca com a terminologia técnica que os documentos provavelmente usam \
(termo técnico em português e o termo usual em inglês, sinônimos, nome do mecanismo).
3. Em added_terms, liste os termos técnicos específicos (em português e em inglês) que devem \
aparecer num documento que trate do assunto; eles são usados para conferir os trechos encontrados.
4. Preserve o sentido e as restrições da pergunta. Não troque o assunto, não acrescente fatos, \
ativos, números ou suposições que a pergunta não tem.
5. Não acrescente termos genéricos (ex.: "blockchain", "criptoativos", "mercado") que não ajudam a \
achar o assunto específico.
6. A consulta é uma lista curta de termos ou uma frase (até 40 palavras), sem perguntas retóricas.

Responda SOMENTE com JSON:
{"main_concept": "conceito", "intent": "o que o usuário quer saber", "rewritten_query": "consulta", \
"added_terms": ["termo acrescentado"]}

Exemplo (tema fora da base, só para mostrar o formato):
Pergunta: O que é quando o criador some com o dinheiro do projeto?
{"main_concept": "rug pull", "intent": "entender o golpe de abandono de projeto", \
"rewritten_query": "rug pull golpe de saída exit scam desenvolvedores abandonam projeto retiram liquidez fraude", \
"added_terms": ["rug pull", "exit scam", "retirada de liquidez"]}"""


class QueryRewrite(BaseModel):
    main_concept: str = Field(min_length=1, max_length=120)
    intent: str = Field(default="", max_length=300)
    rewritten_query: str = Field(min_length=3, max_length=400)
    added_terms: list[str] = Field(default=[], max_length=15)

    @field_validator("rewritten_query")
    @classmethod
    def _single_line(cls, value: str) -> str:
        return " ".join(value.split())


class QueryRewriteError(RuntimeError):
    """O modelo não devolveu uma reescrita válida (a busca segue com a pergunta original)."""


class QueryRewriter:
    def __init__(self, llm: BaseChatModel) -> None:
        self.llm = llm

    def rewrite(self, question: str) -> QueryRewrite:
        try:
            return ask_json(self.llm, SYSTEM_PROMPT, f"Pergunta: {question.strip()}", QueryRewrite)
        except (OutputParserException, ValidationError, ValueError) as exc:
            raise QueryRewriteError(f"reescrita inválida: {exc}") from exc
