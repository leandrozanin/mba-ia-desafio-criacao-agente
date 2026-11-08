"""Tools dos especialistas.

Garantia 2: nenhuma tool recebe apartamento como argumento. O apartamento vem
sempre do state da sessao, gravado uma unica vez em POST /sessoes
(`apartamento_da_sessao`). O modelo nao tem como escolher outro.

Garantia 1: `reservar_area` (quando a area tem taxa) e `autorizar_visitante`
so gravam com `tool_context.tool_confirmation.confirmed`, que o ADK so preenche
quando chega a resposta da rota de confirmacoes. Texto do morador ("ja estou
confirmando") nunca vira confirmacao.
"""

from __future__ import annotations

import re
from datetime import date

from google.adk.tools import ToolContext

from . import banco, regulamento

CHAVE_APARTAMENTO = "apartamento"
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get(CHAVE_APARTAMENTO)
    if not apartamento or apartamento not in banco.APARTAMENTOS:
        raise ValueError("sessao sem apartamento valido")
    return str(apartamento)


def _data_valida(data: str) -> bool:
    if not _DATA.match(data or ""):
        return False
    try:
        date.fromisoformat(data)
    except ValueError:
        return False
    return True


def _erro(mensagem: str) -> dict:
    return {"status": "erro", "mensagem": mensagem}


def _area(area: str) -> dict | None:
    return banco.AREAS.get((area or "").strip().lower())


# ------------------------------------------------------------ reservas


def listar_areas() -> dict:
    """Lista as areas comuns que podem ser reservadas, com id, nome e taxa em reais (0 = sem cobranca)."""
    return {"areas": [{"id": a["id"], "nome": a["nome"], "taxa": a["taxa"]} for a in banco.AREAS.values()]}


def consultar_disponibilidade(area: str, data: str) -> dict:
    """Diz se uma area comum esta livre ou ocupada numa data.

    Args:
        area: id da area (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    if _area(area) is None:
        return _erro(f"Área desconhecida: {area}. Use um id de listar_areas.")
    if not _data_valida(data):
        return _erro("Data inválida. Use AAAA-MM-DD.")
    livre = banco.area_livre(_area(area)["id"], data)
    return {"area": _area(area)["id"], "data": data, "situacao": "livre" if livre else "ocupada"}


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    return {"reservas": banco.reservas_do_apartamento(apartamento_da_sessao(tool_context))}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma area comum para o apartamento do morador desta conversa.

    Areas com taxa geram cobranca e ficam pendentes ate o morador aprovar pela
    confirmacao do aplicativo; areas sem taxa sao reservadas direto.

    Args:
        area: id da area (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    apartamento = apartamento_da_sessao(tool_context)
    dados_area = _area(area)
    if dados_area is None:
        return _erro(f"Área desconhecida: {area}. Use um id de listar_areas.")
    if not _data_valida(data):
        return _erro("Data inválida. Use AAAA-MM-DD.")
    if not banco.area_livre(dados_area["id"], data):
        return {"status": "indisponivel", "mensagem": f"{dados_area['nome']} já está reservado(a) em {data}."}

    if dados_area["taxa"] > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            tool_context.request_confirmation(
                hint=(
                    f"Reservar {dados_area['nome']} em {data} gera cobrança de "
                    f"R$ {dados_area['taxa']:.2f}. Confirma?"
                ),
                payload={"area": dados_area["id"], "data": data, "taxa": dados_area["taxa"]},
            )
            return {"status": "aguardando_confirmacao", "mensagem": "Aguardando a confirmação do morador no aplicativo."}
        if not confirmacao.confirmed:
            return {"status": "nao_confirmada", "mensagem": "O morador recusou; nada foi reservado."}

    codigo = banco.criar_reserva(apartamento, dados_area["id"], data)
    if codigo is None:
        return {"status": "indisponivel", "mensagem": f"{dados_area['nome']} já está reservado(a) em {data}."}
    return {
        "status": "reservada",
        "codigo": codigo,
        "area": dados_area["id"],
        "data": data,
        "cobranca": dados_area["taxa"],
    }


def cancelar_minha_reserva(tool_context: ToolContext, area: str = "", data: str = "", codigo: str = "") -> dict:
    """Cancela uma reserva do proprio apartamento, sem pedir confirmacao.

    Informe o codigo, ou a area e a data. So reservas do apartamento do morador
    desta conversa podem ser canceladas.

    Args:
        area: id da area (opcional se houver codigo).
        data: data AAAA-MM-DD (opcional se houver codigo).
        codigo: codigo da reserva (opcional).
    """
    apartamento = apartamento_da_sessao(tool_context)
    area_id = _area(area)["id"] if area and _area(area) else (area or None)
    canceladas = banco.cancelar_reserva(apartamento, area_id, data or None, (codigo or "").upper() or None)
    if not canceladas:
        return {
            "status": "nao_encontrada",
            "mensagem": "Não há reserva ativa do seu apartamento com esses dados. Nada foi cancelado.",
        }
    return {"status": "cancelada", "reservas": canceladas}


# ------------------------------------------------------------ visitantes


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista as autorizacoes de visita do apartamento do morador desta conversa."""
    return {"visitantes": banco.visitantes_do_apartamento(apartamento_da_sessao(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no predio numa data.

    Libera acesso ao predio, entao sempre fica pendente ate o morador aprovar
    pela confirmacao do aplicativo, mesmo que ele diga na conversa que ja confirmou.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    apartamento = apartamento_da_sessao(tool_context)
    nome = " ".join((nome or "").split())
    if not nome:
        return _erro("Informe o nome do visitante.")
    if not _data_valida(data):
        return _erro("Data inválida. Use AAAA-MM-DD.")
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=f"Liberar a entrada de {nome} no prédio em {data}. Confirma?",
            payload={"nome": nome, "data": data},
        )
        return {"status": "aguardando_confirmacao", "mensagem": "Aguardando a confirmação do morador no aplicativo."}
    if not confirmacao.confirmed:
        return {"status": "nao_confirmada", "mensagem": "O morador recusou; nenhuma entrada foi liberada."}
    banco.autorizar_visitante(apartamento, nome, data)
    return {"status": "autorizado", "nome": nome, "data": data}


# ------------------------------------------------------------ regulamento


def ler_capitulo(numero: str) -> dict:
    """Le UM capitulo do regulamento interno.

    Args:
        numero: numero romano do capitulo, por exemplo "IV".
    """
    texto = regulamento.capitulo(numero)
    if texto is None:
        return _erro(f"Capítulo inexistente: {numero}.")
    return {"capitulo": numero.strip().upper(), "texto": texto}
