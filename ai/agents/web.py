from google import genai
from google.genai import types
import os
from dotenv import load_dotenv

load_dotenv()


class WebAgent:
    def __init__(self):
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = "gemini-2.5-flash-lite"

    def web_search(self, query):
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(tools=[grounding_tool])

        return self.client.models.generate_content(
            model=self.model, contents=query, config=config
        ).text
