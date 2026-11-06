# FAQ

**Do I need the AI?**
No. The guide, the safety levels, the questions, the steps and the shopping lists all work with No AI. The AI adds photo reading and free-form questions.

**Is this a replacement for a plumber or electrician?**
No. It's a careful first step: what it probably is, whether it's safe for you to try, and what to do. When it says call a pro, call a pro.

**How does it decide what's wrong?**
It matches your words against a hand-written guide of 47 common problems. Each one has anchor words (the thing: toilet, dryer) and symptom words (the behavior: running, humming). It scores them and shows the top matches with an honest confidence. Your answers to the quick questions then re-order the causes.

**Why did it say "I couldn't match that"?**
Either the guide doesn't cover that thing yet, or the words were too vague. Add the object and the symptom. Or use Browse and pick from the list.

**Can the AI make up a diagnosis?**
No. With an AI on, it may only choose from the matches the guide already found, or say "none fit". It can't invent a cause, and it can't change the safety level or the "call a pro" list. If it tries to call a pro-level job "easy", the app overrides it.

**Why is the safety level so cautious?**
On purpose. Electrical beyond a breaker reset, gas, garage-door springs, microwaves, refrigerant: people get badly hurt doing these without training. The guide tells you the safe checks you *can* do, then says stop.

**Where do the prices come from?**
Rough US ranges, written by hand. Your area and the brand will vary. Treat them as "is this a $10 fix or a $300 fix", not a quote.

**What does the photo actually do?**
The AI describes what it sees in a few sentences (the object, visible damage, labels). That description is added to your words before matching. It helps most when you don't know what something is called. Your typed words carry more weight than the photo.

**Why does the photo need a different model?**
Normal language models can't see. Ollama needs a "vision" model like `moondream` (small) or `llama3.2-vision` (better, bigger). Cloud models from OpenAI and Google already see images.

**How long does it take with Ollama?**
Questions: 20–60 seconds on a normal laptop. Photos: 1–3 minutes. No AI mode is instant.

**Does it work offline?**
Yes, in No AI or Ollama mode. Fully.

**Does it work on Windows or Mac?**
It's built and tested on Linux. It will very likely run on Mac the same way. On Windows, use WSL or replace `source .venv/bin/activate` with `.venv\Scripts\activate`. Not tested.

**Can I add my own problems to the guide?**
Yes. Each one is a Python dictionary in `fix_it_assistant/kb/`. Copy an existing one, change it, run `pytest` — the tests check the structure. Pull requests welcome.

**Can it help with cars?**
The basics: won't start, tire pressure light, check engine light. Enough to know if it's a $0 fix or a tow.

**What about phones, laptops, printers?**
Not yet. The guide is about the house and what's parked outside it.

**Is my data safe?**
Everything is stored only on your computer, readable only by your user. With No AI or Ollama, nothing leaves the machine. See [PRIVACY.md](PRIVACY.md).

**Can I use it on my phone?**
Open `http://127.0.0.1:8768` on the computer running it. For your phone to reach it, you'd have to change the bind address in `app.py`, which we don't recommend without a password in front of it.

**It told me something I know is wrong.**
Please open an issue with what you typed and what you expected. The guide gets better that way.
