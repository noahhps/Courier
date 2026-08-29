## The process and my encounters while building

# The Engineering
Courier was initially meant to be a simple chat interface designed to replace the (quite frankly) ugly Ollama.
I resented Ollama's design due to how stale and uninspired it felt (not to shame it considering it is an essential part of this project).
I mostly wanted an interface that allowed me the freedom to interact with the model in the ways I needed it to while also allowing me the ability to customize it how I wanted it.

That's how I landed on the current skill, memory, project, and calendar system that defines Courier currently.

Skills were mostly a byproduct of the memory system that I was initially building, as memory was half a skill in itself while also being a layer of the harness that was built around Ollama.
Ollama itself lacked a memory layer in its client while other clients that allowed Ollama's API injection took too much memory (Hermes and its use of Electron) or were too clunky to actually use for me.
And speaking of Hermes, it's the single reason as to why I decided to use a web-based platform for Courier; no hate to Hermes, a lot of the UI/UX was actually inspired by their really awesome client (I adore the design they use at Nous Research for Hermes and just wish it wasn't built on electron or a CLI application lol).
I'm also a strong believer in the belief that LLM's weren't and aren't meant to be interacted with through a CLI and that an actually clickable UI interface is the future for human-AI interaction; another reason as to why I decided to use an actual UI.

As said before, Memory was built after Skills and was actually built by Claude within an hour using an entire 5 session's worth of use.  
It's design though is relatively simple and is built on the idea that memory isn't just another layer, but is actually, just another Skill.
Web-Search is a skill that many Harnesses feature by default, while memory is considered a more advanced feature: even though both are essentially the same.
One is a retrieval through a vast library of the closest thing to all of human thought from the past century (and beyond), while anther is another retrieval through your old chat logs with an LLM.
That's why I found it interesting that many harnesses decided to feature things like RAG and Web-Search by default but not memory, and why I decided to implement memory as a default in this project.

Memory is an essential part of how we interact with other people, and when interacting with LLM's it should also be another essential feature no matter how many tokens it may use.
Memory when done properly is done through keyword search which ranks the most relevant paragraphs of conversations between a user and LLM, retrieving these these keywords as conversation's topic is established.
Thus, the underlying systems which run both web-search and memory are essentially the same and thus, can both be implemented not as separate layers, but as skills.
Although context may become an issue for some conversations, context should not limit what you can do with an LLM and should be seen more-so as limits designed to be worked-around.
That's why the memory system currently omits thinking and focusses on actual conversation between the user and model even as chat history saves thinking.  

Calendar and Project system explanation tba...

# The Design
While building Courier, I iterated through a variety of designs with Claude.
I initially was inspired by video games such as Marathon and the design language they employed throughout the game.
A very corporate near-cyberpunk feel which hovered around the struggle between absolute control and freedom/liberation.

I wanted to design a harness which allowed the user both absolute control at the same time as giving them the ultimate freedom to work with an LLM as they wanted.
That's how I landed on both the initial name for the project: Gantry, and the initial UI-design in UI-revert.
I eventually realized that though the design looked cool, it wasn't a modular system that afforded me the freedom to continue building on top of it.

That's why I eventually shifted to the current design, a design focused on simplicity; a simplicity enhanced by color, fonts, and small unnoticeable patterns.
The current design was (to be honest) mostly designed by Claude, the colors I mean.  I'm someone who is very indecisive when it comes to Colors and Themes so I left it to Claude to decide on them for me.
It eventually reached the pastel blue and white color system that I'm using now.

