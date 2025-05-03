#!/usr/bin/env python3

import sys
import json
import requests
import os
import logging

ANKICONNECT_URL = os.getenv("ANKICONNECT_URL", "http://127.0.0.1:8765")
LOG_LEVEL = os.getenv("MCP_LOG_LEVEL", "INFO").upper()

logging.basicConfig(level=LOG_LEVEL, stream=sys.stderr,
                    format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('anki_mcp_server')

def invoke_anki_connect(action, params=None):
    """Helper function to call AnkiConnect HTTP API."""
    payload = json.dumps({"action": action, "version": 6, "params": params or {}})
    headers = {'Content-Type': 'application/json'}
    log.debug(f"Calling AnkiConnect action '{action}' with params: {params}")
    try:
        response = requests.post(ANKICONNECT_URL, headers=headers, data=payload, timeout=10) # 10 sec timeout
        response.raise_for_status() # Raise HTTP errors (4xx, 5xx)

        response_json = response.json()
        log.debug(f"AnkiConnect response: {response_json}")

        if response_json.get("error") is not None:
            log.error(f"AnkiConnect returned error for action '{action}': {response_json['error']}")
            return None, response_json["error"] # Return error message
        else:
            log.info(f"AnkiConnect action '{action}' successful.")
            return response_json.get("result"), None # Return result
    except requests.exceptions.ConnectionError:
        error_msg = f"Connection Error: Could not connect to AnkiConnect at {ANKICONNECT_URL}. Is Anki running with the AnkiConnect add-on enabled and listening?"
        log.exception(error_msg) # Log full traceback
        return None, error_msg
    except requests.exceptions.Timeout:
         error_msg = f"Timeout Error: Connection to AnkiConnect at {ANKICONNECT_URL} timed out."
         log.exception(error_msg)
         return None, error_msg
    except requests.exceptions.RequestException as e:
        error_msg = f"AnkiConnect Request Error: {e}"
        log.exception(error_msg)
        return None, error_msg
    except json.JSONDecodeError:
         error_msg = f"AnkiConnect Invalid Response: Could not decode JSON. Response: {response.text[:200]}" # Log start of bad response
         log.exception(error_msg)
         return None, error_msg
    except Exception as e:
         # Catch-all for other unexpected errors during AnkiConnect call
         error_msg = f"AnkiConnect Unknown Error: {e}"
         log.exception(error_msg)
         return None, error_msg


def handle_list_tools():
    """Defines the tools this server offers via MCP."""
    log.info("Handling listTools request")
    # Define a tool that closely matches the AnkiConnect 'addNote' action
    return {
        "tools": [
            {
                "name": "add_note",
                "description": "Adds a note to Anki using specified deck, model, fields, tags, and options. Matches AnkiConnect's addNote action structure.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "object",
                            "properties": {
                                 "deckName": {"type": "string", "description": "Name of the target deck (e.g., 'Default')."},
                                 "modelName": {"type": "string", "description": "Name of the note type (e.g., 'Basic', 'Cloze')."},
                                 "fields": {"type": "object", "description": "Object mapping field names to content (e.g., {'Front': 'Q', 'Back': 'A'} or {'Text': 'Cloze {{c1::text}}', 'Extra': 'Context'})."},
                                 "options": {"type": "object", "description": "Optional object with 'allowDuplicate': boolean and potentially 'duplicateScope'/'duplicateScopeOptions'.", "properties": {"allowDuplicate": {"type": "boolean"}}, "required": []},
                                 "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional list of tags."},
                             },
                             "required": ["deckName", "modelName", "fields"] # Core requirements for addNote
                         }
                    },
                     "required": ["note"] # The top-level argument must contain 'note'
                }
            }
        ]
    }

def handle_call_tool(params):
    """Handles incoming MCP callTool requests."""
    tool_name = params.get("name")
    args = params.get("arguments", {})
    log.info(f"Handling callTool request for tool: '{tool_name}'")
    log.debug(f"Tool arguments received: {args}")

    if tool_name == "add_note":
        # Expect arguments structure: {"note": {"deckName": ..., "modelName": ..., ...}}
        note_data = args.get("note")
        if not note_data or not isinstance(note_data, dict):
             log.error("'note' object missing or invalid in arguments for add_note.")
             return None, {"code": -32602, "message": "Invalid params: 'note' object missing or invalid in arguments."}

        # Basic validation of required fields within 'note' object
        required_note_keys = ["deckName", "modelName", "fields"]
        missing_keys = [key for key in required_note_keys if key not in note_data]
        if missing_keys:
            error_msg = f"Invalid params: Missing required keys in 'note' object: {', '.join(missing_keys)}"
            log.error(error_msg)
            return None, {"code": -32602, "message": error_msg}
        if not isinstance(note_data["fields"], dict):
             error_msg = "Invalid params: 'fields' value must be an object."
             log.error(error_msg)
             return None, {"code": -32602, "message": error_msg}
        # Optional: Add more validation for types (deckName/modelName are strings, tags is list etc.)

        # Call AnkiConnect 'addNote' action with the provided 'note' data
        result, error = invoke_anki_connect("addNote", {"note": note_data})

        if error:
            # AnkiConnect call failed, format error for MCP response
            log.error(f"AnkiConnect addNote failed: {error}")
            # Use a generic MCP error code for application-level errors
            return None, {"code": -31000, "message": f"AnkiConnect Error: {error}", "data": {"ankiError": str(error)}}
        else:
            # AnkiConnect call succeeded, format result for MCP response
            # AnkiConnect returns the new note ID as the result
            log.info(f"AnkiConnect addNote successful, new note ID: {result}")
            success_message = f"Successfully added note with ID: {result}"
            # MCP result structure typically involves 'content'
            return {"content": [{"type": "text", "text": success_message}]}, None
    else:
        # Tool name doesn't match known tools
        error_msg = f"Method not found: Unknown tool name '{tool_name}'"
        log.warning(error_msg)
        return None, {"code": -32601, "message": error_msg}

# --- Main Stdio Loop ---
def main():
    """Main loop to read stdin, process MCP requests, write stdout."""
    log.info("Python Anki MCP Server starting. Listening on stdin...")
    log.info(f"Attempting to connect to AnkiConnect at: {ANKICONNECT_URL}")
    for line in sys.stdin:
        log.debug(f"Received line from stdin: {line.strip()}")
        request = None
        # Default response structure, ID must be set if request parsing succeeds
        response = {"jsonrpc": "2.0", "id": None}
        try:
            request = json.loads(line)
            req_id = request.get("id")
            response["id"] = req_id # Echo back the request ID

            method = request.get("method")
            params = request.get("params", {})

            if not method:
                 raise ValueError("Request object missing 'method' field.")

            log.info(f"Processing MCP request: ID={req_id}, Method='{method}'")

            # --- Route request based on method ---
            if method == "listTools":
                response["result"] = handle_list_tools()
                log.info("Successfully processed listTools.")
            elif method == "callTool":
                result, error = handle_call_tool(params)
                if error:
                    response["error"] = error
                    log.warning(f"callTool resulted in error: {error}")
                else:
                    response["result"] = result
                    log.info(f"Successfully processed callTool '{params.get('name')}'.")
            else:
                # Unknown MCP method
                log.warning(f"Received unknown MCP method: '{method}'")
                response["error"] = {"code": -32601, "message": f"Method not found: Unknown method '{method}'"}

        except json.JSONDecodeError as e:
            log.exception("JSON Parse Error processing stdin line.")
            response["error"] = {"code": -32700, "message": f"Parse error: Invalid JSON received. Error: {e}"}
            # ID might be unknown if JSON is malformed, setting to null is convention
            response["id"] = None
        except Exception as e:
            # Catch any other unexpected errors during request processing
            log.exception("Internal error processing MCP request.")
            response["error"] = {"code": -32603, "message": f"Internal error: {e}", "data": str(e)}
            # Try to preserve ID if it was parsed before the error
            if request: response["id"] = request.get("id")

        # --- Send Response ---
        try:
            response_str = json.dumps(response)
            log.debug(f"Sending response to stdout: {response_str}")
            sys.stdout.write(response_str + "\n")
            sys.stdout.flush() # Ensure the response is sent immediately
        except Exception as e:
             # This usually means the client closed the pipe
             log.exception(f"Error writing response to stdout (client likely disconnected): {e}")
             break # Exit the loop if we can't write output

    log.info("Stdin closed or write error. Python Anki MCP Server shutting down.")

if __name__ == "__main__":
    main()
