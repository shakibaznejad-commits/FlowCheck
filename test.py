"""Make a minimal LLM provider connectivity check using environment settings."""

import os
import sys

from openai import OpenAI


api_key = os.getenv("LLM_API_KEY")
if not api_key:
    sys.exit("LLM_API_KEY is not set; configure it in the local environment.")

client = OpenAI(
    api_key=api_key,
    base_url=os.getenv("LLM_BASE_URL", "http://localhost:20128/v1"),
    max_retries=0,
    timeout=30.0,
)

response = client.chat.completions.create(
    model=os.getenv("LLM_MODEL", "cx/gpt-6-luna"),
    messages=[{"role": "user", "content": "Reply with the single word: ready"}],
)
print(response.choices[0].message.content or "Provider returned an empty response.")
