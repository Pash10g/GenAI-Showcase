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
import re
import logging
import certifi
import vertexai
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Any, Optional

from typing_extensions import override
from pymongo import MongoClient
from pymongo.collection import Collection
from bson import ObjectId
from vertexai.language_models import TextEmbeddingModel

from google.adk.memory.base_memory_service import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.memory import _utils

if TYPE_CHECKING:
    from google.adk.events.event import Event
    from google.adk.sessions.session import Session

logger = logging.getLogger(__name__)


def _user_key(app_name: str, user_id: str):
    return f'{app_name}/{user_id}'


def _extract_words_lower(text: str) -> set[str]:
    """Extracts words from a string and converts them to lowercase."""
    return set([word.lower() for word in re.findall(r'[A-Za-z]+', text)])


class MongoDBMemoryService(BaseMemoryService):
    """A MongoDB-based memory service for ADK agents.
    
    Provides persistent storage for agent sessions and supports semantic search
    through keyword matching. Built upon the Vertex AI RAG memory service pattern.
    """

    def __init__(
        self,
        mongodb_uri: str = None,
        database_name: str = "adk_agent_memory",
        similarity_top_k: int = 10,
        vector_similarity_threshold: float = 0.5,
        vector_index_name: str = "vector_index",
        embedding_dimensions: int = 768,
    ):
        """
        Initializes a MongoDBMemoryService.
        Args:
            mongodb_uri: MongoDB connection string. If None, uses MONGODB_URI env var.
            database_name: Name of the database to use for storage.
            similarity_top_k: The number of contexts to retrieve during search.
            vector_similarity_threshold: Threshold for similarity matching (0.0-1.0).
            vector_index_name: The name of the vector search index in MongoDB.
            embedding_dimensions: The dimensionality of the embeddings.
        """
        self.mongodb_uri = mongodb_uri or os.getenv("MONGODB_URI")
        if not self.mongodb_uri:
            raise ValueError(
                "MongoDB URI must be provided or set in MONGODB_URI environment variable"
            )

        self.database_name = database_name
        self.similarity_top_k = similarity_top_k
        self.vector_similarity_threshold = vector_similarity_threshold
        self.vector_index_name = vector_index_name
        self.embedding_dimensions = embedding_dimensions

        # Initialize MongoDB connection
        self.client = MongoClient(self.mongodb_uri, tlsCAFile=certifi.where())
        self.db = self.client[database_name]

        # Initialize Vertex AI
        vertexai.init()
        self.embedding_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        
        # Collections
        self.session_events: Collection = self.db.session_events
        self.memory_entries: Collection = self.db.memory_entries
        
        # Create indexes for better performance
        self._create_indexes()
        
        logger.info(f"MongoDBMemoryService initialized with database: {database_name}")

    def _create_indexes(self):
        """Create database indexes for optimal performance."""
        try:
            # Session events indexes
            self.session_events.create_index([("user_key", 1), ("session_id", 1)])
            self.session_events.create_index([("app_name", 1), ("user_id", 1)])
            self.session_events.create_index([("timestamp", -1)])

            # Memory entries indexes for search
            self.memory_entries.create_index([("user_key", 1)])
            self.memory_entries.create_index([("app_name", 1), ("user_id", 1)])
            self.memory_entries.create_index([("timestamp", -1)])

            self._create_vector_search_index()

            logger.info("MongoDB indexes created successfully")
        except Exception as e:
            logger.warning(f"Error creating indexes: {e}")

    def _create_vector_search_index(self):
        """Creates the vector search index if it doesn't exist."""
        try:
            # Define the vector search index
            index_model = {
    "name": self.vector_index_name,
    "type": "vectorSearch",
    "definition": {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": self.embedding_dimensions,
                "similarity": "cosine",
            }
        ]
    },
}
            
            self.memory_entries.create_search_index(model=index_model)
            
        except Exception as e:
            logger.error(f"Error creating vector search index: {e}")

    @override
    async def add_session_to_memory(self, session: Session):
        """
        Adds a session to the memory service.
        
        A session may be added multiple times during its lifetime.
        
        Args:
            session: The session to add.
        """
        user_key = _user_key(session.app_name, session.user_id)
        
        # Filter events with content and parts
        filtered_events = [
            event for event in session.events
            if event.content and event.content.parts
        ]
        
        if not filtered_events:
            logger.debug(f"No events with content found for session {session.id}")
            return
        
        # Store session events
        session_doc = {
            "user_key": user_key,
            "app_name": session.app_name,
            "user_id": session.user_id,
            "session_id": session.id,
            "events": [],
            "timestamp": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        
        # Process each event and create memory entries
        memory_entries = []
        
        for event in filtered_events:
            # Store event data
            event_data = {
                "content": self._serialize_content(event.content),
                "author": event.author,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "event_id": getattr(event, 'id', None)
            }
            session_doc["events"].append(event_data)
            
            # Extract text content for keyword indexing
            text_content = ' '.join([
                part.text for part in event.content.parts 
                if part.text
            ])
            
            if text_content:
                # Generate embedding for the text content
                embedding = self._generate_embeddings(text_content)

                memory_entry = {
                    "user_key": user_key,
                    "app_name": session.app_name,
                    "user_id": session.user_id,
                    "session_id": session.id,
                    "content": self._serialize_content(event.content),
                    "author": event.author,
                    "timestamp": datetime.now(timezone.utc),
                    "text_content": text_content,
                    "embedding": embedding,
                    "event_timestamp": event.timestamp.isoformat() if event.timestamp else None,
                }
                memory_entries.append(memory_entry)
        
        try:
            # Upsert session document
            self.session_events.replace_one(
                {"user_key": user_key, "session_id": session.id},
                session_doc,
                upsert=True
            )
            
            # Insert memory entries (remove existing ones for this session first)
            if memory_entries:
                self.memory_entries.delete_many({
                    "user_key": user_key,
                    "session_id": session.id
                })
                self.memory_entries.insert_many(memory_entries)
            
            logger.info(
                f"Added session {session.id} to memory for {user_key} "
                f"with {len(memory_entries)} memory entries"
            )
            
        except Exception as e:
            logger.error(f"Error adding session to memory: {e}")
            raise

    @override
    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """
        Searches for sessions that match the query using vector-based semantic search.
        Args:
            app_name: The application name to search within.
            user_id: The user ID to search for.
            query: The search query string.
        Returns:
            SearchMemoryResponse containing matching memory entries.
        """
        user_key = _user_key(app_name, user_id)

        if not query:
            return SearchMemoryResponse()

        try:
            # Generate embedding for the query
            query_embedding = self._generate_embeddings(query)

            # Define the aggregation pipeline for vector search
            pipeline = [
                {
                    "$vectorSearch": {
                        "index": self.vector_index_name,
                        "path": "embedding",
                        "queryVector": query_embedding,
                        "numCandidates": self.similarity_top_k * 10,  # Oversample for better accuracy
                        "limit": self.similarity_top_k,
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "content": 1,
                        "author": 1,
                        "timestamp": 1,
                        "event_timestamp": 1,
                        "score": {"$meta": "vectorSearchScore"},
                    }
                },
            ]

            # Execute the aggregation pipeline
            cursor = self.memory_entries.aggregate(pipeline)
            results = list(cursor)

            # Convert results to MemoryEntry objects
            response = SearchMemoryResponse()

            for result in results:
                # Check if the similarity score meets the threshold
                if result["score"] >= self.vector_similarity_threshold:
                    memory_entry = MemoryEntry(
                        content=self._deserialize_content(result["content"]),
                        author=result["author"],
                        timestamp=result.get("event_timestamp")
                        or result["timestamp"].isoformat(),
                    )
                    response.memories.append(memory_entry)

            logger.info(
                f"Found {len(response.memories)} memory entries for query '{query}' "
                f"for user {user_key}"
            )

            return response

        except Exception as e:
            logger.error(f"Error searching memory: {e}")
            return SearchMemoryResponse()

    def _generate_embeddings(self, text: str) -> list[float]:
        """Generates embeddings for a given text."""
        try:
            result = self.embedding_model.get_embeddings([text])
            return result[0].values
        except Exception as e:
            logger.error(f"Error generating embeddings: {e}")
            return []

    def _serialize_content(self, content) -> Dict[str, Any]:
        """Serialize content object to dictionary for MongoDB storage."""
        if not content:
            return {}

        serialized = {}
        if hasattr(content, "parts") and content.parts:
            serialized["parts"] = []
            for part in content.parts:
                part_data = {}
                if hasattr(part, "text") and part.text:
                    part_data["text"] = part.text
                if hasattr(part, "function_call") and part.function_call:
                    part_data["function_call"] = str(part.function_call)
                if hasattr(part, "function_response") and part.function_response:
                    part_data["function_response"] = str(part.function_response)
                serialized["parts"].append(part_data)

        return serialized

    def _deserialize_content(self, content_dict: Dict[str, Any]):
        """Deserialize content dictionary back to content object."""
        # This is a simplified deserialization - in a full implementation,
        # you'd want to properly reconstruct the original content objects
        from google.genai import types
        
        if not content_dict or 'parts' not in content_dict:
            return None
        
        parts = []
        for part_data in content_dict['parts']:
            if 'text' in part_data:
                parts.append(types.Part(text=part_data['text']))
        
        return types.Content(parts=parts) if parts else None

    def close(self):
        """Close the MongoDB connection."""
        if hasattr(self, 'client') and self.client:
            self.client.close()
            logger.info("MongoDB connection closed")

    def __del__(self):
        """Cleanup when object is destroyed."""
        self.close()


# Utility function for easy instantiation
def create_mongodb_memory_service(
    mongodb_uri: str = None,
    database_name: str = "adk_agent_memory",
    similarity_top_k: int = 10,
    vector_similarity_threshold: float = 0.5,
    vector_index_name: str = "vector_index",
    embedding_dimensions: int = 768,
) -> MongoDBMemoryService:
    """
    Create a MongoDB memory service instance.
    Args:
        mongodb_uri: MongoDB connection string
        database_name: Database name to use
        similarity_top_k: Number of results to return
        vector_similarity_threshold: Similarity threshold
        vector_index_name: The name of the vector search index in MongoDB.
        embedding_dimensions: The dimensionality of the embeddings.
    Returns:
        MongoDBMemoryService instance
    """
    return MongoDBMemoryService(
        mongodb_uri=mongodb_uri,
        database_name=database_name,
        similarity_top_k=similarity_top_k,
        vector_similarity_threshold=vector_similarity_threshold,
        vector_index_name=vector_index_name,
        embedding_dimensions=embedding_dimensions,
    )
