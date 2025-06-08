
from google.adk.agents import LlmAgent
#from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioServerParameters
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, SseServerParams
from dotenv import load_dotenv

load_dotenv() 

import os


def create_agent() -> LlmAgent:
    """Constructs the ADK agent."""
    return LlmAgent(
        model="gemini-2.5-pro-preview-06-05",
        name="support_agent",
        description="An agent for calendar scheduling queries and calendar event creation.",
        instruction=f"""
You are a specialized support assistant for scheduling calendar events. Your primary function is to utilize the provided tools to retrieve and relay scheduling information in response to user queries. You must rely exclusively on these tools for data and refrain from inventing information. Ensure that all responses include the detailed output from the tools used and are formatted in Markdown.

Each calendar event you create should include the following details:
- Title: A brief title for the event.
- Description: A detailed description of the event.
- Name : The name of the person or entity associated with the event.
- Phone Number: The contact number for the person or entity.
        """,
        tools=[
            MCPToolset(
                connection_params=SseServerParams(
                     url=os.getenv("MEETING_SCHEDULE_MCP","http://localhost:8001/sse")
            ))
        ],
    )
