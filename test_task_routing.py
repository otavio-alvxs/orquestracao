import unittest

from task_routing import select_route, escalate_route, LUNA, SOL


class RoutingTests(unittest.TestCase):
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
