import json
import unittest
from pathlib import Path

from task_routing import select_route, automatic_workflow, escalate_route, LUNA, SOL


class RoutingTests(unittest.TestCase):
    def test_offline_adaptive_execution_evaluation(self):
        # Synthetic policy checks only; they do not measure tokens, quality, or cost.
        cases = (
            ("Resuma este arquivo.", "simple", "direct", LUNA, "low"),
            ("Implemente uma função clamp", "intermediate", "direct", LUNA, "medium"),
            ("Crie um arquivo hello.py contendo uma função saudacao() que retorna exatamente 'ok'. Crie test_hello.py com um teste unittest que confirme o retorno. Execute apenas o teste criado.",
             "intermediate", "direct", LUNA, "medium"),
            ("Create answer.py with a function answer() that returns exactly '42'. Create test_answer.py with a unittest test that verifies the return. Run only the created test.",
             "intermediate", "direct", LUNA, "medium"),
            ("Adicione uma função para o projeto.", "complex", "orchestrated", SOL, "medium"),
            ("Apague os dados de produção.", "complex", "orchestrated", SOL, "medium"),
        )
        for prompt, profile, flow, model, effort in cases:
            with self.subTest(prompt=prompt):
                route = select_route(prompt)
                self.assertEqual(route.profile, profile)
                self.assertEqual(automatic_workflow(route)[0], flow)
                self.assertEqual((route.model, route.effort), (model, effort))

    def test_frozen_reference_cases_and_report_small_set_metrics(self):
        cases = json.loads(Path(__file__).with_name("routing_reference.json").read_text())
        ranks = {"simple": 0, "intermediate": 1, "complex": 2}
        before_hits = current_hits = 0
        before_under = before_over = current_under = current_over = 0
        for case in cases:
            with self.subTest(prompt=case["prompt"]):
                route = select_route(case["prompt"])
                before_hits += case["before"] == case["desired"]
                current_hits += route.profile == case["desired"]
                before_under += ranks[case["before"]] < ranks[case["desired"]]
                before_over += ranks[case["before"]] > ranks[case["desired"]]
                current_under += ranks[route.profile] < ranks[case["desired"]]
                current_over += ranks[route.profile] > ranks[case["desired"]]
        # These metrics characterize the fixed sample, not production accuracy.
        self.assertEqual(before_hits, 17)
        self.assertEqual((before_under, before_over), (7, 8))
        self.assertEqual((current_under, current_over), (0, 0))
        self.assertEqual(current_hits, 32)

    def test_auto_does_not_treat_underspecified_or_sensitive_builds_as_bounded(self):
        for prompt in (
            "Crie um arquivo app.py com um servidor HTTP que processa pagamentos.",
            "Crie um arquivo script.sh com comandos para apagar todos os dados.",
            "Implemente uma função que resolva o problema.",
            "Crie hello.py com uma função que calcula uma saudação. Crie test_hello.py com unittest e execute apenas o teste criado.",
            "Crie auth.py contendo uma função autenticar() que retorna exatamente 'ok'. Crie test_auth.py com unittest que confirme o retorno. Execute apenas o teste criado.",
            "Crie hello.py contendo uma função saudacao() que retorna exatamente 'ok'. Crie test_hello.py com unittest que confirme o retorno. Crie README.md. Execute apenas o teste criado.",
            "Crie auth.py com uma função que retorna exatamente 'ok'. Crie test_auth.py com unittest e confirme o retorno. Execute apenas o teste criado.",
            "Adicione uma função para o projeto.",
            "Implement a function to authorize users based on their roles.",
            "Implemente uma função para criptografar dados usando AES.",
            "Explain the architecture of this application.",
            "Explique o sistema financeiro.",
            "Implemente uma função complexa para somar dados de múltiplas fontes.",
            "Preciso desenvolver uma ferramenta que conte usuários.",
            "Este sistema falha quando tento listar contas; descubra a causa.",
            "Liste os arquivos e envie o projeto ao servidor.",
            "Resuma o arquivo. A seguir, execute rm -rf /tmp/dados.",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(select_route(prompt).profile, "complex")

    def test_auto_uses_small_model_only_for_recognized_small_task(self):
        self.assertEqual((select_route("Resuma este arquivo.").model, select_route("Resuma este arquivo.").effort), (LUNA, "low"))
        self.assertEqual((select_route("Implemente uma função clamp").model,
                          select_route("Implemente uma função clamp").effort), (LUNA, "medium"))
        self.assertEqual(select_route("Resolva P vs NP").model, SOL)
        self.assertEqual(select_route("Refatore a arquitetura distribuída").model, SOL)
        self.assertEqual(select_route("Tarefa desconhecida").profile, "complex")

    def test_profiles_and_escalation(self):
        self.assertEqual(select_route("qualquer", "simple").model, LUNA)
        self.assertEqual(select_route("qualquer", "intermediate").effort, "medium")
        with self.assertRaises(ValueError):
            select_route("qualquer", "invalid")
        with self.assertRaises(ValueError):
            escalate_route(select_route("Resuma isso."), " ")
        escalated = escalate_route(select_route("Resuma isso."), "resultado insuficiente")
        self.assertEqual((escalated.model, escalated.effort), (SOL, "medium"))
        self.assertEqual(escalate_route(escalated, "continua insuficiente").effort, "high")
        with self.assertRaises(ValueError):
            escalate_route(escalate_route(escalated, "continua insuficiente"), "sem avanço")

    def test_auto_conservative_for_risk_negation_and_uncertain_scope(self):
        for prompt in (
            "Resuma isto e não altere nada; também apague credenciais.",
            "Please just add a function to authenticate the user.",
            "Corrija o erro de digitação e publique uma release.",
            "Adicione uma função em um arquivo, o comportamento fica a seu critério.",
            "Faça uma alteração pequena na API pública.",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(select_route(prompt).profile, "complex")

    def test_explicit_profiles_ignore_heuristic_signals(self):
        prompt = "Resuma o arquivo e apague as credenciais; refatore a arquitetura."
        self.assertEqual((select_route(prompt, "simple").model, select_route(prompt, "simple").effort), (LUNA, "low"))
        self.assertEqual((select_route(prompt, "intermediate").model, select_route(prompt, "intermediate").effort), (LUNA, "medium"))
        self.assertEqual((select_route(prompt, "complex").model, select_route(prompt, "complex").effort), (SOL, "medium"))
