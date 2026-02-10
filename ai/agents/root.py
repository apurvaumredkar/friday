from ollama import chat

ROOT_MODEL = "phi4-mini:3.8b-q4_K_M"

def reply(user_message):
    response = chat(model=ROOT_MODEL,
                    messages=[{"role": "user", "content": user_message}],
                    stream=False
                    )
    return response.message.content.strip()
