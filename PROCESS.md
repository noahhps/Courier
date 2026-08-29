# The process and my encounters while building

## The Engineering
Courier was initially meant to be a simple chat interface designed to replace Ollama.
I resented Ollama's design due to how stale and uninspired it felt (not to shame it, considering it is an essential part of this project).
I mostly wanted an interface that allowed me the freedom to interact with the model in the ways I needed it to while also allowing me the ability to customize it how I wanted it.

That's how I landed on the skill, memory, project, and calendar system that defines Courier today.

### Skills
Skills were mostly a byproduct of the memory system that I was initially building, as memory was half a skill in itself while also being a layer of the harness that was built around Ollama.
Ollama itself lacked a memory layer in its client, while other clients that allowed Ollama's API injection took too much memory (Hermes and its use of Electron) or were too clunky to actually use.
And speaking of Hermes, it's the single reason why I decided to use a web-based platform for Courier; no hate to Hermes, a lot of the UI/UX was actually inspired by their really awesome client (I adore the design they use at Nous Research for Hermes and just wish it wasn't built on Electron or as a CLI application).
I believe that LLMs weren't and aren't meant to be interacted with through a CLI, and that an actually clickable UI is the future for human-AI interaction; another reason why I decided to use an actual UI.

### Memory
As said before, memory was built after skills and was designed not as another layer but as another skill.
Web search is a skill that many harnesses feature by default, while memory is considered a more advanced feature, even though both are essentially the same.
This design choice is predicated on the idea that memory, just like search, is another retrieval skill that digs through your old chat logs with your models instead of searching through decades of a vast human library.

Memory is an essential part of how we interact with other people, and when interacting with LLMs it should also be an essential feature, no matter how many tokens it may use.
Memory, when done properly, works through keyword search that ranks the most relevant paragraphs of conversations between a user and an LLM, retrieving them as the conversation's topic is established.

#### The architecture behind memory
I employed a relevance floor for semantic recall: a minimum cosine similarity that a passage from your conversation history must reach before it counts as relevant at all.
By default it is set to 0.35 in `server/app/config.py`.
- This value doesn't act as just another weight or tuning knob; it is a floor that stops the nearest-neighbor search from returning irrelevant data:
  - When nothing in your history matches the current context, the search can still return chunks that have nothing to do with it, because the top result of a bad set is still a top result.
  - So we need a floor that throws away anything below a certain level of relevance, which lets the system better distinguish **no match** from a **weak match**.
  - The value 0.35, though, depends on the encoder model I used in this project, nomic-embed-text, so be careful when using a different embedding model, as the value it wants will differ.

Although context may become an issue for some conversations, context should not limit what you can do with an LLM and should be seen more as a limit designed to be worked around.
That's why the memory system currently omits thinking and focuses on actual conversation between the user and model, even as chat history saves thinking.

## The Design
While building Courier, I iterated through a variety of designs with Claude.
I was initially inspired by video games such as Marathon and the design language they employed throughout the game: a very corporate, near-cyberpunk feel which hovered around the struggle between absolute control and freedom/liberation.

I wanted to design a harness which allowed the user absolute control while also giving them the freedom to work with an LLM however they wanted.
That's how I landed on both the initial name for the project, Gantry, and the initial UI design in `.ui-revert`.
I eventually realized that though the design looked cool, it wasn't a modular system that afforded me the freedom to keep building on top of it.

That's why I eventually shifted to the current design, one focused on simplicity: a simplicity enhanced by color, fonts, and small, unnoticeable patterns.
The current design was made in parallel with Claude.
The elements and colors were heavily influenced by the designs the team at Kimi employs in their own UIs.
