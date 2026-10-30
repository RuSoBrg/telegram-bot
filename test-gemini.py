import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

# Inicializa o cliente com a versão mais recente do SDK da Google
client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))

print("--- Modelos disponíveis para a tua chave ---")
for model in client.models.list():
    print(model.name)