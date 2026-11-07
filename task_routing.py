"""Small deterministic model policy; deliberately makes no model call."""
from dataclasses import dataclass
import re

LUNA = "gpt-6-luna"
SOL = "gpt-6.1-sol"


@dataclass(frozen=True)
class Route:
    profile: str
    model: str
    effort: str
    reason: str


def select_route(prompt: str, profile: str = "auto") -> Route:
    if profile not in {"auto", "simple", "intermediate", "complex"}:
        raise ValueError("Perfil de roteamento inválido.")
    text = prompt.strip().lower()
    complex_words = r"\b(arquitetura|migra(?:ção|r)|refator(?:ação|ar)|segurança|concorrência|distribuíd\w*|sistema|múltipl\w*|complex\w*|investigue|diagnostique|audite|planeje)\b"
    simple_task = re.match(r"^(resuma|explique|liste|traduza|corrija (?:o )?(?:typo|erro de digitação)|formate|conte|converta)\b", text)
    bounded_build = re.match(r"^(implemente|corrija|adicione|crie)\b.{0,120}\b(função|teste|arquivo|método)\b", text)
    if profile == "auto":
        if len(text) <= 180 and simple_task and not re.search(complex_words, text):
            profile = "simple"
        elif len(text) <= 700 and bounded_build and not re.search(complex_words, text):
            profile = "intermediate"
        else:
            profile = "complex"
    if profile == "simple":
        return Route(profile, LUNA, "low", "tarefa curta e delimitada")
    if profile == "intermediate":
        return Route(profile, LUNA, "medium", "tarefa de escopo moderado")
    return Route(profile, SOL, "medium", "escopo amplo ou risco técnico elevado")


def escalate_route(route: Route, reason: str) -> Route:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Informe o motivo do escalonamento.")
    if route.model == SOL:
        if route.effort == "high":
            raise ValueError("A política já está no esforço máximo.")
        return Route(route.profile, SOL, "high", "escalonamento: " + reason.strip())
    return Route(route.profile, SOL, "medium", "escalonamento: " + reason.strip())
