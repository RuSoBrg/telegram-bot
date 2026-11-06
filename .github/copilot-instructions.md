# Contexto do projeto
Bot Telegram (python-telegram-bot + FastAPI) sobre um grafo LangGraph com agentes: supervisor, marketing, engenharia, vida, formador e quality_reviewer. Persistência em PostgreSQL (checkpoints LangGraph, quotas, workflows). Modelos chamados via LiteLLM. Python 3.12, gerido com uv.

# Objetivo global (requisitos obrigatórios)
1. O bot responde SEMPRE ao utilizador, nunca fica em silêncio nem só com "erro genérico".
2. O agente supervisor usa sempre a melhor IA disponível para essa função.
3. O supervisor identifica o tipo de agente necessário (marketing, engenharia, vida, formador, generalista).
4. Existe um ranking ordenado de plataformas/modelos por agente.
5. Tenta-se do melhor para o pior; se não houver plafond ou o modelo estiver indisponível, passa-se ao seguinte.
6. Quando um modelo falha, grava-se a data/hora a partir da qual volta a estar disponível (unavailable_until) e esse modelo não é tentado até essa data.
7. Há sempre alguma resposta, mesmo que venha do 8.º melhor modelo ou de uma resposta local de último recurso.

# Regras de trabalho
- Responde e comenta em Português de Portugal; identificadores de código em inglês ou como já estão no projeto.
- Faz alterações mínimas e focadas. Não reformatar ficheiros inteiros nem renomear coisas sem necessidade.
- Cada alteração de comportamento tem testes pytest em tests/. No fim de cada tarefa corre: uv run pytest -q e corrige o que partires.
- Nunca escrever chaves, tokens ou segredos no código ou em testes. Usar variáveis de ambiente.
- Não usar métodos privados/internos de bibliotecas de terceiros.
- No fim de cada tarefa, resume: ficheiros alterados, o que mudou, resultado dos testes, e o que ficou por fazer.
