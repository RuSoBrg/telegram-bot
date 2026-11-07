import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ.setdefault("ALLOWED_USER_IDS", "12345678")
