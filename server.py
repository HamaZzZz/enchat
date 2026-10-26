import asyncio
import os
import websockets
import json

# Map user_id -> websocket connection
connected_clients = {}
# Map user_id -> public key pem string
client_public_keys = {}
# Map user_id -> list of missed message dicts while offline
offline_queues = {}
# group_name -> set of websockets
groups = {}

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

                # Deliver any offline/missed messages immediately upon connection
                if current_user_id in offline_queues and offline_queues[current_user_id]:
                    print(f"[Server] Flushing {len(offline_queues[current_user_id])} queued messages to {current_user_id}")
                    for missed_msg in offline_queues[current_user_id]:
                        await websocket.send(json.dumps(missed_msg))
                    offline_queues[current_user_id] = []

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
                    # Recipient is online, send immediately
                    recipient_ws = connected_clients[recipient_id]
                    await recipient_ws.send(json.dumps(data))
                    print(f"[Server] Relayed message from {data.get('from')} to {recipient_id}")
                else:
                    # Recipient is offline, queue the message for later
                    if recipient_id not in offline_queues:
                        offline_queues[recipient_id] = []
                    offline_queues[recipient_id].append(data)
                    print(f"[Server] Recipient {recipient_id} offline. Message queued.")

            elif msg_type == "join_group":
                group_name = data.get("group")
                if group_name:
                    if group_name not in groups:
                        groups[group_name] = set()
                    groups[group_name].add(websocket)
                    print(f"[Server] User joined group: {group_name}")

           elif msg_type == "group_message":
                group_name = data.get("group")
                sender = data.get("from")
                payload = data.get("payload")
                g_name_meta = data.get("group_name")

                # Broadcast to all other members in the group room
                if group_name in groups:
                    disconnected = set()
                    for client_ws in groups[group_name]:
                        if client_ws != websocket:  # Don't echo back to sender
                            try:
                                await client_ws.send(json.dumps({
                                    "type": "group_message",
                                    "group": group_name,
                                    "from": sender,
                                    "payload": payload,
                                    "group_name": g_name_meta
                                }))
                            except Exception:
                                disconnected.add(client_ws)
                    # Clean up dead connections
                    for client_ws in disconnected:
                        groups[group_name].remove(client_ws)
                    print(f"[Server] Relayed group message in {group_name} from {sender}")

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if current_user_id and current_user_id in connected_clients:
            if connected_clients[current_user_id] == websocket:
                del connected_clients[current_user_id]
                print(f"[Server] User disconnected: {current_user_id}")

        # Clean up from group rooms
        for g_name in list(groups.keys()):
            if websocket in groups[g_name]:
                groups[g_name].remove(websocket)

async def main():
    port = int(os.environ.get("PORT", 8765))
    async with websockets.serve(relay_handler, "0.0.0.0", port):
        print(f"EnChat Blind Relay Server running on port {port}")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
