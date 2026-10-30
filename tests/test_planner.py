import unittest

from agents.planner import create_plan, mark_steps_done, plan_as_state


class PlannerTests(unittest.TestCase):
    def test_training_request_is_decomposed(self):
        plan = create_plan("Cria um curso de Python com 6 sessões")
        self.assertEqual(len(plan), 10)
        self.assertEqual(plan[0].order, 1)
        self.assertEqual(plan[-1].title, "Empacotar e enviar")

    def test_simple_request_has_single_step(self):
        self.assertEqual(len(create_plan("Explica uma função Python")), 1)

    def test_plan_tracks_execution_status(self):
        state = plan_as_state(create_plan("Cria um curso de Python"), current=3)
        self.assertEqual(state[0]["status"], "done")
        self.assertEqual(state[2]["status"], "in_progress")
        updated = mark_steps_done(state, [3])
        self.assertEqual(updated[2]["status"], "done")
