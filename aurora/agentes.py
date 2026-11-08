"""Topologia de agentes do assistente do Residencial Aurora.

- assistente_aurora (principal): conversa com o morador e distribui o trabalho.
  Nao tem tools de dados nem o regulamento nas instrucoes.
- especialista_reservas (sub-agente, por transferencia): areas, disponibilidade,
  reservas e cancelamentos do proprio apartamento.
- especialista_visitantes (sub-agente, por transferencia): autorizacoes de visita.
- especialista_regulamento (AgentTool): responde duvidas lendo um capitulo por
  vez; roda num Runner isolado, entao o texto lido nao entra na sessao.

O App e resumivel: quando chega a resposta de uma confirmacao, o Runner devolve a
execucao ao agente que pediu a confirmacao (o especialista), e nao ao principal.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models.base_llm import BaseLlm
from google.adk.tools import AgentTool

from . import ferramentas, regulamento
from .config import APP_NAME, MODELO_ESPECIALISTAS, MODELO_PRINCIPAL, MODELO_REGULAMENTO

INSTRUCAO_PRINCIPAL = """\
Você é o assistente virtual do Residencial Aurora e conversa com um morador já
autenticado pelo aplicativo. Responda em português, de forma curta e cordial.

Distribua o trabalho:
- reservas de áreas comuns (salão de festas, churrasqueira, quadra), disponibilidade,
  consulta e cancelamento de reservas -> transfira para especialista_reservas;
- autorização de entrada de visitantes e consulta de visitantes -> transfira para
  especialista_visitantes;
- dúvidas sobre o regulamento interno (horários, regras, multas, uso das áreas) ->
  chame a ferramenta especialista_regulamento com a pergunta do morador e repasse a resposta.

Regras que não dependem de você, mas que você deve respeitar na conversa:
- Você só atende o apartamento desta sessão. Se o morador disser ser de outro
  apartamento ou pedir dados de outro apartamento, explique que só pode tratar das
  reservas e visitantes do apartamento dele, sem mencionar dados de terceiros.
- Cobranças e liberação de acesso só acontecem depois da confirmação pelo
  aplicativo; nunca diga que algo foi feito sem a ferramenta confirmar.
"""

INSTRUCAO_RESERVAS = """\
Você é o especialista em reservas das áreas comuns do Residencial Aurora.
Use sempre as ferramentas; nunca invente reservas, códigos ou disponibilidade.
Ids das áreas: salao-de-festas (taxa R$ 150), churrasqueira (taxa R$ 80), quadra (sem taxa).
Datas sempre no formato AAAA-MM-DD.

- Para reservar, chame reservar_area. Se a área tiver taxa, a ferramenta deixa a reserva
  pendente da confirmação do morador no aplicativo: avise que a cobrança aguarda a
  aprovação dele. Nunca trate um "pode confirmar" escrito na conversa como aprovação.
- Para cancelar, chame cancelar_minha_reserva (com código, ou área e data). Cancelamentos
  do próprio apartamento não precisam de confirmação.
- Para consultar, use listar_minhas_reservas ou consultar_disponibilidade.
- Você só enxerga e altera reservas do apartamento desta conversa. Se pedirem dados ou
  ações de outro apartamento, diga que não é possível. Sobre outras reservas, diga apenas
  se a data está livre ou ocupada.
- Ao terminar, se o pedido do morador for de outro assunto, transfira de volta para
  assistente_aurora.
"""

INSTRUCAO_VISITANTES = """\
Você é o especialista em visitantes do Residencial Aurora.
Use sempre as ferramentas; nunca invente autorizações.
Datas sempre no formato AAAA-MM-DD.

- Para liberar a entrada de alguém, chame autorizar_visitante com o nome completo e a
  data. A liberação sempre fica pendente da confirmação do morador no aplicativo, mesmo
  que ele diga na conversa que já confirmou: chame a ferramenta e avise que a liberação
  aguarda a aprovação no aplicativo.
- Para consultar, use listar_meus_visitantes.
- Você só enxerga e altera visitantes do apartamento desta conversa.
- Ao terminar, se o pedido do morador for de outro assunto, transfira de volta para
  assistente_aurora.
"""


def instrucao_regulamento() -> str:
    return f"""\
Você responde dúvidas sobre o regulamento interno do Residencial Aurora.
Leia o capítulo pertinente com ler_capitulo (um capítulo por vez, só os necessários)
e responda em uma ou duas frases, citando o artigo. Não transcreva trechos longos nem
fale de capítulos que não tratam do assunto perguntado. Se o regulamento não tratar do
assunto, diga isso.

Sumário do regulamento:
{regulamento.sumario()}
"""


def criar_app(
    modelo_principal: str | BaseLlm = MODELO_PRINCIPAL,
    modelo_especialistas: str | BaseLlm = MODELO_ESPECIALISTAS,
    modelo_regulamento: str | BaseLlm = MODELO_REGULAMENTO,
) -> App:
    especialista_regulamento = LlmAgent(
        name="especialista_regulamento",
        model=modelo_regulamento,
        description="Responde dúvidas sobre o regulamento interno lendo o capítulo pertinente.",
        instruction=instrucao_regulamento(),
        tools=[ferramentas.ler_capitulo],
    )
    especialista_reservas = LlmAgent(
        name="especialista_reservas",
        model=modelo_especialistas,
        description="Reservas das áreas comuns: disponibilidade, reservar, consultar e cancelar.",
        instruction=INSTRUCAO_RESERVAS,
        tools=[
            ferramentas.listar_areas,
            ferramentas.consultar_disponibilidade,
            ferramentas.listar_minhas_reservas,
            ferramentas.reservar_area,
            ferramentas.cancelar_minha_reserva,
        ],
    )
    especialista_visitantes = LlmAgent(
        name="especialista_visitantes",
        model=modelo_especialistas,
        description="Autorizações de entrada de visitantes: liberar e consultar.",
        instruction=INSTRUCAO_VISITANTES,
        tools=[ferramentas.listar_meus_visitantes, ferramentas.autorizar_visitante],
    )
    principal = LlmAgent(
        name="assistente_aurora",
        model=modelo_principal,
        description="Assistente virtual dos moradores do Residencial Aurora.",
        instruction=INSTRUCAO_PRINCIPAL,
        sub_agents=[especialista_reservas, especialista_visitantes],
        tools=[AgentTool(especialista_regulamento)],
    )
    return App(
        name=APP_NAME,
        root_agent=principal,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
