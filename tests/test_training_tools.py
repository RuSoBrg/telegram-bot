import json
import unittest

from tools.training_tools import validar_pacote_formacao


class TrainingToolTests(unittest.TestCase):
    def test_validates_training_package(self):
        result = json.loads(
            validar_pacote_formacao.invoke(
                {
                    "pacote_json": (
                        '{"title":"Python","audience":"Iniciantes",'
                        '"duration_minutes":60,"learning_objectives":["Criar funções"],'
                        '"session_plan":[{"title":"Prática","duration_minutes":20,'
                        '"objective":"Criar funções","instructions":"Resolver"}],'
                        '"assessment":["Exercício"]}'
                    )
                }
            )
        )
        self.assertTrue(result["valid"])

    def test_rejects_incomplete_package(self):
        result = json.loads(
            validar_pacote_formacao.invoke({"pacote_json": '{"title":"Incompleto"}'})
        )
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
