# Telegram Bot

Bot Telegram baseado em LangGraph, com especialistas, fallback de modelos e
persistência de disponibilidade.

## Variáveis de ambiente

As variáveis podem ser definidas no `.env`:

- `REQUEST_BUDGET_SECONDS`: orçamento máximo de uma chamada ao router (por
  omissão, `90`).
- `SHOW_MODEL_FOOTER`: mostra o agente e o modelo usados no fim da resposta
  (`true` por omissão).
- `OLLAMA_BASE_URL`: URL de uma instância Ollama; pode ser usada como último
  recurso local. Para executar o serviço opcional, use
  `docker compose --profile local-llm up`.
- `ALLOWED_USER_IDS`: IDs Telegram autorizados para `/estado`, separados por
  vírgulas. Vazio recusa o acesso por omissão.
- `ALLOW_PUBLIC`: use `true` apenas para permitir acesso público quando
  `ALLOWED_USER_IDS` está vazio.
- `ADMIN_CHAT_ID`: chat Telegram que recebe alertas únicos quando um fornecedor
  é desativado por autenticação ou um modelo deixa de existir.

Também são necessárias as credenciais dos fornecedores indicadas no
`.env.example`.

## Editar o catálogo

Os rankings estão em [`routing/catalog.yaml`](routing/catalog.yaml). Cada
entrada define fornecedor, modelo, variável de credencial, capacidades,
timeout e score. A ordem da lista é a ordem de tentativa; o router ignora
entradas cuja variável `api_key_env` não esteja configurada. Valide os nomes
dos modelos na documentação do fornecedor antes de os ativar.

Para usar um modelo local, configure `OLLAMA_BASE_URL` e mantenha a entrada
Ollama no fim do ranking. Não é necessário expor PostgreSQL para a rede
externa: o Compose limita a publicação a `127.0.0.1`.
