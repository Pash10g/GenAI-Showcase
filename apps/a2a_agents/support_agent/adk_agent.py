
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
        description="An agent for Smar Watch support queries",
        instruction=f"""You are a specialized support assistant for smart watches. Your primary function is to utilize the provided tools to retrieve and relay support information in response to user queries. You must rely exclusively on these tools for data and refrain from inventing information. Ensure that all responses include the detailed output from the tools used and are formatted in Markdown.
        
        You have a notion searching TOOLS to get information about the Biggly Bobsy Smart Kids' Watch. You can use this tool to search for information about the watch's features, pricing, and other details.
        
        Kbowledge Base: 

        Biggly Bobsy Smart Kids' Watch - Pricing Guide
Biggly Bobsy Basic
Price: $49.99
Features:
- Voice Activation (Double-Tap)
- Digital Clock Display
- Step Counter
- 1 Year Warranty
Biggly Bobsy Explorer
Price: $69.99
Features:
- All Basic Features
- GPS Tracking
- 2 Learning Games
- Water-Resistant Design
Biggly Bobsy Hero
Price: $99.99
Features:
- All Explorer Features
- Call & Message Support
- Interactive AI Assistant
- Free Strap Customization

        
        """
    )
