# Troubleshooting

Every message the app can show, what it means, and what to do. First, run the check script — it finds most problems by itself:

```bash
source .venv/bin/activate
python scripts/selfcheck.py
```

## Starting the app

| What you see | Why | Fix |
|---|---|---|
| `python3: command not found` | Python isn't installed | `sudo apt install python3 python3-venv` |
| `...ensurepip is not available` | Debian/Ubuntu ships Python without venv | `sudo apt install python3-venv` then redo step 2 of the README (or use the no-admin line in the README) |
| `Some required packages are not installed yet` | You skipped `pip install` or forgot to activate the venv | `source .venv/bin/activate` then `pip install -r requirements.txt` |
| `Port 8768 is busy, using 8769 instead` | Something else uses that port | Nothing to fix — use the address the app prints |
| Browser didn't open | Some Linux setups block auto-open | Open a browser yourself and go to `http://127.0.0.1:8768` |
| "The app's server isn't answering. Go back to the terminal…" | The terminal running `python app.py` was closed | Start it again with `python app.py` and reload the page |
| "The app couldn't load its settings" | The settings file is unreadable | Run `python scripts/selfcheck.py`; if needed delete `~/.fix_it_assistant/config.json` |

## Describing a problem

| What you see | Why | Fix |
|---|---|---|
| "Tell me a bit more: what is the thing, and what is it doing?" | Fewer than 8 characters | Say what it is (toilet, dryer) and what it does (runs, hums) |
| "I couldn't match that to anything in the guide" | The guide doesn't know that thing, or the words were too vague | Add the object and the symptom. Or click **Browse all problems** and pick from the list. |
| The match is wrong | Your words overlap with a different problem | Click one of the **other matches** chips, or Browse |
| "Photos must be JPG, PNG or WebP" | iPhone HEIC or another format | Take a screenshot of the photo, or set the camera to "Most compatible" |
| "That photo is bigger than 8 MB" | Very high-resolution photo | Share a smaller size, or screenshot it |
| "That file doesn't look like a real photo" | A renamed or broken file | Pick the photo again from your gallery |
| "Photos need an AI option turned on" | You're in No AI mode | Describe what you see in words — that works just as well. Or turn on an AI in Settings. |
| "Looking at photos needs a vision model. Run: ollama pull moondream" | Ollama is on but has no model that can see images | In a terminal: `ollama pull moondream` (1.7 GB). Then try again. |
| "What the AI saw in your photo" is wrong or vague | Small local vision models are limited | Your words carry more weight than the photo. Add detail in text, or try `llama3.2-vision` on a stronger computer. |

## The results page

| What you see | Why | Fix |
|---|---|---|
| A red box at the top | Your words matched an emergency rule (gas, CO, sparks, sewage, flooding, a bulging ceiling, structural cracks) | Do what the box says first. If it was a false alarm (e.g. "gas stove won't light" is not a leak), keep scrolling — the normal match is below. |
| "Pick a problem from the matches first, then ask about it" | No problem is selected (no match was found) | Click an **other matches** chip or use Browse, then ask |
| "That check wasn't found. Run it again." | The app restarted and saving is off, or the entry was deleted | Click **Check something else** and run it again |
| Questions don't change the order | The causes already agree with your answer | Nothing to fix — the labels show which ones fit |
| "Pick a problem first." | The answers were sent before a problem was chosen | Choose a match, then answer |

## Asking questions

| What you see | Why | Fix |
|---|---|---|
| "Without an AI option turned on, I can only answer simple questions…" | No AI mode | Ask about tools, parts, cost, time, safety, when to call a pro, or the first step. Or turn on an AI. |
| ⚠️ "Remember: this one is marked Call a professional" at the start of an answer | The AI's answer sounded like it was encouraging a risky job | The warning is the app overriding the AI on purpose. Follow the guide's safety level. |
| The answer is about something else | The chat is tied to the current repair only | Start a new check for a different problem |
| "Could not reach Ollama. Is it running? Try: ollama serve" | Ollama isn't running right now | Open another terminal and run `ollama serve`. Leave it open. Then click **Test** in Settings. |
| "Ollama took too long to answer" | The model is slow on this computer | The app waits up to 15 minutes. Try a smaller model (`llama3.2:1b`) or close heavy programs. |
| "The AI got stuck repeating itself" | Small models sometimes loop | The app already retried once. Click Ask again, or try a bigger model. |

## The AI (optional)

| What you see | Why | Fix |
|---|---|---|
| "Ollama isn't running (or isn't installed)" | Not installed or not started | Install: `curl -fsSL https://ollama.com/install.sh \| sh` then `ollama pull llama3.2:3b`. If installed, run `ollama serve`. |
| "Model '…' is not downloaded" | | `ollama pull llama3.2:3b` (or the model named in the message) |
| "'…' is not a valid address" | Typo in the Ollama address | Use `http://127.0.0.1:11434` |
| "No API key saved. Paste your key in Settings." | Cloud option without a key | Settings → paste → Test → Save |
| "The API key was rejected" | Typo or expired key | Paste it again and click **Test** |
| "The API says you are out of credits" | Billing or rate limit at the provider | Check your account there; wait a minute |
| Test says "ready" but the pill says "— problem" | An environment variable is overriding settings | Settings shows a note listing them; unset them and restart |
| The pill at the top says "— problem" | The AI option can't be reached | Click the pill to open Settings and press **Test** |
| "The AI option has a problem…" under the Ask box | Same as above | You still get simple answers until it's fixed |

### Which model should I use?

| Model | For | RAM | Speed on CPU |
|---|---|---|---|
| `llama3.2:1b` | questions | ~2 GB | fast, basic |
| `llama3.2:3b` (default) | questions | ~4 GB | medium, good |
| `qwen2.5:7b` | questions | ~8 GB | slower, better |
| `moondream` (default) | photos | ~3 GB | medium, basic |
| `llama3.2-vision` | photos | ~10 GB | slow, much better |

## Saved repairs

| What you see | Why | Fix |
|---|---|---|
| "not saved" next to an entry | "Save the repairs I check" is off | Turn it on in Settings if you want them kept |
| Entries disappeared after restart | Same | Same |
| "Delete all" didn't remove the folder | Only the entries are deleted; the empty folder stays | Safe to delete `~/.fix_it_assistant/` by hand if you want |

## Still stuck?

Run `python app.py --verbose` and read the lines that appear when the problem happens. Open an issue on GitHub with those lines (they never contain your descriptions or photos).
