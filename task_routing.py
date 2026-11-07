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
    text = re.sub(r"\s+", " ", prompt.strip().lower())
    if profile == "auto":
        # Keep the patterns deliberately finite: these are intent cues, not
        # semantic understanding. Unknown or compound requests stay conservative.
        risk = re.compile(
            r"\b(arquitetur\w*|architecture|migra(?:ção|r|te|ting)\w*|refactor\w*|refator\w*|"
            r"seguran\w*|security|permission\w*|permiss(?:ão|ões)|authenticat\w*|"
            r"autentica\w*|credential\w*|credencia\w*|secret\w*|senha\w*|"
            r"authorize\w*|authoriz\w*|autoriza\w*|encrypt\w*|criptograf\w*|"
            r"payment\w*|pagament\w*|financeir\w*|financial|finance|"
            r"apagar|apague|delete|delet\w*|rm\s+-|destroy\w*|"
            r"concorr\w*|concurren\w*|distribu\w*|distributed|investig\w*|"
            r"debug\w*|diagnos\w*|audit\w*|audite\w*|review\w*|revise\w*|"
            r"regress\w*|intermit\w*|intermittent|publicar|publique|release|deploy\w*|"
            r"produ\w*|api pública|public api|banco de dados|database|system|sistema|"
            r"plan\w*|planej\w*|múltipl\w*|multiple|complex\w*)\b"
        )
        if not text or risk.search(text):
            profile = "complex"
        else:
            # Compound requests stay conservative. Allow only the recognized
            # nominal bounds phrase; other conjunctions can overroute safely.
            action = (r"resum\w*|summari[sz]\w*|explique\w*|explain\w*|liste\w*|"
                      r"list\w*|tradu\w*|translat\w*|formate\w*|format\w*|"
                      r"conte\w*|count\w*|converta\w*|convert\w*|corrija\w*|"
                      r"fix\w*|implemente\w*|implement\w*|adicione\w*|add\w*|"
                      r"crie\w*|create\w*|remova\w*|remove\w*|altere\w*|change\w*|"
                      r"publique\w*|publish\w*|delete\w*|apague\w*")
            action_re = re.compile(r"\b(?:" + action + r")\b")
            actions = list(action_re.finditer(text))
            conjunction_text = re.sub(r"\bmínimo\s+e\s+máximo\b", " ", text)
            compound_cue = (re.search(r"\b(and|e|mas|but|then|depois|também|also)\b", conjunction_text)
                            or re.search(r"[;!?]|\.\s+[a-zà-ÿ]", text))
            if len(actions) > 1 or compound_cue:
                profile = "complex"
            else:
                # Accept only a small set of clear lead-ins. A verb mentioned
                # later as context is not enough to establish the request.
                intent = re.sub(
                    r"^(?:(?:por favor|please|kindly)\s*[, :]?\s*)*"
                    r"(?:(?:no|in)\s+(?:arquivo|file)\s+[\w./-]+\s*[, :]\s*)?", "", text)
                simple = re.search(
                    r"^(resum\w*|summari[sz]\w*|explique\w*|explain\w*|"
                    r"liste\w*|list\w*|tradu\w*|translat\w*|formate\w*|format\w*|"
                    r"conte\w*|count\w*|converta\w*|convert\w*)\b", intent)
                typo_edit = re.search(r"^(corrija|fix)\b.{0,50}\b(typo|erro de digita\w*|spelling)\b", intent)
                literal_file = re.fullmatch(
                    r"(?:crie|create)\s+(?:(?:o|a|the)\s+)?(?:arquivo|file)\s+[\w-]+\.(?:txt|md)\s+"
                    r"(?:com\s+(?:o\s+)?texto|contendo|with\s+(?:the\s+)?text|containing)\s+"
                    r"[\"']?[\w .,!?-]{1,80}[\"']?[.!]?", intent)
                build = re.search(r"^(implemente|implement|adicione|add|crie|create|corrija|fix)\b", intent)
                target = re.search(r"\b(funç\w*|function|teste|test|método|method)\b", intent)
                behavior = re.search(
                    r"\b(return\w*|limit\w*|raise\w*|lanç\w*|retorn\w*|"
                    r"somar|sum|valid\w*|is_even|clamp|calculate\w*|calcula\w*|"
                    r"divide\w*|divid\w*|compare\w*|compar\w*|sort\w*|orden\w*)\b", intent)
                if len(text) <= 700 and (simple or typo_edit or literal_file) and (not build or typo_edit or literal_file):
                    profile = "simple"
                elif len(text) <= 700 and build and target and behavior:
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
