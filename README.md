## Anki Card Generation w/ Gemini
This started off as an MCP experiment, but I wanted to see a preview of the cards before Gemini wrote them to Anki, so it's been decoupled. You can very easily couple it back though.

### Setup 
Make sure to have a `GOOGLE_API_KEY`, and set it as an envvar. 

I recommend using python's `virtualenv` to create an environment: 
`python3 -m venv env && pip install -r requirements.txt`. 

Also make sure to have Anki running locally, with the AnkiConnect plugin (https://ankiweb.net/shared/info/2055492159) installed. After that, you should be good to go!

### Running
`python3 app.py` and navigate to `localhost:5000`.
