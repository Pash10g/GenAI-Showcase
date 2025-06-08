# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Any, Optional

from typing_extensions import override
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase
from bson import ObjectId

from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.session import Session
from google.adk.events.event import Event

if TYPE_CHECKING:
    from google.adk.events.event import Event

logger = logging.getLogger(__name__)


# Simple response classes to match the ADK interface
class ListEventsResponse:
    """Response class for list_events method."""
    def __init__(self, events: List[Event] = None):
        self.events = events or []


class ListSessionsResponse:
    """Response class for list_sessions method."""
    def __init__(self, sessions: List[Session] = None):
        self.sessions = sessions or []


class MongoDBSessionService(BaseSessionService):
    """A MongoDB-based session service for ADK agents.
    
    Provides persistent storage for agent sessions and events.
    Built upon the Vertex AI session service pattern.
    """

    def __init__(
        self, 
        mongodb_uri: str = None, 
        database_name: str = "adk_agent_sessions"
    ):
        """
        Initializes a MongoDBSessionService.
        
        Args:
            mongodb_uri: MongoDB connection string. If None, uses MONGODB_URI env var.
            database_name: Name of the database to use for storage.
        """
        self.mongodb_uri = mongodb_uri or os.getenv("MONGODB_URI")
        if not self.mongodb_uri:
            raise ValueError(
                "MongoDB URI must be provided or set in MONGODB_URI environment variable"
            )
        
        self.database_name = database_name
        
        # Initialize MongoDB connection with Motor async client
        self.client: AsyncIOMotorClient = AsyncIOMotorClient(self.mongodb_uri)
        self.db: AsyncIOMotorDatabase = self.client[database_name]
        
        # Collections
        self.sessions: AsyncIOMotorCollection = self.db.sessions
        self.events: AsyncIOMotorCollection = self.db.events
        
        # Note: Index creation should be called separately with await
        logger.info(f"MongoDBSessionService initialized with database: {database_name}")

    async def _create_indexes(self):
        """Create database indexes for optimal performance."""
        try:
            # Sessions indexes
            await self.sessions.create_index([("app_name", 1), ("user_id", 1), ("session_id", 1)], unique=True)
            await self.sessions.create_index([("app_name", 1), ("user_id", 1)])
            await self.sessions.create_index([("created_at", -1)])
            await self.sessions.create_index([("updated_at", -1)])
            
            # Events indexes
            await self.events.create_index([("app_name", 1), ("user_id", 1), ("session_id", 1)])
            await self.events.create_index([("session_id", 1), ("timestamp", 1)])
            await self.events.create_index([("timestamp", -1)])
            
            logger.info("MongoDB session service indexes created successfully")
        except Exception as e:
            logger.warning(f"Error creating session service indexes: {e}")

    @override
    async def create_session(
        self, 
        *, 
        app_name: str, 
        user_id: str, 
        state: Any = None, 
        session_id: str = None
    ) -> Session:
        """
        Creates a new session.
        
        Args:
            app_name: The name of the app.
            user_id: The id of the user.
            state: The initial state of the session.
            session_id: The client-provided id of the session. If not provided, a generated ID will be used.
            
        Returns:
            The newly created session instance.
        """
        if session_id is None:
            session_id = str(uuid.uuid4())
        
        session_doc = {
            "app_name": app_name,
            "user_id": user_id,
            "session_id": session_id,
            "state": self._serialize_state(state),
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "event_count": 0
        }
        
        try:
            # Insert the session document
            result = await self.sessions.insert_one(session_doc)
            
            # Create and return the session object
            session = Session(
                app_name=app_name,
                user_id=user_id,
                id=session_id,
                events=[]
            )
            
            logger.info(f"Created session {session_id} for app {app_name}, user {user_id}")
            return session
            
        except Exception as e:
            logger.error(f"Error creating session: {e}")
            raise

    @override
    async def get_session(
        self, 
        *, 
        app_name: str, 
        user_id: str, 
        session_id: str, 
        config: Any = None
    ) -> Optional[Session]:
        """
        Gets a session.
        
        Args:
            app_name: The name of the app.
            user_id: The id of the user.
            session_id: The id of the session.
            config: Optional configuration.
            
        Returns:
            The session instance or None if not found.
        """
        try:
            # Find the session document
            session_doc = await self.sessions.find_one({
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id
            })
            
            if not session_doc:
                logger.debug(f"Session {session_id} not found for app {app_name}, user {user_id}")
                return None
            
            # Get events for this session
            events = await self._get_session_events(app_name, user_id, session_id)
            
            # Create and return the session object
            session = Session(
                app_name=app_name,
                user_id=user_id,
                id=session_id,
                events=events
            )
            
            logger.debug(f"Retrieved session {session_id} with {len(events)} events")
            return session
            
        except Exception as e:
            logger.error(f"Error getting session: {e}")
            return None

    @override
    async def delete_session(
        self, 
        *, 
        app_name: str, 
        user_id: str, 
        session_id: str
    ) -> None:
        """
        Deletes a session.
        
        Args:
            app_name: The name of the app.
            user_id: The id of the user.
            session_id: The id of the session.
        """
        try:
            # Delete session document
            session_result = await self.sessions.delete_one({
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id
            })
            
            # Delete associated events
            events_result = await self.events.delete_many({
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id
            })
            
            logger.info(
                f"Deleted session {session_id} (sessions: {session_result.deleted_count}, "
                f"events: {events_result.deleted_count})"
            )
            
        except Exception as e:
            logger.error(f"Error deleting session: {e}")
            raise

    @override
    async def list_sessions(
        self, 
        *, 
        app_name: str, 
        user_id: str
    ) -> ListSessionsResponse:
        """
        Lists all the sessions.
        
        Args:
            app_name: The name of the app.
            user_id: The id of the user.
            
        Returns:
            ListSessionsResponse containing the sessions.
        """
        try:
            # Find all sessions for the user
            session_docs = await self.sessions.find({
                "app_name": app_name,
                "user_id": user_id
            }).sort("updated_at", -1).to_list(length=None)
            
            sessions = []
            for doc in session_docs:
                # Get events for each session (limited for performance)
                events = await self._get_session_events(
                    app_name, user_id, doc["session_id"], limit=100
                )
                
                session = Session(
                    app_name=app_name,
                    user_id=user_id,
                    id=doc["session_id"],
                    events=events
                )
                sessions.append(session)
            
            logger.info(f"Listed {len(sessions)} sessions for app {app_name}, user {user_id}")
            return ListSessionsResponse(sessions=sessions)
            
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
            return ListSessionsResponse(sessions=[])

    @override
    async def list_events(
        self, 
        *, 
        app_name: str, 
        user_id: str, 
        session_id: str
    ) -> ListEventsResponse:
        """
        Lists events in a session.
        
        Args:
            app_name: The name of the app.
            user_id: The id of the user.
            session_id: The id of the session.
            
        Returns:
            ListEventsResponse containing the events.
        """
        try:
            events = await self._get_session_events(app_name, user_id, session_id)
            
            logger.debug(f"Listed {len(events)} events for session {session_id}")
            return ListEventsResponse(events=events)
            
        except Exception as e:
            logger.error(f"Error listing events: {e}")
            return ListEventsResponse(events=[])

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        """
        Appends an event to a session object.
        
        Args:
            session: The session to append to.
            event: The event to append.
            
        Returns:
            The appended event.
        """
        try:
            # Serialize the event for storage
            event_doc = {
                "app_name": session.app_name,
                "user_id": session.user_id,
                "session_id": session.id,
                "event_id": str(uuid.uuid4()),
                "content": self._serialize_content(event.content),
                "author": event.author,
                "timestamp": event.timestamp or datetime.now(timezone.utc),
                "created_at": datetime.now(timezone.utc)
            }
            
            # Insert the event
            await self.events.insert_one(event_doc)
            
            # Update session's updated_at timestamp and event count
            await self.sessions.update_one(
                {
                    "app_name": session.app_name,
                    "user_id": session.user_id,
                    "session_id": session.id
                },
                {
                    "$set": {"updated_at": datetime.now(timezone.utc)},
                    "$inc": {"event_count": 1}
                }
            )
            
            # Add event to session's events list
            session.events.append(event)
            
            logger.debug(f"Appended event to session {session.id}")
            return event
            
        except Exception as e:
            logger.error(f"Error appending event: {e}")
            raise

    async def _get_session_events(
        self, 
        app_name: str, 
        user_id: str, 
        session_id: str, 
        limit: int = None
    ) -> List[Event]:
        """
        Get events for a session.
        
        Args:
            app_name: The name of the app.
            user_id: The id of the user.
            session_id: The id of the session.
            limit: Maximum number of events to return.
            
        Returns:
            List of events.
        """
        try:
            query = {
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id
            }
            
            cursor = self.events.find(query).sort("timestamp", 1)
            if limit:
                cursor = cursor.limit(limit)
            
            event_docs = await cursor.to_list(length=None)
            
            events = []
            for doc in event_docs:
                event = Event(
                    content=self._deserialize_content(doc.get("content")),
                    author=doc.get("author"),
                    timestamp=doc.get("timestamp")
                )
                events.append(event)
            
            return events
            
        except Exception as e:
            logger.error(f"Error getting session events: {e}")
            return []

    def _serialize_content(self, content) -> Dict[str, Any]:
        """Serialize content object to dictionary for MongoDB storage."""
        if not content:
            return {}
        
        serialized = {}
        if hasattr(content, 'parts') and content.parts:
            serialized['parts'] = []
            for part in content.parts:
                part_data = {}
                if hasattr(part, 'text') and part.text:
                    part_data['text'] = part.text
                if hasattr(part, 'function_call') and part.function_call:
                    part_data['function_call'] = str(part.function_call)
                if hasattr(part, 'function_response') and part.function_response:
                    part_data['function_response'] = str(part.function_response)
                serialized['parts'].append(part_data)
        
        return serialized

    def _deserialize_content(self, content_dict: Dict[str, Any]):
        """Deserialize content dictionary back to content object."""
        from google.genai import types
        
        if not content_dict or 'parts' not in content_dict:
            return None
        
        parts = []
        for part_data in content_dict['parts']:
            if 'text' in part_data:
                parts.append(types.Part(text=part_data['text']))
        
        return types.Content(parts=parts) if parts else None

    def _serialize_state(self, state: Any) -> Dict[str, Any]:
        """Serialize session state for storage."""
        if state is None:
            return {}
        
        # Simple serialization - in production, you might want more sophisticated handling
        if isinstance(state, dict):
            return state
        elif hasattr(state, '__dict__'):
            return state.__dict__
        else:
            return {"value": str(state)}

    def close(self):
        """Close the MongoDB connection."""
        if hasattr(self, 'client') and self.client:
            self.client.close()
            logger.info("MongoDB session service connection closed")

    def __del__(self):
        """Cleanup when object is destroyed."""
        self.close()


# Utility function for easy instantiation
def create_mongodb_session_service(
    mongodb_uri: str = None,
    database_name: str = "adk_agent_sessions"
) -> MongoDBSessionService:
    """
    Create a MongoDB session service instance.
    
    Args:
        mongodb_uri: MongoDB connection string
        database_name: Database name to use
        
    Returns:
        MongoDBSessionService instance
    """
    return MongoDBSessionService(
        mongodb_uri=mongodb_uri,
        database_name=database_name
    )
