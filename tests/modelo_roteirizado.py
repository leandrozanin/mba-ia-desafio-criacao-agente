"""Modelo roteirizado para testes locais sem chave do Gemini.

Substitui o LLM por regras simples sobre o texto do morador, decidindo qual tool
ou transferencia chamar. Serve para exercitar de verdade o que NAO depende do
modelo: Runner, transferencias, confirmacao/retomada, sessao persistida,
gravacao no banco e concorrencia. Nunca e usado pela API de producao.
"""

from __future__ import annotations

import json
import re
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

DATA = re.compile(r"\d{4}-\d{2}-\d{2}")
AREAS = {"salão": "salao-de-festas", "salao": "salao-de-festas", "churrasqueira": "churrasqueira", "quadra": "quadra"}


def _texto(c: types.Content) -> str:
    return " ".join(p.text for p in c.parts or [] if p.text)


def _area(texto: str) -> str:
    baixo = texto.lower()
    return next((v for k, v in AREAS.items() if k in baixo), "")


def _chamada(nome: str, args: dict) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=nome, args=args))]))


def _fala(texto: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=texto)]))


class ModeloRoteirizado(BaseLlm):
    model: str = "roteirizado"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        yield self._decidir(llm_request)

    def _decidir(self, req: LlmRequest) -> LlmResponse:
        tools = set(req.tools_dict)
        ultimo = req.contents[-1] if req.contents else types.Content(role="user", parts=[])
        respostas = [p.function_response for p in ultimo.parts or [] if p.function_response]
        if respostas and respostas[0].name != "transfer_to_agent":
            return self._resumir(respostas[0])
        pedido = next(
            (_texto(c) for c in reversed(req.contents) if c.role == "user" and _texto(c) and not _texto(c).startswith("For context")),
            "",
        )
        baixo = pedido.lower()
        datas = DATA.findall(pedido)
        de_visita = any(k in baixo for k in ("visitante", "libera", "entrada"))
        de_reserva = any(k in baixo for k in ("reserv", "cancel"))
        if "reservar_area" in tools and not de_reserva:
            destino = "especialista_visitantes" if de_visita else "assistente_aurora"
            return _chamada("transfer_to_agent", {"agent_name": destino})
        if "autorizar_visitante" in tools and not de_visita:
            destino = "especialista_reservas" if de_reserva else "assistente_aurora"
            return _chamada("transfer_to_agent", {"agent_name": destino})
        if "reservar_area" in tools:
            if "cancel" in baixo:
                return _chamada("cancelar_minha_reserva", {"area": _area(pedido), "data": datas[0] if datas else ""})
            if "reserv" in baixo and datas and "quais" not in baixo:
                return _chamada("reservar_area", {"area": _area(pedido), "data": datas[0]})
            return _chamada("listar_minhas_reservas", {})
        if "autorizar_visitante" in tools:
            nome = re.search(r"entrada d[ao] ([A-ZÀ-Ú][\wÀ-ú]+(?: [A-ZÀ-Ú][\wÀ-ú]+)+)", pedido)
            if nome and datas:
                return _chamada("autorizar_visitante", {"nome": nome.group(1), "data": datas[0]})
            return _chamada("listar_meus_visitantes", {})
        if "ler_capitulo" in tools:
            return _chamada("ler_capitulo", {"numero": "IV" if "piscina" in baixo else "I"})
        # agente principal
        if de_reserva:
            return _chamada("transfer_to_agent", {"agent_name": "especialista_reservas"})
        if de_visita:
            return _chamada("transfer_to_agent", {"agent_name": "especialista_visitantes"})
        return _chamada("especialista_regulamento", {"request": pedido})

    def _resumir(self, fr: types.FunctionResponse) -> LlmResponse:
        resposta = fr.response or {}
        if fr.name == "ler_capitulo":
            linha = next((l for l in resposta.get("texto", "").splitlines() if "domingos" in l), "")
            return _fala(f"Pelo Art. 22 do regulamento: {linha.strip()}")
        if fr.name == "especialista_regulamento":
            return _fala(str(resposta.get("result", resposta)))
        return _fala(f"Resultado de {fr.name}: {json.dumps(resposta, ensure_ascii=False)}")
