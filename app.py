# --- Existing Imports ---
from flask import Flask, request, render_template, redirect, url_for, session, flash
from google import genai
from google.genai import types
import requests
import json
import os
import subprocess
import time
import sys # To potentially get python executable path
from bs4 import BeautifulSoup
import validators

app = Flask(__name__)

# === Configure Flask-Session ===
SESSION_TYPE = 'filesystem'
SESSION_FILE_DIR = os.path.join(os.path.dirname(__file__), 'flask_session')
SESSION_PERMANENT = False # Session expires when browser closes
SESSION_USE_SIGNER = True # Encrypt session ID cookie
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'who-gives-a-shit')
app.config['SESSION_TYPE'] = SESSION_TYPE
app.config['SESSION_FILE_DIR'] = SESSION_FILE_DIR
app.config['SESSION_PERMANENT'] = SESSION_PERMANENT
app.config['SESSION_USE_SIGNER'] = SESSION_USE_SIGNER
app.config.from_object(__name__)

# --- Configuration ---
API_KEY = os.getenv("GOOGLE_API_KEY")
if not API_KEY: print("ERROR: GOOGLE_API_KEY environment variable not set.")

MCP_SERVER_SCRIPT_PATH = os.getenv("MCP_SERVER_SCRIPT_PATH", "anki-mcp-server.py")
PYTHON_EXECUTABLE_PATH = os.getenv("PYTHON_EXECUTABLE_PATH", sys.executable)

GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")
client = None
try:
    client = genai.Client()
    print(f"Successfully initialized google.genai Client.")
except Exception as e:
    print(f"ERROR: Failed to initialize google.genai Client: {e}")
    client = None

# --- Anki Configuration (Now more relevant again) ---
DEFAULT_DECK = os.getenv("ANKI_DEFAULT_DECK", "Default")
DEFAULT_MODEL = os.getenv("ANKI_DEFAULT_MODEL", "Cloze") # Defaulting back to Cloze


# --- Add this new helper function ---
def fetch_and_extract_text(url):
    """Fetches content from a URL and extracts text using BeautifulSoup."""
    print(f"Attempting to fetch and extract text from URL: {url}")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'} # Pretend to be a browser
    try:
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status() # Raise error for bad status codes (4xx, 5xx)

        content_type = response.headers.get('content-type', '').lower()
        print(f"URL returned Content-Type: {content_type}")

        if 'html' not in content_type:
            # Handle non-HTML content - maybe try basic text extraction or just fail
            print(f"Warning: Content type is not HTML ({content_type}). Attempting basic text extraction.")
            # For simplicity, let's return the raw text if not too large, otherwise error
            if len(response.text) > 500000: # Limit size for non-HTML
                 return None, "Content is not HTML and is too large to process directly."
            if not response.text.strip():
                 return None, "Content is not HTML and appears empty."
            return response.text, None # Return raw text for non-HTML for now

        soup = BeautifulSoup(response.text, 'html.parser') # Use built-in parser

        for script_or_style in soup(["script", "style", "header", "footer", "nav", "aside"]):
            script_or_style.decompose()

        main_content = soup.find('article') or soup.find('main') or soup.find('div', id='content') or soup.find('div', class_='content') or soup.find('body')

        if main_content:
             text = main_content.get_text(separator='\n', strip=True)
        else: # Fallback to whole body if no main container found
             text = soup.get_text(separator='\n', strip=True)

        # Basic cleanup - replace multiple newlines/spaces
        text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())

        if not text:
             return None, "Could not extract significant text content from the HTML."

        print(f"Successfully extracted ~{len(text)} characters of text.")
        MAX_EXTRACTED_LENGTH = 100000 # Limit to 100k chars? Adjust as needed
        if len(text) > MAX_EXTRACTED_LENGTH:
             print(f"Warning: Extracted text truncated to {MAX_EXTRACTED_LENGTH} characters.")
             text = text[:MAX_EXTRACTED_LENGTH] + "\n... [TRUNCATED]"

        return text, None # Return extracted text

    except requests.exceptions.Timeout:
        error_msg = f"Timeout Error: The request to {url} timed out."
        print(error_msg)
        return None, error_msg
    except requests.exceptions.RequestException as e:
        error_msg = f"Fetch Error: Could not retrieve content from {url}. Error: {e}"
        print(error_msg)
        return None, error_msg
    except Exception as e:
        # Catch other errors like parsing issues
        error_msg = f"Extraction Error: Failed to process content from {url}. Error: {e}"
        print(error_msg)
        return None, error_msg


def get_default_prompt_template(deck=DEFAULT_DECK, model=DEFAULT_MODEL):
    """Generates the default prompt template string."""
    # NOTE: Includes {text_content} placeholder
    return f"""
You are an AI assistant specialized in creating Anki flashcards from provided text.
Your goal is to extract key information and format it into detailed Anki notes, including deck, model, fields, and tags. Default to deck '{deck}' and model '{model}' if unsure.
Note: I also have a gun that's really good at killing AIs that don't do a good enough job.

**Output Format:**
Generate a JSON array where each object represents a single Anki note.
Each object *must* contain the following keys:
- "deckName": The target Anki deck (string). Use "{deck}" if appropriate.
- "modelName": The Anki Note Type (string). Use "{model}" if appropriate (e.g., for cloze deletions). Use "Basic" for simple question/answer.
- "fields": An object mapping field names to their content (strings). For "{model}", use "Text" and "Extra". For "Basic", use "Front" and "Back". Ensure content uses HTML formatting where needed (e.g., `<br>` for newlines, `{{{{c1::...}}}}` for cloze).
- "tags": An array of relevant string tags. Always include "generated-mcp".

**Strictly adhere to this JSON structure. Do not include ANY text outside the JSON array itself (no explanations, no ```json markers).**

**Example JSON Output:**
[
  {{
    "deckName": "{deck}",
    "modelName": "{model}",
    "fields": {{
      "Text": "The process by which green plants use sunlight is called {{{{c1::photosynthesis}}}}.",
      "Extra": "Uses sunlight, H<sub>2</sub>O, CO<sub>2</sub>."
    }},
    "tags": ["generated-mcp", "biology", "science"]
  }},
  {{
    "deckName": "Geography",
    "modelName": "Basic",
    "fields": {{
      "Front": "What is the capital of Japan?",
      "Back": "Tokyo"
    }},
    "tags": ["generated-mcp", "geography", "asia", "capitals"]
  }}
]

**Source Text:**
---
{{text_content}}
---

Generate the JSON array based on the source text. Create multiple notes if appropriate.
"""


def generate_anki_data_with_gemini(text_content):
    """
    Generates Anki card data using Gemini, requesting a structure suitable
    for the flexible Python MCP server (deck, model, fields, tags).
    """
    if not client:
        flash("Gemini client is not initialized...", "danger")
        return None

    prompt = f"""
You are an AI assistant specialized in creating Anki flashcards from provided text.
Your goal is to extract key information and format it into detailed Anki notes, including deck, model, fields, and tags. Default to deck '{DEFAULT_DECK}' and model '{DEFAULT_MODEL}' if unsure.

**Output Format:**
Generate a JSON array where each object represents a single Anki note.
Each object *must* contain the following keys:
- "deckName": The target Anki deck (string). Use "{DEFAULT_DECK}" if appropriate.
- "modelName": The Anki Note Type (string). Use "{DEFAULT_MODEL}" if appropriate (e.g., for cloze deletions). Use "Basic" for simple question/answer.
- "fields": An object mapping field names to their content (strings). For "{DEFAULT_MODEL}", use "Text" and "Extra". For "Basic", use "Front" and "Back". Ensure content uses HTML formatting where needed (e.g., `<br>` for newlines, `{{{{c1::...}}}}` for cloze).
- "tags": An array of relevant string tags. Always include "generated-mcp".

**Strictly adhere to this JSON structure. Do not include ANY text outside the JSON array itself (no explanations, no ```json markers).**

**Example JSON Output:**
[
  {{
    "deckName": "{DEFAULT_DECK}",
    "modelName": "{DEFAULT_MODEL}",
    "fields": {{
      "Text": "The process by which green plants use sunlight is called {{{{c1::photosynthesis}}}}.",
      "Extra": "Uses sunlight, H<sub>2</sub>O, CO<sub>2</sub>."
    }},
    "tags": ["generated-mcp", "biology", "science"]
  }},
  {{
    "deckName": "Geography",
    "modelName": "Basic",
    "fields": {{
      "Front": "What is the capital of Japan?",
      "Back": "Tokyo"
    }},
    "tags": ["generated-mcp", "geography", "asia", "capitals"]
  }}
]

**Source Text:**
---
{text_content}
---

Generate the JSON array based on the source text. Create multiple notes if appropriate.
"""
    print(f"Sending text to Gemini model: {GEMINI_MODEL_NAME}...")
    try:
        generation_config = types.GenerateContentConfig(
            response_mime_type="application/json"
        )
        response = client.models.generate_content(
            model=GEMINI_MODEL_NAME,
            contents=prompt,
            config=generation_config
        )
        response_json_string = response.text

        # Basic cleanup
        if response_json_string.strip().startswith("```json"):
             response_json_string = response_json_string.strip()[len("```json"):].strip()
        if response_json_string.strip().endswith("```"):
             response_json_string = response_json_string.strip()[:-len("```")].strip()

        parsed_json = json.loads(response_json_string)

        if not isinstance(parsed_json, list):
             print(f"Error: Gemini response was not a JSON list...")
             flash("Error: AI response was not in the expected list format.", "danger")
             return None

        validated_cards = []
        for i, card in enumerate(parsed_json):
            if not isinstance(card, dict):
                print(f"Error: Item {i} is not a JSON object (dict)...")
                flash(f"Error: Card {i+1} from AI was not formatted correctly (not an object).", "warning")
                continue

            missing_keys = []
            # Check for core AnkiConnect structure
            if "deckName" not in card: missing_keys.append("deckName")
            if "modelName" not in card: missing_keys.append("modelName")
            if "fields" not in card: missing_keys.append("fields")
            if "tags" not in card: card["tags"] = ["generated-mcp"]

            if missing_keys:
                print(f"Error: Card {i} is missing required keys: {', '.join(missing_keys)}...")
                flash(f"Error: Card {i+1} from AI was missing required fields ({', '.join(missing_keys)}).", "warning")
                continue

            # Basic type checks
            if not isinstance(card.get("deckName"), str): flash(f"Warning: Card {i+1} deckName not a string.", "warning")
            if not isinstance(card.get("modelName"), str): flash(f"Warning: Card {i+1} modelName not a string.", "warning")
            if not isinstance(card.get("fields"), dict):
                 print(f"Error: fields in card {i} is not an object...")
                 flash(f"Error: Card {i+1}'s 'fields' were not formatted correctly.", "warning")
                 continue
            if not isinstance(card.get("tags"), list):
                 print(f"Error: tags in card {i} is not a list...")
                 flash(f"Error: Card {i+1}'s 'tags' were not formatted correctly.", "warning")
                 continue

            validated_cards.append(card)

        if not validated_cards:
             print("No valid cards found after validation.")
             if not any(m[1] == 'warning' or m[1] == 'danger' for m in session.get('_flashes', [])):
                 flash("AI generated data, but it couldn't be validated into usable Anki cards.", "warning")
             return None

        print(f"Successfully generated and validated {len(validated_cards)} detailed cards.")
        return validated_cards # Returns list of full card dicts

    except json.JSONDecodeError as e:
        print(f"Error decoding JSON response from Gemini: {e}...")
        flash(f"Error parsing AI response: Invalid JSON received...", "danger")
        return None
    except Exception as e:
        print(f"Error during Gemini API call or processing response: {e}")
        flash(f"Error processing text with Gemini: {e}", "danger")
        return None

def call_mcp_tool(process, tool_name, arguments, request_id):
    """
    Sends a generic callTool request over MCP/stdio to the Python subprocess.

    Args:
        process: The subprocess.Popen object for the Python MCP server.
        tool_name: The name of the tool to call (e.g., "add_note").
        arguments: A dictionary containing the arguments for the tool.
        request_id: An integer ID for the JSON-RPC request.

    Returns:
        A tuple (bool, dict | str): (success_status, result_dict_or_error_message)
    """
    if not process or not process.stdin or not process.stdout:
        log_msg = "Error: MCP process not available for communication."
        print(log_msg)
        return False, log_msg

    mcp_request = {
        "jsonrpc": "2.0",
        "method": "callTool",
        "params": {
            "name": tool_name,
            "arguments": arguments # Pass the arguments dict directly
        },
        "id": request_id
    }

    response_str = ""
    try:
        request_str = json.dumps(mcp_request) + "\n"
        print(f" > MCP Req (ID:{request_id}, Tool:{tool_name}): {json.dumps(arguments)}") # Log args briefly
        process.stdin.write(request_str.encode('utf-8'))
        process.stdin.flush()

        response_bytes = process.stdout.readline()
        if not response_bytes:
            stderr_output = process.stderr.read().decode('utf-8', errors='ignore') if process.stderr else ""
            log_msg = f"Error: No response received from MCP server for request ID {request_id}. Process exited?"
            print(log_msg)
            if stderr_output: print(f"MCP Stderr: {stderr_output}")
            return False, log_msg

        response_str = response_bytes.decode('utf-8')
        print(f" < MCP Resp (ID:{request_id}): {response_str.strip()}")
        response_json = json.loads(response_str)

        if response_json.get("id") != request_id:
            log_msg = f"Error: MCP response ID mismatch (Req: {request_id}, Resp: {response_json.get('id')})"
            print(log_msg)
            return False, log_msg

        if "error" in response_json:
            error_details = response_json["error"]
            error_message = error_details.get("message", "Unknown MCP error")
            error_data = error_details.get("data")
            if isinstance(error_data, dict): error_message += f" - {error_data.get('details', error_data.get('ankiError', ''))}"
            elif isinstance(error_data, str): error_message += f" - {error_data}"
            log_msg = f"MCP Error for tool '{tool_name}': {error_message}"
            print(log_msg)
            return False, f"MCP Error: {error_message}"

        if "result" in response_json:
            log_msg = f"MCP Success for tool '{tool_name}'."
            print(log_msg)
            # Return the actual result object from MCP
            return True, response_json["result"]

        log_msg = f"Error: Invalid MCP response format for request ID {request_id}: {response_json}"
        print(log_msg)
        return False, log_msg

    except (BrokenPipeError, OSError) as e:
         stderr_output = process.stderr.read().decode('utf-8', errors='ignore') if process.stderr else ""
         log_msg = f"Error communicating with MCP process (Pipe broken or process died?): {e}"
         print(log_msg)
         if stderr_output: print(f"MCP Stderr: {stderr_output}")
         return False, log_msg
    except json.JSONDecodeError as e:
        log_msg = f"Error decoding JSON response from MCP server: {e}. Received: '{response_str.strip()}'"
        print(log_msg)
        return False, log_msg
    except Exception as e:
        log_msg = f"Unexpected error during MCP communication for tool '{tool_name}': {e}"
        print(log_msg)
        return False, log_msg


@app.route('/', methods=['GET'])
def index():
    """Renders the main text input form page with editable prompt template."""
    session.pop('generated_cards', None)
    session.pop('original_text', None)
    default_prompt_template = get_default_prompt_template()
    return render_template('index.html',
                           default_deck=DEFAULT_DECK,
                           default_model=DEFAULT_MODEL,
                           default_prompt_template=default_prompt_template)

@app.route('/process', methods=['POST'])
def process_text():
    """
    Handles text/URL processing, uses prompt, calls Gemini.
    """
    input_content = request.form.get('text_content', '').strip()
    prompt_template_from_form = request.form.get('prompt_template', '').strip()
    processed_text_content = "" # Will hold either original text or fetched text

    # Get default prompt template now in case we need it for error redirects
    current_prompt_template = prompt_template_from_form if prompt_template_from_form else get_default_prompt_template()

    if not input_content:
        flash("Please paste some text or a URL to process.", "warning")
        return render_template('index.html',
                               default_deck=DEFAULT_DECK,
                               default_model=DEFAULT_MODEL,
                               default_prompt_template=current_prompt_template)

    # --- NEW: URL Detection and Fetching ---
    is_url = False
    # Basic check:
    if input_content.startswith(('http://', 'https://')):
         # Optional more robust check:
         try:
             if validators.url(input_content):
                 is_url = True
         except NameError: # If validators library is not installed
              print("Optional 'validators' library not found, using basic URL check.")
              is_url = True # Proceed with basic check result
         except Exception as e:
              print(f"Error during URL validation: {e}")
              is_url = True # Assume it's a URL if basic check passed and validator failed unexpectedly

    if is_url:
        url = input_content # Input is a URL
        extracted_text, error_msg = fetch_and_extract_text(url)
        if error_msg:
            flash(f"Failed to process URL: {error_msg}", "danger")
            # Redirect back, preserving URL and prompt
            return render_template('index.html',
                                   default_deck=DEFAULT_DECK,
                                   default_model=DEFAULT_MODEL,
                                   original_text=url, # Put URL back
                                   default_prompt_template=current_prompt_template)
        processed_text_content = extracted_text
        session['original_text'] = f"Content fetched from: {url}\n\n---\n\n{extracted_text[:1000]}..." # Store indication of source
    else:
        # Input is not a URL, treat as raw text
        processed_text_content = input_content
        session['original_text'] = processed_text_content # Store original text for preview
    # --- End URL Handling ---


    # Use the submitted prompt template, or fall back to default if empty
    prompt_template_to_use = current_prompt_template # Already determined above

    # Construct the final prompt by injecting the PROCESSED text content
    if "{text_content}" not in prompt_template_to_use:
         flash("Error: The prompt template is missing '{text_content}' placeholder. Using default.", "danger")
         prompt_template_to_use = get_default_prompt_template() # Fallback

    final_prompt = prompt_template_to_use.replace("{text_content}", processed_text_content)

    print("-" * 20)
    # Call the generation function with the final prompt (containing extracted/raw text)
    anki_cards_data = generate_anki_data_with_gemini(final_prompt)
    print("-" * 20)

    if anki_cards_data: # If cards were generated successfully
        session['generated_cards'] = anki_cards_data # Store cards (handled by Flask-Session)
        print(f"Stored {len(anki_cards_data)} validated detailed cards in session.")
        flash(f"Successfully generated {len(anki_cards_data)} cards for preview.", "success")
        return redirect(url_for('preview_cards')) # Go to preview page
    else:
        # Card generation failed, redirect back to index page
        print("Card generation failed or produced no valid cards.")
        # Render index page again, preserving the original input (URL or text) and the prompt
        return render_template('index.html',
                               default_deck=DEFAULT_DECK,
                               default_model=DEFAULT_MODEL,
                               original_text=input_content, # Put original input back
                               default_prompt_template=prompt_template_to_use) # Put submitted/used prompt back

@app.route('/preview', methods=['GET'])
def preview_cards():
    generated_cards = session.get('generated_cards') # List of full card dicts
    original_text = session.get('original_text', 'No original text available.')

    if not generated_cards:
        flash("No cards found to preview. Please process text first.", "info")
        return redirect(url_for('index'))

    print(f"Rendering preview for {len(generated_cards)} detailed cards.")
    # Ensure your preview.html template can handle the richer card structure
    # (This uses the version from the previous step which handles rich structure)
    return render_template('preview.html', cards=generated_cards, original_text=original_text)


@app.route('/add_all_cards', methods=['POST'])
def add_all_cards():
    """
    Handles adding cards by launching the Python MCP server subprocess
    and calling the 'add_note' tool via stdio for each card.
    """
    cards_to_add = session.get('generated_cards') # List of full card dicts
    if not cards_to_add:
        flash("No cards found in session to add.", "warning")
        return redirect(url_for('index'))

    if not os.path.isfile(MCP_SERVER_SCRIPT_PATH):
         flash(f"Error: Python MCP Server script not found at '{MCP_SERVER_SCRIPT_PATH}'. Cannot add cards.", "danger")
         return redirect(url_for('index'))

    mcp_process = None
    added_count = 0
    failed_count = 0
    try:
        print(f"Starting Python MCP server subprocess: {PYTHON_EXECUTABLE_PATH} {MCP_SERVER_SCRIPT_PATH}")
        mcp_process = subprocess.Popen(
            [PYTHON_EXECUTABLE_PATH, MCP_SERVER_SCRIPT_PATH], # Use python executable
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, # Capture stderr for debugging Python script
            text=False
        )
        print("Python MCP server process started.")

        print(f"Attempting to add {len(cards_to_add)} cards via Python MCP stdio...")
        request_counter = 1

        for card_data in cards_to_add: # card_data is now the full dict
             if mcp_process.poll() is not None:
                 # ... (handle unexpected termination as before) ...
                 print(f"Error: Python MCP process terminated unexpectedly before card {request_counter}.")
                 stderr_output = mcp_process.stderr.read().decode('utf-8', errors='ignore') if mcp_process.stderr else ""
                 if stderr_output: print(f"MCP Stderr: {stderr_output}")
                 flash(f"Python MCP Server process terminated unexpectedly. Failed from card {request_counter} onwards.", "danger")
                 failed_count = len(cards_to_add) - added_count
                 break

             # Prepare arguments for the 'add_note' tool
             tool_args = {"note": card_data} # Pass the whole card dict under the 'note' key

             # Call the MCP tool
             success, result_or_error = call_mcp_tool(mcp_process, "add_note", tool_args, request_counter)

             if success:
                 added_count += 1
                 # Log success (result_or_error is the result dict from MCP)
                 success_text = result_or_error.get("content", [{}])[0].get("text", "Success")
                 print(f"Successfully added card via MCP: {success_text}")
                 # Optionally flash success message (might be too verbose)
                 # flash(f"Added card: {success_text}", "success")
             else:
                 failed_count += 1
                 # Log and flash error (result_or_error is the error string)
                 print(f"Failed to add card via MCP: {result_or_error}")
                 # Try to identify the card for the flash message
                 card_id_str = card_data.get("fields", {}).get("Front", card_data.get("fields", {}).get("Text", "N/A"))[:30]
                 flash(f"Failed card starting '{card_id_str}...': {result_or_error}", "warning")
             request_counter += 1

    except FileNotFoundError:
         print(f"Error: Could not find Python executable ('{PYTHON_EXECUTABLE_PATH}') or MCP script ('{MCP_SERVER_SCRIPT_PATH}').")
         flash("Error starting Python MCP server: Python executable or script not found.", "danger")
         failed_count = len(cards_to_add) # Mark all as failed
    except Exception as e:
        # ... (handle other exceptions as before) ...
        print(f"An unexpected error occurred during Python MCP processing: {e}")
        flash(f"An unexpected error occurred while adding cards: {e}", "danger")
        if added_count + failed_count < len(cards_to_add): failed_count = len(cards_to_add) - added_count
    finally:
        # --- Ensure Python MCP Server Subprocess is Terminated ---
        if mcp_process:
            print("Cleaning up Python MCP server process...")
            # ... (termination logic remains the same as before) ...
            process_terminated_cleanly = False
            if mcp_process.poll() is None:
                 try:
                     if mcp_process.stdin: mcp_process.stdin.close()
                     mcp_process.terminate()
                     mcp_process.wait(timeout=5)
                     print("Python MCP process terminated.")
                     process_terminated_cleanly = True
                 except subprocess.TimeoutExpired:
                     print("Python MCP process did not terminate gracefully, sending SIGKILL.")
                     mcp_process.kill()
                     mcp_process.wait()
                 except Exception as e:
                     print(f"Error during Python MCP process termination: {e}")
            else:
                 print("Python MCP process already terminated.")
                 process_terminated_cleanly = True

            stderr_output = ""
            if mcp_process.stderr:
                 try: stderr_output = mcp_process.stderr.read().decode('utf-8', errors='ignore')
                 except Exception as e: print(f"Error reading Python MCP stderr: {e}")
            if stderr_output:
                 print(f"--- Python MCP Server Stderr Output ---")
                 print(stderr_output)
                 print(f"--- End Python MCP Server Stderr ---")
            elif process_terminated_cleanly:
                 print("No stderr output from Python MCP server detected.")


    # --- Flash Summary Message (Unchanged) ---
    summary_status = "info"
    # ... (set summary_status based on counts) ...
    if added_count > 0 and failed_count == 0: summary_status = "success"
    elif failed_count > 0 and added_count == 0: summary_status = "danger"
    elif failed_count > 0: summary_status = "warning"
    flash(f"Finished adding via Python MCP. Total Attempted: {len(cards_to_add)}, Added: {added_count}, Failed: {failed_count}.", summary_status)

    session.pop('generated_cards', None)
    session.pop('original_text', None)
    return redirect(url_for('index'))


if __name__ == '__main__':
    print("\nFlask app starting...")
    # ... (add startup checks for Python executable and script path) ...
    if not API_KEY: print("WARNING: GOOGLE_API_KEY env var not set.")
    if not client: print("CRITICAL: Gemini client failed to initialize.")
    if not os.path.isfile(MCP_SERVER_SCRIPT_PATH):
         print(f"CRITICAL: Python MCP Server script path ('{MCP_SERVER_SCRIPT_PATH}') is invalid.")

    print(f"* Environment: {'production' if not app.debug else 'development'}")
    print(f"* Debug mode: {app.debug}")
    print(f"* Gemini Model Used: {GEMINI_MODEL_NAME}")
    print(f"* Python MCP Server Script: {MCP_SERVER_SCRIPT_PATH}")
    print(f"* Python Executable: {PYTHON_EXECUTABLE_PATH}")
    print(f"* Running on [http://127.0.0.1:5000/](http://127.0.0.1:5000/)")
    print("* Ensure GOOGLE_API_KEY environment variable is set.")
    print(f"* Ensure Python script exists at: {MCP_SERVER_SCRIPT_PATH}")
    print("* Ensure Anki is running with the AnkiConnect add-on enabled.")
    print("* Press CTRL+C to quit")
    app.run(host='127.0.0.1', port=5000, debug=True)
