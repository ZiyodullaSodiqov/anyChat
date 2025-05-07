from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
import string
from typing import Dict, List
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
import urllib.parse

app = FastAPI()

# MongoDB Atlas connection settings
# Properly encode the username and password
username = urllib.parse.quote_plus("ziyodullasodiqov01")
password = urllib.parse.quote_plus("HZL53G_Cgni3NT3")
MONGODB_URL = f"mongodb+srv://{username}:{password}@cluster0.vfh7g.mongodb.net/chat_app?retryWrites=true&w=majority"

# Initialize MongoDB client
client = AsyncIOMotorClient(MONGODB_URL)
db = client.get_database()
chats_collection = db["chats"]
messages_collection = db["messages"]

# Allow CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CreateChatResponse(BaseModel):
    chat_id: str

class JoinChatRequest(BaseModel):
    chat_id: str
    name: str

class Message(BaseModel):
    sender: str
    content: str
    timestamp: str
    chat_id: str

def generate_chat_id(length=6):
    """Generate a random chat ID like A12V4A or AAA123"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

@app.on_event("startup")
async def startup_db_client():
    try:
        # MongoDB connection check
        await client.admin.command('ping')
        print("Successfully connected to MongoDB Atlas!")
        
        # Create indexes if they don't exist
        await chats_collection.create_index("chat_id", unique=True)
        await messages_collection.create_index("chat_id")
        await messages_collection.create_index("timestamp")
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

@app.post("/create-chat", response_model=CreateChatResponse)
async def create_chat():
    chat_id = generate_chat_id()
    
    # Check if chat ID already exists
    if await chats_collection.find_one({"chat_id": chat_id}):
        raise HTTPException(status_code=400, detail="Chat ID already exists")
    
    # Create new chat
    new_chat = {
        "chat_id": chat_id,
        "created_at": datetime.now(),
        "active_users": 0,
        "participants": []
    }
    
    try:
        await chats_collection.insert_one(new_chat)
        return {"chat_id": chat_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating chat: {str(e)}")

@app.post("/join-chat")
async def join_chat(request: JoinChatRequest):
    # Check if chat exists
    chat = await chats_collection.find_one({"chat_id": request.chat_id})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    try:
        # Update active users and add participant
        await chats_collection.update_one(
            {"chat_id": request.chat_id},
            {
                "$inc": {"active_users": 1},
                "$addToSet": {"participants": request.name}
            }
        )
        return {"status": "success", "message": f"Welcome to chat {request.chat_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error joining chat: {str(e)}")

@app.get("/chat/{chat_id}/messages")
async def get_messages(chat_id: str, limit: int = 100):
    # Check if chat exists
    chat = await chats_collection.find_one({"chat_id": chat_id})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    try:
        # Get messages (newest first)
        messages = await messages_collection.find(
            {"chat_id": chat_id}
        ).sort("timestamp", -1).limit(limit).to_list(limit)
        
        # Convert ObjectId to string and format timestamp
        for message in messages:
            message["_id"] = str(message["_id"])
            message["timestamp"] = message["timestamp"].isoformat()
        
        return messages[::-1]  # Return oldest first
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving messages: {str(e)}")

@app.websocket("/ws/{chat_id}")
async def websocket_endpoint(websocket: WebSocket, chat_id: str, name: str):
    # Check if chat exists
    chat = await chats_collection.find_one({"chat_id": chat_id})
    if not chat:
        await websocket.close(code=1008, reason="Chat not found")
        return

    await websocket.accept()
    
    # Join notification
    join_message = {
        "sender": "System",
        "content": f"{name} joined the chat",
        "timestamp": datetime.now(),
        "chat_id": chat_id
    }
    
    try:
        # Save join message
        await messages_collection.insert_one(join_message)
        
        # Send join notification
        await websocket.send_json({
            "sender": join_message["sender"],
            "content": join_message["content"],
            "timestamp": join_message["timestamp"].isoformat()
        })
    except Exception as e:
        print(f"Error sending join message: {e}")

    try:
        while True:
            data = await websocket.receive_text()
            
            # Create message
            message = {
                "sender": name,
                "content": data,
                "timestamp": datetime.now(),
                "chat_id": chat_id
            }
            
            try:
                # Save message to database
                await messages_collection.insert_one(message)
                
                # Send message back to sender (echo)
                await websocket.send_json({
                    "sender": message["sender"],
                    "content": message["content"],
                    "timestamp": message["timestamp"].isoformat()
                })
            except Exception as e:
                print(f"Error processing message: {e}")
                
    except WebSocketDisconnect:
        # Leave notification
        leave_message = {
            "sender": "System",
            "content": f"{name} left the chat",
            "timestamp": datetime.now(),
            "chat_id": chat_id
        }
        
        try:
            # Save leave message
            await messages_collection.insert_one(leave_message)
            
            # Update active users count
            await chats_collection.update_one(
                {"chat_id": chat_id},
                {"$inc": {"active_users": -1}}
            )
        except Exception as e:
            print(f"Error handling disconnect: {e}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        timeout_keep_alive=60
    )