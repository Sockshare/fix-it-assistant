# Privacy

Short version: **with No AI or Ollama, nothing leaves your computer. Ever.** With a cloud key, only what you typed and the photo you attached are sent, only when you click.

## Where things live

| What | Where | Who can read it |
|---|---|---|
| Settings (and any API key) | `~/.fix_it_assistant/config.json` | Only your user (`600`) |
| Saved repairs | `~/.fix_it_assistant/repairs/*.json` | Only your user (`600`, folder `700`) |
| Photos | **Nowhere.** Photos are held in memory while the AI looks at them, then dropped. They are never saved to disk. | — |
| Chat with the AI | Inside the repair's file, if saving is on | Only your user |

Turn off **Save the repairs I check** in Settings and nothing is written at all; entries live in memory and vanish when the app closes.

**Delete all** in My repairs removes every entry. Deleting the `~/.fix_it_assistant/` folder removes everything including settings and keys.

## What goes over the network

| Mode | Network traffic |
|---|---|
| **No AI** | None. The tests run the whole app with network sockets blocked. |
| **Ollama** | Only to `127.0.0.1:11434` on your own machine. Ollama doesn't phone home. |
| **OpenAI / compatible** | Your description, the AI's own photo description (not the photo itself) for the explanation step; the photo itself for the photo step; your chat question plus the repair guide card. Sent only when you click **Figure it out** or **Ask**. |
| **Gemini** | Same as above, to Google. |

What is **never** sent, in any mode:

- Other saved repairs or their chats.
- File paths, your username, your IP.
- Anything when you just open the app, browse the guide, answer questions, or change status.

`/api/status` never contacts any cloud service on page load — only when you open Settings or click **Test**. It checks local Ollama (`127.0.0.1:11434`) only when Ollama is your selected provider, on the first-run setup screen, or when you open Settings. Your API key is sent only to the provider you chose, in a request header, never in a URL.

## The app itself

- Listens on `127.0.0.1` only. Other devices on your Wi-Fi can't reach it. The `Host` header is checked to block DNS-rebinding tricks.
- Rejects any write request (`POST`/`DELETE`) that comes from another website (`Origin` / `Sec-Fetch-Site` check).
- No analytics, no telemetry, no update checks, no third-party scripts or fonts. The UI is three local files.
- Logs (`--verbose`) never include your descriptions, photos, or keys.

## Photos and cloud providers

If you use a cloud key and attach a photo, that image goes to that company and is covered by their data policy. Photos of your home can show more than you intend — a mail pile, a prescription on the counter. Crop before you send, or use Ollama, which keeps it local.

## Environment variables

Developers can set `FIXIT_PROVIDER`, `FIXIT_MODEL`, `FIXIT_VISION_MODEL`, `FIXIT_DATA_DIR`, `OLLAMA_HOST`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GEMINI_API_KEY`. They override the settings file and are never written to it. Settings shows a note when any are active.

## Found a problem?

Open an issue on GitHub. If it's a security issue, say so in the title; we'll fix it fast.
