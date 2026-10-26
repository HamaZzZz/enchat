import asyncio
import os
import websockets
import json

# Map user_id -> websocket connection
connected_clients = {}
# Map user_id -> public key pem string
client_public_keys = {}

async def relay_handler(websocket):
    current_user_id = None
    try:
        async for raw_msg in websocket:
            data = json.loads(raw_msg)
            msg_type = data.get("type")

            if msg_type == "register":
                current_user_id = data.get("user_id")
                pub_key = data.get("public_key")
                connected_clients[current_user_id] = websocket
                if pub_key:
                    client_public_keys[current_user_id] = pub_key
                print(f"[Server] User registered: {current_user_id}")

            elif msg_type == "get_pubkey":
                target_id = data.get("target_id")
                if target_id in client_public_keys:
                    await websocket.send(json.dumps({
                        "type": "pubkey_response",
                        "target_id": target_id,
                        "public_key": client_public_keys[target_id]
                    }))
                else:
                    await websocket.send(json.dumps({
                        "type": "pubkey_response",
                        "target_id": target_id,
                        "public_key": None
                    }))

            elif msg_type == "message":
                recipient_id = data.get("to")
                if recipient_id in connected_clients:
                    recipient_ws = connected_clients[recipient_id]
                    await recipient_ws.send(json.dumps(data))
                    print(f"[Server] Relayed message from {data.get('from')} to {recipient_id}")
                else:
                    print(f"[Server] Recipient {recipient_id} not connected.")

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if current_user_id and current_user_id in connected_clients:
            del connected_clients[current_user_id]
            print(f"[Server] User disconnected: {current_user_id}")

async def main():
    # Bind to 0.0.0.0 and use Render's dynamic PORT environment variable (default to 8765 locally)
    port = int(os.environ.get("PORT", 8765))
    async with websockets.serve(relay_handler, "0.0.0.0", port):
        print(f"EnChat Blind Relay Server running on port {port}")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
