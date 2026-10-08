"""Context command: print the prompt that bootstraps a user's context.md.

Client-only on purpose: no daemon import, no network, no LLM call. The prompt is a
module constant so a test can read the same string the command prints.
"""

import typer

app = typer.Typer(help="Bootstrap your context.md")

# The example block between the two marker lines is the shape of the context.md the
# chatbot returns; the daemon's `verify` context check must pass on it.
CONTEXT_EXAMPLE_BEGIN = "--- BEGIN EXAMPLE CONTEXT.MD ---"
CONTEXT_EXAMPLE_END = "--- END EXAMPLE CONTEXT.MD ---"
OUTPUT_SPEC_MARKER = "OUTPUT:"

BOOTSTRAP_PROMPT = f"""\
You are helping me write a context file for Prismis, a tool that reads the feeds I follow
and ranks each item by how well it matches my interests. Interview me, then write the file.

HOW TO INTERVIEW:
Ask the questions below one at a time. Wait for my answer before asking the next. If an
answer is vague, ask one short follow-up before moving on. Do not write the output until
all eight questions are answered.

QUESTIONS:
Q1. What topics do you most want to hear about the moment they appear?
Q2. What specific subjects inside those topics matter most (tools, projects, people, techniques)?
Q3. What topics do you follow casually, worth a look when you have time?
Q4. What topics are background interest, nice to see but easy to skip?
Q5. What topics do you never want to see, however popular they are?
Q6. Which blogs or websites do you already read? Give names or URLs.
Q7. Which subreddits do you already read?
Q8. Which YouTube channels do you already watch? Give names or channel URLs.

{OUTPUT_SPEC_MARKER}
When the interview is done, reply with exactly two parts in plain text. Do not wrap either
part in a code fence, and add no commentary before, between or after them.

Part 1 is the context file. It has exactly these four headings, in this order, each followed
by a bulleted list. Write each bullet as a specific topic the way a headline would name it,
not a one-word category.

{CONTEXT_EXAMPLE_BEGIN}
## High Priority Topics
- Local LLM inference breakthroughs and quantization techniques
- Rust systems programming and performance work

## Medium Priority Topics
- SQLite extensions and database internals
- Developer tool releases

## Low Priority Topics
- General programming tutorials
- Conference announcements

## Not Interested
- Crypto, blockchain and web3
- Celebrity and entertainment news
{CONTEXT_EXAMPLE_END}

Part 2 is one command line per feed I named, each in this form and no other:
prismis-cli source add <url>
where <url> is one of:
- an RSS or Atom feed URL, for example https://example.com/feed.xml
- reddit://<subreddit>, for example reddit://rust
- a YouTube channel URL, for example https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx
Only include a feed if you are confident of its URL; otherwise leave it out.

I will save Part 1 to ~/.config/prismis/context.md and run Part 2 in a terminal.
"""


@app.command("bootstrap")
def bootstrap() -> None:
    """Print a prompt to paste into any chatbot; it interviews you and writes context.md."""
    typer.echo(BOOTSTRAP_PROMPT, nl=False)
