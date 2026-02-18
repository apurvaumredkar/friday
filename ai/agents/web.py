import os
import logging
from .base import BaseAgent
from google import genai
from google.genai import types


class WebAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = "gemini-2.5-flash-lite"
        self.system_prompt = "You are a web search agent. Return latest, real-time, and precise information."
        self.tools = [self.search_web]

    def search_web(self, query):
        """
        Runs a Google search on the input query.

        Args:
            query (str): Input query.

        Returns:
            result (str): Google search results in a natural language format.
        """
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(tools=[grounding_tool])
        return self.client.models.generate_content(
            model=self.model, contents=query, config=config
        ).text
