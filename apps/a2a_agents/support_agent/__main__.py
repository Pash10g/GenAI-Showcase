import logging
import os

import sys
from pathlib import Path

# Add the project root to the Python path
# This allows for absolute imports, making the script runnable from any location
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root))

import click
import uvicorn

from adk_agent import create_agent
from adk_agent_executor import ADKAgentExecutor
from dotenv import load_dotenv
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from starlette.routing import Route
from apps.a2a_agents.mongodb.mongodb_session_service import MongoDBSessionService
from apps.a2a_agents.mongodb.mongodb_memory_service import MongoDBMemoryService
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from starlette.applications import Starlette


load_dotenv()

logging.basicConfig()


@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=10001)
def main(host: str, port: int):
    # Verify an API key is set.
    # Not required if using Vertex AI APIs.
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI") != "TRUE" and not os.getenv(
        "GOOGLE_API_KEY"
    ):
        raise ValueError(
            "GOOGLE_API_KEY environment variable not set and "
            "GOOGLE_GENAI_USE_VERTEXAI is not TRUE."
        )

    # skill = AgentSkill(
    #     id="Notion Search",
    #     name="Notion Search",
    #     description="Help with searching Notion",
    #     tags=["notion"],
    #     examples=["search for meeting notes", "find project documentation"],
    # )

    agent_card = AgentCard(
        name="Support Agent",
        description="An agent that can assist with various support tasks on Biggly Bobsy watches.",
        url=f"http://{host}:{port}/",
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
    )

    adk_agent = create_agent()
    runner = Runner(
        app_name=agent_card.name,
        agent=adk_agent,
        artifact_service=InMemoryArtifactService(),
        session_service=MongoDBSessionService(database_name="agent_memory"),
        memory_service=MongoDBMemoryService(database_name="agent_memory"),
    )
    agent_executor = ADKAgentExecutor(runner, agent_card)

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor, task_store=InMemoryTaskStore()
    )

    a2a_app = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    uvicorn.run(a2a_app.build(), host=host, port=port)


if __name__ == "__main__":
    main()
